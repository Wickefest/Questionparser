"""Approve / reject / edit draft workflow tests (in-memory store)."""

from __future__ import annotations

from pathlib import Path

from questbank.factory.bootstrap import create_ingestion_scaffold
from questbank.factory.assets import MemoryAssetUploader
from questbank.factory.review import approve_draft, edit_draft, reject_draft, replace_draft_asset
from questbank.factory.service import QuestionFactoryIngestService
from questbank.factory.store import InMemoryFactoryStore


def _seed(tmp_path: Path):
    store = InMemoryFactoryStore()
    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 q")
    a.write_bytes(b"%PDF-1.4 a")
    scaffold = create_ingestion_scaffold(
        title="Chem 2023 P1",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="6092/01",
        store=store,
        uploader=MemoryAssetUploader(),
    )
    record = {
        "question_ref": "4",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Zinc reacts with dilute acid."}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "true"},
                {"label": "B", "content": "false"},
                {"label": "C", "content": "maybe"},
                {"label": "D", "content": "never"},
            ],
        },
        "answer": {"value": "A", "explanation": None, "explanation_source": None},
        "status": "DRAFT",
        "source_page": 2,
        "requires_review": False,
    }
    QuestionFactoryIngestService(store, uploader=MemoryAssetUploader()).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=tmp_path / "assets",
    )
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    return store, draft


def test_edit_creates_review_event(tmp_path: Path):
    store, draft = _seed(tmp_path)
    content = [{"type": "text", "value": "Edited stem"}]
    result = edit_draft(store, draft_id=draft.id, content=content, options=draft.options)
    assert result["event"]["action"] == "EDIT"
    updated = store.get_draft(draft.id)
    assert updated.content[0]["value"] == "Edited stem"
    assert updated.status != "APPROVED"


def test_reject_keeps_draft(tmp_path: Path):
    store, draft = _seed(tmp_path)
    reject_draft(store, draft_id=draft.id, reason="Answer mismatch")
    updated = store.get_draft(draft.id)
    assert updated is not None
    assert updated.status == "REJECTED"
    assert any(e["action"] == "REJECT" for e in store.review_events.values())


def test_approve_creates_immutable_version(tmp_path: Path):
    store, draft = _seed(tmp_path)
    result = approve_draft(store, draft_id=draft.id)
    assert result["questionId"]
    assert result["version"] == 1
    assert store.get_draft(draft.id).status == "APPROVED"
    bank = store.get_bank_question(result["questionId"])
    assert bank is not None
    assert "-B" in (result.get("sourceKey") or bank.get("sourceKey") or "")
    assert len(bank["versions"]) == 1
    assert bank["versions"][0]["status"] == "APPROVED"
    assert bank["versions"][0]["answer"]["value"] == "A"

    # Second approval of a new draft with same source key creates v2 and retires v1.
    # Simulate re-edit path: create another draft row is hard; approve again blocked.
    # Instead upsert by approving after resetting status via a second seeded draft.
    draft2_content = [{"type": "text", "value": "Corrected"}]
    # Manually create parallel draft same ref different run is complex; verify retire path:
    store.update_draft_json(
        draft.id,
        content=draft2_content,
        options=draft.options,
        status="READY",
        requires_review=False,
        validation_issues=None,
    )
    # Can't approve APPROVED→ already approved was set; we reset to READY above
    result2 = approve_draft(store, draft_id=draft.id)
    assert result2["version"] == 2
    bank = store.get_bank_question(result["questionId"])
    statuses = {v["version"]: v["status"] for v in bank["versions"]}
    assert statuses[1] == "RETIRED"
    assert statuses[2] == "APPROVED"


def test_create_manual_draft_fills_gap(tmp_path: Path):
    from questbank.factory.review import create_manual_draft, suggest_question_ref

    store, draft = _seed(tmp_path)
    batch_id = draft.batch_id
    # Seed only has Q4; suggested gap should be 1
    assert suggest_question_ref(store, batch_id=batch_id) == "1"
    result = create_manual_draft(
        store,
        batch_id=batch_id,
        question_ref="3",
        stem="Fe^2+ is oxidised as it lost an electron.",
        options={"A": "true", "B": "false", "C": "maybe", "D": "never"},
        answer_value="A",
        explanation="Oxidation is loss of electrons.",
    )
    created = store.get_draft(result["draftId"])
    assert created is not None
    assert created.question_ref == "3"
    assert created.status == "NEEDS_REVIEW"
    answer = store.get_answer(created.run_id, "3")
    assert answer is not None
    assert answer.answer_value == "A"
    approved = approve_draft(store, draft_id=created.id)
    assert approved["questionId"]


def test_create_manual_draft_rejects_duplicate(tmp_path: Path):
    from questbank.factory.review import create_manual_draft

    store, draft = _seed(tmp_path)
    try:
        create_manual_draft(
            store,
            batch_id=draft.batch_id,
            question_ref=draft.question_ref,
            stem="dup",
            answer_value="B",
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "already exists" in str(exc)


def test_different_batches_get_unique_bank_keys(tmp_path: Path):
    from questbank.factory.review import make_source_key

    k1 = make_source_key(
        subject="Chemistry",
        exam_year=2026,
        paper_code="P1",
        question_ref="1",
        batch_id="540abd61-fa57-4cfc-b30f-7378467283c4",
    )
    k2 = make_source_key(
        subject="Chemistry",
        exam_year=2026,
        paper_code="P1",
        question_ref="1",
        batch_id="445c6848-6996-421f-8531-0d00dfd8a8e9",
    )
    assert k1 != k2
    assert k1.endswith("-B540abd61")
    assert k2.endswith("-B445c6848")


def test_replace_draft_asset(tmp_path: Path):
    from io import BytesIO
    from uuid import uuid4

    from PIL import Image

    from questbank.factory.store import AssetRow

    store, draft = _seed(tmp_path)
    buf = BytesIO()
    Image.new("RGB", (40, 30), (10, 20, 30)).save(buf, format="PNG")
    original = buf.getvalue()

    asset = store.create_asset(
        AssetRow(
            id=str(uuid4()),
            draft_id=draft.id,
            source_document_id=None,
            role="STEM_IMAGE",
            storage_path=f"question-assets/{draft.batch_id}/{draft.run_id}/old.png",
            mime_type="image/png",
            source_page=1,
            bbox=None,
            width=40,
            height=30,
            sha256="abc",
        )
    )
    # Seed local file so Memory path works after replace via LocalDisk uploader
    from questbank.factory.assets import MemoryAssetUploader

    uploader = MemoryAssetUploader()
    new_buf = BytesIO()
    Image.new("RGB", (80, 60), (200, 100, 50)).save(new_buf, format="PNG")
    new_bytes = new_buf.getvalue()

    result = replace_draft_asset(
        store,
        draft_id=draft.id,
        asset_id=asset.id,
        data=new_bytes,
        content_type="image/png",
        filename="fixed.png",
        uploader=uploader,
    )
    assert result["assetId"] == asset.id
    assert result["width"] == 80
    assert result["height"] == 60
    updated = store.get_asset(asset.id)
    assert updated.storage_path.endswith("fixed.png")
    assert uploader.uploaded[-1]["bytes"] == len(new_bytes)
    assert len(original) != len(new_bytes)
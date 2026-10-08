"""API-level smoke for Question Factory admin routes (no Gemini)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from questbank.api import factory_routes
from questbank.api.app import app
from questbank.factory.assets import MemoryAssetUploader
from questbank.factory.bootstrap import create_ingestion_scaffold
from questbank.factory.local_storage import LocalDiskAssetUploader
from questbank.factory.review import approve_draft
from questbank.factory.service import QuestionFactoryIngestService
from questbank.factory.store import InMemoryFactoryStore


def test_factory_api_review_approve_flow(tmp_path: Path, monkeypatch):
    store = InMemoryFactoryStore()
    factory_routes._STORE = store

    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")
    scaffold = create_ingestion_scaffold(
        title="API smoke",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
        store=store,
        uploader=MemoryAssetUploader(),
    )
    QuestionFactoryIngestService(store, uploader=MemoryAssetUploader()).persist(
        context=scaffold.context,
        parse_result={
            "questions": [
                {
                    "question_ref": "1",
                    "question_type": "mcq",
                    "content": [{"type": "text", "value": "What is water?"}],
                    "options": {
                        "mode": "text",
                        "items": [
                            {"label": "A", "content": "H2O"},
                            {"label": "B", "content": "CO2"},
                            {"label": "C", "content": "O2"},
                            {"label": "D", "content": "N2"},
                        ],
                    },
                    "answer": {
                        "value": "A",
                        "explanation": "Mark scheme note",
                        "explanation_source": "mark_scheme",
                    },
                    "status": "NEEDS_REVIEW",
                    "source_page": 1,
                    "requires_review": True,
                }
            ]
        },
        assets_dir=tmp_path / "assets",
    )
    store.update_batch_status(scaffold.context.batch_id, "READY_FOR_REVIEW")

    client = TestClient(app)
    batch_id = scaffold.context.batch_id

    dash = client.get("/api/factory/dashboard")
    assert dash.status_code == 200
    assert dash.json()["metrics"]["totalQuestions"] >= 1

    batch = client.get(f"/api/factory/batches/{batch_id}")
    assert batch.status_code == 200
    assert batch.json()["questionCount"] == 1

    drafts = client.get(f"/api/factory/batches/{batch_id}/drafts")
    assert drafts.status_code == 200
    draft_id = drafts.json()["drafts"][0]["id"]

    junk = client.get(f"/api/factory/batches/{batch_id}/drafts?status=NEEDS_REVIEW&q=undefined")
    assert junk.status_code == 200
    assert len(junk.json()["drafts"]) == 1

    detail = client.get(f"/api/factory/batches/{batch_id}/drafts/{draft_id}")
    assert detail.status_code == 200
    assert detail.json()["draft"]["answer"]["value"] == "A"

    edited = client.patch(
        f"/api/factory/batches/{batch_id}/drafts/{draft_id}",
        json={
            "content": [{"type": "text", "value": "What is H2O?"}],
            "options": detail.json()["draft"]["options"],
            "comment": "fix fix",
        },
    )
    assert edited.status_code == 200

    # reject a clone path: create second draft then reject — for single draft, reject then we can't approve.
    # Approve the edited draft instead.
    approved = client.post(f"/api/factory/batches/{batch_id}/drafts/{draft_id}/approve")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["questionId"]
    assert body["version"] == 1

    bank = client.get("/api/factory/questions")
    assert bank.status_code == 200
    assert any(q["id"] == body["questionId"] for q in bank.json()["questions"])

    # Duplicate detection
    q_hash = __import__("hashlib").sha256(q.read_bytes()).hexdigest()
    a_hash = __import__("hashlib").sha256(a.read_bytes()).hexdigest()
    dup = client.post(
        "/api/factory/imports/check-duplicates",
        json={"questionSha256": q_hash, "answerSha256": a_hash},
    )
    assert dup.status_code == 200
    assert dup.json()["status"] == "exact_duplicate"


def test_batch_with_drafts_is_not_failed(tmp_path: Path, monkeypatch):
    store = InMemoryFactoryStore()
    factory_routes._STORE = store
    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")
    scaffold = create_ingestion_scaffold(
        title="API smoke",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
        store=store,
        uploader=MemoryAssetUploader(),
    )
    QuestionFactoryIngestService(store, uploader=MemoryAssetUploader()).persist(
        context=scaffold.context,
        parse_result={
            "questions": [
                {
                    "question_ref": "1",
                    "question_type": "mcq",
                    "content": [{"type": "text", "value": "What is water?"}],
                    "options": {
                        "mode": "text",
                        "items": [
                            {"label": "A", "content": "H2O"},
                            {"label": "B", "content": "CO2"},
                            {"label": "C", "content": "O2"},
                            {"label": "D", "content": "N2"},
                        ],
                    },
                    "status": "NEEDS_REVIEW",
                    "source_page": 1,
                    "requires_review": True,
                }
            ]
        },
        assets_dir=tmp_path / "assets",
    )
    store.update_batch_status(scaffold.context.batch_id, "HAS_ERRORS")
    client = TestClient(app)
    body = client.get(f"/api/factory/batches/{scaffold.context.batch_id}").json()
    assert body["questionCount"] == 1
    assert body["stages"]["failed"] is False
    assert body["stages"]["complete"] is True


def test_bank_question_serves_asset_via_storage_url(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FACTORY_STORAGE_ROOT", str(tmp_path / "storage"))
    store = InMemoryFactoryStore()
    factory_routes._STORE = store
    uploader = LocalDiskAssetUploader()

    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")
    scaffold = create_ingestion_scaffold(
        title="Visual bank",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
        store=store,
        uploader=uploader,
    )
    from PIL import Image

    assets = tmp_path / "assets"
    assets.mkdir()
    Image.new("RGB", (16, 12), (9, 8, 7)).save(assets / "q01-stem-1.png")

    QuestionFactoryIngestService(store, uploader=uploader).persist(
        context=scaffold.context,
        parse_result={
            "questions": [
                {
                    "question_ref": "1",
                    "question_type": "mcq",
                    "content": [
                        {"type": "text", "value": "See the graph."},
                        {"type": "image", "asset": "q01-stem-1.png"},
                    ],
                    "options": {
                        "mode": "text",
                        "items": [
                            {"label": "A", "content": "H2O"},
                            {"label": "B", "content": "CO2"},
                            {"label": "C", "content": "O2"},
                            {"label": "D", "content": "N2"},
                        ],
                    },
                    "answer": {
                        "value": "A",
                        "explanation": "Mark scheme",
                        "explanation_source": "mark_scheme",
                    },
                    "status": "NEEDS_REVIEW",
                    "source_page": 1,
                    "requires_review": True,
                }
            ]
        },
        assets_dir=assets,
    )
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    approved = approve_draft(store, draft_id=draft.id)
    asset = store.list_assets_for_draft(draft.id)[0]

    client = TestClient(app)
    detail = client.get(f"/api/factory/questions/{approved['questionId']}")
    assert detail.status_code == 200
    version = detail.json()["versions"][0]
    expected_url = f"/api/factory/assets/{asset.id}"
    assert version["assetUrls"][asset.id] == expected_url

    img = client.get(expected_url)
    assert img.status_code == 200
    assert img.content[:8] == b"\x89PNG\r\n\x1a\n"

    source = client.get(f"/api/factory/documents/{scaffold.context.question_document_id}/file")
    assert source.status_code == 200
    assert source.content.startswith(b"%PDF")


def test_export_questions_endpoint(tmp_path: Path, monkeypatch):
    store = InMemoryFactoryStore()
    factory_routes._STORE = store

    # Set mock key env var
    monkeypatch.setenv("REAL_APP_PUBLISH_KEY", "test-secret")

    # Call mock bootstrap to create questions
    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")
    scaffold = create_ingestion_scaffold(
        title="Export API Test",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
        store=store,
        uploader=MemoryAssetUploader(),
    )
    QuestionFactoryIngestService(store, uploader=MemoryAssetUploader()).persist(
        context=scaffold.context,
        parse_result={
            "questions": [
                {
                    "question_ref": "1",
                    "question_type": "mcq",
                    "content": [{"type": "text", "value": "Is this water?"}],
                    "options": {
                        "mode": "text",
                        "items": [
                            {"label": "A", "content": "H2O"},
                            {"label": "B", "content": "CO2"},
                            {"label": "C", "content": "O2"},
                            {"label": "D", "content": "N2"},
                        ],
                    },
                    "answer": {
                        "value": "A",
                        "explanation": "Mark scheme note",
                        "explanation_source": "mark_scheme",
                    },
                    "status": "NEEDS_REVIEW",
                    "source_page": 1,
                    "requires_review": False,
                }
            ]
        },
        assets_dir=tmp_path / "assets",
    )
    
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    approved = approve_draft(store, draft_id=draft.id)

    client = TestClient(app)

    # 1. Test missing API key gets rejected with 403
    res_no_key = client.get("/api/factory/export/questions")
    assert res_no_key.status_code == 403

    # 2. Test valid API key returns 200 with approved questions
    res_valid = client.get("/api/factory/export/questions?api_key=test-secret")
    assert res_valid.status_code == 200
    res_json = res_valid.json()
    assert res_json["success"] is True
    assert res_json["count"] == 1
    assert res_json["questions"][0]["id"] == approved["questionId"]
    assert res_json["questions"][0]["answer_value"] == "A"


def test_history_pulls_previous_succeeded_run_after_failed_reprocess(tmp_path: Path):
    from datetime import datetime, timedelta, timezone

    store = InMemoryFactoryStore()
    factory_routes._STORE = store
    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")
    scaffold = create_ingestion_scaffold(
        title="History keep approved",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
        store=store,
        uploader=MemoryAssetUploader(),
    )
    QuestionFactoryIngestService(store, uploader=MemoryAssetUploader()).persist(
        context=scaffold.context,
        parse_result={
            "questions": [
                {
                    "question_ref": "1",
                    "question_type": "mcq",
                    "content": [{"type": "text", "value": "What is water?"}],
                    "options": {
                        "mode": "text",
                        "items": [
                            {"label": "A", "content": "H2O"},
                            {"label": "B", "content": "CO2"},
                            {"label": "C", "content": "O2"},
                            {"label": "D", "content": "N2"},
                        ],
                    },
                    "answer": {
                        "value": "A",
                        "explanation": "Mark scheme note",
                        "explanation_source": "mark_scheme",
                    },
                    "status": "NEEDS_REVIEW",
                    "source_page": 1,
                    "requires_review": True,
                }
            ]
        },
        assets_dir=tmp_path / "assets",
    )
    store.update_run_status(scaffold.context.run_id, "COMPLETED")
    store.update_batch_status(scaffold.context.batch_id, "READY_FOR_REVIEW")
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    approve_draft(store, draft_id=draft.id)

    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    store.create_run(
        {
            "batch_id": scaffold.context.batch_id,
            "parser_version": "test",
            "model_name": "none",
            "status": "FAILED",
            "started_at": later,
        }
    )
    store.update_batch_status(scaffold.context.batch_id, "HAS_ERRORS")

    client = TestClient(app)
    dash = client.get("/api/factory/dashboard?limit=0")
    assert dash.status_code == 200
    batch = next(b for b in dash.json()["imports"] if b["id"] == scaffold.context.batch_id)
    assert batch["questionCount"] == 1
    assert batch["approvedCount"] == 1
    assert batch["displayRunId"] == scaffold.context.run_id
    assert batch["latestRunStatus"] == "FAILED"

    drafts = client.get(f"/api/factory/batches/{scaffold.context.batch_id}/drafts")
    assert drafts.status_code == 200
    body = drafts.json()
    assert body["approved"] == 1
    assert any(d["status"] == "APPROVED" for d in body["drafts"])




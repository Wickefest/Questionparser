"""Tests for CanonicalParseResult → Question Factory mapper / ingest service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from questbank.factory.assets import MemoryAssetUploader
from questbank.factory.bootstrap import create_ingestion_scaffold
from questbank.factory.enums import (
    EnumMappingError,
    map_draft_status,
    map_explanation_source,
    map_question_type,
)
from questbank.factory.service import QuestionFactoryIngestService
from questbank.factory.store import InMemoryFactoryStore
from questbank.types.canonical import CompleteQuestionRecord


def _png(path: Path, color: tuple[int, int, int] = (20, 40, 60)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), color).save(path)
    return path


def _scaffold(store: InMemoryFactoryStore, tmp_path: Path):
    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 q")
    a.write_bytes(b"%PDF-1.4 a")
    return create_ingestion_scaffold(
        title="test-batch",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="6092/01",
        store=store,
        uploader=MemoryAssetUploader(),
        upload_pdfs=True,
    )


def _svc(store: InMemoryFactoryStore) -> QuestionFactoryIngestService:
    return QuestionFactoryIngestService(store, uploader=MemoryAssetUploader())


def test_1_plain_text_mcq(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    record = {
        "question_ref": "1",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "What is NaCl?"}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "salt"},
                {"label": "B", "content": "sugar"},
                {"label": "C", "content": "sand"},
                {"label": "D", "content": "water"},
            ],
        },
        "answer": {"value": "A", "explanation": None, "explanation_source": None},
        "status": "DRAFT",
        "source_page": 1,
        "requires_review": False,
    }
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=tmp_path / "assets",
    )
    assert report.questions_persisted == 1
    assert report.answers_persisted == 1
    assert report.assets_uploaded == 0
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    assert draft.question_type == "MCQ"
    assert draft.status == "DRAFT"
    assert draft.content[0]["value"] == "What is NaCl?"
    assert "asset" not in json.dumps(draft.content)
    answer = store.list_answers_for_run(scaffold.context.run_id)[0]
    assert answer.answer_value == "A"
    assert answer.explanation is None
    assert answer.explanation_source is None


def test_2_stem_image_asset(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    assets = tmp_path / "assets"
    _png(assets / "q01-stem-1.png")
    record = {
        "question_ref": "1",
        "question_type": "mcq",
        "content": [
            {"type": "text", "value": "See diagram."},
            {"type": "image", "asset": "q01-stem-1.png", "bbox": [10, 20, 30, 40]},
        ],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "answer": {"value": "B"},
        "status": "DRAFT",
        "source_page": 2,
    }
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=assets,
    )
    assert report.assets_uploaded == 1
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    image = next(b for b in draft.content if b["type"] == "image")
    assert "asset_id" in image
    assert "asset" not in image
    assert "bbox" not in image
    asset = store.list_assets_for_draft(draft.id)[0]
    assert asset.role == "STEM_IMAGE"
    assert asset.storage_path.startswith(
        f"question-assets/{scaffold.context.batch_id}/{scaffold.context.run_id}/"
    )
    assert asset.bbox == [10, 20, 30, 40]
    assert image["asset_id"] == asset.id


def test_3_composite_visual_options(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    assets = tmp_path / "assets"
    _png(assets / "q01-options.png", (80, 10, 10))
    record = {
        "question_ref": "1",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Which structure?"}],
        "options": {
            "mode": "composite_visual",
            "labels": ["A", "B", "C", "D"],
            "asset": "q01-options.png",
            "bbox": [1, 2, 3, 4],
        },
        "answer": {"value": "C"},
        "status": "DRAFT",
        "source_page": 3,
    }
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=assets,
    )
    assert report.assets_uploaded == 1
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    assert draft.options["mode"] == "composite_visual"
    assert "asset_id" in draft.options
    assert "asset" not in draft.options
    assets_rows = store.list_assets_for_draft(draft.id)
    assert len(assets_rows) == 1
    assert assets_rows[0].role == "COMPOSITE_OPTIONS"


def test_4_mark_scheme_explanation(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    record = {
        "question_ref": "5",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Q"}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "answer": {
            "value": "A",
            "explanation": "Official mark scheme wording.",
            "explanation_source": "mark_scheme",
        },
        "status": "DRAFT",
    }
    _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=tmp_path / "assets",
    )
    answer = store.list_answers_for_run(scaffold.context.run_id)[0]
    assert answer.explanation_source == "MARK_SCHEME"
    assert answer.explanation == "Official mark scheme wording."
    # Nested answer must not live on the draft JSON.
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    assert "answer" not in draft.content if isinstance(draft.content, dict) else True
    dumped = json.dumps({"content": draft.content, "options": draft.options})
    assert "mark_scheme" not in dumped
    assert "Official mark scheme" not in dumped


def test_5_answer_without_explanation(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    record = {
        "question_ref": "6",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Q"}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "answer": {"value": "D", "explanation": None, "explanation_source": None},
        "status": "DRAFT",
    }
    _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=tmp_path / "assets",
    )
    answer = store.list_answers_for_run(scaffold.context.run_id)[0]
    assert answer.answer_value == "D"
    assert answer.explanation is None
    assert answer.explanation_source is None


def test_6_enum_mapping():
    assert map_question_type("mcq") == "MCQ"
    assert map_draft_status("DRAFT") == "DRAFT"
    assert map_draft_status("NEEDS_REVIEW") == "NEEDS_REVIEW"
    assert map_explanation_source("mark_scheme") == "MARK_SCHEME"
    assert map_explanation_source("ai") == "AI"
    assert map_explanation_source(None) is None


def test_7_unknown_enum_rejected(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    with pytest.raises(EnumMappingError):
        map_question_type("true_false")
    with pytest.raises(EnumMappingError):
        map_draft_status("PUBLISHED")
    with pytest.raises(EnumMappingError):
        map_explanation_source("invented")

    bad = {
        "question_ref": "1",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Q"}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "answer": {"value": "A"},
        "status": "PUBLISHED",
    }
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [bad]},
        assets_dir=tmp_path / "assets",
    )
    assert report.questions_persisted == 0
    assert report.ok is False
    assert any("Unknown draft status" in e["error"] for e in report.record_errors)


def test_8_missing_referenced_asset(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    record = {
        "question_ref": "1",
        "question_type": "mcq",
        "content": [
            {"type": "image", "asset": "missing-stem.png"},
        ],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "answer": {"value": "A"},
        "status": "DRAFT",
    }
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=tmp_path / "assets",
    )
    assert report.ok is False
    assert report.assets_uploaded == 0
    draft = store.list_drafts_for_run(scaffold.context.run_id)[0]
    assert draft.status == "NEEDS_REVIEW"
    assert draft.requires_review is True
    assert draft.validation_issues
    assert any(i["code"] == "asset_missing" for i in draft.validation_issues)
    # Local filename must not be treated as a successful rewrite.
    assert draft.content[0].get("asset") == "missing-stem.png"
    assert "asset_id" not in draft.content[0]


def test_9_question_answer_ref_mismatch(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    questions = [
        {
            "question_ref": "1",
            "question_type": "mcq",
            "content": [{"type": "text", "value": "Q1"}],
            "options": {
                "mode": "text",
                "items": [
                    {"label": "A", "content": "a"},
                    {"label": "B", "content": "b"},
                    {"label": "C", "content": "c"},
                    {"label": "D", "content": "d"},
                ],
            },
            "answer": None,
            "status": "DRAFT",
        },
        {
            "question_ref": "2",
            "question_type": "mcq",
            "content": [{"type": "text", "value": "Q2"}],
            "options": {
                "mode": "text",
                "items": [
                    {"label": "A", "content": "a"},
                    {"label": "B", "content": "b"},
                    {"label": "C", "content": "c"},
                    {"label": "D", "content": "d"},
                ],
            },
            "answer": {"value": "B"},
            "status": "DRAFT",
        },
    ]
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": questions},
        assets_dir=tmp_path / "assets",
    )
    assert report.questions_persisted == 2
    assert report.answers_persisted == 1
    assert report.unmatched_questions == ["1"]
    draft1 = next(d for d in store.list_drafts_for_run(scaffold.context.run_id) if d.question_ref == "1")
    assert draft1.status == "NEEDS_REVIEW"
    assert draft1.requires_review is True


def test_10_duplicate_question_reference(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    base = {
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Q"}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "answer": {"value": "A"},
        "status": "DRAFT",
    }
    questions = [
        {**base, "question_ref": "7"},
        {**base, "question_ref": "7"},
    ]
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": questions},
        assets_dir=tmp_path / "assets",
    )
    assert report.ok is False
    assert report.duplicate_question_refs == ["7"]
    assert report.questions_persisted == 1
    assert any("duplicate" in e["error"] for e in report.record_errors)


def test_smoke_fixture_page8_persistence(tmp_path: Path):
    fixture = Path("output/smoke-page-8/matched_questions.json")
    if not fixture.is_file():
        pytest.skip("smoke-page-8 fixture not present")
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    data = json.loads(fixture.read_text(encoding="utf-8"))
    # Ensure records validate as canonical before persist.
    for q in data["questions"]:
        CompleteQuestionRecord.model_validate(q)
    assets_dir = fixture.parent / "assets"
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result=data,
        assets_dir=assets_dir if assets_dir.is_dir() else tmp_path / "assets",
    )
    assert report.questions_parsed == len(data["questions"])
    assert report.questions_persisted == len(data["questions"])
    drafts = store.list_drafts_for_run(scaffold.context.run_id)
    assert len(drafts) == len(data["questions"])
    sample = drafts[0]
    assert sample.question_type == "MCQ"
    assert isinstance(sample.content, list)
    assert isinstance(sample.options, dict)
    answers = store.list_answers_for_run(scaffold.context.run_id)
    assert len(answers) >= 1
    # Representative shape for reporting.
    assert sample.question_ref
    assert scaffold.context.batch_id in store.batches


def test_partial_persist_keeps_processing(tmp_path: Path):
    store = InMemoryFactoryStore()
    scaffold = _scaffold(store, tmp_path)
    record = {
        "question_ref": "1",
        "question_type": "mcq",
        "content": [{"type": "text", "value": "Partial page one."}],
        "options": {
            "mode": "text",
            "items": [
                {"label": "A", "content": "a"},
                {"label": "B", "content": "b"},
                {"label": "C", "content": "c"},
                {"label": "D", "content": "d"},
            ],
        },
        "status": "DRAFT",
        "source_page": 1,
        "requires_review": False,
    }
    report = _svc(store).persist(
        context=scaffold.context,
        parse_result={"questions": [record]},
        assets_dir=tmp_path / "assets",
        finalize=False,
    )
    assert report.ok is True
    assert report.questions_persisted == 1
    assert report.batch_status == "PROCESSING"
    assert report.run_status == "PROCESSING"
    assert store.get_batch(scaffold.context.batch_id)["status"] == "PROCESSING"
    assert store.get_run(scaffold.context.run_id)["status"] == "PROCESSING"

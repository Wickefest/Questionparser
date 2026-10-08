"""Import path durability: background job must not depend on deleted temp dirs."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from questbank.api import factory_routes
from questbank.api.app import app
from questbank.factory.assets import MemoryAssetUploader
from questbank.factory.bootstrap import create_ingestion_scaffold
from questbank.factory.jobs import create_batch_and_start, materialize_batch_source_pdfs
from questbank.factory.local_storage import LocalDiskAssetUploader, factory_storage_root
from questbank.factory.store import InMemoryFactoryStore


def test_create_batch_and_start_uses_durable_storage_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_STORAGE_ROOT", str(tmp_path / "storage"))
    store = InMemoryFactoryStore()

    staging = tmp_path / "staging"
    staging.mkdir()
    q = staging / "question-paper.pdf"
    a = staging / "answer-scheme.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")

    # Avoid launching a real parser thread that needs Gemini / full PDFs.
    started: dict = {}

    def fake_start(**kwargs):
        started.update(kwargs)
        from questbank.factory.jobs import JobState

        return JobState(batch_id=kwargs["batch_id"], run_id=kwargs["run_id"], status="PENDING")

    monkeypatch.setattr("questbank.factory.jobs.start_ingest_job", fake_start)

    result = create_batch_and_start(
        store=store,
        title="Test Paper",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
    )

    batch_id = result["batchId"]
    uploader = LocalDiskAssetUploader(root=factory_storage_root())
    durable_q = uploader.resolve_path(f"exam-sources/{batch_id}/question-paper.pdf")
    durable_a = uploader.resolve_path(f"exam-sources/{batch_id}/answer-scheme.pdf")
    assert durable_q is not None and durable_q.is_file()
    assert durable_a is not None and durable_a.is_file()

    # Job must point at durable copies, not the staging folder.
    assert Path(started["question_pdf"]) == durable_q
    assert Path(started["answer_pdf"]) == durable_a

    # Deleting staging must not remove what the job will read.
    q.unlink()
    a.unlink()
    assert Path(started["question_pdf"]).is_file()
    assert Path(started["answer_pdf"]).is_file()


class _CloudOnlyUploader:
    """Mirrors live Supabase: upload stores bytes remotely, no local resolve_path."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def upload(self, *, storage_path: str, data: bytes, content_type: str) -> str:
        self.blobs[storage_path] = data
        return storage_path


def test_create_batch_and_start_materializes_when_uploader_has_no_local_path(tmp_path, monkeypatch):
    """Live server: PDFs live in object storage; the job still needs a Path."""
    monkeypatch.setenv("FACTORY_STORAGE_ROOT", str(tmp_path / "storage"))
    store = InMemoryFactoryStore()
    cloud = _CloudOnlyUploader()
    monkeypatch.setattr("questbank.factory.jobs.create_asset_uploader", lambda: cloud)

    staging = tmp_path / "staging"
    staging.mkdir()
    q = staging / "question-paper.pdf"
    a = staging / "answer-scheme.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")

    started: dict = {}

    def fake_start(**kwargs):
        started.update(kwargs)
        from questbank.factory.jobs import JobState

        return JobState(batch_id=kwargs["batch_id"], run_id=kwargs["run_id"], status="PENDING")

    monkeypatch.setattr("questbank.factory.jobs.start_ingest_job", fake_start)

    result = create_batch_and_start(
        store=store,
        title="Live Paper",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
    )

    batch_id = result["batchId"]
    assert f"exam-sources/{batch_id}/question-paper.pdf" in cloud.blobs
    assert Path(started["question_pdf"]).is_file()
    assert Path(started["answer_pdf"]).is_file()
    assert Path(started["question_pdf"]) != q

    q.unlink()
    a.unlink()
    assert Path(started["question_pdf"]).is_file()
    assert Path(started["answer_pdf"]).read_bytes() == b"%PDF-1.4 answer"


def test_reprocess_downloads_from_remote_storage(tmp_path, monkeypatch):
    """After a live restart, local disk is empty; reprocess must fetch stored PDFs."""
    monkeypatch.setenv("FACTORY_STORAGE_ROOT", str(tmp_path / "storage"))
    store = InMemoryFactoryStore()
    factory_routes._STORE = store

    q = tmp_path / "q.pdf"
    a = tmp_path / "a.pdf"
    q.write_bytes(b"%PDF-1.4 question")
    a.write_bytes(b"%PDF-1.4 answer")
    scaffold = create_ingestion_scaffold(
        title="Remote reprocess",
        subject="Chemistry",
        question_pdf=q,
        answer_pdf=a,
        exam_year=2023,
        paper_code="Paper 1",
        store=store,
        uploader=MemoryAssetUploader(),
    )
    batch_id = scaffold.context.batch_id
    blobs = {
        f"exam-sources/{batch_id}/question-paper.pdf": q.read_bytes(),
        f"exam-sources/{batch_id}/answer-scheme.pdf": a.read_bytes(),
    }
    monkeypatch.setattr(
        "questbank.factory.local_storage._remote_storage_bytes",
        lambda path: (blobs[path], "application/pdf") if path in blobs else None,
    )

    disk = LocalDiskAssetUploader(root=factory_storage_root())
    assert disk.resolve_path(f"exam-sources/{batch_id}/question-paper.pdf") is None

    q_path, a_path = materialize_batch_source_pdfs(store, batch_id)
    assert q_path.is_file()
    assert a_path.read_bytes() == b"%PDF-1.4 answer"

    started: dict = {}

    def fake_start(**kwargs):
        started.update(kwargs)
        from questbank.factory.jobs import JobState

        return JobState(batch_id=kwargs["batch_id"], run_id=kwargs["run_id"], status="PENDING")

    monkeypatch.setattr("questbank.factory.jobs.start_ingest_job", fake_start)
    factory_routes._UPLOAD_CACHE.clear()
    client = TestClient(app)
    res = client.post(f"/api/factory/batches/{batch_id}/reprocess")
    assert res.status_code == 200, res.text
    assert Path(started["question_pdf"]).is_file()

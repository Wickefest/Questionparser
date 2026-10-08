"""Background ingestion jobs for Question Factory UI."""

from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from questbank.factory.bootstrap import create_ingestion_scaffold
from questbank.factory.context import IngestionContext
from questbank.factory.local_storage import create_asset_uploader, materialize_storage_path
from questbank.factory.service import QuestionFactoryIngestService
from questbank.factory.store import FactoryStore
from questbank.ingest.runner import PARSER_VERSIONS, run_mcq_ingest, run_structured_ingest
from questbank.parsers.chemistry.detect_paper_type import detect_paper_type
from questbank.types.ingest import PaperIdentity

logger = logging.getLogger(__name__)


@dataclass
class JobState:
    batch_id: str
    run_id: str
    status: str = "PENDING"  # PENDING | RUNNING | COMPLETED | FAILED
    stage: str = "queued"
    message: str = ""
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)


_JOBS: dict[str, JobState] = {}
_LOCK = threading.Lock()
_CANCELLED_BATCH_IDS: set[str] = set()
# Cap concurrent ingest threads — each opens its own Postgres session.
_INGEST_SEMAPHORE = threading.Semaphore(2)


def get_job(batch_id: str) -> JobState | None:
    with _LOCK:
        return _JOBS.get(batch_id)


def _set_job(job: JobState) -> None:
    with _LOCK:
        _JOBS[job.batch_id] = job


def cancel_job(batch_id: str) -> bool:
    global _CANCELLED_BATCH_IDS
    with _LOCK:
        job = _JOBS.get(batch_id)
        if job and job.status in {"PENDING", "RUNNING"}:
            job.status = "FAILED"
            job.stage = "cancelled"
            job.message = "Cancelled by user"
            job.completed_at = datetime.now(timezone.utc)
            _CANCELLED_BATCH_IDS.add(batch_id)
            return True
        return False


def start_ingest_job(
    *,
    store: FactoryStore,
    batch_id: str,
    run_id: str,
    question_pdf: Path,
    answer_pdf: Path,
    title: str,
    subject: str,
    exam_year: int | None,
    paper_code: str | None,
    expected_question_count: int | None = None,
    context: IngestionContext,
    output_root: str | Path = "output",
) -> JobState:
    job = JobState(batch_id=batch_id, run_id=run_id, status="PENDING", stage="queued")
    _set_job(job)

    thread = threading.Thread(
        target=_run_job,
        kwargs={
            "store": store,
            "job_batch_id": batch_id,
            "question_pdf": Path(question_pdf),
            "answer_pdf": Path(answer_pdf),
            "title": title,
            "subject": subject,
            "exam_year": exam_year,
            "paper_code": paper_code,
            "expected_question_count": expected_question_count,
            "context": context,
            "output_root": Path(output_root),
        },
        daemon=True,
        name=f"factory-ingest-{batch_id[:8]}",
    )
    thread.start()
    return job


def _run_job(
    *,
    store: FactoryStore,
    job_batch_id: str,
    question_pdf: Path,
    answer_pdf: Path,
    title: str,
    subject: str,
    exam_year: int | None,
    paper_code: str | None,
    expected_question_count: int | None,
    context: IngestionContext,
    output_root: Path,
) -> None:
    # Fresh DB connection for this thread (psycopg connections are not thread-safe).
    # Wait for an ingest slot first so we do not exhaust the Supabase pool (max ~15).
    from questbank.factory.store import InMemoryFactoryStore, PostgresFactoryStore
    import os

    job = get_job(job_batch_id) or JobState(batch_id=job_batch_id, run_id=context.run_id)
    job.status = "PENDING"
    job.stage = "queued"
    job.message = "Waiting for ingest slot…"
    _set_job(job)

    with _INGEST_SEMAPHORE:
        if job_batch_id in _CANCELLED_BATCH_IDS:
            job.status = "FAILED"
            job.stage = "cancelled"
            job.message = "Cancelled by user"
            job.completed_at = datetime.now(timezone.utc)
            _set_job(job)
            return

        thread_store: FactoryStore
        own = False
        if isinstance(store, InMemoryFactoryStore):
            thread_store = store
        else:
            dsn = (os.environ.get("DATABASE_URL") or "").strip()
            thread_store = PostgresFactoryStore(dsn=dsn) if dsn else InMemoryFactoryStore()
            own = True

        job = get_job(job_batch_id) or JobState(batch_id=job_batch_id, run_id=context.run_id)
        job.status = "RUNNING"
        job.stage = "parsing_questions"
        job.started_at = datetime.now(timezone.utc)
        job.message = "Parsing question paper"
        _set_job(job)

        try:
            _execute_ingest_job(
                thread_store=thread_store,
                job=job,
                job_batch_id=job_batch_id,
                question_pdf=question_pdf,
                answer_pdf=answer_pdf,
                title=title,
                subject=subject,
                exam_year=exam_year,
                paper_code=paper_code,
                expected_question_count=expected_question_count,
                context=context,
                output_root=output_root,
            )
        finally:
            if own and hasattr(thread_store, "close"):
                try:
                    thread_store.close()
                except Exception:  # noqa: BLE001
                    pass


def _execute_ingest_job(
    *,
    thread_store: FactoryStore,
    job: JobState,
    job_batch_id: str,
    question_pdf: Path,
    answer_pdf: Path,
    title: str,
    subject: str,
    exam_year: int | None,
    paper_code: str | None,
    expected_question_count: int | None,
    context: IngestionContext,
    output_root: Path,
) -> None:
    question_pdf = Path(question_pdf)
    answer_pdf = Path(answer_pdf)
    if not question_pdf.is_file() or not answer_pdf.is_file():
        q_fallback = materialize_storage_path(
            f"exam-sources/{job_batch_id}/question-paper.pdf"
        )
        a_fallback = materialize_storage_path(
            f"exam-sources/{job_batch_id}/answer-scheme.pdf"
        )
        if q_fallback is not None and a_fallback is not None:
            question_pdf, answer_pdf = q_fallback, a_fallback
            logger.info(
                "Materialized source PDFs from storage for batch %s", job_batch_id
            )
        else:
            job.status = "FAILED"
            job.stage = "failed"
            job.error = f"Question PDF not found: {question_pdf}"
            job.message = job.error
            job.completed_at = datetime.now(timezone.utc)
            _set_job(job)
            try:
                thread_store.complete_run(
                    context.run_id,
                    status="FAILED",
                    completed_at=job.completed_at,
                )
                thread_store.update_batch_status(job_batch_id, "HAS_ERRORS")
            except Exception:  # noqa: BLE001
                pass
            return

    try:
        thread_store.update_batch_status(job_batch_id, "PROCESSING")
        thread_store.update_run_status(context.run_id, "PROCESSING")

        uploader = create_asset_uploader()
        kind = detect_paper_type(
            question_pdf,
            answer_pdf,
            title=title,
            paper_code=paper_code,
        )
        identity = PaperIdentity(
            source_exam_year=exam_year or 0,
            paper_code=paper_code or ("6092/02" if kind == "structured" else "6092/01"),
            paper_type=kind,
        )

        job.stage = "parsing"
        job.message = f"Detecting paper type · {kind}"
        _set_job(job)

        def on_progress(message: str) -> None:
            if job_batch_id in _CANCELLED_BATCH_IDS:
                raise RuntimeError("INGEST_CANCELLED")
            job.message = message
            job.stage = "parsing"
            _set_job(job)

        ingest_kwargs = dict(
            question_pdf=question_pdf,
            answer_pdf=answer_pdf,
            output_root=output_root,
            run_id=f"factory-{job_batch_id[:8]}-{uuid4().hex[:6]}",
            paper_identity=identity,
            expected_question_count=expected_question_count,
            title=title,
            subject=subject,
            persist_factory=True,
            factory_store=thread_store,
            factory_uploader=uploader,
            ingestion_context=context,
            check_gemini=True,
            require_full_answer_match=False,
            on_progress=on_progress,
        )
        if kind == "structured":
            job.message = "Running structured parser pipeline"
            _set_job(job)
            # Prefer native PDF text first; full OCR has crashed the Windows
            # serve process (RapidOCR/onnx access violation) on some papers.
            manifest = run_structured_ingest(**ingest_kwargs, ocr=False)
        else:
            job.message = "Running MCQ parser pipeline"
            _set_job(job)
            manifest = run_mcq_ingest(**ingest_kwargs)

        failed = [s for s in manifest.stages if s.status == "failed"]
        factory_stage = next((s for s in manifest.stages if s.name == "factory_persist"), None)
        persisted = 0
        if factory_stage and factory_stage.details:
            persisted = int(factory_stage.details.get("questionsPersisted") or 0)

        # Validation / incomplete matching are review flags, not a dead import.
        hard_fail = persisted <= 0 and any(
            s.name in {"parse_questions", "parse_answers", "factory_persist"} and s.status == "failed"
            for s in manifest.stages
        )
        if hard_fail or (failed and persisted <= 0):
            first = next(
                (s for s in failed if s.name in {"parse_questions", "parse_answers", "factory_persist"}),
                failed[0] if failed else None,
            )
            job.status = "FAILED"
            job.stage = first.name if first else "failed"
            job.message = (first.message if first else None) or "Processing failed"
            job.error = job.message
            job.completed_at = datetime.now(timezone.utc)
            job.details = {
                "stages": [s.model_dump(by_alias=True) for s in manifest.stages],
            }
            thread_store.update_run_status(
                context.run_id,
                "FAILED",
                completed_at=job.completed_at,
            )
            thread_store.update_batch_status(job_batch_id, "HAS_ERRORS")
            _set_job(job)
            return

        job.status = "COMPLETED"
        job.stage = "complete"
        job.message = f"Processing complete · {persisted or 'questions'} saved"
        job.completed_at = datetime.now(timezone.utc)
        job.details = {
            "stages": [s.model_dump(by_alias=True) for s in manifest.stages],
            "factory": factory_stage.details if factory_stage else None,
            "paperType": kind,
        }
        _set_job(job)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Factory ingest job failed for batch %s", job_batch_id)
        if job_batch_id in _CANCELLED_BATCH_IDS:
            try:
                thread_store.update_run_status(
                    context.run_id,
                    "FAILED",
                    completed_at=datetime.now(timezone.utc),
                )
                thread_store.update_batch_status(job_batch_id, "HAS_ERRORS")
            except Exception:  # noqa: BLE001
                pass
            job.status = "FAILED"
            job.stage = "cancelled"
            job.message = "Cancelled by user"
            job.completed_at = datetime.now(timezone.utc)
            _set_job(job)
            return

        job.status = "FAILED"
        job.stage = "error"
        job.message = str(exc)
        job.error = str(exc)
        job.completed_at = datetime.now(timezone.utc)
        job.details = {"trace": traceback.format_exc()[-2000:]}
        try:
            thread_store.update_run_status(
                context.run_id,
                "FAILED",
                completed_at=job.completed_at,
            )
            thread_store.update_batch_status(job_batch_id, "HAS_ERRORS")
        except Exception:  # noqa: BLE001
            pass
        _set_job(job)


def create_batch_and_start(
    *,
    store: FactoryStore,
    title: str,
    subject: str,
    question_pdf: Path,
    answer_pdf: Path,
    exam_year: int | None = None,
    paper_code: str | None = None,
    syllabus_id: str | None = None,
    expected_question_count: int | None = None,
    output_root: str | Path = "output",
) -> dict[str, Any]:
    """Create scaffold, then start background parse. Returns immediately.

    PDFs are uploaded to durable factory storage during scaffold creation.
    The background job always reads from local working copies materialized
    from those storage objects — never from a request-scoped temp directory
    that the HTTP handler may delete. On a live server the durable copy lives
    in Supabase; this step downloads/copies it so PyMuPDF can open a Path.
    """
    uploader = create_asset_uploader()
    scaffold = create_ingestion_scaffold(
        title=title,
        subject=subject,
        question_pdf=question_pdf,
        answer_pdf=answer_pdf,
        exam_year=exam_year,
        paper_code=paper_code,
        syllabus_id=syllabus_id,
        parser_version=PARSER_VERSIONS.get("questbank"),
        model_name=PARSER_VERSIONS.get("mcq_engine"),
        store=store,
        uploader=uploader,
        upload_pdfs=True,
    )

    batch_id = scaffold.context.batch_id
    q_storage = f"exam-sources/{batch_id}/question-paper.pdf"
    a_storage = f"exam-sources/{batch_id}/answer-scheme.pdf"
    q_job = materialize_storage_path(q_storage, source=Path(question_pdf))
    a_job = materialize_storage_path(a_storage, source=Path(answer_pdf))

    if q_job is None or not q_job.is_file() or a_job is None or not a_job.is_file():
        raise FileNotFoundError(
            f"Durable source PDFs missing after upload for batch {batch_id}"
        )

    start_ingest_job(
        store=store,
        batch_id=batch_id,
        run_id=scaffold.context.run_id,
        question_pdf=q_job,
        answer_pdf=a_job,
        title=title,
        subject=subject,
        exam_year=exam_year,
        paper_code=paper_code,
        expected_question_count=expected_question_count,
        context=scaffold.context,
        output_root=output_root,
    )
    return {
        "batchId": batch_id,
        "runId": scaffold.context.run_id,
        "questionDocumentId": scaffold.context.question_document_id,
        "answerDocumentId": scaffold.context.answer_document_id,
    }


def materialize_batch_source_pdfs(store: FactoryStore, batch_id: str) -> tuple[Path, Path]:
    """Resolve question + answer PDFs from stored storage_path (local or remote)."""
    docs = store.list_documents_for_batch(batch_id)
    q_doc = next((d for d in docs if d.get("document_type") == "QUESTION_PAPER"), None)
    a_doc = next((d for d in docs if d.get("document_type") == "ANSWER_SCHEME"), None)
    if not q_doc or not a_doc:
        raise ValueError("Batch is missing source documents")
    q_path = materialize_storage_path(q_doc["storage_path"])
    a_path = materialize_storage_path(a_doc["storage_path"])
    if q_path is None or a_path is None:
        raise FileNotFoundError(
            f"Source PDFs are not available in storage for batch {batch_id}"
        )
    return q_path, a_path


def reprocess_batch(
    *,
    store: FactoryStore,
    batch_id: str,
    question_pdf: Path,
    answer_pdf: Path,
    expected_question_count: int | None = None,
    output_root: str | Path = "output",
) -> dict[str, Any]:
    """Create a NEW IngestionRun for an existing batch and re-parse."""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise KeyError(f"Batch not found: {batch_id}")

    docs = store.list_documents_for_batch(batch_id)
    q_doc = next((d for d in docs if d.get("document_type") == "QUESTION_PAPER"), None)
    a_doc = next((d for d in docs if d.get("document_type") == "ANSWER_SCHEME"), None)
    if not q_doc or not a_doc:
        raise ValueError("Batch is missing source documents")

    run = store.create_run(
        {
            "id": str(uuid4()),
            "batch_id": batch_id,
            "parser_version": PARSER_VERSIONS.get("questbank"),
            "model_name": PARSER_VERSIONS.get("mcq_engine"),
            "status": "PROCESSING",
        }
    )
    context = IngestionContext(
        batch_id=batch_id,
        run_id=run["id"],
        question_document_id=q_doc["id"],
        answer_document_id=a_doc["id"],
    )
    store.update_batch_status(batch_id, "PROCESSING")
    start_ingest_job(
        store=store,
        batch_id=batch_id,
        run_id=run["id"],
        question_pdf=question_pdf,
        answer_pdf=answer_pdf,
        title=batch.get("title") or "reprocess",
        subject=batch.get("subject") or "Chemistry",
        exam_year=batch.get("exam_year"),
        paper_code=batch.get("paper_code"),
        expected_question_count=expected_question_count,
        context=context,
        output_root=output_root,
    )
    return {"batchId": batch_id, "runId": run["id"]}

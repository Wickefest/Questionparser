"""Create IngestionBatch / SourceDocument / IngestionRun before parsing."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from questbank.factory.assets import AssetUploader, MemoryAssetUploader
from questbank.factory.context import IngestionContext
from questbank.factory.store import FactoryStore, InMemoryFactoryStore, PostgresFactoryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionScaffold:
    context: IngestionContext
    batch: dict[str, Any]
    run: dict[str, Any]
    question_document: dict[str, Any]
    answer_document: dict[str, Any] | None


def create_ingestion_scaffold(
    *,
    title: str,
    subject: str,
    question_pdf: str | Path,
    answer_pdf: str | Path | None = None,
    exam_year: int | None = None,
    paper_code: str | None = None,
    syllabus_id: str | None = None,
    parser_version: str | None = None,
    model_name: str | None = None,
    store: FactoryStore | None = None,
    uploader: AssetUploader | None = None,
    upload_pdfs: bool = True,
) -> IngestionScaffold:
    """Create batch + source documents + run; return IngestionContext.

    PDFs are uploaded to Storage when ``upload_pdfs`` and an uploader
    are available. Local paths are never stored as production ``storage_path``.
    """
    store = store or _default_store()
    question_pdf = Path(question_pdf)
    answer_pdf_path = Path(answer_pdf) if answer_pdf else None

    batch = store.create_batch(
        {
            "id": str(uuid4()),
            "title": title,
            "subject": subject,
            "exam_year": exam_year,
            "paper_code": paper_code,
            "syllabus_id": syllabus_id,
            "uploaded_by": None,
            "status": "PROCESSING",
        }
    )
    batch_id = batch["id"]

    active_uploader = uploader
    if upload_pdfs and active_uploader is None:
        try:
            from questbank.factory.local_storage import create_asset_uploader

            active_uploader = create_asset_uploader()
        except Exception:  # noqa: BLE001
            logger.warning("Uploader unavailable; using memory uploader for PDF paths")
            active_uploader = MemoryAssetUploader()

    q_bytes = question_pdf.read_bytes()
    q_storage = f"exam-sources/{batch_id}/question-paper.pdf"
    if upload_pdfs and active_uploader is not None:
        active_uploader.upload(
            storage_path=q_storage,
            data=q_bytes,
            content_type="application/pdf",
        )

    question_document = store.create_source_document(
        {
            "id": str(uuid4()),
            "batch_id": batch_id,
            "document_type": "QUESTION_PAPER",
            "storage_path": q_storage,
            "original_name": question_pdf.name,
            "mime_type": "application/pdf",
            "page_count": None,
            "sha256": hashlib.sha256(q_bytes).hexdigest(),
        }
    )

    answer_document = None
    answer_document_id = None
    if answer_pdf_path is not None:
        a_bytes = answer_pdf_path.read_bytes()
        a_storage = f"exam-sources/{batch_id}/answer-scheme.pdf"
        if upload_pdfs and active_uploader is not None:
            active_uploader.upload(
                storage_path=a_storage,
                data=a_bytes,
                content_type="application/pdf",
            )
        answer_document = store.create_source_document(
            {
                "id": str(uuid4()),
                "batch_id": batch_id,
                "document_type": "ANSWER_SCHEME",
                "storage_path": a_storage,
                "original_name": answer_pdf_path.name,
                "mime_type": "application/pdf",
                "page_count": None,
                "sha256": hashlib.sha256(a_bytes).hexdigest(),
            }
        )
        answer_document_id = answer_document["id"]

    run = store.create_run(
        {
            "id": str(uuid4()),
            "batch_id": batch_id,
            "parser_version": parser_version,
            "model_name": model_name,
            "status": "PROCESSING",
        }
    )

    context = IngestionContext(
        batch_id=batch_id,
        run_id=run["id"],
        question_document_id=question_document["id"],
        answer_document_id=answer_document_id,
    )
    return IngestionScaffold(
        context=context,
        batch=batch,
        run=run,
        question_document=question_document,
        answer_document=answer_document,
    )


def _default_store() -> FactoryStore:
    import os

    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if dsn:
        return PostgresFactoryStore(dsn=dsn)
    return InMemoryFactoryStore()

"""Question Factory admin HTTP API (FastAPI)."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Query
from fastapi.responses import Response
from pydantic import BaseModel


from questbank.factory.jobs import (
    create_batch_and_start,
    get_job,
    materialize_batch_source_pdfs,
    reprocess_batch,
)
from questbank.factory.local_storage import resolve_storage_bytes
from questbank.factory.mapping import ensure_list_blocks
from questbank.factory.review import (
    approve_all_in_batch,
    approve_draft,
    create_manual_draft,
    edit_draft,
    next_unresolved_draft_id,
    reject_draft,
    replace_draft_asset,
    suggest_question_ref,
)
from questbank.factory.store import FactoryStore, InMemoryFactoryStore, PostgresFactoryStore

router = APIRouter(prefix="/api/factory", tags=["question-factory"])

_STORE: FactoryStore | None = None
_UPLOAD_CACHE: dict[str, Path] = {}


def get_store() -> FactoryStore:
    global _STORE
    if _STORE is not None:
        if hasattr(_STORE, "ensure_ready"):
            _STORE.ensure_ready()  # type: ignore[union-attr]
        return _STORE
    import os

    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if dsn:
        try:
            _STORE = PostgresFactoryStore(dsn=dsn)
            return _STORE
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Database unavailable: {exc}") from exc
    _STORE = InMemoryFactoryStore()
    return _STORE


def get_thread_safe_store() -> FactoryStore:
    import os
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if dsn:
        return PostgresFactoryStore(dsn=dsn)
    return InMemoryFactoryStore()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _draft_preview(content: Any, options: Any = None) -> str:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = str(block.get("value") or "").strip()
                if text:
                    return text[:160]
    if isinstance(options, dict) and options.get("mode") == "structured":
        stem = str(options.get("stem") or "").strip()
        if stem:
            return stem[:160]
        parts = options.get("parts") or []
        for part in parts:
            if not isinstance(part, dict):
                continue
            prompt = str(part.get("prompt") or "").strip()
            label = str(part.get("label") or "").strip()
            if prompt:
                return f"({label}) {prompt}"[:160] if label else prompt[:160]
    return ""


def _has_visual(content: Any, options: Any) -> bool:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                return True
    if isinstance(options, dict) and options.get("mode") in {"composite_visual", "mixed"}:
        return True
    return False


def _collect_asset_ids(content: Any, options: Any) -> list[str]:
    """Collect every asset_id referenced anywhere in a question's content/options,
    including per-part visuals nested in structured questions (options.parts[]
    and options.navigationOrder[], with children walked recursively) - not just
    top-level content-image blocks and the MCQ options.asset_id case. Missing
    this nested walk was why structured per-part images never resolved to a
    URL outside the draft-review screen (which instead lists assets by
    draft_id, sidestepping the JSON structure entirely)."""
    ids: list[str] = []
    seen: set[str] = set()

    def add(asset_id: Any) -> None:
        if isinstance(asset_id, str) and asset_id and asset_id not in seen:
            seen.add(asset_id)
            ids.append(asset_id)

    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                add(block.get("asset_id"))

    if isinstance(options, dict):
        add(options.get("asset_id"))

        def walk_parts(parts: Any) -> None:
            for part in parts or []:
                if not isinstance(part, dict):
                    continue
                for vis in part.get("visuals") or []:
                    if isinstance(vis, dict):
                        add(vis.get("asset_id"))
                if part.get("children"):
                    walk_parts(part["children"])

        walk_parts(options.get("parts"))
        walk_parts(options.get("navigationOrder"))

    return ids


def _versioned_asset_url(asset_id: str, sha256: str | None) -> str:
    """Content-address the asset URL so replacing an image (same asset_id,
    new bytes) yields a new URL everywhere it's read from, instead of every
    consumer (review UI reload, Simulated Test, Question Bank) continuing to
    serve whatever their browser/proxy had already cached for the old bytes."""
    if sha256:
        return f"/api/factory/assets/{asset_id}?v={sha256[:12]}"
    return f"/api/factory/assets/{asset_id}"


def _asset_urls_for_payload(store: FactoryStore, content: Any, options: Any) -> dict[str, str]:
    urls: dict[str, str] = {}
    for asset_id in _collect_asset_ids(content, options):
        asset = store.get_asset(asset_id)
        urls[asset_id] = _versioned_asset_url(asset_id, asset.sha256 if asset else None)
    return urls


def _asset_urls_for_payloads_batch(
    store: FactoryStore, pairs: list[tuple[Any, Any]]
) -> list[dict[str, str]]:
    """Same result as calling _asset_urls_for_payload per (content, options) pair,
    but resolves every referenced asset_id across all pairs in a single batched
    query instead of one DB round-trip per asset per question (list_bank_questions
    previously called get_asset() once per asset per question, which meant a
    listing of N questions with M assets each did N*M sequential round-trips to
    the remote Supabase Postgres — this is what made the Question Bank page
    take minutes, or appear to hang, to load)."""
    per_pair_ids = [_collect_asset_ids(content, options) for content, options in pairs]
    all_ids = sorted({aid for ids in per_pair_ids for aid in ids})
    assets_by_id = {a.id: a for a in store.list_assets_by_ids(all_ids)}
    results = []
    for ids in per_pair_ids:
        urls = {
            aid: _versioned_asset_url(aid, assets_by_id[aid].sha256 if aid in assets_by_id else None)
            for aid in ids
        }
        results.append(urls)
    return results


def _serialize_draft(store: FactoryStore, draft) -> dict[str, Any]:
    answer = store.get_answer(draft.run_id, draft.question_ref)
    assets = store.list_assets_for_draft(draft.id)
    asset_urls = {
        a.id: _versioned_asset_url(a.id, a.sha256)
        for a in assets
    }
    content = ensure_list_blocks(draft.content)
    return {
        "id": draft.id,
        "runId": draft.run_id,
        "batchId": draft.batch_id,
        "questionRef": draft.question_ref,
        "questionType": draft.question_type,
        "content": content,
        "options": draft.options,
        "sourcePage": draft.source_page,
        "sourceRegions": draft.source_regions,
        "status": draft.status,
        "requiresReview": draft.requires_review,
        "validationIssues": draft.validation_issues or [],
        "preview": _draft_preview(content, draft.options),
        "hasVisual": _has_visual(content, draft.options),
        "classification": (
            {
                "topicCode": draft.topic_code,
                "title": draft.topic_title,
                "subtopicCode": draft.subtopic_code,
                "subtopicTitle": draft.subtopic_title,
            }
            if draft.topic_node_id
            else None
        ),
        "bloomLevel": draft.bloom_level,
        "bloomReasoning": draft.bloom_reasoning,
        "answer": (
            {
                "value": answer.answer_value,
                "partsAnswer": getattr(answer, "parts_answer", None),
                "explanation": answer.explanation,
                "explanationSource": answer.explanation_source,
                "sourcePage": answer.source_page,
            }
            if answer
            else None
        ),
        "assets": [
            {
                "id": a.id,
                "role": a.role,
                "url": asset_urls[a.id],
                "mimeType": a.mime_type,
                "sourcePage": a.source_page,
                "width": a.width,
                "height": a.height,
            }
            for a in assets
        ],
        "assetUrls": asset_urls,
    }


class DuplicateCheckRequest(BaseModel):
    questionSha256: str
    answerSha256: str


class EditDraftRequest(BaseModel):
    content: Any
    options: Any
    comment: str | None = None
    answerValue: str | None = None
    explanation: str | None = None
    partsAnswer: Any | None = None
    questionRef: str | None = None


class RejectDraftRequest(BaseModel):
    reason: str | None = None


class CreateManualDraftRequest(BaseModel):
    questionRef: str | None = None
    stem: str = ""
    options: dict[str, str] | None = None  # {"A": "...", "B": "...", ...} — MCQ only
    answerValue: str | None = None  # required for MCQ, ignored for STRUCTURED
    explanation: str | None = None
    questionType: str = "MCQ"  # "MCQ" | "STRUCTURED"


@router.get("/health")
def factory_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/dashboard")
def dashboard(limit: int = Query(0, ge=0, le=5000)) -> dict[str, Any]:
    """Dashboard metrics + import list.

    ``limit=0`` (default) returns all batches so the UI can show full history.
    """
    store = get_store()
    metrics = store.dashboard_metrics()
    batches = store.list_batches(limit=None if limit == 0 else limit)
    return {
        "metrics": metrics,
        "recentImports": batches,
        "imports": batches,
        "totalImports": len(batches),
    }


@router.get("/batches")
def list_import_batches(limit: int = Query(0, ge=0, le=5000)) -> dict[str, Any]:
    store = get_store()
    batches = store.list_batches(limit=None if limit == 0 else limit)
    return {"batches": batches, "total": len(batches)}


@router.get("/syllabi")
def list_syllabi() -> dict[str, Any]:
    from questbank.factory.syllabus_service import SUBJECT_PRESETS

    return {
        "syllabi": get_store().list_syllabi(),
        "subjectPresets": SUBJECT_PRESETS,
    }


@router.get("/syllabi/{syllabus_id}")
def get_syllabus(syllabus_id: str) -> dict[str, Any]:
    detail = get_store().get_syllabus(syllabus_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    return detail


class CreateSyllabusRequest(BaseModel):
    subject: str
    code: str
    year: int
    title: str | None = None


@router.post("/syllabi")
def create_syllabus(body: CreateSyllabusRequest) -> dict[str, Any]:
    from questbank.factory.syllabus_service import SUBJECT_PRESETS, create_syllabus_record

    store = get_store()
    subject = body.subject.strip()
    code = body.code.strip()
    if not subject or not code:
        raise HTTPException(status_code=400, detail="Subject and code are required")
    title = (body.title or "").strip()
    if not title:
        preset = next((p for p in SUBJECT_PRESETS if p["subject"] == subject), None)
        title = (preset or {}).get("defaultTitle") or f"{subject} {code} · {body.year}"
    try:
        created = create_syllabus_record(
            store,
            subject=subject,
            code=code,
            year=body.year,
            title=title,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            raise HTTPException(
                status_code=409,
                detail=f"A syllabus with code {code} for year {body.year} already exists.",
            ) from exc
        raise HTTPException(status_code=500, detail=msg) from exc
    return store.get_syllabus(created["id"]) or created


@router.post("/syllabi/{syllabus_id}/import-pdf")
async def import_syllabus_pdf(
    syllabus_id: str,
    syllabus_pdf: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse a syllabus PDF into topic / subtopic / learning-objective nodes."""
    from questbank.factory.syllabus_service import persist_taxonomy_nodes
    from questbank.syllabus.parse_syllabus_pdf import parse_syllabus_pdf

    store = get_store()
    existing = store.get_syllabus(syllabus_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    if not syllabus_pdf.filename or not syllabus_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="syllabus_pdf must be a PDF")

    with tempfile.TemporaryDirectory(prefix="syllabus-") as tmp:
        path = Path(tmp) / "syllabus.pdf"
        path.write_bytes(await syllabus_pdf.read())
        try:
            taxonomy = parse_syllabus_pdf(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Failed to parse syllabus PDF: {exc}") from exc
        counts = persist_taxonomy_nodes(store, syllabus_id=syllabus_id, taxonomy=taxonomy)

    detail = store.get_syllabus(syllabus_id)
    return {"ok": True, "counts": counts, "syllabus": detail}


@router.post("/imports/check-duplicates")
def check_duplicates(body: DuplicateCheckRequest) -> dict[str, Any]:
    store = get_store()
    q_docs = store.find_documents_by_sha256(body.questionSha256)
    a_docs = store.find_documents_by_sha256(body.answerSha256)

    q_batches = {d["batch_id"] for d in q_docs}
    a_batches = {d["batch_id"] for d in a_docs}
    both = q_batches & a_batches

    if both:
        batch_id = next(iter(both))
        detail = store.get_batch_detail(batch_id)
        return {
            "status": "exact_duplicate",
            "message": "This paper appears to have already been imported.",
            "existingBatch": detail,
        }

    if q_docs and not both:
        batch_id = q_docs[0]["batch_id"]
        detail = store.get_batch_detail(batch_id)
        return {
            "status": "question_exists_answer_differs",
            "message": (
                "The question paper already exists, but the mark scheme is different. "
                "This may be a revised answer scheme."
            ),
            "existingBatch": detail,
        }

    return {"status": "new", "message": None, "existingBatch": None}


@router.post("/imports")
async def create_import(
    question_pdf: UploadFile = File(...),
    answer_pdf: UploadFile = File(...),
    title: str = Form(...),
    subject: str = Form(default="Chemistry"),
    exam_year: int | None = Form(default=None),
    paper_code: str | None = Form(default=None),
    syllabus_id: str | None = Form(default=None),
    school: str | None = Form(default=None),
    expected_question_count: int | None = Form(default=None),
    force: bool = Form(default=False),
) -> dict[str, Any]:
    if not question_pdf.filename or not question_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Question paper must be a PDF")
    if not answer_pdf.filename or not answer_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Answer scheme must be a PDF")
    if question_pdf.content_type and "pdf" not in question_pdf.content_type.lower():
        if question_pdf.content_type not in {"application/octet-stream", "binary/octet-stream"}:
            raise HTTPException(status_code=400, detail="Question paper MIME type must be PDF")
    if answer_pdf.content_type and "pdf" not in answer_pdf.content_type.lower():
        if answer_pdf.content_type not in {"application/octet-stream", "binary/octet-stream"}:
            raise HTTPException(status_code=400, detail="Answer scheme MIME type must be PDF")

    q_bytes = await question_pdf.read()
    a_bytes = await answer_pdf.read()
    if not q_bytes:
        raise HTTPException(status_code=400, detail="Question PDF is empty")
    if not a_bytes:
        raise HTTPException(status_code=400, detail="Answer PDF is empty")

    q_hash = _sha256_bytes(q_bytes)
    a_hash = _sha256_bytes(a_bytes)

    store = get_store()
    if not force:
        dup = check_duplicates(
            DuplicateCheckRequest(questionSha256=q_hash, answerSha256=a_hash)
        )
        if dup["status"] == "exact_duplicate":
            return {**dup, "created": False}
        if dup["status"] == "question_exists_answer_differs":
            return {**dup, "created": False}

    display_title = title
    if school:
        display_title = f"{title} · {school}"

    # Stage under durable factory storage — not a TemporaryDirectory that can
    # vanish before the background ingest thread reads the files.
    from uuid import uuid4

    from questbank.factory.local_storage import factory_storage_root

    staging_dir = factory_storage_root() / "import-staging" / str(uuid4())
    staging_dir.mkdir(parents=True, exist_ok=True)
    q_path = staging_dir / "question-paper.pdf"
    a_path = staging_dir / "answer-scheme.pdf"
    q_path.write_bytes(q_bytes)
    a_path.write_bytes(a_bytes)

    try:
        result = create_batch_and_start(
            store=store,
            title=display_title,
            subject=subject,
            question_pdf=q_path,
            answer_pdf=a_path,
            exam_year=exam_year,
            paper_code=paper_code,
            syllabus_id=syllabus_id or None,
            expected_question_count=expected_question_count,
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Keep a named copy for reprocess fallback; durable exam-sources/ is primary.
    keep = factory_storage_root() / "import-keep" / result["batchId"]
    if keep.exists():
        shutil.rmtree(keep, ignore_errors=True)
    keep.mkdir(parents=True, exist_ok=True)
    shutil.copy2(q_path, keep / "question-paper.pdf")
    shutil.copy2(a_path, keep / "answer-scheme.pdf")
    _UPLOAD_CACHE[result["batchId"]] = keep
    # Safe: the ingest job reads from exam-sources/{batchId}/, not staging.
    shutil.rmtree(staging_dir, ignore_errors=True)

    return {
        "created": True,
        "status": "started",
        "batchId": result["batchId"],
        "runId": result["runId"],
        "questionSha256": q_hash,
        "answerSha256": a_hash,
    }


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    store = get_store()
    detail = store.get_batch_detail(batch_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Batch not found")
    job = get_job(batch_id)

    q_count = detail.get("questionCount") or 0
    live_job = job is not None and job.status in {"PENDING", "RUNNING"}

    # Worker gone but drafts already exist: surface them for review.
    if (
        not live_job
        and detail.get("status") == "PROCESSING"
        and q_count > 0
        and detail.get("latestRunId")
    ):
        try:
            from datetime import datetime, timezone

            store.update_run_status(
                detail["latestRunId"],
                "COMPLETED",
                completed_at=datetime.now(timezone.utc),
            )
            store.update_batch_status(batch_id, "READY_FOR_REVIEW")
            detail = store.get_batch_detail(batch_id) or detail
        except Exception:  # noqa: BLE001
            pass

    # Orphaned PROCESSING after API restart: no live worker for a while, 0 questions.
    if (
        job is None
        and detail.get("status") == "PROCESSING"
        and detail.get("latestRunStatus") == "PROCESSING"
        and (detail.get("questionCount") or 0) == 0
        and detail.get("latestRunId")
    ):
        started = None
        for run in detail.get("runs") or []:
            if run.get("id") == detail.get("latestRunId"):
                started = run.get("started_at") or run.get("startedAt")
                break
        stale = True
        if started:
            try:
                from datetime import datetime, timezone

                if isinstance(started, str):
                    started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                else:
                    started_dt = started
                age_s = (datetime.now(timezone.utc) - started_dt).total_seconds()
                stale = age_s >= 30 * 60
            except Exception:  # noqa: BLE001
                stale = True
        if stale:
            try:
                from datetime import datetime, timezone

                store.update_run_status(
                    detail["latestRunId"],
                    "FAILED",
                    completed_at=datetime.now(timezone.utc),
                )
                store.update_batch_status(batch_id, "HAS_ERRORS")
                detail = store.get_batch_detail(batch_id) or detail
            except Exception:  # noqa: BLE001
                pass

    stages = _derive_stages(detail, job)
    return {
        **detail,
        "job": (
            {
                "status": job.status,
                "stage": job.stage,
                "message": job.message,
                "error": job.error,
                "details": job.details or {},
            }
            if job
            else None
        ),
        "stages": stages,
    }


def _derive_stages(detail: dict[str, Any], job: Any) -> dict[str, Any]:
    docs = detail.get("documents") or []
    has_q = any(d.get("document_type") == "QUESTION_PAPER" for d in docs)
    has_a = any(d.get("document_type") == "ANSWER_SCHEME" for d in docs)
    run_status = detail.get("latestRunStatus")
    batch_status = detail.get("status")
    q_count = detail.get("questionCount") or 0

    def mark(done: bool, active: bool = False) -> str:
        if done:
            return "done"
        if active:
            return "active"
        return "pending"

    parsing = False
    matching = False
    validation = False
    complete = batch_status in {"READY_FOR_REVIEW", "COMPLETED"} and q_count > 0
    live_job = job is not None and job.status in {"PENDING", "RUNNING"}
    job_failed = job is not None and job.status == "FAILED"
    hard_fail = q_count == 0 and (
        batch_status == "HAS_ERRORS"
        or run_status == "FAILED"
        or job_failed
    )
    failed = hard_fail

    if live_job:
        parsing = True
        failed = False
        complete = False
    elif job is not None and job.status == "COMPLETED":
        complete = True
        failed = False

    if run_status == "COMPLETED" and q_count > 0:
        complete = True
        failed = False
    if run_status == "PROCESSING" and not live_job and q_count == 0:
        # Still inside the 30-minute orphan window: show in-progress, not error.
        parsing = not failed

    if batch_status == "HAS_ERRORS" and q_count == 0 and not live_job:
        failed = True
        parsing = False
        complete = False
    if q_count > 0 and not live_job:
        complete = True
        failed = False
        parsing = False

    error_message = None
    if job and job.error:
        error_message = job.error
    elif failed and run_status == "PROCESSING" and job is None:
        error_message = (
            "Processing stopped (server restarted or worker lost). "
            "Click Retry / Reprocess to continue."
        )
    elif failed:
        error_message = "Processing failed."

    return {
        "questionPaper": [
            {"label": "Uploaded", "state": mark(has_q)},
            {"label": "Stored", "state": mark(has_q)},
            {
                "label": "PDF analysed",
                "state": mark(complete or (run_status == "COMPLETED"), parsing),
            },
            {
                "label": "Parsing questions",
                "state": mark(complete or q_count > 0, parsing and not complete),
            },
        ],
        "answerScheme": [
            {"label": "Uploaded", "state": mark(has_a)},
            {"label": "Stored", "state": mark(has_a)},
            {
                "label": "Answers extracted",
                "state": mark(complete or q_count > 0, parsing and not complete),
            },
            {
                "label": "Matching answers",
                "state": mark(complete, parsing and not complete),
            },
        ],
        "validation": [
            {
                "label": "Validation",
                "state": mark(complete, False) if not failed else "error",
            }
        ],
        "failed": failed,
        "complete": complete and not failed,
        "errorMessage": error_message,
    }


@router.post("/batches/{batch_id}/reprocess")
def reprocess(batch_id: str) -> dict[str, Any]:
    store = get_store()
    q = a = None
    try:
        q, a = materialize_batch_source_pdfs(store, batch_id)
    except (KeyError, ValueError, FileNotFoundError):
        q = a = None
    if q is None or a is None:
        keep = _UPLOAD_CACHE.get(batch_id)
        if keep is not None:
            q = keep / "question-paper.pdf"
            a = keep / "answer-scheme.pdf"
    if q is None or a is None or not Path(q).is_file() or not Path(a).is_file():
        raise HTTPException(
            status_code=400,
            detail=(
                "Source PDFs are not available in storage. "
                "Re-upload the paper so the live server can refer to the stored copies."
            ),
        )
    try:
        result = reprocess_batch(
            store=store,
            batch_id=batch_id,
            question_pdf=Path(q),
            answer_pdf=Path(a),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"created": True, **result}


@router.post("/batches/{batch_id}/cancel")
def cancel_batch_ingest(batch_id: str) -> dict[str, Any]:
    from questbank.factory.jobs import cancel_job
    store = get_store()
    cancelled = cancel_job(batch_id)
    if cancelled:
        detail = store.get_batch_detail(batch_id)
        if detail and detail.get("latestRunId"):
            from datetime import datetime, timezone
            try:
                store.update_run_status(
                    detail["latestRunId"],
                    "FAILED",
                    completed_at=datetime.now(timezone.utc),
                )
                store.update_batch_status(batch_id, "HAS_ERRORS")
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "message": "Job cancellation requested."}
    return {"ok": False, "message": "No active job to cancel."}


@router.delete("/batches/{batch_id}")
def delete_ingestion_batch(batch_id: str) -> dict[str, Any]:
    from questbank.factory.jobs import cancel_job
    store = get_store()
    cancel_job(batch_id)
    try:
        store.delete_batch(batch_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to delete batch: {exc}") from exc
    return {"ok": True, "message": "Batch successfully deleted."}


@router.get("/batches/{batch_id}/drafts")
def list_drafts(batch_id: str, status: str | None = None, q: str | None = None) -> dict[str, Any]:
    store = get_store()
    detail = store.get_batch_detail(batch_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Batch not found")
    drafts = store.list_drafts_for_batch(batch_id)
    items = []
    for draft in drafts:
        if status and status != "ALL":
            if status == "NEEDS_REVIEW" and draft.status not in {"NEEDS_REVIEW", "DRAFT"}:
                continue
            if status == "READY" and draft.status != "READY":
                continue
            if status == "APPROVED" and draft.status != "APPROVED":
                continue
            if status == "REJECTED" and draft.status != "REJECTED":
                continue
        preview = _draft_preview(draft.content, draft.options)
        needle = (q or "").strip().lower()
        if needle in {"", "undefined", "null"}:
            needle = ""
        if needle:
            if needle not in draft.question_ref.lower() and needle not in preview.lower():
                continue
        answer = store.get_answer(draft.run_id, draft.question_ref)
        official = None
        if answer is not None:
            official = answer.answer_value
            if official is None and getattr(answer, "parts_answer", None):
                official = "parts"
        items.append(
            {
                "id": draft.id,
                "questionRef": draft.question_ref,
                "questionType": draft.question_type,
                "status": draft.status,
                "preview": preview,
                "hasVisual": _has_visual(draft.content, draft.options),
                "officialAnswer": official,
                "sourcePage": draft.source_page,
                "classification": (
                    {
                        "topicCode": draft.topic_code,
                        "title": draft.topic_title,
                        "subtopicCode": draft.subtopic_code,
                        "subtopicTitle": draft.subtopic_title,
                    }
                    if draft.topic_node_id
                    else None
                ),
                "bloomLevel": draft.bloom_level,
            }
        )
    items.sort(key=lambda d: _ref_key(d["questionRef"]))
    approved = sum(1 for d in drafts if d.status == "APPROVED")
    return {
        "batch": detail,
        "total": len(drafts),
        "approved": approved,
        "suggestedQuestionRef": suggest_question_ref(store, batch_id=batch_id),
        "drafts": items,
    }


@router.post("/batches/{batch_id}/drafts")
def create_draft(batch_id: str, body: CreateManualDraftRequest) -> dict[str, Any]:
    """Manually add a draft (MCQ or STRUCTURED) when parse missed a question."""
    store = get_store()
    if not store.get_batch(batch_id):
        raise HTTPException(status_code=404, detail="Batch not found")
    try:
        result = create_manual_draft(
            store,
            batch_id=batch_id,
            question_ref=body.questionRef,
            stem=body.stem,
            options=body.options,
            answer_value=body.answerValue,
            explanation=body.explanation,
            question_type=body.questionType,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


def _ref_key(ref: str) -> tuple:
    text = str(ref).strip()
    if text.upper().startswith("Q"):
        text = text[1:]
    if text.isdigit():
        return (0, int(text))
    return (1, text)


@router.get("/batches/{batch_id}/drafts/{draft_id}")
def get_draft_detail(batch_id: str, draft_id: str) -> dict[str, Any]:
    store = get_store()
    detail = store.get_batch_detail(batch_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Batch not found")
    draft = store.get_draft(draft_id)
    if not draft or draft.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="Draft not found")

    drafts = sorted(store.list_drafts_for_batch(batch_id), key=lambda d: _ref_key(d.question_ref))
    index = next((i for i, d in enumerate(drafts) if d.id == draft_id), 0)
    prev_id = drafts[index - 1].id if index > 0 else None
    next_id = drafts[index + 1].id if index < len(drafts) - 1 else None

    q_doc = next(
        (d for d in (detail.get("documents") or []) if d.get("document_type") == "QUESTION_PAPER"),
        None,
    )
    return {
        "batch": detail,
        "draft": _serialize_draft(store, draft),
        "position": {"index": index + 1, "total": len(drafts), "prevId": prev_id, "nextId": next_id},
        "sourceDocument": (
            {
                "id": q_doc["id"],
                "url": f"/api/factory/documents/{q_doc['id']}/file",
                "originalName": q_doc.get("original_name"),
                "pageCount": q_doc.get("page_count"),
            }
            if q_doc
            else None
        ),
    }


@router.patch("/batches/{batch_id}/drafts/{draft_id}")
def patch_draft(batch_id: str, draft_id: str, body: EditDraftRequest) -> dict[str, Any]:
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft or draft.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        result = edit_draft(
            store,
            draft_id=draft_id,
            content=body.content,
            options=body.options,
            comment=body.comment,
            answer_value=body.answerValue,
            explanation=body.explanation,
            parts_answer=body.partsAnswer,
            question_ref=body.questionRef,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/batches/{batch_id}/drafts/{draft_id}/assets/{asset_id}/replace")
async def replace_asset(
    batch_id: str,
    draft_id: str,
    asset_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft or draft.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    data = await file.read()
    try:
        return replace_draft_asset(
            store,
            draft_id=draft_id,
            asset_id=asset_id,
            data=data,
            content_type=file.content_type or "image/png",
            filename=file.filename,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batches/{batch_id}/drafts/{draft_id}/reject")
def reject(batch_id: str, draft_id: str, body: RejectDraftRequest) -> dict[str, Any]:
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft or draft.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    return reject_draft(store, draft_id=draft_id, reason=body.reason)


@router.post("/batches/{batch_id}/drafts/{draft_id}/approve")
def approve(batch_id: str, draft_id: str) -> dict[str, Any]:
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft or draft.batch_id != batch_id:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        result = approve_draft(store, draft_id=draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    nxt = next_unresolved_draft_id(store, batch_id=batch_id, after_draft_id=draft_id)
    return {**result, "nextDraftId": nxt}


@router.post("/batches/{batch_id}/approve-all")
def approve_all(batch_id: str, include_flagged: bool = Query(False)) -> dict[str, Any]:
    store = get_store()
    batch = store.get_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return approve_all_in_batch(store, batch_id=batch_id, include_flagged=include_flagged)


@router.get("/documents/{document_id}/file")
def document_file(document_id: str) -> Response:
    store = get_store()
    # Resolve via batch documents scan
    # Postgres: look up by querying all batches is heavy; use SQL if available
    doc = None
    if hasattr(store, "documents"):
        doc = store.documents.get(document_id)  # type: ignore[attr-defined]
    if doc is None and isinstance(store, PostgresFactoryStore):
        with store._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, storage_path, mime_type, original_name
                FROM "source_documents" WHERE id = %s::uuid
                """,
                (document_id,),
            )
            row = cur.fetchone()
            if row:
                doc = {
                    "id": row[0],
                    "storage_path": row[1],
                    "mime_type": row[2],
                    "original_name": row[3],
                }
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    resolved = resolve_storage_bytes(doc["storage_path"])
    if not resolved:
        raise HTTPException(status_code=404, detail="Original source unavailable")
    data, mime = resolved
    return Response(
        content=data,
        media_type=mime or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{doc.get("original_name") or "source.pdf"}"'
        },
    )


@router.get("/assets/{asset_id}")
def asset_file(asset_id: str, v: str | None = Query(None)) -> Response:
    store = get_store()
    asset = store.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    resolved = resolve_storage_bytes(asset.storage_path)
    if not resolved:
        raise HTTPException(status_code=404, detail="Asset file unavailable")
    data, mime = resolved
    # A versioned URL (?v=<sha256 prefix>) points at immutable content, so it's
    # safe to cache aggressively; an un-versioned/legacy URL might have been
    # replaced since a client last fetched it, so force revalidation instead.
    cache_control = "public, max-age=31536000, immutable" if v else "no-cache"
    return Response(
        content=data,
        media_type=mime or asset.mime_type,
        headers={"Cache-Control": cache_control},
    )


@router.get("/questions")
def list_questions() -> dict[str, Any]:
    store = get_store()
    questions = store.list_bank_questions(limit=1000)
    for q in questions:
        current = q.get("currentVersion")
        if current:
            current["content"] = ensure_list_blocks(current.get("content"))
    pairs = [
        (q["currentVersion"].get("content"), q["currentVersion"].get("options"))
        for q in questions
        if q.get("currentVersion")
    ]
    asset_urls_list = _asset_urls_for_payloads_batch(store, pairs)
    i = 0
    for q in questions:
        current = q.get("currentVersion")
        if current:
            current["assetUrls"] = asset_urls_list[i]
            i += 1
    return {"questions": questions}


@router.get("/export/questions")
def export_questions_for_production(
    api_key: str | None = Query(None, alias="api_key"),
) -> dict[str, Any]:
    """Secure export API for the Real App to pull all approved and active questions with their topics, subtopics, answers, explanations, and Bloom's Taxonomy classifications."""
    # Simple API Key check for security
    expected_key = os.environ.get("REAL_APP_PUBLISH_KEY") or os.environ.get("INTERNAL_PUBLISH_KEY") or "edunets-secret-sync"
    incoming_key = api_key or ""
    if incoming_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid API Key. Access denied.")

    store = get_store()
    questions = store.list_bank_questions(limit=1000)
    
    exported = []
    for q in questions:
        q_id = q["id"]
        # Fetch complete question detail (which includes all historical and active versions)
        full_q = store.get_bank_question(q_id)
        if not full_q or not full_q.get("versions"):
            continue
            
        # Get active/latest approved version
        active_version = next((v for v in full_q["versions"] if v["status"] == "APPROVED"), None)
        if not active_version:
            # If no approved version exists, skip
            continue
            
        v_content = ensure_list_blocks(active_version.get("content"))
        v_options = active_version.get("options")
        
        # Populate assets
        asset_urls = _asset_urls_for_payload(store, v_content, v_options)
        
        # Fetch syllabus node classifications
        # In Postgres, query classifications for this version
        topic_info = None
        if hasattr(store, "_conn"):
            with store._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT n.id::text, n.code, n.title, n.parent_id::text, n.node_type::text
                    FROM "question_classifications" c
                    JOIN "syllabus_nodes" n ON n.id = c.syllabus_node_id
                    WHERE c.question_version_id = %s::uuid AND c.status = 'CLASSIFIED'
                    LIMIT 1
                    """,
                    (active_version["id"],),
                )
                node_row = cur.fetchone()
                
                if node_row:
                    node_id, node_code, node_title, parent_id, node_type = node_row
                    topic_id, topic_code, topic_title = None, None, None
                    subtopic_id, subtopic_code, subtopic_title = None, None, None
                    
                    if node_type == "SUBTOPIC":
                        subtopic_id, subtopic_code, subtopic_title = node_id, node_code, node_title
                        # Traverse up to find parent topic
                        if parent_id:
                            cur.execute(
                                'SELECT id::text, code, title FROM "syllabus_nodes" WHERE id = %s::uuid',
                                (parent_id,),
                            )
                            p_row = cur.fetchone()
                            if p_row:
                                topic_id, topic_code, topic_title = p_row
                    elif node_type == "TOPIC":
                        topic_id, topic_code, topic_title = node_id, node_code, node_title
                    
                    topic_info = {
                        "classified_node_id": node_id,
                        "classified_node_type": node_type,
                        "topic": {
                            "id": topic_id,
                            "code": topic_code,
                            "title": topic_title,
                        } if topic_id else None,
                        "subtopic": {
                            "id": subtopic_id,
                            "code": subtopic_code,
                            "title": subtopic_title,
                        } if subtopic_id else None,
                    }
                
        # Reconstruct full clean answer details
        answer_data = active_version.get("answer") or {}
        
        exported.append({
            "id": q["id"],
            "subject": q["subject"],
            "source_key": q["sourceKey"],
            "version_id": active_version["id"],
            "version_number": active_version["version"],
            "content": v_content,
            "options": v_options,
            "asset_urls": asset_urls,
            "answer_value": answer_data.get("value"),
            "explanation": answer_data.get("explanation"),
            "classification": topic_info,
            "bloom_taxonomy": {
                "level": active_version.get("bloomLevel"),
                "reasoning": active_version.get("bloomReasoning"),
            } if active_version.get("bloomLevel") else None,
            "approved_at": active_version.get("approvedAt"),
        })
        
    return {
        "success": True,
        "count": len(exported),
        "questions": exported,
    }


@router.get("/questions/{question_id}")
def get_question(question_id: str) -> dict[str, Any]:
    store = get_store()
    q = store.get_bank_question(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    for version in q.get("versions") or []:
        version["content"] = ensure_list_blocks(version.get("content"))
        version["assetUrls"] = _asset_urls_for_payload(
            store, version.get("content"), version.get("options")
        )
    
    # Fetch all questions in the bank to determine next and previous IDs
    all_questions = store.list_bank_questions(limit=1000)
    q_ids = [item["id"] for item in all_questions]
    
    prev_id = None
    next_id = None
    try:
        idx = q_ids.index(question_id)
        if idx > 0:
            prev_id = q_ids[idx - 1]
        if idx < len(q_ids) - 1:
            next_id = q_ids[idx + 1]
    except ValueError:
        pass

    return {**q, "prevId": prev_id, "nextId": next_id}


class CreateQuestionVersionRequest(BaseModel):
    content: Any
    options: Any
    value: str
    explanation: str | None = None


@router.post("/questions/{question_id}/versions")
def create_question_correction_version(question_id: str, body: CreateQuestionVersionRequest) -> dict[str, Any]:
    store = get_store()
    q = store.get_bank_question(question_id)
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
        
    try:
        from datetime import datetime, timezone
        
        # Retire active versions first (to make sure there's only one active approved version)
        store.retire_active_versions(question_id)
        
        # Get next version number
        version_num = store.next_version_number(question_id)
        now = datetime.now(timezone.utc)
        
        # Create new question version
        version = store.create_question_version(
            {
                "question_id": question_id,
                "version": version_num,
                "content": ensure_list_blocks(body.content),
                "options": body.options,
                "status": "APPROVED",
                "approved_by": None,
                "approved_at": now,
                "published_at": None,
            }
        )
        
        # Create question answer
        store.create_question_answer(
            {
                "question_version_id": version["id"],
                "value": body.value,
                "explanation": body.explanation,
                "explanation_source": "MARK_SCHEME" if body.explanation else None,
            }
        )
        
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to create correction version: {exc}") from exc
        
    return {"ok": True, "version": version_num, "versionId": version["id"]}


@router.post("/assets/upload")
async def upload_asset_endpoint(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    from uuid import uuid4
    from io import BytesIO
    from PIL import Image
    from questbank.factory.store import AssetRow
    from questbank.factory.local_storage import create_asset_uploader

    store = get_store()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file uploaded")
    
    content_type = file.content_type or "image/png"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are supported")

    width = height = None
    try:
        with Image.open(BytesIO(data)) as img:
            width, height = img.size
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image format: {exc}")

    digest = hashlib.sha256(data).hexdigest()
    ext = "png"
    if "jpeg" in content_type or "jpg" in content_type:
        ext = "jpg"
    elif "webp" in content_type:
        ext = "webp"
        
    asset_id = str(uuid4())
    safe_name = f"correction-{uuid4().hex[:8]}.{ext}"
    storage_path = f"question-assets/corrections/{safe_name}"

    uploader = create_asset_uploader()
    uploader.upload(storage_path=storage_path, data=data, content_type=content_type)

    row = AssetRow(
        id=asset_id,
        draft_id=None,
        source_document_id=None,
        role="STEM_IMAGE",
        storage_path=storage_path,
        mime_type=content_type,
        source_page=None,
        bbox=None,
        width=width,
        height=height,
        sha256=digest,
    )
    store.create_asset(row)

    return {
        "success": True,
        "assetId": asset_id,
        "url": _versioned_asset_url(asset_id, digest),
    }


class RunClassificationRequest(BaseModel):
    versionId: str


class BatchClassificationRequest(BaseModel):
    versionIds: list[str]
    # Default True: only classify PENDING versions; keep existing tags.
    skipClassified: bool = True


class ManualClassificationRequest(BaseModel):
    versionId: str
    topicCode: str
    subtopicCode: str | None = None
    bloomLevel: str | None = None
    reasoning: str | None = None


def _version_already_classified(store: FactoryStore, version_id: str) -> bool:
    """True when a primary CLASSIFIED row already exists for this version."""
    if hasattr(store, "_conn"):
        with store._conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                """
                SELECT 1
                FROM "question_classifications"
                WHERE question_version_id = %s::uuid
                  AND is_primary = true
                  AND status = 'CLASSIFIED'::classification_status
                LIMIT 1
                """,
                (version_id,),
            )
            return cur.fetchone() is not None
    # In-memory / test store: treat presence in classifications as classified
    classifications = getattr(store, "classifications", {}) or {}
    for row in classifications.values():
        if (
            row.get("question_version_id") == version_id
            and row.get("is_primary", True)
            and row.get("status") == "CLASSIFIED"
        ):
            return True
    return False


def _build_numbered_taxonomy(store: FactoryStore) -> dict[str, Any]:
    """Compact numbered topic/subtopic options for manual tagging UI."""
    import re

    taxonomies: dict[str, Any] = {}
    for syl in store.list_syllabi():
        sid = syl["id"]
        nodes = store.list_syllabus_nodes(sid)
        topics = []
        subtopics = []
        for n in nodes:
            code = str(n.get("code") or "").strip()
            ntype = n.get("node_type")
            if ntype == "TOPIC" and re.fullmatch(r"\d+", code):
                topics.append(
                    {
                        "id": n["id"],
                        "code": code,
                        "title": n.get("title") or "",
                    }
                )
            elif ntype == "SUBTOPIC" and re.fullmatch(r"\d+\.\d+", code):
                parent_id = n.get("parent_id")
                parent_code = code.split(".", 1)[0]
                subtopics.append(
                    {
                        "id": n["id"],
                        "code": code,
                        "title": n.get("title") or "",
                        "parentId": parent_id,
                        "parentCode": parent_code,
                    }
                )
        topics.sort(key=lambda t: [int(x) for x in re.findall(r"\d+", t["code"])])
        subtopics.sort(key=lambda t: [int(x) for x in re.findall(r"\d+", t["code"])])
        taxonomies[sid] = {
            "syllabusId": sid,
            "subject": syl.get("subject"),
            "code": syl.get("code"),
            "title": syl.get("title"),
            "topics": topics,
            "subtopics": subtopics,
        }
    return taxonomies


@router.post("/classification/manual")
def manual_classification(body: ManualClassificationRequest) -> dict[str, Any]:
    """Manually assign numbered topic/subtopic (and optional Bloom level)."""
    import re

    store = get_store()
    version_id = body.versionId
    topic_code = str(body.topicCode or "").strip()
    subtopic_code = (
        str(body.subtopicCode).strip()
        if body.subtopicCode is not None and str(body.subtopicCode).strip()
        else None
    )

    if not re.fullmatch(r"\d+", topic_code):
        raise HTTPException(status_code=400, detail="topicCode must be a numbered topic like 1, 2, 10")
    if subtopic_code and not re.fullmatch(r"\d+\.\d+", subtopic_code):
        raise HTTPException(status_code=400, detail="subtopicCode must be numbered like 1.1 or 11.2")
    if subtopic_code and not subtopic_code.startswith(f"{topic_code}."):
        raise HTTPException(
            status_code=400,
            detail=f"Subtopic {subtopic_code} does not belong under Topic {topic_code}",
        )

    allowed_bloom = {
        "Remembering",
        "Understanding",
        "Applying",
        "Analyzing",
        "Evaluating",
        "Creating",
    }
    bloom_level = body.bloomLevel
    if bloom_level:
        matched = next((b for b in allowed_bloom if b.lower() == bloom_level.lower()), None)
        if not matched:
            raise HTTPException(status_code=400, detail=f"Invalid bloomLevel: {bloom_level}")
        bloom_level = matched

    # Resolve syllabus + source key for this version
    source_key = None
    subject = None
    syllabus_id = None
    if hasattr(store, "_conn"):
        store.ensure_ready()  # type: ignore[union-attr]
        with store._conn.cursor() as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                SELECT q.source_key, q.subject, b.syllabus_id::text
                FROM "question_versions" v
                JOIN "bank_questions" q ON q.id = v.question_id
                LEFT JOIN "question_drafts" d ON d.id = v.source_draft_id
                LEFT JOIN "ingestion_batches" b ON b.id = d.batch_id
                WHERE v.id = %s::uuid
                """,
                (version_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Question version not found")
            source_key, subject, syllabus_id = row

    if not syllabus_id:
        # Fallback: first syllabus matching subject
        for syl in store.list_syllabi():
            if subject and syl.get("subject") == subject:
                syllabus_id = syl["id"]
                break
        if not syllabus_id and store.list_syllabi():
            syllabus_id = store.list_syllabi()[0]["id"]

    if not syllabus_id:
        raise HTTPException(status_code=400, detail="No syllabus available for manual tagging")

    nodes = store.list_syllabus_nodes(syllabus_id)
    topics = {
        str(n.get("code") or "").strip(): n
        for n in nodes
        if n.get("node_type") == "TOPIC" and re.fullmatch(r"\d+", str(n.get("code") or "").strip())
    }
    subtopics = {
        str(n.get("code") or "").strip(): n
        for n in nodes
        if n.get("node_type") == "SUBTOPIC" and re.fullmatch(r"\d+\.\d+", str(n.get("code") or "").strip())
    }

    topic = topics.get(topic_code)
    if not topic:
        raise HTTPException(status_code=400, detail=f"Topic code {topic_code} not found in syllabus")

    children = [
        s for s in subtopics.values()
        if s.get("parent_id") == topic["id"] or str(s.get("code") or "").startswith(f"{topic_code}.")
    ]

    final_node = topic
    final_sub = None
    if children:
        if not subtopic_code:
            raise HTTPException(
                status_code=400,
                detail=f"Topic {topic_code} requires a numbered subtopic "
                f"({', '.join(sorted(str(c.get('code')) for c in children))})",
            )
        final_sub = subtopics.get(subtopic_code)
        if not final_sub or (
            final_sub.get("parent_id")
            and final_sub.get("parent_id") != topic["id"]
            and not str(final_sub.get("code") or "").startswith(f"{topic_code}.")
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Subtopic {subtopic_code} is not under Topic {topic_code}",
            )
        final_node = final_sub
    elif subtopic_code:
        raise HTTPException(
            status_code=400,
            detail=f"Topic {topic_code} has no numbered subtopics; leave subtopic empty",
        )

    method = "Manual Tag"
    reasoning = (body.reasoning or "").strip() or f"Manually tagged as Topic {topic_code}" + (
        f" / Subtopic {subtopic_code}" if subtopic_code else " (topic only)"
    )

    store.create_question_classification(
        {
            "question_version_id": version_id,
            "syllabus_node_id": final_node["id"],
            "is_primary": True,
            "status": "CLASSIFIED",
            "classified_by": method,
        }
    )

    if bloom_level and hasattr(store, "_conn"):
        store.ensure_ready()  # type: ignore[union-attr]
        with store._conn.cursor() as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                UPDATE "question_versions"
                SET "bloom_level" = %s, "bloom_reasoning" = %s
                WHERE "id" = %s::uuid
                """,
                (bloom_level, reasoning, version_id),
            )
        store._conn.commit()  # type: ignore[union-attr]

    if hasattr(store, "append_classification_history"):
        store.append_classification_history(
            {
                "question_version_id": version_id,
                "source_key": source_key,
                "syllabus_node_id": final_node["id"],
                "topic_code": topic_code,
                "topic_title": topic.get("title"),
                "subtopic_code": subtopic_code if final_sub else None,
                "subtopic_title": final_sub.get("title") if final_sub else None,
                "bloom_level": bloom_level,
                "classified_by": method,
                "reasoning": reasoning,
                "method": method,
            }
        )

    return {
        "success": True,
        "nodeId": final_node["id"],
        "title": final_node.get("title"),
        "code": final_node.get("code"),
        "topicCode": topic_code,
        "subtopicCode": subtopic_code if final_sub else None,
        "bloomLevel": bloom_level,
        "reasoning": reasoning,
        "method": method,
    }


@router.post("/classification/batch")
def run_batch_ai_classification(body: BatchClassificationRequest) -> dict[str, Any]:
    from questbank.llm.kimi_classifier import classify_with_kimi, classify_bloom_with_kimi
    import concurrent.futures

    store = get_store()
    version_ids = list(body.versionIds or [])
    skipped: list[str] = []
    if body.skipClassified:
        to_run: list[str] = []
        for v_id in version_ids:
            if _version_already_classified(store, v_id):
                skipped.append(v_id)
            else:
                to_run.append(v_id)
        version_ids = to_run

    results = []
    success_count = 0

    def process_one(v_id: str) -> dict[str, Any]:
        thread_store = get_thread_safe_store()
        try:
            # Topic classification
            res = classify_with_kimi(thread_store, v_id)
            # Bloom's Taxonomy classification
            bloom_res = classify_bloom_with_kimi(thread_store, v_id)
            res["bloomLevel"] = bloom_res.get("bloomLevel")
            res["bloomReasoning"] = bloom_res.get("reasoning")
            return {"versionId": v_id, **res}
        except Exception as exc:
            return {"versionId": v_id, "success": False, "message": str(exc)}
        finally:
            if hasattr(thread_store, "close"):
                thread_store.close()

    if not version_ids:
        return {
            "success": True,
            "classifiedCount": 0,
            "skippedCount": len(skipped),
            "skippedVersionIds": skipped,
            "results": [
                {
                    "versionId": v_id,
                    "success": True,
                    "skipped": True,
                    "message": "Already classified — retained",
                }
                for v_id in skipped
            ],
            "message": "All selected questions were already classified; nothing re-run.",
        }

    # Process versions concurrently with an isolated connection per thread
    # max_workers=5 is safe and fast
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_one, v_id): v_id for v_id in version_ids}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            results.append(res)
            if res.get("success"):
                success_count += 1

    return {
        "success": True,
        "classifiedCount": success_count,
        "skippedCount": len(skipped),
        "skippedVersionIds": skipped,
        "results": results,
    }


@router.post("/classification/run")
def run_ai_classification(body: RunClassificationRequest) -> dict[str, Any]:
    from questbank.llm.kimi_classifier import classify_with_kimi, classify_bloom_with_kimi
    import concurrent.futures

    def run_topic() -> dict[str, Any]:
        thread_store = get_thread_safe_store()
        try:
            return classify_with_kimi(thread_store, body.versionId)
        finally:
            if hasattr(thread_store, "close"):
                thread_store.close()

    def run_bloom() -> dict[str, Any]:
        thread_store = get_thread_safe_store()
        try:
            return classify_bloom_with_kimi(thread_store, body.versionId)
        finally:
            if hasattr(thread_store, "close"):
                thread_store.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_topic = executor.submit(run_topic)
            future_bloom = executor.submit(run_bloom)
            
            res = future_topic.result()
            bloom_res = future_bloom.result()
            
        res["bloomLevel"] = bloom_res.get("bloomLevel")
        res["bloomReasoning"] = bloom_res.get("reasoning")
        return res
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/classification")
def classification_queue() -> dict[str, Any]:
    store = get_store()
    serialized = []
    
    if hasattr(store, "_conn"):
        with store._conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    v.id::text, 
                    v.question_id::text, 
                    q.source_key, 
                    q.subject, 
                    v.version,
                    v.content, 
                    v.options, 
                    v.status::text,
                    v.bloom_level, 
                    v.bloom_reasoning,
                    d.batch_id::text, 
                    b.title,
                    b.syllabus_id::text,
                    sn.id::text, 
                    sn.code, 
                    sn.title, 
                    sn.parent_id::text, 
                    sn.node_type::text
                FROM "question_versions" v
                JOIN "bank_questions" q ON q.id = v.question_id
                LEFT JOIN "question_classifications" c ON c.question_version_id = v.id AND c.status = 'CLASSIFIED'
                LEFT JOIN "syllabus_nodes" sn ON sn.id = c.syllabus_node_id
                LEFT JOIN "question_drafts" d ON d.id = v.source_draft_id
                LEFT JOIN "ingestion_batches" b ON b.id = d.batch_id
                WHERE v.status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                ORDER BY b.created_at DESC, q.source_key ASC, v.version DESC
            """)
            rows = cur.fetchall()
            
            # Cache syllabus node lookups to keep it fast
            node_cache = {}
            
            for r in rows:
                (
                    v_id, q_id, source_key, subject, version, content, options, status,
                    bloom_level, bloom_reasoning, batch_id, batch_title, syllabus_id,
                    sn_id, sn_code, sn_title, sn_parent_id, sn_node_type,
                ) = r
                
                # Fetch asset URLs
                content = ensure_list_blocks(content)
                asset_urls = _asset_urls_for_payload(store, content, options)
                
                topic_info = None
                if sn_id:
                    topic_id, topic_code, topic_title = None, None, None
                    subtopic_id, subtopic_code, subtopic_title = None, None, None
                    
                    if sn_node_type == "SUBTOPIC":
                        subtopic_id, subtopic_code, subtopic_title = sn_id, sn_code, sn_title
                        if sn_parent_id:
                            if sn_parent_id not in node_cache:
                                cur.execute(
                                    'SELECT id::text, code, title FROM "syllabus_nodes" WHERE id = %s::uuid',
                                    (sn_parent_id,),
                                )
                                p_row = cur.fetchone()
                                node_cache[sn_parent_id] = p_row if p_row else None
                            
                            p_row = node_cache[sn_parent_id]
                            if p_row:
                                topic_id, topic_code, topic_title = p_row
                    elif sn_node_type == "TOPIC":
                        topic_id, topic_code, topic_title = sn_id, sn_code, sn_title
                        
                    topic_info = {
                        "classified_node_id": sn_id,
                        "classified_node_type": sn_node_type,
                        "topic": {
                            "id": topic_id,
                            "code": topic_code,
                            "title": topic_title,
                        } if topic_id else None,
                        "subtopic": {
                            "id": subtopic_id,
                            "code": subtopic_code,
                            "title": subtopic_title,
                        } if subtopic_id else None,
                    }
                
                serialized.append({
                    "versionId": v_id,
                    "questionId": q_id,
                    "sourceKey": source_key,
                    "subject": subject,
                    "version": version,
                    "content": content,
                    "options": options,
                    "status": status,
                    "bloomLevel": bloom_level,
                    "bloomReasoning": bloom_reasoning,
                    "batchId": batch_id or "legacy_or_manual",
                    "batchTitle": batch_title or "Unlinked Questions (Legacy/Manual)",
                    "syllabusId": syllabus_id,
                    "classification": topic_info,
                    "classificationStatus": "CLASSIFIED" if sn_id else "PENDING",
                    "assetUrls": asset_urls,
                })
    else:
        # Fallback for In-Memory stores in test environment
        items = store.list_unclassified_versions(limit=200)
        for item in items:
            item = {**item, "content": ensure_list_blocks(item.get("content"))}
            asset_urls = _asset_urls_for_payload(store, item.get("content"), item.get("options"))
            serialized.append({
                **item,
                "batchId": "in_memory_batch",
                "batchTitle": "In-Memory Test Batch",
                "assetUrls": asset_urls
            })
            
    return {
        "items": serialized,
        "taxonomy": _build_numbered_taxonomy(store),
        "history": (
            store.list_classification_history(limit=200)
            if hasattr(store, "list_classification_history")
            else []
        ),
        "configured": True,
        "message": "AI Classification queue loaded.",
    }


@router.get("/classification/history")
def classification_history(limit: int = Query(80, ge=1, le=200)) -> dict[str, Any]:
    store = get_store()
    if not hasattr(store, "list_classification_history"):
        return {"items": []}
    return {"items": store.list_classification_history(limit=limit)}

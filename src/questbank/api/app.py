"""FastAPI app: MCQ ingest + Question Factory admin API."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from questbank.api.factory_routes import router as factory_router
from questbank.api.simulated_test_routes import router as simulated_test_router
from questbank.config.env import load_questbank_env
from questbank.ingest.runner import run_mcq_ingest
from questbank.types.ingest import PaperIdentity

load_questbank_env()

app = FastAPI(
    title="EduNets Question Factory",
    description="Ingest examination papers into verified EduNets questions.",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(factory_router)
app.include_router(simulated_test_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest/mcq")
async def ingest_mcq(
    question_pdf: UploadFile = File(..., description="Question paper PDF"),
    answer_pdf: UploadFile = File(..., description="Answer key / mark scheme PDF"),
    title: str | None = Form(default=None),
    subject: str = Form(default="Chemistry"),
    year: int | None = Form(default=None),
    paper_code: str = Form(default="6092/01"),
    expected_question_count: int | None = Form(default=None),
    push_supabase: bool = Form(
        default=False,
        description="Legacy: push to question_banks/questions (not Question Factory)",
    ),
    persist_factory: bool = Form(
        default=False,
        description="Persist canonical output into Prisma Question Factory drafts",
    ),
    output_root: str = Form(default="output"),
    run_id: str | None = Form(default=None),
) -> dict[str, Any]:
    """Accept question + answer PDFs; do not require a syllabus PDF.

    Intended Question Factory flow when ``persist_factory`` is true:

    create IngestionBatch → upload PDFs → SourceDocuments → IngestionRun
    → parse → QuestionFactoryIngestService → return batch/run IDs
    """
    if not question_pdf.filename or not question_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="question_pdf must be a PDF")
    if not answer_pdf.filename or not answer_pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="answer_pdf must be a PDF")

    identity = PaperIdentity(
        source_exam_year=year or 0,
        paper_code=paper_code,
        paper_type="mcq",
    )

    factory_store = None
    factory_uploader = None
    if persist_factory:
        import os

        from questbank.factory.local_storage import create_asset_uploader
        from questbank.factory.store import InMemoryFactoryStore, PostgresFactoryStore

        dsn = (os.environ.get("DATABASE_URL") or "").strip()
        if dsn:
            try:
                factory_store = PostgresFactoryStore(dsn=dsn)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to connect DATABASE_URL for Question Factory: {exc}",
                ) from exc
        else:
            factory_store = InMemoryFactoryStore()

        factory_uploader = create_asset_uploader()

    with tempfile.TemporaryDirectory(prefix="questbank-ingest-") as tmp:
        tmp_path = Path(tmp)
        q_path = tmp_path / "question.pdf"
        a_path = tmp_path / "answer.pdf"
        q_path.write_bytes(await question_pdf.read())
        a_path.write_bytes(await answer_pdf.read())

        try:
            manifest = run_mcq_ingest(
                question_pdf=q_path,
                answer_pdf=a_path,
                output_root=output_root,
                run_id=run_id,
                paper_identity=identity,
                expected_question_count=expected_question_count,
                title=title or question_pdf.filename,
                subject=subject,
                push_supabase=push_supabase,
                persist_factory=persist_factory,
                factory_store=factory_store,
                factory_uploader=factory_uploader,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            if factory_store is not None and hasattr(factory_store, "close"):
                try:
                    factory_store.close()
                except Exception:  # noqa: BLE001
                    pass

    out_dir = Path(output_root) / manifest.run_id
    failed = [s for s in manifest.stages if s.status == "failed"]
    factory_stage = next((s for s in manifest.stages if s.name == "factory_persist"), None)
    scaffold_stage = next((s for s in manifest.stages if s.name == "factory_scaffold"), None)
    return {
        "run_id": manifest.run_id,
        "ok": len(failed) == 0,
        "output_dir": str(out_dir).replace("\\", "/"),
        "stages": [s.model_dump(by_alias=True) for s in manifest.stages],
        "expected_question_count": expected_question_count,
        "batch_id": (scaffold_stage.details or {}).get("batchId") if scaffold_stage else None,
        "factory_run_id": (scaffold_stage.details or {}).get("runId") if scaffold_stage else None,
        "factory_report": factory_stage.details if factory_stage else None,
        "artifacts": {
            "questions": str(out_dir / "questions.json").replace("\\", "/"),
            "answers": str(out_dir / "answers.json").replace("\\", "/"),
            "matched_questions": str(out_dir / "matched_questions.json").replace("\\", "/"),
            "validation_report": str(out_dir / "validation_report.json").replace("\\", "/"),
            "assets": str(out_dir / "assets").replace("\\", "/"),
            "factory_ingest_report": str(out_dir / "factory_ingest_report.json").replace(
                "\\", "/"
            ),
        },
    }


def serve() -> None:
    """Console entry: ``questbank-serve``."""
    import uvicorn
    import os

    load_questbank_env()
    # Never enable --reload here: writing parser output (or touching src/)
    # restarts uvicorn and kills the background ingest thread, leaving the
    # UI stuck on PROCESSING / HAS_ERRORS with no live job.
    port = int(os.environ.get("PORT") or 8001)
    uvicorn.run(
        "questbank.api.app:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


__all__ = ["app", "serve"]

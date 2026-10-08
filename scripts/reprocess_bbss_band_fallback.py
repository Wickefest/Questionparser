"""Re-ingest the Bukit Batok paper with per-question band fallback into the factory batch."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from questbank.config.env import load_questbank_env
from questbank.factory.jobs import reprocess_batch
from questbank.factory.local_storage import LocalDiskAssetUploader
from questbank.factory.store import PostgresFactoryStore

BATCH_ID = "1f64807e-5dc6-4805-8ca5-e3dd564e31d0"


def main() -> int:
    load_questbank_env()
    os.environ.setdefault("GEMINI_MAX_OUTPUT_TOKENS", "16384")
    os.environ.setdefault("GEMINI_TIMEOUT_MS", "180000")

    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL not set")
        return 1

    uploader = LocalDiskAssetUploader()
    q = uploader.resolve_path(f"exam-sources/{BATCH_ID}/question-paper.pdf")
    a = uploader.resolve_path(f"exam-sources/{BATCH_ID}/answer-scheme.pdf")
    if not q or not a:
        print("Source PDFs missing in factory storage")
        return 1

    store = PostgresFactoryStore(dsn=dsn)
    try:
        result = reprocess_batch(
            store=store,
            batch_id=BATCH_ID,
            question_pdf=q,
            answer_pdf=a,
            expected_question_count=40,
            output_root=Path("output"),
        )
        print("started", result)
        print("Background ingest running. Watch the UI import page for live progress.")
        print(f"batch={BATCH_ID}")
        # Keep process alive until job finishes — reprocess starts a daemon thread
        # that dies if this process exits.
        from questbank.factory.jobs import get_job
        import time

        for _ in range(180):  # up to ~90 min (30s polls)
            time.sleep(30)
            job = get_job(BATCH_ID)
            if job is None:
                print("job missing")
                continue
            print(f"status={job.status} stage={job.stage} msg={job.message}", flush=True)
            if job.status in {"COMPLETED", "FAILED"}:
                print("done", job.status, job.error or job.message)
                return 0 if job.status == "COMPLETED" else 1
        print("timed out waiting for job")
        return 1
    finally:
        # Don't close store while job thread may still use a copy — jobs open their own PG conn
        pass


if __name__ == "__main__":
    raise SystemExit(main())

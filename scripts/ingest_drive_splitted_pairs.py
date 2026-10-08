"""Ingest Drive-paired P{n}/MS{n} PDFs into Question Factory as batches.

create_batch_and_start() starts parsing on a daemon thread and returns
immediately — that's correct for a long-running server (the thread finishes
in the background while the process stays alive), but fatal for a one-shot
script: when this script's process exits, Python kills the daemon thread
mid-parse, no matter how far it got. This script polls job status in-process
until each batch reaches a terminal state before moving to the next pair (or
exiting), so a batch is never silently abandoned.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from questbank.api.factory_routes import get_store
from questbank.factory.jobs import create_batch_and_start, get_job

MANIFEST = ROOT / "output" / "drive-splitted-topical" / "pairs_manifest.json"
JOB_TIMEOUT_S = 900  # a structured paper can take several minutes across pages
POLL_INTERVAL_S = 5


def clean_title(raw: str, n: int) -> str:
    text = re.sub(r"\s+removed\s*$", "", raw, flags=re.I).strip()
    text = re.sub(r"^Salinan\s+", "", text, flags=re.I).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = f"Topical Structured Pair {n}"
    return f"P{n}/MS{n} · {text}"


def wait_for_job(batch_id: str, *, label: str) -> dict:
    """Poll the in-process job state until COMPLETED/FAILED or timeout.

    Must run in the SAME process that called create_batch_and_start —
    job state lives in an in-memory dict, not the database.
    """
    deadline = time.monotonic() + JOB_TIMEOUT_S
    last_stage = None
    while time.monotonic() < deadline:
        job = get_job(batch_id)
        if job is None:
            time.sleep(POLL_INTERVAL_S)
            continue
        if job.stage != last_stage:
            print(f"  [{label}] {job.status}/{job.stage}: {job.message}")
            last_stage = job.stage
        if job.status in {"COMPLETED", "FAILED"}:
            return {"status": job.status, "message": job.message, "error": job.error}
        time.sleep(POLL_INTERVAL_S)
    return {"status": "TIMEOUT", "message": f"No terminal state after {JOB_TIMEOUT_S}s"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Max pairs to ingest (0=all)")
    parser.add_argument("--only", type=int, nargs="*", help="Only these pair numbers")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not MANIFEST.is_file():
        print(f"Missing manifest: {MANIFEST}", file=sys.stderr)
        return 1
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    if args.only:
        want = set(args.only)
        pairs = [p for p in pairs if p["n"] in want]
    if args.limit and args.limit > 0:
        pairs = pairs[: args.limit]

    store = get_store()
    results = []
    for pair in pairs:
        n = pair["n"]
        q_path = Path(pair["questionPath"])
        a_path = Path(pair["answerPath"])
        title = clean_title(pair.get("title") or "", n)
        paper_code = f"TOPICAL-P{n}"
        print(f"Ingesting {title} ...")
        if args.dry_run:
            results.append({"n": n, "title": title, "dryRun": True})
            continue
        if not q_path.is_file() or not a_path.is_file():
            results.append({"n": n, "error": "pdf missing"})
            print(f"  SKIP missing PDF")
            continue
        try:
            created = create_batch_and_start(
                store=store,
                title=title,
                subject="Chemistry",
                question_pdf=q_path,
                answer_pdf=a_path,
                exam_year=None,
                paper_code=paper_code,
                expected_question_count=None,
                output_root=ROOT / "output",
            )
            print(f"  batch={created['batchId']} — waiting for it to finish...")
            outcome = wait_for_job(created["batchId"], label=f"P{n}")
            print(f"  {outcome['status']}: {outcome.get('message') or outcome.get('error') or ''}")
            results.append({"n": n, "title": title, **created, **outcome})
        except Exception as exc:  # noqa: BLE001
            results.append({"n": n, "title": title, "error": str(exc)})
            print(f"  ERROR: {exc}")

    out = ROOT / "output" / "drive-splitted-topical" / "ingest_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {out} ({len(results)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

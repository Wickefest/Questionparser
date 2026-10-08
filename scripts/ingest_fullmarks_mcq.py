"""Ingest split fullmarks MCQ question/answer PDF pairs into Question Factory
as DRAFT batches for human review (mirrors ingest_drive_splitted_pairs.py).

Runs create_batch_and_start in-process and polls until each batch reaches a
terminal state before moving to the next, since the background parse thread
would otherwise die when this one-shot script's process exits.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from questbank.api.factory_routes import get_store
from questbank.factory.jobs import create_batch_and_start, get_job

MANIFEST = ROOT / "output" / "drive-mcq-fullmarks" / "split_manifest.json"
JOB_TIMEOUT_S = 900
POLL_INTERVAL_S = 5


def wait_for_job(batch_id: str, *, label: str) -> dict:
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
    parser.add_argument("--limit", type=int, default=0, help="Max items to ingest (0=all)")
    parser.add_argument("--only", type=str, nargs="*", help="Only these slugs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not MANIFEST.is_file():
        print(f"Missing manifest: {MANIFEST}", file=sys.stderr)
        return 1
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = data.get("items") or []
    if args.only:
        want = set(args.only)
        items = [it for it in items if it["slug"] in want]
    if args.limit and args.limit > 0:
        items = items[: args.limit]

    store = get_store()
    results = []
    out_path = ROOT / "output" / "drive-mcq-fullmarks" / "ingest_results.json"
    for idx, item in enumerate(items, start=1):
        slug = item["slug"]
        title = f"MCQ · {item['title']}"
        q_path = Path(item["questionPath"])
        a_path = Path(item["answerPath"])
        print(f"[{idx}/{len(items)}] Ingesting {title} ...")
        if args.dry_run:
            results.append({"slug": slug, "title": title, "dryRun": True})
            continue
        if not q_path.is_file() or not a_path.is_file():
            results.append({"slug": slug, "error": "pdf missing"})
            print("  SKIP missing PDF")
            continue
        try:
            created = create_batch_and_start(
                store=store,
                title=title,
                subject="Chemistry",
                question_pdf=q_path,
                answer_pdf=a_path,
                exam_year=None,
                paper_code="6092/01",
                expected_question_count=None,
                output_root=ROOT / "output",
            )
            print(f"  batch={created['batchId']} — waiting for it to finish...")
            outcome = wait_for_job(created["batchId"], label=slug)
            print(f"  {outcome['status']}: {outcome.get('message') or outcome.get('error') or ''}")
            results.append({"slug": slug, "title": title, **created, **outcome})
        except Exception as exc:  # noqa: BLE001
            results.append({"slug": slug, "title": title, "error": str(exc)})
            print(f"  ERROR: {exc}")

        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"Wrote {out_path} ({len(results)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

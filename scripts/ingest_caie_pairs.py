"""Ingest CAIE O-Level Chemistry 5070 QP/MS pairs into Question Factory.

Matches files named 5070_s25_qp_NN.pdf to 5070_s25_ms_NN.pdf by their shared
NN suffix (paper/variant number, e.g. 11, 12, 21...), then reuses the same
create_batch_and_start + wait_for_job pattern as ingest_drive_splitted_pairs.py
so a batch is never abandoned mid-parse when this one-shot script exits.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from questbank.api.factory_routes import get_store
from questbank.factory.jobs import create_batch_and_start, get_job

FOLDER = ROOT / "downloads" / "caie-o-level-chemistry-5070-2025-may-june"
JOB_TIMEOUT_S = 900
POLL_INTERVAL_S = 5

PAPER_NAMES = {
    "1": "Multiple Choice",
    "2": "Theory",
    "3": "Practical Test",
    "4": "Alternative to Practical",
}


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
    qp_files = sorted(FOLDER.glob("5070_s25_qp_*.pdf"))
    pairs = []
    for qp in qp_files:
        m = re.search(r"qp_(\d+)\.pdf$", qp.name)
        if not m:
            continue
        nn = m.group(1)
        ms = FOLDER / f"5070_s25_ms_{nn}.pdf"
        if not ms.is_file():
            print(f"SKIP {qp.name}: no matching mark scheme ms_{nn}.pdf")
            continue
        paper_num = nn[0]
        variant = nn[1]
        title = f"CAIE 5070 s25 Paper {paper_num} ({PAPER_NAMES.get(paper_num, 'Paper ' + paper_num)}) Variant {variant}"
        pairs.append({"nn": nn, "qp": qp, "ms": ms, "title": title, "paper_code": f"5070/{nn}/S25"})

    print(f"Found {len(pairs)} QP/MS pairs: {[p['nn'] for p in pairs]}")

    store = get_store()
    results = []
    for pair in pairs:
        print(f"Ingesting {pair['title']} ...")
        try:
            created = create_batch_and_start(
                store=store,
                title=pair["title"],
                subject="Chemistry",
                question_pdf=pair["qp"],
                answer_pdf=pair["ms"],
                exam_year=2025,
                paper_code=pair["paper_code"],
                expected_question_count=None,
                output_root=ROOT / "output",
            )
            print(f"  batch={created['batchId']} — waiting for it to finish...")
            outcome = wait_for_job(created["batchId"], label=pair["nn"])
            print(f"  {outcome['status']}: {outcome.get('message') or outcome.get('error') or ''}")
            results.append({"nn": pair["nn"], "title": pair["title"], **created, **outcome})
        except Exception as exc:  # noqa: BLE001
            results.append({"nn": pair["nn"], "title": pair["title"], "error": str(exc)})
            print(f"  ERROR: {exc}")

    out = ROOT / "output" / "caie_ingest_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {out} ({len(results)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

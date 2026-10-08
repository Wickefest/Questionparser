"""Retroactively flag structured drafts whose answer labels don't match the
question's own parts — the exact silent-mismatch bug found by hand in P6/Q5
(mark scheme graded "c(i)".."c(iv)" while the question only has those under
"d"). This was fixed going forward via answer_covers_question_parts() in
structured_to_canonical.py, but drafts ingested before that fix never got
checked. No re-parsing, no Gemini calls — just re-reads what's already
stored and updates requires_review where the mismatch is found.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psycopg
from dotenv import load_dotenv

from questbank.parsers.chemistry.structured_to_canonical import answer_covers_question_parts
from questbank.types.canonical import StructuredAnswerPart, StructuredAnswerRecord
from questbank.types.structured import StructuredPart

load_dotenv(ROOT / ".env")


def build_parts(raw: list[dict]) -> list[StructuredPart]:
    return [StructuredPart.model_validate(p) for p in (raw or [])]


def build_answer(question_ref: str, raw: list[dict]) -> StructuredAnswerRecord:
    parts = [StructuredAnswerPart.model_validate(p) for p in (raw or [])]
    return StructuredAnswerRecord(question_ref=question_ref, parts=parts)


def main() -> int:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    checked = 0
    flagged = 0
    already_flagged = 0
    no_answer = 0
    mismatches: list[dict] = []

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT qd.id::text, qd.run_id::text, qd.question_ref, qd.options,
                       qd.requires_review, ib.title, ar.parts_answer
                FROM "question_drafts" qd
                JOIN "ingestion_batches" ib ON ib.id = qd.batch_id
                LEFT JOIN "answer_records" ar
                  ON ar.run_id = qd.run_id AND ar.question_ref = qd.question_ref
                WHERE qd.question_type = 'STRUCTURED'::question_type
                """
            )
            rows = cur.fetchall()

        for draft_id, run_id, ref, options, requires_review, title, parts_answer in rows:
            checked += 1
            if parts_answer is None:
                no_answer += 1
                continue
            try:
                parts = build_parts((options or {}).get("parts") or [])
                answer = build_answer(ref, parts_answer)
                covered = answer_covers_question_parts(parts, answer)
            except Exception as exc:  # noqa: BLE001
                print(f"  SKIP (parse error) draft={draft_id} ref={ref}: {exc}")
                continue

            if covered:
                continue

            mismatches.append({"draftId": draft_id, "ref": ref, "title": title})
            if requires_review:
                already_flagged += 1
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE "question_drafts"
                    SET requires_review = true,
                        status = CASE WHEN status = 'APPROVED' THEN status ELSE 'NEEDS_REVIEW'::draft_status END,
                        updated_at = NOW()
                    WHERE id = %s::uuid
                    """,
                    (draft_id,),
                )
            flagged += 1

        conn.commit()

    print(f"Checked {checked} structured drafts ({no_answer} with no paired answer yet).")
    print(f"Found {len(mismatches)} with a question/answer label mismatch:")
    print(f"  newly flagged requires_review: {flagged}")
    print(f"  already flagged: {already_flagged}")
    for m in mismatches:
        print(f"  - {m['title']} Q{m['ref']} (draft {m['draftId']})")

    out = ROOT / "output" / "structured_coverage_audit.json"
    out.write_text(json.dumps(mismatches, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

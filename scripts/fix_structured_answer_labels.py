"""Positional re-alignment for structured answer labels that were attached
to the wrong parent letter (e.g. mark scheme says "c(i)".."c(iv)" but the
question's own parts put that same sequence under "d"). Exam mark schemes
are always written in question order, so when a question letter's
sub-part *count* matches an orphaned answer group's count exactly, and no
other question letter is a better match, we relabel the answer group's
parent letter to the question's — content, marks, and wording are never
touched, only the letter prefix.

Defaults to dry-run: prints every proposed change without writing. Pass
--apply to actually update answer_records.parts_answer in the DB.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import psycopg
from dotenv import load_dotenv

from questbank.parsers.chemistry.parse_structured_questions_gemini import (
    flatten_structured_parts,
    normalize_structured_part_label,
)
from questbank.types.structured import StructuredPart

load_dotenv(ROOT / ".env")

_COMPOSITE_RE = re.compile(r"^([a-z])\(([ivxlc]+)\)$")


def group_by_letter(labels: list[str]) -> dict[str, list[str]]:
    """{'d': ['i','ii','iii','iv'], 'a': [] (bare letter only), ...}"""
    groups: dict[str, list[str]] = defaultdict(list)
    for label in labels:
        m = _COMPOSITE_RE.match(label)
        if m:
            groups[m.group(1)].append(m.group(2))
        else:
            groups.setdefault(label, [])
    return dict(groups)


def propose_relabel(question_labels: list[str], answer_parts: list[dict]) -> dict[str, str] | None:
    """Return {old_label: new_label} for answer parts, or None if no safe fix found."""
    answer_labels = [normalize_structured_part_label(p.get("label", "")) for p in answer_parts]
    q_groups = group_by_letter(question_labels)
    a_groups = group_by_letter(answer_labels)

    # Question letters whose roman sequence has NO matching answer letter.
    missing = {
        letter: romans
        for letter, romans in q_groups.items()
        if romans and a_groups.get(letter) != romans
    }
    # Answer letters that don't correspond to any real question group at all.
    extra = {
        letter: romans
        for letter, romans in a_groups.items()
        if romans and letter not in q_groups
    }

    if len(missing) != 1 or len(extra) != 1:
        return None
    (miss_letter, miss_romans), = missing.items()
    (extra_letter, extra_romans), = extra.items()
    if miss_romans != extra_romans:
        return None  # different sequence — not a safe positional match

    mapping = {}
    for roman in extra_romans:
        old = f"{extra_letter}({roman})"
        new = f"{miss_letter}({roman})"
        mapping[old] = new
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="Show/apply at most N drafts (0=all)")
    args = parser.parse_args()

    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    fixed = 0
    unresolved = 0
    shown = 0

    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT qd.id::text, qd.run_id::text, qd.question_ref, qd.options,
                       ib.title, ar.id::text, ar.parts_answer
                FROM "question_drafts" qd
                JOIN "ingestion_batches" ib ON ib.id = qd.batch_id
                JOIN "answer_records" ar
                  ON ar.run_id = qd.run_id AND ar.question_ref = qd.question_ref
                WHERE qd.question_type = 'STRUCTURED'::question_type
                  AND qd.requires_review = true
                  AND ar.parts_answer IS NOT NULL
                """
            )
            rows = cur.fetchall()

        for draft_id, run_id, ref, options, title, answer_id, parts_answer in rows:
            if args.limit and shown >= args.limit:
                break
            try:
                q_parts = [StructuredPart.model_validate(p) for p in (options or {}).get("parts") or []]
                question_labels = [item["label"] for item in flatten_structured_parts(q_parts)]
            except Exception as exc:  # noqa: BLE001
                print(f"  SKIP (parse error) {title} Q{ref}: {exc}")
                continue

            mapping = propose_relabel(question_labels, parts_answer)
            if not mapping:
                unresolved += 1
                continue

            shown += 1
            print(f"{title} Q{ref} (answer {answer_id})")
            for old, new in mapping.items():
                print(f"    {old}  ->  {new}")

            if args.apply:
                new_parts = []
                for p in parts_answer:
                    old_label = normalize_structured_part_label(p.get("label", ""))
                    if old_label in mapping:
                        # Swap only the leading letter; keep the original
                        # "(i)"/"(ii)" formatting exactly as printed.
                        new_letter = mapping[old_label][0]
                        p = {**p, "label": re.sub(r"^[a-zA-Z]", new_letter, str(p["label"]).strip())}
                    new_parts.append(p)
                with conn.cursor() as cur:
                    cur.execute(
                        'UPDATE "answer_records" SET parts_answer = %s::jsonb WHERE id = %s::uuid',
                        (json.dumps(new_parts), answer_id),
                    )
                fixed += 1

        if args.apply:
            conn.commit()

    mode = "APPLIED" if args.apply else "DRY-RUN (pass --apply to write)"
    print(f"\n[{mode}] proposals shown: {shown}, no-safe-fix: {unresolved}")
    if args.apply:
        print(f"Updated {fixed} answer records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Take output/fullmarks_normalized.json and shape it exactly like the live
Supabase schema this project already uses for ingested papers:

  ingestion_batches  (one per chapter, mirrors "one batch per paper" convention)
  ingestion_runs     (one per batch)
  question_drafts    (question_type='STRUCTURED', options = the parts tree)
  answer_records     (parts_answer = the marking points)

Every id is a real uuid4 generated up front, so the output can be executed
directly as INSERT statements with no further transformation - this is the
"separated and structured as if ready to be ingested" artifact.

The 5 questions whose source answer key was never scraped (still literally
"Opening the mark scheme...") are still inserted as drafts (so the question
text isn't lost) but flagged requires_review=true with a validation_issues
note, and get an empty parts_answer - exactly the pattern already used in
this project for other incomplete drafts.

Run: .venv/Scripts/python.exe scripts/build_fullmarks_ingest_ready.py
Outputs:
  output/fullmarks_ingest_ready.json   (structured rows, per batch)
  output/fullmarks_ingest.sql          (literal INSERT statements)
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

SRC = Path("output/fullmarks_normalized.json")
OUT_JSON = Path("output/fullmarks_ingest_ready.json")
OUT_SQL = Path("output/fullmarks_ingest.sql")

MISSING_ANSWER_MARK = "Opening the mark scheme"


def sql_str(s: str | None) -> str:
    if s is None:
        return "NULL"
    return "'" + s.replace("'", "''") + "'"


def sql_json(obj) -> str:
    return sql_str(json.dumps(obj, ensure_ascii=False)) + "::jsonb"


def main() -> None:
    chapters = json.loads(SRC.read_text(encoding="utf-8"))

    batches_out = []
    sql_lines: list[str] = []

    for ch_index, ch in enumerate(chapters, start=1):
        chapter_title = ch["chapter"]
        batch_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())

        batch_row = {
            "id": batch_id,
            "title": chapter_title,
            "subject": "Chemistry",
            "exam_year": None,
            "paper_code": None,
            "syllabus_id": None,
            "status": "COMPLETED",
        }
        run_row = {
            "id": run_id,
            "batch_id": batch_id,
            "parser_version": "fullmarks-import-v1",
            "model_name": None,
            "status": "COMPLETED",
        }

        draft_rows = []
        answer_rows = []

        for q in ch["questions"]:
            question_ref = str(q["question_number"])
            is_missing_answer = not q["parts_answer"] or any(
                MISSING_ANSWER_MARK in a for pa in q["parts_answer"] for a in pa["accepted_answers"]
            )
            # the normalizer already produced empty accepted_answers=[] for those
            # 5 questions (no B/M/A points found), so parts_answer entries exist
            # but each accepted_answers list is empty - detect that instead:
            is_missing_answer = any(len(pa["accepted_answers"]) == 0 for pa in q["parts_answer"]) if q["parts_answer"] else True

            draft_id = str(uuid.uuid4())
            draft_row = {
                "id": draft_id,
                "run_id": run_id,
                "batch_id": batch_id,
                "question_ref": question_ref,
                "question_type": "STRUCTURED",
                "content": q["content"],
                "options": q["options"],
                "status": "DRAFT",
                "requires_review": bool(is_missing_answer),
                "validation_issues": (
                    ["Source answer key was not scraped (placeholder 'Opening the mark scheme...' only) - needs manual re-entry"]
                    if is_missing_answer
                    else None
                ),
                "topic_node_id": None,
            }
            draft_rows.append(draft_row)

            answer_row = {
                "run_id": run_id,
                "batch_id": batch_id,
                "question_ref": question_ref,
                "answer_value": None,
                "explanation": None,
                "explanation_source": None,
                "parts_answer": q["parts_answer"] if not is_missing_answer else [],
            }
            answer_rows.append(answer_row)

        batches_out.append(
            {
                "batch": batch_row,
                "run": run_row,
                "question_drafts": draft_rows,
                "answer_records": answer_rows,
            }
        )

        # ---- SQL ----
        sql_lines.append(f"-- ===== Chapter {ch_index}: {chapter_title} =====")
        sql_lines.append(
            "INSERT INTO ingestion_batches (id, title, subject, status) VALUES "
            f"('{batch_id}', {sql_str(chapter_title)}, 'Chemistry', 'COMPLETED');"
        )
        sql_lines.append(
            "INSERT INTO ingestion_runs (id, batch_id, parser_version, status) VALUES "
            f"('{run_id}', '{batch_id}', 'fullmarks-import-v1', 'COMPLETED');"
        )

        if draft_rows:
            sql_lines.append("INSERT INTO question_drafts (id, run_id, batch_id, question_ref, question_type, content, options, status, requires_review, validation_issues) VALUES")
            value_lines = []
            for d in draft_rows:
                validation_sql = (
                    sql_json(d["validation_issues"]) if d["validation_issues"] is not None else "NULL"
                )
                value_lines.append(
                    "  ("
                    f"'{d['id']}', '{d['run_id']}', '{d['batch_id']}', {sql_str(d['question_ref'])}, "
                    f"'{d['question_type']}', {sql_json(d['content'])}, {sql_json(d['options'])}, "
                    f"'{d['status']}', {str(d['requires_review']).lower()}, {validation_sql}"
                    ")"
                )
            sql_lines.append(",\n".join(value_lines) + ";")

        if answer_rows:
            sql_lines.append("INSERT INTO answer_records (run_id, batch_id, question_ref, parts_answer) VALUES")
            value_lines = []
            for a in answer_rows:
                value_lines.append(
                    "  ("
                    f"'{a['run_id']}', '{a['batch_id']}', {sql_str(a['question_ref'])}, {sql_json(a['parts_answer'])}"
                    ")"
                )
            sql_lines.append(",\n".join(value_lines) + ";")

        sql_lines.append("")

    OUT_JSON.write_text(json.dumps(batches_out, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_SQL.write_text("\n".join(sql_lines), encoding="utf-8")

    total_q = sum(len(b["question_drafts"]) for b in batches_out)
    total_flagged = sum(1 for b in batches_out for d in b["question_drafts"] if d["requires_review"])
    print(f"Wrote {OUT_JSON} - {len(batches_out)} batches, {total_q} questions ({total_flagged} flagged requires_review)")
    print(f"Wrote {OUT_SQL} ({len(sql_lines)} lines)")


if __name__ == "__main__":
    main()

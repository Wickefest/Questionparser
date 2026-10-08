"""Re-bind all APPROVED drafts into unique batch-scoped bank questions.

Old source keys were year-paper-Q only, so two imports both using Q1–Q10
collapsed into 10 bank rows. New keys include ``-B{batch8}``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env")

from questbank.factory.mapping import ensure_list_blocks  # noqa: E402
from questbank.factory.review import make_source_key  # noqa: E402


def main() -> int:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL missing", file=sys.stderr)
        return 1

    import psycopg

    created = 0
    skipped = 0
    cleaned = 0
    now = datetime.now(timezone.utc)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id::text, d.batch_id::text, d.question_ref, d.content, d.options,
                       d.bloom_level, d.bloom_reasoning, d.topic_node_id::text,
                       b.subject, b.exam_year, b.paper_code,
                       a.answer_value, a.explanation, a.explanation_source::text
                FROM question_drafts d
                JOIN ingestion_batches b ON b.id = d.batch_id
                LEFT JOIN answer_records a
                  ON a.run_id = d.run_id AND a.question_ref = d.question_ref
                WHERE d.status = 'APPROVED'::draft_status
                ORDER BY b.created_at, d.question_ref
                """
            )
            drafts = cur.fetchall()

            for row in drafts:
                (
                    draft_id,
                    batch_id,
                    qref,
                    content,
                    options,
                    bloom_level,
                    bloom_reasoning,
                    topic_node_id,
                    subject,
                    exam_year,
                    paper_code,
                    answer_value,
                    explanation,
                    explanation_source,
                ) = row

                source_key = make_source_key(
                    subject=subject or "Chemistry",
                    exam_year=exam_year,
                    paper_code=paper_code,
                    question_ref=qref,
                    batch_id=batch_id,
                )
                content = ensure_list_blocks(content)

                # Already linked under this exact source key?
                cur.execute(
                    """
                    SELECT v.id::text
                    FROM question_versions v
                    JOIN bank_questions q ON q.id = v.question_id
                    WHERE v.source_draft_id = %s::uuid
                      AND q.source_key = %s
                      AND v.status = 'APPROVED'::question_version_status
                    LIMIT 1
                    """,
                    (draft_id, source_key),
                )
                if cur.fetchone():
                    skipped += 1
                    continue

                # Upsert bank question
                cur.execute(
                    'SELECT id::text FROM "bank_questions" WHERE source_key = %s',
                    (source_key,),
                )
                existing = cur.fetchone()
                if existing:
                    question_id = existing[0]
                else:
                    question_id = str(uuid4())
                    cur.execute(
                        """
                        INSERT INTO "bank_questions" (id, source_key, subject)
                        VALUES (%s::uuid, %s, %s)
                        """,
                        (question_id, source_key, subject or "Chemistry"),
                    )

                # Retire active versions on this question
                cur.execute(
                    """
                    UPDATE "question_versions"
                    SET status = 'RETIRED'::question_version_status
                    WHERE question_id = %s::uuid
                      AND status <> 'RETIRED'::question_version_status
                    """,
                    (question_id,),
                )
                cur.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM "question_versions"
                    WHERE question_id = %s::uuid
                    """,
                    (question_id,),
                )
                version_num = cur.fetchone()[0]
                version_id = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO "question_versions"
                      (id, question_id, version, content, options, source_draft_id,
                       status, approved_at, bloom_level, bloom_reasoning)
                    VALUES
                      (%s::uuid, %s::uuid, %s, %s::jsonb, %s::jsonb, %s::uuid,
                       'APPROVED'::question_version_status, %s,
                       %s, %s)
                    """,
                    (
                        version_id,
                        question_id,
                        version_num,
                        json.dumps(content),
                        json.dumps(options),
                        draft_id,
                        now,
                        bloom_level,
                        bloom_reasoning,
                    ),
                )

                if answer_value is not None and str(answer_value).strip() != "":
                    expl_src = None
                    if explanation_source:
                        # Normalize enum label if needed
                        expl_src = str(explanation_source).upper()
                        if expl_src not in {"MARK_SCHEME", "AI", "MANUAL"}:
                            expl_src = "MARK_SCHEME" if explanation else None
                    cur.execute(
                        """
                        INSERT INTO "question_answers"
                          (id, question_version_id, value, explanation, explanation_source)
                        VALUES
                          (%s::uuid, %s::uuid, %s, %s, %s::explanation_source)
                        """,
                        (
                            str(uuid4()),
                            version_id,
                            answer_value,
                            explanation,
                            expl_src,
                        ),
                    )

                # Copy syllabus classification from draft topic node if present
                if topic_node_id:
                    cur.execute(
                        """
                        INSERT INTO "question_classifications"
                          (id, question_version_id, syllabus_node_id, is_primary, status, classified_by)
                        VALUES
                          (%s::uuid, %s::uuid, %s::uuid, true,
                           'CLASSIFIED'::classification_status, 'batch-rebind')
                        ON CONFLICT (question_version_id, syllabus_node_id)
                        DO UPDATE SET
                          is_primary = true,
                          status = 'CLASSIFIED'::classification_status,
                          classified_by = EXCLUDED.classified_by,
                          updated_at = CURRENT_TIMESTAMP
                        """,
                        (str(uuid4()), version_id, topic_node_id),
                    )
                else:
                    # Fall back: copy from any prior version of this draft
                    cur.execute(
                        """
                        SELECT c.syllabus_node_id::text, c.classified_by
                        FROM question_versions v
                        JOIN question_classifications c ON c.question_version_id = v.id
                        WHERE v.source_draft_id = %s::uuid
                          AND c.status = 'CLASSIFIED'::classification_status
                          AND c.is_primary = true
                        ORDER BY v.version DESC
                        LIMIT 1
                        """,
                        (draft_id,),
                    )
                    prev = cur.fetchone()
                    if prev:
                        cur.execute(
                            """
                            INSERT INTO "question_classifications"
                              (id, question_version_id, syllabus_node_id, is_primary, status, classified_by)
                            VALUES
                              (%s::uuid, %s::uuid, %s::uuid, true,
                               'CLASSIFIED'::classification_status, %s)
                            ON CONFLICT (question_version_id, syllabus_node_id)
                            DO UPDATE SET
                              is_primary = true,
                              status = 'CLASSIFIED'::classification_status,
                              classified_by = EXCLUDED.classified_by,
                              updated_at = CURRENT_TIMESTAMP
                            """,
                            (str(uuid4()), version_id, prev[0], prev[1] or "batch-rebind"),
                        )

                created += 1
                print(f"  + {source_key}  (draft {qref})")

            # Remove legacy collision keys that no longer have a batch suffix
            # only when every approved draft from those versions is re-bound.
            cur.execute(
                """
                SELECT q.id::text, q.source_key
                FROM bank_questions q
                WHERE q.source_key !~ '-B[0-9a-f]{6,}$'
                """
            )
            legacy = cur.fetchall()
            for qid, skey in legacy:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM question_versions v
                    WHERE v.question_id = %s::uuid
                      AND v.status = 'APPROVED'::question_version_status
                      AND EXISTS (
                        SELECT 1 FROM question_versions v2
                        JOIN bank_questions q2 ON q2.id = v2.question_id
                        WHERE v2.source_draft_id = v.source_draft_id
                          AND q2.source_key ~ '-B[0-9a-f]{6,}$'
                          AND v2.status = 'APPROVED'::question_version_status
                      )
                    """,
                    (qid,),
                )
                rebound = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT COUNT(*) FROM question_versions v
                    WHERE v.question_id = %s::uuid
                      AND v.status = 'APPROVED'::question_version_status
                    """,
                    (qid,),
                )
                active = cur.fetchone()[0]
                if active > 0 and rebound >= active:
                    # Safe to drop legacy row once drafts live under batch keys.
                    # Cascade via FKs may not exist — delete children first.
                    cur.execute(
                        """
                        DELETE FROM question_answers
                        WHERE question_version_id IN (
                          SELECT id FROM question_versions WHERE question_id = %s::uuid
                        )
                        """,
                        (qid,),
                    )
                    cur.execute(
                        """
                        DELETE FROM question_classifications
                        WHERE question_version_id IN (
                          SELECT id FROM question_versions WHERE question_id = %s::uuid
                        )
                        """,
                        (qid,),
                    )
                    cur.execute(
                        'DELETE FROM question_versions WHERE question_id = %s::uuid',
                        (qid,),
                    )
                    cur.execute('DELETE FROM bank_questions WHERE id = %s::uuid', (qid,))
                    cleaned += 1
                    print(f"  - removed legacy {skey}")

            cur.execute('SELECT COUNT(*) FROM bank_questions')
            bank_total = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM question_versions
                WHERE status = 'APPROVED'::question_version_status
                """
            )
            approved_versions = cur.fetchone()[0]

        conn.commit()

    print(f"created/updated={created} skipped_existing={skipped} legacy_removed={cleaned}")
    print(f"bank_questions={bank_total} approved_versions={approved_versions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

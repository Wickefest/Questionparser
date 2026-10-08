"""Copy parse-time draft topic/bloom onto approved bank versions missing them."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")


def main() -> int:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL missing", file=sys.stderr)
        return 1

    import psycopg

    copied_topic = 0
    copied_bloom = 0

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.id::text, d.topic_node_id::text, d.bloom_level, d.bloom_reasoning,
                       q.source_key, d.topic_node_id IS NOT NULL AS has_topic
                FROM question_versions v
                JOIN question_drafts d ON d.id = v.source_draft_id
                JOIN bank_questions q ON q.id = v.question_id
                WHERE v.status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                  AND (
                    (d.topic_node_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM question_classifications c
                       WHERE c.question_version_id = v.id
                         AND c.status = 'CLASSIFIED'::classification_status
                         AND c.is_primary = true
                    ))
                    OR (
                      d.bloom_level IS NOT NULL
                      AND (v.bloom_level IS NULL OR btrim(v.bloom_level) = '')
                    )
                  )
                """
            )
            rows = cur.fetchall()
            for version_id, topic_node_id, bloom_level, bloom_reasoning, source_key, _has_topic in rows:
                if topic_node_id:
                    cur.execute(
                        """
                        SELECT 1 FROM question_classifications
                        WHERE question_version_id = %s::uuid
                          AND status = 'CLASSIFIED'::classification_status
                          AND is_primary = true
                        LIMIT 1
                        """,
                        (version_id,),
                    )
                    if cur.fetchone() is None:
                        cur.execute(
                            """
                            INSERT INTO question_classifications
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
                            (
                                str(uuid4()),
                                version_id,
                                topic_node_id,
                                "Parse / draft classification",
                            ),
                        )
                        copied_topic += 1
                        print(f"  + topic -> {source_key}")

                if bloom_level and str(bloom_level).strip():
                    cur.execute(
                        """
                        UPDATE question_versions
                        SET bloom_level = COALESCE(NULLIF(btrim(bloom_level), ''), %s),
                            bloom_reasoning = COALESCE(NULLIF(btrim(bloom_reasoning), ''), %s)
                        WHERE id = %s::uuid
                          AND (bloom_level IS NULL OR btrim(bloom_level) = '')
                        """,
                        (bloom_level, bloom_reasoning, version_id),
                    )
                    if cur.rowcount:
                        copied_bloom += 1
                        print(f"  + bloom -> {source_key} ({bloom_level})")

            cur.execute(
                """
                SELECT COUNT(*) FROM question_versions v
                WHERE v.status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                  AND EXISTS (
                    SELECT 1 FROM question_classifications c
                    WHERE c.question_version_id = v.id
                      AND c.status = 'CLASSIFIED'::classification_status
                  )
                """
            )
            classified = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM question_versions v
                WHERE v.status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                """
            )
            total = cur.fetchone()[0]

        conn.commit()

    print(f"copied_topic={copied_topic} copied_bloom={copied_bloom}")
    print(f"classified_bank_versions={classified}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

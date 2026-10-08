"""Upgrade flat numbered statement text into list blocks in Supabase.

Updates question_versions and question_drafts where content still stores
1./2./3. statements as a single text block.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env")

from questbank.factory.mapping import ensure_list_blocks  # noqa: E402


def _changed(before, after) -> bool:
    return json.dumps(before, sort_keys=True, default=str) != json.dumps(
        after, sort_keys=True, default=str
    )


def _has_list(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "list" for b in content)


def main() -> int:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        print("DATABASE_URL missing", file=sys.stderr)
        return 1

    import psycopg

    upgraded_versions = 0
    upgraded_drafts = 0
    scanned_versions = 0
    scanned_drafts = 0
    samples: list[str] = []

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id::text, version, status::text, content FROM "question_versions" ORDER BY version'
            )
            rows = cur.fetchall()
            for row_id, version, status, content in rows:
                scanned_versions += 1
                after = ensure_list_blocks(content)
                if not _changed(content, after):
                    continue
                cur.execute(
                    'UPDATE "question_versions" SET content = %s::jsonb WHERE id = %s::uuid',
                    (json.dumps(after), row_id),
                )
                upgraded_versions += 1
                if len(samples) < 12:
                    lead = ""
                    items = 0
                    if isinstance(after, list):
                        for b in after:
                            if isinstance(b, dict) and b.get("type") == "text" and not lead:
                                lead = (b.get("value") or "")[:80]
                            if isinstance(b, dict) and b.get("type") == "list":
                                items = len(b.get("items") or [])
                    samples.append(
                        f"version {row_id[:8]}… v{version} {status}: list({items}) | {lead!r}"
                    )

            cur.execute(
                'SELECT id::text, question_ref, status::text, content FROM "question_drafts" ORDER BY question_ref'
            )
            drafts = cur.fetchall()
            for draft_id, qref, status, content in drafts:
                scanned_drafts += 1
                after = ensure_list_blocks(content)
                if not _changed(content, after):
                    continue
                cur.execute(
                    'UPDATE "question_drafts" SET content = %s::jsonb WHERE id = %s::uuid',
                    (json.dumps(after), draft_id),
                )
                upgraded_drafts += 1
                if len(samples) < 20:
                    items = 0
                    if isinstance(after, list):
                        for b in after:
                            if isinstance(b, dict) and b.get("type") == "list":
                                items = len(b.get("items") or [])
                    samples.append(f"draft {qref} {status}: list({items})")

        conn.commit()

        # Verify remaining flat numbered candidates
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM "question_versions" v
                WHERE EXISTS (
                  SELECT 1 FROM jsonb_array_elements(v.content) elem
                  WHERE elem->>'type' = 'text'
                    AND (elem->>'value') ~ E'\\n[[:space:]]*[0-9]{1,2}[[:space:]]*[.\\)][[:space:]]+\\S'
                )
                """
            )
            remaining_v = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM "question_drafts" d
                WHERE EXISTS (
                  SELECT 1 FROM jsonb_array_elements(d.content) elem
                  WHERE elem->>'type' = 'text'
                    AND (elem->>'value') ~ E'\\n[[:space:]]*[0-9]{1,2}[[:space:]]*[.\\)][[:space:]]+\\S'
                )
                """
            )
            remaining_d = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM "question_versions" v
                WHERE EXISTS (
                  SELECT 1 FROM jsonb_array_elements(v.content) elem
                  WHERE elem->>'type' = 'list'
                )
                """
            )
            list_versions = cur.fetchone()[0]
            cur.execute(
                """
                SELECT COUNT(*) FROM "question_drafts" d
                WHERE EXISTS (
                  SELECT 1 FROM jsonb_array_elements(d.content) elem
                  WHERE elem->>'type' = 'list'
                )
                """
            )
            list_drafts = cur.fetchone()[0]

    print(f"scanned versions={scanned_versions} drafts={scanned_drafts}")
    print(f"upgraded versions={upgraded_versions} drafts={upgraded_drafts}")
    print(f"now have list blocks: versions={list_versions} drafts={list_drafts}")
    print(f"remaining flat numbered text candidates: versions={remaining_v} drafts={remaining_d}")
    if samples:
        print("samples:")
        for s in samples:
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

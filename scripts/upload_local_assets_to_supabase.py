"""One-off migration: upload local factory-storage assets to Supabase Storage.

Walks output/factory-storage/question-assets/** and uploads every file to the
Supabase "questions" bucket at the SAME relative path already recorded in
question_assets.storage_path, so no database rows need to change.

Usage:
    python scripts/upload_local_assets_to_supabase.py [--dry-run]
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from questbank.factory.local_storage import factory_storage_root  # noqa: E402
from questbank.integrations.supabase_store import get_supabase_client, questions_bucket  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="List what would upload, without uploading")
    args = parser.parse_args()

    root = factory_storage_root()
    assets_dir = root / "question-assets"
    if not assets_dir.is_dir():
        print(f"No local assets dir found at {assets_dir}")
        return 1

    files = sorted(p for p in assets_dir.rglob("*") if p.is_file())
    print(f"Found {len(files)} local files under {assets_dir}")

    if args.dry_run:
        for p in files[:20]:
            rel = p.relative_to(root).as_posix()
            print(f"  would upload: {rel}")
        if len(files) > 20:
            print(f"  ... and {len(files) - 20} more")
        return 0

    client = get_supabase_client()
    if client is None:
        print("Supabase is not configured (SUPABASE_URL / SUPABASE_KEY missing). Aborting.")
        return 1

    bucket = questions_bucket()
    ok, failed = 0, []
    for p in files:
        storage_path = p.relative_to(root).as_posix()
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        data = p.read_bytes()
        try:
            client.storage.from_(bucket).upload(
                path=storage_path,
                file=data,
                file_options={"content-type": mime, "upsert": "true"},
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((storage_path, str(exc)))
        if ok % 50 == 0 and ok:
            print(f"  uploaded {ok}/{len(files)}...")

    print(f"Done. Uploaded {ok}/{len(files)} to bucket '{bucket}'.")
    if failed:
        print(f"{len(failed)} failed:")
        for path, err in failed[:20]:
            print(f"  FAILED {path}: {err}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

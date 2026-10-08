"""Execute the fullmarks ingest SQL chunks (output/ingest_chunk_NN.sql) directly
against the Postgres database, one chapter at a time, each in its own
transaction so a failure in one chapter doesn't roll back the others already
committed.

Run: .venv/Scripts/python.exe scripts/run_fullmarks_ingest_sql.py
"""

from __future__ import annotations

import glob
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not set", file=sys.stderr)
    sys.exit(1)

chunk_files = sorted(glob.glob("output/ingest_chunk_*.sql"))
print(f"Found {len(chunk_files)} chunk files")

conn = psycopg.connect(DATABASE_URL, autocommit=False)

for path in chunk_files:
    sql = open(path, encoding="utf-8").read()
    first_line = sql.split("\n", 1)[0]
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        print(f"OK   {path}: {first_line}")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"FAIL {path}: {first_line}\n  {exc}")

conn.close()

"""Serve the React parse viewer (Vite) with /output access for smoke JSON + assets.

Usage:
  python scripts/serve_viewer.py
  # opens http://127.0.0.1:8765/

Requires: Node.js + npm dependencies in web/ (npm install).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve QuestBank React parse viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Run npm install in web/ before starting",
    )
    args = parser.parse_args()

    if not (WEB / "package.json").exists():
        print(f"Missing {WEB / 'package.json'}", file=sys.stderr)
        return 2

    npm = shutil.which("npm")
    if not npm:
        print("npm not found on PATH. Install Node.js first.", file=sys.stderr)
        return 2

    if args.install or not (WEB / "node_modules").exists():
        print("Installing web dependencies…")
        subprocess.check_call([npm, "install"], cwd=str(WEB))

    env = os.environ.copy()
    env["BROWSER"] = "none" if args.no_open else env.get("BROWSER", "")
    cmd = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if not args.no_open:
        cmd.append("--open")

    print(f"Starting Vite viewer for {WEB}")
    print(f"Smoke samples: /output/smoke-page-16/matched_questions.json")
    return subprocess.call(cmd, cwd=str(WEB), env=env)


if __name__ == "__main__":
    raise SystemExit(main())

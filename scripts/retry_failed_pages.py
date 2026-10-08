"""Retry question-paper pages that previously failed (default: 2, 12)."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from questbank.config.env import load_questbank_env
from questbank.llm.gemini_client import GeminiClient
from questbank.parsers.chemistry.parse_mcq_gemini import parse_mcq_questions_gemini


def main() -> int:
    load_questbank_env()
    os.environ.setdefault("GEMINI_MAX_OUTPUT_TOKENS", "16384")

    pages = [int(x) for x in sys.argv[1:]] or [2, 12]
    pdf = Path(
        "output/factory-storage/exam-sources/"
        "1f64807e-5dc6-4805-8ca5-e3dd564e31d0/question-paper.pdf"
    )
    out = Path("output/bbss-retry-p2-p12")
    assets = out / "assets"
    out.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    if not pdf.is_file():
        print(f"Missing PDF: {pdf}")
        return 2

    client = GeminiClient()
    client.ensure_available()
    print(f"model={client.model}")
    print(f"GEMINI_MAX_OUTPUT_TOKENS={os.environ.get('GEMINI_MAX_OUTPUT_TOKENS')}")
    print(f"pdf={pdf} bytes={pdf.stat().st_size}")

    results: dict = {}
    for page in pages:
        print(f"--- page {page} ---", flush=True)
        try:
            qs = parse_mcq_questions_gemini(
                pdf,
                assets_dir=assets,
                client=client,
                dpi=150,
                pages=[page],
                check_gemini=False,
                use_layout_context=False,
            )
            info = []
            for q in qs:
                imgs = [
                    getattr(b, "asset", None)
                    for b in q.content
                    if getattr(b, "type", None) == "image"
                ]
                info.append(
                    {
                        "ref": q.question_ref,
                        "review": q.requires_review,
                        "blocks": [getattr(b, "type", None) for b in q.content],
                        "images": imgs,
                        "options_mode": getattr(q.options, "mode", None),
                    }
                )
            results[str(page)] = {
                "ok": True,
                "count": len(qs),
                "refs": [q.question_ref for q in qs],
                "questions": info,
                "latency_ms": client.last_latency_ms,
            }
            print(
                f"  refs={[q.question_ref for q in qs]} "
                f"latency_ms={round(client.last_latency_ms or 0)}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            results[str(page)] = {
                "ok": False,
                "error": str(exc),
                "trace": traceback.format_exc()[-2000:],
            }
            print(f"  FAILED: {exc}", flush=True)

    path = out / "retry_summary.json"
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    ok = all(v.get("ok") for v in results.values()) and all(
        v.get("count", 0) > 0 for v in results.values() if v.get("ok")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

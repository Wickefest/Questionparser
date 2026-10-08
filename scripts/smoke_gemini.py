"""Minimal Gemini connectivity + structured-output smoke test."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

from questbank.llm.gemini_client import GeminiClient


class Smoke(BaseModel):
    ok: bool
    message: str


def main() -> int:
    import sys

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    model = sys.argv[1] if len(sys.argv) > 1 else None
    client = GeminiClient(model=model) if model else GeminiClient()
    client.ensure_available()
    print("model", client.model)
    result = client.generate_structured(
        prompt='Reply with ok=true and message="gemini works".',
        response_model=Smoke,
    )
    print("SMOKE_OK", result.model_dump())
    print("latency_ms", round(client.last_latency_ms or 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

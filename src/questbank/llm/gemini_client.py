"""Reusable Gemini client using the current Google GenAI SDK."""

from __future__ import annotations

import json
import logging
import os
import time
from io import BytesIO
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_TEMPERATURE = 0.0


def _heal_gemini_options_json(payload: Any) -> Any:
    """Recursively repair Gemini option payloads that mix latex/content fields."""
    if isinstance(payload, list):
        return [_heal_gemini_options_json(x) for x in payload]
    if not isinstance(payload, dict):
        return payload

    out = {k: _heal_gemini_options_json(v) for k, v in payload.items()}
    options = out.get("options")
    if isinstance(options, dict):
        mode = str(options.get("mode") or "").strip().lower()
        items = options.get("items")
        if mode and mode not in {"text", "math", "table", "composite_visual", "mixed"}:
            if isinstance(items, list) and any(isinstance(i, dict) and i.get("latex") for i in items):
                mode = "math"
            else:
                mode = "text"
            options = {**options, "mode": mode}
        if mode == "text" and isinstance(items, list):
            healed_items = []
            for item in items:
                if not isinstance(item, dict):
                    healed_items.append(item)
                    continue
                row = dict(item)
                if (row.get("content") is None or row.get("content") == "") and row.get("latex"):
                    row["content"] = row["latex"]
                healed_items.append(row)
            options = {**options, "items": healed_items}
        elif mode == "math" and isinstance(items, list):
            healed_items = []
            for item in items:
                if not isinstance(item, dict):
                    healed_items.append(item)
                    continue
                row = dict(item)
                if (row.get("latex") is None or row.get("latex") == "") and row.get("content"):
                    row["latex"] = row["content"]
                healed_items.append(row)
            options = {**options, "items": healed_items}
        out["options"] = options

    questions = out.get("questions")
    if isinstance(questions, list):
        out["questions"] = [_heal_gemini_options_json(q) for q in questions]
    return out


def _validate_structured_json(response_model: type[T], text: str) -> T:
    """Validate Gemini JSON with a soft-heal pass for option schema slips."""
    try:
        return response_model.model_validate_json(text)
    except Exception as first_exc:  # noqa: BLE001
        try:
            raw = json.loads(text)
        except Exception:  # noqa: BLE001
            raise first_exc from first_exc
        healed = _heal_gemini_options_json(raw)
        try:
            result = response_model.model_validate(healed)
            logger.warning("Healed Gemini structured JSON after validation error: %s", first_exc)
            return result
        except Exception:  # noqa: BLE001
            raise first_exc from first_exc


class GeminiClient:
    """Thin wrapper around ``google.genai.Client`` with structured output."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        temperature: float | None = None,
    ) -> None:
        self.api_key = (api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        self.model = (model or os.environ.get("GEMINI_MODEL") or "").strip()
        self.max_attempts = max(1, int(max_attempts))
        if temperature is None:
            raw = os.environ.get("GEMINI_TEMPERATURE", str(_DEFAULT_TEMPERATURE))
            temperature = float(raw or _DEFAULT_TEMPERATURE)
        self.temperature = temperature
        self.last_latency_ms: float | None = None
        self._client = None

    def ensure_available(self) -> None:
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set. "
                "Copy .env.example to .env and add your key."
            )
        if not self.model:
            raise RuntimeError(
                "GEMINI_MODEL is not set. Set e.g. GEMINI_MODEL=gemini-3.1-flash-lite in .env."
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "google-genai is required. Install with: pip install google-genai"
            ) from exc
        if self._client is None:
            from google.genai import types

            # Hard request timeout so a bad page cannot hang the ingest worker forever.
            timeout_ms = int(os.environ.get("GEMINI_TIMEOUT_MS", "120000"))
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )

    def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[T],
        image=None,
        images: list | None = None,
        max_attempts: int | None = None,
    ) -> T:
        """Call Gemini with a Pydantic response schema; retry on failure."""
        self.ensure_available()
        assert self._client is not None

        attempts = max_attempts if max_attempts is not None else self.max_attempts
        last_error: Exception | None = None
        schema = response_model.model_json_schema()

        pil_images = []
        if image is not None:
            pil_images.append(_as_pil_rgb(image))
        if images:
            pil_images.extend(_as_pil_rgb(img) for img in images)

        token_budget = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "16384"))
        for attempt in range(1, attempts + 1):
            try:
                result = self._generate_once(
                    prompt=prompt,
                    schema=schema,
                    response_model=response_model,
                    images=pil_images,
                    max_output_tokens=token_budget,
                )
                return result
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "gemini structured attempt %s/%s failed: %s",
                    attempt,
                    attempts,
                    exc,
                )
                if "MAX_TOKENS" in str(exc).upper():
                    token_budget = min(token_budget * 2, 32768)
                if attempt >= attempts:
                    break
        raise RuntimeError(
            f"Gemini structured generation failed after {attempts} attempts: {last_error}"
        ) from last_error

    def _generate_once(
        self,
        *,
        prompt: str,
        schema: dict,
        response_model: type[T],
        images: list,
        max_output_tokens: int | None = None,
    ) -> T:
        from google.genai import types

        assert self._client is not None
        parts: list = [types.Part.from_text(text=prompt)]
        for pil in images:
            parts.append(_pil_to_part(pil))

        if max_output_tokens is None:
            max_output_tokens = int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS", "16384"))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            response_mime_type="application/json",
            response_json_schema=schema,
            max_output_tokens=max_output_tokens,
        )

        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0

        text = getattr(response, "text", None) or ""
        if not str(text).strip():
            # Some SDK versions put JSON on candidates
            text = _extract_text_fallback(response)

        finish = _finish_reason(response)
        if finish and "MAX_TOKENS" in str(finish).upper():
            raise RuntimeError(
                f"Gemini truncated JSON (finish_reason={finish}). "
                "Raise GEMINI_MAX_OUTPUT_TOKENS and retry."
            )
        if not str(text).strip():
            raise RuntimeError("Gemini returned an empty response")

        logger.info(
            "gemini model=%s latency_ms=%.0f chars=%d",
            self.model,
            self.last_latency_ms,
            len(text),
        )
        return _validate_structured_json(response_model, text)


def _pil_to_part(image):
    from google.genai import types

    buf = BytesIO()
    image.save(buf, format="PNG")
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")


def _as_pil_rgb(image):
    from PIL import Image

    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, (bytes, bytearray)):
        return Image.open(BytesIO(image)).convert("RGB")
    raise TypeError(f"Unsupported image type: {type(image)!r}")


def _finish_reason(response) -> str:
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        return str(getattr(candidates[0], "finish_reason", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def _extract_text_fallback(response) -> str:
    try:
        candidates = getattr(response, "candidates", None) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", None) or []
            chunks = []
            for part in parts:
                t = getattr(part, "text", None)
                if t:
                    chunks.append(t)
            if chunks:
                return "".join(chunks)
    except Exception:  # noqa: BLE001
        pass
    return ""


__all__ = ["GeminiClient"]

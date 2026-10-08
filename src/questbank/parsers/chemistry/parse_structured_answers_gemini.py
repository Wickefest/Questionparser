"""Gemini extraction of structured (Paper 2) mark-scheme answers by part."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import pymupdf
from pydantic import BaseModel, Field

from questbank.llm.gemini_client import GeminiClient
from questbank.pdf.bbox_crop import render_page, save_visual_crop, validate_and_normalize_bbox
from questbank.parsers.chemistry.parse_structured_questions_gemini import (
    normalize_structured_part_label,
)
from questbank.types.canonical import StructuredAnswerPart, StructuredAnswerRecord
from typing import Literal

logger = logging.getLogger(__name__)

STRUCTURED_ANSWER_INSTRUCTION = """\
You extract official structured / Paper-2 mark-scheme answers for Chemistry from a PAGE IMAGE.
These schemes are often scans. The image is AUTHORITATIVE.

For each question on the page:
- question_ref: main question number ("1", "2") as printed (Q1 → "1")
- parts: one row per graded part, in print order:
  - label: composite "a(i)", "a(ii)", "b(i)" (letter + roman as printed)
  - marks_max: TOTAL marks for that part (SUM every 1 in the right-hand marks
    column for that row — e.g. two "1"s → 2)
  - accepted_answers: printed wording / working lines
  - notes: examiner notes / ECF / "if polymer -1m" etc.
  - ecf: true only if ECF is printed
  - visuals: crop EVERY drawn structure / displayed formula in that row
      bbox = [ymin, xmin, ymax, xmax] on 0–1000 of THIS page
      role = "structure"
    If the answer is a chemical structure drawing, you MUST crop it.
    Also put a short text description in accepted_answers (e.g. "F2C=CH2").

Rules:
- Do NOT invent chemistry. Only extract what is printed.
- Ignore headers, footers, cover, MCQ letter keys.
- Empty page → empty answers list.

Return data matching the supplied schema only.
"""


class GeminiAnswerVisualOut(BaseModel):
    bbox: list[float]
    role: Literal["diagram", "structure", "other"] = "structure"


class GeminiStructuredAnswerPartOut(BaseModel):
    label: str
    marks_max: int | None = None
    accepted_answers: list[str] = Field(default_factory=list)
    notes: str | None = None
    ecf: bool = False
    visuals: list[GeminiAnswerVisualOut] = Field(default_factory=list)


class GeminiStructuredAnswerRecordOut(BaseModel):
    question_ref: str
    parts: list[GeminiStructuredAnswerPartOut] = Field(default_factory=list)


class GeminiStructuredAnswerPage(BaseModel):
    answers: list[GeminiStructuredAnswerRecordOut] = Field(default_factory=list)


def parse_structured_answers_gemini(
    answer_pdf: str | Path,
    *,
    client: GeminiClient | None = None,
    dpi: int = 150,
    assets_dir: str | Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> list[StructuredAnswerRecord]:
    """Parse structured mark scheme pages via Gemini; merge by question_ref."""
    answer_pdf = Path(answer_pdf)
    active = client or GeminiClient()
    out_assets = Path(assets_dir) if assets_dir else None
    if out_assets is not None:
        out_assets.mkdir(parents=True, exist_ok=True)
    by_ref: dict[str, StructuredAnswerRecord] = {}

    doc = pymupdf.open(answer_pdf)
    try:
        total = doc.page_count
        for page_index in range(total):
            if on_progress:
                on_progress(f"Parsing structured answers page {page_index + 1}/{total}")
            page_text = doc[page_index].get_text("text") or ""
            if _looks_like_mcq_key_only(page_text):
                continue
            rendered = render_page(doc, page_index, dpi=dpi)
            prompt = STRUCTURED_ANSWER_INSTRUCTION
            if page_text.strip():
                prompt = (
                    f"{STRUCTURED_ANSWER_INSTRUCTION}\n\n"
                    f"Native page text (may be incomplete):\n{page_text[:6000]}"
                )
            try:
                result = active.generate_structured(
                    prompt=prompt,
                    response_model=GeminiStructuredAnswerPage,
                    images=[rendered.image],
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Structured answer parse failed on page %s/%s after retries: %s",
                    page_index + 1, total, exc,
                )
                continue
            page_num = page_index + 1
            for ans in result.answers or []:
                ref = str(ans.question_ref).strip().lstrip("0") or str(ans.question_ref)
                normalized_parts: list[StructuredAnswerPart] = []
                for p in ans.parts or []:
                    label = normalize_structured_part_label(p.label)
                    if not label:
                        continue
                    visuals: list[dict] = []
                    if out_assets is not None:
                        for vi, visual in enumerate(p.visuals or []):
                            crop = _crop_answer_visual(
                                rendered,
                                visual.bbox,
                                out_assets / f"ms-q{ref}-{label.replace('(', '').replace(')', '')}-{page_num}-{vi + 1}.png",
                            )
                            if crop:
                                visuals.append({"asset": crop, "role": visual.role})
                    normalized_parts.append(
                        StructuredAnswerPart(
                            label=label,
                            marks_max=p.marks_max,
                            accepted_answers=list(p.accepted_answers or []),
                            notes=p.notes,
                            ecf=bool(p.ecf),
                            visuals=visuals,
                        )
                    )
                existing = by_ref.get(ref)
                if existing is None:
                    by_ref[ref] = StructuredAnswerRecord(
                        question_ref=ref,
                        parts=normalized_parts,
                    )
                else:
                    existing.parts.extend(normalized_parts)
    finally:
        doc.close()

    # Dedupe part labels per question (keep first row that has accepted answers).
    for ans in by_ref.values():
        ordered: list[StructuredAnswerPart] = []
        index: dict[str, int] = {}
        for part in ans.parts:
            key = part.label
            if key not in index:
                index[key] = len(ordered)
                ordered.append(part)
                continue
            prev = ordered[index[key]]
            if part.accepted_answers and not prev.accepted_answers:
                ordered[index[key]] = part
                prev = ordered[index[key]]
            if part.visuals and not prev.visuals:
                prev.visuals = list(part.visuals)
            if part.marks_max and (prev.marks_max is None or part.marks_max > prev.marks_max):
                prev.marks_max = part.marks_max
        ans.parts = ordered

    return [by_ref[k] for k in sorted(by_ref.keys(), key=_ref_sort)]


def _crop_answer_visual(rendered, bbox, dest: Path) -> str | None:
    try:
        norm, _review = validate_and_normalize_bbox(bbox, question_ref="ms")
        if norm is None:
            return None
        saved, _mime = save_visual_crop(rendered.image, norm, dest=dest)
        return Path(saved).name
    except Exception:  # noqa: BLE001
        return None


def _looks_like_mcq_key_only(text: str) -> bool:
    if not text.strip():
        return False
    letters = len(re.findall(r"(?i)\b\d{1,2}\s*[.:)\-]?\s*[ABCD]\b", text))
    parts = len(re.findall(r"\(\s*[a-z]\s*\)", text, flags=re.I))
    return letters >= 6 and parts <= 1


def _ref_sort(ref: str) -> tuple:
    text = str(ref).strip()
    if text.isdigit():
        return (0, int(text))
    return (1, text)


__all__ = ["parse_structured_answers_gemini"]

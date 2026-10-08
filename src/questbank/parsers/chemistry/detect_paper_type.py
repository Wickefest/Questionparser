"""Detect whether a paper PDF is MCQ or structured (written)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

PaperKind = Literal["mcq", "structured"]

_STRUCTURED_NAME = re.compile(
    r"(?i)\b(structured|paper\s*2|section\s*b|written|p\d+_question|topical-p\d+)\b"
)
_MCQ_NAME = re.compile(r"(?i)\b(mcq|multiple\s*choice|paper\s*1|6092/01)\b")
_PART_MARK = re.compile(r"\(\s*[a-z]\s*\)|\(\s*[ivx]+\s*\)", re.I)
_OPTION_MARK = re.compile(r"(?m)^\s*[A-D][\.\)\:]\s+\S")
_MCQ_KEY = re.compile(r"(?i)\b\d{1,2}\s*[.:)\-]?\s*[ABCD]\b")


def detect_paper_type(
    question_pdf: str | Path,
    answer_pdf: str | Path | None = None,
    *,
    title: str | None = None,
    paper_code: str | None = None,
) -> PaperKind:
    """Heuristic MCQ vs structured detection from names + sample page text."""
    q_path = Path(question_pdf)
    a_path = Path(answer_pdf) if answer_pdf else None
    name_blob = " ".join(
        filter(
            None,
            [
                title,
                paper_code,
                q_path.name,
                a_path.name if a_path else "",
            ],
        )
    )
    if _STRUCTURED_NAME.search(name_blob) and not _MCQ_NAME.search(name_blob):
        return "structured"
    if _MCQ_NAME.search(name_blob) and not _STRUCTURED_NAME.search(name_blob):
        return "mcq"

    q_text = _sample_pdf_text(q_path)
    a_text = _sample_pdf_text(a_path) if a_path and a_path.is_file() else ""

    part_hits = len(_PART_MARK.findall(q_text))
    option_hits = len(_OPTION_MARK.findall(q_text))
    key_hits = len(_MCQ_KEY.findall(a_text))

    # Strong structured signal: many (a)/(i) parts, few A–D option lines
    if part_hits >= 4 and part_hits > option_hits * 2:
        return "structured"
    # Strong MCQ signal: option lines or letter key table
    if option_hits >= 8 or key_hits >= 8:
        return "mcq"
    if part_hits >= 2 and option_hits <= 2:
        return "structured"
    if option_hits >= 4:
        return "mcq"
    # Filename fallback already checked; default MCQ for legacy factory behaviour
    if _STRUCTURED_NAME.search(name_blob):
        return "structured"
    return "mcq"


def _sample_pdf_text(path: Path, *, max_pages: int = 4) -> str:
    try:
        import pymupdf
    except ImportError:
        return ""
    if not path.is_file():
        return ""
    chunks: list[str] = []
    try:
        doc = pymupdf.open(path)
        try:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                chunks.append(page.get_text("text") or "")
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return ""
    return "\n".join(chunks)


__all__ = ["PaperKind", "detect_paper_type"]

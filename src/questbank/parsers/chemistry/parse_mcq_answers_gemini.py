"""Answer / mark-scheme PDF parser via PyMuPDF page render + Gemini."""

from __future__ import annotations

import logging
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from questbank.llm.gemini_answer import parse_answer_page
from questbank.llm.gemini_client import GeminiClient
from questbank.pdf.bbox_crop import render_page
from questbank.types.canonical import ParsedAnswer
from questbank.types.ingest import normalize_question_ref

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 150


def parse_mcq_answers_gemini(
    pdf_path: str | Path,
    *,
    client: GeminiClient | None = None,
    backend: str = "pymupdf",
    ocr: bool | None = None,
    dpi: int = _DEFAULT_DPI,
    max_pages: int | None = None,
    pages: list[int] | None = None,
    check_gemini: bool = True,
    use_layout_context: bool = False,
    on_page: Callable[[int, int], None] | None = None,
) -> list[ParsedAnswer]:
    """Parse an answer/mark-scheme PDF into canonical ParsedAnswer records.

    Default: PyMuPDF page image (+ native page text) → Gemini.
    Layout/Docling context is off unless ``use_layout_context=True``.
    """
    try:
        import pymupdf
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF and Pillow are required") from exc

    pdf_path = Path(pdf_path)
    llm = client or GeminiClient()
    if check_gemini:
        llm.ensure_available()

    layout = None
    if use_layout_context:
        from questbank.parsers.chemistry.parse_mcq_gemini import page_docling_context
        from questbank.pdf.extract_pdf_layout import extract_pdf_layout

        layout = extract_pdf_layout(pdf_path, backend=backend, ocr=ocr)  # type: ignore[arg-type]
    else:
        page_docling_context = None  # type: ignore[assignment]

    by_ref: dict[str, ParsedAnswer] = {}
    failed_pages: list[int] = []
    page_filter = set(pages) if pages else None

    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        for page_index in range(page_count):
            page = doc[page_index]
            page_number = page_index + 1
            if page_filter is not None and page_number not in page_filter:
                continue
            if on_page is not None:
                try:
                    on_page(page_number, page_count)
                except Exception:  # noqa: BLE001
                    logger.debug("answer on_page callback failed", exc_info=True)
            page_text = page.get_text("text") or ""
            rendered_page = render_page(doc, page_index, dpi=dpi)
            page_image = rendered_page.image
            context = (
                page_docling_context(layout, page_number)  # type: ignore[misc]
                if use_layout_context and layout is not None
                else None
            )

            try:
                result = parse_answer_page(
                    llm,
                    page_image=page_image,
                    page_number=page_number,
                    docling_context=context,
                    page_text=page_text,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Gemini answer parse failed on page %s after retries: %s",
                    page_number, exc,
                )
                failed_pages.append(page_number)
                continue

            for entry in result.answers:
                ref = _canonical_ref(entry.question_ref)
                # parse_answer_page already sanitized provenance.
                parsed = ParsedAnswer(
                    question_ref=ref,
                    answer=entry.answer,
                    explanation=entry.explanation,
                    explanation_source=entry.explanation_source,
                )
                existing = by_ref.get(ref)
                if existing is None:
                    by_ref[ref] = parsed
                    continue
                # Prefer entry that carries an official explanation.
                if existing.explanation is None and parsed.explanation:
                    by_ref[ref] = parsed

    if failed_pages:
        raise RuntimeError(
            f"Answer/mark-scheme parsing failed on page(s) {failed_pages} after retries "
            f"({len(by_ref)} answer(s) parsed from other pages). Re-run ingestion to retry "
            "these pages rather than persisting an incomplete answer set."
        )

    return sorted(by_ref.values(), key=_answer_sort_key)


def _canonical_ref(value: str) -> str:
    # normalize_question_ref returns "Q4"; strip Q for canonical "4"
    normalized = normalize_question_ref(value)
    if normalized.startswith("Q"):
        return normalized[1:]
    return normalized


def _answer_sort_key(a: ParsedAnswer) -> tuple:
    if a.question_ref.isdigit():
        return (0, int(a.question_ref))
    return (1, a.question_ref)


__all__ = ["parse_mcq_answers_gemini"]

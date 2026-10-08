"""MCQ question paper parser: PyMuPDF page render → Gemini → canonical questions."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

import os
from PIL import Image
from questbank.llm.gemini_client import GeminiClient
from questbank.llm.gemini_question import parse_page_questions, verify_crop_with_gemini
from questbank.pdf.bbox_crop import (
    normalize_norm_bbox,
    save_visual_crop,
    validate_and_normalize_bbox,
    save_debug_artifacts,
    expand_norm_bbox,
    norm_bbox_to_pixels,
    RenderedPage,
    render_page,
)
from questbank.types.canonical import (
    CompositeVisualOptions,
    GeminiPageQuestion,
    ImageBlock,
    MixedOptions,
    ParsedQuestion,
    SourceRegion,
    TextBlock,
)
from questbank.types.layout import PaperLayout

logger = logging.getLogger(__name__)

_DEFAULT_DPI = 150
_QNUM_RE = re.compile(r"^\d{1,2}$")


def _is_output_limit_error(exc: BaseException) -> bool:
    text = str(exc).upper()
    return "MAX_TOKENS" in text or "TRUNCATED JSON" in text


def _question_starts_on_page(page) -> list[tuple[str, float]]:
    """Return (question_ref, y0_page_points) for left-margin question numbers."""
    width = float(page.rect.width)
    left_max_env = os.environ.get("QUESTION_NUM_LEFT_MARGIN_PTS")
    if left_max_env is not None:
        try:
            left_max = float(left_max_env)
        except ValueError:
            left_max = max(90.0, width * 0.18)
    else:
        left_max = max(90.0, width * 0.18)

    found: dict[int, float] = {}
    try:
        words = page.get_text("words") or []
    except Exception:  # noqa: BLE001
        return []
    for w in words:
        try:
            x0, y0 = float(w[0]), float(w[1])
            token = str(w[4]).strip()
        except Exception:  # noqa: BLE001
            continue
        if x0 > left_max or not _QNUM_RE.fullmatch(token):
            continue
        num = int(token)
        if not 1 <= num <= 80:
            continue
        prev = found.get(num)
        if prev is None or y0 < prev:
            found[num] = y0
    return [(str(n), found[n]) for n in sorted(found)]


def _band_crops_from_page(
    page_image,
    page,
    *,
    dpi: int,
    pad_pts: float = 8.0,
) -> list[tuple[str, Any, tuple[int, int]]]:
    """Split a rendered page into vertical crops around each question number."""
    starts = _question_starts_on_page(page)
    if len(starts) < 2:
        w, h = page_image.size
        mid = h // 2
        return [
            ("top-half", page_image.crop((0, 0, w, min(h, mid + 40))), (0, min(h, mid + 40))),
            ("bottom-half", page_image.crop((0, max(0, mid - 30), w, h)), (max(0, mid - 30), h)),
        ]

    page_h = float(page.rect.height)
    scale = float(dpi) / 72.0
    img_h = page_image.size[1]
    bands: list[tuple[str, Any, tuple[int, int]]] = []
    for idx, (ref, y0) in enumerate(starts):
        y1 = starts[idx + 1][1] if idx + 1 < len(starts) else page_h
        top = max(0, int((y0 - pad_pts) * scale))
        bottom = min(img_h, int((y1 + pad_pts) * scale))
        if bottom - top < 40:
            continue
        bands.append((ref, page_image.crop((0, top, page_image.size[0], bottom)), (top, bottom)))
    return bands


def _parse_page_by_bands(
    llm: GeminiClient,
    *,
    page,
    page_image,
    page_number: int,
    dpi: int,
) -> list[tuple[GeminiPageQuestion, Any, tuple[int, int]]]:
    """Parse each question band separately after a full-page truncation.

    Returns (question, band_image, y_range) so crops use band-relative bboxes.
    """
    bands = _band_crops_from_page(page_image, page, dpi=dpi)
    collected: list[tuple[GeminiPageQuestion, Any, tuple[int, int]]] = []
    for hint, band_image, y_range in bands:
        items = _parse_one_band(
            llm,
            band_image=band_image,
            page_number=page_number,
            hint=hint,
            y_range=y_range,
        )
        collected.extend(items)
    return collected


def _parse_one_band(
    llm: GeminiClient,
    *,
    band_image,
    page_number: int,
    hint: str,
    y_range: tuple[int, int],
) -> list[tuple[GeminiPageQuestion, Any, tuple[int, int]]]:
    try:
        result = parse_page_questions(
            llm,
            page_image=band_image,
            page_number=page_number,
            band_hint=hint,
            max_attempts=2,
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_output_limit_error(exc):
            logger.warning(
                "Band parse failed page=%s hint=%s: %s",
                page_number,
                hint,
                exc,
            )
            return []
        # Dense single question (multi-graph): split the band in half.
        logger.warning(
            "Band still truncated page=%s hint=%s; splitting band in half",
            page_number,
            hint,
        )
        return _parse_band_halves(
            llm,
            band_image=band_image,
            page_number=page_number,
            hint=hint,
            y_range=y_range,
        )

    out: list[tuple[GeminiPageQuestion, Any, tuple[int, int]]] = []
    for item in result.questions:
        if hint.isdigit():
            ref = _normalize_ref(item.question_ref)
            if not ref.isdigit():
                item.question_ref = hint
        out.append((item, band_image, y_range))
    return out


def _parse_band_halves(
    llm: GeminiClient,
    *,
    band_image,
    page_number: int,
    hint: str,
    y_range: tuple[int, int],
) -> list[tuple[GeminiPageQuestion, Any, tuple[int, int]]]:
    w, h = band_image.size
    mid = h // 2
    top, bottom = y_range
    halves = [
        (f"{hint}-top", band_image.crop((0, 0, w, min(h, mid + 30))), (top, top + min(h, mid + 30))),
        (f"{hint}-bottom", band_image.crop((0, max(0, mid - 30), w, h)), (top + max(0, mid - 30), bottom)),
    ]
    collected: list[tuple[GeminiPageQuestion, Any, tuple[int, int]]] = []
    for half_hint, half_img, half_y_range in halves:
        try:
            result = parse_page_questions(
                llm,
                page_image=half_img,
                page_number=page_number,
                band_hint=hint if hint.isdigit() else half_hint,
                max_attempts=2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Half-band parse failed page=%s hint=%s: %s",
                page_number,
                half_hint,
                exc,
            )
            continue
        for item in result.questions:
            if hint.isdigit():
                item.question_ref = hint
            collected.append((item, half_img, half_y_range))
    return collected


def page_docling_context(layout: PaperLayout, page_number: int) -> dict[str, Any]:
    """Serialize layout evidence for one page (optional / legacy)."""
    page = next((p for p in layout.pages if p.page_number == page_number), None)
    if page is None:
        return {"page": page_number, "lines": [], "tables": [], "images": []}
    lines = [
        {
            "text": line.text,
            "bbox": [line.bbox.y0, line.bbox.x0, line.bbox.y1, line.bbox.x1],
            "reading_order": line.reading_order,
        }
        for line in page.lines
    ]
    tables = [
        {
            "headers": table.headers,
            "rows": table.rows,
            "bbox": [table.bbox.y0, table.bbox.x0, table.bbox.y1, table.bbox.x1],
        }
        for table in page.tables
    ]
    images = [
        {
            "bbox": [img.bbox.y0, img.bbox.x0, img.bbox.y1, img.bbox.x1],
            "block_index": img.block_index,
        }
        for img in page.images
    ]
    return {
        "page": page_number,
        "width": page.width,
        "height": page.height,
        "raw_text": page.raw_text,
        "lines": lines,
        "tables": tables,
        "images": images,
        "image_bbox_warning": (
            "Layout/PDF image bboxes are often TRUNCATED for graphs. "
            "Compare them to the rendered page image and EXPAND stem_visuals "
            "so axis titles (e.g. volume, time), tick labels, units, legends, "
            "and all plotted data are fully inside the crop."
        ),
    }


def parse_mcq_questions_gemini(
    pdf_path: str | Path,
    *,
    assets_dir: str | Path,
    client: GeminiClient | None = None,
    layout: PaperLayout | None = None,
    backend: str = "auto",
    ocr: bool | None = None,
    dpi: int = _DEFAULT_DPI,
    max_pages: int | None = None,
    pages: list[int] | None = None,
    check_gemini: bool = True,
    use_layout_context: bool = True,
    on_page: Callable[[int, int], None] | None = None,
    on_questions: Callable[[list[ParsedQuestion], int, int], None] | None = None,
    delay_seconds: float = 3.0,
) -> list[ParsedQuestion]:
    """Parse an MCQ question paper into canonical ParsedQuestion records.

    Default path: Docling/layout extract → PyMuPDF page render → Gemini compares
    layout vs image, then we crop visuals from the page render.
    Set ``use_layout_context=False`` for image-only Gemini.
    """
    try:
        import pymupdf
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF and Pillow are required") from exc

    pdf_path = Path(pdf_path)
    out_dir = Path(assets_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    llm = client or GeminiClient()
    if check_gemini:
        llm.ensure_available()

    paper_layout = layout
    if use_layout_context and paper_layout is None:
        from questbank.pdf.extract_pdf_layout import extract_pdf_layout

        paper_layout = extract_pdf_layout(pdf_path, backend=backend, ocr=ocr)  # type: ignore[arg-type]

    by_ref: dict[str, ParsedQuestion] = {}
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
                    logger.debug("on_page callback failed", exc_info=True)
            rendered_page = render_page(doc, page_index, dpi=dpi)
            page_image = rendered_page.image
            context = (
                page_docling_context(paper_layout, page_number)
                if use_layout_context and paper_layout is not None
                else None
            )

            materialized: list[tuple[GeminiPageQuestion, Any, Any, Any]] = []
            # Each entry: (item, crop_image, pdf_page_or_None, band_y_range_or_None)
            try:
                result = parse_page_questions(
                    llm,
                    page_image=page_image,
                    page_number=page_number,
                    docling_context=context,
                    max_attempts=2,
                )
                materialized = [(item, page_image, page, None) for item in result.questions]
            except Exception as exc:  # noqa: BLE001
                # Never hard-fail a page: retry via per-question bands for ANY
                # structured-output failure (schema slips, truncation, etc.).
                logger.warning(
                    "Full-page Gemini failed on page %s (%s); trying per-question bands",
                    page_number,
                    exc,
                )
                try:
                    band_items = _parse_page_by_bands(
                        llm,
                        page=page,
                        page_image=page_image,
                        page_number=page_number,
                        dpi=dpi,
                    )
                    materialized = [(item, band_img, page, y_range) for item, band_img, y_range in band_items]
                except Exception as band_exc:  # noqa: BLE001
                    logger.exception(
                        "Band fallback also failed on page %s: %s",
                        page_number,
                        band_exc,
                    )
                    materialized = []

                if not materialized:
                    ref = f"page-{page_number}-failed"
                    by_ref[ref] = ParsedQuestion(
                        question_ref=ref,
                        content=[TextBlock(value=f"[parse failed on page {page_number}: {exc}]")],
                        options=CompositeVisualOptions(asset="", labels=["A", "B", "C", "D"]),
                        source_page=page_number,
                        source_regions=[SourceRegion(page=page_number)],
                        requires_review=True,
                    )
                    if on_questions is not None:
                        try:
                            on_questions(
                                sorted(by_ref.values(), key=_ref_sort_key),
                                page_number,
                                page_count,
                            )
                        except Exception:  # noqa: BLE001
                            logger.debug("on_questions callback failed", exc_info=True)
                    continue

            for item, crop_image, crop_page, band_y_range in materialized:
                question = _materialize_question(
                    item,
                    page_number=page_number,
                    page_image=crop_image,
                    assets_dir=out_dir,
                    page=crop_page,
                    doc=doc if crop_page is not None else None,
                    client=llm,
                    band_y_range=band_y_range,
                )
                _merge_into(by_ref, question)

            if on_questions is not None:
                try:
                    snapshot = sorted(by_ref.values(), key=_ref_sort_key)
                    on_questions(snapshot, page_number, page_count)
                except Exception:  # noqa: BLE001
                    logger.debug("on_questions callback failed", exc_info=True)

            if delay_seconds > 0 and page_index < page_count - 1:
                import time
                logger.info("Pausing for %s seconds before processing next page...", delay_seconds)
                time.sleep(delay_seconds)

    return sorted(by_ref.values(), key=_ref_sort_key)


def _norm_bbox_iou(box1: list[float], box2: list[float]) -> float:
    y0_1, x0_1, y1_1, x1_1 = box1
    y0_2, x0_2, y1_2, x1_2 = box2
    
    # intersection
    ix0 = max(x0_1, x0_2)
    iy0 = max(y0_1, y0_2)
    ix1 = min(x1_1, x1_2)
    iy1 = min(y1_1, y1_2)
    
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
        
    area1 = (x1_1 - x0_1) * (y1_1 - y0_1)
    area2 = (x1_2 - x0_2) * (y1_2 - y0_2)
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


def _crop_with_verification(
    *,
    client: GeminiClient | None,
    verify_flag: bool,
    page_image,
    bbox: list[float],
    dest: Path,
    page=None,
    doc=None,
    band_y_range: tuple[int, int] | None = None,
    ref: str,
    asset_label: str,
    max_rounds: int = 3,
) -> tuple[list[float], bool, str]:
    """Crop a visual, then iteratively re-verify/correct it with Gemini.

    Never widens toward a "safe" full-question fallback: if verification
    keeps failing, the last attempted (tightest available) bbox is kept and
    the question is flagged requires_review=True for manual correction.
    Returns (final_bbox, requires_review, crop_source).
    """
    requires_review = False
    current_bbox = bbox
    _path, crop_source = save_visual_crop(
        page_image, current_bbox, dest=dest, page=page, doc=doc, band_y_range=band_y_range
    )
    if not (verify_flag and client is not None):
        return current_bbox, requires_review, crop_source

    for round_idx in range(max_rounds):
        crop_img = Image.open(dest)
        verification = verify_crop_with_gemini(
            client,
            original_image=page_image,
            crop_image=crop_img,
            question_context=f"Q{ref} {asset_label}",
        )
        if verification.valid:
            return current_bbox, requires_review, crop_source

        logger.warning(
            "Crop verification failed for Q%s %s (round %s/%s): %s",
            ref, asset_label, round_idx + 1, max_rounds, verification.issue,
        )
        if not verification.corrected_bbox:
            requires_review = True
            break

        corrected = [
            verification.corrected_bbox.ymin,
            verification.corrected_bbox.xmin,
            verification.corrected_bbox.ymax,
            verification.corrected_bbox.xmax,
        ]
        valid_corrected_bbox, corrected_requires_review = validate_and_normalize_bbox(corrected, ref)
        if not valid_corrected_bbox:
            requires_review = True
            break

        current_bbox = expand_norm_bbox(valid_corrected_bbox, frac=0.03, min_pad=15.0)
        if corrected_requires_review:
            requires_review = True
        logger.info(
            "Retrying Q%s %s crop (round %s/%s) with corrected bbox: %s",
            ref, asset_label, round_idx + 1, max_rounds, current_bbox,
        )
        _path, crop_source = save_visual_crop(
            page_image, current_bbox, dest=dest, page=page, doc=doc, band_y_range=band_y_range
        )
    else:
        # Exhausted all rounds without a valid=True verification.
        requires_review = True

    return current_bbox, requires_review, crop_source


def _materialize_question(
    item: GeminiPageQuestion,
    *,
    page_number: int,
    page_image,
    assets_dir: Path,
    page=None,
    doc=None,
    client: GeminiClient | None = None,
    band_y_range: tuple[int, int] | None = None,
) -> ParsedQuestion:
    ref = _normalize_ref(item.question_ref)
    content = list(item.content)
    options = deepcopy(item.options)
    
    # Vision Parser Plain Text Spec: Strip all question numbers, sub-parts, and option prefixes completely
    import re

    def strip_all_labels(text: str, ref_val: str | None = None, label_val: str | None = None) -> str:
        if not isinstance(text, str):
            return text
        
        # 1. Strip question references (e.g. "1.", "Q1:", "Question 1 -")
        if ref_val:
            ref_patterns = [
                rf"^\s*(?:Question|Q)?\s*{re.escape(ref_val)}\b[\s\.\-:]+\s*",
                rf"^\s*\({re.escape(ref_val)}\)[\s\.\-:]*\s*"
            ]
            for pat in ref_patterns:
                text = re.sub(pat, "", text, flags=re.IGNORECASE)
                
        # 2. Strip option prefixes (e.g. "A.", "(A)", "A -")
        if label_val:
            label_patterns = [
                rf"^\s*\({re.escape(label_val)}\)[\s\.\-:]*\s*",
                rf"^\s*\b{re.escape(label_val)}\b[\s\.\-:]+\s*"
            ]
            for pat in label_patterns:
                text = re.sub(pat, "", text, flags=re.IGNORECASE)
                
        # 3. Strip general sub-part labels (e.g. "(a)", "(b)", "(i)", "(ii)", "a.", "b.", "i.", "ii.")
        subpart_patterns = [
            r"^\s*\([a-z0-9]+\)[\s\.\-:]*\s*", # (a), (b), (i), (ii)
            r"^\s*\b[a-z0-9]+[\.\-:]\s+"        # a., b., i.
        ]
        for pat in subpart_patterns:
            text = re.sub(pat, "", text, flags=re.IGNORECASE)
            
        return text.strip()

    cleaned_content = []
    for block in content:
        b_type = getattr(block, "type", None)
        if b_type == "text":
            orig_val = getattr(block, "value", "") or ""
            block.value = strip_all_labels(orig_val, ref_val=ref)
        elif b_type == "list":
            orig_items = getattr(block, "items", []) or []
            block.items = [strip_all_labels(item, ref_val=ref) for item in orig_items]
        elif b_type == "table":
            orig_cols = getattr(block, "columns", []) or []
            block.columns = [strip_all_labels(col, ref_val=ref) for col in orig_cols]
            orig_rows = getattr(block, "rows", []) or []
            cleaned_rows = []
            for row in orig_rows:
                cleaned_rows.append([strip_all_labels(cell, ref_val=ref) for cell in row])
            block.rows = cleaned_rows
        cleaned_content.append(block)
    content = cleaned_content

    from questbank.normalize.inline_blocks import merge_inline_notation_blocks
    from questbank.normalize.list_blocks import split_numbered_statement_lists

    content = merge_inline_notation_blocks(content)
    content = split_numbered_statement_lists(content)

    from questbank.types.canonical import TextOptions, MathOptions, TableOptions, MixedOptions
    if isinstance(options, TextOptions):
        for opt_item in options.items:
            # Self-healing if Gemini confuses "text" mode with "math/latex" fields
            if getattr(opt_item, "content", None) is None:
                opt_item.content = getattr(opt_item, "latex", None) or ""
            opt_item.content = strip_all_labels(opt_item.content, ref_val=ref, label_val=opt_item.label)
    elif isinstance(options, MathOptions):
        for opt_item in options.items:
            # Self-healing if Gemini confuses "math" mode with "text/content" fields
            if getattr(opt_item, "latex", None) is None:
                opt_item.latex = getattr(opt_item, "content", None) or ""
            opt_item.latex = strip_all_labels(opt_item.latex, ref_val=ref, label_val=opt_item.label)
    elif isinstance(options, TableOptions):
        options.columns = [strip_all_labels(col, ref_val=ref) for col in options.columns]
        for opt_row in options.rows:
            opt_row.cells = [strip_all_labels(cell, ref_val=ref, label_val=opt_row.label) for cell in opt_row.cells]
    elif isinstance(options, MixedOptions):
        for opt_item in options.items:
            if opt_item.content is not None:
                opt_item.content = strip_all_labels(opt_item.content, ref_val=ref, label_val=opt_item.label)
            if opt_item.latex is not None:
                opt_item.latex = strip_all_labels(opt_item.latex, ref_val=ref, label_val=opt_item.label)

    slug = _asset_slug(ref)
    requires_review = bool(item.requires_review)
    verify_flag = os.environ.get("VERIFY_VISUAL_CROPS", "true").lower() == "true"

    # Deduplicate stem visuals that significantly overlap with composite options
    bbox_options = None
    from questbank.types.canonical import CompositeVisualOptions, MixedOptions
    if isinstance(options, (CompositeVisualOptions, MixedOptions)):
        bbox_raw = options.bbox or item.options_bbox
        if bbox_raw:
            bbox_options = normalize_norm_bbox(bbox_raw)

    filtered_stem_visuals = []
    for visual in item.stem_visuals:
        vis_box = normalize_norm_bbox(visual.bbox)
        if vis_box and bbox_options:
            iou = _norm_bbox_iou(vis_box, bbox_options)
            if iou > 0.75:
                logger.info("Removing duplicate stem_visual for Q%s overlapping with options_bbox (IOU=%.2f)", ref, iou)
                # Strip any matching ImageBlock from content
                new_content = []
                for block in content:
                    if getattr(block, "type", None) == "image" and getattr(block, "bbox", None):
                        block_box = normalize_norm_bbox(block.bbox)
                        if block_box and _norm_bbox_iou(block_box, vis_box) > 0.75:
                            logger.info("Removing duplicate content ImageBlock for Q%s overlapping with duplicate stem_visual", ref)
                            continue
                    new_content.append(block)
                content = new_content
                continue
        filtered_stem_visuals.append(visual)

    # Crop meaningful stem visuals and rewrite image blocks / append new ones.
    stem_index = 0
    for visual in filtered_stem_visuals:
        stem_index += 1
        asset_name = f"{slug}-stem-{stem_index}.png"
        bbox, bbox_requires_review = validate_and_normalize_bbox(visual.bbox, ref)
        if bbox_requires_review:
            requires_review = True
        if bbox is None:
            logger.warning("stem bbox unusable for %s: %s", ref, visual.bbox)
            requires_review = True
            continue
        try:
            bbox, verify_requires_review, crop_source = _crop_with_verification(
                client=client,
                verify_flag=verify_flag,
                page_image=page_image,
                bbox=bbox,
                dest=assets_dir / asset_name,
                page=page,
                doc=doc,
                band_y_range=band_y_range,
                ref=ref,
                asset_label=f"Stem Visual {stem_index}",
            )
            if verify_requires_review:
                requires_review = True

            # Save debug artifacts
            width, height = page_image.size
            bbox_pixels = norm_bbox_to_pixels(expand_norm_bbox(bbox), width=width, height=height, padding=8)
            cropped_img = Image.open(assets_dir / asset_name)
            save_debug_artifacts(
                original_image=page_image,
                crop_image=cropped_img,
                bbox_normalized=bbox,
                bbox_pixels=bbox_pixels,
                assets_dir=assets_dir,
                question_ref=ref,
                asset_role=f"stem-{stem_index}",
                source_page=page_number,
                page_index=page_number - 1,
            )

            if crop_source == "page_crop" and visual.role in {"graph", "diagram"}:
                h_val = max(0.0, bbox[2] - bbox[0])
                w_val = max(0.0, bbox[3] - bbox[1])
                area = h_val * w_val
                # Only flag tiny / needle crops — wide diagram strips are fine.
                if area < 25_000 or min(w_val, h_val) < 40:
                    requires_review = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("stem crop failed for %s: %s", ref, exc)
            requires_review = True
            continue
        # Replace first image block with empty/placeholder asset, or append.
        replaced = False
        new_content = []
        for block in content:
            if not replaced and getattr(block, "type", None) == "image":
                new_content.append(ImageBlock(asset=asset_name, bbox=list(bbox)))
                replaced = True
            else:
                new_content.append(block)
        if not replaced:
            new_content.append(ImageBlock(asset=asset_name, bbox=list(bbox)))
        content = new_content

    # Fallback: content image blocks that still lack a real asset file.
    repaired_content = []
    for block in content:
        if getattr(block, "type", None) != "image":
            repaired_content.append(block)
            continue
        asset = getattr(block, "asset", "") or ""
        asset_path = assets_dir / Path(asset).name if asset else None
        if asset_path is not None and asset_path.exists():
            repaired_content.append(
                ImageBlock(asset=asset_path.name, bbox=normalize_norm_bbox(getattr(block, "bbox", None)))
            )
            continue
        bbox, bbox_requires_review = validate_and_normalize_bbox(getattr(block, "bbox", None), ref)
        if bbox_requires_review:
            requires_review = True
        if bbox is None:
            logger.warning("dropping image block without usable bbox for %s", ref)
            requires_review = True
            continue
        stem_index += 1
        asset_name = f"{slug}-stem-{stem_index}.png"
        try:
            bbox, verify_requires_review, _crop_source = _crop_with_verification(
                client=client,
                verify_flag=verify_flag,
                page_image=page_image,
                bbox=bbox,
                dest=assets_dir / asset_name,
                page=page,
                doc=doc,
                band_y_range=band_y_range,
                ref=ref,
                asset_label=f"Content Image {stem_index}",
            )
            if verify_requires_review:
                requires_review = True
            repaired_content.append(ImageBlock(asset=asset_name, bbox=list(bbox)))
        except Exception as exc:  # noqa: BLE001
            logger.warning("content image crop failed for %s: %s", ref, exc)
            requires_review = True
    content = repaired_content

    # Composite / mixed visual options: one A-D crop.
    if isinstance(options, CompositeVisualOptions):
        bbox_raw = options.bbox or item.options_bbox
        bbox, bbox_requires_review = validate_and_normalize_bbox(bbox_raw, ref)
        if bbox_requires_review:
            requires_review = True
        if not bbox:
            return ParsedQuestion(
                question_ref=ref,
                content=content,
                options=options,
                source_page=page_number,
                source_regions=[SourceRegion(page=page_number)],
                requires_review=True,
            )
        asset_name = f"{slug}-options.png"
        try:
            bbox, verify_requires_review, _crop_source = _crop_with_verification(
                client=client,
                verify_flag=verify_flag,
                page_image=page_image,
                bbox=bbox,
                dest=assets_dir / asset_name,
                page=page,
                doc=doc,
                band_y_range=band_y_range,
                ref=ref,
                asset_label="Composite Options",
            )
            if verify_requires_review:
                requires_review = True

            options = CompositeVisualOptions(
                labels=list(options.labels) or ["A", "B", "C", "D"],
                asset=asset_name,
                bbox=list(bbox),
            )

            # Save debug artifacts
            width, height = page_image.size
            bbox_pixels = norm_bbox_to_pixels(expand_norm_bbox(bbox), width=width, height=height, padding=8)
            cropped_img = Image.open(assets_dir / asset_name)
            save_debug_artifacts(
                original_image=page_image,
                crop_image=cropped_img,
                bbox_normalized=bbox,
                bbox_pixels=bbox_pixels,
                assets_dir=assets_dir,
                question_ref=ref,
                asset_role="options",
                source_page=page_number,
                page_index=page_number - 1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("options crop failed for %s: %s", ref, exc)
            return ParsedQuestion(
                question_ref=ref,
                content=content,
                options=options,
                source_page=page_number,
                source_regions=[SourceRegion(page=page_number)],
                requires_review=True,
            )

    if isinstance(options, MixedOptions) and (options.bbox or item.options_bbox):
        bbox_raw = options.bbox or item.options_bbox
        bbox, bbox_requires_review = validate_and_normalize_bbox(bbox_raw, ref)
        if bbox_requires_review:
            requires_review = True
        asset_name = f"{slug}-options.png"
        if bbox is None:
            requires_review = True
        else:
            try:
                bbox, verify_requires_review, _crop_source = _crop_with_verification(
                    client=client,
                    verify_flag=verify_flag,
                    page_image=page_image,
                    bbox=bbox,
                    dest=assets_dir / asset_name,
                    page=page,
                    doc=doc,
                    band_y_range=band_y_range,
                    ref=ref,
                    asset_label="Mixed Options",
                )
                if verify_requires_review:
                    requires_review = True

                options = MixedOptions(
                    items=options.items,
                    asset=asset_name,
                    bbox=list(bbox),
                )

                # Save debug artifacts
                width, height = page_image.size
                bbox_pixels = norm_bbox_to_pixels(expand_norm_bbox(bbox), width=width, height=height, padding=8)
                cropped_img = Image.open(assets_dir / asset_name)
                save_debug_artifacts(
                    original_image=page_image,
                    crop_image=cropped_img,
                    bbox_normalized=bbox,
                    bbox_pixels=bbox_pixels,
                    assets_dir=assets_dir,
                    question_ref=ref,
                    asset_role="options",
                    source_page=page_number,
                    page_index=page_number - 1,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("mixed options crop failed for %s: %s", ref, exc)
                requires_review = True

    # Strip unresolved image placeholders without assets.
    cleaned = []
    for block in content:
        if getattr(block, "type", None) == "image":
            asset = getattr(block, "asset", "") or ""
            if not asset or not (assets_dir / Path(asset).name).exists():
                requires_review = True
                continue
            cleaned.append(
                ImageBlock(
                    asset=Path(asset).name,
                    bbox=normalize_norm_bbox(getattr(block, "bbox", None)),
                )
            )
        else:
            cleaned.append(block)

    return ParsedQuestion(
        question_ref=ref,
        content=cleaned,
        options=options,
        source_page=page_number,
        source_regions=[SourceRegion(page=page_number)],
        requires_review=requires_review,
    )


def _merge_into(by_ref: dict[str, ParsedQuestion], question: ParsedQuestion) -> None:
    existing = by_ref.get(question.question_ref)
    if existing is None:
        by_ref[question.question_ref] = question
        return
    # Continuation onto another page: append content / regions.
    existing.content = list(existing.content) + list(question.content)
    existing.source_regions = list(existing.source_regions) + list(question.source_regions)
    if question.requires_review:
        existing.requires_review = True
    # Prefer later options only if existing options look empty/incomplete
    by_ref[question.question_ref] = existing


def _normalize_ref(value: str) -> str:
    text = str(value).strip()
    if text.upper().startswith("Q"):
        text = text[1:].lstrip()
    text = text.lstrip("0") or "0"
    if text.isdigit():
        return str(int(text))
    return text


def _asset_slug(ref: str) -> str:
    if ref.isdigit():
        return f"q{int(ref):02d}"
    safe = "".join(ch if ch.isalnum() else "-" for ch in ref.lower())
    return f"q{safe}"


def _ref_sort_key(q: ParsedQuestion) -> tuple:
    ref = q.question_ref
    if ref.isdigit():
        return (0, int(ref))
    return (1, ref)


__all__ = ["page_docling_context", "parse_mcq_questions_gemini"]

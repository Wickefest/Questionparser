"""Convert Gemini 0–1000 normalized boxes to pixel crops."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image
from questbank.types.canonical import NormBBox

logger = logging.getLogger(__name__)


@dataclass
class RenderedPage:
    """Encapsulates a rendered PDF page and its coordinate context."""

    page_index: int            # 0-based
    source_page_number: int    # 1-based
    image: Image.Image         # PIL Image
    width: int                 # Pixel width
    height: int                # Pixel height
    pdf_width: float           # PDF points width
    pdf_height: float          # PDF points height


def render_page(doc, page_index: int, dpi: int = 150) -> RenderedPage:
    """Render a single page image canonically with a clear abstraction."""
    from io import BytesIO
    page = doc[page_index]
    rect = page.rect
    pix = page.get_pixmap(dpi=dpi)
    image = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    return RenderedPage(
        page_index=page_index,
        source_page_number=page_index + 1,
        image=image,
        width=image.width,
        height=image.height,
        pdf_width=float(rect.width),
        pdf_height=float(rect.height),
    )


def validate_and_normalize_bbox(
    bbox: Any,
    question_ref: str,
) -> tuple[list[float] | None, bool]:
    """Validate a 0-1000 normalized bbox.

    Returns (normalized_bbox, requires_review).
    Clamps minor overshoots (e.g. -15 to 1015).
    For major violations or degenerate boxes, returns (None, True).
    """
    if bbox is None:
        return None, True

    if isinstance(bbox, NormBBox):
        values = bbox.as_list()
    elif isinstance(bbox, dict):
        keys = ("ymin", "xmin", "ymax", "xmax")
        if all(k in bbox for k in keys):
            values = [bbox[k] for k in keys]
        else:
            logger.warning("bbox dict missing keys for Q%s: %s", question_ref, bbox)
            return None, True
    else:
        try:
            values = [float(v) for v in list(bbox)]
        except (TypeError, ValueError):
            return None, True

    if len(values) > 4:
        values = values[:4]
    if len(values) != 4:
        return None, True

    ymin, xmin, ymax, xmax = values

    # Check for gross out-of-bounds or inversions
    has_gross_error = False
    for val in (ymin, xmin, ymax, xmax):
        if val < -20.0 or val > 1020.0:
            has_gross_error = True

    if ymax <= ymin or xmax <= xmin:
        has_gross_error = True

    # Normal clamping
    ymin_clamped = max(0.0, min(1000.0, ymin))
    xmin_clamped = max(0.0, min(1000.0, xmin))
    ymax_clamped = max(0.0, min(1000.0, ymax))
    xmax_clamped = max(0.0, min(1000.0, xmax))

    # Reject degenerate boxes (too thin)
    if (ymax_clamped - ymin_clamped) < 5 or (xmax_clamped - xmin_clamped) < 5:
        logger.warning("bbox degenerate for Q%s: %s", question_ref, values)
        return None, True

    clamped_box = [ymin_clamped, xmin_clamped, ymax_clamped, xmax_clamped]

    clamped_any = (
        ymin_clamped != ymin or xmin_clamped != xmin or
        ymax_clamped != ymax or xmax_clamped != xmax
    )

    requires_review = has_gross_error or clamped_any
    if has_gross_error:
        logger.warning("BBox gross error or inversion for Q%s: %s", question_ref, values)
        return clamped_box, True

    return clamped_box, requires_review


def save_debug_artifacts(
    original_image,
    crop_image,
    bbox_normalized: list[float],
    bbox_pixels: tuple[int, int, int, int],
    assets_dir: Path,
    question_ref: str,
    asset_role: str,
    source_page: int,
    page_index: int,
) -> None:
    """Save original page image, page image with bbox overlay, and final cropped image."""
    try:
        from PIL import ImageDraw
        import json

        run_dir = assets_dir.parent
        debug_dir = run_dir / "debug" / f"q{question_ref}"
        debug_dir.mkdir(parents=True, exist_ok=True)

        orig_path = debug_dir / f"page_p{source_page}.png"
        if not orig_path.exists():
            original_image.save(orig_path, format="PNG")

        overlay_image = original_image.copy()
        draw = ImageDraw.Draw(overlay_image)
        draw.rectangle(bbox_pixels, outline="red", width=3)
        overlay_path = debug_dir / f"bbox-overlay-{asset_role}.png"
        overlay_image.save(overlay_path, format="PNG")

        crop_path = debug_dir / f"crop-{asset_role}.png"
        crop_image.save(crop_path, format="PNG")

        metadata = {
            "question_ref": question_ref,
            "source_page": source_page,
            "page_index": page_index,
            "page_width": original_image.width,
            "page_height": original_image.height,
            "bbox_normalized": {
                "ymin": bbox_normalized[0],
                "xmin": bbox_normalized[1],
                "ymax": bbox_normalized[2],
                "xmax": bbox_normalized[3]
            },
            "bbox_pixels": {
                "left": bbox_pixels[0],
                "top": bbox_pixels[1],
                "right": bbox_pixels[2],
                "bottom": bbox_pixels[3]
            },
            "asset_role": asset_role
        }

        meta_path = debug_dir / "metadata.json"
        existing = []
        if meta_path.exists():
            try:
                existing = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = [existing]
            except Exception:
                existing = []
        existing.append(metadata)
        meta_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    except Exception as exc:
        logger.exception("Failed to save debug artifacts for Q%s: %s", question_ref, exc)


def normalize_norm_bbox(
    bbox: list[float] | tuple[float, ...] | NormBBox | Any | None,
) -> list[float] | None:
    """Coerce model output into [ymin, xmin, ymax, xmax] on a 0–1000 scale.

    Gemini sometimes returns 5–6 floats or swapped corners. Recover when safe;
    return None when the box is unusable.
    """
    if bbox is None:
        return None
    if isinstance(bbox, NormBBox):
        values = bbox.as_list()
    elif isinstance(bbox, dict):
        keys = ("ymin", "xmin", "ymax", "xmax")
        if all(k in bbox for k in keys):
            values = [bbox[k] for k in keys]
        else:
            return None
    else:
        try:
            values = [float(v) for v in list(bbox)]
        except (TypeError, ValueError):
            return None

    if len(values) > 4:
        # Common over-long outputs: take the first four [ymin,xmin,ymax,xmax].
        logger.warning("bbox length %s; using first 4 values: %s", len(values), values)
        values = values[:4]
    if len(values) != 4:
        logger.warning("bbox unusable (need 4 values, got %s): %s", len(values), values)
        return None

    ymin, xmin, ymax, xmax = values
    # Clamp to Gemini's 0–1000 space.
    ymin = max(0.0, min(1000.0, ymin))
    xmin = max(0.0, min(1000.0, xmin))
    ymax = max(0.0, min(1000.0, ymax))
    xmax = max(0.0, min(1000.0, xmax))
    if ymax < ymin:
        ymin, ymax = ymax, ymin
    if xmax < xmin:
        xmin, xmax = xmax, xmin
    # Reject degenerate boxes (too thin to be a real visual).
    if (ymax - ymin) < 5 or (xmax - xmin) < 5:
        logger.warning("bbox degenerate after normalize: %s", [ymin, xmin, ymax, xmax])
        return None
    return [ymin, xmin, ymax, xmax]


def expand_norm_bbox(
    bbox: list[float],
    *,
    frac: float = 0.02,
    min_pad: float = 10.0,
) -> list[float]:
    """Grow a 0–1000 box so slightly-short Gemini crops still include axes."""
    ymin, xmin, ymax, xmax = bbox
    height = max(1.0, ymax - ymin)
    width = max(1.0, xmax - xmin)
    dy = max(min_pad, height * frac)
    dx = max(min_pad, width * frac)
    return [
        max(0.0, ymin - dy),
        max(0.0, xmin - dx),
        min(1000.0, ymax + dy),
        min(1000.0, xmax + dx),
    ]


def page_rect_to_norm_bbox(
    rect: tuple[float, float, float, float],
    *,
    page_width: float,
    page_height: float,
) -> list[float]:
    """Convert page-space (x0,y0,x1,y1) → [ymin, xmin, ymax, xmax] on 0–1000."""
    x0, y0, x1, y1 = rect
    width = max(1.0, float(page_width))
    height = max(1.0, float(page_height))
    return [
        max(0.0, min(1000.0, (y0 / height) * 1000.0)),
        max(0.0, min(1000.0, (x0 / width) * 1000.0)),
        max(0.0, min(1000.0, (y1 / height) * 1000.0)),
        max(0.0, min(1000.0, (x1 / width) * 1000.0)),
    ]


def _rect_area(rect: tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = rect
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _union_rect(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _rects_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    *,
    pad: float = 2.0,
) -> bool:
    return not (
        a[2] + pad < b[0]
        or b[2] + pad < a[0]
        or a[3] + pad < b[1]
        or b[3] + pad < a[1]
    )


def _rects_close(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    dist: float = 15.0,
) -> bool:
    return not (
        a[2] + dist < b[0]
        or b[2] + dist < a[0]
        or a[3] + dist < b[1]
        or b[3] + dist < a[1]
    )


def is_likely_diagram_label(text: str) -> bool:
    """Determine if a raw text block on the page is likely a diagram annotation or caption,

    and NOT a question stem, instruction, or multiple choice option prose.
    """
    cleaned = text.strip()
    if not cleaned:
        return False
    # Labels, substance names, and captions are short.
    if len(cleaned) > 55:
        return False
    # Avoid text blocks containing common question stem / instruction keywords.
    lower_text = cleaned.lower()
    question_words = {
        "which", "what", "calculate", "determine", "explain", "state", "show",
        "represent", "titrate", "following", "acid-alkali", "diagram", "below",
        "pairs", "correct", "statement", "confirm", "nature", "solution",
        "describe", "suggest", "compare", "contrast", "identify", "predict",
        "give", "name", "write", "equation", "formula", "using"
    }
    words = set(lower_text.split())
    if any(q_word in words for q_word in question_words):
        return False
    return True


def snap_norm_bbox_to_drawings(
    page,
    bbox: list[float],
    *,
    min_iou: float = 0.08,
    max_page_frac: float = 0.42,
) -> list[float]:
    """Grow a Gemini box to cover overlapping vector drawings (axes, graphs) and raster images.

    Gemini 0–1000 boxes are approximate. Chemistry graphs are often drawn as
    PDF vectors or embedded raster images, so the pixmap crop must include those elements
    even when the model under-boxes the figure.
    """
    env_min_iou = os.environ.get("SNAP_MIN_IOU")
    if env_min_iou is not None:
        try:
            min_iou = float(env_min_iou)
        except ValueError:
            pass

    env_max_page_frac = os.environ.get("SNAP_MAX_PAGE_FRAC")
    if env_max_page_frac is not None:
        try:
            max_page_frac = float(env_max_page_frac)
        except ValueError:
            pass

    try:
        rect = page.rect
        page_width, page_height = float(rect.width), float(rect.height)
        target = norm_bbox_to_page_rect(
            bbox, page_width=page_width, page_height=page_height
        )
        drawings = page.get_drawings() or []
        
        candidates = []
        for item in drawings:
            raw = item.get("rect") if isinstance(item, dict) else None
            if raw is not None:
                candidates.append((float(raw.x0), float(raw.y0), float(raw.x1), float(raw.y1)))
                
        # Also collect raster image rects on the page as candidates for snapping
        image_list = page.get_images() or []
        for img_info in image_list:
            xref = img_info[0]
            # Get the exact locations where this image is displayed on the page
            for img_rect in page.get_image_rects(xref):
                candidates.append((float(img_rect.x0), float(img_rect.y0), float(img_rect.x1), float(img_rect.y1)))
                
    except Exception as exc:  # noqa: BLE001
        logger.debug("drawing/image snap skipped: %s", exc)
        return bbox

    page_area = max(1.0, page_width * page_height)
    grown = target
    changed = False

    # Pass 1: Grow the bounding box using vector drawings and raster images
    for cand in candidates:
        area = _rect_area(cand)
        if area < 80 or area > page_area * max_page_frac:
            continue

        # Filter out long, thin horizontal/vertical dividers or lines that extend far beyond the target
        cand_w = max(0.1, cand[2] - cand[0])
        cand_h = max(0.1, cand[3] - cand[1])
        target_w = max(1.0, target[2] - target[0])
        target_h = max(1.0, target[3] - target[1])
        if cand_w < 6.0 and cand_h > target_h * 2.0:
            continue
        if cand_h < 6.0 and cand_w > target_w * 2.0:
            continue

        # Check overlap strictly against the ORIGINAL target box to prevent cascading runaways
        if not (_rects_overlap(target, cand) or _rect_iou(target, cand) >= min_iou):
            continue
        # Snap when the Gemini box sits inside a larger figure (typical under-box).
        contains_target = (
            cand[0] <= target[0] + 2
            and cand[1] <= target[1] + 2
            and cand[2] >= target[2] - 2
            and cand[3] >= target[3] - 2
        )
        if (
            area > _rect_area(target) * 8
            and _rect_iou(target, cand) < 0.2
            and not contains_target
        ):
            continue
        grown = _union_rect(grown, cand)
        changed = True

    # Pass 2: Identify and merge adjacent valid text labels (captions, substance names, axis labels)
    # only if they are immediately close (dist <= 15 points) to the core diagram box (grown after Pass 1).
    # We check closeness against the fixed base_diagram from Pass 1 to prevent cascading union inflation
    # (where growing the box would recursively pull in more distant text blocks like MCQ choices).
    try:
        base_diagram = grown
        text_blocks = page.get_text("blocks") or []
        for b in text_blocks:
            if len(b) >= 7 and b[6] == 0:  # text block type
                bx0, by0, bx1, by1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
                b_text = str(b[4]).strip()
                if is_likely_diagram_label(b_text):
                    cand_rect = (bx0, by0, bx1, by1)
                    if _rects_close(base_diagram, cand_rect, dist=15.0):
                        grown = _union_rect(grown, cand_rect)
                        changed = True
    except Exception as t_exc:
        logger.debug("text label snapping skipped: %s", t_exc)

    if not changed or _rect_area(grown) > page_area * max_page_frac:
        return bbox
    snapped = normalize_norm_bbox(
        page_rect_to_norm_bbox(grown, page_width=page_width, page_height=page_height)
    )
    return snapped or bbox


def norm_bbox_to_pixels(
    bbox: list[float] | tuple[float, ...] | NormBBox,
    *,
    width: int,
    height: int,
    padding: int = 2,
) -> tuple[int, int, int, int]:
    """Return PIL crop box (left, upper, right, lower) in pixel coords."""
    normalized = normalize_norm_bbox(bbox)
    if normalized is None:
        raise ValueError(f"Expected usable 4 bbox values, got {bbox!r}")
    ymin, xmin, ymax, xmax = normalized

    left = int(round((xmin / 1000.0) * width)) - padding
    upper = int(round((ymin / 1000.0) * height)) - padding
    right = int(round((xmax / 1000.0) * width)) + padding
    lower = int(round((ymax / 1000.0) * height)) + padding

    left = max(0, min(left, width - 1))
    upper = max(0, min(upper, height - 1))
    right = max(left + 1, min(right, width))
    lower = max(upper + 1, min(lower, height))
    return left, upper, right, lower


def crop_norm_bbox(
    page_image,
    bbox: list[float] | tuple[float, ...] | NormBBox,
    *,
    padding: int = 2,
):
    """Crop a PIL image using a Gemini normalized bbox."""
    width, height = page_image.size
    box = norm_bbox_to_pixels(bbox, width=width, height=height, padding=padding)
    return page_image.crop(box)


def save_crop(
    page_image,
    bbox: list[float] | tuple[float, ...] | NormBBox,
    *,
    dest: str | Path,
    padding: int = 2,
) -> Path:
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    cropped = crop_norm_bbox(page_image, bbox, padding=padding)
    cropped.save(path, format="PNG")
    return path


def norm_bbox_to_page_rect(
    bbox: list[float] | tuple[float, ...] | NormBBox,
    *,
    page_width: float,
    page_height: float,
):
    """Convert [ymin,xmin,ymax,xmax] 0–1000 → pymupdf-like (x0,y0,x1,y1) page points."""
    normalized = normalize_norm_bbox(bbox)
    if normalized is None:
        raise ValueError(f"Expected usable 4 bbox values, got {bbox!r}")
    ymin, xmin, ymax, xmax = normalized
    return (
        (xmin / 1000.0) * page_width,
        (ymin / 1000.0) * page_height,
        (xmax / 1000.0) * page_width,
        (ymax / 1000.0) * page_height,
    )


def _rect_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def find_overlapping_image_xref(
    page,
    bbox: list[float] | tuple[float, ...] | NormBBox,
    *,
    min_iou: float = 0.15,
) -> int | None:
    """Return PDF image xref whose *display* bbox overlaps the norm crop box."""
    try:
        rect = page.rect
        target = norm_bbox_to_page_rect(
            bbox, page_width=float(rect.width), page_height=float(rect.height)
        )
        best_xref: int | None = None
        best_iou = 0.0
        for img in page.get_images(full=True):
            xref = int(img[0])
            try:
                display = page.get_image_bbox(img)
            except Exception:  # noqa: BLE001
                continue
            cand = (float(display.x0), float(display.y0), float(display.x1), float(display.y1))
            iou = _rect_iou(target, cand)
            if iou > best_iou:
                best_iou = iou
                best_xref = xref
        if best_xref is not None and best_iou >= min_iou:
            return best_xref
    except Exception as exc:  # noqa: BLE001
        logger.debug("image xref lookup failed: %s", exc)
    return None


def save_embedded_image(doc, xref: int, *, dest: str | Path) -> Path | None:
    """Save a PDF XObject image by xref (full bitmap, not the page display crop)."""
    try:
        import pymupdf
    except ImportError:
        return None
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha >= 4:  # CMYK etc.
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        pix.save(str(path))
        return path
    except Exception as exc:  # noqa: BLE001
        logger.warning("embedded image save failed xref=%s: %s", xref, exc)
        return None


def save_visual_crop(
    page_image,
    bbox: list[float] | tuple[float, ...] | NormBBox,
    *,
    dest: str | Path,
    page=None,
    doc=None,
    padding: int = 8,
    prefer_embedded: bool = False,
    band_y_range: tuple[int, int] | None = None,
) -> tuple[Path, str]:
    """Crop a stem/options visual from the same page render Gemini saw.

    Default is a padded page-image crop. Embedded PDF XObjects are optional
    because they often do not match the figure Gemini boxed (vector graphs,
    logos, truncated display bboxes).
    """
    normalized = normalize_norm_bbox(bbox)
    if normalized is None:
        raise ValueError(f"Unusable bbox for crop: {bbox!r}")

    path = Path(dest)

    # Check for embedded image preference before we snap/grow the bounding box,
    # as snapping can make the page crop area too large and disqualify a complete embedded image.
    if prefer_embedded and page is not None and doc is not None:
        un_snapped_expanded = expand_norm_bbox(normalized)
        xref = find_overlapping_image_xref(page, un_snapped_expanded)
        if xref is not None:
            page_box = norm_bbox_to_pixels(
                un_snapped_expanded,
                width=page_image.size[0],
                height=page_image.size[1],
                padding=padding,
            )
            crop_w = max(1, page_box[2] - page_box[0])
            crop_h = max(1, page_box[3] - page_box[1])
            try:
                import pymupdf

                probe = pymupdf.Pixmap(doc, xref)
                emb_w, emb_h = int(probe.width), int(probe.height)
                if emb_w * emb_h >= int(crop_w * crop_h * 1.25) or emb_w >= int(crop_w * 1.35):
                    saved = save_embedded_image(doc, xref, dest=path)
                    if saved is not None:
                        logger.info(
                            "using embedded PDF image xref=%s (%sx%s) instead of page crop (%sx%s)",
                            xref,
                            emb_w,
                            emb_h,
                            crop_w,
                            crop_h,
                        )
                        return saved, "embedded"
            except Exception as exc:  # noqa: BLE001
                logger.debug("embedded preference skipped: %s", exc)

    # Support band coordinate space translation and full page snapping
    if band_y_range is not None and page is not None:
        try:
            top, bottom = band_y_range
            rect = page.rect
            band_h_actual = page_image.size[1]
            
            # Estimate full-page pixel height assuming 150 DPI render
            dpi = 150
            full_page_h_pixels = int(float(rect.height) * (dpi / 72.0))
            
            # Convert band-relative bbox to full-page normalized coordinates
            ymin, xmin, ymax, xmax = normalized
            ymin_full = (top + (ymin / 1000.0) * band_h_actual) / full_page_h_pixels * 1000.0
            ymax_full = (top + (ymax / 1000.0) * band_h_actual) / full_page_h_pixels * 1000.0
            normalized_full = [ymin_full, xmin, ymax_full, xmax]
            
            # Snap full-page normalized coordinates to vector lines/images on the full page
            expanded_full = expand_norm_bbox(normalized_full)
            snapped_full = snap_norm_bbox_to_drawings(page, expanded_full)
            
            # Convert the snapped full-page bbox back to band-relative normalized coordinates
            ymin_snapped_full, xmin_snapped, ymax_snapped_full, xmax_snapped = snapped_full
            ymin_band = ((ymin_snapped_full / 1000.0) * full_page_h_pixels - top) / band_h_actual * 1000.0
            ymax_band = ((ymax_snapped_full / 1000.0) * full_page_h_pixels - top) / band_h_actual * 1000.0
            
            ymin_band = max(0.0, min(1000.0, ymin_band))
            ymax_band = max(0.0, min(1000.0, ymax_band))
            expanded = [ymin_band, xmin_snapped, ymax_band, xmax_snapped]
        except Exception as exc:
            logger.warning("Band-to-full-page coordinate transformation failed: %s", exc)
            expanded = expand_norm_bbox(normalized)
    else:
        expanded = expand_norm_bbox(normalized)
        if page is not None:
            expanded = snap_norm_bbox_to_drawings(page, expanded)

    high_quality_crop = os.environ.get("HIGH_QUALITY_PDF_CROP", "false").lower() == "true"
    if high_quality_crop and page is not None:
        try:
            import pymupdf
            rect = page.rect
            pdf_left = (expanded[1] / 1000.0) * float(rect.width)
            pdf_top = (expanded[0] / 1000.0) * float(rect.height)
            pdf_right = (expanded[3] / 1000.0) * float(rect.width)
            pdf_bottom = (expanded[2] / 1000.0) * float(rect.height)
            
            clip = pymupdf.Rect(pdf_left, pdf_top, pdf_right, pdf_bottom)
            clip.intersect(rect)
            if not clip.is_empty:
                pix = page.get_pixmap(dpi=300, clip=clip)
                path.parent.mkdir(parents=True, exist_ok=True)
                pix.save(str(path))
                return path, "pymupdf_hq_crop"
        except Exception as exc:
            logger.warning("High-quality PyMuPDF crop failed, falling back to PIL: %s", exc)

    saved = save_crop(page_image, expanded, dest=path, padding=padding)
    return saved, "page_crop"


__all__ = [
    "RenderedPage",
    "render_page",
    "validate_and_normalize_bbox",
    "save_debug_artifacts",
    "crop_norm_bbox",
    "expand_norm_bbox",
    "find_overlapping_image_xref",
    "normalize_norm_bbox",
    "norm_bbox_to_page_rect",
    "norm_bbox_to_pixels",
    "page_rect_to_norm_bbox",
    "save_crop",
    "save_embedded_image",
    "save_visual_crop",
    "snap_norm_bbox_to_drawings",
]

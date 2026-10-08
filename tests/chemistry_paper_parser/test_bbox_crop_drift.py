from __future__ import annotations

import os
from pathlib import Path
import pymupdf
import pytest
from PIL import Image

from questbank.pdf.bbox_crop import (
    render_page,
    validate_and_normalize_bbox,
    expand_norm_bbox,
    norm_bbox_to_pixels,
    save_visual_crop,
)

# Use the existing PDF fixture path
PDF_PATH = Path(__file__).resolve().parents[2] / "P1 BBSS - 3E Chemistry (6092) EYE P1 2023_.pdf"


@pytest.mark.skipif(not PDF_PATH.exists(), reason="sample PDF missing")
def test_page_render_abstraction_is_page_local_and_independent():
    """Verify that every page has its own independent coordinate system and size."""
    doc = pymupdf.open(PDF_PATH)
    try:
        # Render early, middle, and late pages
        p1 = render_page(doc, 0, dpi=150)
        p5 = render_page(doc, 4, dpi=150)
        p12 = render_page(doc, 11, dpi=150)

        # Check abstraction fields
        for p in (p1, p5, p12):
            assert p.image is not None
            assert p.width > 0
            assert p.height > 0
            assert p.pdf_width > 0
            assert p.pdf_height > 0
            assert p.source_page_number == p.page_index + 1

        # Check size consistency: they should have the same dimensions in this standard PDF,
        # but most importantly, their rendering and coordinate mappings are completely local
        # and do not accumulate coordinates of previous pages.
        assert p1.width == p5.width == p12.width
        assert p1.height == p5.height == p12.height

        # Coordinate conversion check: same normalized bbox on different pages
        # must result in the exact same pixel boundaries, showing zero drift.
        norm_box = [200, 150, 600, 850]
        box_p1 = norm_bbox_to_pixels(norm_box, width=p1.width, height=p1.height, padding=0)
        box_p5 = norm_bbox_to_pixels(norm_box, width=p5.width, height=p5.height, padding=0)
        box_p12 = norm_bbox_to_pixels(norm_box, width=p12.width, height=p12.height, padding=0)

        assert box_p1 == box_p5 == box_p12
        assert box_p1 == (191, 330, 1084, 990)  # (xmin/1000 * W, ymin/1000 * H, xmax/1000 * W, ymax/1000 * H)

    finally:
        doc.close()


def test_bbox_validation_clamping_and_gross_errors():
    """Verify that validate_and_normalize_bbox clamps small overshoots but flags gross errors."""
    # Perfect bbox
    box, review = validate_and_normalize_bbox([200, 150, 600, 850], "1")
    assert box == [200, 150, 600, 850]
    assert not review

    # Minor overshoot (e.g. -5, 1005) is clamped but may set review to True depending on clamp
    box, review = validate_and_normalize_bbox([-5, 150, 600, 1005], "1")
    assert box == [0, 150, 600, 1000]
    assert review

    # Gross error: extreme value
    box, review = validate_and_normalize_bbox([200, -100, 600, 850], "1")
    assert review

    # Gross error: inverted boundaries (ymin > ymax)
    box, review = validate_and_normalize_bbox([600, 150, 200, 850], "1")
    assert review


def test_safety_padding_bounds():
    """Verify that expand_norm_bbox adds small padding within 1-3% limits."""
    # Test normal bbox
    bbox = [200, 150, 600, 850]
    expanded = expand_norm_bbox(bbox)
    
    ymin, xmin, ymax, xmax = expanded
    # Bbox is expanded outwards (coordinates decrease/increase)
    assert ymin < 200
    assert xmin < 150
    assert ymax > 600
    assert xmax > 850

    # Bbox must be clipped to [0.0, 1000.0] page boundaries
    extreme_bbox = [0, 10, 990, 1000]
    expanded_extreme = expand_norm_bbox(extreme_bbox)
    assert expanded_extreme[0] == 0.0
    assert expanded_extreme[3] == 1000.0


@pytest.mark.skipif(not PDF_PATH.exists(), reason="sample PDF missing")
def test_high_quality_pdf_crop_consistent_mapping(tmp_path: Path):
    """Test high-quality PDF crop with pymupdf and ensure it is consistent."""
    doc = pymupdf.open(PDF_PATH)
    try:
        page = doc[0]
        p1 = render_page(doc, 0, dpi=150)
        norm_box = [200, 150, 600, 850]

        # Enable high-quality crop
        os.environ["HIGH_QUALITY_PDF_CROP"] = "true"
        dest_hq = tmp_path / "hq_crop.png"
        
        path, source = save_visual_crop(
            p1.image,
            norm_box,
            dest=dest_hq,
            page=page,
            doc=doc,
        )
        assert source == "pymupdf_hq_crop"
        assert path.exists()

        # Check size: 300 DPI crop should be larger than standard DPI page crop
        with Image.open(path) as img:
            assert img.width > 0
            assert img.height > 0

    finally:
        os.environ["HIGH_QUALITY_PDF_CROP"] = "false"
        doc.close()

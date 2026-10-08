"""Tests for visual crop helpers (embedded PDF image preference)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from questbank.pdf.bbox_crop import (
    find_overlapping_image_xref,
    save_visual_crop,
)

PDF = Path(__file__).resolve().parents[2] / "P1 BBSS - 3E Chemistry (6092) EYE P1 2023_.pdf"


@pytest.mark.skipif(not PDF.exists(), reason="sample PDF missing")
def test_page4_prefers_full_embedded_graph(tmp_path: Path):
    """Page 4 graph: display bbox is truncated but XObject bitmap is complete."""
    doc = pymupdf.open(PDF)
    page = doc[3]
    # Same truncated display box Docling/Gemini used previously (0-1000).
    bad_bbox = [328.28, 130.6, 512.58, 466.4]
    xref = find_overlapping_image_xref(page, bad_bbox)
    assert xref is not None

    pix = page.get_pixmap(dpi=120)
    page_image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    dest = tmp_path / "q08-stem-1.png"
    path, source = save_visual_crop(
        page_image,
        bad_bbox,
        dest=dest,
        page=page,
        doc=doc,
        prefer_embedded=True,
    )
    assert source == "embedded"
    assert path.exists()
    with Image.open(path) as img:
        # Full embedded graph is wide; truncated page crop was ~343px at 120dpi.
        assert img.width >= 500
        assert img.height >= 200
    doc.close()

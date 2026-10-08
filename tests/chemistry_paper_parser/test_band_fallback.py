"""Unit tests for per-question band fallback helpers (no Gemini)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from questbank.parsers.chemistry.parse_mcq_gemini import (
    _band_crops_from_page,
    _is_output_limit_error,
    _question_starts_on_page,
)

PDF = Path(
    "output/factory-storage/exam-sources/"
    "1f64807e-5dc6-4805-8ca5-e3dd564e31d0/question-paper.pdf"
)


def test_is_output_limit_error():
    assert _is_output_limit_error(RuntimeError("finish_reason=MAX_TOKENS"))
    assert _is_output_limit_error(RuntimeError("Gemini truncated JSON"))
    assert not _is_output_limit_error(RuntimeError("timeout"))


@pytest.mark.skipif(not PDF.is_file(), reason="batch PDF missing")
def test_question_starts_page2_and_12():
    doc = pymupdf.open(PDF)
    p2 = _question_starts_on_page(doc[1])
    p12 = _question_starts_on_page(doc[11])
    doc.close()
    assert [r for r, _ in p2] == ["3", "4"]
    assert [r for r, _ in p12] == ["27", "28", "29"]


@pytest.mark.skipif(not PDF.is_file(), reason="batch PDF missing")
def test_band_crops_page2():
    doc = pymupdf.open(PDF)
    page = doc[1]
    pix = page.get_pixmap(dpi=72)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    bands = _band_crops_from_page(img, page, dpi=72)
    doc.close()
    assert [h for h, _, _ in bands] == ["3", "4"]
    assert all(b.size[1] > 40 for _, b, _ in bands)

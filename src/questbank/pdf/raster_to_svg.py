"""Convert raster exam assets to SVG (PyMuPDF) for Simulated Test experiments.

Note: PyMuPDF wraps the raster image inside an SVG ``<image>`` element — it does
not vectorize diagram strokes into true path geometry. Useful for testing SVG
delivery in the Simulated Test UI without changing stored bank assets.
"""

from __future__ import annotations

from pathlib import Path


def raster_bytes_to_svg(data: bytes, *, filename_hint: str = "asset.png") -> bytes:
    """Open PNG/JPEG/WebP bytes with PyMuPDF and return SVG markup bytes."""
    import pymupdf

    suffix = Path(filename_hint).suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}:
        suffix = ".png"

    doc = pymupdf.open(stream=data, filetype=suffix.lstrip("."))
    try:
        if doc.page_count < 1:
            raise ValueError("Image produced no pages for SVG conversion")
        svg = doc[0].get_svg_image()
    finally:
        doc.close()

    if isinstance(svg, bytes):
        return svg
    return str(svg).encode("utf-8")

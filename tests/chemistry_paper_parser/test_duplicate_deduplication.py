from __future__ import annotations

import pytest
from questbank.types.canonical import (
    GeminiPageQuestion,
    GeminiStemVisual,
    CompositeVisualOptions,
    ImageBlock,
    TextBlock,
)
from questbank.parsers.chemistry.parse_mcq_gemini import _materialize_question, _norm_bbox_iou


def test_norm_bbox_iou_calculation():
    """Verify that _norm_bbox_iou correctly computes overlap percentage."""
    box1 = [100.0, 100.0, 300.0, 300.0]
    box2 = [100.0, 100.0, 300.0, 300.0]
    # Perfect overlap
    assert abs(_norm_bbox_iou(box1, box2) - 1.0) < 0.001

    # Half overlap
    box3 = [100.0, 100.0, 200.0, 300.0]
    assert abs(_norm_bbox_iou(box1, box3) - 0.5) < 0.001

    # No overlap
    box4 = [400.0, 400.0, 500.0, 500.0]
    assert _norm_bbox_iou(box1, box4) == 0.0


def test_duplicate_stem_visual_deduplication(tmp_path):
    """Verify that stem visuals overlapping with composite options are cleanly deduplicated."""
    from PIL import Image
    page_image = Image.new("RGB", (1000, 1000), "white")

    # Bbox representing the options visual block (e.g. 4 graphs)
    shared_bbox = [200.0, 150.0, 600.0, 850.0]

    item = GeminiPageQuestion(
        question_ref="27",
        content=[
            TextBlock(value="Dilute sulfuric acid was added..."),
            # Gemini mistakenly duplicated the same visual block in the content
            ImageBlock(asset="placeholder.png", bbox=shared_bbox),
        ],
        stem_visuals=[
            # Gemini mistakenly classified the options block as a stem visual as well
            GeminiStemVisual(bbox=shared_bbox, role="graph"),
        ],
        options=CompositeVisualOptions(
            labels=["A", "B", "C", "D"],
            asset="",
            bbox=shared_bbox,
        ),
        options_bbox=shared_bbox,
    )

    question = _materialize_question(
        item,
        page_number=12,
        page_image=page_image,
        assets_dir=tmp_path,
    )

    # After deduplication:
    # 1. The stem_visual MUST be removed (meaning no duplicate stem crops created)
    # 2. The duplicate ImageBlock in content MUST be filtered out
    # 3. Only the composite options visual block must remain
    stem_image_assets = [b.asset for b in question.content if b.type == "image"]
    assert len(stem_image_assets) == 0  # The content image was stripped

    # The options crop is still generated and populated
    assert question.options.mode == "composite_visual"
    assert question.options.asset == "q27-options.png"
    assert (tmp_path / "q27-options.png").exists()
    assert not (tmp_path / "q27-stem-1.png").exists()  # Ensure duplicate crop is NOT created

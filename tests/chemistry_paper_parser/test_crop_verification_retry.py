from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock
from PIL import Image
from pathlib import Path
from questbank.types.canonical import (
    GeminiPageQuestion,
    CompositeVisualOptions,
    ImageBlock,
    TextBlock,
)
from questbank.llm.gemini_question import GeminiCropVerificationResult, GeminiBBox
from questbank.parsers.chemistry.parse_mcq_gemini import _materialize_question


def test_visual_crop_verification_retry_and_expansion(tmp_path: Path):
    """Verify that crop verification failure triggers a retry with expanded corrected bbox."""
    # Ensure VERIFY_VISUAL_CROPS environment variable is enabled
    os.environ["VERIFY_VISUAL_CROPS"] = "true"

    page_image = Image.new("RGB", (1000, 1000), "white")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Initial proposed bounding box (0-1000 coordinates)
    initial_bbox = [200.0, 150.0, 400.0, 750.0]

    item = GeminiPageQuestion(
        question_ref="4",
        content=[TextBlock(value="Which graph shows...")],
        stem_visuals=[],
        options=CompositeVisualOptions(
            labels=["A", "B", "C", "D"],
            asset="temp.png",
            bbox=initial_bbox,
        ),
        options_bbox=initial_bbox,
        answer_key=None,
        requires_review=False,
    )

    # Mock GeminiClient
    mock_client = MagicMock()
    
    # Simulate a crop verification response indicating the crop is missing content,
    # and provide a corrected bounding box.
    mock_verification = GeminiCropVerificationResult(
        valid=False,
        issue="The right side with choice D is cut off",
        corrected_bbox=GeminiBBox(ymin=200.0, xmin=150.0, ymax=400.0, xmax=850.0),
    )
    mock_client.generate_structured.return_value = mock_verification

    # Call _materialize_question with the mocked client
    parsed_q = _materialize_question(
        item,
        page_number=1,
        page_image=page_image,
        assets_dir=assets_dir,
        client=mock_client,
    )

    # Verify that generate_structured was called for verification
    assert mock_client.generate_structured.called

    # Get the resulting options bbox
    final_bbox = parsed_q.options.bbox

    # Ensure the final bounding box is different from the initial_bbox
    assert final_bbox != initial_bbox

    # Ensure the final bounding box is an expansion of the corrected_bbox
    # Corrected: [200.0, 150.0, 400.0, 850.0]
    # expand_norm_bbox(frac=0.03, min_pad=15.0):
    # height = 200.0, width = 700.0
    # dy = max(15.0, 200 * 0.03 = 6.0) = 15.0
    # dx = max(15.0, 700 * 0.03 = 21.0) = 21.0
    # ymin = 200 - 15 = 185
    # xmin = 150 - 21 = 129
    # ymax = 400 + 15 = 415
    # xmax = 850 + 21 = 871
    expected_expanded = [185.0, 129.0, 415.0, 871.0]
    assert final_bbox == expected_expanded

    # Check that the corrected asset file exists in the directory
    asset_path = assets_dir / f"q04-options.png"
    assert asset_path.exists()


def test_verification_stops_once_valid_instead_of_exhausting_all_rounds(tmp_path: Path):
    """A crop accepted as valid on round 2 should not trigger a 3rd verification call."""
    os.environ["VERIFY_VISUAL_CROPS"] = "true"

    page_image = Image.new("RGB", (1000, 1000), "white")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    initial_bbox = [200.0, 150.0, 400.0, 750.0]
    item = GeminiPageQuestion(
        question_ref="5",
        content=[TextBlock(value="Which graph shows...")],
        stem_visuals=[],
        options=CompositeVisualOptions(labels=["A", "B", "C", "D"], asset="temp.png", bbox=initial_bbox),
        options_bbox=initial_bbox,
        answer_key=None,
        requires_review=False,
    )

    mock_client = MagicMock()
    invalid_result = GeminiCropVerificationResult(
        valid=False,
        issue="choice D cut off",
        corrected_bbox=GeminiBBox(ymin=200.0, xmin=150.0, ymax=400.0, xmax=850.0),
    )
    valid_result = GeminiCropVerificationResult(valid=True, issue=None, corrected_bbox=None)
    mock_client.generate_structured.side_effect = [invalid_result, valid_result]

    parsed_q = _materialize_question(
        item, page_number=1, page_image=page_image, assets_dir=assets_dir, client=mock_client,
    )

    # Exactly 2 verification calls: fails once, then succeeds — must not call a 3rd time.
    assert mock_client.generate_structured.call_count == 2
    assert parsed_q.requires_review is False


def test_content_image_block_now_goes_through_verification(tmp_path: Path):
    """Diagrams delivered via content[] (not stem_visuals) must also be verified.

    Previously this fallback path saved whatever bbox Gemini gave with zero
    verification, silently keeping bad crops.
    """
    os.environ["VERIFY_VISUAL_CROPS"] = "true"

    page_image = Image.new("RGB", (1000, 1000), "white")
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    from questbank.types.canonical import TextOptions, TextOptionItem

    initial_bbox = [100.0, 100.0, 300.0, 300.0]
    item = GeminiPageQuestion(
        question_ref="6",
        content=[ImageBlock(asset="unresolved.png", bbox=initial_bbox)],
        stem_visuals=[],
        options=TextOptions(items=[
            TextOptionItem(label="A", content="foo"),
            TextOptionItem(label="B", content="bar"),
            TextOptionItem(label="C", content="baz"),
            TextOptionItem(label="D", content="qux"),
        ]),
        options_bbox=None,
        answer_key=None,
        requires_review=False,
    )

    mock_client = MagicMock()
    corrected_result = GeminiCropVerificationResult(
        valid=False,
        issue="diagram cut off",
        corrected_bbox=GeminiBBox(ymin=100.0, xmin=100.0, ymax=300.0, xmax=500.0),
    )
    mock_client.generate_structured.return_value = corrected_result

    parsed_q = _materialize_question(
        item, page_number=1, page_image=page_image, assets_dir=assets_dir, client=mock_client,
    )

    # Verification must have been invoked for the content-image fallback path.
    assert mock_client.generate_structured.called
    assert parsed_q.requires_review is True

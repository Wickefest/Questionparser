"""Tests for normalize_norm_bbox coercion."""

from __future__ import annotations

from questbank.pdf.bbox_crop import (
    expand_norm_bbox,
    normalize_norm_bbox,
    page_rect_to_norm_bbox,
    snap_norm_bbox_to_drawings,
)


def test_expand_grows_and_clamps():
    grown = expand_norm_bbox([200, 200, 400, 400], frac=0.05, min_pad=18)
    assert grown[0] < 200
    assert grown[1] < 200
    assert grown[2] > 400
    assert grown[3] > 400
    edge = expand_norm_bbox([0, 0, 50, 50], frac=0.5, min_pad=30)
    assert edge[0] == 0.0
    assert edge[1] == 0.0



def test_normalize_keeps_valid_four():
    assert normalize_norm_bbox([100, 200, 300, 400]) == [100.0, 200.0, 300.0, 400.0]


def test_normalize_truncates_six_to_four():
    assert normalize_norm_bbox([445, 225, 575, 715, 0, 1]) == [445.0, 225.0, 575.0, 715.0]


def test_normalize_swaps_inverted_corners():
    assert normalize_norm_bbox([300, 400, 100, 200]) == [100.0, 200.0, 300.0, 400.0]


def test_normalize_rejects_too_short():
    assert normalize_norm_bbox([1, 2, 3]) is None


def test_normalize_rejects_degenerate():
    assert normalize_norm_bbox([100, 200, 101, 201]) is None


def test_snap_bbox_grows_to_overlapping_drawings():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=500, height=700)
    figure = pymupdf.Rect(80, 120, 420, 360)
    page.draw_rect(figure, color=(0, 0, 0), width=1.5)
    page.draw_line(pymupdf.Point(80, 360), pymupdf.Point(420, 360), color=(0, 0, 0))
    tight = page_rect_to_norm_bbox((180, 180, 280, 260), page_width=500, page_height=700)
    snapped = snap_norm_bbox_to_drawings(page, tight)
    doc.close()
    assert snapped[0] < tight[0]
    assert snapped[1] < tight[1]
    assert snapped[2] > tight[2]
    assert snapped[3] > tight[3]


def test_snap_bbox_grows_to_overlapping_raster_images():
    class MockRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0 = x0
            self.y0 = y0
            self.x1 = x1
            self.y1 = y1
            self.width = x1 - x0
            self.height = y1 - y0

    class MockPage:
        def __init__(self):
            self.rect = MockRect(0, 0, 612, 792)
        def get_drawings(self):
            return []
        def get_images(self):
            return [(444, 0, 612, 336, 8, "DeviceRGB", "", "Image67", "FlateDecode", 0)]
        def get_image_rects(self, xref):
            return [MockRect(130.0, 328.0, 466.0, 512.0)]

    page = MockPage()
    # Gemini under-boxed representation (xmin is close, but ymin is cut off)
    under_boxed_bbox = [428.0, 213.0, 648.0, 755.0]

    snapped = snap_norm_bbox_to_drawings(page, under_boxed_bbox)

    # ymin should snap to approximately (328/792)*1000 = 414.14
    assert snapped[0] < 420.0
    assert abs(snapped[0] - 414.14) < 1.0

    # xmax should snap to approximately (466/612)*1000 = 761.43
    assert snapped[3] > 758.0
    assert abs(snapped[3] - 761.43) < 1.0


def test_save_visual_crop_with_band_mapping(tmp_path):
    from PIL import Image
    from questbank.pdf.bbox_crop import save_visual_crop

    class MockRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0 = x0
            self.y0 = y0
            self.x1 = x1
            self.y1 = y1
            self.width = x1 - x0
            self.height = y1 - y0

    class MockPage:
        def __init__(self):
            self.rect = MockRect(0, 0, 612, 792)
        def get_drawings(self):
            return []
        def get_images(self):
            return [(444, 0, 612, 336, 8, "DeviceRGB", "", "Image67", "FlateDecode", 0)]
        def get_image_rects(self, xref):
            # Let's say there is an image at y0=328, y1=512
            return [MockRect(130.0, 328.0, 466.0, 512.0)]

    page = MockPage()

    # Create a band-image (say, of height 400 pixels) representing a vertical slice of the page
    # Full page is 1650 pixels high at 150 DPI (792 * 150 / 72 = 1650)
    # Let's say this band starts at y=500 and ends at y=900 (height=400)
    band_y_range = (500, 900)
    band_image = Image.new("RGB", (1275, 400), "white")

    # In band space, let's say the bbox is at ymin=200, ymax=300 (which corresponds to y_full_pixels = 580 to 620)
    # On a 0-1000 scale relative to the 400px band: ymin=200 is 80px, ymax=300 is 120px.
    # Relative to full page (1650px): y_full_pixels = 500 + 80 = 580, 500 + 120 = 620.
    # But the image on page is at 328 to 512 points = 683.33 to 1066.67 pixels.
    # Let's align the band so that the image falls inside it!
    # If band is (600, 1100), height=500.
    # Image in pixels: y0_pixels = 328 * 150/72 = 683.33. y1_pixels = 512 * 150/72 = 1066.67.
    # Both are fully inside the band!
    band_y_range = (600, 1100)
    band_image = Image.new("RGB", (1275, 500), "white")

    # Let's give a Gemini band-relative bbox that overlaps with the image.
    # In band pixels: y0_pixels_overlap = 700 (which is 100 relative to band), y1_pixels_overlap = 1000 (which is 400 relative to band).
    # On 0-1000 scale relative to 500px band: ymin = (100/500)*1000 = 200.0, ymax = (400/500)*1000 = 800.0.
    # Let's use xmin=250.0, xmax=700.0.
    initial_band_bbox = [200.0, 250.0, 800.0, 700.0]

    dest_path = tmp_path / "crop.png"
    path, role = save_visual_crop(
        band_image,
        initial_band_bbox,
        dest=dest_path,
        page=page,
        band_y_range=band_y_range,
    )

    assert path.exists()
    assert role == "page_crop"


def test_snap_bbox_grows_to_labels_not_prose():
    class MockRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0 = x0
            self.y0 = y0
            self.x1 = x1
            self.y1 = y1
            self.width = x1 - x0
            self.height = y1 - y0

    class MockPage:
        def __init__(self):
            self.rect = MockRect(0, 0, 612, 792)
        def get_drawings(self):
            # Graph axis box: y = [200, 350], x = [150, 400]
            return [{"rect": MockRect(150.0, 200.0, 400.0, 350.0)}]
        def get_images(self):
            return []
        def get_text(self, mode):
            if mode == "blocks":
                # We have 3 blocks on the page:
                # 1. A short label "volume of acid" close to the graph axis
                # 2. A long question stem block close to the graph
                # 3. An option text block
                return [
                    (160.0, 355.0, 280.0, 370.0, "volume of acid", 0, 0),
                    (100.0, 150.0, 500.0, 190.0, "The graph below represents the change in pH during a titration of sodium hydroxide.", 1, 0),
                    (120.0, 410.0, 450.0, 440.0, "Which one of the following acid-alkali pairs could this graph represent?", 2, 0),
                ]
            return []

    page = MockPage()
    # Initial tight bbox (only covering the core of the graph axes, excluding text labels)
    tight_bbox = [
        (200.0 / 792.0) * 1000.0,
        (150.0 / 612.0) * 1000.0,
        (350.0 / 792.0) * 1000.0,
        (400.0 / 612.0) * 1000.0,
    ]

    snapped = snap_norm_bbox_to_drawings(page, tight_bbox)

    # Convert snapped back to points
    snapped_ymin_pts = (snapped[0] / 1000.0) * 792.0
    snapped_ymax_pts = (snapped[2] / 1000.0) * 792.0

    # The short label "volume of acid" (ymax = 370.0) should be SNAPPED!
    assert snapped_ymax_pts >= 370.0

    # The long question stem (ymin = 150.0) and option text (ymax = 440.0) should NOT be snapped!
    assert snapped_ymin_pts >= 195.0
    assert snapped_ymax_pts < 400.0


def test_snap_bbox_no_cascading_mcq_options():
    class MockRect:
        def __init__(self, x0, y0, x1, y1):
            self.x0 = x0
            self.y0 = y0
            self.x1 = x1
            self.y1 = y1
            self.width = x1 - x0
            self.height = y1 - y0

    class MockPage:
        def __init__(self):
            self.rect = MockRect(0, 0, 612, 792)
        def get_drawings(self):
            # Chromatogram box: y = [200, 350], x = [150, 400]
            return [{"rect": MockRect(150.0, 200.0, 400.0, 350.0)}]
        def get_images(self):
            return []
        def get_text(self, mode):
            if mode == "blocks":
                # Layout with MCQ choices close to each other, forming a chain:
                # - "start line" (y = [345, 355]) is close to chromatogram bottom (y = 350)
                # - Option A (y = [370, 380]) is close to "start line"
                # - Option B (y = [390, 400]) is close to Option A
                # - Option C (y = [410, 420]) is close to Option B
                # Without our fix, the snapping box would recursively merge "start line" -> Option A -> Option B -> Option C!
                # With our fix, only "start line" should merge because Option A is NOT close to the core chromatogram box ([200, 350]).
                return [
                    (160.0, 345.0, 280.0, 355.0, "start line", 0, 0),
                    (160.0, 370.0, 280.0, 380.0, "A  K101, K122", 1, 0),
                    (160.0, 390.0, 400.0, 400.0, "B  K101, K151", 2, 0),
                    (160.0, 410.0, 400.0, 420.0, "C  K101, K151, K122", 3, 0),
                ]
            return []

    page = MockPage()
    tight_bbox = [
        (200.0 / 792.0) * 1000.0,
        (150.0 / 612.0) * 1000.0,
        (350.0 / 792.0) * 1000.0,
        (400.0 / 612.0) * 1000.0,
    ]

    snapped = snap_norm_bbox_to_drawings(page, tight_bbox)

    # Convert snapped back to points
    snapped_ymax_pts = (snapped[2] / 1000.0) * 792.0

    # "start line" (ymax = 355.0) should be merged
    assert snapped_ymax_pts >= 355.0

    # Option A (ymin = 370.0) and other options should NOT be merged!
    assert snapped_ymax_pts < 370.0


def test_option_schema_self_healing():
    from pathlib import Path
    from PIL import Image
    from questbank.types.canonical import GeminiPageQuestion, TextOptions, MathOptions, TextOptionItem, MathOptionItem, GeminiPageParseResult
    from questbank.parsers.chemistry.parse_mcq_gemini import _materialize_question
    from questbank.llm.gemini_client import _validate_structured_json
    import json

    # 0. Exact production failure from page 5 (latex under text mode)
    page5_json = json.dumps({
        "questions": [{
            "question_ref": "7",
            "content": [{"type": "text", "value": "Which gas?"}],
            "options": {
                "mode": "text",
                "items": [
                    {"label": "A", "content": "O2"},
                    {"label": "B", "latex": "F_2"},
                    {"label": "C", "latex": "NO_2"},
                    {"label": "D", "latex": "SO_2"},
                ],
            },
        }]
    })
    parsed = _validate_structured_json(GeminiPageParseResult, page5_json)
    assert parsed.questions[0].options.items[1].content == "F_2"

    # Invalid mode tag (page 6 style)
    page6_json = json.dumps({
        "questions": [{
            "question_ref": "8",
            "content": [],
            "options": {
                "mode": "chemistry",
                "items": [
                    {"label": "A", "latex": "H_2"},
                    {"label": "B", "latex": "O_2"},
                    {"label": "C", "latex": "N_2"},
                    {"label": "D", "latex": "Cl_2"},
                ],
            },
        }]
    })
    parsed6 = _validate_structured_json(GeminiPageParseResult, page6_json)
    assert parsed6.questions[0].options.mode == "math"

    # 1. Test TextOptions self-healing: text options mode but outputs latex field
    bad_text_item = TextOptionItem(label="B", content=None, latex="F_2")
    bad_text_options = TextOptions(mode="text", items=[bad_text_item])
    gemini_q1 = GeminiPageQuestion(
        question_ref="5",
        content=[],
        options=bad_text_options,
    )
    
    dummy_img = Image.new("RGB", (100, 100))
    materialized_1 = _materialize_question(
        gemini_q1,
        page_number=5,
        page_image=dummy_img,
        assets_dir=Path("output/temp_test_assets"),
    )
    assert materialized_1.options.items[0].content == "F_2"

    # 2. Test MathOptions self-healing: math options mode but outputs content field
    bad_math_item = MathOptionItem(label="C", content="NO_2", latex=None)
    bad_math_options = MathOptions(mode="math", items=[bad_math_item])
    gemini_q2 = GeminiPageQuestion(
        question_ref="6",
        content=[],
        options=bad_math_options,
    )
    materialized_2 = _materialize_question(
        gemini_q2,
        page_number=6,
        page_image=dummy_img,
        assets_dir=Path("output/temp_test_assets"),
    )
    assert materialized_2.options.items[0].latex == "NO_2"





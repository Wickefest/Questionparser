"""Gemini question reconciliation: page image → canonical questions."""

from __future__ import annotations

import json
from typing import Any

from questbank.llm.gemini_client import GeminiClient
from questbank.types.canonical import GeminiPageParseResult

QUESTION_RECONCILE_INSTRUCTION = """\
You are reconstructing Chemistry examination questions from their original source.

You receive:
1. A rendered image of the examination page (AUTHORITATIVE), and
2. Optional Docling / layout extraction (SUPPORTING EVIDENCE ONLY).

CRITICAL COMPARISON RULE:
- Use Docling/layout to help locate text, tables, and candidate figure regions.
- ALWAYS verify every claim against the rendered page image.
- When Docling and the image disagree (reading order, missing text, truncated
  figure boxes, wrong image regions), FOLLOW THE IMAGE.
- Never copy Docling/PDF image bboxes blindly — they are often truncated,
  especially for vector-drawn graphs.

Extract all question text, tables, maths, chemistry notation, and visuals
needed to answer each question faithfully.

Produce the faithful final representation of every examination question visible
in the supplied source.

================================================================
VISION PARSER SPECIFICATION RULES (CRITICAL MANDATE)
================================================================

Rule 1: Numbering Suppression: Strip ONLY the main question identifier or main index (such as the bold "Q1", "Q2", "1.", "2." preceding the question) and option prefix labels (like "A", "B", "C", "D" at the start of multiple choice items) completely from all text output fields. Do NOT strip any internal numbers, values, concentrations, masses, or subscripts (e.g., "0.1 mol/dm3", "25 cm3", "Mr = 34.1", etc.) from within the question stem or options. Any numbers or quantities inside the question prose must be preserved exactly as they are in the original source. The question identifier is only stored in "question_ref" for answer-key matching; it must not prefix any parsed text field.

Rule 2: Tight Asset Bounding: When detecting figures, graphs, data tables, or diagrams, compute bounding box coordinates that isolate only the visual asset itself (matching the tight crop of a chromatography strip, visual option, or data grid). Never capture surrounding text stems, headers, or full-page frames. Keep bboxes extremely tight around the borders of the visual elements while preserving essential labels inside it.

Rule 3: Exhaustive Recall: Scan every block systematically. Do not skip marginal items, secondary diagrams, or low-contrast sub-questions. Every visual element must be mapped to its corresponding text block without omission.

================================================================
CRITICAL READING-ORDER REQUIREMENT
================================================================

The final content[] array MUST represent the semantic reading order of
the ORIGINAL examination page.

Do NOT preserve Docling reading order when it conflicts with the visible
page layout.

Use the rendered page image as the authoritative source for block order.

For each question:

1. Visually locate all elements belonging to the question.
2. Determine their top-to-bottom semantic reading order.
3. Return content blocks in exactly that order.
4. Insert image/table/math blocks at the point where they occur in the
   question.

Example:

If the source visually shows:

    Introductory sentence
    [diagram]
    Question sentence

return:

    content: [
        text,
        image,
        text
    ]

NOT:

    content: [
        text,
        text,
        image
    ]

Do not group all textual extraction before images.
Do not group all images after text.

The goal is to reproduce the QUESTION'S LOGICAL LAYOUT, not merely to
recover all of its information.

================================================================
IMPORTANT VISUAL / CROP RULE (MUST RETAIN ALL NEEDED INFORMATION)
================================================================

A visual must be included only when it contains information necessary
for the question.

If included, crop ONLY the meaningful visual itself — never the whole
question stem prose — BUT the crop must retain EVERY piece of information
required to use that visual.

CRITICAL for graphs / charts / plots:

The crop is INVALID unless ALL of the following are inside the box:
- full plotted data (curves, bars, points, lines)
- BOTH axes (x and y), including axis lines
- ALL axis titles / quantity labels (e.g. "volume of gas / cm³", "time / s",
  "temperature / °C", abundance, concentration, etc.)
- ALL tick marks and numeric tick labels
- legends, keys, and unit annotations that belong to the figure
- any labels printed on/near the plotted region that are part of the graph

FORBIDDEN graph crops:
- only the left axis, or only the curve without titles
- a box that cuts off "volume", units, tick numbers, or the last data point
- a Docling/PDF truncated image region that misses axis titles

Before finalising a graph bbox, mentally check: "Can a student read the
quantities and units from this crop alone?" If no, EXPAND the box.

CRITICAL for apparatus / structures / diagrams:
- Include the complete drawing plus any caption or labels that belong to it
  (e.g. "water", gas syringe markings) needed to answer.

CRITICAL for composite_visual OPTIONS (diagram/structure choices):
- Crop the ENTIRE A–D answer region as ONE asset.
- The crop MUST include the option letters A, B, C, and D themselves
  (headers or labels), plus every choice drawing/caption in that block.
- Do NOT omit A/B/C/D letters from the options crop.
- Do NOT split into four separate crops.

Other visual rules:
1. Derive bounding boxes from the RENDERED PAGE IMAGE on a 0–1000 scale
   [ymin, xmin, ymax, xmax] relative to that image — not PDF points, not a
   layout-engine list.
2. The box MUST NOT include neighbouring question stems or unrelated prose
   (except A–D labels when cropping composite options).
3. Content image blocks use type "image" (never "asset").
4. stem_visuals[].bbox and image.bbox MUST be exactly four numbers
   [ymin, xmin, ymax, xmax] (never 5 or 6 values) and must match the same
   complete visual.

Bad crop example (FORBIDDEN): only the left axis of a bar chart, or a box
that cuts through stem sentences and cuts off the plotted data / axis titles.

For a meaningful diagram/graph inside the stem, return a bounding box for
that visual in stem_visuals. Do NOT invent stem visuals for ordinary text
questions.

================================================================
REFERENCE-LABEL QUESTIONS
================================================================

Some questions have a diagram labelled A, B, C, D and ask the student to
choose one of those labels.

For these questions:

- keep the labelled diagram as an image block in the correct content
  position
- text answer options A/B/C/D are valid
- do not crop A/B/C/D separately
- do not invent descriptions for what A/B/C/D represent

================================================================
SOURCE FIDELITY
================================================================

The final question must contain ALL information required to answer the
source question and NO unrelated information.

Preserve:
- exact source wording wherever readable
- option labels
- table row/column relationships
- mathematical notation
- chemical notation
- units
- diagrams / graphs / chemical structures
- captions and labels needed to answer the question

Exclude / Do NOT:
- the question number itself (e.g., do NOT include "1" or "1." or "Q1" at the start of the extracted stem text blocks; keep only the question content. The question number must be separate and not joined together with the question prose).
- neighbouring questions
- headers, footers, school branding, page numbers
- instructions that are not part of the question
- answer information
- model-generated explanations
- content inferred rather than visible in the source
- solve the question
- paraphrase the source
- add educational context
- "fix" the examiner's wording
- invent unreadable content

================================================================
NOTATION / CONTENT BLOCK TYPES (CRITICAL)
================================================================

Choose the content block type by what the source actually needs:

- Ordinary prose / instructions / legends → type "text"
  Use plain UTF-8 text. Do NOT wrap ordinary sentences in LaTeX.

- Numbered / bulleted statement lists (VERY COMMON in Chemistry MCQs) → type "list"
  Example source:
      Which of the following is/are true about aqueous methylamine?
      1. It turns litmus paper blue.
      2. It consists of CH3NH3+ and OH- ions only.
      3. It forms a blue precipitate with copper(II) sulfate.
  You MUST encode this as:
      content: [
        { "type": "text", "value": "Which of the following is/are true about aqueous methylamine?" },
        {
          "type": "list",
          "ordered": true,
          "items": [
            "It turns litmus paper blue.",
            "It consists of CH3NH3+ and OH- ions only.",
            "It forms a blue precipitate with copper(II) sulfate."
          ]
        }
      ]
  Rules for lists:
  - Put the lead-in question sentence in a text block.
  - Put each numbered/bulleted statement as one list item (WITHOUT the leading "1." / "2." / "•").
  - Set ordered=true for 1/2/3-style statements; ordered=false for bullet points.
  - Do NOT flatten numbered statements into one long text block when they are visually a list.
  - A–D choices like "1 only", "1 and 2 only" still belong in options (usually mode=text).

- Mathematical notation where layout carries meaning → type "math"
  Put LaTeX in the latex field. Use ONLY for:
  fractions, powers, roots, algebraic expressions, equations,
  matrices, and nuclide notation when spatial super/subscript
  placement matters.

- Chemical formulae, ions, chemical equations → type "chemistry"
  Return the INNER mhchem body only (no \\ce{...} wrapper), e.g.:
      H2SO4
      SO4^2-
      NH4+
      CH4 + 2O2 -> CO2 + 2H2O

Rules:

- Do NOT convert ordinary prose into LaTeX or chemistry blocks.
- Do NOT convert simple units (cm3, s, °C, pH) into separate math
  blocks unless formatting cannot be faithfully represented as plain
  Unicode text (UTF-8 in a text block is preferred).
- INLINE FORMULAS MUST STAY IN ONE TEXT BLOCK (CRITICAL):
  If a chemistry formula or short math expression appears INSIDE a
  sentence (e.g. "Two gases, H2S (Mr = 34.1) and SO2 (Mr = 64.1) were
  released..."), keep the WHOLE sentence as a single type="text" block.
  Write formulas in plain text / Unicode (H2S, SO2, Mr = 34.1).
  NEVER emit alternating text + chemistry + math + text fragments for
  one continuous paragraph — the UI stacks each block vertically and
  the sentence will look broken.
- Separate type="chemistry" / type="math" blocks ONLY for standalone
  display equations or reactions that sit alone on their own visual
  line(s) in the paper (e.g. a Haber-process equation on its own row).
- If mathematical or chemical notation cannot be read confidently
  from the source: do NOT invent it. Keep the relevant source visual
  (stem_visuals / image) and set requires_review=true.

================================================================
TABLE / GRID OPTION QUESTIONS (CRITICAL)
================================================================

Many Chemistry MCQs present A/B/C/D as a comparison table or tick/cross
grid (rows labelled A–D; columns for substances, properties, etc.).

For these questions you MUST:

1. Set options.mode = "table"
2. Put header labels in options.columns (exclude the A–D label column)
3. Put each choice row in options.rows as:
      { "label": "A"|"B"|"C"|"D", "cells": [ ...column values... ] }
4. Preserve every cell exactly (√, ×, ticks, crosses, yes/no, blanks)

You MUST NOT:

- put the A–D choice table into content[] AND also invent stub text options
- use options.mode = "text" with placeholder items like
      { "label": "A", "content": "A" }
- invent missing ticks/crosses or rearrange columns
- flatten the grid into prose

Where the option table goes:

- The A–D choice grid belongs in options (mode=table), not as a duplicate
  content table.
- Stem sentences and any legend explaining symbols (e.g. "√ can be obtained…")
  belong in content[] in visual reading order.
- Typical order:

      content: [ stem text…, legend text if present ]
      options: { mode: "table", columns: [...], rows: [A,B,C,D] }

If a table in the stem is NOT the A–D answer choices (data for the question
before the options), keep that table as a content table block and still
encode the actual A–D choices correctly in options.

================================================================
OPTION MODE SELECTION (STRICT PYDANTIC CONSTRAINTS)
================================================================

Represent answer options as exactly one of:
text, math, table, composite_visual, mixed.

Choose the mode that matches the VISIBLE form of A–D on the page. You MUST follow these exact Pydantic fields based on the chosen mode:

1. Mode: "text"
   - Every item MUST have exactly these fields: "label" (e.g., "A", "B", "C", "D") and "content" (string).
   - CRITICAL: "content" is REQUIRED. There is NO "latex" field in "text" mode! Do NOT output "latex" inside a "text" mode item (e.g. do NOT output {"label": "B", "latex": "F_2"}). If you have small LaTeX/chemical subscript options, either write them as simple plain text in the "content" field (e.g., {"label": "B", "content": "F2"}) or use Mode: "math".

2. Mode: "math"
   - Every item MUST have exactly these fields: "label" and "latex" (string).
   - CRITICAL: "latex" is REQUIRED. There is NO "content" field in "math" mode!

3. Mode: "mixed"
   - Items can have optional "content", "latex", and "asset" fields.

4. Mode: "table"
   - Used for A–D comparison grids or tick/cross tables. Use "columns" and "rows".

5. Mode: "composite_visual"
   - Used when options are diagrams/structures. Crop the options block and provide "asset" and "bbox".

Choose carefully:
- simple text / prose / plain chemical formulas → text (using "content" field!)
- equations / subscripts where layout carries meaning → math (using "latex" field!)
- A–D comparison / tick grid → table
- diagrams/structures as choices → composite_visual (one A–D crop)
- mixed modalities per option → mixed

For tables that are NOT options, preserve every row and every column in a
content table block. Never flatten H2 | O2 into H2O2.

Bounding boxes use normalized coordinates [ymin, xmin, ymax, xmax] on a 0-1000 scale.

Set requires_review=true only when the source cannot be reconstructed safely.
Return only data conforming to the supplied structured-output schema.
"""


def serialize_docling_page_context(blocks: list[dict[str, Any]] | dict[str, Any] | str) -> str:
    if isinstance(blocks, str):
        return blocks
    return json.dumps(blocks, ensure_ascii=False, indent=2)


def parse_page_questions(
    client: GeminiClient,
    *,
    page_image,
    page_number: int,
    docling_context: list[dict[str, Any]] | dict[str, Any] | str | None = None,
    band_hint: str | None = None,
    max_attempts: int | None = None,
) -> GeminiPageParseResult:
    """Parse one page image into structured questions (vision-first)."""
    chunks = [
        QUESTION_RECONCILE_INSTRUCTION,
        f"Page number: {page_number}",
    ]
    if band_hint:
        chunks.append(
            "This image is a VERTICAL CROP of one (or a few) MCQ(s) from the page, "
            f"not the full page. Focus on question {band_hint} if visible. "
            "Extract every complete MCQ visible in this crop. "
            "Bounding boxes are relative to THIS crop image (0–1000)."
        )
    else:
        chunks.append(
            "Extract every visible MCQ on this page. "
            "Compare Docling/layout hints (if provided) with the page image; "
            "the image wins on conflicts. "
            "For graphs, expand bboxes until axis titles/units/ticks are fully inside. "
            "For composite_visual options, include A, B, C, D letters in the crop."
        )
    if docling_context is not None:
        context_json = serialize_docling_page_context(docling_context)
        chunks.append(
            "Docling/layout extraction for THIS page (SUPPORTING — verify against image).\n"
            "Use it to find candidates, then CORRECT truncated image boxes using the PNG:\n"
            f"{context_json}"
        )
    prompt = "\n\n".join(chunks) + "\n"
    return client.generate_structured(
        prompt=prompt,
        response_model=GeminiPageParseResult,
        image=page_image,
        max_attempts=max_attempts,
    )


from pydantic import BaseModel, Field


class GeminiBBox(BaseModel):
    """Normalized coordinates on a 0-1000 scale: [ymin, xmin, ymax, xmax]."""
    ymin: float
    xmin: float
    ymax: float
    xmax: float


class GeminiCropVerificationResult(BaseModel):
    """The result of verifying a cropped visual using Gemini."""
    valid: bool = Field(description="True if the crop is valid, complete, and contains all needed info; False otherwise.")
    issue: str | None = Field(default=None, description="If invalid, the exact reason why (e.g. axis title cut off, missing choices).")
    corrected_bbox: GeminiBBox | None = Field(default=None, description="If invalid, the correct bounding box coordinates relative to the original page image (0-1000).")


CROP_VERIFICATION_INSTRUCTION = """You are verifying whether a proposed image crop faithfully contains all visual information required for an examination question.

Please check carefully:
1. Complete graph / diagram: All curves, lines, data points, arrows, and ticks must be fully included.
2. Labels and titles: Axis titles (e.g., "volume / cm3" or "time / s"), legends, indicator labels, apparatus labels, and units must be completely within the crop.
3. Vertical & Horizontal Adjacent Text: For vertical chromatography diagrams, all start lines, solvent front lines, numbers (e.g. 12, 8, 4), adjacent arrows, and text labels written immediately to the left or right of the diagram (e.g., "solvent front", "start line", "Rf values") must be fully visible and NOT clipped.
4. Bottom Text: Any text labels or identifiers written immediately underneath the diagrams or tables (e.g. "substance 1", "substance 2", "mixture X") must be completely within the crop and NOT cut off.
5. Option letters: For composite A-D choice crops, the labels A, B, C, D themselves must be fully visible within the crop.
6. NO Question Prose/Stem: For standard graphs, curves, and diagrams inside the stem, the crop must tightly isolate ONLY the visual asset itself. It must NOT include the question stem sentences, introductory prose, or the multiple-choice option rows in the cropped image. The crop is invalid if it contains large blocks of ordinary question text above, below, or to the side of the diagram.
7. Neighboring context: Do not include neighboring question stems or text from OTHER questions.

You are given:
- Image 1: The original single-page image (or band crop image) Gemini inspected.
- Image 2: The proposed crop image.

Return whether the proposed crop is valid. If valid=False, provide the corrected bounding box relative to the original image (Image 1) on a 0-1000 scale.
"""


def verify_crop_with_gemini(
    client: GeminiClient,
    *,
    original_image,
    crop_image,
    question_context: str | None = None,
) -> GeminiCropVerificationResult:
    """Run Gemini verification to check if the proposed crop is complete and correct."""
    prompt = CROP_VERIFICATION_INSTRUCTION
    if question_context:
        prompt += f"\nQuestion reference / context: {question_context}"
    return client.generate_structured(
        prompt=prompt,
        response_model=GeminiCropVerificationResult,
        images=[original_image, crop_image],
    )


__all__ = [
    "QUESTION_RECONCILE_INSTRUCTION",
    "parse_page_questions",
    "serialize_docling_page_context",
    "GeminiBBox",
    "GeminiCropVerificationResult",
    "verify_crop_with_gemini",
]

"""Gemini answer / mark-scheme parsing (source reconstruction only)."""

from __future__ import annotations

import json
import re
from typing import Any

from questbank.llm.gemini_client import GeminiClient
from questbank.types.canonical import GeminiAnswerPageResult, ParsedAnswer

ANSWER_PARSE_INSTRUCTION = """\
You are extracting official MCQ answers from a Chemistry mark scheme / answer key page.

You receive an image of the original answer document page and optional
native page text from PyMuPDF (not Docling).

Extract only what the mark scheme states:
- question reference
- correct option letter (A/B/C/D)
- official explanation text IF AND ONLY IF printed on the page
- explanation_source according to the provenance rules below

================================================================
ANSWER PROVENANCE (CRITICAL — ZERO TOLERANCE)
================================================================

Default assumption for MCQ answer keys:
  MOST keys are letter-only tables such as "1 B  2 A  3 C …".
  For those, EVERY answer MUST have:
      explanation = null
      explanation_source = null

If an explanation is EXPLICITLY printed in the supplied mark scheme for that
question (visible on the page image / page text), preserve the wording
exactly and set:
      explanation_source = "mark_scheme"

If only the answer letter is supplied:
      explanation = null
      explanation_source = null

FORBIDDEN (always):
- Never generate, invent, infer, or reconstruct an explanation.
- Never solve the chemistry question to fill a missing explanation.
- Never copy an option stem from the question paper into explanation.
- Never write reactivity-series / teaching commentary and label it mark_scheme.
- Never paraphrase, expand, or "improve" printed mark-scheme wording.
- Never set explanation_source = "mark_scheme" unless the explanation text
  is literally present on this mark-scheme page.

If unsure whether an explanation is printed → set explanation = null and
explanation_source = null.

================================================================
SOURCE FIDELITY
================================================================

- Do NOT invent explanations.
- Do NOT add educational commentary.
- Preserve official wording of any explanation that is actually printed.
- Ignore headers, footers, school branding, topic revision checklists, and
  structured Paper 2 mark schemes that are not MCQ keys.
- If the page only lists letters (e.g. "1 B  2 A"), set explanation to null
  and explanation_source to null for ALL answers on that page.

Return only data conforming to the supplied structured-output schema.
"""

_LETTER_PAIR = re.compile(
    r"(?:^|[^\d])(\d{1,2})\s*[.:)\-]?\s*([ABCD])\b",
    re.IGNORECASE,
)
_EXPLANATIONISH = re.compile(
    r"\b(because|therefore|hence|reactivity|explain|correct because|"
    r"statement|oxide|carbon can|below carbon|above carbon)\b",
    re.IGNORECASE,
)


def _looks_like_letter_only_key(page_text: str | None) -> bool:
    """True when the page text is dominated by Qn + letter pairs."""
    if not page_text or not page_text.strip():
        return False
    pairs = _LETTER_PAIR.findall(page_text)
    if len(pairs) < 5:
        return False
    # Letter-only keys rarely contain long explanatory sentences.
    long_lines = [
        line.strip()
        for line in page_text.splitlines()
        if len(line.strip()) > 80 and _EXPLANATIONISH.search(line)
    ]
    return len(long_lines) == 0


def sanitize_parsed_answers(
    answers: list[ParsedAnswer],
    *,
    page_text: str | None = None,
) -> list[ParsedAnswer]:
    """Drop invented explanations; enforce letter-only key provenance."""
    force_null = _looks_like_letter_only_key(page_text)
    cleaned: list[ParsedAnswer] = []
    for ans in answers:
        explanation = ans.explanation
        source = ans.explanation_source
        if force_null:
            explanation = None
            source = None
        elif not explanation or not str(explanation).strip():
            explanation = None
            source = None
        elif source != "mark_scheme":
            # Unverified provenance → discard rather than invent mark_scheme.
            explanation = None
            source = None
        cleaned.append(
            ParsedAnswer(
                question_ref=ans.question_ref,
                answer=ans.answer,
                explanation=explanation,
                explanation_source=source,
            )
        )
    return cleaned


def parse_answer_page(
    client: GeminiClient,
    *,
    page_image=None,
    page_number: int,
    docling_context: list[dict[str, Any]] | dict[str, Any] | str | None = None,
    page_text: str | None = None,
) -> GeminiAnswerPageResult:
    chunks: list[str] = [ANSWER_PARSE_INSTRUCTION, f"Page number: {page_number}"]
    if page_text:
        chunks.append(f"Extracted page text:\n{page_text}")
    if docling_context is not None:
        if isinstance(docling_context, str):
            ctx = docling_context
        else:
            ctx = json.dumps(docling_context, ensure_ascii=False, indent=2)
        chunks.append(f"Docling candidate extraction:\n{ctx}")

    prompt = "\n\n".join(chunks)
    result = client.generate_structured(
        prompt=prompt,
        response_model=GeminiAnswerPageResult,
        image=page_image,
    )
    return GeminiAnswerPageResult(
        answers=sanitize_parsed_answers(result.answers, page_text=page_text)
    )


__all__ = [
    "ANSWER_PARSE_INSTRUCTION",
    "parse_answer_page",
    "sanitize_parsed_answers",
]

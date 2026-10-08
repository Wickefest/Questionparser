"""Convert StructuredQuestion → factory-ready StructuredCompleteRecord."""

from __future__ import annotations

from questbank.parsers.chemistry.content_blocks import (
    attach_visual_paths_to_content,
    sync_structured_content_assets,
)
from questbank.parsers.chemistry.parse_structured_questions_gemini import (
    flatten_structured_parts,
    normalize_structured_part_label,
)
from questbank.types.canonical import (
    ImageBlock,
    StructuredAnswerPart,
    StructuredAnswerRecord,
    StructuredCompleteRecord,
    TextBlock,
)
from questbank.types.question import ContentBlock as LayoutContentBlock
from questbank.types.structured import StructuredPart, StructuredQuestion


def structured_question_to_record(
    question: StructuredQuestion,
    *,
    answer: StructuredAnswerRecord | None = None,
) -> StructuredCompleteRecord:
    """Map a package: content = stem + diagrams only; parts live in options."""
    synced = sync_structured_content_assets(question)

    # Shared package body only (stem + diagrams). Part prompts stay in options
    # so Factory review can keep the stem while navigating (a)/(i)/(ii)…
    raw_blocks: list = []
    if (synced.stem or "").strip():
        raw_blocks.append(LayoutContentBlock(type="text", text=synced.stem.strip()))
    if synced.visual and synced.visual.required:
        raw_blocks.append(LayoutContentBlock(type="diagram", requires_visual=True))
    if synced.visual and synced.visual.assets:
        raw_blocks = attach_visual_paths_to_content(raw_blocks, synced.visual.assets)

    content: list[TextBlock | ImageBlock] = []
    for block in raw_blocks:
        data = block.model_dump() if hasattr(block, "model_dump") else dict(block)
        btype = data.get("type") or "text"
        if btype == "text":
            text = data.get("text") or data.get("value") or ""
            if str(text).strip():
                content.append(TextBlock(value=str(text)))
        elif btype in {"diagram", "image"}:
            asset = (
                data.get("asset_path")
                or data.get("assetPath")
                or data.get("asset")
                or data.get("asset_id")
                or data.get("assetId")
            )
            if asset:
                content.append(
                    ImageBlock(asset=str(asset).replace("\\", "/").rsplit("/", 1)[-1])
                )

    if not content and (synced.stem or "").strip():
        content = [TextBlock(value=synced.stem.strip())]

    requires_review = bool(
        synced.validation and synced.validation.status == "review"
    )
    # Empty package body → always needs review
    if not content and not (synced.parts or []):
        requires_review = True

    return StructuredCompleteRecord(
        question_ref=str(synced.question_number),
        question_type="structured",
        stem=synced.stem or "",
        parts=synced.parts or [],
        content=content,  # type: ignore[arg-type]
        marks_total=synced.marks_total,
        answer=answer,
        status="NEEDS_REVIEW" if requires_review else "DRAFT",
        source_page=synced.source.page_start if synced.source else None,
        source_regions=[],
        requires_review=requires_review,
    )


def structured_options_payload(record: StructuredCompleteRecord) -> dict:
    """options JSON for drafts: nested parts + flat navigation order."""
    parts = record.parts or []
    return {
        "mode": "structured",
        "stem": record.stem or "",
        "parts": [_dump_part(p) for p in parts],
        "navigationOrder": flatten_structured_parts(parts),
        "marksTotal": record.marks_total,
    }


def _dump_part(part: StructuredPart) -> dict:
    kind = getattr(part, "response_kind", None)
    if not kind:
        from questbank.parsers.chemistry.parse_structured_questions_gemini import (
            _infer_response_kind,
        )

        kind = _infer_response_kind(part.prompt or "")
    return {
        "label": part.label,
        "prompt": part.prompt or "",
        "marks": part.marks,
        "responseKind": kind,
        "visuals": list(part.visuals or []),
        "children": [_dump_part(c) for c in (part.children or [])],
    }


def empty_answer_from_parts(
    question_ref: str,
    parts: list[StructuredPart],
) -> StructuredAnswerRecord:
    """Build empty answer slots for navigable parts only (skip empty section shells)."""
    out: list[StructuredAnswerPart] = []
    for item in flatten_structured_parts(parts):
        out.append(
            StructuredAnswerPart(
                label=normalize_structured_part_label(item["label"]),
                marks_max=item.get("marks"),
                accepted_answers=[],
                notes=None,
            )
        )
    return StructuredAnswerRecord(question_ref=question_ref, parts=out)


def align_part_marks(parts: list[StructuredPart], answer: StructuredAnswerRecord) -> None:
    """Fill missing question-part marks from the mark scheme."""
    by_label = {
        normalize_structured_part_label(p.label): p
        for p in (answer.parts or [])
    }

    def walk(items: list[StructuredPart], prefix: str = "") -> None:
        for part in items or []:
            label = part.label if not prefix else f"{prefix}({part.label})"
            key = normalize_structured_part_label(label)
            ans = by_label.get(key)
            if ans is not None and ans.marks_max is not None:
                if part.marks is None or part.marks < ans.marks_max:
                    part.marks = ans.marks_max
            if part.children:
                walk(part.children, prefix=label)

    walk(parts)


def iter_part_marks(parts: list[StructuredPart]):
    for part in parts or []:
        yield part
        yield from iter_part_marks(list(part.children or []))


def answer_covers_question_parts(
    parts: list[StructuredPart], answer: StructuredAnswerRecord
) -> bool:
    """False when the mark scheme grades a part the question never extracted.

    The mark scheme parser and the question parser run independently — a
    part label present in ``answer.parts`` but missing from the question's
    own navigable parts means the visible question content is silently
    incomplete (a student would see no prompt for something being graded),
    even though nothing else about the record looks wrong.
    """
    question_labels = {
        normalize_structured_part_label(item["label"])
        for item in flatten_structured_parts(parts or [])
    }
    answer_labels = {
        normalize_structured_part_label(p.label) for p in (answer.parts or [])
    }
    return answer_labels <= question_labels


__all__ = [
    "align_part_marks",
    "answer_covers_question_parts",
    "empty_answer_from_parts",
    "iter_part_marks",
    "structured_options_payload",
    "structured_question_to_record",
]

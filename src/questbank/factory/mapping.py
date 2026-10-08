"""Pure mapping helpers for canonical content/options → persisted JSON."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from questbank.factory.enums import (
    AssetRolePrisma,
    EnumMappingError,
    map_draft_status,
    map_explanation_source,
    map_question_type,
)
from questbank.types.canonical import (
    CompleteQuestionRecord,
    CompositeVisualOptions,
    ImageBlock,
    MixedOptions,
    StructuredCompleteRecord,
)


class RecordValidationError(ValueError):
    """Per-question validation failure during factory mapping."""


def new_id() -> str:
    return str(uuid4())


def collect_local_assets(
    record: CompleteQuestionRecord | StructuredCompleteRecord,
) -> list[dict[str, Any]]:
    """Return ordered asset descriptors referenced by a canonical record.

    Each item: {key, filename, role, bbox, source_page}
    key is stable within the question (for rewrite).
    """
    assets: list[dict[str, Any]] = []
    for index, block in enumerate(record.content):
        if isinstance(block, ImageBlock) and block.asset:
            assets.append(
                {
                    "key": f"content:{index}",
                    "filename": _basename(block.asset),
                    "role": "STEM_IMAGE",
                    "bbox": block.bbox,
                    "source_page": record.source_page,
                }
            )

    if isinstance(record, StructuredCompleteRecord):
        idx = 0
        for filename, role in _iter_structured_filenames(record):
            assets.append(
                {
                    "key": f"structured:{idx}:{filename}",
                    "filename": _basename(filename),
                    "role": "STEM_IMAGE" if role == "stem" else "SOURCE_CROP",
                    "bbox": None,
                    "source_page": record.source_page,
                }
            )
            idx += 1
        return assets

    opts = record.options
    if isinstance(opts, CompositeVisualOptions) and opts.asset:
        assets.append(
            {
                "key": "options:composite",
                "filename": _basename(opts.asset),
                "role": "COMPOSITE_OPTIONS",
                "bbox": opts.bbox,
                "source_page": record.source_page,
            }
        )
    elif isinstance(opts, MixedOptions) and opts.asset:
        assets.append(
            {
                "key": "options:mixed",
                "filename": _basename(opts.asset),
                "role": "COMPOSITE_OPTIONS",
                "bbox": opts.bbox,
                "source_page": record.source_page,
            }
        )
    return assets


def _visual_filename(item: Any) -> str | None:
    if isinstance(item, dict):
        name = item.get("asset") or item.get("filename")
        return _basename(str(name)) if name else None
    return None


def _iter_structured_filenames(record: StructuredCompleteRecord) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(name: str | None, role: str) -> None:
        if not name:
            return
        base = _basename(name)
        if not base or base in seen:
            return
        seen.add(base)
        found.append((base, role))

    def walk_parts(items: list[Any]) -> None:
        for part in items or []:
            visuals = getattr(part, "visuals", None)
            if visuals is None and isinstance(part, dict):
                visuals = part.get("visuals")
            for vis in visuals or []:
                add(_visual_filename(vis), "part")
            children = getattr(part, "children", None)
            if children is None and isinstance(part, dict):
                children = part.get("children")
            walk_parts(children or [])

    walk_parts(record.parts or [])
    if record.answer is not None:
        for part in record.answer.parts or []:
            for vis in getattr(part, "visuals", None) or []:
                add(_visual_filename(vis), "answer")
    return found


def _asset_id_for_filename(filename: str, asset_ids_by_key: dict[str, str]) -> str | None:
    base = _basename(filename)
    for key, asset_id in asset_ids_by_key.items():
        if key.endswith(f":{base}") or key == base:
            return asset_id
    return None


def _rewrite_visual_list(visuals: list[Any], asset_ids_by_key: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for vis in visuals or []:
        item = dict(vis) if isinstance(vis, dict) else {"asset": vis}
        name = item.get("asset") or item.get("filename")
        if name:
            asset_id = _asset_id_for_filename(str(name), asset_ids_by_key)
            if asset_id:
                item.pop("asset", None)
                item.pop("filename", None)
                item["asset_id"] = asset_id
        out.append(item)
    return out


def _rewrite_structured_option_assets(
    options: dict[str, Any],
    asset_ids_by_key: dict[str, str],
) -> dict[str, Any]:
    def walk(parts: list[Any]) -> list[Any]:
        out = []
        for part in parts or []:
            item = dict(part)
            if item.get("visuals"):
                item["visuals"] = _rewrite_visual_list(item["visuals"], asset_ids_by_key)
            if item.get("children"):
                item["children"] = walk(item["children"])
            out.append(item)
        return out

    options["parts"] = walk(options.get("parts") or [])
    nav = []
    for item in options.get("navigationOrder") or []:
        row = dict(item)
        if row.get("visuals"):
            row["visuals"] = _rewrite_visual_list(row["visuals"], asset_ids_by_key)
        nav.append(row)
    options["navigationOrder"] = nav
    return options


def rewrite_parts_answer_asset_ids(
    parts_answer: list[Any] | None,
    *,
    asset_ids_by_key: dict[str, str],
) -> list[Any] | None:
    if not parts_answer:
        return parts_answer
    out = []
    for part in parts_answer:
        item = dict(part)
        if item.get("visuals"):
            item["visuals"] = _rewrite_visual_list(item["visuals"], asset_ids_by_key)
        out.append(item)
    return out


def rewrite_content_asset_ids(
    content: list[dict[str, Any]],
    *,
    asset_ids_by_key: dict[str, str],
) -> list[dict[str, Any]]:
    """Replace local `asset` filenames with `asset_id`; drop bbox (kept on QuestionAsset)."""
    out: list[dict[str, Any]] = []
    for index, block in enumerate(content):
        item = deepcopy(block)
        if item.get("type") == "image":
            key = f"content:{index}"
            asset_id = asset_ids_by_key.get(key)
            if asset_id:
                item.pop("asset", None)
                item.pop("bbox", None)
                item["asset_id"] = asset_id
        out.append(item)
    return out


def rewrite_options_asset_ids(
    options: dict[str, Any],
    *,
    asset_ids_by_key: dict[str, str],
) -> dict[str, Any]:
    out = deepcopy(options)
    mode = out.get("mode")
    if mode == "structured":
        return _rewrite_structured_option_assets(out, asset_ids_by_key)
    if mode == "composite_visual":
        asset_id = asset_ids_by_key.get("options:composite")
        if asset_id:
            out.pop("asset", None)
            out.pop("bbox", None)
            out["asset_id"] = asset_id
    elif mode == "mixed":
        asset_id = asset_ids_by_key.get("options:mixed")
        if asset_id:
            out.pop("asset", None)
            out.pop("bbox", None)
            out["asset_id"] = asset_id
    return out


def map_question_core(
    record: CompleteQuestionRecord | StructuredCompleteRecord,
) -> dict[str, Any]:
    """Map scalar fields + enums; does not touch assets."""
    if isinstance(record, StructuredCompleteRecord) or getattr(
        record, "question_type", None
    ) == "structured":
        return map_structured_question_core(record)  # type: ignore[arg-type]

    try:
        question_type = map_question_type(record.question_type)
        status = map_draft_status(record.status)
    except EnumMappingError as exc:
        raise RecordValidationError(str(exc)) from exc

    return {
        "question_ref": record.question_ref,
        "question_type": question_type,
        "content": [b.model_dump() for b in record.content],
        "options": record.options.model_dump(),
        "source_page": record.source_page,
        "source_regions": [r.model_dump() for r in record.source_regions],
        "status": status,
        "requires_review": bool(record.requires_review),
        "validation_issues": None,
    }


def map_structured_question_core(record: StructuredCompleteRecord) -> dict[str, Any]:
    """Map structured (Paper 2) record into draft columns (parts live in options JSON)."""
    try:
        question_type = map_question_type("structured")
        status = map_draft_status(record.status)
    except EnumMappingError as exc:
        raise RecordValidationError(str(exc)) from exc

    from questbank.parsers.chemistry.structured_to_canonical import structured_options_payload

    return {
        "question_ref": record.question_ref,
        "question_type": question_type,
        "content": [b.model_dump() for b in record.content],
        "options": structured_options_payload(record),
        "source_page": record.source_page,
        "source_regions": [r.model_dump() for r in record.source_regions],
        "status": status,
        "requires_review": bool(record.requires_review),
        "validation_issues": None,
    }


def map_answer_record(
    record: CompleteQuestionRecord | StructuredCompleteRecord,
) -> dict[str, Any] | None:
    """Split nested answer → AnswerRecord fields. None if no answer payload."""
    if isinstance(record, StructuredCompleteRecord) or getattr(
        record, "question_type", None
    ) == "structured":
        return map_structured_answer_record(record)  # type: ignore[arg-type]

    if record.answer is None:
        return None
    try:
        explanation_source = map_explanation_source(record.answer.explanation_source)
    except EnumMappingError as exc:
        raise RecordValidationError(str(exc)) from exc

    explanation = record.answer.explanation
    # Never invent AI explanations; empty explanation clears source.
    if explanation is None or not str(explanation).strip():
        explanation = None
        explanation_source = None

    return {
        "question_ref": record.question_ref,
        "answer_value": record.answer.value,
        "parts_answer": None,
        "explanation": explanation,
        "explanation_source": explanation_source,
        "source_page": record.source_page,
    }


def map_structured_answer_record(
    record: StructuredCompleteRecord,
) -> dict[str, Any] | None:
    """Map structured mark-scheme parts → AnswerRecord.parts_answer.

    Labels are normalized to composite form (a(i), a(ii), b) so the review UI
    can match Official Answer rows to the active navigation part.
    """
    if record.answer is None:
        return None
    from questbank.parsers.chemistry.parse_structured_questions_gemini import (
        normalize_structured_part_label,
    )

    parts: list[Any] = []
    for p in record.answer.parts or []:
        data = p.model_dump(by_alias=True) if hasattr(p, "model_dump") else dict(p)
        label = normalize_structured_part_label(
            str(data.get("label") or getattr(p, "label", "") or "")
        )
        if not label:
            continue
        data["label"] = label
        parts.append(data)
    has_text = any(
        (isinstance(p, dict) and (p.get("accepted_answers") or p.get("acceptedAnswers")))
        for p in parts
    )
    return {
        "question_ref": record.question_ref,
        "answer_value": None,
        "parts_answer": parts,
        "explanation": None,
        "explanation_source": "MARK_SCHEME" if has_text else None,
        "source_page": record.source_page,
    }


def ensure_list_blocks(content: Any) -> Any:
    """Upgrade flat numbered/bulleted text into list blocks for API consumers.

    Existing bank/draft rows may still store methylamine-style statements as one
    text block; this normalizes them on read without requiring re-ingest.
    """
    from questbank.normalize.list_blocks import repair_list_content, split_numbered_statement_lists

    if not isinstance(content, list):
        return content
    # repair_list_content also runs split_numbered_statement_lists at the end
    upgraded = repair_list_content(content)
    out: list[Any] = []
    for block in upgraded:
        if hasattr(block, "model_dump"):
            out.append(block.model_dump(by_alias=True))
        else:
            out.append(block)
    return out


def _basename(path_or_name: str) -> str:
    name = path_or_name.replace("\\", "/").strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def role_type(role: str) -> AssetRolePrisma:
    allowed = {
        "STEM_DIAGRAM",
        "STEM_GRAPH",
        "STEM_IMAGE",
        "COMPOSITE_OPTIONS",
        "TABLE_FALLBACK",
        "SOURCE_CROP",
    }
    if role not in allowed:
        raise EnumMappingError(f"Unknown asset role: {role!r}")
    return role  # type: ignore[return-value]

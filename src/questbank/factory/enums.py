"""Explicit canonical-string → Prisma enum mapping (no silent coercion)."""

from __future__ import annotations

from typing import Literal

QuestionTypePrisma = Literal["MCQ", "STRUCTURED"]
DraftStatusPrisma = Literal["DRAFT", "NEEDS_REVIEW", "READY", "REJECTED", "APPROVED"]
ExplanationSourcePrisma = Literal["MARK_SCHEME", "AI"]
AssetRolePrisma = Literal[
    "STEM_DIAGRAM",
    "STEM_GRAPH",
    "STEM_IMAGE",
    "COMPOSITE_OPTIONS",
    "TABLE_FALLBACK",
    "SOURCE_CROP",
]


class EnumMappingError(ValueError):
    """Raised when a canonical enum value has no Prisma mapping."""


_QUESTION_TYPE = {
    "mcq": "MCQ",
    "structured": "STRUCTURED",
}

_DRAFT_STATUS = {
    "DRAFT": "DRAFT",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "READY": "READY",
    "REJECTED": "REJECTED",
    "APPROVED": "APPROVED",
}

_EXPLANATION_SOURCE = {
    "mark_scheme": "MARK_SCHEME",
    "ai": "AI",
}


def map_question_type(value: str) -> QuestionTypePrisma:
    key = (value or "").strip()
    mapped = _QUESTION_TYPE.get(key)
    if mapped is None:
        raise EnumMappingError(f"Unknown question_type: {value!r}")
    return mapped  # type: ignore[return-value]


def map_draft_status(value: str) -> DraftStatusPrisma:
    key = (value or "").strip()
    mapped = _DRAFT_STATUS.get(key)
    if mapped is None:
        raise EnumMappingError(f"Unknown draft status: {value!r}")
    return mapped  # type: ignore[return-value]


def map_explanation_source(value: str | None) -> ExplanationSourcePrisma | None:
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    mapped = _EXPLANATION_SOURCE.get(key)
    if mapped is None:
        raise EnumMappingError(f"Unknown explanation_source: {value!r}")
    return mapped  # type: ignore[return-value]

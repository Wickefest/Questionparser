"""Persistence context supplied beside canonical parser output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IngestionContext:
    """IDs for an already-created batch / run / source documents.

    Never embed these in Gemini or canonical parser payloads.
    """

    batch_id: str
    run_id: str
    question_document_id: str
    answer_document_id: str | None = None

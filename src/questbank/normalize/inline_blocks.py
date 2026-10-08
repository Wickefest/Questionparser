"""Merge mid-sentence text / chemistry / math fragments into one text block."""

from __future__ import annotations

import re
from typing import Any

from questbank.normalize.chemistry import latex_chemistry_to_unicode, normalize_chemistry_text
from questbank.types.canonical import TextBlock

_ARROW = re.compile(
    r"(?:->|→|⇌|←|↔|\\rightarrow|\\leftarrow|\\rightleftharpoons|\\to\b)",
    re.IGNORECASE,
)
_DISPLAY_MATH = re.compile(r"(\\frac|\\sqrt|\\begin\{|\\sum|\\int|\\\\)")


def _block_type(block: Any) -> str | None:
    return getattr(block, "type", None)


def _is_standalone_notation(text: str, *, kind: str) -> bool:
    """True when the notation should stay as its own display block."""
    t = (text or "").strip()
    if not t:
        return False
    if "\n" in t:
        return True
    if len(t) > 48:
        return True
    if _ARROW.search(t) and len(t) >= 10:
        return True
    if kind == "math" and _DISPLAY_MATH.search(t):
        return True
    if kind == "chemistry" and t.count("+") >= 2 and len(t) >= 18:
        return True
    return False


def _chem_to_inline(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    return normalize_chemistry_text(latex_chemistry_to_unicode(raw)) or raw


def _math_to_inline(latex: str) -> str:
    raw = (latex or "").strip()
    if not raw:
        return ""
    if raw.startswith("$") and raw.endswith("$"):
        return raw
    return f"${raw}$"


def _as_inline_piece(block: Any) -> str:
    b_type = _block_type(block)
    if b_type == "text":
        return str(getattr(block, "value", "") or "")
    if b_type == "chemistry":
        return _chem_to_inline(str(getattr(block, "value", "") or ""))
    if b_type == "math":
        return _math_to_inline(str(getattr(block, "latex", "") or ""))
    return ""


def _join_pieces(pieces: list[str]) -> str:
    out = ""
    for piece in pieces:
        part = piece or ""
        if not part.strip() and not part:
            continue
        stripped = part.strip()
        if not stripped:
            continue
        if not out:
            out = stripped
            continue
        if stripped[0] in "),.;:!?]}" or out[-1] in "([{\"'":
            out += stripped
        else:
            out += " " + stripped
    return " ".join(out.split()) if out else ""


def merge_inline_notation_blocks(blocks: list[Any]) -> list[Any]:
    """Collapse mid-sentence text+chemistry+math runs into a single text block.

    Rules:
    - Consecutive text / short chemistry / short math → one text paragraph.
    - Standalone display equations / multi-line reactions stay as typed blocks.
    - A lone chemistry/math block with no surrounding prose stays typed.
    """
    if not blocks:
        return blocks

    merged: list[Any] = []
    run: list[Any] = []

    def flush_run() -> None:
        nonlocal run
        if not run:
            return
        types = {_block_type(b) for b in run}
        has_text = "text" in types
        only_one_notation = (
            len(run) == 1 and _block_type(run[0]) in {"chemistry", "math"}
        )
        if only_one_notation and not has_text:
            merged.append(run[0])
        elif types <= {"text", "chemistry", "math"} and (has_text or len(run) > 1):
            value = _join_pieces([_as_inline_piece(b) for b in run])
            if value:
                merged.append(TextBlock(value=value))
            elif len(run) == 1:
                merged.append(run[0])
        else:
            merged.extend(run)
        run = []

    for block in blocks:
        b_type = _block_type(block)

        if b_type == "text":
            run.append(block)
            continue

        if b_type == "chemistry":
            chem = str(getattr(block, "value", "") or "")
            if _is_standalone_notation(chem, kind="chemistry"):
                flush_run()
                merged.append(block)
            else:
                run.append(block)
            continue

        if b_type == "math":
            latex = str(getattr(block, "latex", "") or "")
            if _is_standalone_notation(latex, kind="math"):
                flush_run()
                merged.append(block)
            else:
                run.append(block)
            continue

        flush_run()
        merged.append(block)

    flush_run()
    return merged


__all__ = ["merge_inline_notation_blocks"]

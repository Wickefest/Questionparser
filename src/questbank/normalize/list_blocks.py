"""Split numbered exam statement lists out of text blocks into list blocks."""

from __future__ import annotations

import re
from typing import Any

from questbank.types.canonical import ListBlock, TextBlock

# "1. It turns litmus…" / "2) …" / "3- …" on their own line
_NUM_ITEM = re.compile(r"(?m)^\s*(\d{1,2})\s*[\.\)\-:]\s+(\S.*\S|\S)\s*$")
_BULLET_ITEM = re.compile(r"(?m)^\s*[•\-\*]\s+(\S.*\S|\S)\s*$")

# Inline markers: "1 obtaining… 2 obtaining…"
# (?<!\d.) avoids treating the "1." in "5.1. Ceanothus" as a list marker.
_INLINE_NUM = re.compile(r"(?<!\d\.)\b(\d{1,2})(?:[\.\)\-:]\s+|\s+)(?=[A-Za-z(])")

# Peel trailing stem questions glued onto the last statement.
_TRAIL_QUESTION = re.compile(
    r"(?<=[.!?])\s+(?=(?:Which|What|How|Identify|Select|Choose|Name|Give|State)\b)"
)
_MIN_INLINE_ITEM_LEN = 6


def _block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _emit(lead: str, items: list[str], trail: str, *, ordered: bool) -> list[Any]:
    out: list[Any] = []
    if lead:
        out.append(TextBlock(type="text", value=lead))
    out.append(ListBlock(type="list", items=items, ordered=ordered))
    if trail:
        out.append(TextBlock(type="text", value=trail))
    return out


def _split_multiline(text: str) -> list[Any] | None:
    if "\n" not in text:
        return None

    lines = text.split("\n")
    numbered: list[tuple[int, int, str]] = []
    bullets: list[tuple[int, str]] = []

    for idx, line in enumerate(lines):
        m = _NUM_ITEM.match(line)
        if m:
            numbered.append((idx, int(m.group(1)), m.group(2).strip()))
            continue
        b = _BULLET_ITEM.match(line)
        if b:
            bullets.append((idx, b.group(1).strip()))

    use_ordered = False
    item_lines: list[tuple[int, str]] = []
    if len(numbered) >= 2:
        nums = [n for _, n, _ in numbered]
        if nums == list(range(nums[0], nums[0] + len(nums))):
            use_ordered = True
            item_lines = [(i, body) for i, _, body in numbered]
    if not item_lines and len(bullets) >= 2:
        item_lines = bullets

    if len(item_lines) < 2:
        return None

    first_item_line = item_lines[0][0]
    last_item_line = item_lines[-1][0]
    for i in range(first_item_line, last_item_line + 1):
        line = lines[i].strip()
        if not line:
            continue
        if use_ordered and not _NUM_ITEM.match(lines[i]):
            return None
        if not use_ordered and not _BULLET_ITEM.match(lines[i]):
            return None

    lead = "\n".join(lines[:first_item_line]).strip()
    trail = "\n".join(lines[last_item_line + 1 :]).strip()
    items = [body for _, body in item_lines]
    return _emit(lead, items, trail, ordered=use_ordered)


def _is_valid_inline_start(text: str, match: re.Match[str]) -> bool:
    """First numbered marker must be at start or immediately after sentence end."""
    if match.start() == 0:
        return True
    before = text[: match.start()].rstrip()
    return bool(before) and before[-1] in ".!?"


def _best_inline_run(text: str, matches: list[re.Match[str]]) -> list[re.Match[str]] | None:
    """Pick longest contiguous 1,2,3… run whose first marker is a valid list start."""
    # Ignore "0" markers from phrases like "from 0 to -1".
    matches = [m for m in matches if int(m.group(1)) >= 1]
    best: list[re.Match[str]] | None = None
    i = 0
    while i < len(matches):
        if int(matches[i].group(1)) != 1 or not _is_valid_inline_start(text, matches[i]):
            i += 1
            continue
        run = [matches[i]]
        nums = [1]
        j = i + 1
        while j < len(matches):
            n = int(matches[j].group(1))
            expected = nums[-1] + 1
            if n == expected:
                run.append(matches[j])
                nums.append(n)
                j += 1
            elif n < expected:
                # Skip noise numbers that aren't the next list index.
                j += 1
            else:
                break
        if len(run) >= 2 and (best is None or len(run) > len(best)):
            best = run
        i = max(j, i + 1)
    return best


def _split_inline(text: str) -> list[Any] | None:
    matches = list(_INLINE_NUM.finditer(text))
    run = _best_inline_run(text, matches)
    if not run:
        return None

    lead = text[: run[0].start()].strip()
    items: list[str] = []
    for idx, m in enumerate(run):
        start = m.end()
        end = run[idx + 1].start() if idx + 1 < len(run) else len(text)
        items.append(text[start:end].strip())

    trail = ""
    if items:
        last = items[-1]
        tq = _TRAIL_QUESTION.search(last)
        if tq:
            items[-1] = last[: tq.start()].strip()
            trail = last[tq.end() :].strip()
        else:
            # "…chloride Which techniques…" (no period before the follow-up stem)
            loose = re.search(
                r"\s+(?=(?:Which|What|How|Identify|Select|Choose)\b)",
                last,
            )
            if loose and loose.start() > _MIN_INLINE_ITEM_LEN:
                items[-1] = last[: loose.start()].strip()
                trail = last[loose.end() :].strip()

    items = [it.rstrip(" ;") for it in items if it]
    if len(items) < 2:
        return None
    if any(len(it) < _MIN_INLINE_ITEM_LEN for it in items):
        return None
    # Guard "Experiments 1 and 2"
    if items[0].lower() in {"and", "or", "to", "of", "in"}:
        return None
    return _emit(lead, items, trail, ordered=True)


def _split_text_value(value: str) -> list[Any] | None:
    """If value has a lead-in + 2+ numbered/bulleted statements, return [text?, list, text?]."""
    text = (value or "").replace("\r\n", "\n").strip()
    # Some Gemini rows store literal backslash-n instead of real newlines.
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    if not text:
        return None

    return _split_multiline(text) or _split_inline(text)


def repair_list_content(content: list[Any]) -> list[Any]:
    """Fix already-upgraded rows: decimal false splits, glued trails, orphan '1.' text."""
    if not isinstance(content, list):
        return content

    # Undo false splits from decimals like "5.1. Ceanothus" / "7.2. Which"
    repaired: list[Any] = []
    i = 0
    while i < len(content):
        block = content[i]
        btype = _block_type(block)
        value = ""
        if isinstance(block, dict):
            value = block.get("value") or ""
        else:
            value = getattr(block, "value", "") or ""

        nxt = content[i + 1] if i + 1 < len(content) else None
        if (
            btype == "text"
            and nxt is not None
            and _block_type(nxt) == "list"
            and re.search(r"\d\.$", value.strip())
        ):
            items = []
            if isinstance(nxt, dict):
                items = list(nxt.get("items") or [])
            else:
                items = list(getattr(nxt, "items", []) or [])
            # Reconstruct original prose: lead "…5." + "1." + item0 + "2." + item1…
            # (no spaces between "5." and "1." so decimals restore as "5.1.")
            merged = value.strip()
            for idx, item in enumerate(items):
                merged += f"{idx + 1}. {str(item).strip()}"
            repaired.append(TextBlock(type="text", value=merged))
            i += 2
            continue

        repaired.append(block)
        i += 1

    # Peel trailing stem questions still glued onto last list item.
    fixed: list[Any] = []
    for block in repaired:
        if _block_type(block) != "list":
            fixed.append(block)
            continue
        items = list(
            (block.get("items") if isinstance(block, dict) else getattr(block, "items", []))
            or []
        )
        ordered = True
        if isinstance(block, dict):
            ordered = block.get("ordered", True) is not False
        else:
            ordered = getattr(block, "ordered", True) is not False
        trail = ""
        if items:
            last = items[-1]
            tq = _TRAIL_QUESTION.search(last)
            loose = None if tq else re.search(
                r"\s+(?=(?:Which|What|How|Identify|Select|Choose)\b)",
                last,
            )
            m = tq or loose
            if m and m.start() > _MIN_INLINE_ITEM_LEN:
                items[-1] = last[: m.start()].strip()
                trail = last[m.end() :].strip()
        fixed.append(ListBlock(type="list", items=items, ordered=ordered))
        if trail:
            fixed.append(TextBlock(type="text", value=trail))

    # Drop orphan leftover markers like {"type":"text","value":"1."}
    cleaned: list[Any] = []
    for block in fixed:
        if _block_type(block) == "text":
            value = (
                block.get("value")
                if isinstance(block, dict)
                else getattr(block, "value", "")
            ) or ""
            if re.fullmatch(r"\d{1,2}\s*[\.\)\-: ]*", value.strip()):
                continue
        cleaned.append(block)

    return split_numbered_statement_lists(cleaned)


def split_numbered_statement_lists(content: list[Any]) -> list[Any]:
    """Expand text blocks that contain 1./2./3. statement lists into list blocks."""
    out: list[Any] = []
    for block in content or []:
        if _block_type(block) != "text":
            out.append(block)
            continue
        value = ""
        if isinstance(block, dict):
            value = block.get("value") or ""
        else:
            value = getattr(block, "value", "") or ""
        split = _split_text_value(value)
        if split is None:
            out.append(block)
        else:
            out.extend(split)
    return out

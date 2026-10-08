"""Normalize fullmarks_complete_database.json into a Supabase-ready structured
format: splits each question's PART markers into a `parts` array (matching the
question_drafts.options schema used elsewhere in this project), splits each
answer's B/M/A marking points into `parts_answer` (matching answer_records),
strips UI-artifact junk text, and normalizes chemistry notation (arrows,
equilibrium signs, degree symbols) for consistent rendering.

Run: .venv/Scripts/python.exe scripts/normalize_fullmarks_database.py
Outputs:
  output/fullmarks_normalized.json
  output/fullmarks_qc_report.txt
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path("fullmarks_complete_database.json")
OUT_JSON = Path("output/fullmarks_normalized.json")
OUT_REPORT = Path("output/fullmarks_qc_report.txt")

JUNK_FOOTER = (
    "This question is not counting towards your marks. "
    "Anything you already earned is kept."
)
ANSWER_HEADER = "THE ANSWER, POINT BY POINT"

MARKER_RE = re.compile(r"PART\s*((?:\([^)]*\))+)\s*·\s*(\d+)\s*MARKS?", re.IGNORECASE)
PAREN_RE = re.compile(r"\(([^)]*)\)")
SUBPOINT_RE = re.compile(r"(?:^|\n)((?:B|M|A)\d+)\n")

ROMAN_MAP = {"I": "i", "II": "ii", "III": "iii", "IV": "iv", "V": "v", "VI": "vi"}

SUPERSCRIPT_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
SUBSCRIPT_DIGITS = str.maketrans("0123456789-", "₀₁₂₃₄₅₆₇₈₉₋")

# nuclear notation written as "131/53 I" (mass/atomic-number fraction) ->
# proper superscript-mass/subscript-atomic-number isotope notation "¹³¹₅₃I".
# Requires an uppercase-led element symbol (or spelled-out beta/alpha) right
# after the numbers, so ordinary mark-banding fractions like "4/4 correct"
# never match (lowercase word, not a symbol).
ISOTOPE_RE = re.compile(r"(\d+)/(-?\d+)\s*([A-Z][a-z]?|beta|alpha|β|α)\b")


def _isotope_sub(m: re.Match) -> str:
    mass, atomic, sym = m.group(1), m.group(2), m.group(3)
    if sym.lower() == "beta":
        sym = "β"
    elif sym.lower() == "alpha":
        sym = "α"
    return mass.translate(SUPERSCRIPT_DIGITS) + atomic.translate(SUBSCRIPT_DIGITS) + sym

MISSING_CHAPTER_TITLE = "CH 10 - Metals - 18 questions - 55%"

LETTER_SUB_RE = re.compile(r"^([a-z])\(([a-z0-9]+)\)$")
LETTER_ONLY_RE = re.compile(r"^([a-z])$")


def nest_parts(flat_parts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Regroup a flat list of {label, marks, prompt} (labels like 'a', 'a(i)',
    'd(chemical)', 'procedure') into a genuinely nested parts tree: a top-level
    letter with sub-parts becomes {label:'a', children:[{label:'i',...}, ...]}
    instead of a flat composite label. Returns (nested_parts, nav_order).
    """
    groups: dict[str, list[tuple[str | None, dict]]] = {}
    order: list[tuple[str, str]] = []

    for p in flat_parts:
        m = LETTER_SUB_RE.match(p["label"])
        if m:
            letter, sub = m.group(1), m.group(2)
            if letter not in groups:
                groups[letter] = []
                order.append(("group", letter))
            groups[letter].append((sub, p))
            continue
        m2 = LETTER_ONLY_RE.match(p["label"])
        if m2:
            letter = m2.group(1)
            if letter not in groups:
                groups[letter] = []
                order.append(("group", letter))
            groups[letter].append((None, p))
            continue
        # standalone non-letter label (e.g. "procedure", "treatment-of-results")
        groups[p["label"]] = [(None, p)]
        order.append(("group", p["label"]))

    nested: list[dict] = []
    nav: list[dict] = []

    for _, key in order:
        entries = groups[key]
        if len(entries) == 1 and entries[0][0] is None:
            p = entries[0][1]
            nested.append(
                {
                    "label": key,
                    "marks": p["marks"],
                    "prompt": p["prompt"],
                    "visuals": [],
                    "children": [],
                    "responseKind": "text",
                }
            )
            nav.append(
                {
                    "label": key,
                    "marks": p["marks"],
                    "prompt": p["prompt"],
                    "visuals": [],
                    "hasChildren": False,
                    "sectionOnly": False,
                    "responseKind": "text",
                }
            )
        else:
            children = []
            total_marks = 0
            for sub, p in entries:
                child_label = sub if sub is not None else "i"
                children.append(
                    {
                        "label": child_label,
                        "marks": p["marks"],
                        "prompt": p["prompt"],
                        "visuals": [],
                        "children": [],
                        "responseKind": "text",
                    }
                )
                total_marks += p["marks"]
            nested.append(
                {
                    "label": key,
                    "marks": total_marks,
                    "prompt": "",
                    "visuals": [],
                    "children": children,
                    "responseKind": "text",
                }
            )
            nav.append(
                {
                    "label": key,
                    "marks": total_marks,
                    "prompt": "",
                    "visuals": [],
                    "hasChildren": True,
                    "sectionOnly": True,
                    "responseKind": "text",
                }
            )
            for child in children:
                nav.append(
                    {
                        "label": f"{key}({child['label']})",
                        "marks": child["marks"],
                        "prompt": child["prompt"],
                        "visuals": [],
                        "hasChildren": False,
                        "sectionOnly": False,
                        "responseKind": "text",
                    }
                )

    return nested, nav


def clean_chem_text(s: str) -> str:
    if s is None:
        return s
    # strip UI artifacts
    s = s.replace(JUNK_FOOTER, "")
    s = re.sub(r"\n?Attach photo or sketch\n?Draw it\n?", "\n", s)
    # reaction arrows with a condition written between dashes, e.g. --CuO--> or --[O]-->
    s = re.sub(r"--\[?([^\-\[\]]+)\]?-->", r" → (\1) ", s)
    # plain long arrow
    s = s.replace("-->", " → ")
    # equilibrium arrows
    s = s.replace("<=>", " ⇌ ").replace("<->", " ⇌ ")
    # simple reaction arrow
    s = s.replace("->", " → ")
    # inequality signs (after <=> already consumed)
    s = s.replace(">=", "≥").replace("<=", "≤")
    # degrees
    s = s.replace("degC", "°C")
    s = re.sub(r"\s?\bdeg\b", "°", s)
    # nuclear notation: "131/53 I" -> superscript/subscript isotope form
    s = ISOTOPE_RE.sub(_isotope_sub, s)
    # whitespace cleanup
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def build_label(raw_marker: str) -> str:
    """raw_marker is the full '(A)(I)' / '(PROCEDURE)' / '(TREATMENT)(OF)(RESULTS)' text."""
    pieces = [p.strip() for p in PAREN_RE.findall(raw_marker)]
    if not pieces:
        return "a"
    first = pieces[0]
    if re.fullmatch(r"[A-Z]", first):
        label = first.lower()
        rest = pieces[1:]
        if rest:
            sub_pieces = []
            for p in rest:
                if p.upper() in ROMAN_MAP:
                    sub_pieces.append(ROMAN_MAP[p.upper()])
                else:
                    sub_pieces.append(p.lower())
            label = f"{label}({'-'.join(sub_pieces)})"
        return label
    # no leading single-letter piece (e.g. PROCEDURE, TREATMENT)(OF)(RESULTS))
    return "-".join(p.lower() for p in pieces)


def split_by_markers(text: str) -> tuple[str, list[dict], list[str]]:
    """Returns (stem, parts, dup_labels_dropped) for a question/answer text body.

    Duplicate PART labels within the same text (a rare source-scrape glitch,
    e.g. "PART (C)" appearing 3 times in one question) are collapsed to the
    first occurrence so the part list matches 1:1 against the answer key.
    """
    matches = list(MARKER_RE.finditer(text))
    parts = []
    seen_labels: set[str] = set()
    dropped: list[str] = []
    for i, m in enumerate(matches):
        raw_marker = m.group(1)
        marks = int(m.group(2))
        label = build_label(raw_marker)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end]
        if label in seen_labels:
            dropped.append(f"PART {raw_marker} (label {label!r})")
            continue
        seen_labels.add(label)
        parts.append(
            {
                "raw_marker": f"PART {raw_marker}",
                "label": label,
                "marks": marks,
                "content": content,
            }
        )
    stem = text[: matches[0].start()] if matches else text
    return stem, parts, dropped


def split_answer_points(content: str) -> list[str]:
    matches = list(SUBPOINT_RE.finditer(content))
    if not matches:
        cleaned = clean_chem_text(content)
        return [cleaned] if cleaned else []
    points = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        pt = clean_chem_text(content[start:end])
        if pt:
            points.append(pt)
    return points


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))

    normalized_chapters = []
    qc_lines: list[str] = []
    total_q = 0
    total_parts = 0

    for ch in data:
        chapter_title = ch["chapter"] or MISSING_CHAPTER_TITLE
        if not ch["chapter"]:
            qc_lines.append(
                f"[CHAPTER TITLE MISSING] source had blank chapter title for a "
                f"{len(ch['questions'])}-question chapter; inferred title: {MISSING_CHAPTER_TITLE!r} "
                f"(content clearly covers Metals/reactivity series/corrosion - please confirm)."
            )

        out_questions = []
        for q in ch["questions"]:
            total_q += 1
            qn = q["question_number"]
            qtext = q["question"]
            atext = q["answer"]

            if "Opening the mark scheme" in atext:
                qc_lines.append(
                    f"[{chapter_title} Q{qn}] ANSWER KEY NOT SCRAPED - source file only contains "
                    f"the loading placeholder 'Opening the mark scheme...', no real answer content exists to convert"
                )
            elif not atext.startswith(ANSWER_HEADER):
                qc_lines.append(f"[{chapter_title} Q{qn}] answer missing expected header prefix")
            atext_body = atext[len(ANSWER_HEADER):] if atext.startswith(ANSWER_HEADER) else atext

            q_stem_raw, q_parts_raw, q_dropped = split_by_markers(qtext)
            a_stem_raw, a_parts_raw, a_dropped = split_by_markers(atext_body)

            for d in q_dropped:
                qc_lines.append(f"[{chapter_title} Q{qn}] dropped duplicate {d} in question text")
            for d in a_dropped:
                qc_lines.append(f"[{chapter_title} Q{qn}] dropped duplicate {d} in answer text")

            # Fallback: single free-response question with no "PART (X) · N MARKS"
            # header of its own, but the answer wraps it in exactly one PART block
            # (e.g. "outline a method..." style questions). Treat the whole
            # (Attach/Draw-stripped) question text as that one part's prompt.
            if not q_parts_raw and len(a_parts_raw) == 1:
                only = a_parts_raw[0]
                q_parts_raw = [
                    {
                        "raw_marker": only["raw_marker"],
                        "label": only["label"],
                        "marks": only["marks"],
                        "content": qtext,
                    }
                ]
                q_stem_raw = ""

            stem_clean = clean_chem_text(q_stem_raw)

            if len(q_parts_raw) != len(a_parts_raw):
                qc_lines.append(
                    f"[{chapter_title} Q{qn}] MISMATCH part count: "
                    f"question has {len(q_parts_raw)} parts, answer has {len(a_parts_raw)} parts"
                )

            flat_clean_parts = []
            for p in q_parts_raw:
                prompt_clean = clean_chem_text(p["content"])
                flat_clean_parts.append(
                    {"label": p["label"], "marks": p["marks"], "prompt": prompt_clean}
                )
                total_parts += 1

            parts, nav_order = nest_parts(flat_clean_parts)

            parts_answer = []
            for i, ap in enumerate(a_parts_raw):
                points = split_answer_points(ap["content"])
                q_label = q_parts_raw[i]["label"] if i < len(q_parts_raw) else ap["label"]
                if i < len(q_parts_raw) and q_parts_raw[i]["label"] != ap["label"]:
                    qc_lines.append(
                        f"[{chapter_title} Q{qn}] LABEL MISMATCH at index {i}: "
                        f"question part label={q_parts_raw[i]['label']!r} vs answer part label={ap['label']!r}"
                    )
                if not points:
                    qc_lines.append(
                        f"[{chapter_title} Q{qn}] part {ap['label']!r} has NO parsed answer points "
                        f"(raw content: {ap['content'][:80]!r}...)"
                    )
                parts_answer.append(
                    {
                        "label": q_label,
                        "marks_max": ap["marks"],
                        "accepted_answers": points,
                        "notes": None,
                        "ecf": False,
                        "visuals": [],
                    }
                )

            marks_total = sum(p["marks"] for p in parts)

            out_questions.append(
                {
                    "chapter": chapter_title,
                    "question_number": qn,
                    "content": [{"type": "text", "value": stem_clean}],
                    "options": {
                        "mode": "structured",
                        "stem": stem_clean,
                        "parts": parts,
                        "marksTotal": marks_total,
                        "navigationOrder": nav_order,
                    },
                    "parts_answer": parts_answer,
                }
            )

        normalized_chapters.append({"chapter": chapter_title, "questions": out_questions})

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(normalized_chapters, indent=2, ensure_ascii=False), encoding="utf-8")

    qc_lines.insert(0, f"Total questions processed: {total_q}")
    qc_lines.insert(1, f"Total parts extracted: {total_parts}")
    qc_lines.insert(2, f"Total issues flagged below: {len(qc_lines) - 2}")
    qc_lines.insert(3, "")
    OUT_REPORT.write_text("\n".join(qc_lines), encoding="utf-8")

    print(f"Wrote {OUT_JSON} ({total_q} questions, {total_parts} parts)")
    print(f"Wrote {OUT_REPORT} ({len(qc_lines)} lines)")


if __name__ == "__main__":
    main()

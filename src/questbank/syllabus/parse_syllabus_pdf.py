from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

_TOPIC_INLINE = re.compile(
    r"^\s*(?P<num>\d{1,2})\.\s+(?P<title>[A-Za-z].+?)\s*$"
)
# Topic number alone, with or without trailing period (PDF extract often drops ".").
_TOPIC_BARE = re.compile(r"^\s*(?P<num>\d{1,2})\.?\s*$")
_SUBTOPIC_LINE = re.compile(
    r"^\s*(?P<code>\d{1,2}\.\d+)\s+(?P<title>[A-Za-z].+?)\s*$"
)
_CONTENT_START = re.compile(r"(?i)SUBJECT\s+CONTENT")
_CONTENT_END = re.compile(
    r"(?i)SUMMARY\s+OF\s+KEY\s+QUANTITIES|PRACTICAL\s+ASSESSMENT|NOTES\s+FOR\s+QUALITATIVE"
)
_STRUCTURE_START = re.compile(r"(?i)CONTENT\s+STRUCTURE")
_LETTER_OUTCOME = re.compile(
    r"^\s*\((?P<letter>[a-z])\)\s*(?P<text>.*)$",
    re.IGNORECASE,
)
# Longer roman forms first so "(ii)" is not read as letter/roman "i" + leftover "i)".
_ROMAN_OUTCOME = re.compile(
    r"^\s*\((?P<roman>viii|vii|iii|ii|iv|ix|vi|i|v|x)\)\s*(?P<text>.*)$",
    re.IGNORECASE,
)
_ROMAN_SEQ = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")
_SECTION_BANNER = re.compile(
    r"(?i)^\s*SECTION\s+(?P<roman>[IVX]+)\s*:?\s*(?P<title>.*)$"
)
_SKIP_LO_HEADERS = {
    "content",
    "learning outcomes",
    "candidates should be able to:",
    "candidates should be able to",
    "overview",
}
# Structural labels that must never become topic titles.
_NON_TITLE_STARTS = (
    "section ",
    "overview",
    "content",
    "learning outcomes",
    "candidates should",
)


class SyllabusOutcomeItem(BaseModel):
    """Letter (a/b/c) or nested roman (i/ii/iii) learning-objective item."""

    code: str  # "a", "b", "i", "ii"
    text: str
    children: list["SyllabusOutcomeItem"] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class SyllabusSubtopic(BaseModel):
    code: str
    title: str
    outcomes_text: str = Field(default="", serialization_alias="outcomesText")
    outcomes: list[SyllabusOutcomeItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class SyllabusTopic(BaseModel):
    number: int
    title: str
    section: str | None = None
    subtopics: list[SyllabusSubtopic] = Field(default_factory=list)
    # Topic-level (a)(b)(c) when the syllabus has no subtopic grouping (e.g. topic 12).
    outcomes: list[SyllabusOutcomeItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class SyllabusTaxonomy(BaseModel):
    syllabus_code: str = Field(default="6092", serialization_alias="syllabusCode")
    subject: str = "Chemistry"
    title: str | None = None
    topics: list[SyllabusTopic] = Field(default_factory=list)
    # Section title -> overview / intro paragraph(s).
    section_descriptions: dict[str, str] = Field(
        default_factory=dict,
        serialization_alias="sectionDescriptions",
    )
    source_path: str | None = Field(default=None, serialization_alias="sourcePath")

    model_config = {"populate_by_name": True}

    def subtopic_count(self) -> int:
        return sum(len(topic.subtopics) for topic in self.topics)

    def iter_subtopics(self) -> list[tuple[SyllabusTopic, SyllabusSubtopic]]:
        pairs: list[tuple[SyllabusTopic, SyllabusSubtopic]] = []
        for topic in self.topics:
            for sub in topic.subtopics:
                pairs.append((topic, sub))
        return pairs


def parse_syllabus_pdf(pdf_path: str | Path) -> SyllabusTaxonomy:
    """Extract topic/subtopic/(a)(b)/(i)(ii) taxonomy from a syllabus PDF."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Syllabus PDF not found: {pdf_path}")

    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF is required for syllabus parsing") from exc

    page_texts: list[str] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            page_texts.append(page.get_text("text") or "")

    cleaned_pages = _strip_repeated_page_chrome(page_texts)
    text = "\n".join(cleaned_pages)
    taxonomy = parse_syllabus_text(text)
    taxonomy.source_path = str(pdf_path).replace("\\", "/")
    if not taxonomy.title:
        taxonomy.title = pdf_path.stem
    return taxonomy


def parse_syllabus_text(text: str) -> SyllabusTaxonomy:
    """Parse taxonomy from plain syllabus text (PDF extract or fixture)."""
    lines = [_clean_line(line) for line in text.splitlines()]
    structure_topics = _parse_content_structure(lines)
    subject_lines = _subject_content_lines(lines)
    detailed, section_descriptions = _parse_subject_content(subject_lines)

    by_number: dict[int, SyllabusTopic] = {t.number: t for t in structure_topics}
    for topic in detailed:
        existing = by_number.get(topic.number)
        if existing is None:
            by_number[topic.number] = topic
            continue
        if topic.title and (not existing.title or len(topic.title) > len(existing.title)):
            existing.title = topic.title
        if topic.section and (
            not existing.section or len(topic.section) > len(existing.section or "")
        ):
            existing.section = topic.section
        if topic.subtopics:
            existing.subtopics = _merge_subtopics(existing.subtopics, topic.subtopics)
        if topic.outcomes:
            existing.outcomes = topic.outcomes

    topics = [by_number[n] for n in sorted(by_number)]
    return SyllabusTaxonomy(topics=topics, section_descriptions=section_descriptions)


def _strip_repeated_page_chrome(page_texts: list[str]) -> list[str]:
    """Drop repeating headers/footers and bare page numbers without subject-specific rules.

    Any short line that appears across many pages is treated as chrome (running
    header / footer). Lone digit lines are page numbers. This works for every
    syllabus PDF, not just one subject code.
    """
    if not page_texts:
        return []

    page_line_lists: list[list[str]] = []
    for page_text in page_texts:
        page_line_lists.append([_clean_line(line) for line in page_text.splitlines()])

    page_count = len(page_line_lists)
    # Count pages each normalized line appears on (presence, not raw frequency).
    presence: Counter[str] = Counter()
    for lines in page_line_lists:
        seen_on_page = {line for line in lines if line}
        for line in seen_on_page:
            presence[line] += 1

    # Appear on enough pages to be running chrome. Use a majority threshold so
    # outline labels like "(i)" / "(f)" that recur in body text are not dropped.
    min_pages = 2 if page_count <= 4 else max(3, (page_count + 1) // 2)
    chrome: set[str] = set()
    for line, count in presence.items():
        if count < min_pages:
            continue
        if _is_outline_marker_line(line):
            continue
        # Running headers/footers are short; long unique body text rarely repeats.
        if len(line) <= 120:
            chrome.add(line)

    cleaned_pages: list[str] = []
    for lines in page_line_lists:
        kept: list[str] = []
        for line in lines:
            if not line:
                kept.append("")
                continue
            if line in chrome and not _is_outline_marker_line(line):
                continue
            kept.append(line)
        cleaned_pages.append("\n".join(kept))
    return cleaned_pages


def _is_outline_marker_line(line: str) -> bool:
    """Letter/roman list labels — never treat these as running page chrome."""
    if not line:
        return False
    return bool(_ROMAN_OUTCOME.match(line) or _LETTER_OUTCOME.match(line))


def _next_roman(code: str) -> str | None:
    try:
        idx = _ROMAN_SEQ.index(code.lower())
    except ValueError:
        return None
    if idx + 1 >= len(_ROMAN_SEQ):
        return None
    return _ROMAN_SEQ[idx + 1]


def _merge_subtopics(
    old: list[SyllabusSubtopic],
    new: list[SyllabusSubtopic],
) -> list[SyllabusSubtopic]:
    by_code = {s.code: s.model_copy(deep=True) for s in old}
    for sub in new:
        existing = by_code.get(sub.code)
        if existing is None:
            by_code[sub.code] = sub.model_copy(deep=True)
            continue
        if sub.title and (not existing.title or len(sub.title) > len(existing.title)):
            existing.title = sub.title
        if sub.outcomes:
            existing.outcomes = sub.outcomes
            existing.outcomes_text = sub.outcomes_text or _outcomes_to_text(sub.outcomes)
        elif sub.outcomes_text and not existing.outcomes_text:
            existing.outcomes_text = sub.outcomes_text
    return [by_code[c] for c in sorted(by_code, key=_subtopic_sort_key)]


def _subtopic_sort_key(code: str) -> tuple:
    parts = str(code).split(".")
    nums = []
    for p in parts:
        nums.append(int(p) if p.isdigit() else 0)
    return tuple(nums)


def _clean_line(line: str) -> str:
    return (
        (line or "")
        .replace("\u00a0", " ")
        .replace("\u2009", " ")
        .replace("\ufb01", "fi")
        .replace("\ufb02", "fl")
        .strip()
    )


def _looks_like_title(line: str) -> bool:
    """Short heading-like text, not an overview paragraph or line wrap fragment."""
    if not line or not line[0].isalpha():
        return False
    # Wrapped prose fragments usually continue in lowercase.
    if line[0].islower():
        return False
    lower = line.lower()
    if any(lower.startswith(prefix) for prefix in _NON_TITLE_STARTS):
        return False
    if len(line) > 90:
        return False
    words = line.split()
    if len(words) > 14:
        return False
    # Long prose usually contains multiple sentence breaks.
    if line.count(". ") >= 1 and len(words) > 8:
        return False
    return True


def _looks_like_prose(line: str) -> bool:
    """Section overview / paragraph body rather than a heading or LO."""
    if not line or not line[0].isalpha():
        return False
    lower = line.lower()
    if lower in _SKIP_LO_HEADERS:
        return False
    if any(lower.startswith(prefix) for prefix in _NON_TITLE_STARTS):
        return False
    words = line.split()
    if len(line) >= 90 or len(words) >= 16:
        return True
    if line.count(". ") >= 1 and len(words) >= 10:
        return True
    return False


def _subject_content_lines(lines: list[str]) -> list[str]:
    # PDFs often list "SUBJECT CONTENT" in the contents table first; use the last one.
    starts = [i for i, line in enumerate(lines) if _CONTENT_START.search(line)]
    if not starts:
        return lines
    start = starts[-1] + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if _CONTENT_END.search(lines[i]):
            end = i
            break
    return lines[start:end]


def _parse_content_structure(lines: list[str]) -> list[SyllabusTopic]:
    # Prefer the real structure block (last occurrence), not the contents-page stub.
    structure_idxs = [i for i, line in enumerate(lines) if _STRUCTURE_START.search(line)]
    if not structure_idxs:
        return []
    start = structure_idxs[-1] + 1
    end = len(lines)
    for i in range(start, len(lines)):
        if _CONTENT_START.search(lines[i]):
            end = i
            break
    block = lines[start:end]

    section: str | None = None
    topics: list[SyllabusTopic] = []
    pending_num: int | None = None
    pending_section_roman = False

    for line in block:
        if not line:
            continue
        lower = line.lower()
        if lower in {"sections", "topics", "sections topics"}:
            continue

        # "III. Chemistry in a Sustainable World" or "I." then title on next line
        sec_inline = re.match(
            r"^\s*(?P<roman>[IVX]+)\.\s+(?P<title>.+)$",
            line,
            re.IGNORECASE,
        )
        if sec_inline and not line[0].isdigit():
            section = re.sub(r"\s+", " ", sec_inline.group("title")).strip(" :-")
            pending_num = None
            continue
        if re.match(r"^\s*(?P<roman>[IVX]+)\.\s*$", line, re.IGNORECASE):
            pending_section_roman = True
            pending_num = None
            continue
        if pending_section_roman and _looks_like_title(line):
            section = re.sub(r"\s+", " ", line).strip(" :-")
            pending_section_roman = False
            continue
        pending_section_roman = False

        inline = _TOPIC_INLINE.match(line)
        if inline:
            number = int(inline.group("num"))
            title = inline.group("title").strip()
            if 1 <= number <= 40 and _looks_like_title(title):
                topics.append(
                    SyllabusTopic(number=number, title=title, section=section, subtopics=[])
                )
            pending_num = None
            continue

        bare = _TOPIC_BARE.match(line)
        if bare and "." in line:
            pending_num = int(bare.group("num"))
            continue
        # Digits-only may be a page remnant inside the structure table; only accept
        # when a title-looking line follows.
        if bare and line.replace(".", "").isdigit():
            pending_num = int(bare.group("num"))
            continue

        if pending_num is not None and _looks_like_title(line):
            topics.append(
                SyllabusTopic(
                    number=pending_num,
                    title=line.strip(),
                    section=section,
                    subtopics=[],
                )
            )
            pending_num = None
        elif pending_num is not None:
            pending_num = None

    return topics


def _should_nest_roman(
    current_letter: SyllabusOutcomeItem | None,
    roman: str,
    following_lines: list[str] | None = None,
) -> bool:
    """Decide whether (i)/(v)/(x) nest under the open letter, or start a new letter.

    Multi-character romans (ii, iii, …) are never letters. Single-character
    tokens nest when they continue an open roman list, when the parent
    introduces a list, or when treating the token as the next letter would
    skip intermediate letters (a→i). (h) followed by (i) uses lookahead:
    nest if (ii) appears before the next non-roman letter such as (j).
    """
    if current_letter is None:
        return False
    roman = roman.lower()

    if current_letter.children:
        last = (current_letter.children[-1].code or "").lower()
        expected = _next_roman(last)
        if expected and roman == expected:
            return True
        if roman == "i":
            # A new (i) after an existing roman list is the next letter, not another sub.
            return False
        return roman not in {"i", "v", "x"}

    if roman not in {"i", "v", "x"}:
        return True

    text = (current_letter.text or "").rstrip()
    if text.endswith(":"):
        return True

    parent = (current_letter.code or "").lower()
    if len(parent) == 1 and parent.isalpha() and len(roman) == 1:
        gap = ord(roman) - ord(parent)
        if gap > 1:
            return True
        if gap == 1:
            return _lookahead_is_roman_list(following_lines or [])
        return False
    return False


def _lookahead_is_roman_list(following_lines: list[str]) -> bool:
    """True when the next outline items look like (ii)/(iii), not (j)/(k)."""
    for line in following_lines:
        if not line:
            continue
        roman_match = _ROMAN_OUTCOME.match(line)
        if roman_match:
            token = roman_match.group("roman").lower()
            if token in {"ii", "iii", "iv", "vi", "vii", "viii", "ix"}:
                return True
            continue
        letter_match = _LETTER_OUTCOME.match(line)
        if letter_match:
            return False
        if _SUBTOPIC_LINE.match(line) or _TOPIC_INLINE.match(line):
            return False
        if _SECTION_BANNER.match(line):
            return False
    return False


def _parse_subject_content(lines: list[str]) -> tuple[list[SyllabusTopic], dict[str, str]]:
    """Parse SUBJECT CONTENT into topics / subtopics / (a)(b) / (i)(ii)."""
    section: str | None = None
    section_descriptions: dict[str, str] = {}
    collecting_section_desc = False
    topics: dict[int, SyllabusTopic] = {}
    current_topic: SyllabusTopic | None = None
    current_sub: SyllabusSubtopic | None = None
    current_letter: SyllabusOutcomeItem | None = None
    pending_topic_num: int | None = None
    pending_section = False
    in_learning_outcomes = False
    # Content-phase subtopic titles (before LO block) keyed by code.
    content_titles: dict[str, str] = {}

    def ensure_topic(number: int) -> SyllabusTopic:
        nonlocal current_topic
        topic = topics.get(number)
        if topic is None:
            topic = SyllabusTopic(
                number=number,
                title=f"Topic {number}",
                section=section,
                subtopics=[],
            )
            topics[number] = topic
        current_topic = topic
        return topic

    def ensure_subtopic(code: str, title: str | None = None) -> SyllabusSubtopic:
        nonlocal current_sub, current_letter, collecting_section_desc
        collecting_section_desc = False
        topic_num = int(code.split(".", 1)[0])
        topic = ensure_topic(topic_num)
        existing = next((s for s in topic.subtopics if s.code == code), None)
        resolved_title = title or content_titles.get(code) or (existing.title if existing else code)
        if existing is None:
            existing = SyllabusSubtopic(code=code, title=resolved_title, outcomes=[], outcomes_text="")
            topic.subtopics.append(existing)
        elif title and (not existing.title or existing.title == code):
            existing.title = title
        elif not existing.title or existing.title == code:
            existing.title = resolved_title
        current_sub = existing
        current_letter = None
        return existing

    def append_section_description(text: str) -> None:
        if not section or not text:
            return
        prev = section_descriptions.get(section, "").strip()
        chunk = re.sub(r"\s+", " ", text).strip()
        if not chunk:
            return
        section_descriptions[section] = f"{prev} {chunk}".strip() if prev else chunk

    def start_section(title: str) -> None:
        nonlocal section, collecting_section_desc, current_letter, in_learning_outcomes
        finish_letter()
        cleaned = re.sub(r"\s+", " ", title).strip(" :-")
        if cleaned:
            section = cleaned
            section_descriptions.setdefault(section, "")
            collecting_section_desc = True
            in_learning_outcomes = False

    def finish_letter() -> None:
        nonlocal current_letter
        if current_letter is None:
            return
        current_letter.text = re.sub(r"\s+", " ", current_letter.text).strip(" ;.")
        for child in current_letter.children:
            child.text = re.sub(r"\s+", " ", child.text).strip(" ;.")
        if current_sub is not None:
            current_sub.outcomes = [o for o in current_sub.outcomes if o.code != current_letter.code]
            current_sub.outcomes.append(current_letter)
            current_sub.outcomes_text = _outcomes_to_text(current_sub.outcomes)
        elif current_topic is not None:
            current_topic.outcomes = [o for o in current_topic.outcomes if o.code != current_letter.code]
            current_topic.outcomes.append(current_letter)
        current_letter = None

    def begin_topic(number: int, title: str) -> None:
        nonlocal current_topic, current_sub, collecting_section_desc, in_learning_outcomes
        finish_letter()
        collecting_section_desc = False
        current_topic = SyllabusTopic(
            number=number,
            title=title.strip(),
            section=section,
            subtopics=[],
        )
        topics[number] = current_topic
        current_sub = None
        in_learning_outcomes = False

    for idx, line in enumerate(lines):
        if not line:
            continue
        following = lines[idx + 1 :]

        lower = line.lower().strip()

        section_match = _SECTION_BANNER.match(line)
        if section_match:
            title = (section_match.group("title") or "").strip()
            if title:
                start_section(title)
            else:
                pending_section = True
                finish_letter()
                collecting_section_desc = False
                in_learning_outcomes = False
            continue
        if pending_section and _looks_like_title(line):
            start_section(line)
            pending_section = False
            continue
        if pending_section and _looks_like_prose(line):
            # Unusual, but don't treat overview as the section name.
            pending_section = False

        if lower == "overview" or lower.startswith("overview"):
            collecting_section_desc = True
            finish_letter()
            in_learning_outcomes = False
            continue

        if lower == "learning outcomes" or lower.startswith("learning outcomes"):
            collecting_section_desc = False
            in_learning_outcomes = True
            finish_letter()
            continue
        if lower == "content":
            collecting_section_desc = False
            in_learning_outcomes = False
            finish_letter()
            continue
        if lower in _SKIP_LO_HEADERS:
            continue

        if pending_topic_num is not None:
            if _looks_like_title(line):
                begin_topic(pending_topic_num, line)
                pending_topic_num = None
                continue
            # Digits-only was a page number (or noise) if what follows isn't a title.
            pending_topic_num = None

        inline = _TOPIC_INLINE.match(line)
        if inline:
            number = int(inline.group("num"))
            title = inline.group("title").strip()
            if 1 <= number <= 40 and _looks_like_title(title):
                begin_topic(number, title)
                continue

        bare = _TOPIC_BARE.match(line)
        if bare:
            number = int(bare.group("num"))
            if 1 <= number <= 40:
                pending_topic_num = number
            continue

        sub_match = _SUBTOPIC_LINE.match(line)
        if sub_match:
            finish_letter()
            code = sub_match.group("code")
            title = sub_match.group("title").strip()
            if not in_learning_outcomes:
                content_titles[code] = title
            ensure_subtopic(code, title=title)
            continue

        # Prefer roman (i)/(ii)/(iii) under an open letter that introduces a list.
        roman_match = _ROMAN_OUTCOME.match(line)
        if roman_match and _should_nest_roman(
            current_letter, roman_match.group("roman"), following
        ):
            collecting_section_desc = False
            roman = roman_match.group("roman").lower()
            text = (roman_match.group("text") or "").strip()
            child = SyllabusOutcomeItem(code=roman, text=text, children=[])
            current_letter.children = [
                c for c in current_letter.children if c.code != roman
            ]
            current_letter.children.append(child)
            continue

        letter_match = _LETTER_OUTCOME.match(line)
        if letter_match:
            collecting_section_desc = False
            maybe_roman = _ROMAN_OUTCOME.match(line)
            if maybe_roman and _should_nest_roman(
                current_letter, maybe_roman.group("roman"), following
            ):
                roman = maybe_roman.group("roman").lower()
                text = (maybe_roman.group("text") or "").strip()
                child = SyllabusOutcomeItem(code=roman, text=text, children=[])
                current_letter.children = [
                    c for c in current_letter.children if c.code != roman
                ]
                current_letter.children.append(child)
                continue
            finish_letter()
            letter = letter_match.group("letter").lower()
            text = (letter_match.group("text") or "").strip()
            current_letter = SyllabusOutcomeItem(code=letter, text=text, children=[])
            in_learning_outcomes = True
            continue

        # Section overview / intro paragraphs (not topics, not LOs).
        if collecting_section_desc and section and not in_learning_outcomes:
            if _looks_like_prose(line) or (
                line[0].isalpha() and lower not in _SKIP_LO_HEADERS and not _looks_like_title(line)
            ):
                append_section_description(line)
                continue
            if _looks_like_title(line):
                # Short leftover heading under a section before topics — keep as description.
                append_section_description(line)
                continue

        # Continuation text for current letter or last roman child.
        if current_letter is not None and in_learning_outcomes:
            if _looks_like_prose(line) and collecting_section_desc:
                # Shouldn't happen often; prefer not to pollute LOs with overviews.
                continue
            if current_letter.children:
                last = current_letter.children[-1]
                # "(not required)" / "(see also …)" notes sit under the letter, not the last (i).
                if (
                    line.startswith("(")
                    and not _LETTER_OUTCOME.match(line)
                    and not _ROMAN_OUTCOME.match(line)
                ):
                    current_letter.text = (
                        f"{current_letter.text} {line}".strip() if current_letter.text else line
                    )
                else:
                    last.text = f"{last.text} {line}".strip() if last.text else line
            else:
                current_letter.text = (
                    f"{current_letter.text} {line}".strip() if current_letter.text else line
                )
            continue

        # Stray overview paragraph between section banner and first topic.
        if section and not in_learning_outcomes and current_topic is None and _looks_like_prose(line):
            append_section_description(line)
            continue

    finish_letter()

    # Ensure content-only subtopics still exist even if LO block omitted them.
    for code, title in content_titles.items():
        major = int(code.split(".", 1)[0])
        if major in topics:
            ensure_topic(major)
            if not any(s.code == code for s in topics[major].subtopics):
                topics[major].subtopics.append(
                    SyllabusSubtopic(code=code, title=title, outcomes=[], outcomes_text="")
                )

    for topic in topics.values():
        topic.subtopics.sort(key=lambda s: _subtopic_sort_key(s.code))
        for sub in topic.subtopics:
            sub.outcomes_text = sub.outcomes_text or _outcomes_to_text(sub.outcomes)

    # Drop empty description entries.
    section_descriptions = {
        key: value.strip() for key, value in section_descriptions.items() if value and value.strip()
    }
    return [topics[n] for n in sorted(topics)], section_descriptions


def _outcomes_to_text(outcomes: list[SyllabusOutcomeItem]) -> str:
    parts: list[str] = []
    for item in outcomes:
        parts.append(f"({item.code}) {item.text}".strip())
        for child in item.children:
            parts.append(f"({child.code}) {child.text}".strip())
    return " ".join(p for p in parts if p)


# Rebuild models for forward refs
SyllabusOutcomeItem.model_rebuild()

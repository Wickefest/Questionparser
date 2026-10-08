"""Gemini extraction of structured (Paper 2) question packages from page images.

Package model (Factory / practice):
- One main question (Q1, Q2, …) keeps a shared stem + diagrams.
- Parts navigate in order: (a) → (a)(i) → (a)(ii) → (b) → (b)(i) → …
- Classification is one topic + subtopic for the whole package (not per letter).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field

from questbank.llm.gemini_client import GeminiClient
from questbank.pdf.bbox_crop import (
    render_page,
    save_visual_crop,
    validate_and_normalize_bbox,
)
from questbank.types.question import (
    QuestionValidation,
    SourceRegion,
    VisualAsset,
    VisualExpectation,
)
from questbank.types.structured import StructuredPart, StructuredQuestion

logger = logging.getLogger(__name__)

STRUCTURED_QUESTION_INSTRUCTION = """\
You extract structured / Paper-2 Chemistry exam questions from a page IMAGE.
The image is AUTHORITATIVE (these papers are often scans). Do not invent chemistry.

PAGE HEADER
- page_question_header: the printed main question number on THIS page ("1", "2", …)
  if you can see "Q1" / "Q2" / a bold "1" starting a new question.
- If this page has NO new question number — only leftover (i)/(ii)/(iii) or (b)(i) —
  set page_question_header to null and put those parts on the previous question
  (question_ref of the open question, stem="").

PACKAGE MODEL — each main number (Q1, Q2, …) is one package that may span pages:
- Q1 stays Q1 until a new heading Q2 appears. Never merge Q2 into Q1.
- question_ref: ONLY the printed main number. Never invent 0 or 10 from page footers.
- stem: shared intro for THAT question only, BEFORE part (a). Do not put (a)/(i) prompts in stem.
- One question often changes topic between (a) and (b) (e.g. Viton copolymer then polyesters).
  That is still ONE package. Keep both.

PARTS — nested tree in print order for THIS question only:
- Parent letters: label "a", "b", "c"
- Children: label "i", "ii", "iii" (NOT "a(i)" as the child label)
- Printed "(a) (i) …" with no separate (a) sentence → parent "a" with prompt "" + child "i"
- Printed "(b)" intro + diagram, then later "(i) (ii) (iii)" → parent "b" keeps the intro
  prompt; children hold the real questions. Continuation page with only (i)(ii)(iii)
  belongs under the last letter started (usually "b").
- marks: copy [n] printed next to that part. Sum if several 1s belong to one part.
- marks_total: [Total: n] if printed on this page.
- response_kind: "text" | "draw" | "calculation"

VISUALS (same crop rules as MCQ):
- Every chemical structure, polymer repeat unit, Fig., Table-as-image MUST be cropped.
- visuals[].bbox = [ymin, xmin, ymax, xmax] on 0–1000 of THIS page image.
- visuals[].owner = "stem" for the shared intro diagram (e.g. Viton repeat unit),
  or the part label it sits under: "b" for compound P under (b), "a(ii)" if a figure
  belongs to that sub-part.
- Tight crop around the drawing + its labels/caption. Do NOT include neighbouring
  question prose or the whole page.
- Do NOT skip diagrams. A structure with no crop is a failed parse.

OTHER
- Cover/footer-only pages → empty questions list.
- continues_on_next_page true if the question clearly runs onto the next page.

Return data matching the supplied schema only.
"""


class GeminiStemVisualOut(BaseModel):
    bbox: list[float]
    role: Literal["diagram", "graph", "apparatus", "structure", "other"] = "structure"
    owner: str = "stem"


class GeminiStructuredPartOut(BaseModel):
    label: str
    prompt: str = ""
    marks: int | None = None
    response_kind: Literal["text", "draw", "calculation"] = "text"
    children: list[GeminiStructuredPartOut] = Field(default_factory=list)


class GeminiStructuredQuestionOut(BaseModel):
    question_ref: str
    stem: str = ""
    stem_visuals: list[GeminiStemVisualOut] = Field(default_factory=list)
    visuals: list[GeminiStemVisualOut] = Field(default_factory=list)
    parts: list[GeminiStructuredPartOut] = Field(default_factory=list)
    marks_total: int | None = None
    continues_on_next_page: bool = False
    requires_review: bool = False


class GeminiStructuredPageResult(BaseModel):
    page_question_header: str | None = None
    questions: list[GeminiStructuredQuestionOut] = Field(default_factory=list)


def pdf_text_is_sparse(pdf_path: str | Path, *, max_pages: int = 4, min_chars: int = 400) -> bool:
    """True when early pages lack enough native text for layout parsing."""
    path = Path(pdf_path)
    if not path.is_file():
        return True
    total = 0
    try:
        doc = pymupdf.open(path)
        try:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                total += len((page.get_text("text") or "").strip())
        finally:
            doc.close()
    except Exception:  # noqa: BLE001
        return True
    return total < min_chars


def parse_structured_questions_gemini(
    question_pdf: str | Path,
    *,
    assets_dir: str | Path | None = None,
    client: GeminiClient | None = None,
    dpi: int = 150,
    on_progress: Callable[[str], None] | None = None,
) -> list[StructuredQuestion]:
    """Parse structured question packages from page images via Gemini."""
    question_pdf = Path(question_pdf)
    out_assets = Path(assets_dir) if assets_dir else None
    if out_assets is not None:
        out_assets.mkdir(parents=True, exist_ok=True)
    active = client or GeminiClient()
    by_ref: dict[str, StructuredQuestion] = {}
    visuals_by_ref: dict[str, list[VisualAsset]] = {}
    open_ref: str | None = None

    doc = pymupdf.open(question_pdf)
    try:
        total = doc.page_count
        for page_index in range(total):
            if on_progress:
                on_progress(f"Parsing structured questions page {page_index + 1}/{total}")
            rendered = render_page(doc, page_index, dpi=dpi)
            try:
                result = active.generate_structured(
                    prompt=STRUCTURED_QUESTION_INSTRUCTION,
                    response_model=GeminiStructuredPageResult,
                    images=[rendered.image],
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Structured question parse failed on page %s/%s after retries: %s",
                    page_index + 1, total, exc,
                )
                continue
            page_num = page_index + 1
            header = _norm_ref(result.page_question_header or "")
            if header and not _valid_question_ref(header):
                header = ""

            for item in result.questions or []:
                ref = _norm_ref(item.question_ref)
                if header:
                    if not ref or not _valid_question_ref(ref):
                        ref = header
                elif open_ref:
                    # No Q heading on this page → leftover (i)/(ii) of the open question.
                    ref = open_ref
                if not ref or not _valid_question_ref(ref):
                    continue

                parts = [_to_part(p) for p in (item.parts or [])]
                owned_visuals = list(item.visuals or []) + list(item.stem_visuals or [])
                if out_assets is not None:
                    for vi, visual in enumerate(owned_visuals):
                        owner = (visual.owner or "stem").strip().lower() or "stem"
                        asset = _crop_stem_visual(
                            rendered,
                            visual.bbox,
                            out_assets / f"q{ref}-{_safe_owner(owner)}-{page_num}-{vi + 1}.png",
                            page_num,
                            role=visual.role,
                        )
                        if not asset:
                            continue
                        filename = Path(asset.path).name
                        if owner in {"stem", "intro", ""}:
                            visuals_by_ref.setdefault(ref, []).append(asset)
                        else:
                            _attach_part_visual(parts, owner, filename, visual.role)

                existing = by_ref.get(ref)
                if existing is None:
                    stem = (item.stem or "").strip()
                    parts = sanitize_package_parts(stem, parts)
                    status = "review" if item.requires_review else "pass"
                    assets = list(visuals_by_ref.get(ref, []))
                    by_ref[ref] = StructuredQuestion(
                        question_number=int(ref) if ref.isdigit() else _safe_int(ref),
                        source=SourceRegion(page_start=page_num, page_end=page_num),
                        stem=stem,
                        parts=parts,
                        marks_total=item.marks_total,
                        visual=VisualExpectation(
                            required=bool(assets),
                            extraction_pending=False,
                            assets=assets,
                        ),
                        validation=QuestionValidation(status=status, issues=[]),
                    )
                else:
                    incoming_stem = (item.stem or "").strip()
                    # Continuation pages should have empty stem. A long conflicting
                    # stem on a header-less page is usually a wrong question_ref —
                    # but header-less pages are already forced to open_ref.
                    if header and incoming_stem and not _stems_compatible(
                        existing.stem or "", incoming_stem
                    ):
                        continue
                    if incoming_stem and not (existing.stem or "").strip():
                        existing.stem = incoming_stem
                    package_stem = existing.stem or incoming_stem
                    existing.parts = sanitize_package_parts(
                        package_stem,
                        _merge_parts(existing.parts, parts),
                    )
                    if item.marks_total is not None:
                        existing.marks_total = item.marks_total
                    if existing.source:
                        existing.source.page_end = max(existing.source.page_end, page_num)
                    assets = list(visuals_by_ref.get(ref, []))
                    existing.visual = VisualExpectation(
                        required=bool(assets) or existing.visual.required,
                        extraction_pending=False,
                        assets=assets,
                    )
                    if item.requires_review:
                        existing.validation = QuestionValidation(status="review", issues=[])
                open_ref = ref
    finally:
        doc.close()

    for q in by_ref.values():
        q.parts = sanitize_package_parts(q.stem or "", q.parts or [])
    return [by_ref[k] for k in sorted(by_ref.keys(), key=_ref_sort)]


def flatten_structured_parts(
    parts: list[StructuredPart],
    *,
    prefix: str = "",
) -> list[dict]:
    """Depth-first walk for review navigation: a(i), a(ii), b, b(i)…

    Empty letter shells (e.g. bare (a) with only children) are skipped — nav
    starts at (a)(i). Composite labels still use the letter prefix.
    """
    out: list[dict] = []

    def walk(items: list[StructuredPart], parent: str) -> None:
        for part in items:
            label = part.label if not parent else f"{parent}({part.label})"
            kind = getattr(part, "response_kind", None) or _infer_response_kind(part.prompt)
            prompt = part.prompt or ""
            section_only = bool(part.children) and not prompt.strip() and not (part.visuals or [])
            # Skip empty section headers; keep real prompts (letter or roman).
            if not section_only:
                out.append(
                    {
                        "label": label,
                        "prompt": prompt,
                        "marks": part.marks,
                        "responseKind": kind,
                        "hasChildren": bool(part.children),
                        "sectionOnly": False,
                        "visuals": list(part.visuals or []),
                    }
                )
            if part.children:
                walk(part.children, label)

    walk(parts, prefix)
    return out


def _infer_response_kind(prompt: str) -> str:
    text = (prompt or "").lower()
    if any(k in text for k in ("draw", "sketch", "structure of", "structures of")):
        return "draw"
    if any(k in text for k in ("calculate", "minimum number", "how many", "mr ", "relative molecular")):
        return "calculation"
    return "text"


def _to_part(item: GeminiStructuredPartOut) -> StructuredPart:
    part = StructuredPart(
        label=str(item.label or "").strip().lower() or "?",
        prompt=(item.prompt or "").strip(),
        marks=item.marks,
        children=[_to_part(c) for c in (item.children or [])],
    )
    object.__setattr__(part, "response_kind", item.response_kind or _infer_response_kind(part.prompt))
    object.__setattr__(part, "visuals", [])
    return part


def _merge_parts(
    existing: list[StructuredPart],
    incoming: list[StructuredPart],
) -> list[StructuredPart]:
    """Merge part trees. Orphan romans on a continuation page attach to the last letter."""
    if not incoming:
        return existing
    if not existing:
        return list(incoming)
    if incoming and all(_is_roman_label(p.label) for p in incoming):
        last_letter = next(
            (p for p in reversed(existing) if re.fullmatch(r"[a-z]", (p.label or "").lower())
             and not _is_roman_label(p.label)),
            None,
        )
        if last_letter is not None:
            last_letter.children = _merge_parts(list(last_letter.children or []), incoming)
            return existing
    by_label = {p.label: p for p in existing}
    out = list(existing)
    for part in incoming:
        prev = by_label.get(part.label)
        if prev is None:
            out.append(part)
            by_label[part.label] = part
            continue
        if part.prompt and not (prev.prompt or "").strip():
            prev.prompt = part.prompt
        if part.marks is not None:
            prev.marks = part.marks
        if getattr(part, "response_kind", None) and not getattr(prev, "response_kind", None):
            object.__setattr__(prev, "response_kind", part.response_kind)
        incoming_vis = list(part.visuals or [])
        if incoming_vis:
            prev.visuals = list(prev.visuals or []) + incoming_vis
        if part.children:
            prev.children = _merge_parts(prev.children, part.children)
    return out


def sanitize_package_parts(stem: str, parts: list[StructuredPart]) -> list[StructuredPart]:
    """Nest (a)→(i)/(ii). Keep all parts of the same question (a/b may change topic)."""
    del stem
    return repair_part_nesting(parts)


def _stems_compatible(existing_stem: str, incoming_stem: str) -> bool:
    """Continuation pages may have empty stem; conflicting long stems → different Q."""
    a = (existing_stem or "").strip().lower()
    b = (incoming_stem or "").strip().lower()
    if not b or not a:
        return True
    wa = {w for w in re.findall(r"[a-z0-9]+", a) if len(w) > 3}
    wb = {w for w in re.findall(r"[a-z0-9]+", b) if len(w) > 3}
    if not wa or not wb:
        return True
    overlap = len(wa & wb)
    return overlap >= min(4, max(1, len(wb) // 3))


def _crop_stem_visual(rendered, bbox, path: Path, page_num: int, *, role: str) -> VisualAsset | None:
    try:
        norm, _review = validate_and_normalize_bbox(bbox, question_ref=str(page_num))
        if norm is None:
            return None
        saved_path, _mime = save_visual_crop(rendered.image, norm, dest=path)
        from questbank.types.layout import BoundingBox

        return VisualAsset(
            role="stem",
            option_label=None,
            page=page_num,
            bounding_box=BoundingBox(x0=0, y0=0, x1=1, y1=1),
            path=str(saved_path).replace("\\", "/"),
            mime_type="image/png",
        )
    except Exception:  # noqa: BLE001
        return None


def _safe_owner(owner: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (owner or "stem").lower()).strip("-")
    return text or "stem"


def _attach_part_visual(parts: list[StructuredPart], owner: str, filename: str, role: str) -> bool:
    """Attach a cropped diagram to the matching letter/roman part."""
    want = normalize_structured_part_label(owner)
    if not want:
        return False

    def walk(items: list[StructuredPart], prefix: str) -> bool:
        for part in items or []:
            label = part.label if not prefix else f"{prefix}({part.label})"
            canon = normalize_structured_part_label(label)
            if canon == want or (part.label or "").lower() == want:
                vis = list(part.visuals or [])
                vis.append({"asset": filename, "role": role})
                part.visuals = vis
                return True
            if walk(list(part.children or []), label):
                return True
        return False

    return walk(parts, "")


def repair_part_nesting(parts: list[StructuredPart]) -> list[StructuredPart]:
    """Nest orphan roman labels (i/ii/iii) under the preceding letter part (a/b/…)."""
    if not parts:
        return parts

    # Flatten roman-under-roman nests (Gemini sometimes nests ii under i).
    flat: list[StructuredPart] = []

    def _push_unwrapped(part: StructuredPart) -> None:
        lab = (part.label or "").strip().lower()
        kids = list(part.children or [])
        if _is_roman_label(lab) and kids:
            head = StructuredPart(
                label=part.label,
                prompt=part.prompt,
                marks=part.marks,
                children=[],
            )
            object.__setattr__(
                head,
                "response_kind",
                getattr(part, "response_kind", None) or _infer_response_kind(part.prompt),
            )
            object.__setattr__(head, "visuals", list(part.visuals or []))
            flat.append(head)
            for kid in kids:
                _push_unwrapped(kid)
            return
        # Keep letter parents; leave their children empty for now and re-attach below.
        if kids and re.fullmatch(r"[a-z]", lab) and not _is_roman_label(lab):
            saved = kids
            part.children = []
            flat.append(part)
            for kid in saved:
                _push_unwrapped(kid)
            return
        part.children = []
        flat.append(part)
        for kid in kids:
            _push_unwrapped(kid)

    for part in parts:
        _push_unwrapped(part)

    out: list[StructuredPart] = []
    current_letter: StructuredPart | None = None
    for part in flat:
        lab = (part.label or "").strip().lower()
        # Romans before letters — "i"/"v"/"x" are sub-parts, not section letters.
        if re.fullmatch(r"[ivxlc]+", lab) and _is_roman_label(lab):
            if current_letter is None:
                current_letter = StructuredPart(label="a", prompt="", marks=None, children=[])
                object.__setattr__(current_letter, "response_kind", "text")
                out.append(current_letter)
            if not any((c.label or "").lower() == lab for c in current_letter.children):
                current_letter.children.append(part)
            continue
        if re.fullmatch(r"[a-z]", lab):
            current_letter = part
            out.append(part)
            continue
        m = re.fullmatch(r"([a-z])\(([ivxlc]+)\)", lab)
        if m and _is_roman_label(m.group(2)):
            letter, roman = m.group(1), m.group(2)
            parent = next((p for p in out if (p.label or "").lower() == letter), None)
            if parent is None:
                parent = StructuredPart(label=letter, prompt="", marks=None, children=[])
                object.__setattr__(parent, "response_kind", "text")
                out.append(parent)
            current_letter = parent
            child = StructuredPart(
                label=roman,
                prompt=part.prompt,
                marks=part.marks,
                children=[],
            )
            object.__setattr__(
                child,
                "response_kind",
                getattr(part, "response_kind", None) or _infer_response_kind(part.prompt),
            )
            object.__setattr__(child, "visuals", list(part.visuals or []))
            if not any((c.label or "").lower() == roman for c in parent.children):
                parent.children.append(child)
            continue
        out.append(part)
        current_letter = None
    return out


def _is_roman_label(lab: str) -> bool:
    """True for i, ii, iii, iv, v, … (not arbitrary letter strings)."""
    lab = (lab or "").strip().lower()
    if not lab or not re.fullmatch(r"[ivxlc]+", lab):
        return False
    # Valid short roman numerals used in exam part labels.
    return lab in {
        "i",
        "ii",
        "iii",
        "iv",
        "v",
        "vi",
        "vii",
        "viii",
        "ix",
        "x",
        "xi",
        "xii",
        "xiii",
        "xiv",
        "xv",
    }


def normalize_structured_part_label(label: str) -> str:
    """Canonical composite label: a, a(i), a(ii), b(iii).

    Accepts variants like '(a)(i)', 'a(i)', 'A (i)', 'ai'.
    """
    raw = str(label or "").strip().lower()
    if not raw:
        return ""
    # Prefer explicit letter + roman from parentheses / separators.
    m = re.fullmatch(
        r"\(?([a-z])\)?\s*\(?([ivxlc]+)\)?",
        raw.replace(" ", ""),
    )
    if m and _is_roman_label(m.group(2)):
        return f"{m.group(1)}({m.group(2)})"
    m = re.fullmatch(r"\(?([a-z])\)?", raw.replace(" ", ""))
    if m:
        return m.group(1)
    # Compact form: ai, aii, biii
    compact = re.sub(r"[^a-z]", "", raw)
    m = re.fullmatch(r"([a-z])([ivxlc]+)", compact)
    if m and _is_roman_label(m.group(2)):
        return f"{m.group(1)}({m.group(2)})"
    if re.fullmatch(r"[a-z]", compact):
        return compact
    if _is_roman_label(compact):
        return compact
    return compact or raw


def _valid_question_ref(ref: str) -> bool:
    if not ref.isdigit():
        return False
    n = int(ref)
    return 1 <= n <= 40


def _norm_ref(ref: str) -> str:
    text = str(ref or "").strip()
    if text.upper().startswith("Q"):
        text = text[1:].strip()
    text = text.lstrip("0") or text
    return text


def _safe_int(ref: str) -> int:
    digits = re.sub(r"\D", "", ref)
    return int(digits) if digits else 0


def _ref_sort(ref: str) -> tuple:
    if ref.isdigit():
        return (0, int(ref))
    return (1, ref)


__all__ = [
    "flatten_structured_parts",
    "normalize_structured_part_label",
    "parse_structured_questions_gemini",
    "pdf_text_is_sparse",
    "repair_part_nesting",
    "sanitize_package_parts",
]

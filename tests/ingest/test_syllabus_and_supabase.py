from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from questbank.integrations.supabase_store import map_option_rows, map_question_row, push_mcq_bank
from questbank.syllabus.classify_mcq import classify_question
from questbank.syllabus.parse_syllabus_pdf import (
    SyllabusSubtopic,
    SyllabusTaxonomy,
    SyllabusTopic,
    parse_syllabus_text,
)
from questbank.types.ingest import (
    PaperIdentity,
    QuestionRecord,
    TopicClassification,
    question_ref_from_number,
)
from questbank.types.layout import BoundingBox
from questbank.types.question import (
    ParsedOption,
    ParsedQuestion,
    SourceRegion,
    VisualExpectation,
)

IDENTITY = PaperIdentity(source_exam_year=2023, paper_code="6092/01", paper_type="mcq")

SYLLABUS_FIXTURE = """
CONTENT STRUCTURE
Sections Topics
I. Matter - Structures and Properties
1. Experimental Chemistry
2. The Particulate Nature of Matter
3. Chemical Bonding and Structure

SUBJECT CONTENT
SECTION I: MATTER - STRUCTURES AND PROPERTIES

Overview
Matter is understood in terms of particles, the way particles are arranged and the forces that hold them together.
Evidence of the particulate nature of matter come from daily observable phenomena such as diffusion and crystal growth.

1. Experimental Chemistry
Content
1.1 Experimental Design
1.2 Methods of Purification and Analysis
Learning Outcomes
Candidates should be able to:
1.1 Experimental Design
(a) name appropriate apparatus for the measurement of time, temperature, mass and volume
(b) suggest suitable apparatus, given relevant information, for a variety of simple experiments

1.2 Methods of Purification and Analysis
(a) describe methods of separation and purification for the components of mixtures, to include:
(i) use of a suitable solvent, filtration and crystallisation or evaporation
(ii) sublimation
(iii) distillation and fractional distillation
(b) suggest suitable separation and purification methods
(c) interpret paper chromatograms including comparison with known samples

2. The Particulate Nature of Matter
Content
2.1 Kinetic Particle Theory
2.2 Atomic Structure
Learning Outcomes
Candidates should be able to:
2.1 Kinetic Particle Theory
(a) describe solid liquid gaseous states diffusion
2.2 Atomic Structure
(e) define the term isotopes
(f) deduce protons neutrons electrons in atoms and ions

3
Formulae, Stoichiometry and the Mole Concept
Learning Outcomes
Candidates should be able to:
(a) state the symbols of the elements
(b) deduce formulae of simple compounds
(c) deduce formulae of ionic compounds
(d) interpret chemical equations
(e) construct chemical equations
(f) define relative atomic mass
(g) define relative molecular mass
(h) calculate the percentage mass of an element
(i) calculate relative molecular mass from a formula
(j) calculate the percentage mass of an element in a compound
"""


def test_parse_syllabus_text_topics_and_subtopics():
    taxonomy = parse_syllabus_text(SYLLABUS_FIXTURE)
    numbers = [t.number for t in taxonomy.topics]
    assert 1 in numbers and 2 in numbers
    by_num = {t.number: t for t in taxonomy.topics}
    codes = {s.code for s in by_num[1].subtopics} | {s.code for s in by_num[2].subtopics}
    assert "1.1" in codes or "1.2" in codes
    assert "2.2" in codes


def test_parse_lettered_outcomes_with_roman_subs():
    taxonomy = parse_syllabus_text(SYLLABUS_FIXTURE)
    by_num = {t.number: t for t in taxonomy.topics}
    sub_12 = next(s for s in by_num[1].subtopics if s.code == "1.2")
    letters = {o.code for o in sub_12.outcomes}
    assert "a" in letters and "b" in letters and "c" in letters
    roman_children = sub_12.outcomes[0].children
    roman_codes = {c.code for c in roman_children}
    assert {"i", "ii", "iii"} <= roman_codes
    assert "filtration" in sub_12.outcomes[0].children[0].text.lower()


def test_section_overview_not_used_as_topic_title():
    taxonomy = parse_syllabus_text(SYLLABUS_FIXTURE)
    titles = [t.title for t in taxonomy.topics]
    assert all("particles are arranged" not in t.lower() for t in titles)
    assert any("MATTER" in (key or "").upper() for key in taxonomy.section_descriptions)
    desc = next(iter(taxonomy.section_descriptions.values()))
    assert "particles" in desc.lower()
    assert "6092" not in desc


def test_bare_topic_number_without_period():
    taxonomy = parse_syllabus_text(SYLLABUS_FIXTURE)
    by_num = {t.number: t for t in taxonomy.topics}
    assert 3 in by_num
    assert "Mole Concept" in by_num[3].title
    codes = {o.code for o in by_num[3].outcomes}
    assert "a" in codes and "i" in codes and "j" in codes
    # Topic 3 letters must not leak onto topic 2 subtopics.
    for sub in by_num[2].subtopics:
        assert "Mole Concept" not in (sub.outcomes_text or "")
        assert all("symbols of the elements" not in (o.text or "") for o in sub.outcomes)


def test_strip_repeated_page_chrome():
    from questbank.syllabus.parse_syllabus_pdf import _strip_repeated_page_chrome

    pages = [
        "6092 CHEMISTRY GCE ORDINARY LEVEL SYLLABUS\n11\nSECTION I: MATTER\nOverview\nMatter is understood in terms of particles.",
        "6092 CHEMISTRY GCE ORDINARY LEVEL SYLLABUS\n12\n1. Experimental Chemistry\n(a) name apparatus",
        "6092 CHEMISTRY GCE ORDINARY LEVEL SYLLABUS\n13\n(b) suggest apparatus",
    ]
    cleaned = _strip_repeated_page_chrome(pages)
    joined = "\n".join(cleaned)
    assert "6092 CHEMISTRY GCE ORDINARY LEVEL SYLLABUS" not in joined
    assert "Matter is understood" in joined
    assert "(a) name apparatus" in joined
    # Lone page numbers may remain; the parser ignores them unless a title follows.


def test_chrome_keeps_repeating_outline_markers():
    """PDF hanging-indent often emits a bare '(i)' / '(f)' on its own line.

    Those labels repeat across many pages and must not be treated as headers.
    """
    from questbank.syllabus.parse_syllabus_pdf import _strip_repeated_page_chrome

    pages = []
    for n in range(12):
        pages.append(
            "CHEMISTRY SYLLABUS HEADER\n"
            f"{10 + n}\n(i)\nuse of a suitable solvent on page {n}\n"
            "(ii) sublimation\n(f)\n"
            f"explain purity on page {n}"
        )
    cleaned = _strip_repeated_page_chrome(pages)
    joined = "\n".join(cleaned)
    assert "CHEMISTRY SYLLABUS HEADER" not in joined
    assert joined.count("(i)") >= 12
    assert joined.count("(f)") >= 12
    assert "use of a suitable solvent on page 0" in joined


def test_split_roman_i_nests_under_letter():
    """Bare '(i)' on its own line still becomes a child of the open letter."""
    text = """
SUBJECT CONTENT
SECTION I: MATTER
1. Experimental Chemistry
Learning Outcomes
1.2 Methods of Purification and Analysis
(a) describe methods of separation, to include:
(i)
use of a suitable solvent, filtration and crystallisation or evaporation
(ii) sublimation
(iii) distillation and fractional distillation
(b) suggest suitable methods for mixtures:
(i)
solid-solid
(ii) solid-liquid
(f)
explain the importance of measuring the purity in substances
"""
    taxonomy = parse_syllabus_text(text)
    sub = next(s for s in taxonomy.topics[0].subtopics if s.code == "1.2")
    by_code = {o.code: o for o in sub.outcomes}
    assert {c.code for c in by_code["a"].children} == {"i", "ii", "iii"}
    assert "filtration" in by_code["a"].children[0].text.lower()
    assert {c.code for c in by_code["b"].children} == {"i", "ii"}
    assert "solid-solid" in by_code["b"].children[0].text.lower()
    assert "f" in by_code
    assert "purity" in by_code["f"].text.lower()
    assert "filtration" not in by_code["a"].text.lower()


def test_exclusion_note_stays_on_letter_not_last_roman():
    text = """
SUBJECT CONTENT
SECTION I: MATTER
5. Acid-Base Chemistry
Learning Outcomes
5.1 Acids and Bases
(b) describe neutrality and relative acidity and alkalinity, in terms of
(i) relative H+ and OH- ion concentrations,
(ii) colour in Universal Indicator, and
(iii) the pH scale
(calculation of pH from hydrogen ion concentration is not required)
(c) describe qualitatively the difference between strong and weak acids
"""
    taxonomy = parse_syllabus_text(text)
    sub = next(s for s in taxonomy.topics[0].subtopics if s.code == "5.1")
    letter_b = next(o for o in sub.outcomes if o.code == "b")
    assert {c.code for c in letter_b.children} == {"i", "ii", "iii"}
    assert "not required" in letter_b.text.lower()
    assert "not required" not in letter_b.children[-1].text.lower()
    assert letter_b.children[-1].text.lower().startswith("the ph scale")


def test_roman_under_h_then_letter_i():
    """(h) may introduce (i)/(ii) and then a real letter (i) must stay a sibling."""
    text = """
SUBJECT CONTENT
SECTION III: CHEMISTRY IN A SUSTAINABLE WORLD
11. Organic Chemistry
Learning Outcomes
11.4 Polymers
(h) describe two methods of recycling plastics as
(i)
physical method (exemplified by melting poly(ethene) waste into pellets)
(ii) chemical method (exemplified by depolymerisation)
(i) describe depolymerisation as a process in which polymers are broken down
(j) discuss the social, economic and environmental issues of recycling plastics
"""
    taxonomy = parse_syllabus_text(text)
    sub = next(s for s in taxonomy.topics[0].subtopics if s.code == "11.4")
    by_code = {o.code: o for o in sub.outcomes}
    assert {c.code for c in by_code["h"].children} == {"i", "ii"}
    assert "physical method" in by_code["h"].children[0].text.lower()
    assert "i" in by_code and "j" in by_code
    assert "depolymerisation" in by_code["i"].text.lower()
    assert not by_code["i"].children


def test_letter_i_after_h_is_not_roman():
    """(h) then (i) then (j) is a letter sequence, not a one-item roman list."""
    text = """
SUBJECT CONTENT
SECTION II: CHEMICAL REACTIONS
7. Redox Chemistry
Learning Outcomes
7.2 Electrochemistry
(h) describe the electrolysis of aqueous copper(II) sulfate with copper electrodes
(i) describe the electroplating of metals, e.g. copper plating
(j) describe the production of electrical energy from simple cells
"""
    taxonomy = parse_syllabus_text(text)
    sub = next(s for s in taxonomy.topics[0].subtopics if s.code == "7.2")
    codes = [o.code for o in sub.outcomes]
    assert codes == ["h", "i", "j"]
    assert not sub.outcomes[0].children


def test_chemistry_pdf_nests_roman_outcomes():
    pdf = Path(__file__).resolve().parents[2] / "Chemistry Syllabus.pdf"
    if not pdf.exists():
        pytest.skip("Chemistry Syllabus.pdf is not in the repo root")
    from questbank.syllabus.parse_syllabus_pdf import parse_syllabus_pdf

    taxonomy = parse_syllabus_pdf(pdf)
    by_num = {t.number: t for t in taxonomy.topics}
    sub_12 = next(s for s in by_num[1].subtopics if s.code == "1.2")
    letter_a = next(o for o in sub_12.outcomes if o.code == "a")
    assert [c.code for c in letter_a.children] == ["i", "ii", "iii", "iv", "v"]
    assert "filtration" in letter_a.children[0].text.lower()
    assert {o.code for o in sub_12.outcomes} >= {"a", "b", "c", "d", "e", "f"}

    sub_72 = next(s for s in by_num[7].subtopics if s.code == "7.2")
    letter_e = next(o for o in sub_72.outcomes if o.code == "e")
    assert {c.code for c in letter_e.children} == {"i", "ii", "iii"}
    letter_h = next(o for o in sub_72.outcomes if o.code == "h")
    assert not letter_h.children
    assert {o.code for o in sub_72.outcomes} >= {"e", "f", "h", "i", "j"}

    sub_114 = next(s for s in by_num[11].subtopics if s.code == "11.4")
    letter_h = next(o for o in sub_114.outcomes if o.code == "h")
    assert {c.code for c in letter_h.children} == {"i", "ii"}
    letter_i = next(o for o in sub_114.outcomes if o.code == "i")
    assert "depolymerisation" in letter_i.text.lower()

    topic_12 = by_num[12]
    letter_c = next(o for o in topic_12.outcomes if o.code == "c")
    assert {c.code for c in letter_c.children} == {"i", "ii", "iii"}



def test_classifier_maps_isotopes_to_atomic_structure():
    taxonomy = parse_syllabus_text(SYLLABUS_FIXTURE)
    record = _record(
        7,
        stem="An atom of an isotope of chlorine has 17 protons and 20 neutrons. How many electrons?",
        options={"A": "17", "B": "20", "C": "37", "D": "3"},
    )
    result = classify_question(record, taxonomy)
    assert result.syllabus_code is not None
    assert "2.2" in (result.syllabus_code or "")
    assert result.topic_match_method == "keyword_overlap"
    assert result.topic_confidence is not None and result.topic_confidence > 0


def test_map_visual_option_uses_placeholder_text():
    record = _record(
        4,
        stem="Which apparatus?",
        options={"A": "", "B": "", "C": "", "D": ""},
        correct="A",
    )
    for label in ("A", "B", "C", "D"):
        record.question.options[label].requires_visual = True
        record.question.options[label].text = None
    options = map_option_rows(record, question_id="q-4")
    assert all(o["text"] == "[visual option]" for o in options)
    assert sum(1 for o in options if o["is_correct"]) == 1


def test_map_question_and_options_include_correct_answer():
    record = _record(
        1,
        stem="Which apparatus?",
        options={"A": "beaker", "B": "burette", "C": "pipette", "D": "flask"},
        correct="B",
        topic=TopicClassification(
            topic="Experimental Design",
            parent_topic="Experimental Chemistry",
            subject="Chemistry",
            syllabus_code="6092:1.1",
            topic_confidence=0.4,
            topic_match_reasoning="test",
            topic_match_method="keyword_overlap",
            status="ok",
        ),
    )
    row = map_question_row(record, bank_id="bank-1", question_id="q-1")
    assert row["correct_answer"] == "B"
    assert row["topic"] == "Experimental Design"
    assert row["syllabus_code"] == "6092:1.1"
    options = map_option_rows(record, question_id="q-1")
    assert len(options) == 4
    assert sum(1 for o in options if o["is_correct"]) == 1
    assert next(o for o in options if o["label"] == "B")["is_correct"] is True


def test_push_mcq_bank_mocked_client():
    taxonomy = SyllabusTaxonomy(
        topics=[
            SyllabusTopic(
                number=2,
                title="The Particulate Nature of Matter",
                subtopics=[SyllabusSubtopic(code="2.2", title="Atomic Structure")],
            )
        ]
    )
    record = _record(
        1,
        stem="Isotopes have same proton number",
        options={"A": "true", "B": "false", "C": "maybe", "D": "never"},
        correct="A",
        topic=TopicClassification(
            topic="Atomic Structure",
            parent_topic="The Particulate Nature of Matter",
            syllabus_code="6092:2.2",
            topic_confidence=0.5,
            topic_match_method="keyword_overlap",
            topic_match_reasoning="test",
            status="ok",
        ),
    )

    class FakeQuery:
        def __init__(self, store, table):
            self.store = store
            self.table_name = table
            self._action = None
            self._payload = None
            self._filters = {}

        def select(self, *_a, **_k):
            self._action = "select"
            return self

        def insert(self, payload):
            self._action = "insert"
            self._payload = payload
            return self

        def update(self, payload):
            self._action = "update"
            self._payload = payload
            return self

        def delete(self):
            self._action = "delete"
            return self

        def eq(self, key, value):
            self._filters[key] = value
            return self

        def in_(self, key, values):
            self._filters[f"in:{key}"] = list(values)
            return self

        def limit(self, *_a, **_k):
            return self

        def execute(self):
            table = self.store.setdefault(self.table_name, [])
            if self._action == "select":
                rows = table
                for key, value in self._filters.items():
                    if key.startswith("in:"):
                        field = key.split(":", 1)[1]
                        rows = [r for r in rows if r.get(field) in value]
                    else:
                        rows = [r for r in rows if r.get(key) == value]
                return SimpleNamespace(data=rows)
            if self._action == "insert":
                payload = self._payload if isinstance(self._payload, list) else [self._payload]
                table.extend(payload)
                return SimpleNamespace(data=payload)
            if self._action == "update":
                for row in table:
                    if all(row.get(k) == v for k, v in self._filters.items() if not k.startswith("in:")):
                        row.update(self._payload)
                return SimpleNamespace(data=[])
            if self._action == "delete":
                keep = []
                for row in table:
                    drop = False
                    for key, value in self._filters.items():
                        if key.startswith("in:"):
                            field = key.split(":", 1)[1]
                            if row.get(field) in value:
                                drop = True
                        elif row.get(key) == value:
                            drop = True
                    if not drop:
                        keep.append(row)
                self.store[self.table_name] = keep
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[])

    class FakeClient:
        def __init__(self):
            self.store: dict = {}

        def table(self, name):
            return FakeQuery(self.store, name)

    client = FakeClient()
    result = push_mcq_bank(
        records=[record],
        paper_identity=IDENTITY,
        bank_name="unit-test-bank",
        source_files=["a.pdf"],
        taxonomy=taxonomy,
        client=client,
    )
    assert result["questions"] == 1
    assert result["options"] == 4
    assert len(client.store["question_banks"]) == 1
    assert len(client.store["questions"]) == 1
    assert client.store["questions"][0]["correct_answer"] == "A"
    assert sum(1 for o in client.store["question_options"] if o["is_correct"]) == 1


def _record(
    number: int,
    *,
    stem: str,
    options: dict[str, str],
    correct: str | None = None,
    topic: TopicClassification | None = None,
) -> QuestionRecord:
    qref = question_ref_from_number(number)
    question = ParsedQuestion(
        question_number=number,
        source=SourceRegion(
            page_start=1,
            page_end=1,
            bounding_box=BoundingBox(x0=0, y0=0, x1=10, y1=10),
        ),
        stem=stem,
        options={
            label: ParsedOption(label=label, text=text, requires_visual=False)
            for label, text in options.items()
        },
        visual=VisualExpectation(required=False, extraction_pending=False),
        validation={"status": "pass", "issues": []},
    )
    return QuestionRecord(
        paper_identity=IDENTITY,
        source_key=IDENTITY.source_key_for(qref),
        question_ref=qref,
        source_regions=[question.source],
        question=question,
        correct_option=correct,  # type: ignore[arg-type]
        topic_classification=topic,
    )

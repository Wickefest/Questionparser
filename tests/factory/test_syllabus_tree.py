"""Syllabus tree persistence: Section → Topic → Subtopic → (a) → (i)."""

from __future__ import annotations

from questbank.factory.store import InMemoryFactoryStore
from questbank.factory.syllabus_service import (
    build_syllabus_tree,
    create_syllabus_record,
    persist_taxonomy_nodes,
)
from questbank.syllabus.parse_syllabus_pdf import (
    SyllabusOutcomeItem,
    SyllabusSubtopic,
    SyllabusTaxonomy,
    SyllabusTopic,
)


def test_persist_letter_and_roman_hierarchy():
    store = InMemoryFactoryStore()
    syllabus = create_syllabus_record(
        store,
        subject="Chemistry",
        code="6092",
        year=2026,
        title="Chemistry 6092 · 2026",
    )
    taxonomy = SyllabusTaxonomy(
        subject="Chemistry",
        syllabus_code="6092",
        section_descriptions={
            "SECTION I": "Matter is understood in terms of particles and structure."
        },
        topics=[
            SyllabusTopic(
                number=1,
                title="Experimental Chemistry",
                section="SECTION I",
                subtopics=[
                    SyllabusSubtopic(
                        code="1.2",
                        title="Methods of Purification and Analysis",
                        outcomes=[
                            SyllabusOutcomeItem(
                                code="a",
                                text="describe methods of separation",
                                children=[
                                    SyllabusOutcomeItem(code="i", text="filtration"),
                                    SyllabusOutcomeItem(code="ii", text="sublimation"),
                                    SyllabusOutcomeItem(code="iii", text="distillation"),
                                ],
                            ),
                            SyllabusOutcomeItem(code="b", text="suggest suitable methods", children=[]),
                        ],
                    )
                ],
            )
        ],
    )
    counts = persist_taxonomy_nodes(store, syllabus_id=syllabus["id"], taxonomy=taxonomy)
    assert counts["topics"] == 1
    assert counts["subtopics"] == 1
    assert counts["outcomes"] == 5  # a + i + ii + iii + b

    detail = store.get_syllabus(syllabus["id"])
    tree = detail["tree"]
    section = tree[0]
    assert "particles" in (section.get("outcomesText") or "")
    topic = section["children"][0]
    sub = topic["children"][0]
    assert sub["code"] == "1.2"
    letter_a = next(c for c in sub["children"] if c["code"].endswith(".a"))
    assert letter_a["title"] == "describe methods of separation"
    romans = {c["code"].split(".")[-1] for c in letter_a["children"]}
    assert romans == {"i", "ii", "iii"}


def test_reimport_overwrites_nodes():
    store = InMemoryFactoryStore()
    syllabus = create_syllabus_record(
        store,
        subject="Chemistry",
        code="6092",
        year=2026,
        title="Chemistry",
    )
    first = SyllabusTaxonomy(
        topics=[
            SyllabusTopic(
                number=1,
                title="Old",
                subtopics=[SyllabusSubtopic(code="1.1", title="Old sub", outcomes=[])],
            )
        ]
    )
    persist_taxonomy_nodes(store, syllabus_id=syllabus["id"], taxonomy=first)
    second = SyllabusTaxonomy(
        topics=[
            SyllabusTopic(
                number=2,
                title="New",
                subtopics=[
                    SyllabusSubtopic(
                        code="2.1",
                        title="New sub",
                        outcomes=[SyllabusOutcomeItem(code="a", text="fresh", children=[])],
                    )
                ],
            )
        ]
    )
    persist_taxonomy_nodes(store, syllabus_id=syllabus["id"], taxonomy=second, replace_existing=True)
    detail = store.get_syllabus(syllabus["id"])
    codes = {n["code"] for n in detail["nodes"]}
    assert "1.1" not in codes
    assert "2.1" in codes
    assert "2.1.a" in codes


def test_build_tree_orders_children():
    nodes = [
        {
            "id": "t1",
            "parent_id": None,
            "code": "1",
            "title": "Topic",
            "node_type": "TOPIC",
            "sort_order": 10,
            "outcomes_text": None,
        },
        {
            "id": "s1",
            "parent_id": "t1",
            "code": "1.1",
            "title": "Sub",
            "node_type": "SUBTOPIC",
            "sort_order": 11,
            "outcomes_text": None,
        },
        {
            "id": "o1",
            "parent_id": "s1",
            "code": "1.1.a",
            "title": "Learn X",
            "node_type": "OUTCOME",
            "sort_order": 12,
            "outcomes_text": None,
        },
    ]
    tree = build_syllabus_tree(nodes)
    assert tree[0]["children"][0]["children"][0]["title"] == "Learn X"

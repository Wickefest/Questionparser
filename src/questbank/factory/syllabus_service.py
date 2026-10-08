"""Persist syllabus taxonomy into Syllabus / SyllabusNode tables."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from questbank.factory.store import FactoryStore
from questbank.syllabus.parse_syllabus_pdf import SyllabusOutcomeItem, SyllabusTaxonomy

# Subjects available in the “Add syllabus” dropdown.
SUBJECT_PRESETS: list[dict[str, Any]] = [
    {
        "subject": "Biology",
        "code": "6093",
        "defaultTitle": "Singapore-Cambridge GCE O-Level Biology (6093)",
    },
    {
        "subject": "Chemistry",
        "code": "6092",
        "defaultTitle": "Singapore-Cambridge GCE O-Level Chemistry (6092)",
    },
    {
        "subject": "Mathematics",
        "code": "4048",
        "defaultTitle": "Singapore-Cambridge GCE O-Level Mathematics (4048)",
    },
    {
        "subject": "Physics",
        "code": "6091",
        "defaultTitle": "Singapore-Cambridge GCE O-Level Physics (6091)",
    },
    {
        "subject": "Additional Mathematics",
        "code": "4049",
        "defaultTitle": "Singapore-Cambridge GCE O-Level Additional Mathematics (4049)",
    },
    {
        "subject": "English",
        "code": "1128",
        "defaultTitle": "Singapore-Cambridge GCE O-Level English Language (1128)",
    },
]


def create_syllabus_record(
    store: FactoryStore,
    *,
    subject: str,
    code: str,
    year: int,
    title: str,
) -> dict[str, Any]:
    return store.create_syllabus(
        {
            "id": str(uuid4()),
            "subject": subject.strip(),
            "code": code.strip(),
            "year": int(year),
            "title": title.strip() or f"{subject} {code} · {year}",
        }
    )


def persist_taxonomy_nodes(
    store: FactoryStore,
    *,
    syllabus_id: str,
    taxonomy: SyllabusTaxonomy,
    replace_existing: bool = True,
) -> dict[str, int]:
    """Map SECTION → TOPIC → SUBTOPIC → (a)/(b) → (i)/(ii) into SyllabusNode rows.

    Re-importing a PDF with replace_existing=True deletes prior nodes for this
    syllabus and writes a fresh tree (overwrite).
    """
    if replace_existing:
        store.delete_syllabus_nodes(syllabus_id)

    topic_count = 0
    subtopic_count = 0
    outcome_count = 0
    sort = 0
    section_ids: dict[str, str] = {}
    section_descriptions = getattr(taxonomy, "section_descriptions", None) or {}

    for topic in taxonomy.topics:
        sort += 10
        parent_id = None
        if topic.section:
            key = topic.section.strip()
            if key not in section_ids:
                description = (section_descriptions.get(key) or "").strip() or None
                section = store.create_syllabus_node(
                    {
                        "id": str(uuid4()),
                        "syllabus_id": syllabus_id,
                        "parent_id": None,
                        "code": f"SEC:{key[:40]}",
                        "title": key,
                        "outcomes_text": description,
                        "node_type": "SECTION",
                        "sort_order": sort,
                    }
                )
                section_ids[key] = section["id"]
            parent_id = section_ids[key]

        topic_node = store.create_syllabus_node(
            {
                "id": str(uuid4()),
                "syllabus_id": syllabus_id,
                "parent_id": parent_id,
                "code": str(topic.number),
                "title": topic.title,
                "outcomes_text": None,
                "node_type": "TOPIC",
                "sort_order": sort,
            }
        )
        topic_count += 1

        for sub in topic.subtopics:
            sort += 1
            sub_node = store.create_syllabus_node(
                {
                    "id": str(uuid4()),
                    "syllabus_id": syllabus_id,
                    "parent_id": topic_node["id"],
                    "code": sub.code,
                    "title": sub.title,
                    "outcomes_text": None,
                    "node_type": "SUBTOPIC",
                    "sort_order": sort,
                }
            )
            subtopic_count += 1
            outcome_count += _persist_outcomes(
                store,
                syllabus_id=syllabus_id,
                parent_id=sub_node["id"],
                prefix=sub.code,
                outcomes=sub.outcomes,
                sort_start=sort,
            )
            sort += max(len(sub.outcomes), 1) * 5

        # Topic-level letters when there are no subtopics (e.g. Maintaining Air Quality).
        if topic.outcomes:
            outcome_count += _persist_outcomes(
                store,
                syllabus_id=syllabus_id,
                parent_id=topic_node["id"],
                prefix=str(topic.number),
                outcomes=topic.outcomes,
                sort_start=sort,
            )

    store.touch_syllabus(syllabus_id)
    return {
        "topics": topic_count,
        "subtopics": subtopic_count,
        "outcomes": outcome_count,
    }


def _persist_outcomes(
    store: FactoryStore,
    *,
    syllabus_id: str,
    parent_id: str,
    prefix: str,
    outcomes: list[SyllabusOutcomeItem],
    sort_start: int,
) -> int:
    count = 0
    sort = sort_start
    for item in outcomes:
        sort += 1
        letter_node = store.create_syllabus_node(
            {
                "id": str(uuid4()),
                "syllabus_id": syllabus_id,
                "parent_id": parent_id,
                "code": f"{prefix}.{item.code}",
                "title": item.text or f"({item.code})",
                "outcomes_text": None,
                "node_type": "OUTCOME",
                "sort_order": sort,
            }
        )
        count += 1
        for child in item.children:
            sort += 1
            store.create_syllabus_node(
                {
                    "id": str(uuid4()),
                    "syllabus_id": syllabus_id,
                    "parent_id": letter_node["id"],
                    "code": f"{prefix}.{item.code}.{child.code}",
                    "title": child.text or f"({child.code})",
                    "outcomes_text": None,
                    "node_type": "OUTCOME",
                    "sort_order": sort,
                }
            )
            count += 1
    return count


def build_syllabus_tree(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nest flat SyllabusNode rows into a tree for the UI."""
    by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        by_id[node["id"]] = {
            "id": node["id"],
            "code": node.get("code"),
            "title": node["title"],
            "outcomesText": node.get("outcomes_text") or node.get("outcomesText"),
            "nodeType": node.get("node_type") or node.get("nodeType"),
            "sortOrder": node.get("sort_order") or node.get("sortOrder"),
            "children": [],
        }

    roots: list[dict[str, Any]] = []
    for node in nodes:
        item = by_id[node["id"]]
        parent_id = node.get("parent_id") or node.get("parentId")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(item)
        else:
            roots.append(item)

    def sort_tree(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items.sort(key=lambda n: (n.get("sortOrder") is None, n.get("sortOrder") or 0, n.get("code") or ""))
        for child in items:
            child["children"] = sort_tree(child["children"])
        return items

    return sort_tree(roots)

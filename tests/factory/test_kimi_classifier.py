from __future__ import annotations

import pytest
from questbank.factory.store import InMemoryFactoryStore
from questbank.llm.kimi_classifier import (
    get_syllabus_id_for_version,
    _fallback_rule_based_classifier,
    _resolve_numbered_classification,
)


def test_get_syllabus_id_for_version_fallback() -> None:
    store = InMemoryFactoryStore()
    # Populate a fake syllabus in store
    store.syllabi["syllabus-1"] = {
        "id": "syllabus-1",
        "subject": "Chemistry",
        "code": "6092",
        "year": 2023,
    }
    
    resolved = get_syllabus_id_for_version(store, "fake-version-id")
    assert resolved == "syllabus-1"


def test_fallback_rule_based_classifier() -> None:
    candidates = [
        {
            "id": "node-1",
            "code": "6092:1",
            "title": "Atomic Structure",
            "node_type": "TOPIC",
        },
        {
            "id": "node-2",
            "code": "6092:2",
            "title": "Chemical Bonding",
            "node_type": "TOPIC",
        },
    ]
    
    q_text = "Which of the following describes covalent chemical bonding in molecules?"
    
    res = _fallback_rule_based_classifier(q_text, candidates)
    assert res["success"] is True
    assert res["nodeId"] == "node-2"
    assert "taxonomy overlap" in res["reasoning"].lower()


def test_resolve_requires_subtopic_when_children_exist() -> None:
    topics_by_id = {
        "t1": {"id": "t1", "code": "1", "title": "Experimental Chemistry", "node_type": "TOPIC"},
    }
    topics_by_code = {"1": topics_by_id["t1"]}
    subtopics_by_code = {
        "1.1": {"id": "s1", "code": "1.1", "title": "Experimental design", "parent_id": "t1", "node_type": "SUBTOPIC"},
        "1.2": {"id": "s2", "code": "1.2", "title": "Methods of purification", "parent_id": "t1", "node_type": "SUBTOPIC"},
    }
    subtopics_by_topic = {"t1": list(subtopics_by_code.values())}

    with pytest.raises(ValueError, match="requires a numbered subtopic"):
        _resolve_numbered_classification(
            topic_code="1",
            subtopic_code=None,
            topics_by_id=topics_by_id,
            topics_by_code=topics_by_code,
            subtopics_by_code=subtopics_by_code,
            subtopics_by_topic=subtopics_by_topic,
        )


def test_resolve_picks_specific_subtopic() -> None:
    topics_by_id = {
        "t1": {"id": "t1", "code": "1", "title": "Experimental Chemistry", "node_type": "TOPIC"},
    }
    topics_by_code = {"1": topics_by_id["t1"]}
    subtopics_by_code = {
        "1.2": {"id": "s2", "code": "1.2", "title": "Methods of purification", "parent_id": "t1", "node_type": "SUBTOPIC"},
    }
    subtopics_by_topic = {"t1": list(subtopics_by_code.values())}

    _topic, topic_code, sub_code, node = _resolve_numbered_classification(
        topic_code="1",
        subtopic_code="1.2",
        topics_by_id=topics_by_id,
        topics_by_code=topics_by_code,
        subtopics_by_code=subtopics_by_code,
        subtopics_by_topic=subtopics_by_topic,
    )
    assert topic_code == "1"
    assert sub_code == "1.2"
    assert node["id"] == "s2"


def test_resolve_topic_without_children_is_topic_only() -> None:
    topics_by_id = {
        "t10": {"id": "t10", "code": "10", "title": "Rate of Reactions", "node_type": "TOPIC"},
    }
    topics_by_code = {"10": topics_by_id["t10"]}

    _topic, topic_code, sub_code, node = _resolve_numbered_classification(
        topic_code="10",
        subtopic_code=None,
        topics_by_id=topics_by_id,
        topics_by_code=topics_by_code,
        subtopics_by_code={},
        subtopics_by_topic={},
    )
    assert topic_code == "10"
    assert sub_code is None
    assert node["id"] == "t10"


def test_resolve_topic_without_children_ignores_fake_subtopic() -> None:
    topics_by_id = {
        "t10": {"id": "t10", "code": "10", "title": "Rate of Reactions", "node_type": "TOPIC"},
    }
    topics_by_code = {"10": topics_by_id["t10"]}

    _topic, topic_code, sub_code, node = _resolve_numbered_classification(
        topic_code="10",
        subtopic_code="10.1",
        topics_by_id=topics_by_id,
        topics_by_code=topics_by_code,
        subtopics_by_code={},
        subtopics_by_topic={},
    )
    assert topic_code == "10"
    assert sub_code is None
    assert node["id"] == "t10"


def test_fallback_bloom_classifier() -> None:
    from questbank.llm.kimi_classifier import _fallback_bloom_classifier
    
    store = InMemoryFactoryStore()
    
    # Mock connection cursor for the update statement
    from unittest.mock import MagicMock
    store._conn = MagicMock()
    
    # 1. Applying verb
    res = _fallback_bloom_classifier("Calculate the mass of element Z in grams.", "fake-version-id", store)
    assert res["success"] is True
    assert res["bloomLevel"] == "Applying"
    assert "calcul" in res["reasoning"].lower()
    
    # 2. Analyzing verb
    res2 = _fallback_bloom_classifier("Study the chromatogram diagram on the right.", "fake-version-id", store)
    assert res2["success"] is True
    assert res2["bloomLevel"] == "Analyzing"
    assert "chromatogram" in res2["reasoning"].lower()


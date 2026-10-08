"""Kimi Moonshot AI classification agent for matching question versions to syllabus nodes."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any
import httpx

from questbank.factory.store import FactoryStore

logger = logging.getLogger(__name__)

KIMI_API_URL = (
    os.environ.get("MOONSHOT_API_URL")
    or os.environ.get("KIMI_API_URL")
    or "https://api.moonshot.ai/v1/chat/completions"
).strip()
KIMI_MODEL = (
    os.environ.get("MOONSHOT_MODEL")
    or os.environ.get("KIMI_MODEL")
    or "kimi-k2.6"
).strip()


def _store_rollback(store: FactoryStore | None) -> None:
    if store is None:
        return
    rollback = getattr(store, "rollback", None)
    if callable(rollback):
        rollback()
    elif hasattr(store, "ensure_ready"):
        store.ensure_ready()  # type: ignore[union-attr]


KIMI_TIMEOUT_S = float(os.environ.get("MOONSHOT_TIMEOUT_S") or os.environ.get("KIMI_TIMEOUT_S") or "120")
KIMI_THINKING = (
    os.environ.get("MOONSHOT_THINKING")
    or os.environ.get("KIMI_THINKING")
    or "disabled"
).strip().lower()


def _kimi_chat(messages: list[dict[str, Any]], *, temperature: float = 1) -> str:
    """Call Moonshot/Kimi chat completions and return assistant text."""
    api_key = (
        os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("KIMI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("MOONSHOT_API_KEY is not configured")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": KIMI_MODEL,
        "messages": messages,
    }

    if KIMI_THINKING == "disabled":
        payload["thinking"] = {"type": "disabled"}
        payload["temperature"] = 0.6
    else:
        payload["thinking"] = {"type": "enabled"}
        payload["temperature"] = 1.0

    response = httpx.post(KIMI_API_URL, headers=headers, json=payload, timeout=KIMI_TIMEOUT_S)
    response.raise_for_status()
    res_data = response.json()
    return str(res_data["choices"][0]["message"]["content"]).strip()


def get_syllabus_id_for_version(store: FactoryStore, version_id: str) -> str | None:
    """Resolve the syllabus ID for a given question version using multiple fallbacks."""
    if not hasattr(store, "_conn"):
        # In-memory or dummy fallback
        if hasattr(store, "syllabi") and store.syllabi:
            return list(store.syllabi.keys())[0]
        return None

    with store._conn.cursor() as cur:
        # 1. Join version to draft to batch to syllabus
        cur.execute(
            """
            SELECT b.syllabus_id::text
            FROM "question_versions" v
            JOIN "question_drafts" d ON d.id = v.source_draft_id
            JOIN "ingestion_batches" b ON b.id = d.batch_id
            WHERE v.id = %s::uuid
            """,
            (version_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        # 2. Join bank_questions to active drafts
        cur.execute(
            """
            SELECT b.syllabus_id::text
            FROM "question_versions" v
            JOIN "bank_questions" q ON q.id = v.question_id
            JOIN "question_drafts" d ON d.batch_id IN (
                SELECT id FROM "ingestion_batches" WHERE syllabus_id IS NOT NULL
            )
            JOIN "ingestion_batches" b ON b.id = d.batch_id
            WHERE v.id = %s::uuid
            LIMIT 1
            """,
            (version_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        # 3. Get first available syllabus matching the subject
        cur.execute(
            """
            SELECT s.id::text
            FROM "syllabuses" s
            JOIN "bank_questions" q ON q.subject = s.subject
            JOIN "question_versions" v ON v.question_id = q.id
            WHERE v.id = %s::uuid
            LIMIT 1
            """,
            (version_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

        # 4. Global syllabus fallback
        cur.execute('SELECT id::text FROM "syllabuses" LIMIT 1')
        row = cur.fetchone()
        if row and row[0]:
            return row[0]

    return None


def extract_plain_text(content: Any) -> str:
    """Helper to merge visual draft content blocks into a cohesive paragraph."""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("value") or ""))
        return " ".join(parts)
    return str(content)


def _code_sort_key(code: Any) -> tuple[int, ...]:
    parts = [int(p) for p in re.findall(r"\d+", str(code or ""))]
    return tuple(parts) if parts else (10**9,)


def _is_topic_code(code: Any) -> bool:
    return bool(re.fullmatch(r"\d+", str(code or "").strip()))


def _is_subtopic_code(code: Any) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+", str(code or "").strip()))


def _normalize_optional_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "na"}:
        return None
    return text


def _topic_subtopic_agent_prompt(q_text: str, opts_str: str, hierarchy: str) -> str:
    """Two-tier Topic → Subtopic prompt.

    Topic is always mandatory. Subtopic is mandatory when the chosen topic lists
    numbered children in the syllabus; otherwise subtopic must be null.
    Outcomes are context only — never assign an outcome as the classification node.
    """
    return f"""You are a Singapore GCE O-Level Science Syllabus Classification Agent.

Use a strict TWO-TIER hierarchy: Topic -> Subtopic (numbered codes only).
Classify using ONLY the numeric syllabus codes below.
Do NOT invent codes. Do NOT use UUIDs. Titles / learning outcomes are hints; CODES are mandatory.
Do NOT classify to OUTCOME nodes — outcomes are supporting text only.

[QUESTION TEXT]
{q_text}
{opts_str}

[NUMBERED SYLLABUS HIERARCHY]
{hierarchy}

CORE RULES:
1. MANDATORY TOPIC: every question gets exactly one TOPIC code (integer string like "1", "2", "10", "11").
2. SUBTOPIC WHEN CHILDREN EXIST: if the chosen TOPIC lists numbered SUBTOPICS (e.g. "1.1", "1.2"), you MUST choose exactly one of those subtopic codes. Never leave subtopic null in that case.
3. TOPIC-ONLY WHEN NO CHILDREN: if the chosen TOPIC has NO numbered subtopics in the hierarchy (e.g. topic "6", "9", "10", "12" may only have learning outcomes), set selected_subtopic_code to null. Do NOT invent a fake "10.1".
4. A subtopic code MUST belong under the chosen topic (topic "1" → only "1.1","1.2",…; topic "11" → only "11.1",…).
5. Prefer the most specific numbered subtopic that matches the question when children exist.

Examples:
- Question about chromatography under Experimental Chemistry → topic "1", subtopic "1.2"
- Question under Qualitative Analysis (topic has no numbered subtopics) → topic "6", subtopic null

Respond with raw JSON only (no markdown fences):
{{
  "selected_topic_code": "1",
  "selected_subtopic_code": "1.2",
  "reasoning": "Short justification referencing the numeric codes."
}}
"""


def _resolve_numbered_classification(
    *,
    topic_code: str,
    subtopic_code: str | None,
    topics_by_id: dict[str, dict[str, Any]],
    topics_by_code: dict[str, dict[str, Any]],
    subtopics_by_code: dict[str, dict[str, Any]],
    subtopics_by_topic: dict[str, list[dict[str, Any]]],
    legacy_topic_id: Any = None,
) -> tuple[dict[str, Any], str, str | None, dict[str, Any]]:
    """Resolve Topic (required) + Subtopic (required iff topic has numbered children).

    Returns (selected_topic, topic_code, final_subtopic_code, final_node).
    """
    selected_topic = None
    if topic_code in topics_by_code:
        selected_topic = topics_by_code[topic_code]
    elif legacy_topic_id in topics_by_id:
        selected_topic = topics_by_id[legacy_topic_id]
        topic_code = str(selected_topic.get("code") or "").strip()

    if not selected_topic:
        raise ValueError(f"Invalid or missing numbered topic code: {topic_code!r}")

    selected_topic_id = selected_topic["id"]
    numbered_subs = subtopics_by_topic.get(selected_topic_id, [])
    numbered_sub_codes = {
        str(s.get("code") or "").strip(): s for s in numbered_subs
    }

    if not numbered_subs:
        # Standalone topic (outcomes may exist, but no numbered subtopics)
        if subtopic_code:
            logger.info(
                "Ignoring subtopic %s for topic-only node %s (no numbered children)",
                subtopic_code,
                topic_code,
            )
        return selected_topic, topic_code, None, selected_topic

    if not subtopic_code:
        raise ValueError(
            f"Topic {topic_code} requires a numbered subtopic "
            f"({', '.join(sorted(numbered_sub_codes, key=_code_sort_key))}); got null"
        )

    if subtopic_code in numbered_sub_codes:
        final_node = numbered_sub_codes[subtopic_code]
        return selected_topic, topic_code, subtopic_code, final_node

    if subtopic_code in subtopics_by_code:
        cand = subtopics_by_code[subtopic_code]
        parent_id = cand.get("parent_id")
        if parent_id in topics_by_id:
            logger.warning(
                "Healing code mismatch: topic %s vs subtopic %s (parent %s)",
                topic_code,
                subtopic_code,
                topics_by_id[parent_id].get("code"),
            )
            selected_topic = topics_by_id[parent_id]
            topic_code = str(selected_topic.get("code") or "").strip()
            return selected_topic, topic_code, subtopic_code, cand
        raise ValueError(f"Subtopic {subtopic_code} is not under topic {topic_code}")

    raise ValueError(
        f"Subtopic {subtopic_code!r} is not a valid numbered child of Topic {topic_code} "
        f"({', '.join(sorted(numbered_sub_codes, key=_code_sort_key))})"
    )


def classify_with_kimi(
    store: FactoryStore,
    version_id: str,
) -> dict[str, Any]:
    """Run GCE O-Level hierarchical topic & subtopic classification using Kimi Moonshot AI."""
    if hasattr(store, "ensure_ready"):
        store.ensure_ready()  # type: ignore[union-attr]
    # Retrieve the question version
    with store._conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.id::text, q.source_key, q.subject, v.content, v.options
            FROM "question_versions" v
            JOIN "bank_questions" q ON q.id = v.question_id
            WHERE v.id = %s::uuid
            """,
            (version_id,),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f"Question version not found: {version_id}")

    v_id, source_key, subject, content, options = row
    q_text = extract_plain_text(content)

    # Reconstruct options if dictionary
    opts_str = ""
    if isinstance(options, dict):
        for key, opt in sorted(options.items()):
            if isinstance(opt, dict):
                opts_str += f"\nOption {key}: {opt.get('value', '')}"
            else:
                opts_str += f"\nOption {key}: {opt}"

    syllabus_id = get_syllabus_id_for_version(store, version_id)
    if not syllabus_id:
        return {
            "success": False,
            "message": f"No syllabus found for version {version_id}.",
        }

    # Fetch all nodes for the resolved syllabus
    nodes = store.list_syllabus_nodes(syllabus_id)

    # Numbered topics only (1, 2, 10, 11...) — ignore unnumbered section headers
    topics = [
        n for n in nodes
        if n.get("node_type") == "TOPIC" and _is_topic_code(n.get("code"))
    ]
    # Numbered subtopics only (1.1, 1.2, 11.3...)
    subtopics = [
        n for n in nodes
        if n.get("node_type") == "SUBTOPIC" and _is_subtopic_code(n.get("code"))
    ]
    outcomes = [n for n in nodes if n.get("node_type") == "OUTCOME"]

    if not topics:
        return {
            "success": False,
            "message": "Syllabus has no numbered TOPIC nodes (e.g. 1, 2, 10).",
        }

    topics_by_id = {t["id"]: t for t in topics}
    topics_by_code = {str(t.get("code")).strip(): t for t in topics}
    subtopics_by_id = {s["id"]: s for s in subtopics}
    subtopics_by_code = {str(s.get("code")).strip(): s for s in subtopics}

    # Group numbered subtopics and outcomes by parent ID
    subtopics_by_topic: dict[str, list[dict[str, Any]]] = {}
    for sub in subtopics:
        p_id = sub.get("parent_id")
        if p_id:
            subtopics_by_topic.setdefault(p_id, []).append(sub)

    outcomes_by_parent: dict[str, list[str]] = {}
    for out in outcomes:
        p_id = out.get("parent_id")
        if p_id:
            outcomes_by_parent.setdefault(p_id, []).append(out.get("title") or "")

    # Build NUMBER-FIRST hierarchy for the LLM (codes are authoritative)
    hierarchy_lines = []
    for t in sorted(topics, key=lambda x: _code_sort_key(x.get("code"))):
        t_id = t["id"]
        t_code = str(t.get("code") or "").strip()
        t_title = t.get("title") or ""
        hierarchy_lines.append(f"TOPIC {t_code}: {t_title}")

        t_outcomes = outcomes_by_parent.get(t_id, [])
        if t_outcomes:
            hierarchy_lines.append("  Topic learning outcomes:")
            for out in t_outcomes[:8]:
                hierarchy_lines.append(f"    - {out}")

        subs = sorted(
            subtopics_by_topic.get(t_id, []),
            key=lambda x: _code_sort_key(x.get("code")),
        )
        if subs:
            hierarchy_lines.append(
                f"  NUMBERED SUBTOPICS under TOPIC {t_code} (you MUST pick exactly one of these codes):"
            )
            for sub in subs:
                s_code = str(sub.get("code") or "").strip()
                s_title = sub.get("title") or ""
                hierarchy_lines.append(f"    SUBTOPIC {s_code}: {s_title}")
                s_outcomes = outcomes_by_parent.get(sub["id"], [])
                for out in s_outcomes[:5]:
                    hierarchy_lines.append(f"      - {out}")
        else:
            hierarchy_lines.append(
                f"  (TOPIC {t_code} has NO numbered subtopics like {t_code}.1 / {t_code}.2 — "
                "set selected_subtopic_code to null; topic only)"
            )

        hierarchy_lines.append("-" * 60)

    candidates_prompt = "\n".join(hierarchy_lines)

    prompt = _topic_subtopic_agent_prompt(q_text, opts_str, candidates_prompt)

    candidates = topics + subtopics

    api_key = (
        os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("KIMI_API_KEY")
        or ""
    ).strip()

    if not api_key:
        logger.warning("No MOONSHOT_API_KEY configured. Falling back to rule-based keyword match...")
        return _fallback_rule_based_classifier(
            q_text, candidates, version_id=version_id, store=store, source_key=source_key
        )

    try:
        content_text = _kimi_chat([{"role": "user", "content": prompt}], temperature=1)

        clean_text = re.sub(r"^```json\s*", "", content_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"^```\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text)
        clean_text = clean_text.strip()

        result = json.loads(clean_text)

        # Prefer numeric codes; accept legacy UUID fields as a soft fallback
        topic_code = str(
            result.get("selected_topic_code")
            or result.get("topic_code")
            or ""
        ).strip()
        subtopic_code = _normalize_optional_code(
            result.get("selected_subtopic_code")
            if result.get("selected_subtopic_code") is not None
            else result.get("subtopic_code")
        )
        # If model echoed the topic code as "subtopic", treat as topic-only
        if subtopic_code and _is_topic_code(subtopic_code) and not _is_subtopic_code(subtopic_code):
            subtopic_code = None
        reasoning = result.get("reasoning") or "Matched by Kimi Moonshot AI."

        selected_topic, topic_code, final_subtopic_code, final_node = _resolve_numbered_classification(
            topic_code=topic_code,
            subtopic_code=subtopic_code,
            topics_by_id=topics_by_id,
            topics_by_code=topics_by_code,
            subtopics_by_code=subtopics_by_code,
            subtopics_by_topic=subtopics_by_topic,
            legacy_topic_id=result.get("selected_topic_id"),
        )

        final_node_id = final_node["id"]
        method = "Kimi Moonshot AI (Numbered Codes)"

        store.create_question_classification(
            {
                "question_version_id": version_id,
                "syllabus_node_id": final_node_id,
                "is_primary": True,
                "status": "CLASSIFIED",
                "classified_by": method,
            }
        )

        history_row = {
            "question_version_id": version_id,
            "source_key": source_key,
            "syllabus_node_id": final_node_id,
            "topic_code": topic_code,
            "topic_title": selected_topic.get("title"),
            "subtopic_code": final_subtopic_code,
            "subtopic_title": final_node.get("title") if final_subtopic_code else None,
            "classified_by": method,
            "reasoning": reasoning,
            "method": method,
        }
        if hasattr(store, "append_classification_history"):
            store.append_classification_history(history_row)

        return {
            "success": True,
            "nodeId": final_node_id,
            "title": final_node["title"],
            "code": final_node.get("code"),
            "topicCode": topic_code,
            "subtopicCode": final_subtopic_code,
            "reasoning": reasoning,
            "method": method,
        }

    except Exception as exc:
        logger.exception("Kimi Moonshot classification failed. Falling back...")
        _store_rollback(store)
        return _fallback_rule_based_classifier(
            q_text, candidates, version_id=version_id, store=store, source_key=source_key
        )


def _fallback_rule_based_classifier(
    q_text: str,
    candidates: list[dict[str, Any]],
    version_id: str | None = None,
    store: FactoryStore | None = None,
    source_key: str | None = None,
) -> dict[str, Any]:
    """Smart heuristic keyword-based fallback if API is offline or key is missing."""
    q_clean = q_text.lower()
    best_node = None
    best_score = 0

    # Prefer numbered subtopics, then numbered topics
    ranked = sorted(
        candidates,
        key=lambda c: (
            0 if _is_subtopic_code(c.get("code")) else 1 if _is_topic_code(c.get("code")) else 2,
            _code_sort_key(c.get("code")),
        ),
    )

    for c in ranked:
        title_words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", c["title"]) if len(w) > 3]
        score = sum(3 for w in title_words if w in q_clean)
        if _is_subtopic_code(c.get("code")):
            score += 2
        if c.get("code") and str(c["code"]).lower() in q_clean:
            score += 10
        if score > best_score:
            best_score = score
            best_node = c

    if not best_node:
        best_node = ranked[0] if ranked else candidates[0]

    reasoning = f"Matched via smart taxonomy overlap (score={best_score})."
    method = "Rule-Based Fallback (Numbered)"

    topic_code = None
    subtopic_code = None
    topic_title = None
    subtopic_title = None
    code = str(best_node.get("code") or "").strip()
    if _is_subtopic_code(code):
        subtopic_code = code
        subtopic_title = best_node.get("title")
        topic_code = code.split(".", 1)[0]
    elif _is_topic_code(code):
        topic_code = code
        topic_title = best_node.get("title")

    if version_id and store:
        try:
            store.create_question_classification(
                {
                    "question_version_id": version_id,
                    "syllabus_node_id": best_node["id"],
                    "is_primary": True,
                    "status": "CLASSIFIED",
                    "classified_by": method,
                }
            )
            if hasattr(store, "append_classification_history"):
                store.append_classification_history(
                    {
                        "question_version_id": version_id,
                        "source_key": source_key,
                        "syllabus_node_id": best_node["id"],
                        "topic_code": topic_code,
                        "topic_title": topic_title,
                        "subtopic_code": subtopic_code,
                        "subtopic_title": subtopic_title,
                        "classified_by": method,
                        "reasoning": reasoning,
                        "method": method,
                    }
                )
        except Exception:  # noqa: BLE001
            _store_rollback(store)

    return {
        "success": True,
        "nodeId": best_node["id"],
        "title": best_node["title"],
        "code": best_node["code"],
        "topicCode": topic_code,
        "subtopicCode": subtopic_code,
        "reasoning": reasoning,
        "method": method,
    }


def classify_bloom_with_kimi(
    store: FactoryStore,
    version_id: str,
) -> dict[str, Any]:
    """Identify the Bloom's Taxonomy cognitive level of a question version with high precision."""
    if hasattr(store, "ensure_ready"):
        store.ensure_ready()  # type: ignore[union-attr]
    # Retrieve the question version
    with store._conn.cursor() as cur:
        cur.execute(
            """
            SELECT v.id::text, q.source_key, q.subject, v.content, v.options
            FROM "question_versions" v
            JOIN "bank_questions" q ON q.id = v.question_id
            WHERE v.id = %s::uuid
            """,
            (version_id,),
        )
        row = cur.fetchone()

    if not row:
        raise ValueError(f"Question version not found: {version_id}")

    v_id, source_key, subject, content, options = row
    q_text = extract_plain_text(content)

    # Reconstruct options if dictionary
    opts_str = ""
    if isinstance(options, dict):
        for key, opt in sorted(options.items()):
            if isinstance(opt, dict):
                opts_str += f"\nOption {key}: {opt.get('value', '')}"
            else:
                opts_str += f"\nOption {key}: {opt}"

    # Bloom Prompting with strict rules
    prompt = f"""You are an elite educational assessment expert specialized in Singapore GCE O-Level Chemistry, Physics, and Biology curriculum designs.
Your task is to analyze the cognitive action verbs and problem setup of the following examination question, and classify its cognitive complexity to exactly one of the six levels of Bloom's Taxonomy.

[QUESTION TEXT]
{q_text}
{opts_str}

STRICT TAXONOMY INDICATORS & CRITERIA:
1. "Remembering" (Knowledge Recall):
   - Indicators: Direct factual recall, repeating standard definitions, naming specific chemical symbols, formula names, chemical names, color of common compounds, or identifying laboratory apparatus.
   - Example verbs: recall, name, state, what is, list.

2. "Understanding" (Conceptual Translation/Explanation):
   - Indicators: Explaining why simple trends occur (e.g. why temperature increases reaction rate), translating word equations into simple chemical formulas, matching simple conceptual particle diagrams to descriptive states of matter, comparing simple physical/chemical properties.
   - Example verbs: explain, describe, compare, predict general trends.

3. "Applying" (Calculation or Reaction Balancing):
   - Indicators: Any numerical calculation (relative atomic/molecular mass, mole/stoichiometry math, Empirical Formula calculations, gas volume, or concentration calculations), applying chemical formula rules to balance reactions, assigning element oxidation states using mathematical rules, predicting specific products of a novel reaction under a known rule.
   - Example verbs: calculate, determine (via calculation), balance, solve.

4. "Analyzing" (Data Breakdown & Graphical Interpretation):
   - Indicators: Interpreting complex multi-row/multi-column experimental data tables, analyzing a grid of chromatography Rf values, reading speed/time or concentration/time gas syringe curves to find instantaneous rates, or translating dense visual systems (e.g. complex fractional distillation tower layouts).
   - Example verbs: analyze, interpret, deduce from table, inspect, compare curves.

5. "Evaluating" (Judgment & Claim Validity):
   - Indicators: Judging the validity of an experimental setup, evaluating student errors/misconceptions, deciding which subset of scientific claims (e.g., I, II, III, IV) are correct under complex multi-variable constraints.
   - Example verbs: evaluate, judge, support, justify, select correct combination of statements.

6. "Creating" (Synthesis Design):
   - Indicators: Reorganizing parts to design a novel synthesis route or multi-step experimental sequence. (This is extremely rare or non-existent in Paper 1 MCQs).

INSTRUCTIONS:
1. Select the SINGLE most accurate Bloom Level from the list: ["Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"].
2. Provide a short, highly precise, professional reasoning why the question matches this specific level, mentioning the exact action verbs and cognitive steps required.
3. You must respond with a raw JSON object (with NO markdown backticks, NO ```json wrapping, just the raw JSON) matching this exact format:
{{
  "bloom_level": "one of the six levels above",
  "reasoning": "educational reasoning matching question actions"
}}
"""

    api_key = (
        os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("KIMI_API_KEY")
        or ""
    ).strip()

    if not api_key:
        logger.warning("No MOONSHOT_API_KEY configured for Bloom's Taxonomy. Falling back to simple heuristic match...")
        return _fallback_bloom_classifier(q_text, version_id, store)

    try:
        content_text = _kimi_chat([{"role": "user", "content": prompt}], temperature=1)

        # Clean JSON wrapping if any
        clean_text = re.sub(r"^```json\s*", "", content_text, flags=re.IGNORECASE)
        clean_text = re.sub(r"^```\s*", "", clean_text)
        clean_text = re.sub(r"\s*```$", "", clean_text)
        clean_text = clean_text.strip()

        result = json.loads(clean_text)
        bloom_level = result.get("bloom_level")
        reasoning = result.get("reasoning") or "Classified by Kimi Moonshot AI."

        # Validate bloom level
        allowed_levels = {"Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"}
        if not bloom_level or bloom_level not in allowed_levels:
            # Match case-insensitively or set default
            matched = next((l for l in allowed_levels if str(bloom_level).lower() == l.lower()), None)
            if matched:
                bloom_level = matched
            else:
                bloom_level = "Understanding"

        # Update in database
        with store._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "question_versions"
                SET "bloom_level" = %s, "bloom_reasoning" = %s
                WHERE "id" = %s::uuid
                """,
                (bloom_level, reasoning, version_id),
            )
        store._conn.commit()

        return {
            "success": True,
            "bloomLevel": bloom_level,
            "reasoning": reasoning,
            "method": "Kimi Moonshot AI",
        }

    except Exception as exc:
        logger.exception("Kimi Bloom's Taxonomy classification failed. Falling back...")
        _store_rollback(store)
        return _fallback_bloom_classifier(q_text, version_id, store)


def _bloom_heuristic_verbs(q_text: str) -> tuple[str, str]:
    """Pure keyword heuristic for Bloom's level — no DB access, no persistence."""
    q_clean = q_text.lower()
    if any(verb in q_clean for verb in ["calculate", "mass", "volume", "formula", "concentration", "moles", "ratio", "mass of Z"]):
        return "Applying", "Classified as 'Applying' because the question asks the student to perform a numerical or chemical calculation."
    if any(verb in q_clean for verb in ["chromatogram", "diagram", "shows", "grid", "graph", "table", "interpret"]):
        return "Analyzing", "Classified as 'Analyzing' because the question requires students to break down and interpret visual data (diagram/chromatogram)."
    if any(verb in q_clean for verb in ["explain", "describe", "why", "compare", "meaning"]):
        return "Understanding", "Classified as 'Understanding' because the question expects the student to explain or interpret chemical concepts or trends."
    return "Remembering", "Classified as 'Remembering' because the question asks for factual recall of chemical definitions, names, or basic properties."


def _fallback_bloom_classifier(q_text: str, version_id: str, store: FactoryStore) -> dict[str, Any]:
    """Fallback Bloom's Taxonomy classifier based on action verbs in the question."""
    bloom_level, reasoning = _bloom_heuristic_verbs(q_text)

    try:
        with store._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "question_versions"
                SET "bloom_level" = %s, "bloom_reasoning" = %s
                WHERE "id" = %s::uuid
                """,
                (bloom_level, reasoning, version_id),
            )
        store._conn.commit()
    except Exception:  # noqa: BLE001
        _store_rollback(store)

    return {
        "success": True,
        "bloomLevel": bloom_level,
        "reasoning": reasoning,
        "method": "Verbs Heuristic Fallback",
    }


# ---------------------------------------------------------------------------
# Draft (pre-approval) classification — same taxonomy/prompting approach as
# the version-based classifiers above, but reads from question_drafts and
# writes to question_drafts.topic_node_id / bloom_level / bloom_reasoning
# directly (drafts have no question_classifications row — that table is
# keyed to question_version_id, which does not exist until approval).
# ---------------------------------------------------------------------------


def get_syllabus_id_for_draft(store: FactoryStore, draft_id: str) -> str | None:
    if not hasattr(store, "_conn"):
        if hasattr(store, "syllabi") and store.syllabi:
            return list(store.syllabi.keys())[0]
        return None
    with store._conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.syllabus_id::text
            FROM "question_drafts" d
            JOIN "ingestion_batches" b ON b.id = d.batch_id
            WHERE d.id = %s::uuid
            """,
            (draft_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        cur.execute('SELECT id::text FROM "syllabuses" LIMIT 1')
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    return None


def _build_topic_hierarchy(nodes: list[dict[str, Any]]):
    """Shared candidate/prompt-hierarchy builder for topic classification."""
    topics = [n for n in nodes if n.get("node_type") == "TOPIC" and _is_topic_code(n.get("code"))]
    subtopics = [n for n in nodes if n.get("node_type") == "SUBTOPIC" and _is_subtopic_code(n.get("code"))]
    outcomes = [n for n in nodes if n.get("node_type") == "OUTCOME"]

    topics_by_id = {t["id"]: t for t in topics}
    topics_by_code = {str(t.get("code")).strip(): t for t in topics}
    subtopics_by_code = {str(s.get("code")).strip(): s for s in subtopics}
    subtopics_by_topic: dict[str, list[dict[str, Any]]] = {}
    for sub in subtopics:
        p_id = sub.get("parent_id")
        if p_id:
            subtopics_by_topic.setdefault(p_id, []).append(sub)
    outcomes_by_parent: dict[str, list[str]] = {}
    for out in outcomes:
        p_id = out.get("parent_id")
        if p_id:
            outcomes_by_parent.setdefault(p_id, []).append(out.get("title") or "")

    hierarchy_lines = []
    for t in sorted(topics, key=lambda x: _code_sort_key(x.get("code"))):
        t_id = t["id"]
        t_code = str(t.get("code") or "").strip()
        t_title = t.get("title") or ""
        hierarchy_lines.append(f"TOPIC {t_code}: {t_title}")
        t_outcomes = outcomes_by_parent.get(t_id, [])
        if t_outcomes:
            hierarchy_lines.append("  Topic learning outcomes:")
            for out in t_outcomes[:8]:
                hierarchy_lines.append(f"    - {out}")
        subs = sorted(subtopics_by_topic.get(t_id, []), key=lambda x: _code_sort_key(x.get("code")))
        if subs:
            hierarchy_lines.append(
                f"  NUMBERED SUBTOPICS under TOPIC {t_code} (you MUST pick exactly one of these codes):"
            )
            for sub in subs:
                s_code = str(sub.get("code") or "").strip()
                s_title = sub.get("title") or ""
                hierarchy_lines.append(f"    SUBTOPIC {s_code}: {s_title}")
                for out in outcomes_by_parent.get(sub["id"], [])[:5]:
                    hierarchy_lines.append(f"      - {out}")
        else:
            hierarchy_lines.append(
                f"  (TOPIC {t_code} has NO numbered subtopics like {t_code}.1 / {t_code}.2 — "
                "set selected_subtopic_code to null; topic only)"
            )
        hierarchy_lines.append("-" * 60)

    return topics, subtopics, topics_by_id, topics_by_code, subtopics_by_code, subtopics_by_topic, "\n".join(hierarchy_lines)


def classify_draft_with_kimi(store: FactoryStore, draft_id: str) -> dict[str, Any]:
    """Topic/subtopic classification for a DRAFT (pre-approval) question."""
    if hasattr(store, "ensure_ready"):
        store.ensure_ready()  # type: ignore[union-attr]

    with store._conn.cursor() as cur:
        cur.execute(
            'SELECT id::text, content, options FROM "question_drafts" WHERE id = %s::uuid',
            (draft_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"Question draft not found: {draft_id}")

    _id, content, options = row
    q_text = extract_plain_text(content)
    opts_str = ""
    if isinstance(options, dict):
        for key, opt in sorted(options.items()):
            if isinstance(opt, dict):
                opts_str += f"\nOption {key}: {opt.get('value', '')}"
            else:
                opts_str += f"\nOption {key}: {opt}"

    syllabus_id = get_syllabus_id_for_draft(store, draft_id)
    if not syllabus_id:
        return {"success": False, "message": f"No syllabus found for draft {draft_id}."}

    nodes = store.list_syllabus_nodes(syllabus_id)
    topics, subtopics, topics_by_id, topics_by_code, subtopics_by_code, subtopics_by_topic, candidates_prompt = (
        _build_topic_hierarchy(nodes)
    )
    if not topics:
        return {"success": False, "message": "Syllabus has no numbered TOPIC nodes (e.g. 1, 2, 10)."}

    prompt = _topic_subtopic_agent_prompt(q_text, opts_str, candidates_prompt)

    candidates = topics + subtopics
    api_key = (os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY") or "").strip()

    result: dict[str, Any] | None = None
    if not api_key:
        result = _fallback_rule_based_classifier(q_text, candidates)
    else:
        try:
            content_text = _kimi_chat([{"role": "user", "content": prompt}], temperature=1)
            clean_text = re.sub(r"^```json\s*", "", content_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"^```\s*", "", clean_text)
            clean_text = re.sub(r"\s*```$", "", clean_text)
            parsed = json.loads(clean_text.strip())

            topic_code = str(parsed.get("selected_topic_code") or parsed.get("topic_code") or "").strip()
            subtopic_code = _normalize_optional_code(
                parsed.get("selected_subtopic_code")
                if parsed.get("selected_subtopic_code") is not None
                else parsed.get("subtopic_code")
            )
            if subtopic_code and _is_topic_code(subtopic_code) and not _is_subtopic_code(subtopic_code):
                subtopic_code = None
            reasoning = parsed.get("reasoning") or "Matched by Kimi Moonshot AI."

            selected_topic, topic_code, final_subtopic_code, final_node = _resolve_numbered_classification(
                topic_code=topic_code,
                subtopic_code=subtopic_code,
                topics_by_id=topics_by_id,
                topics_by_code=topics_by_code,
                subtopics_by_code=subtopics_by_code,
                subtopics_by_topic=subtopics_by_topic,
            )

            result = {
                "success": True,
                "nodeId": final_node["id"],
                "title": final_node["title"],
                "code": final_node.get("code"),
                "topicCode": topic_code,
                "subtopicCode": final_subtopic_code,
                "reasoning": reasoning,
                "method": "Kimi Moonshot AI (Numbered Codes)",
            }
        except Exception:
            logger.exception("Kimi draft classification failed for %s. Falling back...", draft_id)
            _store_rollback(store)
            result = _fallback_rule_based_classifier(q_text, candidates)

    if not result.get("success"):
        return result

    store.set_draft_topic(draft_id, topic_node_id=result["nodeId"])
    return result


def classify_draft_bloom_with_kimi(store: FactoryStore, draft_id: str) -> dict[str, Any]:
    """Bloom's Taxonomy classification for a DRAFT (pre-approval) question."""
    if hasattr(store, "ensure_ready"):
        store.ensure_ready()  # type: ignore[union-attr]

    with store._conn.cursor() as cur:
        cur.execute(
            'SELECT content, options FROM "question_drafts" WHERE id = %s::uuid',
            (draft_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"Question draft not found: {draft_id}")

    content, options = row
    q_text = extract_plain_text(content)
    opts_str = ""
    if isinstance(options, dict):
        for key, opt in sorted(options.items()):
            if isinstance(opt, dict):
                opts_str += f"\nOption {key}: {opt.get('value', '')}"
            else:
                opts_str += f"\nOption {key}: {opt}"

    api_key = (os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY") or "").strip()
    bloom_level: str
    reasoning: str
    method: str

    if not api_key:
        bloom_level, reasoning = _bloom_heuristic_verbs(q_text)
        method = "Verbs Heuristic Fallback"
    else:
        prompt = f"""You are an elite educational assessment expert specialized in Singapore GCE O-Level Chemistry, Physics, and Biology curriculum designs.
Your task is to analyze the cognitive action verbs and problem setup of the following examination question, and classify its cognitive complexity to exactly one of the six levels of Bloom's Taxonomy.

[QUESTION TEXT]
{q_text}
{opts_str}

INSTRUCTIONS:
1. Select the SINGLE most accurate Bloom Level from the list: ["Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"].
2. Provide a short, highly precise, professional reasoning why the question matches this specific level, mentioning the exact action verbs and cognitive steps required.
3. You must respond with a raw JSON object (with NO markdown backticks, NO ```json wrapping, just the raw JSON) matching this exact format:
{{
  "bloom_level": "one of the six levels above",
  "reasoning": "educational reasoning matching question actions"
}}
"""
        try:
            content_text = _kimi_chat([{"role": "user", "content": prompt}], temperature=1)
            clean_text = re.sub(r"^```json\s*", "", content_text, flags=re.IGNORECASE)
            clean_text = re.sub(r"^```\s*", "", clean_text)
            clean_text = re.sub(r"\s*```$", "", clean_text)
            parsed = json.loads(clean_text.strip())
            candidate = parsed.get("bloom_level")
            reasoning = parsed.get("reasoning") or "Classified by Kimi Moonshot AI."
            allowed_levels = {"Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"}
            if candidate in allowed_levels:
                bloom_level = candidate
            else:
                matched = next((lvl for lvl in allowed_levels if str(candidate).lower() == lvl.lower()), None)
                bloom_level = matched or "Understanding"
            method = "Kimi Moonshot AI"
        except Exception:
            logger.exception("Kimi draft Bloom classification failed for %s. Falling back...", draft_id)
            _store_rollback(store)
            bloom_level, reasoning = _bloom_heuristic_verbs(q_text)
            method = "Verbs Heuristic Fallback"

    store.set_draft_bloom(draft_id, bloom_level=bloom_level, bloom_reasoning=reasoning)
    return {"success": True, "bloomLevel": bloom_level, "reasoning": reasoning, "method": method}


def classify_drafts_for_run(store: FactoryStore, run_id: str) -> dict[str, int]:
    """Auto-classify (topic + Bloom) every draft persisted for one ingestion run.

    Best-effort: one draft's failure does not stop the others. Called right
    after factory persistence succeeds so the review queue can show
    classification immediately, with no manual trigger needed.
    """
    drafts = store.list_drafts_for_run(run_id)
    classified = 0
    skipped = 0
    failed = 0
    for draft in drafts:
        already_topic = bool(draft.topic_node_id)
        already_bloom = bool(draft.bloom_level)
        if already_topic and already_bloom:
            skipped += 1
            continue
        try:
            if not already_topic:
                classify_draft_with_kimi(store, draft.id)
            if not already_bloom:
                classify_draft_bloom_with_kimi(store, draft.id)
            classified += 1
        except Exception:
            logger.exception("Draft classification failed for %s", draft.id)
            failed += 1
    return {"classified": classified, "skipped": skipped, "failed": failed}

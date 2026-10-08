"""Approve / reject / edit QuestionDraft workflow."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from questbank.factory.store import FactoryStore
from questbank.types.ingest import normalize_question_ref


def make_source_key(
    *,
    subject: str,
    exam_year: int | None,
    paper_code: str | None,
    question_ref: str,
    school: str | None = None,
    batch_id: str | None = None,
) -> str:
    """Stable logical identity for a production Question.

    Includes a short batch id so different imports with the same Q-number
    (e.g. two 2026 Paper 1 tests) each get their own bank row.

    Example: 2026-CHEM-P1-Q29-B540abd61
    """
    year = exam_year or 0
    raw_subj = re.sub(r"[^A-Za-z0-9]", "", (subject or "CHEM").upper())
    if raw_subj.startswith("CHEMISTRY"):
        subj = "CHEM"
    else:
        subj = (raw_subj[:8] or "CHEM")
    raw_paper = re.sub(r"[^A-Za-z0-9]", "", (paper_code or "P1").upper())
    if raw_paper.startswith("PAPER") and len(raw_paper) > 5:
        paper = "P" + raw_paper[5:]
    else:
        paper = raw_paper or "P1"
    ref = normalize_question_ref(question_ref)
    if ref.upper().startswith("Q"):
        q = ref.upper()
    else:
        q = f"Q{ref}"
    school_slug = ""
    if school:
        school_slug = re.sub(r"[^A-Za-z0-9]", "", school.upper())[:12]
        if school_slug:
            school_slug = f"{school_slug}-"
    base = f"{school_slug}{year}-{subj}-{paper}-{q}"
    if batch_id:
        # First 8 hex chars of the batch UUID — enough to disambiguate imports.
        compact = re.sub(r"[^A-Za-z0-9]", "", str(batch_id))[:8].lower()
        if compact:
            return f"{base}-B{compact}"
    return base


def edit_draft(
    store: FactoryStore,
    *,
    draft_id: str,
    content: Any,
    options: Any,
    comment: str | None = None,
    reviewer_id: str | None = None,
    answer_value: str | None = None,
    explanation: str | None = None,
    parts_answer: Any | None = None,
    question_ref: str | None = None,
) -> dict[str, Any]:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise KeyError(f"Draft not found: {draft_id}")

    status = draft.status
    if status == "REJECTED":
        status = "NEEDS_REVIEW"
    # Keep APPROVED as-is if already approved; content edits still allowed so
    # reviewers can fix images/text without a dead-end lock.

    from questbank.factory.mapping import ensure_list_blocks
    from questbank.factory.store import AnswerRow
    from uuid import uuid4

    content = ensure_list_blocks(content)

    new_ref = draft.question_ref
    if question_ref is not None and str(question_ref).strip() and str(question_ref).strip() != draft.question_ref:
        new_ref = str(question_ref).strip()
        store.rename_draft_question_ref(draft_id, new_ref)

    store.update_draft_json(
        draft_id,
        content=content,
        options=options,
        status=status,
        requires_review=draft.requires_review,
        validation_issues=draft.validation_issues,
    )

    if answer_value is not None:
        cleaned = str(answer_value).strip().upper()
        if cleaned not in {"A", "B", "C", "D"}:
            raise ValueError("answer_value must be A, B, C, or D")
        existing = store.get_answer(draft.run_id, new_ref)
        expl = explanation if explanation is not None else (existing.explanation if existing else None)
        store.upsert_answer(
            AnswerRow(
                id=existing.id if existing else str(uuid4()),
                run_id=draft.run_id,
                batch_id=draft.batch_id,
                question_ref=new_ref,
                answer_value=cleaned,
                explanation=(expl.strip() if isinstance(expl, str) and expl.strip() else None),
                explanation_source="MARK_SCHEME" if expl else None,
                source_page=existing.source_page if existing else None,
            )
        )
    elif parts_answer is not None:
        # Structured question: the mark scheme is a list of per-part grading
        # rows (label/marks_max/accepted_answers/notes/ecf/visuals), not a
        # single letter. Keep it aligned with question_ref so grading can
        # find it the same way the MCQ answer_value path does.
        existing = store.get_answer(draft.run_id, new_ref)
        store.upsert_answer(
            AnswerRow(
                id=existing.id if existing else str(uuid4()),
                run_id=draft.run_id,
                batch_id=draft.batch_id,
                question_ref=new_ref,
                answer_value=existing.answer_value if existing else None,
                parts_answer=parts_answer,
                explanation=existing.explanation if existing else None,
                explanation_source=existing.explanation_source if existing else None,
                source_page=existing.source_page if existing else None,
            )
        )

    event = store.create_review_event(
        draft_id=draft_id,
        action="EDIT",
        comment=comment,
        reviewer_id=reviewer_id,
    )
    return {"draftId": draft_id, "status": status, "event": event}


def _plain_question_ref(value: str | int) -> str:
    text = str(value).strip()
    if text.upper().startswith("Q"):
        text = text[1:].strip()
    text = text.lstrip("0") or "0"
    if text.isdigit():
        return str(int(text))
    if not text:
        raise ValueError("question_ref is required")
    return text


def suggest_question_ref(store: FactoryStore, *, batch_id: str) -> str:
    """First missing numeric ref in the batch (1,2,3…), else max+1."""
    refs: set[int] = set()
    for draft in store.list_drafts_for_batch(batch_id):
        plain = _plain_question_ref(draft.question_ref)
        if plain.isdigit():
            refs.add(int(plain))
    if not refs:
        return "1"
    for i in range(1, max(refs) + 2):
        if i not in refs:
            return str(i)
    return str(max(refs) + 1)


def create_manual_draft(
    store: FactoryStore,
    *,
    batch_id: str,
    question_ref: str | None = None,
    stem: str,
    options: dict[str, str] | None = None,
    answer_value: str | None = None,
    explanation: str | None = None,
    question_type: str = "MCQ",
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    """Insert a hand-authored draft (+ answer) into the batch's latest run.

    ``question_type="STRUCTURED"`` creates a minimal one-part free-response
    shell (stem + a single empty part "a") instead of an A/B/C/D MCQ, so it
    opens directly in StructuredPartsEditor / StructuredAnswerEditor for the
    reviewer to build out — there was previously no way to manually add a
    structured question at all, only MCQs.
    """
    from uuid import uuid4

    from questbank.factory.mapping import ensure_list_blocks
    from questbank.factory.store import AnswerRow, DraftRow

    detail = store.get_batch_detail(batch_id)
    if not detail:
        raise ValueError("Batch not found")
    run_id = detail.get("latestRunId")
    if not run_id:
        raise ValueError("Batch has no ingestion run to attach a draft to")

    ref = _plain_question_ref(question_ref or suggest_question_ref(store, batch_id=batch_id))
    existing = [
        d
        for d in store.list_drafts_for_batch(batch_id)
        if _plain_question_ref(d.question_ref) == ref and d.run_id == run_id
    ]
    if existing:
        raise ValueError(f"Question {ref} already exists in this batch")

    is_structured = str(question_type or "MCQ").strip().upper() == "STRUCTURED"
    stem_text = (stem or "").strip() or f"(Manual question {ref} — edit stem)"

    if is_structured:
        content = ensure_list_blocks([{"type": "text", "value": stem_text}])
        empty_part = {
            "label": "a",
            "prompt": "",
            "marks": None,
            "responseKind": "text",
            "visuals": [],
            "children": [],
        }
        options_payload = {
            "mode": "structured",
            "stem": stem_text,
            "parts": [empty_part],
            "marksTotal": None,
            "navigationOrder": [
                {
                    "label": "a",
                    "prompt": "",
                    "marks": None,
                    "responseKind": "text",
                    "visuals": [],
                    "hasChildren": False,
                    "sectionOnly": False,
                }
            ],
        }
        draft_question_type = "STRUCTURED"
        answer_row_kwargs: dict[str, Any] = {
            "answer_value": None,
            "parts_answer": [
                {
                    "label": "a",
                    "marks_max": None,
                    "accepted_answers": [],
                    "notes": (explanation.strip() if explanation and explanation.strip() else None),
                    "ecf": False,
                    "visuals": [],
                }
            ],
            "explanation": None,
            "explanation_source": None,
        }
    else:
        cleaned_answer = str(answer_value or "").strip().upper()
        if cleaned_answer not in {"A", "B", "C", "D"}:
            raise ValueError("answer_value must be A, B, C, or D")

        opt_map = options or {}
        items = []
        for label in ("A", "B", "C", "D"):
            items.append(
                {
                    "label": label,
                    "content": str(opt_map.get(label) or opt_map.get(label.lower()) or "").strip(),
                }
            )
        content = ensure_list_blocks([{"type": "text", "value": stem_text}])
        options_payload = {"mode": "text", "items": items}
        draft_question_type = "MCQ"
        answer_row_kwargs = {
            "answer_value": cleaned_answer,
            "explanation": (explanation.strip() if explanation and explanation.strip() else None),
            "explanation_source": "MARK_SCHEME" if explanation and explanation.strip() else None,
        }

    draft = store.upsert_draft(
        DraftRow(
            id=str(uuid4()),
            run_id=run_id,
            batch_id=batch_id,
            question_ref=ref,
            question_type=draft_question_type,
            content=content,
            options=options_payload,
            source_page=None,
            source_regions=None,
            status="NEEDS_REVIEW",
            requires_review=True,
            validation_issues=[{"code": "MANUAL", "message": "Manually added question"}],
        )
    )
    store.upsert_answer(
        AnswerRow(
            id=str(uuid4()),
            run_id=run_id,
            batch_id=batch_id,
            question_ref=ref,
            source_page=None,
            **answer_row_kwargs,
        )
    )
    event = store.create_review_event(
        draft_id=draft.id,
        action="EDIT",
        comment="Manually added question",
        reviewer_id=reviewer_id,
    )
    return {
        "draftId": draft.id,
        "questionRef": ref,
        "status": draft.status,
        "event": event,
    }


def replace_draft_asset(
    store: FactoryStore,
    *,
    draft_id: str,
    asset_id: str,
    data: bytes,
    content_type: str = "image/png",
    filename: str | None = None,
    uploader: Any | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    """Replace a draft visual with new image bytes (local or Supabase Storage)."""
    import hashlib
    from io import BytesIO
    from uuid import uuid4

    from questbank.factory.local_storage import create_asset_uploader

    draft = store.get_draft(draft_id)
    if draft is None:
        raise KeyError(f"Draft not found: {draft_id}")
    # Allow replace on any draft status — reviewers must be able to fix crops
    # even after accidental approval, and correction workflows reuse this path.

    asset = store.get_asset(asset_id)
    if asset is None:
        raise KeyError(f"Asset not found: {asset_id}")
    # Accept assets owned by this draft, unlinked correction uploads, or
    # assets still referenced by this draft's content/options JSON.
    owned = asset.draft_id is None or asset.draft_id == draft_id
    if not owned:
        referenced = _draft_references_asset(draft, asset_id)
        if not referenced:
            raise KeyError(f"Asset not found for draft: {asset_id}")

    if not data:
        raise ValueError("Empty image upload")
    if content_type and not content_type.startswith("image/"):
        raise ValueError("Only image uploads are supported")

    width = height = None
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            width, height = img.size
    except Exception:  # noqa: BLE001
        pass

    digest = hashlib.sha256(data).hexdigest()
    ext = "png"
    if "jpeg" in (content_type or "") or "jpg" in (content_type or ""):
        ext = "jpg"
    elif "webp" in (content_type or ""):
        ext = "webp"
    safe_name = filename or f"replaced-{uuid4().hex[:8]}.{ext}"
    storage_path = f"question-assets/{draft.batch_id}/{draft.run_id}/{safe_name}"

    active = uploader or create_asset_uploader()
    active.upload(storage_path=storage_path, data=data, content_type=content_type or "image/png")

    updated = store.update_asset(
        asset_id,
        storage_path=storage_path,
        mime_type=content_type or "image/png",
        sha256=digest,
        width=width,
        height=height,
    )
    store.create_review_event(
        draft_id=draft_id,
        action="EDIT",
        comment=f"Replaced asset {asset_id}",
        reviewer_id=reviewer_id,
    )
    return {
        "draftId": draft_id,
        "assetId": updated.id,
        "storagePath": updated.storage_path,
        "width": updated.width,
        "height": updated.height,
        "url": f"/api/factory/assets/{updated.id}?v={digest[:12]}",
    }


def _draft_references_asset(draft: Any, asset_id: str) -> bool:
    """True if content/options JSON still points at this asset id."""
    content = getattr(draft, "content", None) or []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("asset_id") == asset_id:
                return True
    options = getattr(draft, "options", None) or {}
    if isinstance(options, dict) and options.get("asset_id") == asset_id:
        return True
    return False


def reject_draft(
    store: FactoryStore,
    *,
    draft_id: str,
    reason: str | None = None,
    reviewer_id: str | None = None,
) -> dict[str, Any]:
    draft = store.get_draft(draft_id)
    if draft is None:
        raise KeyError(f"Draft not found: {draft_id}")

    store.update_draft_json(
        draft_id,
        content=draft.content,
        options=draft.options,
        status="REJECTED",
        requires_review=False,
        validation_issues=draft.validation_issues,
    )
    event = store.create_review_event(
        draft_id=draft_id,
        action="REJECT",
        comment=reason,
        reviewer_id=reviewer_id,
    )
    return {"draftId": draft_id, "status": "REJECTED", "event": event}


def approve_draft(
    store: FactoryStore,
    *,
    draft_id: str,
    reviewer_id: str | None = None,
    school: str | None = None,
) -> dict[str, Any]:
    """Promote QuestionDraft → Question → QuestionVersion → QuestionAnswer.

    Never mutates an existing QuestionVersion; creates a new version instead.
    """
    draft = store.get_draft(draft_id)
    if draft is None:
        raise KeyError(f"Draft not found: {draft_id}")
    if draft.status == "REJECTED":
        raise ValueError("Rejected drafts cannot be approved without restoring them first")
    if draft.status == "APPROVED":
        raise ValueError("Draft is already approved")

    batch = store.get_batch(draft.batch_id)
    if batch is None:
        raise ValueError(f"Batch not found for draft: {draft.batch_id}")

    answer = store.get_answer(draft.run_id, draft.question_ref)
    if answer is None:
        raise ValueError("Cannot approve a draft without a matched AnswerRecord")

    source_key = make_source_key(
        subject=batch.get("subject") or "Chemistry",
        exam_year=batch.get("exam_year"),
        paper_code=batch.get("paper_code"),
        question_ref=draft.question_ref,
        school=school,
        batch_id=draft.batch_id,
    )

    question = store.upsert_bank_question(
        source_key=source_key,
        subject=batch.get("subject") or "Chemistry",
    )
    store.retire_active_versions(question["id"])
    version_num = store.next_version_number(question["id"])
    now = datetime.now(timezone.utc)

    from questbank.factory.mapping import ensure_list_blocks

    content = ensure_list_blocks(draft.content)

    version = store.create_question_version(
        {
            "question_id": question["id"],
            "version": version_num,
            "content": content,
            "options": draft.options,
            "source_draft_id": draft.id,
            "status": "APPROVED",
            "approved_by": reviewer_id,
            "approved_at": now,
            "published_at": None,
            "bloom_level": draft.bloom_level,
            "bloom_reasoning": draft.bloom_reasoning,
        }
    )
    q_answer = store.create_question_answer(
        {
            "question_version_id": version["id"],
            "value": answer.answer_value,
            "parts_answer": getattr(answer, "parts_answer", None),
            "explanation": answer.explanation,
            "explanation_source": answer.explanation_source,
        }
    )

    # Retain parse-time / draft classification in the bank + classification queue.
    if draft.topic_node_id:
        store.create_question_classification(
            {
                "question_version_id": version["id"],
                "syllabus_node_id": draft.topic_node_id,
                "is_primary": True,
                "status": "CLASSIFIED",
                "classified_by": "Parse / draft classification",
            }
        )
        topic_code = draft.topic_code
        topic_title = draft.topic_title
        subtopic_code = None
        subtopic_title = None
        # If the draft node is a subtopic, lift parent as topic for history.
        if topic_code and "." in str(topic_code):
            subtopic_code, subtopic_title = topic_code, topic_title
            parent_code = str(topic_code).split(".", 1)[0]
            topic_code = parent_code
            topic_title = None
            if hasattr(store, "_conn"):
                with store._conn.cursor() as cur:  # type: ignore[attr-defined]
                    cur.execute(
                        """
                        SELECT code, title FROM "syllabus_nodes"
                        WHERE id = (
                          SELECT parent_id FROM "syllabus_nodes" WHERE id = %s::uuid
                        )
                        """,
                        (draft.topic_node_id,),
                    )
                    prow = cur.fetchone()
                    if prow:
                        topic_code, topic_title = prow[0], prow[1]
        if hasattr(store, "append_classification_history"):
            store.append_classification_history(
                {
                    "question_version_id": version["id"],
                    "source_key": source_key,
                    "syllabus_node_id": draft.topic_node_id,
                    "topic_code": topic_code,
                    "topic_title": topic_title,
                    "subtopic_code": subtopic_code,
                    "subtopic_title": subtopic_title,
                    "bloom_level": draft.bloom_level,
                    "classified_by": "Parse / draft classification",
                    "reasoning": draft.bloom_reasoning or "Retained from ingestion parse classification.",
                    "method": "parse",
                }
            )

    store.update_draft_json(
        draft_id,
        content=content,
        options=draft.options,
        status="APPROVED",
        requires_review=False,
        validation_issues=draft.validation_issues,
    )
    event = store.create_review_event(
        draft_id=draft_id,
        action="APPROVE",
        comment=None,
        reviewer_id=reviewer_id,
    )

    # Batch completion: if no unresolved drafts remain on latest run.
    unresolved = [
        d
        for d in store.list_drafts_for_batch(draft.batch_id)
        if d.status not in {"APPROVED", "REJECTED"}
    ]
    if not unresolved:
        store.update_batch_status(draft.batch_id, "COMPLETED")

    return {
        "draftId": draft_id,
        "questionId": question["id"],
        "sourceKey": source_key,
        "versionId": version["id"],
        "version": version_num,
        "answerId": q_answer["id"],
        "event": event,
        "batchCompleted": len(unresolved) == 0,
    }


def approve_all_in_batch(
    store: FactoryStore,
    *,
    batch_id: str,
    reviewer_id: str | None = None,
    school: str | None = None,
    include_flagged: bool = False,
) -> dict[str, Any]:
    """Approve every still-unresolved draft in a batch in one shot.

    Drafts marked requires_review (e.g. missing answer key, needs a manual
    diagram) are skipped by default rather than silently waved through -
    pass include_flagged=True to approve those too. Each draft is approved
    independently so one bad row (e.g. no matched AnswerRecord) doesn't
    block the rest of the batch; failures are collected and returned rather
    than raised.
    """
    drafts = sorted(
        store.list_drafts_for_batch(batch_id),
        key=lambda d: _ref_sort(d.question_ref),
    )
    candidates = [d for d in drafts if d.status not in {"APPROVED", "REJECTED"}]

    approved: list[str] = []
    skipped_flagged: list[str] = []
    failed: list[dict[str, str]] = []

    for draft in candidates:
        if draft.requires_review and not include_flagged:
            skipped_flagged.append(draft.id)
            continue
        try:
            approve_draft(store, draft_id=draft.id, reviewer_id=reviewer_id, school=school)
            approved.append(draft.id)
        except (KeyError, ValueError) as exc:
            failed.append({"draftId": draft.id, "questionRef": draft.question_ref, "error": str(exc)})

    return {
        "batchId": batch_id,
        "approvedCount": len(approved),
        "approvedIds": approved,
        "skippedFlaggedCount": len(skipped_flagged),
        "skippedFlaggedIds": skipped_flagged,
        "failedCount": len(failed),
        "failed": failed,
    }


def next_unresolved_draft_id(
    store: FactoryStore,
    *,
    batch_id: str,
    after_draft_id: str | None = None,
) -> str | None:
    drafts = sorted(
        store.list_drafts_for_batch(batch_id),
        key=lambda d: _ref_sort(d.question_ref),
    )
    unresolved = [d for d in drafts if d.status not in {"APPROVED", "REJECTED"}]
    if not unresolved:
        return None
    if after_draft_id is None:
        return unresolved[0].id
    seen = False
    for d in unresolved:
        if seen:
            return d.id
        if d.id == after_draft_id:
            seen = True
    # wrap / fall through to first remaining that isn't the current
    for d in unresolved:
        if d.id != after_draft_id:
            return d.id
    return None


def _ref_sort(ref: str) -> tuple:
    text = str(ref).strip()
    if text.upper().startswith("Q"):
        text = text[1:]
    if text.isdigit():
        return (0, int(text))
    return (1, text)

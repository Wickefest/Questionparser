"""Persistence backends for Question Factory tables (Prisma schema)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> Any:
    """Ensure JSON-serializable structures for JSONB columns."""
    return json.loads(json.dumps(value, default=str))


def _draft_row_with_topic_hierarchy(
    *,
    node_code: str | None,
    node_title: str | None,
    node_type: str | None,
    parent_code: str | None,
    parent_title: str | None,
    **fields: Any,
) -> "DraftRow":
    """Resolve a draft's classification node into topic + subtopic.

    ``topic_node_id`` points at whichever node the classifier picked — a bare
    TOPIC when the topic has no subtopics, otherwise a SUBTOPIC. Reviewers
    need to see the parent TOPIC too (e.g. "12 Experimental techniques" above
    "12.2 Acid-base titrations"), so a SUBTOPIC node's parent is looked up
    via its own JOIN rather than re-deriving it from the code string.
    """
    if node_type == "SUBTOPIC":
        topic_code, topic_title = parent_code, parent_title
        subtopic_code, subtopic_title = node_code, node_title
    else:
        topic_code, topic_title = node_code, node_title
        subtopic_code, subtopic_title = None, None
    return DraftRow(
        **fields,
        topic_code=topic_code,
        topic_title=topic_title,
        subtopic_code=subtopic_code,
        subtopic_title=subtopic_title,
    )


@dataclass
class DraftRow:
    id: str
    run_id: str
    batch_id: str
    question_ref: str
    question_type: str
    content: Any
    options: Any
    source_page: int | None
    source_regions: Any
    status: str
    requires_review: bool
    validation_issues: Any
    topic_node_id: str | None = None
    topic_code: str | None = None
    topic_title: str | None = None
    subtopic_code: str | None = None
    subtopic_title: str | None = None
    bloom_level: str | None = None
    bloom_reasoning: str | None = None


@dataclass
class AnswerRow:
    id: str
    run_id: str
    batch_id: str
    question_ref: str
    answer_value: str | None
    explanation: str | None
    explanation_source: str | None
    source_page: int | None
    parts_answer: Any | None = None


@dataclass
class AssetRow:
    id: str
    draft_id: str | None
    source_document_id: str | None
    role: str
    storage_path: str
    mime_type: str
    source_page: int | None
    bbox: Any
    width: int | None
    height: int | None
    sha256: str | None


_PARSING_RUN_STATUSES = frozenset({"PENDING", "RUNNING", "PROCESSING", "QUEUED"})


def _sort_runs_newest_first(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(runs, key=lambda r: str(r.get("started_at") or ""), reverse=True)


def pick_display_run(
    runs: list[dict[str, Any]],
    *,
    run_has_drafts,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pick the live latest run, plus the run History should show.

    While a run is still parsing, both are that live run. After it finishes,
    prefer the newest COMPLETED run that still has drafts so a later failed
    reprocess does not hide approved questions from a previous success.
    """
    ordered = _sort_runs_newest_first(runs)
    if not ordered:
        return None, None
    latest = ordered[0]
    latest_status = str(latest.get("status") or "").upper()
    if latest_status in _PARSING_RUN_STATUSES:
        return latest, latest
    succeeded = [
        r
        for r in ordered
        if str(r.get("status") or "").upper() == "COMPLETED" and run_has_drafts(str(r.get("id") or ""))
    ]
    if succeeded:
        return latest, succeeded[0]
    with_drafts = [r for r in ordered if run_has_drafts(str(r.get("id") or ""))]
    return latest, (with_drafts[0] if with_drafts else latest)


class FactoryStore(Protocol):
    def get_batch(self, batch_id: str) -> dict[str, Any] | None: ...

    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def get_source_document(self, document_id: str) -> dict[str, Any] | None: ...

    def create_batch(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def create_source_document(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def create_run(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def update_batch_status(self, batch_id: str, status: str) -> None: ...

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        completed_at: datetime | None = None,
    ) -> None: ...

    def delete_drafts_for_run(self, run_id: str) -> None: ...

    def delete_answers_for_run(self, run_id: str) -> None: ...

    def delete_batch(self, batch_id: str) -> None: ...

    def upsert_draft(self, row: DraftRow) -> DraftRow: ...

    def update_draft_json(
        self,
        draft_id: str,
        *,
        content: Any,
        options: Any,
        status: str,
        requires_review: bool,
        validation_issues: Any,
    ) -> None: ...

    def rename_draft_question_ref(self, draft_id: str, new_ref: str) -> None: ...

    def upsert_answer(self, row: AnswerRow) -> AnswerRow: ...

    def create_asset(self, row: AssetRow) -> AssetRow: ...

    def update_asset(
        self,
        asset_id: str,
        *,
        storage_path: str,
        mime_type: str,
        sha256: str | None,
        width: int | None,
        height: int | None,
    ) -> AssetRow: ...

    def list_drafts_for_run(self, run_id: str) -> list[DraftRow]: ...

    def list_answers_for_run(self, run_id: str) -> list[AnswerRow]: ...

    def list_assets_for_draft(self, draft_id: str) -> list[AssetRow]: ...

    def list_batches(self, *, limit: int | None = 50) -> list[dict[str, Any]]: ...

    def get_batch_detail(self, batch_id: str) -> dict[str, Any] | None: ...

    def find_documents_by_sha256(self, sha256: str) -> list[dict[str, Any]]: ...

    def list_syllabi(self) -> list[dict[str, Any]]: ...

    def get_syllabus(self, syllabus_id: str) -> dict[str, Any] | None: ...

    def create_syllabus(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def list_syllabus_nodes(self, syllabus_id: str) -> list[dict[str, Any]]: ...

    def create_syllabus_node(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def delete_syllabus_nodes(self, syllabus_id: str) -> None: ...

    def touch_syllabus(self, syllabus_id: str) -> None: ...

    def get_draft(self, draft_id: str) -> DraftRow | None: ...

    def list_drafts_for_batch(self, batch_id: str) -> list[DraftRow]: ...

    def get_answer(self, run_id: str, question_ref: str) -> AnswerRow | None: ...

    def create_review_event(
        self,
        *,
        draft_id: str,
        action: str,
        comment: str | None = None,
        reviewer_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get_asset(self, asset_id: str) -> AssetRow | None: ...

    def list_assets_by_ids(self, asset_ids: list[str]) -> list[AssetRow]: ...

    def list_runs_for_batch(self, batch_id: str) -> list[dict[str, Any]]: ...

    def list_documents_for_batch(self, batch_id: str) -> list[dict[str, Any]]: ...

    def upsert_bank_question(self, *, source_key: str, subject: str) -> dict[str, Any]: ...

    def next_version_number(self, question_id: str) -> int: ...

    def retire_active_versions(self, question_id: str) -> None: ...

    def create_question_version(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def create_question_answer(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def create_question_classification(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def append_classification_history(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def list_classification_history(self, *, limit: int = 50) -> list[dict[str, Any]]: ...

    def list_bank_questions(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def get_bank_question(self, question_id: str) -> dict[str, Any] | None: ...

    def dashboard_metrics(self) -> dict[str, int]: ...

    def list_unclassified_versions(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def random_mcq_drafts(self, *, count: int) -> list[dict[str, Any]]: ...

    def random_structured_drafts(self, *, count: int) -> list[dict[str, Any]]: ...

    def random_approved_structured_drafts(self, *, count: int) -> list[dict[str, Any]]: ...

    def random_bank_mcqs(self, *, count: int) -> list[dict[str, Any]]: ...

    def get_bank_version_answer(self, version_id: str) -> dict[str, Any] | None: ...

    def set_draft_topic(self, draft_id: str, *, topic_node_id: str) -> None: ...

    def set_draft_bloom(self, draft_id: str, *, bloom_level: str, bloom_reasoning: str) -> None: ...


@dataclass
class InMemoryFactoryStore:
    """In-process store for unit tests (no Postgres)."""

    batches: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    drafts: dict[str, DraftRow] = field(default_factory=dict)
    answers: dict[str, AnswerRow] = field(default_factory=dict)
    assets: dict[str, AssetRow] = field(default_factory=dict)
    review_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    bank_questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    bank_by_source_key: dict[str, str] = field(default_factory=dict)
    versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    version_answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    syllabi: dict[str, dict[str, Any]] = field(default_factory=dict)
    syllabus_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    classifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    classification_history: list[dict[str, Any]] = field(default_factory=list)
    _draft_by_run_ref: dict[tuple[str, str], str] = field(default_factory=dict)
    _answer_by_run_ref: dict[tuple[str, str], str] = field(default_factory=dict)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self.batches.get(batch_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def get_source_document(self, document_id: str) -> dict[str, Any] | None:
        return self.documents.get(document_id)

    def create_batch(self, row: dict[str, Any]) -> dict[str, Any]:
        batch_id = row.get("id") or str(uuid4())
        stored = {**row, "id": batch_id}
        stored.setdefault("created_at", _utcnow())
        self.batches[batch_id] = stored
        return stored

    def create_source_document(self, row: dict[str, Any]) -> dict[str, Any]:
        doc_id = row.get("id") or str(uuid4())
        stored = {**row, "id": doc_id}
        stored.setdefault("created_at", _utcnow())
        self.documents[doc_id] = stored
        return stored

    def create_run(self, row: dict[str, Any]) -> dict[str, Any]:
        run_id = row.get("id") or str(uuid4())
        stored = {**row, "id": run_id}
        stored.setdefault("started_at", _utcnow())
        self.runs[run_id] = stored
        return stored

    def update_batch_status(self, batch_id: str, status: str) -> None:
        if batch_id not in self.batches:
            raise KeyError(f"Unknown batch: {batch_id}")
        self.batches[batch_id]["status"] = status

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        completed_at: datetime | None = None,
    ) -> None:
        if run_id not in self.runs:
            raise KeyError(f"Unknown run: {run_id}")
        self.runs[run_id]["status"] = status
        if completed_at is not None:
            self.runs[run_id]["completed_at"] = completed_at

    def delete_drafts_for_run(self, run_id: str) -> None:
        to_delete = [d.id for d in self.drafts.values() if d.run_id == run_id]
        for draft_id in to_delete:
            draft = self.drafts.pop(draft_id)
            self._draft_by_run_ref.pop((draft.run_id, draft.question_ref), None)
            asset_ids = [a.id for a in self.assets.values() if a.draft_id == draft_id]
            for aid in asset_ids:
                self.assets.pop(aid, None)

    def delete_answers_for_run(self, run_id: str) -> None:
        to_delete = [a.id for a in self.answers.values() if a.run_id == run_id]
        for answer_id in to_delete:
            ans = self.answers.pop(answer_id)
            self._answer_by_run_ref.pop((ans.run_id, ans.question_ref), None)

    def delete_batch(self, batch_id: str) -> None:
        # Physical disk cleanup
        import shutil
        from pathlib import Path
        from questbank.factory.local_storage import factory_storage_root

        storage_root = factory_storage_root()
        shutil.rmtree(storage_root / "import-keep" / batch_id, ignore_errors=True)
        shutil.rmtree(storage_root / "exam-sources" / batch_id, ignore_errors=True)

        prefix = f"factory-{batch_id[:8]}"
        output_root = Path("output")
        if output_root.is_dir():
            for child in output_root.iterdir():
                if child.is_dir() and child.name.startswith(prefix):
                    shutil.rmtree(child, ignore_errors=True)

        # Delete documents
        doc_ids = [d["id"] for d in self.documents.values() if d.get("batch_id") == batch_id]
        for d_id in doc_ids:
            self.documents.pop(d_id, None)

        # Delete runs
        run_ids = [r["id"] for r in self.runs.values() if r.get("batch_id") == batch_id]
        for r_id in run_ids:
            self.runs.pop(r_id, None)
            self.delete_drafts_for_run(r_id)
            self.delete_answers_for_run(r_id)

        # Delete batch itself
        self.batches.pop(batch_id, None)

    def upsert_draft(self, row: DraftRow) -> DraftRow:
        key = (row.run_id, row.question_ref)
        existing_id = self._draft_by_run_ref.get(key)
        if existing_id:
            row.id = existing_id
        self.drafts[row.id] = row
        self._draft_by_run_ref[key] = row.id
        return row

    def update_draft_json(
        self,
        draft_id: str,
        *,
        content: Any,
        options: Any,
        status: str,
        requires_review: bool,
        validation_issues: Any,
    ) -> None:
        draft = self.drafts[draft_id]
        draft.content = content
        draft.options = options
        draft.status = status
        draft.requires_review = requires_review
        draft.validation_issues = validation_issues

    def rename_draft_question_ref(self, draft_id: str, new_ref: str) -> None:
        draft = self.drafts.get(draft_id)
        if draft is None:
            return
        old_ref = draft.question_ref
        old_key = (draft.run_id, old_ref)
        draft.question_ref = new_ref
        self._draft_by_run_ref.pop(old_key, None)
        self._draft_by_run_ref[(draft.run_id, new_ref)] = draft_id

        answer_id = self._answer_by_run_ref.pop(old_key, None)
        if answer_id and answer_id in self.answers:
            self.answers[answer_id].question_ref = new_ref
            self._answer_by_run_ref[(draft.run_id, new_ref)] = answer_id

    def upsert_answer(self, row: AnswerRow) -> AnswerRow:
        key = (row.run_id, row.question_ref)
        existing_id = self._answer_by_run_ref.get(key)
        if existing_id:
            row.id = existing_id
        self.answers[row.id] = row
        self._answer_by_run_ref[key] = row.id
        return row

    def create_asset(self, row: AssetRow) -> AssetRow:
        self.assets[row.id] = row
        return row

    def update_asset(
        self,
        asset_id: str,
        *,
        storage_path: str,
        mime_type: str,
        sha256: str | None,
        width: int | None,
        height: int | None,
    ) -> AssetRow:
        asset = self.assets.get(asset_id)
        if asset is None:
            raise KeyError(f"Asset not found: {asset_id}")
        asset.storage_path = storage_path
        asset.mime_type = mime_type
        asset.sha256 = sha256
        asset.width = width
        asset.height = height
        return asset

    def list_drafts_for_run(self, run_id: str) -> list[DraftRow]:
        return [d for d in self.drafts.values() if d.run_id == run_id]

    def list_answers_for_run(self, run_id: str) -> list[AnswerRow]:
        return [a for a in self.answers.values() if a.run_id == run_id]

    def list_assets_for_draft(self, draft_id: str) -> list[AssetRow]:
        return [a for a in self.assets.values() if a.draft_id == draft_id]

    def list_batches(self, *, limit: int | None = 50) -> list[dict[str, Any]]:
        rows = sorted(
            self.batches.values(),
            key=lambda b: b.get("created_at") or "",
            reverse=True,
        )
        if limit is not None and limit > 0:
            rows = rows[:limit]
        out = []
        for batch in rows:
            out.append(self._batch_summary(batch["id"]))
        return out

    def get_batch_detail(self, batch_id: str) -> dict[str, Any] | None:
        if batch_id not in self.batches:
            return None
        return self._batch_summary(batch_id, full=True)

    def _batch_summary(self, batch_id: str, *, full: bool = False) -> dict[str, Any]:
        batch = self.batches[batch_id]
        drafts = [d for d in self.drafts.values() if d.batch_id == batch_id]
        runs = [r for r in self.runs.values() if r.get("batch_id") == batch_id]
        docs = [d for d in self.documents.values() if d.get("batch_id") == batch_id]
        drafts_by_run: dict[str, list[DraftRow]] = {}
        for d in drafts:
            drafts_by_run.setdefault(d.run_id, []).append(d)
        latest_run, display_run = pick_display_run(
            runs,
            run_has_drafts=lambda rid: bool(drafts_by_run.get(rid)),
        )
        run_drafts = drafts_by_run.get(display_run["id"], []) if display_run else []
        summary = {
            "id": batch_id,
            "title": batch.get("title"),
            "subject": batch.get("subject"),
            "examYear": batch.get("exam_year"),
            "paperCode": batch.get("paper_code"),
            "syllabusId": batch.get("syllabus_id"),
            "uploadedBy": batch.get("uploaded_by"),
            "status": batch.get("status"),
            "createdAt": batch.get("created_at"),
            "questionCount": len(run_drafts),
            "approvedCount": sum(1 for d in run_drafts if d.status == "APPROVED"),
            "needsReviewCount": sum(
                1 for d in run_drafts if d.status in {"NEEDS_REVIEW", "DRAFT", "READY"}
            ),
            "rejectedCount": sum(1 for d in run_drafts if d.status == "REJECTED"),
            "latestRunId": latest_run["id"] if latest_run else None,
            "latestRunStatus": latest_run.get("status") if latest_run else None,
            "displayRunId": display_run["id"] if display_run else None,
        }
        if full:
            summary["documents"] = docs
            summary["runs"] = runs
            summary["drafts"] = run_drafts
        return summary

    def find_documents_by_sha256(self, sha256: str) -> list[dict[str, Any]]:
        return [d for d in self.documents.values() if d.get("sha256") == sha256]

    def list_syllabi(self) -> list[dict[str, Any]]:
        out = []
        for s in self.syllabi.values():
            nodes = [n for n in self.syllabus_nodes.values() if n["syllabus_id"] == s["id"]]
            out.append(
                {
                    **s,
                    "updatedAt": s.get("updated_at") or s.get("created_at"),
                    "topicCount": sum(1 for n in nodes if n["node_type"] == "TOPIC"),
                    "subtopicCount": sum(1 for n in nodes if n["node_type"] == "SUBTOPIC"),
                    "outcomeCount": sum(1 for n in nodes if n["node_type"] == "OUTCOME"),
                }
            )
        return sorted(
            out,
            key=lambda x: (str(x.get("updatedAt") or ""), x.get("subject") or ""),
            reverse=True,
        )

    def get_syllabus(self, syllabus_id: str) -> dict[str, Any] | None:
        s = self.syllabi.get(syllabus_id)
        if not s:
            return None
        nodes = self.list_syllabus_nodes(syllabus_id)
        from questbank.factory.syllabus_service import build_syllabus_tree

        return {
            **s,
            "topicCount": sum(1 for n in nodes if n["node_type"] == "TOPIC"),
            "subtopicCount": sum(1 for n in nodes if n["node_type"] == "SUBTOPIC"),
            "outcomeCount": sum(1 for n in nodes if n["node_type"] == "OUTCOME"),
            "nodes": nodes,
            "tree": build_syllabus_tree(nodes),
        }

    def create_syllabus(self, row: dict[str, Any]) -> dict[str, Any]:
        sid = row.get("id") or str(uuid4())
        stored = {**row, "id": sid}
        self.syllabi[sid] = stored
        return stored

    def list_syllabus_nodes(self, syllabus_id: str) -> list[dict[str, Any]]:
        return [n for n in self.syllabus_nodes.values() if n["syllabus_id"] == syllabus_id]

    def create_syllabus_node(self, row: dict[str, Any]) -> dict[str, Any]:
        nid = row.get("id") or str(uuid4())
        stored = {**row, "id": nid}
        self.syllabus_nodes[nid] = stored
        return stored

    def delete_syllabus_nodes(self, syllabus_id: str) -> None:
        to_delete = [nid for nid, n in self.syllabus_nodes.items() if n["syllabus_id"] == syllabus_id]
        for nid in to_delete:
            self.syllabus_nodes.pop(nid, None)

    def touch_syllabus(self, syllabus_id: str) -> None:
        if syllabus_id in self.syllabi:
            self.syllabi[syllabus_id]["updated_at"] = _utcnow()

    def get_draft(self, draft_id: str) -> DraftRow | None:
        return self.drafts.get(draft_id)

    def list_drafts_for_batch(self, batch_id: str) -> list[DraftRow]:
        detail = self.get_batch_detail(batch_id)
        if not detail:
            return []
        run_id = detail.get("displayRunId") or detail.get("latestRunId")
        if not run_id:
            return [d for d in self.drafts.values() if d.batch_id == batch_id]
        return [d for d in self.drafts.values() if d.run_id == run_id]

    def get_answer(self, run_id: str, question_ref: str) -> AnswerRow | None:
        aid = self._answer_by_run_ref.get((run_id, question_ref))
        return self.answers.get(aid) if aid else None

    def create_review_event(
        self,
        *,
        draft_id: str,
        action: str,
        comment: str | None = None,
        reviewer_id: str | None = None,
    ) -> dict[str, Any]:
        event = {
            "id": str(uuid4()),
            "draft_id": draft_id,
            "action": action,
            "comment": comment,
            "reviewer_id": reviewer_id,
            "created_at": _utcnow(),
        }
        self.review_events[event["id"]] = event
        return event

    def get_asset(self, asset_id: str) -> AssetRow | None:
        return self.assets.get(asset_id)

    def list_assets_by_ids(self, asset_ids: list[str]) -> list[AssetRow]:
        return [self.assets[i] for i in asset_ids if i in self.assets]

    def list_runs_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        return [r for r in self.runs.values() if r.get("batch_id") == batch_id]

    def list_documents_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        return [d for d in self.documents.values() if d.get("batch_id") == batch_id]

    def upsert_bank_question(self, *, source_key: str, subject: str) -> dict[str, Any]:
        existing = self.bank_by_source_key.get(source_key)
        if existing:
            q = self.bank_questions[existing]
            q["subject"] = subject
            q["updated_at"] = _utcnow()
            return q
        q = {
            "id": str(uuid4()),
            "source_key": source_key,
            "subject": subject,
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        self.bank_questions[q["id"]] = q
        self.bank_by_source_key[source_key] = q["id"]
        return q

    def next_version_number(self, question_id: str) -> int:
        nums = [v["version"] for v in self.versions.values() if v["question_id"] == question_id]
        return (max(nums) + 1) if nums else 1

    def retire_active_versions(self, question_id: str) -> None:
        for v in self.versions.values():
            if v["question_id"] == question_id and v["status"] in {"APPROVED", "PUBLISHED"}:
                v["status"] = "RETIRED"

    def create_question_version(self, row: dict[str, Any]) -> dict[str, Any]:
        vid = row.get("id") or str(uuid4())
        stored = {**row, "id": vid}
        self.versions[vid] = stored
        return stored

    def create_question_answer(self, row: dict[str, Any]) -> dict[str, Any]:
        aid = row.get("id") or str(uuid4())
        stored = {**row, "id": aid}
        self.version_answers[aid] = stored
        return stored

    def create_question_classification(self, row: dict[str, Any]) -> dict[str, Any]:
        cid = row.get("id") or str(uuid4())
        stored = {**row, "id": cid}
        self.classifications[cid] = stored
        return stored

    def append_classification_history(self, row: dict[str, Any]) -> dict[str, Any]:
        from datetime import datetime, timezone

        stored = {
            **row,
            "id": row.get("id") or str(uuid4()),
            "created_at": row.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }
        self.classification_history.insert(0, stored)
        return stored

    def list_classification_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        for h in self.classification_history[:limit]:
            out.append(
                {
                    "id": h.get("id"),
                    "questionVersionId": h.get("question_version_id"),
                    "sourceKey": h.get("source_key"),
                    "syllabusNodeId": h.get("syllabus_node_id"),
                    "topicCode": h.get("topic_code"),
                    "topicTitle": h.get("topic_title"),
                    "subtopicCode": h.get("subtopic_code"),
                    "subtopicTitle": h.get("subtopic_title"),
                    "bloomLevel": h.get("bloom_level"),
                    "classifiedBy": h.get("classified_by"),
                    "reasoning": h.get("reasoning"),
                    "method": h.get("method"),
                    "createdAt": h.get("created_at"),
                }
            )
        return out

    def list_bank_questions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        out = []
        for q in list(self.bank_questions.values())[:limit]:
            versions = [v for v in self.versions.values() if v["question_id"] == q["id"]]
            current = sorted(versions, key=lambda v: v["version"], reverse=True)
            current = next(
                (v for v in current if v["status"] != "RETIRED"),
                current[0] if current else None,
            )
            if current:
                current = dict(current)
                ans = next(
                    (
                        a
                        for a in self.version_answers.values()
                        if a.get("question_version_id") == current["id"]
                    ),
                    None,
                )
                current["answer"] = ans
            batch_id = None
            batch_title = None
            exam_year = None
            paper_code = None
            draft_id = (current or {}).get("source_draft_id")
            if draft_id and draft_id in self.drafts:
                draft = self.drafts[draft_id]
                batch_id = draft.batch_id
                batch = self.batches.get(batch_id) or {}
                batch_title = batch.get("title")
                exam_year = batch.get("exam_year")
                paper_code = batch.get("paper_code")
            out.append(
                {
                    **q,
                    "sourceKey": q["source_key"],
                    "batchId": batch_id,
                    "batchTitle": batch_title,
                    "examYear": exam_year,
                    "paperCode": paper_code,
                    "currentVersion": current,
                    "versionCount": len(versions),
                }
            )
        return out

    def get_bank_question(self, question_id: str) -> dict[str, Any] | None:
        q = self.bank_questions.get(question_id)
        if not q:
            return None
        versions = sorted(
            [v for v in self.versions.values() if v["question_id"] == question_id],
            key=lambda v: v["version"],
            reverse=True,
        )
        for v in versions:
            ans = next(
                (
                    a
                    for a in self.version_answers.values()
                    if a.get("question_version_id") == v["id"]
                ),
                None,
            )
            v["answer"] = ans
        return {**q, "sourceKey": q["source_key"], "versions": versions}

    def dashboard_metrics(self) -> dict[str, int]:
        drafts = list(self.drafts.values())
        return {
            "totalQuestions": len(drafts),
            "needsReview": sum(
                1 for d in drafts if d.status in {"NEEDS_REVIEW", "DRAFT", "READY"}
            ),
            "processingImports": sum(
                1 for b in self.batches.values() if b.get("status") in {"UPLOADED", "PROCESSING"}
            ),
            "publishedQuestions": sum(
                1
                for v in self.versions.values()
                if v.get("status") in {"APPROVED", "PUBLISHED"}
            ),
        }

    def list_unclassified_versions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        out = []
        for v in self.versions.values():
            if v.get("status") not in {"APPROVED", "PUBLISHED"}:
                continue
            q = self.bank_questions.get(v["question_id"])
            out.append(
                {
                    "versionId": v["id"],
                    "questionId": v["question_id"],
                    "sourceKey": q["source_key"] if q else None,
                    "subject": q["subject"] if q else None,
                    "version": v["version"],
                    "content": v.get("content"),
                    "status": v.get("status"),
                    "classificationStatus": "PENDING",
                }
            )
            if len(out) >= limit:
                break
        return out

    def random_mcq_drafts(self, *, count: int) -> list[dict[str, Any]]:
        import random as _random

        candidates = [d for d in self.drafts.values() if d.question_type == "MCQ"]
        picked = _random.sample(candidates, k=min(count, len(candidates)))
        return [
            {
                "id": d.id,
                "questionRef": d.question_ref,
                "content": d.content,
                "options": d.options,
                "paperTitle": (self.batches.get(d.batch_id) or {}).get("title"),
            }
            for d in picked
        ]

    def random_structured_drafts(self, *, count: int) -> list[dict[str, Any]]:
        import random as _random

        candidates = [
            d for d in self.drafts.values()
            if d.question_type == "STRUCTURED"
            and not d.requires_review
            and d.status != "APPROVED"
        ]
        picked = _random.sample(candidates, k=min(count, len(candidates)))
        return [
            {
                "id": d.id,
                "questionRef": d.question_ref,
                "content": d.content,
                "options": d.options,
                "paperTitle": (self.batches.get(d.batch_id) or {}).get("title"),
            }
            for d in picked
        ]

    def random_approved_structured_drafts(self, *, count: int) -> list[dict[str, Any]]:
        import random as _random

        candidates = [
            d for d in self.drafts.values()
            if d.question_type == "STRUCTURED" and d.status == "APPROVED"
        ]
        picked = _random.sample(candidates, k=min(count, len(candidates)))
        return [
            {
                "id": d.id,
                "questionRef": d.question_ref,
                "content": d.content,
                "options": d.options,
                "paperTitle": (self.batches.get(d.batch_id) or {}).get("title"),
            }
            for d in picked
        ]

    def random_bank_mcqs(self, *, count: int) -> list[dict[str, Any]]:
        import random as _random

        latest_by_question: dict[str, dict[str, Any]] = {}
        for v in self.versions.values():
            if v.get("status") not in {"APPROVED", "PUBLISHED"}:
                continue
            qid = v["question_id"]
            current = latest_by_question.get(qid)
            if current is None or v["version"] > current["version"]:
                latest_by_question[qid] = v
        candidates = list(latest_by_question.values())
        picked = _random.sample(candidates, k=min(count, len(candidates)))
        return [
            {
                "id": v["id"],
                "questionId": v["question_id"],
                "paperTitle": (self.bank_questions.get(v["question_id"]) or {}).get("subject"),
                "content": v.get("content"),
                "options": v.get("options"),
            }
            for v in picked
        ]

    def get_bank_version_answer(self, version_id: str) -> dict[str, Any] | None:
        answer = next(
            (a for a in self.version_answers.values() if a.get("question_version_id") == version_id),
            None,
        )
        if not answer:
            return None
        return {
            "value": answer.get("value"),
            "explanation": answer.get("explanation"),
            "explanationSource": answer.get("explanation_source"),
        }

    def set_draft_topic(self, draft_id: str, *, topic_node_id: str) -> None:
        draft = self.drafts.get(draft_id)
        if draft is None:
            return
        node = self.syllabus_nodes.get(topic_node_id) or {}
        draft.topic_node_id = topic_node_id
        if node.get("node_type") == "SUBTOPIC":
            parent = self.syllabus_nodes.get(node.get("parent_id")) or {}
            draft.topic_code = parent.get("code")
            draft.topic_title = parent.get("title")
            draft.subtopic_code = node.get("code")
            draft.subtopic_title = node.get("title")
        else:
            draft.topic_code = node.get("code")
            draft.topic_title = node.get("title")
            draft.subtopic_code = None
            draft.subtopic_title = None

    def set_draft_bloom(self, draft_id: str, *, bloom_level: str, bloom_reasoning: str) -> None:
        draft = self.drafts.get(draft_id)
        if draft is None:
            return
        draft.bloom_level = bloom_level
        draft.bloom_reasoning = bloom_reasoning


class PostgresFactoryStore:
    """Talks to Prisma-managed PostgreSQL tables via psycopg.

    Prisma owns migrations/enums; this store only executes SQL against those tables.
    """

    def __init__(self, dsn: str | None = None, connection: Any | None = None) -> None:
        self._own_conn = connection is None
        if connection is not None:
            self._conn = connection
        else:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "psycopg is required for PostgresFactoryStore. "
                    "Install with: pip install 'questbank[factory]'"
                ) from exc
            dsn = dsn or (os.environ.get("DATABASE_URL") or "").strip()
            if not dsn:
                raise RuntimeError("DATABASE_URL is not set")
            self._conn = psycopg.connect(dsn, autocommit=False)

    def ensure_ready(self) -> None:
        """Clear an aborted transaction so the shared connection can be reused."""
        try:
            status = getattr(self._conn.info, "transaction_status", None)
            status_name = getattr(status, "name", None) or str(status or "")
            if status_name in {"INERROR", "3"} or str(status) == "3":
                self._conn.rollback()
        except Exception:  # noqa: BLE001
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._own_conn:
            self._conn.close()

    def __enter__(self) -> PostgresFactoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        self.ensure_ready()
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, title, subject, exam_year, paper_code, syllabus_id::text,
                       uploaded_by::text, status::text, created_at
                FROM "ingestion_batches" WHERE id = %s::uuid
                """,
                (batch_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "subject": row[2],
            "exam_year": row[3],
            "paper_code": row[4],
            "syllabus_id": row[5],
            "uploaded_by": row[6],
            "status": row[7],
            "created_at": row[8],
        }

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                'SELECT id::text, batch_id::text, status::text FROM "ingestion_runs" WHERE id = %s::uuid',
                (run_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "batch_id": row[1], "status": row[2]}

    def get_source_document(self, document_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                'SELECT id::text, batch_id::text, document_type::text FROM "source_documents" WHERE id = %s::uuid',
                (document_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "batch_id": row[1], "document_type": row[2]}

    def create_batch(self, row: dict[str, Any]) -> dict[str, Any]:
        batch_id = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "ingestion_batches"
                  (id, title, subject, exam_year, paper_code, syllabus_id, uploaded_by, status)
                VALUES
                  (%s::uuid, %s, %s, %s, %s, %s::uuid, %s::uuid, %s::ingestion_batch_status)
                RETURNING id::text
                """,
                (
                    batch_id,
                    row["title"],
                    row["subject"],
                    row.get("exam_year"),
                    row.get("paper_code"),
                    row.get("syllabus_id"),
                    row.get("uploaded_by"),
                    row["status"],
                ),
            )
            out_id = cur.fetchone()[0]
        self._conn.commit()
        return {**row, "id": out_id}

    def create_source_document(self, row: dict[str, Any]) -> dict[str, Any]:
        doc_id = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "source_documents"
                  (id, batch_id, document_type, storage_path, original_name, mime_type, page_count, sha256)
                VALUES
                  (%s::uuid, %s::uuid, %s::source_document_type, %s, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    doc_id,
                    row["batch_id"],
                    row["document_type"],
                    row["storage_path"],
                    row["original_name"],
                    row["mime_type"],
                    row.get("page_count"),
                    row.get("sha256"),
                ),
            )
            out_id = cur.fetchone()[0]
        self._conn.commit()
        return {**row, "id": out_id}

    def create_run(self, row: dict[str, Any]) -> dict[str, Any]:
        run_id = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "ingestion_runs"
                  (id, batch_id, parser_version, model_name, status)
                VALUES
                  (%s::uuid, %s::uuid, %s, %s, %s::ingestion_run_status)
                RETURNING id::text
                """,
                (
                    run_id,
                    row["batch_id"],
                    row.get("parser_version"),
                    row.get("model_name"),
                    row["status"],
                ),
            )
            out_id = cur.fetchone()[0]
        self._conn.commit()
        return {**row, "id": out_id}

    def update_batch_status(self, batch_id: str, status: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "ingestion_batches"
                SET status = %s::ingestion_batch_status, updated_at = NOW()
                WHERE id = %s::uuid
                """,
                (status, batch_id),
            )
        self._conn.commit()

    def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        completed_at: datetime | None = None,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "ingestion_runs"
                SET status = %s::ingestion_run_status,
                    completed_at = COALESCE(%s, completed_at)
                WHERE id = %s::uuid
                """,
                (status, completed_at, run_id),
            )
        self._conn.commit()

    def delete_drafts_for_run(self, run_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                'DELETE FROM "question_assets" WHERE draft_id IN '
                '(SELECT id FROM "question_drafts" WHERE run_id = %s::uuid)',
                (run_id,),
            )
            cur.execute('DELETE FROM "question_drafts" WHERE run_id = %s::uuid', (run_id,))
        self._conn.commit()

    def delete_answers_for_run(self, run_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute('DELETE FROM "answer_records" WHERE run_id = %s::uuid', (run_id,))
        self._conn.commit()

    def delete_batch(self, batch_id: str) -> None:
        # Collect all asset storage paths associated with this batch first
        storage_paths = []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT storage_path FROM "question_assets"
                WHERE draft_id IN (
                    SELECT id FROM "question_drafts" WHERE batch_id = %s::uuid
                )
                OR source_document_id IN (
                    SELECT id FROM "source_documents" WHERE batch_id = %s::uuid
                )
                """,
                (batch_id, batch_id),
            )
            storage_paths = [r[0] for r in cur.fetchall() if r[0]]

        # Physical disk cleanup
        import shutil
        from pathlib import Path
        from questbank.factory.local_storage import factory_storage_root

        storage_root = factory_storage_root()
        shutil.rmtree(storage_root / "import-keep" / batch_id, ignore_errors=True)
        shutil.rmtree(storage_root / "exam-sources" / batch_id, ignore_errors=True)

        prefix = f"factory-{batch_id[:8]}"
        output_root = Path("output")
        if output_root.is_dir():
            for child in output_root.iterdir():
                if child.is_dir() and child.name.startswith(prefix):
                    shutil.rmtree(child, ignore_errors=True)

        # Delete local asset files from local factory storage root
        for sp in storage_paths:
            local_path = storage_root / sp.replace("\\", "/").lstrip("/")
            if local_path.is_file():
                try:
                    local_path.unlink()
                except Exception:
                    pass

        # Delete from Supabase Storage if configured
        if storage_paths:
            try:
                from questbank.integrations.supabase_store import get_supabase_client, questions_bucket
                sb_client = get_supabase_client()
                if sb_client is not None:
                    bucket = questions_bucket()
                    # Chunk deletions up to 100 paths at a time
                    for i in range(0, len(storage_paths), 100):
                        chunk = storage_paths[i:i+100]
                        sb_client.storage.from_(bucket).remove(chunk)
            except Exception as exc:
                logger.warning("Failed to clean up storage paths from Supabase: %s", exc)

        with self._conn.cursor() as cur:
            # 1. Delete assets associated with drafts of this batch
            cur.execute(
                """
                DELETE FROM "question_assets"
                WHERE draft_id IN (
                    SELECT id FROM "question_drafts" WHERE batch_id = %s::uuid
                )
                """,
                (batch_id,),
            )
            # 2. Delete assets associated with source documents of this batch
            cur.execute(
                """
                DELETE FROM "question_assets"
                WHERE source_document_id IN (
                    SELECT id FROM "source_documents" WHERE batch_id = %s::uuid
                )
                """,
                (batch_id,),
            )
            # 3. Delete reviews for drafts of this batch
            cur.execute(
                """
                DELETE FROM "review_events"
                WHERE draft_id IN (
                    SELECT id FROM "question_drafts" WHERE batch_id = %s::uuid
                )
                """,
                (batch_id,),
            )
            # 4. Finally, delete the batch itself (cascades to runs, drafts, documents, answer_records)
            cur.execute('DELETE FROM "ingestion_batches" WHERE id = %s::uuid', (batch_id,))
        self._conn.commit()

    def upsert_draft(self, row: DraftRow) -> DraftRow:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "question_drafts"
                  (id, run_id, batch_id, question_ref, question_type, content, options,
                   source_page, source_regions, status, requires_review, validation_issues)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s, %s::question_type, %s::jsonb, %s::jsonb,
                   %s, %s::jsonb, %s::draft_status, %s, %s::jsonb)
                ON CONFLICT (run_id, question_ref) DO UPDATE SET
                  question_type = EXCLUDED.question_type,
                  content = EXCLUDED.content,
                  options = EXCLUDED.options,
                  source_page = EXCLUDED.source_page,
                  source_regions = EXCLUDED.source_regions,
                  status = EXCLUDED.status,
                  requires_review = EXCLUDED.requires_review,
                  validation_issues = EXCLUDED.validation_issues,
                  updated_at = NOW()
                RETURNING id::text
                """,
                (
                    row.id,
                    row.run_id,
                    row.batch_id,
                    row.question_ref,
                    row.question_type,
                    json.dumps(_json(row.content)),
                    json.dumps(_json(row.options)),
                    row.source_page,
                    json.dumps(_json(row.source_regions)) if row.source_regions is not None else None,
                    row.status,
                    row.requires_review,
                    json.dumps(_json(row.validation_issues))
                    if row.validation_issues is not None
                    else None,
                ),
            )
            row.id = cur.fetchone()[0]
        self._conn.commit()
        return row

    def update_draft_json(
        self,
        draft_id: str,
        *,
        content: Any,
        options: Any,
        status: str,
        requires_review: bool,
        validation_issues: Any,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "question_drafts"
                SET content = %s::jsonb,
                    options = %s::jsonb,
                    status = %s::draft_status,
                    requires_review = %s,
                    validation_issues = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s::uuid
                """,
                (
                    json.dumps(_json(content)),
                    json.dumps(_json(options)),
                    status,
                    requires_review,
                    json.dumps(_json(validation_issues))
                    if validation_issues is not None
                    else None,
                    draft_id,
                ),
            )
        self._conn.commit()

    def rename_draft_question_ref(self, draft_id: str, new_ref: str) -> None:
        """Renumber a draft AND its paired answer row so they stay linked.

        question_drafts and answer_records are matched by (run_id,
        question_ref) — renaming only one side would orphan the mark scheme.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                'SELECT run_id::text, question_ref FROM "question_drafts" WHERE id = %s::uuid',
                (draft_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            run_id, old_ref = row
            cur.execute(
                'UPDATE "question_drafts" SET question_ref = %s, updated_at = NOW() WHERE id = %s::uuid',
                (new_ref, draft_id),
            )
            cur.execute(
                'UPDATE "answer_records" SET question_ref = %s WHERE run_id = %s::uuid AND question_ref = %s',
                (new_ref, run_id, old_ref),
            )
        self._conn.commit()

    def upsert_answer(self, row: AnswerRow) -> AnswerRow:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "answer_records"
                  (id, run_id, batch_id, question_ref, answer_value, parts_answer,
                   explanation, explanation_source, source_page)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::jsonb,
                   %s, %s::explanation_source, %s)
                ON CONFLICT (run_id, question_ref) DO UPDATE SET
                  answer_value = EXCLUDED.answer_value,
                  parts_answer = EXCLUDED.parts_answer,
                  explanation = EXCLUDED.explanation,
                  explanation_source = EXCLUDED.explanation_source,
                  source_page = EXCLUDED.source_page
                RETURNING id::text
                """,
                (
                    row.id,
                    row.run_id,
                    row.batch_id,
                    row.question_ref,
                    row.answer_value,
                    json.dumps(_json(row.parts_answer))
                    if row.parts_answer is not None
                    else None,
                    row.explanation,
                    row.explanation_source,
                    row.source_page,
                ),
            )
            row.id = cur.fetchone()[0]
        self._conn.commit()
        return row

    def create_asset(self, row: AssetRow) -> AssetRow:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "question_assets"
                  (id, draft_id, source_document_id, role, storage_path, mime_type,
                   source_page, bbox, width, height, sha256)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s::asset_role, %s, %s,
                   %s, %s::jsonb, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    row.id,
                    row.draft_id,
                    row.source_document_id,
                    row.role,
                    row.storage_path,
                    row.mime_type,
                    row.source_page,
                    json.dumps(_json(row.bbox)) if row.bbox is not None else None,
                    row.width,
                    row.height,
                    row.sha256,
                ),
            )
            row.id = cur.fetchone()[0]
        self._conn.commit()
        return row

    def update_asset(
        self,
        asset_id: str,
        *,
        storage_path: str,
        mime_type: str,
        sha256: str | None,
        width: int | None,
        height: int | None,
    ) -> AssetRow:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "question_assets"
                SET storage_path = %s,
                    mime_type = %s,
                    sha256 = %s,
                    width = %s,
                    height = %s
                WHERE id = %s::uuid
                RETURNING id::text
                """,
                (storage_path, mime_type, sha256, width, height, asset_id),
            )
            row = cur.fetchone()
        self._conn.commit()
        if not row:
            raise KeyError(f"Asset not found: {asset_id}")
        asset = self.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Asset not found: {asset_id}")
        return asset

    def list_drafts_for_run(self, run_id: str) -> list[DraftRow]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id::text, d.run_id::text, d.batch_id::text, d.question_ref, d.question_type::text,
                       d.content, d.options, d.source_page, d.source_regions, d.status::text,
                       d.requires_review, d.validation_issues,
                       d.topic_node_id::text, n.code, n.title, n.node_type::text, pn.code, pn.title,
                       d.bloom_level, d.bloom_reasoning
                FROM "question_drafts" d
                LEFT JOIN "syllabus_nodes" n ON n.id = d.topic_node_id
                LEFT JOIN "syllabus_nodes" pn ON pn.id = n.parent_id
                WHERE d.run_id = %s::uuid
                """,
                (run_id,),
            )
            rows = cur.fetchall()
        return [
            _draft_row_with_topic_hierarchy(
                id=r[0],
                run_id=r[1],
                batch_id=r[2],
                question_ref=r[3],
                question_type=r[4],
                content=r[5],
                options=r[6],
                source_page=r[7],
                source_regions=r[8],
                status=r[9],
                requires_review=r[10],
                validation_issues=r[11],
                topic_node_id=r[12],
                node_code=r[13],
                node_title=r[14],
                node_type=r[15],
                parent_code=r[16],
                parent_title=r[17],
                bloom_level=r[18],
                bloom_reasoning=r[19],
            )
            for r in rows
        ]

    def list_answers_for_run(self, run_id: str) -> list[AnswerRow]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, run_id::text, batch_id::text, question_ref, answer_value,
                       parts_answer, explanation, explanation_source::text, source_page
                FROM "answer_records" WHERE run_id = %s::uuid
                """,
                (run_id,),
            )
            rows = cur.fetchall()
        return [
            AnswerRow(
                id=r[0],
                run_id=r[1],
                batch_id=r[2],
                question_ref=r[3],
                answer_value=r[4],
                parts_answer=r[5],
                explanation=r[6],
                explanation_source=r[7],
                source_page=r[8],
            )
            for r in rows
        ]

    def list_assets_for_draft(self, draft_id: str) -> list[AssetRow]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, draft_id::text, source_document_id::text, role::text,
                       storage_path, mime_type, source_page, bbox, width, height, sha256
                FROM "question_assets" WHERE draft_id = %s::uuid
                """,
                (draft_id,),
            )
            rows = cur.fetchall()
        return [
            AssetRow(
                id=r[0],
                draft_id=r[1],
                source_document_id=r[2],
                role=r[3],
                storage_path=r[4],
                mime_type=r[5],
                source_page=r[6],
                bbox=r[7],
                width=r[8],
                height=r[9],
                sha256=r[10],
            )
            for r in rows
        ]

    def list_batches(self, *, limit: int | None = 50) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            if limit is None or limit <= 0:
                cur.execute(
                    """
                    SELECT id::text, title, subject, exam_year, paper_code, syllabus_id::text,
                           uploaded_by::text, status::text, created_at
                    FROM "ingestion_batches"
                    ORDER BY created_at DESC
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT id::text, title, subject, exam_year, paper_code, syllabus_id::text,
                           uploaded_by::text, status::text, created_at
                    FROM "ingestion_batches"
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            batch_rows = cur.fetchall()
            ids = [r[0] for r in batch_rows]
            if not ids:
                return []
            cur.execute(
                """
                SELECT id::text, batch_id::text, status::text, started_at
                FROM "ingestion_runs"
                WHERE batch_id = ANY(%s::uuid[])
                ORDER BY started_at DESC NULLS LAST
                """,
                (ids,),
            )
            runs_by_batch: dict[str, list[dict[str, Any]]] = {}
            for r in cur.fetchall():
                runs_by_batch.setdefault(r[1], []).append(
                    {"id": r[0], "batch_id": r[1], "status": r[2], "started_at": r[3]}
                )
            cur.execute(
                """
                SELECT run_id::text,
                       COUNT(*)::int,
                       COUNT(*) FILTER (WHERE status = 'APPROVED'::draft_status)::int,
                       COUNT(*) FILTER (
                         WHERE status IN (
                           'NEEDS_REVIEW'::draft_status,
                           'DRAFT'::draft_status,
                           'READY'::draft_status
                         )
                       )::int,
                       COUNT(*) FILTER (WHERE status = 'REJECTED'::draft_status)::int
                FROM "question_drafts"
                WHERE batch_id = ANY(%s::uuid[])
                GROUP BY run_id
                """,
                (ids,),
            )
            counts_by_run = {
                r[0]: {"q": r[1], "approved": r[2], "needs": r[3], "rejected": r[4]}
                for r in cur.fetchall()
            }

        out: list[dict[str, Any]] = []
        for row in batch_rows:
            batch_id = row[0]
            runs = runs_by_batch.get(batch_id, [])
            latest_run, display_run = pick_display_run(
                runs,
                run_has_drafts=lambda rid, counts=counts_by_run: (counts.get(rid) or {}).get("q", 0) > 0,
            )
            display_id = display_run["id"] if display_run else None
            counts = counts_by_run.get(display_id or "", {}) if display_id else {}
            out.append(
                {
                    "id": batch_id,
                    "title": row[1],
                    "subject": row[2],
                    "examYear": row[3],
                    "paperCode": row[4],
                    "syllabusId": row[5],
                    "uploadedBy": row[6],
                    "status": row[7],
                    "createdAt": row[8],
                    "questionCount": counts.get("q", 0),
                    "approvedCount": counts.get("approved", 0),
                    "needsReviewCount": counts.get("needs", 0),
                    "rejectedCount": counts.get("rejected", 0),
                    "latestRunId": latest_run["id"] if latest_run else None,
                    "latestRunStatus": latest_run.get("status") if latest_run else None,
                    "displayRunId": display_id,
                }
            )
        return out

    def get_batch_detail(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.get_batch(batch_id)
        if not batch:
            return None
        docs = self.list_documents_for_batch(batch_id)
        runs = self.list_runs_for_batch(batch_id)
        latest_run, display_run = pick_display_run(
            runs,
            run_has_drafts=lambda rid: bool(self.list_drafts_for_run(rid)),
        )
        drafts = self.list_drafts_for_run(display_run["id"]) if display_run else []
        return {
            "id": batch["id"],
            "title": batch["title"],
            "subject": batch["subject"],
            "examYear": batch.get("exam_year"),
            "paperCode": batch.get("paper_code"),
            "syllabusId": batch.get("syllabus_id"),
            "uploadedBy": batch.get("uploaded_by"),
            "status": batch["status"],
            "createdAt": batch.get("created_at"),
            "questionCount": len(drafts),
            "approvedCount": sum(1 for d in drafts if d.status == "APPROVED"),
            "needsReviewCount": sum(
                1 for d in drafts if d.status in {"NEEDS_REVIEW", "DRAFT", "READY"}
            ),
            "rejectedCount": sum(1 for d in drafts if d.status == "REJECTED"),
            "latestRunId": latest_run["id"] if latest_run else None,
            "latestRunStatus": latest_run.get("status") if latest_run else None,
            "displayRunId": display_run["id"] if display_run else None,
            "documents": docs,
            "runs": runs,
        }

    def find_documents_by_sha256(self, sha256: str) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, batch_id::text, document_type::text, storage_path,
                       original_name, mime_type, page_count, sha256, created_at
                FROM "source_documents" WHERE sha256 = %s
                """,
                (sha256,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "batch_id": r[1],
                "document_type": r[2],
                "storage_path": r[3],
                "original_name": r[4],
                "mime_type": r[5],
                "page_count": r[6],
                "sha256": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]

    def list_syllabi(self) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id::text, s.subject, s.code, s.year, s.title, s.updated_at,
                       COUNT(*) FILTER (WHERE n.node_type = 'TOPIC'::syllabus_node_type) AS topics,
                       COUNT(*) FILTER (WHERE n.node_type = 'SUBTOPIC'::syllabus_node_type) AS subtopics,
                       COUNT(*) FILTER (WHERE n.node_type = 'OUTCOME'::syllabus_node_type) AS outcomes
                FROM "syllabuses" s
                LEFT JOIN "syllabus_nodes" n ON n.syllabus_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.subject, s.year DESC
                """
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "subject": r[1],
                "code": r[2],
                "year": r[3],
                "title": r[4],
                "updatedAt": r[5],
                "topicCount": int(r[6] or 0),
                "subtopicCount": int(r[7] or 0),
                "outcomeCount": int(r[8] or 0),
            }
            for r in rows
        ]

    def get_syllabus(self, syllabus_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, subject, code, year, title
                FROM "syllabuses" WHERE id = %s::uuid
                """,
                (syllabus_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        nodes = self.list_syllabus_nodes(syllabus_id)
        from questbank.factory.syllabus_service import build_syllabus_tree

        return {
            "id": row[0],
            "subject": row[1],
            "code": row[2],
            "year": row[3],
            "title": row[4],
            "topicCount": sum(1 for n in nodes if n["node_type"] == "TOPIC"),
            "subtopicCount": sum(1 for n in nodes if n["node_type"] == "SUBTOPIC"),
            "outcomeCount": sum(1 for n in nodes if n["node_type"] == "OUTCOME"),
            "nodes": nodes,
            "tree": build_syllabus_tree(nodes),
        }

    def create_syllabus(self, row: dict[str, Any]) -> dict[str, Any]:
        sid = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "syllabuses" (id, subject, code, year, title)
                VALUES (%s::uuid, %s, %s, %s, %s)
                RETURNING id::text, subject, code, year, title
                """,
                (sid, row["subject"], row["code"], row["year"], row["title"]),
            )
            out = cur.fetchone()
        self._conn.commit()
        return {
            "id": out[0],
            "subject": out[1],
            "code": out[2],
            "year": out[3],
            "title": out[4],
        }

    def list_syllabus_nodes(self, syllabus_id: str) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, syllabus_id::text, parent_id::text, code, title,
                       outcomes_text, node_type::text, sort_order
                FROM "syllabus_nodes"
                WHERE syllabus_id = %s::uuid
                ORDER BY sort_order NULLS LAST, code NULLS LAST, title
                """,
                (syllabus_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "syllabus_id": r[1],
                "parent_id": r[2],
                "code": r[3],
                "title": r[4],
                "outcomes_text": r[5],
                "node_type": r[6],
                "sort_order": r[7],
            }
            for r in rows
        ]

    def create_syllabus_node(self, row: dict[str, Any]) -> dict[str, Any]:
        nid = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "syllabus_nodes"
                  (id, syllabus_id, parent_id, code, title, outcomes_text, node_type, sort_order)
                VALUES
                  (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::syllabus_node_type, %s)
                RETURNING id::text
                """,
                (
                    nid,
                    row["syllabus_id"],
                    row.get("parent_id"),
                    row.get("code"),
                    row["title"],
                    row.get("outcomes_text"),
                    row["node_type"],
                    row.get("sort_order"),
                ),
            )
            out_id = cur.fetchone()[0]
        self._conn.commit()
        return {**row, "id": out_id}

    def delete_syllabus_nodes(self, syllabus_id: str) -> None:
        with self._conn.cursor() as cur:
            # Children first due to parent restrict
            cur.execute(
                """
                DELETE FROM "syllabus_nodes"
                WHERE syllabus_id = %s::uuid AND node_type = 'OUTCOME'::syllabus_node_type
                """,
                (syllabus_id,),
            )
            cur.execute(
                """
                DELETE FROM "syllabus_nodes"
                WHERE syllabus_id = %s::uuid AND node_type = 'SUBTOPIC'::syllabus_node_type
                """,
                (syllabus_id,),
            )
            cur.execute(
                """
                DELETE FROM "syllabus_nodes"
                WHERE syllabus_id = %s::uuid AND node_type = 'TOPIC'::syllabus_node_type
                """,
                (syllabus_id,),
            )
            cur.execute(
                """
                DELETE FROM "syllabus_nodes"
                WHERE syllabus_id = %s::uuid
                """,
                (syllabus_id,),
            )
        self._conn.commit()

    def touch_syllabus(self, syllabus_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                'UPDATE "syllabuses" SET updated_at = NOW() WHERE id = %s::uuid',
                (syllabus_id,),
            )
        self._conn.commit()

    def get_draft(self, draft_id: str) -> DraftRow | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.id::text, d.run_id::text, d.batch_id::text, d.question_ref, d.question_type::text,
                       d.content, d.options, d.source_page, d.source_regions, d.status::text,
                       d.requires_review, d.validation_issues,
                       d.topic_node_id::text, n.code, n.title, n.node_type::text, pn.code, pn.title,
                       d.bloom_level, d.bloom_reasoning
                FROM "question_drafts" d
                LEFT JOIN "syllabus_nodes" n ON n.id = d.topic_node_id
                LEFT JOIN "syllabus_nodes" pn ON pn.id = n.parent_id
                WHERE d.id = %s::uuid
                """,
                (draft_id,),
            )
            r = cur.fetchone()
        if not r:
            return None
        return _draft_row_with_topic_hierarchy(
            id=r[0],
            run_id=r[1],
            batch_id=r[2],
            question_ref=r[3],
            question_type=r[4],
            content=r[5],
            options=r[6],
            source_page=r[7],
            source_regions=r[8],
            status=r[9],
            requires_review=r[10],
            validation_issues=r[11],
            topic_node_id=r[12],
            node_code=r[13],
            node_title=r[14],
            node_type=r[15],
            parent_code=r[16],
            parent_title=r[17],
            bloom_level=r[18],
            bloom_reasoning=r[19],
        )

    def list_drafts_for_batch(self, batch_id: str) -> list[DraftRow]:
        detail = self.get_batch_detail(batch_id)
        if not detail:
            return []
        run_id = detail.get("displayRunId") or detail.get("latestRunId")
        if not run_id:
            return []
        return self.list_drafts_for_run(run_id)

    def get_answer(self, run_id: str, question_ref: str) -> AnswerRow | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, run_id::text, batch_id::text, question_ref, answer_value,
                       parts_answer, explanation, explanation_source::text, source_page
                FROM "answer_records"
                WHERE run_id = %s::uuid AND question_ref = %s
                """,
                (run_id, question_ref),
            )
            r = cur.fetchone()
        if not r:
            return None
        return AnswerRow(
            id=r[0],
            run_id=r[1],
            batch_id=r[2],
            question_ref=r[3],
            answer_value=r[4],
            parts_answer=r[5],
            explanation=r[6],
            explanation_source=r[7],
            source_page=r[8],
        )

    def create_review_event(
        self,
        *,
        draft_id: str,
        action: str,
        comment: str | None = None,
        reviewer_id: str | None = None,
    ) -> dict[str, Any]:
        event_id = str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "review_events" (id, draft_id, reviewer_id, action, comment)
                VALUES (%s::uuid, %s::uuid, %s::uuid, %s::review_action, %s)
                RETURNING id::text, created_at
                """,
                (event_id, draft_id, reviewer_id, action, comment),
            )
            row = cur.fetchone()
        self._conn.commit()
        return {
            "id": row[0],
            "draft_id": draft_id,
            "action": action,
            "comment": comment,
            "reviewer_id": reviewer_id,
            "created_at": row[1],
        }

    def get_asset(self, asset_id: str) -> AssetRow | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, draft_id::text, source_document_id::text, role::text,
                       storage_path, mime_type, source_page, bbox, width, height, sha256
                FROM "question_assets" WHERE id = %s::uuid
                """,
                (asset_id,),
            )
            r = cur.fetchone()
        if not r:
            return None
        return AssetRow(
            id=r[0],
            draft_id=r[1],
            source_document_id=r[2],
            role=r[3],
            storage_path=r[4],
            mime_type=r[5],
            source_page=r[6],
            bbox=r[7],
            width=r[8],
            height=r[9],
            sha256=r[10],
        )

    def list_assets_by_ids(self, asset_ids: list[str]) -> list[AssetRow]:
        if not asset_ids:
            return []
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, draft_id::text, source_document_id::text, role::text,
                       storage_path, mime_type, source_page, bbox, width, height, sha256
                FROM "question_assets" WHERE id = ANY(%s::uuid[])
                """,
                (asset_ids,),
            )
            rows = cur.fetchall()
        return [
            AssetRow(
                id=r[0],
                draft_id=r[1],
                source_document_id=r[2],
                role=r[3],
                storage_path=r[4],
                mime_type=r[5],
                source_page=r[6],
                bbox=r[7],
                width=r[8],
                height=r[9],
                sha256=r[10],
            )
            for r in rows
        ]

    def list_runs_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, batch_id::text, parser_version, model_name, status::text,
                       started_at, completed_at
                FROM "ingestion_runs"
                WHERE batch_id = %s::uuid
                ORDER BY started_at DESC
                """,
                (batch_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "batch_id": r[1],
                "parser_version": r[2],
                "model_name": r[3],
                "status": r[4],
                "started_at": r[5],
                "completed_at": r[6],
            }
            for r in rows
        ]

    def list_documents_for_batch(self, batch_id: str) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, batch_id::text, document_type::text, storage_path,
                       original_name, mime_type, page_count, sha256, created_at
                FROM "source_documents" WHERE batch_id = %s::uuid
                """,
                (batch_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "batch_id": r[1],
                "document_type": r[2],
                "storage_path": r[3],
                "original_name": r[4],
                "mime_type": r[5],
                "page_count": r[6],
                "sha256": r[7],
                "created_at": r[8],
            }
            for r in rows
        ]

    def upsert_bank_question(self, *, source_key: str, subject: str) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(
                'SELECT id::text, source_key, subject FROM "bank_questions" WHERE source_key = %s',
                (source_key,),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE "bank_questions"
                    SET subject = %s, updated_at = NOW()
                    WHERE id = %s::uuid
                    RETURNING id::text, source_key, subject
                    """,
                    (subject, existing[0]),
                )
                row = cur.fetchone()
            else:
                qid = str(uuid4())
                cur.execute(
                    """
                    INSERT INTO "bank_questions" (id, source_key, subject)
                    VALUES (%s::uuid, %s, %s)
                    RETURNING id::text, source_key, subject
                    """,
                    (qid, source_key, subject),
                )
                row = cur.fetchone()
        self._conn.commit()
        return {"id": row[0], "source_key": row[1], "subject": row[2]}

    def next_version_number(self, question_id: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                'SELECT COALESCE(MAX(version), 0) FROM "question_versions" WHERE question_id = %s::uuid',
                (question_id,),
            )
            return int(cur.fetchone()[0]) + 1

    def retire_active_versions(self, question_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "question_versions"
                SET status = 'RETIRED'::question_version_status
                WHERE question_id = %s::uuid
                  AND status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                """,
                (question_id,),
            )
        self._conn.commit()

    def create_question_version(self, row: dict[str, Any]) -> dict[str, Any]:
        vid = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "question_versions"
                  (id, question_id, version, content, options, source_draft_id, status,
                   approved_by, approved_at, published_at, bloom_level, bloom_reasoning)
                VALUES
                  (%s::uuid, %s::uuid, %s, %s::jsonb, %s::jsonb, %s::uuid,
                   %s::question_version_status, %s::uuid, %s, %s, %s, %s)
                RETURNING id::text
                """,
                (
                    vid,
                    row["question_id"],
                    row["version"],
                    json.dumps(_json(row["content"])),
                    json.dumps(_json(row["options"])),
                    row.get("source_draft_id"),
                    row["status"],
                    row.get("approved_by"),
                    row.get("approved_at"),
                    row.get("published_at"),
                    row.get("bloom_level"),
                    row.get("bloom_reasoning"),
                ),
            )
            out_id = cur.fetchone()[0]
        self._conn.commit()
        return {**row, "id": out_id}

    def create_question_answer(self, row: dict[str, Any]) -> dict[str, Any]:
        aid = row.get("id") or str(uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO "question_answers"
                  (id, question_version_id, value, parts_answer, explanation, explanation_source)
                VALUES
                  (%s::uuid, %s::uuid, %s, %s::jsonb, %s, %s::explanation_source)
                RETURNING id::text
                """,
                (
                    aid,
                    row["question_version_id"],
                    row.get("value"),
                    json.dumps(_json(row["parts_answer"]))
                    if row.get("parts_answer") is not None
                    else None,
                    row.get("explanation"),
                    row.get("explanation_source"),
                ),
            )
            out_id = cur.fetchone()[0]
        self._conn.commit()
        return {**row, "id": out_id}

    def create_question_classification(self, row: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        cid = row.get("id") or str(uuid4())
        try:
            with self._conn.cursor() as cur:
                # Re-classify safely: replace any existing primary row for this version,
                # then upsert the (version, node) pair.
                if row.get("is_primary", True):
                    cur.execute(
                        """
                        DELETE FROM "question_classifications"
                        WHERE question_version_id = %s::uuid AND is_primary = true
                          AND syllabus_node_id <> %s::uuid
                        """,
                        (row["question_version_id"], row["syllabus_node_id"]),
                    )
                cur.execute(
                    """
                    INSERT INTO "question_classifications"
                      (id, question_version_id, syllabus_node_id, is_primary, status, classified_by)
                    VALUES
                      (%s::uuid, %s::uuid, %s::uuid, %s, %s::classification_status, %s)
                    ON CONFLICT (question_version_id, syllabus_node_id)
                    DO UPDATE SET
                      is_primary = EXCLUDED.is_primary,
                      status = EXCLUDED.status,
                      classified_by = EXCLUDED.classified_by,
                      updated_at = CURRENT_TIMESTAMP
                    RETURNING id::text
                    """,
                    (
                        cid,
                        row["question_version_id"],
                        row["syllabus_node_id"],
                        row.get("is_primary", True),
                        row.get("status", "CLASSIFIED"),
                        row.get("classified_by", "Kimi Moonshot AI"),
                    ),
                )
                out_id = cur.fetchone()[0]
            self._conn.commit()
            return {**row, "id": out_id}
        except Exception:
            self.rollback()
            raise

    def _ensure_classification_history_table(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS "classification_history" (
                  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                  question_version_id UUID,
                  source_key TEXT,
                  syllabus_node_id UUID,
                  topic_code TEXT,
                  topic_title TEXT,
                  subtopic_code TEXT,
                  subtopic_title TEXT,
                  bloom_level TEXT,
                  classified_by TEXT,
                  reasoning TEXT,
                  method TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS classification_history_created_at_idx
                ON "classification_history" (created_at DESC)
                """
            )
        self._conn.commit()

    def append_classification_history(self, row: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        try:
            self._ensure_classification_history_table()
            hid = row.get("id") or str(uuid4())
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO "classification_history"
                      (id, question_version_id, source_key, syllabus_node_id,
                       topic_code, topic_title, subtopic_code, subtopic_title,
                       bloom_level, classified_by, reasoning, method)
                    VALUES
                      (%s::uuid, %s::uuid, %s, %s::uuid,
                       %s, %s, %s, %s,
                       %s, %s, %s, %s)
                    RETURNING id::text, created_at
                    """,
                    (
                        hid,
                        row.get("question_version_id"),
                        row.get("source_key"),
                        row.get("syllabus_node_id"),
                        row.get("topic_code"),
                        row.get("topic_title"),
                        row.get("subtopic_code"),
                        row.get("subtopic_title"),
                        row.get("bloom_level"),
                        row.get("classified_by"),
                        row.get("reasoning"),
                        row.get("method"),
                    ),
                )
                out_id, created_at = cur.fetchone()
            self._conn.commit()
            return {**row, "id": out_id, "created_at": created_at}
        except Exception:
            self.rollback()
            raise

    def list_classification_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_ready()
        try:
            self._ensure_classification_history_table()
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, question_version_id::text, source_key,
                           syllabus_node_id::text, topic_code, topic_title,
                           subtopic_code, subtopic_title, bloom_level,
                           classified_by, reasoning, method, created_at
                    FROM "classification_history"
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (max(limit, 200),),
                )
                rows = cur.fetchall()
            history = [
                {
                    "id": r[0],
                    "questionVersionId": r[1],
                    "sourceKey": r[2],
                    "syllabusNodeId": r[3],
                    "topicCode": r[4],
                    "topicTitle": r[5],
                    "subtopicCode": r[6],
                    "subtopicTitle": r[7],
                    "bloomLevel": r[8],
                    "classifiedBy": r[9],
                    "reasoning": r[10],
                    "method": r[11],
                    "createdAt": r[12].isoformat() if hasattr(r[12], "isoformat") else r[12],
                    "fromLive": False,
                }
                for r in rows
            ]

            # Older Kimi/manual parses wrote question_classifications but not
            # classification_history. Surface those live assignments too.
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      c.id::text,
                      v.id::text,
                      q.source_key,
                      sn.id::text,
                      sn.code,
                      sn.title,
                      sn.node_type::text,
                      parent.code,
                      parent.title,
                      v.bloom_level,
                      v.bloom_reasoning,
                      c.classified_by,
                      COALESCE(c.updated_at, c.created_at)
                    FROM "question_classifications" c
                    JOIN "question_versions" v ON v.id = c.question_version_id
                    JOIN "bank_questions" q ON q.id = v.question_id
                    JOIN "syllabus_nodes" sn ON sn.id = c.syllabus_node_id
                    LEFT JOIN "syllabus_nodes" parent ON parent.id = sn.parent_id
                    WHERE c.status = 'CLASSIFIED'::classification_status
                    ORDER BY COALESCE(c.updated_at, c.created_at) DESC
                    LIMIT %s
                    """,
                    (max(limit, 200),),
                )
                live_rows = cur.fetchall()

            seen = {
                (
                    h.get("questionVersionId"),
                    str(h.get("topicCode") or ""),
                    str(h.get("subtopicCode") or ""),
                )
                for h in history
            }
            for r in live_rows:
                (
                    cid, version_id, source_key, node_id, node_code, node_title,
                    node_type, parent_code, parent_title, bloom_level,
                    bloom_reasoning, classified_by, created_at,
                ) = r
                topic_code = topic_title = subtopic_code = subtopic_title = None
                if node_type == "SUBTOPIC":
                    subtopic_code, subtopic_title = node_code, node_title
                    topic_code, topic_title = parent_code, parent_title
                    if not topic_code and node_code and "." in str(node_code):
                        topic_code = str(node_code).split(".", 1)[0]
                else:
                    topic_code, topic_title = node_code, node_title
                key = (version_id, str(topic_code or ""), str(subtopic_code or ""))
                if key in seen:
                    continue
                seen.add(key)
                history.append(
                    {
                        "id": f"live-{cid}",
                        "questionVersionId": version_id,
                        "sourceKey": source_key,
                        "syllabusNodeId": node_id,
                        "topicCode": topic_code,
                        "topicTitle": topic_title,
                        "subtopicCode": subtopic_code,
                        "subtopicTitle": subtopic_title,
                        "bloomLevel": bloom_level,
                        "classifiedBy": classified_by,
                        "reasoning": bloom_reasoning or "Saved classification from a previous parse.",
                        "method": classified_by or "Previous parse",
                        "createdAt": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                        "fromLive": True,
                    }
                )

            history.sort(key=lambda h: h.get("createdAt") or "", reverse=True)
            return history[:limit]
        except Exception:
            self.rollback()
            return []

    def list_bank_questions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_ready()
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT q.id::text, q.source_key, q.subject, q.created_at,
                       v.id::text, v.version, v.status::text, v.content, v.options, v.approved_at,
                       v.bloom_level, v.bloom_reasoning,
                       n.id::text, n.code, n.title, n.node_type::text, n.parent_id::text,
                       parent.code, parent.title,
                       a.value, a.explanation,
                       d.batch_id::text, b.title, b.exam_year, b.paper_code
                FROM "bank_questions" q
                LEFT JOIN LATERAL (
                    SELECT * FROM "question_versions" vv
                    WHERE vv.question_id = q.id
                    ORDER BY
                      CASE WHEN vv.status = 'RETIRED' THEN 1 ELSE 0 END,
                      vv.version DESC
                    LIMIT 1
                ) v ON true
                LEFT JOIN "question_classifications" c ON c.question_version_id = v.id AND c.status = 'CLASSIFIED'
                LEFT JOIN "syllabus_nodes" n ON n.id = c.syllabus_node_id
                LEFT JOIN "syllabus_nodes" parent ON parent.id = n.parent_id
                LEFT JOIN "question_answers" a ON a.question_version_id = v.id
                LEFT JOIN "question_drafts" d ON d.id = v.source_draft_id
                LEFT JOIN "ingestion_batches" b ON b.id = d.batch_id
                ORDER BY b.created_at DESC NULLS LAST, q.updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        out = []
        for r in rows:
            current = None
            if r[4]:
                classification = None
                if r[13] or r[14]:
                    node_type = r[15]
                    code, title = r[13], r[14]
                    parent_code, parent_title = r[17], r[18]
                    topic_code = topic_title = subtopic_code = subtopic_title = None
                    if node_type == "SUBTOPIC":
                        subtopic_code, subtopic_title = code, title
                        topic_code = parent_code or (
                            str(code).split(".", 1)[0] if code and "." in str(code) else None
                        )
                        topic_title = parent_title
                    else:
                        topic_code, topic_title = code, title
                    classification = {
                        "nodeId": r[12],
                        "code": code,
                        "title": title,
                        "nodeType": node_type,
                        "parentId": r[16],
                        "topicCode": topic_code,
                        "topicTitle": topic_title,
                        "subtopicCode": subtopic_code,
                        "subtopicTitle": subtopic_title,
                    }
                answer = None
                if r[19] is not None and str(r[19]).strip() != "":
                    answer = {
                        "value": r[19],
                        "explanation": r[20],
                    }
                current = {
                    "id": r[4],
                    "version": r[5],
                    "status": r[6],
                    "content": r[7],
                    "options": r[8],
                    "approvedAt": r[9],
                    "bloomLevel": r[10],
                    "bloomReasoning": r[11],
                    "classification": classification,
                    "answer": answer,
                }
            out.append(
                {
                    "id": r[0],
                    "sourceKey": r[1],
                    "source_key": r[1],
                    "subject": r[2],
                    "createdAt": r[3],
                    "batchId": r[21],
                    "batchTitle": r[22],
                    "examYear": r[23],
                    "paperCode": r[24],
                    "currentVersion": current,
                }
            )
        return out

    def get_bank_question(self, question_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                'SELECT id::text, source_key, subject, created_at FROM "bank_questions" WHERE id = %s::uuid',
                (question_id,),
            )
            q = cur.fetchone()
            if not q:
                return None
            cur.execute(
                """
                SELECT v.id::text, v.version, v.content, v.options, v.source_draft_id::text,
                       v.status::text, v.approved_by::text, v.approved_at, v.published_at, v.created_at,
                       a.id::text, a.value, a.explanation, a.explanation_source::text,
                       v.bloom_level, v.bloom_reasoning
                FROM "question_versions" v
                LEFT JOIN "question_answers" a ON a.question_version_id = v.id
                WHERE v.question_id = %s::uuid
                ORDER BY v.version DESC
                """,
                (question_id,),
            )
            version_rows = cur.fetchall()
            
            versions = []
            for r in version_rows:
                v_id = r[0]
                answer = None
                if r[10]:
                    answer = {
                        "id": r[10],
                        "value": r[11],
                        "explanation": r[12],
                        "explanationSource": r[13],
                    }
                
                # Fetch classification info if available
                cur.execute(
                    """
                    SELECT n.id::text, n.code, n.title, n.node_type::text, n.parent_id::text,
                           parent.code, parent.title
                    FROM "question_classifications" c
                    JOIN "syllabus_nodes" n ON n.id = c.syllabus_node_id
                    LEFT JOIN "syllabus_nodes" parent ON parent.id = n.parent_id
                    WHERE c.question_version_id = %s::uuid AND c.status = 'CLASSIFIED'
                    LIMIT 1
                    """,
                    (v_id,),
                )
                node_row = cur.fetchone()
                classification = None
                if node_row:
                    node_type = node_row[3]
                    code, title = node_row[1], node_row[2]
                    parent_code, parent_title = node_row[5], node_row[6]
                    topic_code = topic_title = subtopic_code = subtopic_title = None
                    if node_type == "SUBTOPIC":
                        subtopic_code, subtopic_title = code, title
                        topic_code = parent_code or (
                            str(code).split(".", 1)[0] if code and "." in str(code) else None
                        )
                        topic_title = parent_title
                    else:
                        topic_code, topic_title = code, title
                    classification = {
                        "nodeId": node_row[0],
                        "code": code,
                        "title": title,
                        "nodeType": node_type,
                        "parentId": node_row[4],
                        "topicCode": topic_code,
                        "topicTitle": topic_title,
                        "subtopicCode": subtopic_code,
                        "subtopicTitle": subtopic_title,
                    }
                    
                versions.append(
                    {
                        "id": r[0],
                        "version": r[1],
                        "content": r[2],
                        "options": r[3],
                        "sourceDraftId": r[4],
                        "status": r[5],
                        "approvedBy": r[6],
                        "approvedAt": r[7],
                        "publishedAt": r[8],
                        "createdAt": r[9],
                        "answer": answer,
                        "bloomLevel": r[14],
                        "bloomReasoning": r[15],
                        "classification": classification,
                    }
                )
        return {
            "id": q[0],
            "sourceKey": q[1],
            "source_key": q[1],
            "subject": q[2],
            "createdAt": q[3],
            "versions": versions,
        }

    def dashboard_metrics(self) -> dict[str, int]:
        with self._conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM "question_drafts"')
            total = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT COUNT(*) FROM "question_drafts"
                WHERE status IN ('NEEDS_REVIEW'::draft_status, 'DRAFT'::draft_status, 'READY'::draft_status)
                """
            )
            needs = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT COUNT(*) FROM "ingestion_batches"
                WHERE status IN ('UPLOADED'::ingestion_batch_status, 'PROCESSING'::ingestion_batch_status)
                """
            )
            processing = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT COUNT(*) FROM "question_versions"
                WHERE status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                """
            )
            published = int(cur.fetchone()[0])
        return {
            "totalQuestions": total,
            "needsReview": needs,
            "processingImports": processing,
            "publishedQuestions": published,
        }

    def list_unclassified_versions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.id::text, v.question_id::text, q.source_key, q.subject, v.version,
                       v.content, v.options, v.status::text
                FROM "question_versions" v
                JOIN "bank_questions" q ON q.id = v.question_id
                LEFT JOIN "question_classifications" c ON c.question_version_id = v.id
                WHERE v.status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                  AND c.id IS NULL
                ORDER BY v.approved_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        return [
            {
                "versionId": r[0],
                "questionId": r[1],
                "sourceKey": r[2],
                "subject": r[3],
                "version": r[4],
                "content": r[5],
                "options": r[6],
                "status": r[7],
                "classificationStatus": "PENDING",
            }
            for r in rows
        ]

    def random_mcq_drafts(self, *, count: int) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT qd.id::text, qd.question_ref, qd.content, qd.options, ib.title
                FROM "question_drafts" qd
                JOIN "ingestion_batches" ib ON ib.id = qd.batch_id
                WHERE qd.question_type = 'MCQ'::question_type
                ORDER BY random()
                LIMIT %s
                """,
                (count,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "questionRef": r[1],
                "content": r[2],
                "options": r[3],
                "paperTitle": r[4],
            }
            for r in rows
        ]

    def random_structured_drafts(self, *, count: int) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT qd.id::text, qd.question_ref, qd.content, qd.options, ib.title
                FROM "question_drafts" qd
                JOIN "ingestion_batches" ib ON ib.id = qd.batch_id
                WHERE qd.question_type = 'STRUCTURED'::question_type
                  AND qd.requires_review = false
                  AND qd.status != 'APPROVED'::draft_status
                ORDER BY random()
                LIMIT %s
                """,
                (count,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "questionRef": r[1],
                "content": r[2],
                "options": r[3],
                "paperTitle": r[4],
            }
            for r in rows
        ]

    def random_approved_structured_drafts(self, *, count: int) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT qd.id::text, qd.question_ref, qd.content, qd.options, ib.title
                FROM "question_drafts" qd
                JOIN "ingestion_batches" ib ON ib.id = qd.batch_id
                WHERE qd.question_type = 'STRUCTURED'::question_type
                  AND qd.status = 'APPROVED'::draft_status
                ORDER BY random()
                LIMIT %s
                """,
                (count,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "questionRef": r[1],
                "content": r[2],
                "options": r[3],
                "paperTitle": r[4],
            }
            for r in rows
        ]

    def random_bank_mcqs(self, *, count: int) -> list[dict[str, Any]]:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (q.id)
                        v.id AS version_id, q.id AS question_id, q.subject,
                        v.content, v.options
                    FROM "bank_questions" q
                    JOIN "question_versions" v ON v.question_id = q.id
                    WHERE v.status IN ('APPROVED'::question_version_status, 'PUBLISHED'::question_version_status)
                    ORDER BY q.id, v.version DESC
                )
                SELECT version_id::text, question_id::text, subject, content, options
                FROM latest
                ORDER BY random()
                LIMIT %s
                """,
                (count,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "questionId": r[1],
                "paperTitle": r[2],
                "content": r[3],
                "options": r[4],
            }
            for r in rows
        ]

    def get_bank_version_answer(self, version_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT value, explanation, explanation_source::text
                FROM "question_answers"
                WHERE question_version_id = %s::uuid
                """,
                (version_id,),
            )
            r = cur.fetchone()
        if not r:
            return None
        return {"value": r[0], "explanation": r[1], "explanationSource": r[2]}

    def set_draft_topic(self, draft_id: str, *, topic_node_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                'UPDATE "question_drafts" SET topic_node_id = %s::uuid WHERE id = %s::uuid',
                (topic_node_id, draft_id),
            )
        self._conn.commit()

    def set_draft_bloom(self, draft_id: str, *, bloom_level: str, bloom_reasoning: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                'UPDATE "question_drafts" SET bloom_level = %s, bloom_reasoning = %s WHERE id = %s::uuid',
                (bloom_level, bloom_reasoning, draft_id),
            )
        self._conn.commit()

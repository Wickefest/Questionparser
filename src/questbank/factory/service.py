"""QuestionFactoryIngestService: canonical parse result → Prisma draft rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from questbank.factory.assets import (
    AssetUploadError,
    AssetUploader,
    MemoryAssetUploader,
    resolve_and_upload_assets,
)
from questbank.factory.context import IngestionContext
from questbank.factory.enums import EnumMappingError
from questbank.factory.mapping import (
    RecordValidationError,
    collect_local_assets,
    map_answer_record,
    map_question_core,
    new_id,
    rewrite_content_asset_ids,
    rewrite_options_asset_ids,
    rewrite_parts_answer_asset_ids,
)
from questbank.factory.store import (
    AnswerRow,
    AssetRow,
    DraftRow,
    FactoryStore,
    InMemoryFactoryStore,
)
from questbank.types.canonical import CompleteQuestionRecord, StructuredCompleteRecord


@dataclass
class CanonicalParseResult:
    """Wrapper for parser output — intentionally not Prisma-shaped."""

    questions: list[CompleteQuestionRecord | StructuredCompleteRecord]

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | list[Any]) -> CanonicalParseResult:
        if isinstance(data, list):
            questions = data
        else:
            questions = data.get("questions") or []
        return cls(questions=[_coerce_canonical_record(q) for q in questions])


def _coerce_canonical_record(
    q: Any,
) -> CompleteQuestionRecord | StructuredCompleteRecord:
    if isinstance(q, (CompleteQuestionRecord, StructuredCompleteRecord)):
        return q
    if not isinstance(q, dict):
        raise TypeError(f"Expected question record dict, got {type(q)!r}")
    qtype = str(q.get("question_type") or q.get("questionType") or "mcq").strip().lower()
    if qtype == "structured":
        return StructuredCompleteRecord.model_validate(q)
    return CompleteQuestionRecord.model_validate(q)


@dataclass
class IngestPersistenceReport:
    questions_parsed: int = 0
    questions_persisted: int = 0
    answers_persisted: int = 0
    answers_matched: int = 0
    unmatched_questions: list[str] = field(default_factory=list)
    unused_answers: list[str] = field(default_factory=list)
    assets_uploaded: int = 0
    questions_needing_review: list[str] = field(default_factory=list)
    duplicate_question_refs: list[str] = field(default_factory=list)
    record_errors: list[dict[str, str]] = field(default_factory=list)
    run_status: str = "COMPLETED"
    batch_status: str = "READY_FOR_REVIEW"
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "questionsParsed": self.questions_parsed,
            "questionsPersisted": self.questions_persisted,
            "answersPersisted": self.answers_persisted,
            "answersMatched": self.answers_matched,
            "unmatchedQuestions": self.unmatched_questions,
            "unusedAnswers": self.unused_answers,
            "assetsUploaded": self.assets_uploaded,
            "questionsNeedingReview": self.questions_needing_review,
            "duplicateQuestionRefs": self.duplicate_question_refs,
            "recordErrors": self.record_errors,
            "runStatus": self.run_status,
            "batchStatus": self.batch_status,
            "ok": self.ok,
        }


class QuestionFactoryIngestService:
    """Map canonical parser output into Question Factory tables."""

    def __init__(
        self,
        store: FactoryStore,
        *,
        uploader: AssetUploader | None = None,
    ) -> None:
        self.store = store
        self.uploader = uploader or MemoryAssetUploader()

    def persist(
        self,
        *,
        context: IngestionContext,
        parse_result: CanonicalParseResult | dict[str, Any] | list[Any],
        assets_dir: str | Path,
        expected_question_count: int | None = None,
        replace_existing: bool = True,
        finalize: bool = True,
    ) -> IngestPersistenceReport:
        if not isinstance(parse_result, CanonicalParseResult):
            parse_result = CanonicalParseResult.from_mapping(parse_result)

        assets_path = Path(assets_dir)
        report = IngestPersistenceReport(questions_parsed=len(parse_result.questions))

        self._assert_context(context)

        if replace_existing:
            # Idempotent retry: clear prior draft/answer/asset rows for this run.
            self.store.delete_answers_for_run(context.run_id)
            self.store.delete_drafts_for_run(context.run_id)

        refs = [q.question_ref for q in parse_result.questions]
        dupes = sorted({r for r in refs if refs.count(r) > 1})
        report.duplicate_question_refs = dupes

        seen_refs: set[str] = set()
        for record in parse_result.questions:
            if record.question_ref in seen_refs:
                report.record_errors.append(
                    {
                        "question_ref": record.question_ref,
                        "error": "duplicate question_ref in parse result",
                    }
                )
                continue
            seen_refs.add(record.question_ref)
            try:
                self._persist_one(context, record, assets_path, report)
            except (RecordValidationError, EnumMappingError, AssetUploadError) as exc:
                report.record_errors.append(
                    {"question_ref": record.question_ref, "error": str(exc)}
                )
                report.ok = False
            except Exception as exc:  # noqa: BLE001
                report.record_errors.append(
                    {"question_ref": record.question_ref, "error": f"unexpected: {exc}"}
                )
                report.ok = False

        # Deterministic answer match stats from persisted rows.
        drafts = self.store.list_drafts_for_run(context.run_id)
        answers = self.store.list_answers_for_run(context.run_id)
        draft_refs = {d.question_ref for d in drafts}
        answer_refs = {a.question_ref for a in answers}
        report.answers_matched = len(draft_refs & answer_refs)
        report.unmatched_questions = sorted(draft_refs - answer_refs)
        report.unused_answers = sorted(answer_refs - draft_refs)
        report.questions_needing_review = sorted(
            d.question_ref for d in drafts if d.requires_review or d.status == "NEEDS_REVIEW"
        )

        if (
            finalize
            and expected_question_count is not None
            and report.questions_parsed != expected_question_count
        ):
            report.ok = False
            report.record_errors.append(
                {
                    "question_ref": "*",
                    "error": (
                        f"expected_question_count={expected_question_count} "
                        f"but parsed={report.questions_parsed}"
                    ),
                }
            )

        if report.duplicate_question_refs:
            report.ok = False

        if finalize and (
            report.unmatched_questions or report.record_errors or report.questions_needing_review
        ):
            # Incomplete answer coverage or asset issues → review, not READY.
            for draft in drafts:
                if draft.question_ref in report.unmatched_questions and not draft.requires_review:
                    issues = list(draft.validation_issues or [])
                    issues.append(
                        {
                            "code": "missing_answer",
                            "message": "No AnswerRecord for this question_ref",
                        }
                    )
                    self.store.update_draft_json(
                        draft.id,
                        content=draft.content,
                        options=draft.options,
                        status="NEEDS_REVIEW",
                        requires_review=True,
                        validation_issues=issues,
                    )
                    if draft.question_ref not in report.questions_needing_review:
                        report.questions_needing_review.append(draft.question_ref)

        if not finalize:
            # Mid-parse snapshot so the UI can show drafts while Gemini continues.
            report.run_status = "PROCESSING"
            report.batch_status = "PROCESSING"
            report.ok = True
            self.store.update_run_status(context.run_id, "PROCESSING")
            self.store.update_batch_status(context.batch_id, "PROCESSING")
            return report

        if report.ok and not report.questions_needing_review and not report.record_errors:
            report.run_status = "COMPLETED"
            report.batch_status = "READY_FOR_REVIEW"
        elif report.questions_persisted > 0:
            report.run_status = "COMPLETED"
            report.batch_status = "READY_FOR_REVIEW"
        else:
            report.run_status = "FAILED"
            report.batch_status = "HAS_ERRORS"
            report.ok = False

        self.store.update_run_status(
            context.run_id,
            report.run_status,
            completed_at=datetime.now(timezone.utc),
        )
        self.store.update_batch_status(context.batch_id, report.batch_status)
        return report

    def _assert_context(self, context: IngestionContext) -> None:
        if self.store.get_batch(context.batch_id) is None:
            raise ValueError(f"IngestionBatch not found: {context.batch_id}")
        run = self.store.get_run(context.run_id)
        if run is None:
            raise ValueError(f"IngestionRun not found: {context.run_id}")
        if run.get("batch_id") and run["batch_id"] != context.batch_id:
            raise ValueError("IngestionRun.batch_id does not match IngestionContext.batch_id")
        if self.store.get_source_document(context.question_document_id) is None:
            raise ValueError(f"SourceDocument not found: {context.question_document_id}")
        if context.answer_document_id and self.store.get_source_document(context.answer_document_id) is None:
            raise ValueError(f"SourceDocument not found: {context.answer_document_id}")

    def _persist_one(
        self,
        context: IngestionContext,
        record: CompleteQuestionRecord | StructuredCompleteRecord,
        assets_path: Path,
        report: IngestPersistenceReport,
    ) -> None:
        core = map_question_core(record)
        answer_payload = map_answer_record(record)

        descriptors = collect_local_assets(record)
        issues: list[dict[str, Any]] = []
        status = core["status"]
        requires_review = core["requires_review"]

        # Never persist READY from parser; drafts start DRAFT / NEEDS_REVIEW only.
        if status == "READY":
            raise RecordValidationError("Parser must not emit READY status")

        draft = DraftRow(
            id=new_id(),
            run_id=context.run_id,
            batch_id=context.batch_id,
            question_ref=core["question_ref"],
            question_type=core["question_type"],
            content=core["content"],
            options=core["options"],
            source_page=core["source_page"],
            source_regions=core["source_regions"],
            status=status,
            requires_review=requires_review,
            validation_issues=None,
        )
        draft = self.store.upsert_draft(draft)

        asset_ids_by_key: dict[str, str] = {}
        content = draft.content
        options = draft.options
        asset_failed = False
        if descriptors:
            try:
                resolved = resolve_and_upload_assets(
                    descriptors=descriptors,
                    assets_dir=assets_path,
                    batch_id=context.batch_id,
                    run_id=context.run_id,
                    uploader=self.uploader,
                )
                for asset in resolved:
                    asset_row = self.store.create_asset(
                        AssetRow(
                            id=str(uuid4()),
                            draft_id=draft.id,
                            source_document_id=context.question_document_id,
                            role=asset.role,
                            storage_path=asset.storage_path,
                            mime_type=asset.mime_type,
                            source_page=asset.source_page,
                            bbox=asset.bbox,
                            width=asset.width,
                            height=asset.height,
                            sha256=asset.sha256,
                        )
                    )
                    asset_ids_by_key[asset.key] = asset_row.id
                    report.assets_uploaded += 1
                content = rewrite_content_asset_ids(draft.content, asset_ids_by_key=asset_ids_by_key)
                options = rewrite_options_asset_ids(draft.options, asset_ids_by_key=asset_ids_by_key)
            except AssetUploadError as exc:
                asset_failed = True
                issues.append({"code": "asset_missing", "message": str(exc)})
                requires_review = True
                status = "NEEDS_REVIEW"
                report.record_errors.append(
                    {"question_ref": record.question_ref, "error": str(exc)}
                )
                report.ok = False

        if requires_review and status == "DRAFT":
            status = "NEEDS_REVIEW"
        # Asset failure must never leave a draft looking READY.
        if asset_failed and status not in {"NEEDS_REVIEW", "REJECTED"}:
            status = "NEEDS_REVIEW"
            requires_review = True

        self.store.update_draft_json(
            draft.id,
            content=content,
            options=options,
            status=status,
            requires_review=requires_review,
            validation_issues=issues or None,
        )
        report.questions_persisted += 1

        if answer_payload is not None:
            parts_answer = answer_payload.get("parts_answer")
            if asset_ids_by_key:
                parts_answer = rewrite_parts_answer_asset_ids(
                    parts_answer, asset_ids_by_key=asset_ids_by_key
                )
            self.store.upsert_answer(
                AnswerRow(
                    id=new_id(),
                    run_id=context.run_id,
                    batch_id=context.batch_id,
                    question_ref=answer_payload["question_ref"],
                    answer_value=answer_payload.get("answer_value"),
                    parts_answer=parts_answer,
                    explanation=answer_payload.get("explanation"),
                    explanation_source=answer_payload.get("explanation_source"),
                    source_page=answer_payload.get("source_page"),
                )
            )
            report.answers_persisted += 1


def persist_canonical_parse_result(
    *,
    context: IngestionContext,
    parse_result: CanonicalParseResult | dict[str, Any] | list[Any],
    assets_dir: str | Path,
    store: FactoryStore | None = None,
    uploader: AssetUploader | None = None,
    expected_question_count: int | None = None,
    finalize: bool = True,
) -> IngestPersistenceReport:
    """Module-level entrypoint used by API / runner orchestration."""
    svc = QuestionFactoryIngestService(
        store or InMemoryFactoryStore(),
        uploader=uploader,
    )
    return svc.persist(
        context=context,
        parse_result=parse_result,
        assets_dir=assets_dir,
        expected_question_count=expected_question_count,
        finalize=finalize,
    )

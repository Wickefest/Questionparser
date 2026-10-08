"""MCQ ingest orchestrator: Gemini parse → match → validate → optional Supabase DRAFT."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from questbank.llm.gemini_client import GeminiClient
from questbank.parsers.chemistry.parse_mcq_answers_gemini import parse_mcq_answers_gemini
from questbank.parsers.chemistry.parse_mcq_gemini import parse_mcq_questions_gemini
from questbank.types.canonical import (
    AnswerPayload,
    CompleteQuestionRecord,
    ParsedAnswer,
    ParsedQuestion,
    QuestionStatus,
)
from questbank.types.ingest import (
    DocumentDescriptor,
    DocumentManifest,
    MatchingReport,
    PaperIdentity,
    StageResult,
    normalize_question_ref,
)
from questbank.validation.validate_canonical import validate_canonical_ingest

PARSER_VERSIONS = {
    "questbank": "0.2.0",
    "mcq_engine": "parse_mcq_gemini",
    "answer_engine": "parse_mcq_answers_gemini",
    "schema": "canonical/v1",
}


def run_mcq_ingest(
    *,
    question_pdf: str | Path,
    answer_pdf: str | Path,
    output_root: str | Path = "output",
    run_id: str | None = None,
    paper_identity: PaperIdentity | None = None,
    expected_question_count: int | None = None,
    title: str | None = None,
    subject: str = "Chemistry",
    backend: str = "auto",
    ocr: bool | None = None,
    dpi: int = 150,
    push_supabase: bool = False,
    persist_factory: bool = False,
    factory_store: Any | None = None,
    factory_uploader: Any | None = None,
    ingestion_context: Any | None = None,
    bank_name: str | None = None,
    client: GeminiClient | None = None,
    check_gemini: bool = True,
    require_full_answer_match: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> DocumentManifest:
    """Run full MCQ ingest without syllabus classification."""
    question_pdf = Path(question_pdf)
    answer_pdf = Path(answer_pdf)
    if not question_pdf.exists():
        raise FileNotFoundError(f"Question PDF not found: {question_pdf}")
    if not answer_pdf.exists():
        raise FileNotFoundError(f"Answer PDF not found: {answer_pdf}")

    identity = paper_identity or PaperIdentity(
        source_exam_year=2023,
        paper_code="6092/01",
        paper_type="mcq",
    )
    run_id = run_id or _default_run_id(identity, title=title)
    out_dir = Path(output_root) / run_id
    assets_dir = out_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    llm = client or GeminiClient()
    if check_gemini:
        llm.ensure_available()

    documents = [
        DocumentDescriptor(
            role="question_paper",
            path=str(question_pdf).replace("\\", "/"),
            sha256=_file_sha256(question_pdf),
            page_count=_page_count(question_pdf),
            notes=title,
        ),
        DocumentDescriptor(
            role="answer_document",
            path=str(answer_pdf).replace("\\", "/"),
            sha256=_file_sha256(answer_pdf),
            page_count=_page_count(answer_pdf),
        ),
    ]

    manifest = DocumentManifest(
        run_id=run_id,
        paper_identity=identity,
        expected_question_count=expected_question_count or 0,
        documents=documents,
        stages=[],
        parser_versions=dict(PARSER_VERSIONS),
        missing_inputs=[],
    )

    def _note(message: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(message)
        except Exception:  # noqa: BLE001
            pass

    factory_svc = None
    factory_context = ingestion_context
    factory_store_used = factory_store
    if persist_factory:
        from questbank.factory.bootstrap import create_ingestion_scaffold
        from questbank.factory.service import QuestionFactoryIngestService
        from questbank.factory.store import InMemoryFactoryStore

        factory_store_used = factory_store or InMemoryFactoryStore()
        if factory_context is None:
            scaffold = create_ingestion_scaffold(
                title=title or question_pdf.name,
                subject=subject,
                question_pdf=question_pdf,
                answer_pdf=answer_pdf,
                exam_year=identity.source_exam_year or None,
                paper_code=identity.paper_code,
                parser_version=PARSER_VERSIONS.get("questbank"),
                model_name=PARSER_VERSIONS.get("mcq_engine"),
                store=factory_store_used,
                uploader=factory_uploader,
                upload_pdfs=factory_uploader is not None,
            )
            factory_context = scaffold.context
            manifest.stages.append(
                StageResult(
                    name="factory_scaffold",
                    status="succeeded",
                    message=f"batch={factory_context.batch_id} run={factory_context.run_id}",
                    details={
                        "batchId": factory_context.batch_id,
                        "runId": factory_context.run_id,
                        "questionDocumentId": factory_context.question_document_id,
                        "answerDocumentId": factory_context.answer_document_id,
                    },
                )
            )
        factory_svc = QuestionFactoryIngestService(
            factory_store_used, uploader=factory_uploader
        )

    def persist_snapshot(
        questions_so_far: list[ParsedQuestion],
        answers_so_far: list[ParsedAnswer],
        *,
        finalize: bool,
        note: str,
    ) -> tuple[list[CompleteQuestionRecord], Any]:
        matching = match_questions_and_answers(questions_so_far, answers_so_far)
        records = join_into_complete_records(questions_so_far, answers_so_far, matching)
        if factory_svc is None or factory_context is None:
            return records, None
        try:
            report = factory_svc.persist(
                context=factory_context,
                parse_result={"questions": records},
                assets_dir=assets_dir,
                expected_question_count=expected_question_count if finalize else None,
                finalize=finalize,
            )
        except Exception as exc:  # noqa: BLE001
            _note(f"Draft snapshot failed ({exc})")
            return records, None
        _note(f"{note} · {report.questions_persisted} drafts saved")
        return records, report

    def on_questions_page(
        questions_so_far: list[ParsedQuestion], page_number: int, page_count: int
    ) -> None:
        persist_snapshot(
            questions_so_far,
            [],
            finalize=False,
            note=f"Parsing question paper · page {page_number}/{page_count}",
        )

    # --- Questions ---
    try:
        _note("Extracting layout (Docling) then parsing questions with Gemini…")
        questions = parse_mcq_questions_gemini(
            question_pdf,
            assets_dir=assets_dir,
            client=llm,
            backend=backend,
            ocr=ocr,
            dpi=dpi,
            check_gemini=False,
            use_layout_context=True,
            on_page=lambda n, total: _note(f"Parsing question paper · page {n}/{total}"),
            on_questions=on_questions_page if factory_svc is not None else None,
        )
        _write_json(
            out_dir / "questions.json",
            {
                "paperIdentity": identity.model_dump(by_alias=True),
                "subject": subject,
                "title": title,
                "questions": [q.model_dump() for q in questions],
            },
        )
        manifest.stages.append(
            StageResult(
                name="parse_questions",
                status="succeeded",
                message=f"Parsed {len(questions)} questions",
                details={"count": len(questions)},
            )
        )
    except Exception as exc:  # noqa: BLE001
        manifest.stages.append(
            StageResult(name="parse_questions", status="failed", message=str(exc))
        )
        _write_json(out_dir / "document_manifest.json", manifest)
        return manifest

    if factory_svc is not None:
        try:
            persist_snapshot(
                questions,
                [],
                finalize=False,
                note=f"Parsed {len(questions)} questions. Extracting answers",
            )
        except Exception as exc:  # noqa: BLE001
            _note(f"Draft snapshot failed ({exc}); continuing with answers")

    # --- Answers ---
    try:
        _note(f"Parsed {len(questions)} questions. Extracting answers…")
        answers = parse_mcq_answers_gemini(
            answer_pdf,
            client=llm,
            backend=backend,
            ocr=ocr,
            dpi=dpi,
            check_gemini=False,
            use_layout_context=False,
            on_page=lambda n, total: _note(f"Parsing answer scheme · page {n}/{total}"),
        )
        _write_json(
            out_dir / "answers.json",
            {"answers": [a.model_dump() for a in answers]},
        )
        manifest.stages.append(
            StageResult(
                name="parse_answers",
                status="succeeded",
                message=f"Parsed {len(answers)} answers",
                details={"count": len(answers)},
            )
        )
    except Exception as exc:  # noqa: BLE001
        manifest.stages.append(
            StageResult(name="parse_answers", status="failed", message=str(exc))
        )
        _write_json(out_dir / "document_manifest.json", manifest)
        return manifest

    # --- Matching ---
    _note("Matching questions to answers…")
    matching = match_questions_and_answers(questions, answers)
    records = join_into_complete_records(questions, answers, matching)
    match_complete = (
        not matching.unmatched_questions
        and not matching.unused_answers
        and not matching.duplicate_question_refs
        and not matching.duplicate_answer_refs
        and (
            expected_question_count is None
            or len(matching.matched) == expected_question_count
        )
    )
    match_status = "succeeded"
    match_message = (
        f"matched={len(matching.matched)} "
        f"unmatched_q={len(matching.unmatched_questions)} "
        f"unused_a={len(matching.unused_answers)}"
    )
    if require_full_answer_match and not match_complete:
        match_status = "failed"
        match_message = f"Incomplete answer match: {match_message}"

    _write_json(out_dir / "matching_report.json", matching)
    _write_json(
        out_dir / "matched_questions.json",
        {"questions": [r.model_dump() for r in records]},
    )
    manifest.stages.append(
        StageResult(
            name="matching",
            status=match_status,
            message=match_message,
            details={"matched": len(matching.matched), "complete": match_complete},
        )
    )

    # --- Validation ---
    report = validate_canonical_ingest(
        questions=questions,
        answers=answers,
        records=records,
        matching=matching,
        assets_dir=assets_dir,
        expected_question_count=expected_question_count,
    )
    for ref in report.needs_review_refs:
        for record in records:
            if record.question_ref == ref:
                record.requires_review = True
                record.status = "NEEDS_REVIEW"

    _write_json(out_dir / "validation_report.json", report)
    _write_json(
        out_dir / "matched_questions.json",
        {"questions": [r.model_dump() for r in records]},
    )
    manifest.stages.append(
        StageResult(
            name="validation",
            status="succeeded",
            message=f"ok={report.ok} issues={len(report.issues)}",
            details={
                "ok": report.ok,
                "needs_review": report.needs_review_refs,
                "issue_count": len(report.issues),
            },
        )
    )

    # --- Legacy Supabase question_banks path (do not use for Question Factory) ---
    if push_supabase:
        try:
            from questbank.integrations.supabase_store import push_canonical_bank

            push_result = push_canonical_bank(
                records=records,
                paper_identity=identity,
                bank_name=bank_name or run_id,
                source_files=[
                    str(question_pdf).replace("\\", "/"),
                    str(answer_pdf).replace("\\", "/"),
                ],
                assets_dir=assets_dir,
            )
            manifest.stages.append(
                StageResult(
                    name="supabase_push",
                    status="succeeded",
                    message=f"Pushed {push_result.get('questions', 0)} questions",
                    details=push_result,
                )
            )
        except Exception as exc:  # noqa: BLE001
            manifest.stages.append(
                StageResult(name="supabase_push", status="failed", message=str(exc))
            )

    # --- Question Factory (Prisma drafts) ---
    if persist_factory and factory_svc is not None and factory_context is not None:
        _note("Saving drafts to the question bank…")
        try:
            factory_report = factory_svc.persist(
                context=factory_context,
                parse_result={"questions": records},
                assets_dir=assets_dir,
                expected_question_count=expected_question_count,
                finalize=True,
            )
            _write_json(out_dir / "factory_ingest_report.json", factory_report.as_dict())
            _note(
                f"Saving drafts to the question bank · {factory_report.questions_persisted} drafts saved"
            )
            manifest.stages.append(
                StageResult(
                    name="factory_persist",
                    status="succeeded" if (factory_report.ok or factory_report.questions_persisted > 0) else "failed",
                    message=(
                        f"persisted={factory_report.questions_persisted} "
                        f"answers={factory_report.answers_persisted} "
                        f"assets={factory_report.assets_uploaded}"
                    ),
                    details=factory_report.as_dict(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            manifest.stages.append(
                StageResult(name="factory_persist", status="failed", message=str(exc))
            )
        else:
            _note("Classifying drafts by topic and Bloom's level…")
            try:
                from questbank.llm.kimi_classifier import classify_drafts_for_run

                classify_result = classify_drafts_for_run(factory_store, factory_context.run_id)
                manifest.stages.append(
                    StageResult(
                        name="classify_drafts",
                        status="succeeded",
                        message=(
                            f"classified={classify_result['classified']} "
                            f"failed={classify_result['failed']}"
                        ),
                        details=classify_result,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                # Never fail the whole ingest over classification — drafts are
                # already persisted and reviewable without a topic tag.
                manifest.stages.append(
                    StageResult(name="classify_drafts", status="failed", message=str(exc))
                )

    _write_json(out_dir / "document_manifest.json", manifest)
    return manifest


# Keep old name as alias for CLI migration
run_mcq_pilot_ingest = run_mcq_ingest

STRUCTURED_PARSER_VERSIONS = {
    "questbank": "0.2.0",
    "structured_engine": "parse_structured_paper",
    "answer_engine": "parse_structured_answers_gemini",
    "schema": "canonical/v1",
}


def run_structured_ingest(
    *,
    question_pdf: str | Path,
    answer_pdf: str | Path,
    output_root: str | Path = "output",
    run_id: str | None = None,
    paper_identity: PaperIdentity | None = None,
    expected_question_count: int | None = None,
    title: str | None = None,
    subject: str = "Chemistry",
    backend: str = "auto",
    ocr: bool | None = True,
    dpi: int = 150,
    persist_factory: bool = False,
    factory_store: Any | None = None,
    factory_uploader: Any | None = None,
    ingestion_context: Any | None = None,
    client: GeminiClient | None = None,
    check_gemini: bool = True,
    require_full_answer_match: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> DocumentManifest:
    """Parse structured (Paper 2) questions + mark scheme into factory drafts."""
    from questbank.parsers.chemistry.parse_structured_answers_gemini import (
        parse_structured_answers_gemini,
    )
    from questbank.parsers.chemistry.parse_structured_paper import parse_structured_paper
    from questbank.parsers.chemistry.structured_to_canonical import (
        align_part_marks,
        answer_covers_question_parts,
        iter_part_marks,
        structured_question_to_record,
    )
    from questbank.types.canonical import StructuredCompleteRecord

    question_pdf = Path(question_pdf)
    answer_pdf = Path(answer_pdf)
    if not question_pdf.exists():
        raise FileNotFoundError(f"Question PDF not found: {question_pdf}")
    if not answer_pdf.exists():
        raise FileNotFoundError(f"Answer PDF not found: {answer_pdf}")

    identity = paper_identity or PaperIdentity(
        source_exam_year=2023,
        paper_code="6092/02",
        paper_type="structured",
    )
    run_id = run_id or _default_run_id(identity, title=title)
    out_dir = Path(output_root) / run_id
    assets_dir = out_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    llm = client or GeminiClient()
    if check_gemini:
        llm.ensure_available()

    documents = [
        DocumentDescriptor(
            role="question_paper",
            path=str(question_pdf).replace("\\", "/"),
            sha256=_file_sha256(question_pdf),
            page_count=_page_count(question_pdf),
            notes=title,
        ),
        DocumentDescriptor(
            role="answer_document",
            path=str(answer_pdf).replace("\\", "/"),
            sha256=_file_sha256(answer_pdf),
            page_count=_page_count(answer_pdf),
        ),
    ]

    manifest = DocumentManifest(
        run_id=run_id,
        paper_identity=identity,
        expected_question_count=expected_question_count or 0,
        documents=documents,
        stages=[],
        parser_versions=dict(STRUCTURED_PARSER_VERSIONS),
        missing_inputs=[],
    )

    def _note(message: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(message)
        except Exception:  # noqa: BLE001
            pass

    factory_svc = None
    factory_context = ingestion_context
    factory_store_used = factory_store
    if persist_factory:
        from questbank.factory.bootstrap import create_ingestion_scaffold
        from questbank.factory.service import QuestionFactoryIngestService
        from questbank.factory.store import InMemoryFactoryStore

        factory_store_used = factory_store or InMemoryFactoryStore()
        if factory_context is None:
            scaffold = create_ingestion_scaffold(
                title=title or question_pdf.name,
                subject=subject,
                question_pdf=question_pdf,
                answer_pdf=answer_pdf,
                exam_year=identity.source_exam_year or None,
                paper_code=identity.paper_code,
                parser_version=STRUCTURED_PARSER_VERSIONS.get("questbank"),
                model_name=STRUCTURED_PARSER_VERSIONS.get("structured_engine"),
                store=factory_store_used,
                uploader=factory_uploader,
                upload_pdfs=factory_uploader is not None,
            )
            factory_context = scaffold.context
            manifest.stages.append(
                StageResult(
                    name="factory_scaffold",
                    status="succeeded",
                    message=f"batch={factory_context.batch_id} run={factory_context.run_id}",
                    details={
                        "batchId": factory_context.batch_id,
                        "runId": factory_context.run_id,
                        "questionDocumentId": factory_context.question_document_id,
                        "answerDocumentId": factory_context.answer_document_id,
                    },
                )
            )
        factory_svc = QuestionFactoryIngestService(
            factory_store_used, uploader=factory_uploader
        )

    # --- Questions ---
    try:
        from questbank.parsers.chemistry.parse_structured_questions_gemini import (
            parse_structured_questions_gemini,
            pdf_text_is_sparse,
        )

        sparse = pdf_text_is_sparse(question_pdf)
        if sparse:
            _note("Sparse PDF text — parsing structured questions with Gemini…")
            questions = parse_structured_questions_gemini(
                question_pdf,
                assets_dir=assets_dir,
                client=llm,
                dpi=dpi,
                on_progress=_note,
            )
        else:
            _note("Parsing structured question paper (layout + parts)…")
            paper = parse_structured_paper(
                question_pdf,
                backend=backend,  # type: ignore[arg-type]
                ocr=ocr,
                extract_visuals=True,
                visuals_dir=assets_dir,
                expected_count=expected_question_count,
            )
            questions = list(paper.questions or [])
            # Layout found Q numbers but empty bodies (common on light OCR) → Gemini.
            emptyish = sum(
                1
                for q in questions
                if not (q.stem or "").strip() and not (q.parts or [])
            )
            if questions and emptyish >= max(1, len(questions) // 2):
                _note(
                    f"Layout returned {emptyish}/{len(questions)} empty questions — "
                    "retrying with Gemini…"
                )
                questions = parse_structured_questions_gemini(
                    question_pdf,
                    assets_dir=assets_dir,
                    client=llm,
                    dpi=dpi,
                    on_progress=_note,
                )

        _write_json(
            out_dir / "questions.json",
            {
                "paperIdentity": identity.model_dump(by_alias=True),
                "subject": subject,
                "title": title,
                "questions": [q.model_dump(by_alias=True) for q in questions],
                "engine": "gemini" if sparse else "layout",
            },
        )
        manifest.stages.append(
            StageResult(
                name="parse_questions",
                status="succeeded",
                message=f"Parsed {len(questions)} structured questions",
                details={"count": len(questions), "sparseText": sparse},
            )
        )
    except Exception as exc:  # noqa: BLE001
        manifest.stages.append(
            StageResult(name="parse_questions", status="failed", message=str(exc))
        )
        _write_json(out_dir / "document_manifest.json", manifest)
        return manifest

    # --- Answers (Gemini mark scheme) ---
    try:
        _note(f"Parsed {len(questions)} questions. Extracting structured answers…")
        answers = parse_structured_answers_gemini(
            answer_pdf,
            client=llm,
            dpi=dpi,
            assets_dir=assets_dir,
            on_progress=_note,
        )
        _write_json(
            out_dir / "answers.json",
            {"answers": [a.model_dump(by_alias=True) for a in answers]},
        )
        manifest.stages.append(
            StageResult(
                name="parse_answers",
                status="succeeded",
                message=f"Parsed {len(answers)} structured answers",
                details={"count": len(answers)},
            )
        )
    except Exception as exc:  # noqa: BLE001
        manifest.stages.append(
            StageResult(name="parse_answers", status="failed", message=str(exc))
        )
        _write_json(out_dir / "document_manifest.json", manifest)
        return manifest

    # --- Match + join ---
    _note("Matching structured questions to answers…")
    # Adapt to MatchingReport by wrapping refs as ParsedAnswer-like refs
    class _QRef:
        def __init__(self, ref: str) -> None:
            self.question_ref = ref

    class _ARef:
        def __init__(self, ref: str) -> None:
            self.question_ref = ref

    matching = match_questions_and_answers(
        [_QRef(str(q.question_number)) for q in questions],  # type: ignore[arg-type]
        [_ARef(a.question_ref) for a in answers],  # type: ignore[arg-type]
    )
    answer_by_ref = {_norm(a.question_ref): a for a in answers}
    matched_norms = {_strip_q(r) for r in matching.matched}

    records: list[StructuredCompleteRecord] = []
    for q in questions:
        ref = str(q.question_number)
        ans = answer_by_ref.get(_norm(ref)) if _norm(ref) in matched_norms else None
        if ans is not None:
            align_part_marks(q.parts, ans)
            if q.marks_total is None:
                total = sum((p.marks or 0) for p in iter_part_marks(q.parts))
                if total:
                    q.marks_total = total
        record = structured_question_to_record(q, answer=ans)
        if ans is None and not record.requires_review:
            record.requires_review = True
            record.status = "NEEDS_REVIEW"
        elif ans is not None and not answer_covers_question_parts(q.parts, ans):
            # Mark scheme grades a part label the question parser never
            # extracted a prompt for — the visible content is silently
            # incomplete even though matching "succeeded".
            record.requires_review = True
            record.status = "NEEDS_REVIEW"
        records.append(record)

    match_complete = (
        not matching.unmatched_questions
        and not matching.unused_answers
        and not matching.duplicate_question_refs
        and not matching.duplicate_answer_refs
        and (
            expected_question_count is None
            or len(matching.matched) == expected_question_count
        )
    )
    match_status = "succeeded"
    match_message = (
        f"matched={len(matching.matched)} "
        f"unmatched_q={len(matching.unmatched_questions)} "
        f"unused_a={len(matching.unused_answers)}"
    )
    if require_full_answer_match and not match_complete:
        match_status = "failed"
        match_message = f"Incomplete answer match: {match_message}"

    _write_json(out_dir / "matching_report.json", matching)
    _write_json(
        out_dir / "matched_questions.json",
        {"questions": [r.model_dump(by_alias=True) for r in records]},
    )
    manifest.stages.append(
        StageResult(
            name="matching",
            status=match_status,
            message=match_message,
            details={"matched": len(matching.matched), "complete": match_complete},
        )
    )

    # --- Factory persist ---
    if persist_factory and factory_svc is not None and factory_context is not None:
        _note("Saving structured drafts to the question bank…")
        try:
            factory_report = factory_svc.persist(
                context=factory_context,
                parse_result={"questions": records},
                assets_dir=assets_dir,
                expected_question_count=expected_question_count,
                finalize=True,
            )
            _write_json(out_dir / "factory_ingest_report.json", factory_report.as_dict())
            _note(
                f"Saving drafts · {factory_report.questions_persisted} structured drafts saved"
            )
            manifest.stages.append(
                StageResult(
                    name="factory_persist",
                    status=(
                        "succeeded"
                        if (factory_report.ok or factory_report.questions_persisted > 0)
                        else "failed"
                    ),
                    message=(
                        f"persisted={factory_report.questions_persisted} "
                        f"answers={factory_report.answers_persisted} "
                        f"assets={factory_report.assets_uploaded}"
                    ),
                    details=factory_report.as_dict(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            manifest.stages.append(
                StageResult(name="factory_persist", status="failed", message=str(exc))
            )
        else:
            _note("Classifying drafts by topic and Bloom's level…")
            try:
                from questbank.llm.kimi_classifier import classify_drafts_for_run

                classify_result = classify_drafts_for_run(
                    factory_store_used, factory_context.run_id
                )
                manifest.stages.append(
                    StageResult(
                        name="classify_drafts",
                        status="succeeded",
                        message=(
                            f"classified={classify_result.get('classified', 0)} "
                            f"failed={classify_result.get('failed', 0)}"
                        ),
                        details=classify_result,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                manifest.stages.append(
                    StageResult(name="classify_drafts", status="failed", message=str(exc))
                )

    _write_json(out_dir / "document_manifest.json", manifest)
    return manifest


def match_questions_and_answers(
    questions: list[ParsedQuestion],
    answers: list[ParsedAnswer],
) -> MatchingReport:
    """Deterministic match by normalized question_ref. Never invents answers."""
    # MatchingReport still expects PaperIdentity — use a neutral identity for report.
    identity = PaperIdentity(source_exam_year=0, paper_code="match", paper_type="mcq")

    q_refs = [_norm(q.question_ref) for q in questions]
    a_refs = [_norm(a.question_ref) for a in answers]

    duplicate_q = sorted({r for r in q_refs if q_refs.count(r) > 1})
    duplicate_a = sorted({r for r in a_refs if a_refs.count(r) > 1})

    q_set = set(q_refs)
    a_set = set(a_refs)
    matched = sorted(q_set & a_set, key=_ref_key)
    unmatched_questions = sorted(q_set - a_set, key=_ref_key)
    unused_answers = sorted(a_set - q_set, key=_ref_key)

    return MatchingReport(
        paper_identity=identity,
        matched=[f"Q{r}" if not r.startswith("Q") else r for r in matched],
        unmatched_questions=[f"Q{r}" if not r.startswith("Q") else r for r in unmatched_questions],
        unused_answers=[f"Q{r}" if not r.startswith("Q") else r for r in unused_answers],
        duplicate_question_refs=[f"Q{r}" if not r.startswith("Q") else r for r in duplicate_q],
        duplicate_answer_refs=[f"Q{r}" if not r.startswith("Q") else r for r in duplicate_a],
    )


def join_into_complete_records(
    questions: list[ParsedQuestion],
    answers: list[ParsedAnswer],
    matching: MatchingReport,
) -> list[CompleteQuestionRecord]:
    answer_by_ref = {_norm(a.question_ref): a for a in answers}
    matched_norms = {_strip_q(r) for r in matching.matched}

    records: list[CompleteQuestionRecord] = []
    for q in questions:
        ref = _norm(q.question_ref)
        ans = answer_by_ref.get(ref) if ref in matched_norms else None
        status: QuestionStatus = "NEEDS_REVIEW" if q.requires_review else "DRAFT"
        answer_payload = None
        if ans is not None:
            # Provenance: keep explanation only when explicitly marked mark_scheme.
            explanation = ans.explanation
            if explanation and ans.explanation_source == "mark_scheme":
                explanation_source = "mark_scheme"
            else:
                explanation = None
                explanation_source = None
            answer_payload = AnswerPayload(
                value=ans.answer,
                explanation=explanation,
                explanation_source=explanation_source,
            )
        elif not q.requires_review:
            # Unmatched → needs review
            status = "NEEDS_REVIEW"

        records.append(
            CompleteQuestionRecord(
                question_ref=q.question_ref,
                content=q.content,
                options=q.options,
                answer=answer_payload,
                classification=None,
                status=status,
                source_page=q.source_page,
                source_regions=q.source_regions,
                requires_review=status == "NEEDS_REVIEW",
            )
        )
    return records


def _norm(value: str) -> str:
    normalized = normalize_question_ref(value)
    return _strip_q(normalized)


def _strip_q(ref: str) -> str:
    text = str(ref).strip()
    if text.upper().startswith("Q"):
        text = text[1:].lstrip()
    text = text.lstrip("0") or "0"
    if text.isdigit():
        return str(int(text))
    return text


def _ref_key(ref: str) -> tuple:
    if ref.isdigit():
        return (0, int(ref))
    return (1, ref)


def _default_run_id(identity: PaperIdentity, *, title: str | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = title or f"{identity.paper_code}-{identity.source_exam_year}"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in base)
    return f"{safe}-{stamp}"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_count(path: Path) -> int | None:
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001
        return None


def _write_json(path: Path, payload: Any) -> None:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(by_alias=True)
    else:
        data = payload
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


__all__ = [
    "join_into_complete_records",
    "match_questions_and_answers",
    "run_mcq_ingest",
    "run_mcq_pilot_ingest",
    "run_structured_ingest",
    "STRUCTURED_PARSER_VERSIONS",
]

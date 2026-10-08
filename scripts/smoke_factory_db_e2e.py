"""Seed a minimal live DB batch for Question Factory E2E verification."""

from __future__ import annotations

import tempfile
from pathlib import Path

from questbank.config.env import load_questbank_env
from questbank.factory.bootstrap import create_ingestion_scaffold
from questbank.factory.local_storage import LocalDiskAssetUploader
from questbank.factory.mapping import new_id
from questbank.factory.review import approve_draft, edit_draft, reject_draft
from questbank.factory.service import QuestionFactoryIngestService
from questbank.factory.store import AnswerRow, DraftRow, PostgresFactoryStore


def main() -> None:
    load_questbank_env()
    store = PostgresFactoryStore()

    with store._conn.cursor() as cur:
        cur.execute(
            'SELECT id::text FROM "syllabuses" WHERE code = %s AND year = %s',
            ("6092", 2026),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                """
                INSERT INTO "syllabuses" (subject, code, year, title)
                VALUES ('Chemistry', '6092', 2026, 'Chemistry 6092 · 2026')
                RETURNING id::text
                """
            )
            print("syllabus created", cur.fetchone()[0])
            store._conn.commit()
        else:
            print("syllabus exists", row[0])

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        q = tmp / "q.pdf"
        a = tmp / "a.pdf"
        q.write_bytes(b"%PDF-1.4 live-q")
        a.write_bytes(b"%PDF-1.4 live-a")
        uploader = LocalDiskAssetUploader()
        scaffold = create_ingestion_scaffold(
            title="Live E2E Chemistry 2023 Prelim",
            subject="Chemistry",
            question_pdf=q,
            answer_pdf=a,
            exam_year=2023,
            paper_code="Paper 1",
            store=store,
            uploader=uploader,
        )
        assets = tmp / "assets"
        assets.mkdir()
        report = QuestionFactoryIngestService(store, uploader=uploader).persist(
            context=scaffold.context,
            parse_result={
                "questions": [
                    {
                        "question_ref": "1",
                        "question_type": "mcq",
                        "content": [{"type": "text", "value": "First question about acids."}],
                        "options": {
                            "mode": "text",
                            "items": [
                                {"label": "A", "content": "acid"},
                                {"label": "B", "content": "base"},
                                {"label": "C", "content": "salt"},
                                {"label": "D", "content": "metal"},
                            ],
                        },
                        "answer": {
                            "value": "A",
                            "explanation": None,
                            "explanation_source": None,
                        },
                        "status": "NEEDS_REVIEW",
                        "source_page": 1,
                        "requires_review": True,
                    },
                    {
                        "question_ref": "2",
                        "question_type": "mcq",
                        "content": [{"type": "text", "value": "Second question about salts."}],
                        "options": {
                            "mode": "text",
                            "items": [
                                {"label": "A", "content": "NaCl"},
                                {"label": "B", "content": "H2O"},
                                {"label": "C", "content": "O2"},
                                {"label": "D", "content": "N2"},
                            ],
                        },
                        "answer": {
                            "value": "A",
                            "explanation": None,
                            "explanation_source": None,
                        },
                        "status": "DRAFT",
                        "source_page": 1,
                        "requires_review": False,
                    },
                ]
            },
            assets_dir=assets,
        )
        print("persist ok", report.questions_persisted, report.answers_persisted)

        drafts = sorted(
            store.list_drafts_for_run(scaffold.context.run_id),
            key=lambda d: d.question_ref,
        )
        d1, d2 = drafts
        edit_draft(
            store,
            draft_id=d1.id,
            content=[{"type": "text", "value": "Edited first question about acids."}],
            options=d1.options,
        )
        reject_draft(store, draft_id=d2.id, reason="Answer mismatch")
        assert store.get_draft(d2.id).status == "REJECTED"

        d3 = store.upsert_draft(
            DraftRow(
                id=new_id(),
                run_id=scaffold.context.run_id,
                batch_id=scaffold.context.batch_id,
                question_ref="3",
                question_type="MCQ",
                content=[{"type": "text", "value": "Approve me"}],
                options={
                    "mode": "text",
                    "items": [
                        {"label": "A", "content": "yes"},
                        {"label": "B", "content": "no"},
                        {"label": "C", "content": "x"},
                        {"label": "D", "content": "y"},
                    ],
                },
                source_page=1,
                source_regions=None,
                status="READY",
                requires_review=False,
                validation_issues=None,
            )
        )
        store.upsert_answer(
            AnswerRow(
                id=new_id(),
                run_id=scaffold.context.run_id,
                batch_id=scaffold.context.batch_id,
                question_ref="3",
                answer_value="A",
                explanation=None,
                explanation_source=None,
                source_page=1,
            )
        )
        approved = approve_draft(store, draft_id=d3.id)
        detail = store.get_batch_detail(scaffold.context.batch_id)
        pdf_path = (
            Path("output")
            / "factory-storage"
            / "exam-sources"
            / scaffold.context.batch_id
            / "question-paper.pdf"
        )
        print("approved", approved["sourceKey"], "v", approved["version"])
        print("batch_status", detail["status"], "questions", detail["questionCount"])
        print("rejected_still_present", store.get_draft(d2.id).status)
        print("pdf_stored", pdf_path.exists())
        print("BATCH_ID", scaffold.context.batch_id)
        print("QUESTION_ID", approved["questionId"])

    store.close()


if __name__ == "__main__":
    main()

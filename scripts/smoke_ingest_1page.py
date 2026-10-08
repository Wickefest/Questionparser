"""Cheap 1-page MCQ ingest smoke (questions + answers + match)."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from questbank.config.env import load_questbank_env
from questbank.ingest.runner import join_into_complete_records, match_questions_and_answers
from questbank.llm.gemini_client import GeminiClient
from questbank.parsers.chemistry.parse_mcq_answers_gemini import parse_mcq_answers_gemini
from questbank.parsers.chemistry.parse_mcq_gemini import parse_mcq_questions_gemini
from questbank.validation.validate_canonical import validate_canonical_ingest


def main() -> int:
    load_dotenv(Path(".env"))
    load_questbank_env()

    question_pdf = Path("P1 BBSS - 3E Chemistry (6092) EYE P1 2023_.pdf")
    answer_pdf = Path("MS1 BBSS - 3E Chemistry (6092) EYE P1 2023 Answers.pdf")
    out = Path("output") / "smoke-1page"
    assets = out / "assets"
    out.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    if not question_pdf.exists() or not answer_pdf.exists():
        print("Missing sample PDFs")
        return 2

    client = GeminiClient()
    client.ensure_available()

    print("Parsing question page 1…")
    questions = parse_mcq_questions_gemini(
        question_pdf,
        assets_dir=assets,
        client=client,
        backend="pymupdf",
        dpi=120,
        max_pages=1,
        check_gemini=False,
    )
    print(f"  questions={len(questions)}")

    print("Parsing answer page 1…")
    answers = parse_mcq_answers_gemini(
        answer_pdf,
        client=client,
        backend="pymupdf",
        dpi=120,
        max_pages=1,
        check_gemini=False,
    )
    print(f"  answers={len(answers)}")

    matching = match_questions_and_answers(questions, answers)
    records = join_into_complete_records(questions, answers, matching)
    report = validate_canonical_ingest(
        questions=questions,
        answers=answers,
        records=records,
        matching=matching,
        assets_dir=assets,
        expected_question_count=None,
    )

    summary = {
        "questions": len(questions),
        "answers": len(answers),
        "matched": len(matching.matched),
        "unmatched_questions": matching.unmatched_questions,
        "unused_answers": matching.unused_answers,
        "validation_ok": report.ok,
        "needs_review": report.needs_review_refs,
        "issue_count": len(report.issues),
        "sample_refs": [q.question_ref for q in questions[:5]],
    }
    (out / "smoke_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (out / "matched_questions.json").write_text(
        json.dumps({"questions": [r.model_dump() for r in records]}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0 if questions and answers else 1


if __name__ == "__main__":
    raise SystemExit(main())

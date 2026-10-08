"""Smoke MCQ ingest for a specific question-paper page (default: 16)."""

from __future__ import annotations

import json
import sys
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

    page = int(sys.argv[1]) if len(sys.argv) > 1 else 16

    question_pdf = Path("P1 BBSS - 3E Chemistry (6092) EYE P1 2023_.pdf")
    answer_pdf = Path("MS1 BBSS - 3E Chemistry (6092) EYE P1 2023 Answers.pdf")
    out = Path("output") / f"smoke-page-{page}"
    assets = out / "assets"
    out.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    if not question_pdf.exists() or not answer_pdf.exists():
        print("Missing sample PDFs")
        return 2

    client = GeminiClient()
    client.ensure_available()
    print(f"model={client.model}")

    print(f"Parsing question page {page} (Docling layout + Gemini compare)...")
    questions = parse_mcq_questions_gemini(
        question_pdf,
        assets_dir=assets,
        client=client,
        backend="auto",
        dpi=120,
        pages=[page],
        check_gemini=False,
        use_layout_context=True,
    )
    print(f"  questions={len(questions)} refs={[q.question_ref for q in questions]}")

    # Answer key is short; parse all pages so late questions can match.
    print("Parsing answer key (all pages, no Docling)…")
    answers = parse_mcq_answers_gemini(
        answer_pdf,
        client=client,
        backend="pymupdf",
        dpi=120,
        check_gemini=False,
        use_layout_context=False,
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
        "page": page,
        "model": client.model,
        "layout_context": False,
        "questions": len(questions),
        "answers": len(answers),
        "matched": len(matching.matched),
        "unmatched_questions": matching.unmatched_questions,
        "validation_ok": report.ok,
        "needs_review": report.needs_review_refs,
        "issue_count": len(report.issues),
        "question_refs": [q.question_ref for q in questions],
        "content_types": {
            q.question_ref: [getattr(b, "type", "?") for b in q.content] for q in questions
        },
    }
    (out / "smoke_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (out / "matched_questions.json").write_text(
        json.dumps({"questions": [r.model_dump() for r in records]}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0 if questions else 1


if __name__ == "__main__":
    raise SystemExit(main())

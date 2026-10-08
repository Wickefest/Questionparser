"""Split each combined fullmarks MCQ PDF (questions + trailing answer key page)
into a questions-only PDF and an answers-only PDF, so the existing MCQ ingest
pipeline (which expects separate question_pdf/answer_pdf files) can run
unmodified.

The fullmarks format is consistent: N question pages, then one page whose
first line is literally "Answers" followed by "Q1 A" / "Q2 D" ... lines.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "output" / "drive-mcq-fullmarks"
OUT_DIR = ROOT / "output" / "drive-mcq-fullmarks" / "split"

ANSWERS_HEADING_RE = re.compile(r"^\s*Answers\s*$", re.I | re.M)


def find_answers_page(doc: pymupdf.Document) -> int | None:
    """Return 0-indexed page number where the 'Answers' section starts."""
    for i in range(doc.page_count):
        text = doc[i].get_text("text") or ""
        if ANSWERS_HEADING_RE.search(text):
            return i
    return None


def title_from_slug(slug: str) -> str:
    # Pure-Chemistry-MCQs-Metals-Test-1-0 -> "Metals Test 1.0"
    text = re.sub(r"^Pure-Chemistry-MCQs-", "", slug)
    text = re.sub(r"-(\d+)-(\d+)$", r" \1.\2", text)
    text = text.replace("-", " ")
    return text.strip()


def main() -> int:
    manifest_path = SRC_DIR / "raw_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    problems = []
    for entry in data["files"]:
        src_path = Path(entry["path"])
        slug = entry["slug"]
        with pymupdf.open(src_path) as doc:
            ans_page = find_answers_page(doc)
            if ans_page is None or ans_page == 0:
                problems.append({"slug": slug, "reason": f"no answers page found (ans_page={ans_page})"})
                continue

            q_doc = pymupdf.open()
            q_doc.insert_pdf(doc, from_page=0, to_page=ans_page - 1)
            q_path = OUT_DIR / f"{slug}-questions.pdf"
            q_doc.save(q_path)
            q_doc.close()

            a_doc = pymupdf.open()
            a_doc.insert_pdf(doc, from_page=ans_page, to_page=doc.page_count - 1)
            a_path = OUT_DIR / f"{slug}-answers.pdf"
            a_doc.save(a_path)
            a_doc.close()

            results.append(
                {
                    "slug": slug,
                    "title": title_from_slug(slug),
                    "questionPath": str(q_path),
                    "answerPath": str(a_path),
                    "questionPages": ans_page,
                    "answerPages": doc.page_count - ans_page,
                }
            )

    out_manifest = SRC_DIR / "split_manifest.json"
    out_manifest.write_text(
        json.dumps({"items": results, "problems": problems}, indent=2),
        encoding="utf-8",
    )
    print(f"Split {len(results)} files OK, {len(problems)} problems")
    for p in problems:
        print(f"  PROBLEM: {p}")
    print(f"Manifest: {out_manifest}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())

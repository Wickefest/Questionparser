"""Simulated test: draw random MCQs from the DB (draft or bank) for practice."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from questbank.api.factory_routes import get_store
from questbank.factory.local_storage import resolve_storage_bytes
from questbank.pdf.raster_to_svg import raster_bytes_to_svg

router = APIRouter(prefix="/api/simulated-test", tags=["simulated-test"])

SourceKind = Literal["draft", "bank", "structured", "structuredApproved"]


def _svg_asset_urls_for_payload(content: Any, options: Any) -> dict[str, str]:
    """Point Simulated Test visuals at on-the-fly SVG conversion endpoints."""
    from questbank.api.factory_routes import _collect_asset_ids

    return {
        asset_id: f"/api/simulated-test/assets/{asset_id}/svg"
        for asset_id in _collect_asset_ids(content, options)
    }


@router.get("/questions")
def get_random_questions(
    source: SourceKind = Query("draft"),
    count: int = Query(10, ge=1, le=50),
    assets: Literal["svg", "png"] = Query(
        "svg",
        description="Serve question images as SVG (PyMuPDF) or original PNG for Simulated Test only.",
    ),
) -> dict[str, Any]:
    store = get_store()
    url_builder = _svg_asset_urls_for_payload if assets == "svg" else None
    if url_builder is None:
        from questbank.api.factory_routes import _asset_urls_for_payload

        url_builder = lambda content, options: _asset_urls_for_payload(store, content, options)

    if source == "draft":
        rows = store.random_mcq_drafts(count=count)
        questions = [
            {
                "id": r["id"],
                "source": "draft",
                "questionRef": r["questionRef"],
                "paperTitle": r["paperTitle"],
                "content": r["content"],
                "options": r["options"],
                "assetUrls": url_builder(r["content"], r["options"]),
                "assetFormat": assets,
            }
            for r in rows
        ]
    elif source in ("structured", "structuredApproved"):
        rows = (
            store.random_approved_structured_drafts(count=count)
            if source == "structuredApproved"
            else store.random_structured_drafts(count=count)
        )
        questions = [
            {
                "id": r["id"],
                # Tagged "structured" either way: grading (get_draft + get_answer by
                # run_id/question_ref) and the frontend's free-response UI don't
                # depend on approval status, only on the source pool queried above.
                "source": "structured",
                "questionRef": r["questionRef"],
                "paperTitle": r["paperTitle"],
                "content": r["content"],
                "options": r["options"],
                "assetUrls": url_builder(r["content"], r["options"]),
                "assetFormat": assets,
            }
            for r in rows
        ]
    else:
        rows = store.random_bank_mcqs(count=count)
        questions = [
            {
                "id": r["id"],
                "source": "bank",
                "questionRef": None,
                "paperTitle": r["paperTitle"],
                "content": r["content"],
                "options": r["options"],
                "assetUrls": url_builder(r["content"], r["options"]),
                "assetFormat": assets,
            }
            for r in rows
        ]
    return {"questions": questions}


@router.get("/assets/{asset_id}/svg")
def simulated_test_asset_svg(asset_id: str) -> Response:
    """Convert a stored PNG/JPEG asset to SVG for Simulated Test only."""
    store = get_store()
    asset = store.get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    resolved = resolve_storage_bytes(asset.storage_path)
    if not resolved:
        raise HTTPException(status_code=404, detail="Asset file unavailable")
    data, mime = resolved
    hint = asset.storage_path or "asset.png"
    try:
        svg = raster_bytes_to_svg(data, filename_hint=hint)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"SVG conversion failed: {exc}",
        ) from exc
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )


class SubmitAnswerRequest(BaseModel):
    id: str
    source: SourceKind
    submittedAnswer: str | None = None


@router.post("/answer")
def submit_answer(body: SubmitAnswerRequest) -> dict[str, Any]:
    store = get_store()
    if body.source == "structured":
        # Free-response: nothing to auto-grade against a single letter.
        # "Reveal" the mark scheme's per-part rows so the learner self-checks.
        draft = store.get_draft(body.id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Question not found")
        answer = store.get_answer(draft.run_id, draft.question_ref)
        if answer is None:
            raise HTTPException(status_code=404, detail="No answer on record for this question")
        return {
            "correct": None,
            "correctAnswer": None,
            "partsAnswer": answer.parts_answer or [],
            "explanation": None,
            "explanationSource": None,
        }
    if body.source == "draft":
        draft = store.get_draft(body.id)
        if draft is None:
            raise HTTPException(status_code=404, detail="Question not found")
        answer = store.get_answer(draft.run_id, draft.question_ref)
        if answer is None:
            raise HTTPException(status_code=404, detail="No answer on record for this question")
        correct_value = answer.answer_value
        explanation = answer.explanation
        explanation_source = answer.explanation_source
    else:
        ans = store.get_bank_version_answer(body.id)
        if ans is None:
            raise HTTPException(status_code=404, detail="No answer on record for this question")
        correct_value = ans["value"]
        explanation = ans["explanation"]
        explanation_source = ans["explanationSource"]

    return {
        "correct": body.submittedAnswer == correct_value,
        "correctAnswer": correct_value,
        "explanation": explanation,
        "explanationSource": explanation_source,
    }


__all__ = ["router"]

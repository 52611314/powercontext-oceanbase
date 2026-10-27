from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import answer as answer_service
from .. import retrieval
from ..export import answer_markdown, hits_markdown

router = APIRouter(prefix="/api", tags=["answer"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    doc_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None


def _text_response(content: str, filename: str) -> PlainTextResponse:
    return PlainTextResponse(
        content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/ask")
def ask(payload: AskRequest):
    return answer_service.ask(
        question=payload.question,
        doc_ids=payload.doc_ids,
        tags=payload.tags,
        year_from=payload.year_from,
        year_to=payload.year_to,
    )


@router.get("/answers")
def list_answers(limit: int = 20):
    return {"items": answer_service.list_answers(limit)}


@router.get("/answers/{answer_id}")
def get_answer(answer_id: str):
    record = answer_service.get_answer(answer_id)
    if not record:
        raise HTTPException(
            status_code=404, detail={"code": "ANSWER_NOT_FOUND", "message": "回答不存在"}
        )
    return record


@router.delete("/answers/{answer_id}")
def delete_answer(answer_id: str):
    if not answer_service.delete_answer(answer_id):
        raise HTTPException(
            status_code=404, detail={"code": "ANSWER_NOT_FOUND", "message": "回答不存在"}
        )
    return {"deleted": True, "id": answer_id}


@router.get("/answers/{answer_id}/export.md")
def export_answer(answer_id: str):
    record = answer_service.get_answer(answer_id)
    if not record:
        raise HTTPException(
            status_code=404, detail={"code": "ANSWER_NOT_FOUND", "message": "回答不存在"}
        )
    content = answer_markdown(record["question"], record["bullets"])
    return _text_response(content, f"citeseek-answer-{answer_id[:8]}.md")


@router.get("/searches/{search_id}/export.md")
def export_search(search_id: str):
    record, hits = retrieval.get_search_hits(search_id)
    if not record:
        raise HTTPException(
            status_code=404, detail={"code": "SEARCH_NOT_FOUND", "message": "检索记录不存在"}
        )
    content = hits_markdown(record["query"], hits)
    return _text_response(content, f"citeseek-search-{search_id[:8]}.md")

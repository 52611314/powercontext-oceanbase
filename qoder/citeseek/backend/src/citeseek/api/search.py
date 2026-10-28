from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import retrieval

router = APIRouter(prefix="/api", tags=["retrieval"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["hybrid", "fts", "vector"] = "hybrid"
    doc_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    candidate_k: int = Field(default=50, ge=5, le=200)


@router.post("/search")
def search(payload: SearchRequest):
    try:
        return retrieval.search(
            query=payload.query,
            mode=payload.mode,
            doc_ids=payload.doc_ids,
            tags=payload.tags,
            year_from=payload.year_from,
            year_to=payload.year_to,
            top_k=payload.top_k,
            candidate_k=payload.candidate_k,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "BAD_QUERY", "message": str(exc)}
        ) from exc


@router.get("/searches")
def list_searches(limit: int = 20):
    return {"items": retrieval.list_searches(limit)}


@router.get("/debug/retrieval/{search_id}")
def debug_retrieval(search_id: str):
    result = retrieval.debug_search(search_id)
    if not result:
        raise HTTPException(
            status_code=404, detail={"code": "SEARCH_NOT_FOUND", "message": "检索记录不存在"}
        )
    return result

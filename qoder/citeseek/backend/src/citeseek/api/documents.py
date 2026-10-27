from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Annotated

import pymysql
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import store
from ..config import settings, upload_path
from ..export import bibtex_for
from ..ingest import submit_index

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_TYPES = {".pdf": "pdf", ".md": "markdown", ".markdown": "markdown", ".txt": "text"}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    authors: str | None = Field(default=None, max_length=1000)
    year: int | None = None
    tags: str | None = None
    read_status: str | None = None


@router.post("")
async def upload_documents(
    files: Annotated[list[UploadFile], File()],
    tags: Annotated[str, Form()] = "",
):
    if not files:
        raise _error(400, "NO_FILES", "请选择文件")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    limit = settings.max_upload_mb * 1024 * 1024
    results: list[dict] = []

    for upload in files:
        suffix = Path(upload.filename or "").suffix.lower()
        if suffix not in ALLOWED_TYPES:
            results.append(
                {"file_name": upload.filename, "error": f"不支持的格式：{suffix or '未知'}"}
            )
            continue

        temp_path = settings.upload_dir / f".{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        too_large = False
        try:
            with temp_path.open("wb") as out:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        too_large = True
                        break
                    digest.update(chunk)
                    out.write(chunk)
        finally:
            await upload.close()

        if too_large:
            temp_path.unlink(missing_ok=True)
            results.append(
                {"file_name": upload.filename, "error": f"文件超过 {settings.max_upload_mb}MB"}
            )
            continue

        sha256 = digest.hexdigest()
        existing = store.find_by_sha(sha256)
        if existing:
            temp_path.unlink(missing_ok=True)
            results.append({"file_name": upload.filename, "duplicate": True, "document": existing})
            continue

        final_path = settings.upload_dir / f"{sha256}{suffix}"
        temp_path.replace(final_path)
        try:
            document = store.create_document(
                title=Path(upload.filename or "未命名").stem,
                source_type=ALLOWED_TYPES[suffix],
                sha256=sha256,
                file_name=upload.filename or final_path.name,
                file_path=final_path.name,
                file_size=size,
                tags=tags,
            )
        except pymysql.err.IntegrityError:
            final_path.unlink(missing_ok=True)
            existing = store.find_by_sha(sha256)
            results.append({"file_name": upload.filename, "duplicate": True, "document": existing})
            continue

        submit_index(document["id"])
        results.append({"file_name": upload.filename, "duplicate": False, "document": document})

    return {"items": results}


@router.get("")
def list_documents(
    q: str = "",
    status: str = "",
    read_status: str = "",
    tag: str = "",
    limit: int = 100,
    offset: int = 0,
):
    limit = max(1, min(limit, 200))
    items, total = store.list_documents(
        q=q.strip(),
        status=status.strip(),
        read_status=read_status.strip(),
        tag=tag.strip(),
        limit=limit,
        offset=max(0, offset),
    )
    return {"items": items, "total": total}


@router.get("/{doc_id}")
def get_document(doc_id: str):
    document = store.get_document(doc_id)
    if not document:
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    return document


@router.patch("/{doc_id}")
def patch_document(doc_id: str, payload: DocumentPatch):
    if not store.get_document(doc_id):
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    if payload.read_status and payload.read_status not in {"unread", "reading", "read"}:
        raise _error(400, "BAD_READ_STATUS", "read_status 只能是 unread/reading/read")
    updated = store.update_document(doc_id, payload.model_dump(exclude_none=True))
    return updated


@router.delete("/{doc_id}")
def delete_document(doc_id: str):
    document = store.delete_document(doc_id)
    if not document:
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    upload_path(document["file_path"]).unlink(missing_ok=True)
    return {"deleted": True, "id": doc_id}


@router.post("/{doc_id}/reindex")
def reindex(doc_id: str):
    document = store.get_document(doc_id)
    if not document:
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    if document["status"] == "indexing":
        raise _error(409, "INDEX_IN_PROGRESS", "该文献正在索引中")
    store.update_document(doc_id, {"status": "pending", "progress": 0, "error": ""})
    submit_index(doc_id)
    return {"id": doc_id, "status": "pending"}


@router.get("/{doc_id}/status")
def index_status(doc_id: str):
    document = store.get_document(doc_id)
    if not document:
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    return {
        "id": doc_id,
        "status": document["status"],
        "progress": document["progress"],
        "page_count": document["page_count"],
        "chunk_count": document.get("chunk_count", 0),
        "error": document["error"],
    }


@router.get("/{doc_id}/pages/{page}")
def get_page(doc_id: str, page: int):
    if not store.get_document(doc_id):
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    row = store.get_page(doc_id, page)
    if not row:
        raise _error(404, "PAGE_NOT_FOUND", "该页不存在")
    return row


@router.get("/{doc_id}/file")
def get_file(doc_id: str):
    document = store.get_document(doc_id)
    if not document:
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    path = upload_path(document["file_path"])
    if not path.exists():
        raise _error(404, "FILE_MISSING", "原始文件不存在")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
    return FileResponse(
        path,
        media_type=media_type,
        filename=document["file_name"],
        content_disposition_type="inline",
    )


@router.get("/{doc_id}/citation.bib")
def get_bibtex(doc_id: str):
    document = store.get_document(doc_id)
    if not document:
        raise _error(404, "DOC_NOT_FOUND", "文献不存在")
    return PlainTextResponse(
        bibtex_for(document),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="citeseek-{doc_id[:8]}.bib"'},
    )

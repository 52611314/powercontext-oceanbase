from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from . import store
from .config import settings, upload_path
from .embedding import embedder, vector_literal
from .text import (
    chunk_text,
    looks_like_heading,
    merge_wrapped_lines,
    normalize,
    sections_in_page,
    split_sections,
)

logger = logging.getLogger(__name__)


class DocumentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedPage:
    page: int
    section: str
    text: str


def _guess_title(pages: list[ParsedPage], meta_title: str, file_name: str) -> str:
    meta_title = (meta_title or "").strip()
    if len(meta_title) >= 4:
        return meta_title[:500]
    if pages:
        for line in pages[0].text.split("\n")[:8]:
            line = line.strip()
            if 4 <= len(line) <= 120 and not re.match(
                r"^(abstract|摘要|关键词|关键字)", line, re.I
            ):
                return line
    return Path(file_name).stem[:500]


def _guess_year(text: str) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", text[:3000])
    return int(match.group(0)) if match else None


def parse_pdf(path: Path) -> tuple[list[ParsedPage], dict]:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise DocumentError("PDF_ENCRYPTED", "PDF 已加密，无法解析")
    pages: list[ParsedPage] = []
    current_section = ""
    for number, raw_page in enumerate(reader.pages, start=1):
        try:
            raw_text = raw_page.extract_text() or ""
        except Exception:  # noqa: BLE001 - malformed pages should not abort the whole book
            raw_text = ""
        text = merge_wrapped_lines(normalize(raw_text))
        if not text:
            pages.append(ParsedPage(number, current_section, ""))
            continue
        first_lines = [line for line in text.split("\n")[:4] if line.strip()]
        for line in first_lines:
            if looks_like_heading(line):
                current_section = line.strip()[:250]
                break
        pages.append(ParsedPage(number, current_section, text))

    total_chars = sum(len(page.text) for page in pages)
    if total_chars < 50:
        raise DocumentError("PDF_EMPTY_TEXT", "未提取到文本，可能是扫描版 PDF")

    meta = reader.metadata or {}
    meta_info = {
        "title": _guess_title(pages, str(meta.get("/Title") or ""), path.name),
        "authors": str(meta.get("/Author") or "").strip()[:1000],
        "year": _guess_year(pages[0].text if pages else ""),
    }
    return pages, meta_info


def parse_markdown(path: Path) -> tuple[list[ParsedPage], dict]:
    raw = normalize(path.read_text(encoding="utf-8", errors="ignore"))
    pages: list[ParsedPage] = []
    section = ""
    buffer: list[str] = []
    for line in raw.split("\n"):
        heading = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
        if heading:
            if any(item.strip() for item in buffer):
                pages.append(ParsedPage(len(pages) + 1, section, "\n".join(buffer).strip()))
            section = heading.group(1)[:250]
            buffer = []
        else:
            buffer.append(line)
    if any(item.strip() for item in buffer):
        pages.append(ParsedPage(len(pages) + 1, section, "\n".join(buffer).strip()))
    if not pages:
        raise DocumentError("TEXT_EMPTY", "文件内容为空")

    first_text = pages[0].text
    meta_info = {
        "title": _guess_title(pages, "", path.name),
        "authors": "",
        "year": _guess_year(first_text + pages[0].section),
    }
    return pages, meta_info


def parse_plain(path: Path) -> tuple[list[ParsedPage], dict]:
    raw = normalize(path.read_text(encoding="utf-8", errors="ignore"))
    if len(raw) < 20:
        raise DocumentError("TEXT_EMPTY", "文件内容为空")
    blocks = [block.strip() for block in re.split(r"\n{2,}", raw) if block.strip()]
    pages: list[ParsedPage] = []
    buffer: list[str] = []
    size = 0
    for block in blocks:
        buffer.append(block)
        size += len(block)
        if size >= 2000:
            pages.append(ParsedPage(len(pages) + 1, "", "\n\n".join(buffer)))
            buffer, size = [], 0
    if buffer:
        pages.append(ParsedPage(len(pages) + 1, "", "\n\n".join(buffer)))
    meta_info = {
        "title": _guess_title(pages, "", path.name),
        "authors": "",
        "year": _guess_year(pages[0].text if pages else ""),
    }
    return pages, meta_info


def parse_document(path: Path) -> tuple[list[ParsedPage], dict]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in {".md", ".markdown"}:
        return parse_markdown(path)
    if suffix == ".txt":
        return parse_plain(path)
    raise DocumentError("UNSUPPORTED_TYPE", f"不支持的格式：{suffix}")


def index_document(doc_id: str) -> None:
    doc = store.get_document(doc_id)
    if not doc:
        return
    try:
        store.update_document(doc_id, {"status": "indexing", "progress": 1, "error": ""})
        path = upload_path(doc["file_path"])
        if not path.exists():
            raise DocumentError("FILE_MISSING", "原始文件不存在，请重新上传")

        pages, meta_info = parse_document(path)
        if not any(page.text.strip() for page in pages):
            raise DocumentError("PDF_EMPTY_TEXT", "未提取到文本，可能是扫描版 PDF")

        store.clear_index(doc_id)
        store.insert_pages(doc_id, [(p.page, p.section, p.text) for p in pages])
        store.update_document(
            doc_id,
            {
                "page_count": len(pages),
                "progress": 35,
                "authors": meta_info.get("authors", "") or doc.get("authors", ""),
                "year": meta_info.get("year") or doc.get("year"),
                "title": meta_info.get("title") or doc.get("title"),
            },
        )

        chunk_rows: list[tuple] = []
        chunk_texts: list[str] = []
        for parsed_page in pages:
            if not parsed_page.text.strip():
                continue
            sections = sections_in_page(parsed_page.text)
            chunk_number = 0
            for segment_start, segment_title, segment_text in split_sections(
                parsed_page.text, sections, parsed_page.section
            ):
                pieces = chunk_text(
                    segment_text,
                    target=settings.chunk_size,
                    overlap=settings.chunk_overlap,
                )
                for chunk, start, end in pieces:
                    chunk_id = f"{doc_id}-{parsed_page.page}-{chunk_number}"
                    chunk_rows.append(
                        (
                            chunk_id,
                            doc_id,
                            parsed_page.page,
                            segment_title,
                            chunk_number,
                            chunk,
                            segment_start + start,
                            segment_start + end,
                        )
                    )
                    chunk_texts.append(chunk)
                    chunk_number += 1
        if not chunk_rows:
            raise DocumentError("PDF_EMPTY_TEXT", "未生成有效文本块")
        store.update_document(doc_id, {"progress": 45})

        vectors = embedder.encode(chunk_texts)
        rows: list[tuple] = []
        for meta_row, vector in zip(chunk_rows, vectors, strict=True):
            rows.append((*meta_row, vector_literal(vector)))

        batch_size = 32
        for start in range(0, len(rows), batch_size):
            store.insert_chunks(rows[start : start + batch_size])
            done = min(start + batch_size, len(rows))
            store.update_document(doc_id, {"progress": 45 + int(50 * done / len(rows))})

        store.update_document(doc_id, {"status": "ready", "progress": 100, "error": ""})
        logger.info("indexed doc=%s pages=%s chunks=%s", doc_id, len(pages), len(rows))
    except DocumentError as exc:
        logger.warning("index failed doc=%s code=%s msg=%s", doc_id, exc.code, exc.message)
        store.update_document(
            doc_id, {"status": "failed", "error": exc.message[:500], "progress": 0}
        )
    except Exception as exc:  # noqa: BLE001 - background job must always settle
        logger.exception("index crashed doc=%s", doc_id)
        store.update_document(doc_id, {"status": "failed", "error": str(exc)[:500], "progress": 0})


_executor = ThreadPoolExecutor(max_workers=settings.index_workers, thread_name_prefix="index")
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def submit_index(doc_id: str) -> bool:
    with _inflight_lock:
        if doc_id in _inflight:
            return False
        _inflight.add(doc_id)

    def run() -> None:
        try:
            index_document(doc_id)
        finally:
            with _inflight_lock:
                _inflight.discard(doc_id)

    _executor.submit(run)
    return True

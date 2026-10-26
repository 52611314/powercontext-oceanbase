# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Page-level captured Sources for offline PDF documents and page evidence search."""

from __future__ import annotations

import hashlib
import io
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote

from pydantic import BaseModel, model_validator
from pypdf import PdfReader

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

_IDENTITY_PREFIX = "pdf:"
_MAX_KEYWORD_TERMS = 12
_MAX_TERM_QUERIES = 4
_RRF_K = 60
_MIN_SCORED_HITS = 24

_TERM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "geographies": ("geography", "geographic", "geographical"),
    "geography": ("geographic", "geographical"),
    "legal": ("litigation", "proceedings"),
    "battles": ("litigation", "proceedings", "claims"),
    "capex": ("capital", "expenditures"),
    "d": ("depreciation",),
    "a": ("amortization",),
}


class PDFDocumentInput(BaseModel):
    """One complete offline PDF document to parse into physical pages."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a PDF document with its extractable text."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured PDF page that can be cited as page-level evidence."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_page_identity(self) -> PDFPageSource:
        expected = pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        if self.source_id != expected:
            raise ValueError("source_id must match the PDF page identity")  # noqa: TRY003
        if self.name != self.source_id:
            raise ValueError("name must equal source_id")  # noqa: TRY003
        return self


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one complete PDF, preserving page order."""

    document_id = _validate_document_id(value.document_id)
    if not value.content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    document_sha256 = hashlib.sha256(value.content).hexdigest()
    texts = _extract_page_texts(value.content)
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=document_sha256,
            page_number=page_number,
            text=text,
        )
        for page_number, text in enumerate(texts, start=1)
    )


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable, delimiter-safe identity of one physical PDF page."""

    document_id = _validate_document_id(document_id)
    if not _is_sha256(document_sha256):
        raise ValueError("document_sha256 must be a lowercase SHA-256 hexadecimal digest")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = f"{_IDENTITY_PREFIX}{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"PDF page identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


class PDFPageSourceAdapter:
    """Resolve and read page-level captured PDF Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one PDF page as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return at most ``limit`` distinct page citations for one natural-language question."""

    from powercontext.builtin.runtime import SearchMemoryRequest

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        return ()
    if not isinstance(question, str) or not question.strip():
        return ()
    request_limit = max(limit * 2, 40)
    scored_hits = max(limit, _MIN_SCORED_HITS)
    scores: dict[tuple[str, int], float] = {}
    citations_by_page: dict[tuple[str, int], Any] = {}
    order: dict[tuple[str, int], int] = {}
    seen_entries: set[tuple[str, str]] = set()
    for query in _search_queries(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=request_limit))
        await _score_page_hits(
            memory,
            page.hits[:scored_hits],
            scores,
            citations_by_page,
            order,
            seen_entries,
        )
    ranked = sorted(scores, key=lambda page_key: (-scores[page_key], order[page_key]))
    return tuple(citations_by_page[page_key] for page_key in ranked[:limit])


async def _score_page_hits(
    memory: Any,
    hits: Any,
    scores: dict[tuple[str, int], float],
    citations_by_page: dict[tuple[str, int], Any],
    order: dict[tuple[str, int], int],
    seen_entries: set[tuple[str, str]],
) -> None:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest

    for rank, hit in enumerate(hits):
        entry_key = (hit.entry_id, hit.entry_version_id)
        if entry_key in seen_entries:
            continue
        seen_entries.add(entry_key)
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        page_sources = tuple(source for source in entry.sources if source.source_type == PDF_PAGE_SOURCE_NAME)
        if len(page_sources) != 1:
            continue
        page_key = _page_key(page_sources[0].source_id)
        if page_key is None:
            continue
        scores[page_key] = scores.get(page_key, 0.0) + 1.0 / (_RRF_K + rank)
        citations_by_page.setdefault(page_key, citation)
        order.setdefault(page_key, len(order))


def _search_queries(question: str) -> tuple[str, ...]:
    terms = _distinct_terms(question)
    queries = [question.strip()]
    core_terms = tuple(term for term in terms if len(term) >= 2)[:_MAX_KEYWORD_TERMS]
    if core_terms:
        queries.append(" ".join(core_terms))
    queries.extend(_term_queries(terms))
    return _dedupe_queries(queries)


def _distinct_terms(question: str) -> tuple[str, ...]:
    from powercontext.builtin.artifacts.search import analyze_fts_query

    try:
        analyzed = analyze_fts_query(question)
    except (TypeError, ValueError):
        return ()
    return tuple(dict.fromkeys(analyzed.split()))


def _term_queries(terms: tuple[str, ...]) -> tuple[str, ...]:
    candidates: list[str] = []
    expanded_terms = tuple(term for term in terms if term in _TERM_EXPANSIONS)
    other_terms = tuple(term for term in terms if term not in _TERM_EXPANSIONS and len(term) >= 5)
    for term in (*expanded_terms, *other_terms):
        variants = (*_TERM_EXPANSIONS.get(term, ()), *_stem_forms(term))
        if len(term) >= 2:
            candidates.append(term)
        candidates.extend(variant for variant in variants if variant != term)
        if len(candidates) >= _MAX_TERM_QUERIES:
            break
    return tuple(candidates[:_MAX_TERM_QUERIES])


def _stem_forms(term: str) -> tuple[str, ...]:
    if term.endswith("ies") and len(term) > 4:
        return (term[:-3] + "y",)
    if term.endswith("es") and len(term) > 4:
        return (term[:-2],)
    if term.endswith("s") and len(term) > 3:
        return (term[:-1],)
    return ()


def _dedupe_queries(queries: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        normalized = query.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    return tuple(unique)


def _extract_page_texts(content: bytes) -> tuple[str, ...]:
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise ValueError("PDF content cannot be parsed") from error  # noqa: TRY003
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF requires a user password")  # noqa: TRY003
    try:
        pages = tuple(reader.pages)
        return tuple(page.extract_text() or "" for page in pages)
    except Exception as error:
        raise ValueError("PDF content cannot be parsed") from error  # noqa: TRY003


def _validate_document_id(document_id: object) -> str:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    return document_id


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _page_key(source_id: str) -> tuple[str, int] | None:
    if not source_id.startswith(_IDENTITY_PREFIX):
        return None
    rest = source_id[len(_IDENTITY_PREFIX) :]
    try:
        document_id, _digest, page_number = rest.split(":", 2)
        page = int(page_number)
    except ValueError:
        return None
    return unquote(document_id), page


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]

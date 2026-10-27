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
_HASHED_IDENTITY_PREFIX = "pdfh:"


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
        document_key = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        identity = f"{_HASHED_IDENTITY_PREFIX}{document_key}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("PDF page number exceeds the supported identity length")  # noqa: TRY003
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
    from powercontext.sources._pdf_retrieval import rank_pages

    # Search and enumerate only the caller's active scoped Memory. Enumeration is
    # essential: a global top-k lexical pool can omit an entire document's pages.
    hits = await memory.search(SearchMemoryRequest(query=question, mode="fts", limit=max(80, limit * 4)))
    records = (await memory.list()).entries
    candidates = []
    for record in records:
        sources = tuple(source for source in record.entry.sources if source.source_type == PDF_PAGE_SOURCE_NAME)
        if record.state != "active" or len(sources) != 1:
            continue
        identity = _page_identity(sources[0].source_id)
        if identity is not None:
            candidates.append((identity, record.entry.text, record.citation))
    return rank_pages(candidates, question, limit, hits.hits)


def _extract_page_texts(content: bytes) -> tuple[str, ...]:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a user password")  # noqa: TRY003, TRY301
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


def _page_identity(source_id: str) -> tuple[str, str, int] | None:
    hashed = source_id.startswith(_HASHED_IDENTITY_PREFIX)
    prefix = _HASHED_IDENTITY_PREFIX if hashed else _IDENTITY_PREFIX
    if not source_id.startswith(prefix):
        return None
    try:
        document_id, digest, page_number = source_id[len(prefix) :].split(":", 2)
        page = int(page_number)
    except ValueError:
        return None
    if not document_id or not _is_sha256(digest) or page < 1:
        return None
    if hashed:
        if not _is_sha256(document_id):
            return None
        # Keep the hashed namespace distinct from arbitrary user document IDs.
        # The original document ID remains in the captured Source/projection.
        return f"sha256:{document_id}", f"h:{digest}", page
    return unquote(document_id), digest, page


def _page_key(source_id: str) -> tuple[str, int] | None:
    identity = _page_identity(source_id)
    return None if identity is None else (identity[0], identity[2])


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

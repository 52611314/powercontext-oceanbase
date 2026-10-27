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

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from powercontext.artifacts.models import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceDefinitionRegistry
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    pdf_page_source_id,
    search_pdf_pages,
)


def _record(document: str, page: int, text: str, *, digest: str = "a" * 64, state: str = "active"):
    citation = MemoryCitation(
        memory_ref=ArtifactRef(family="memory", artifact_id="pages", revision=1),
        entry_id=f"{document}-{digest[:2]}-{page}",
        entry_version_id=f"{document}-{digest[:2]}-{page}-v1",
    )
    return SimpleNamespace(
        citation=citation,
        state=state,
        entry=SimpleNamespace(
            text=f"{document}\n{text}",
            sources=(SimpleNamespace(source_type="pdf-page", source_id=pdf_page_source_id(document, digest, page)),),
        ),
    )


class _Memory:
    def __init__(self, records, hits=()):
        self.records = tuple(records)
        self.hits = hits
        self.calls = 0

    async def search(self, request):
        self.calls += 1
        return SimpleNamespace(hits=self.hits)

    async def list(self):
        return SimpleNamespace(entries=self.records)


def test_search_recovers_document_absent_from_fts_pool():
    other = [_record("OTHER_2024_10K", i, "Debt bank loan borrowing") for i in range(1, 101)]
    target = _record("ACME_2024_10K", 51, "Consolidated Balance Sheets\nDebt 950\nCurrent liabilities 120")
    memory = _Memory([*other, target], tuple(record.citation for record in other[:40]))
    found = asyncio.run(search_pdf_pages(memory, "What was Acme's debt in FY2024?", limit=1))
    assert found == (target.citation,)
    assert memory.calls == 1


def test_opaque_document_ids_do_not_force_an_arbitrary_document():
    first = _record("opaque-one", 1, "Lithium patents and inventions")
    second = _record("opaque-two", 1, "Lithium patents and licensing")
    found = asyncio.run(search_pdf_pages(_Memory([first, second]), "Lithium patents", limit=2))
    assert {c.entry_id for c in found} == {first.citation.entry_id, second.citation.entry_id}


def test_report_year_is_selected_before_ranking():
    old = _record("ACME_2023_10K", 2, "Consolidated Balance Sheet\nDebt 100")
    new = _record("ACME_2024_10K", 2, "Consolidated Balance Sheet\nDebt 300")
    found = asyncio.run(search_pdf_pages(_Memory([new, old]), "Acme debt FY2023", limit=2))
    assert found == (old.citation,)


def test_company_matching_respects_word_boundaries():
    acme = _record("ACME_2023_10K", 2, "Consolidated Statement of Income\nOperating margin revenue")
    unrelated = _record("3M_2023_10K", 2, "Consolidated Statement of Income\nOperating margin revenue")
    found = asyncio.run(search_pdf_pages(_Memory([acme, unrelated]), "Acme 2023 margin", limit=2))
    assert found == (acme.citation,)


def test_distinct_financial_statements_survive_fusion():
    income = _record("ACME_2024_10K", 2, "Consolidated Statement of Income\nRevenue 100\nOperating income 20")
    cash = _record("ACME_2024_10K", 3, "Consolidated Statement of Cash Flows\nDepreciation 5\nAmortization 2")
    notes = [_record("ACME_2024_10K", i, "Operating income cash flow revenue profit summary") for i in range(10, 80)]
    found = asyncio.run(
        search_pdf_pages(
            _Memory([*notes, income, cash]), "Acme EBITDA operating income depreciation amortization", limit=10
        )
    )
    assert income.citation in found
    assert cash.citation in found


def test_inactive_non_pdf_and_duplicate_entries_are_excluded():
    active = _record("one", 1, "Lithium patents")
    inactive = _record("one", 2, "Lithium patents", state="inactive")
    non_pdf = _record("one", 3, "Lithium patents")
    non_pdf.entry.sources[0].source_type = "csv-row"
    found = asyncio.run(search_pdf_pages(_Memory([inactive, non_pdf, active, active]), "Lithium patents"))
    assert found == (active.citation,)


def test_page_order_and_separate_scopes_do_not_leak_state():
    first = _record("one", 1, "Lithium patents")
    second = _record("two", 1, "Lithium patents")
    a = asyncio.run(search_pdf_pages(_Memory([first, second]), "Lithium patents"))
    b = asyncio.run(search_pdf_pages(_Memory([second, first]), "Lithium patents"))
    assert a == b
    assert asyncio.run(search_pdf_pages(_Memory([second]), "Lithium patents")) == (second.citation,)


@pytest.mark.parametrize(
    "question,limit", [("", 20), ("  ", 20), (None, 20), ("debt", 0), ("debt", -1), ("debt", True), ("debt", 1.5)]
)
def test_invalid_query_or_limit_does_not_read_memory(question, limit):
    memory = _Memory([])
    assert asyncio.run(search_pdf_pages(memory, question, limit)) == ()
    assert memory.calls == 0


@pytest.mark.parametrize("document", ["报表" * 50, "a" * 1024, "company:/%\n" * 50])
def test_long_document_id_is_bounded_and_round_trips(document):
    writer, output = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    page = parse_pdf_document(PDFDocumentInput(document_id=document, content=output.getvalue()))[0]

    async def check():
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        source = await registry.resolve(page)
        assert source.name.startswith("pdfh:")
        assert len(source.name) <= 256
        assert await registry.read(source) == page
        assert (await registry.resolve(page)).name == source.name
        assert pdf_page_source_id(document + "other", page.document_sha256, 1) != source.name
        assert pdf_page_source_id(document, "b" * 64, 1) != source.name

    asyncio.run(check())


def test_short_identity_keeps_existing_format():
    assert pdf_page_source_id("a:b", "a" * 64, 1) == f"pdf:a%3Ab:{'a' * 64}:1"


def test_revisions_do_not_return_duplicate_physical_pages():
    old = _record("one", 1, "Lithium patents expired", digest="a" * 64)
    new = _record("one", 1, "Lithium patents renewed", digest="b" * 64)
    found = asyncio.run(search_pdf_pages(_Memory([old, new]), "Lithium patents renewed", limit=10))
    assert found == (new.citation,)


def test_hashed_document_namespace_cannot_alias_a_short_document():
    import hashlib

    document = "报表" * 50
    encoded = _record(document, 1, "Lithium patents")
    ordinary = _record("sha256:" + hashlib.sha256(document.encode()).hexdigest(), 1, "Lithium patents")
    found = asyncio.run(search_pdf_pages(_Memory([encoded, ordinary]), "Lithium patents", limit=10))
    assert len(found) == 2


def test_long_id_revisions_persist_and_old_citations_survive_restart(tmp_path):
    from powercontext.builtin.artifacts.memory import MemoryEntryInput
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import (
        BuiltinConfig,
        GetMemoryEntryRequest,
        SubmitSourceObservation,
        open_builtin_runtime,
    )
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.sources import manifest_for_definition, project_source_for_transport
    from powercontext.sources.pdf_pages import PDFPageInput

    class Pipeline:
        async def extract(self, request, /):
            return tuple(
                MemoryEntryInput(
                    kind="source-evidence",
                    text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                    sources=(source,),
                )
                for source in request.sources
            )

    async def check():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        async with open_builtin_runtime(config, candidate_pipeline=Pipeline()) as runtime:
            scope = await runtime.scopes.create(ScopeDraft(title="PDF", summary="PDF", idempotency_key="pdf"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = []
            for digest, text in (("a" * 64, "Lithium patents expired"), ("b" * 64, "Lithium patents renewed")):
                source = await registry.resolve(
                    PDFPageInput(document_id="报表" * 50, document_sha256=digest, page_number=1, text=text)
                )
                observation = project_source_for_transport(registry, source)
                request = SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                await runtime.ingestion.submit(request)
                await runtime.ingestion.submit(request)
                await memory.flush()
                citations.append((await search_pdf_pages(memory, text, limit=1))[0])
            assert len((await memory.list()).entries) == 2
            assert citations[0] != citations[1]
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            old = await memory.get(GetMemoryEntryRequest(citation=citations[0]))
            assert old.entry.text.endswith("Lithium patents expired")
            assert (await search_pdf_pages(memory, "Lithium patents renewed", limit=1))[
                0
            ].entry_version_id == citations[1].entry_version_id

    asyncio.run(check())

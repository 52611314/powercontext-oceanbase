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

"""Offline, document-aware ranking of real PDF page Memory records.

All vocabulary below describes general financial concepts. No document names,
question identifiers, evidence locations or benchmark assets are consulted.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import MemoryCitation

_STOP = frozenset([
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "from",
    "by",
    "as",
    "with",
    "and",
    "or",
    "but",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "will",
    "which",
    "what",
    "when",
    "where",
    "why",
    "how",
    "who",
    "its",
    "it",
    "this",
    "that",
    "these",
    "those",
    "their",
    "they",
    "we",
    "our",
    "you",
    "your",
    "not",
    "no",
    "if",
    "then",
    "than",
    "into",
    "between",
    "based",
    "data",
    "company",
    "business",
    "report",
    "reported",
    "annual",
    "fiscal",
    "fy",
    "year",
    "years",
    "period",
    "please",
    "state",
    "explain",
    "answer",
    "question",
    "analyst",
    "investment",
    "perspective",
    "use",
    "using",
    "used",
    "shown",
    "details",
    "assume",
    "assuming",
    "define",
    "give",
    "response",
    "calculate",
    "quantity",
    "directly",
    "outlined",
    "explicitly",
    "important",
    "materially",
    "ongoing",
    "improving",
    "improved",
    "increased",
    "declined",
    "change",
    "changed",
    "primarily",
    "useful",
    "metric",
    "profile",
    "only",
    "any",
    "about",
    "like",
    "less",
    "much",
    "many",
    "financial",
])

_ALIASES = {
    "cashflow": "cash flow",
    "cashflows": "cash flow",
    "capex": "capital expenditure",
    "geographies": "geographic",
    "geographical": "geographic",
    "geography": "geographic",
    "operates": "operating",
    "operate": "operating",
    "operations": "operating",
    "countries": "country",
    "revenues": "revenue",
    "sales": "revenue",
    "earnings": "income",
    "profits": "profit",
    "liabilities": "liability",
}

_EXPANSIONS = {
    "geographic": "country international region market segment",
    "legal": "litigation proceedings lawsuit contingency",
    "battle": "litigation proceedings lawsuit",
    "debt": "borrowing loan maturity liability",
    "acquisition": "acquired purchase combination goodwill",
    "growth": "revenue organic segment volume",
    "segment": "division business segment",
    "restructuring": "restructuring charge cost expense",
    "dividend": "dividend shareholder cash",
    "repurchase": "repurchase share treasury stock",
}

# These are statement types, not evidence pages. Different statements can supply
# different operands of one financial question, so retain separate rank channels.
_STATEMENTS = {
    "income": (
        "revenue net income operating profit cost expense",
        (
            "statements of income",
            "statements of operations",
            "statement of income",
            "statement of operations",
            "income statements",
            "consolidated income statement",
        ),
    ),
    "balance": (
        "asset liability equity current cash inventory receivable",
        ("balance sheets", "balance sheet", "statements of financial position"),
    ),
    "cash": (
        "operating investing financing cash depreciation amortization capital expenditure",
        ("statements of cash flows", "statement of cash flows", "cash flow statements"),
    ),
}


def _tokens(value: str) -> tuple[str, ...]:
    words = re.findall(r"[^\W_]+", value.casefold())
    expanded = (part for word in words for part in _ALIASES.get(word, word).split())
    return tuple(_stem(word) for word in expanded if not word.isdecimal())


def _stem(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def _company(document: str) -> str:
    name = re.split(r"(?:19|20)\d{2}|10[kq]|8k|annual|earnings", document, maxsplit=1, flags=re.I)[0]
    return re.sub(r"[\W_]", "", name.casefold())


def _documents(question: str, documents: set[str]) -> set[str]:
    matched = {document for document in documents if _mentions_company(question, _company(document))}
    if not matched:
        return documents
    years = set(re.findall(r"(?:19|20)\d{2}", question))
    dated = {document for document in matched if years.intersection(re.findall(r"(?:19|20)\d{2}", document))}
    # Multiple reports may be needed for a comparison. An uncertain date never
    # eliminates a document, and opaque document IDs never force an arbitrary one.
    return dated or matched


def _mentions_company(question: str, company: str) -> bool:
    if len(company) < 3 and not any(character.isdigit() for character in company):
        return False
    words = re.findall(r"[^\W_]+", question.casefold())
    for start in range(len(words)):
        combined = ""
        for word in words[start:]:
            combined += word
            if combined == company:
                return True
            if len(combined) >= len(company):
                break
    return False


def _channels(question: str, documents: set[str]) -> list[tuple[tuple[str, ...], tuple[str, ...], float]]:
    names = {token for document in documents for token in _tokens(document)}
    stop = {_stem(word) for word in _STOP}
    tokens = tuple(
        dict.fromkeys(
            token
            for token in _tokens(question)
            if len(token) > 1 and token not in stop and token not in names and not re.fullmatch(r"fy\d+", token)
        )
    )
    channels = [(tokens, (), 1.0)] if tokens else []
    expansion = tuple(dict.fromkeys(token for term in tokens for token in _tokens(_EXPANSIONS.get(term, ""))))
    if expansion:
        channels.append((expansion, (), 1.0))
    terms = set(tokens)
    statements: set[str] = set()
    if terms.intersection({"margin", "income", "profit", "ebitda", "revenue"}):
        statements.add("income")
    if terms.intersection({"debt", "balance", "asset", "liability", "liquidity", "quick", "current", "equity"}):
        statements.add("balance")
    if terms.intersection({"cash", "depreciation", "amortization", "ebitda", "capex", "expenditure"}):
        statements.add("cash")
    if "intensive" in terms or "turnover" in terms:
        statements.update(("income", "balance", "cash"))
    if "conversion" in terms and "cash" in terms:
        statements.add("income")
    if "ratio" in terms and not statements:
        statements.update(("income", "balance"))
    for name in sorted(statements):
        text, phrases = _STATEMENTS[name]
        channels.append((_tokens(text), phrases, 1.0))
    if "ebitda" in terms or "d&a" in question.casefold():
        channels.append((_tokens("depreciation amortization"), (), 1.0))
    return channels


def _lexical_scores(texts: list[str], channels: list[tuple[tuple[str, ...], tuple[str, ...], float]]) -> list[float]:
    counts = [Counter(_tokens(text)) for text in texts]
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / max(len(lengths), 1) or 1.0
    frequencies: Counter[str] = Counter()
    for count in counts:
        frequencies.update(count.keys())
    scores = [0.0] * len(texts)
    normalized = [" ".join(text.casefold().split()) for text in texts]
    for terms, phrases, weight in channels:
        raw = []
        for i, count in enumerate(counts):
            score = 0.0
            for term in set(terms):
                tf = count[term]
                idf = math.log(1 + (len(texts) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.6 + 0.4 * lengths[i] / average))
            # A statement heading is stronger evidence than incidental references
            # to a statement buried in a note. The stored page text is untouched.
            for phrase in phrases:
                if phrase in normalized[i]:
                    score += 6.0 if phrase in normalized[i][:800] else 2.0
            raw.append(score)
        ranked = sorted(range(len(texts)), key=lambda i: (-raw[i], i))
        for rank, i in enumerate(ranked):
            if raw[i] > 0:
                scores[i] += weight / (5 + rank)
    return scores


def rank_pages(
    candidates: list[tuple[tuple[str, str, int], str, Any]],
    question: str,
    limit: int,
    hits: Any,
) -> tuple[MemoryCitation, ...]:
    """Rank active page records without discarding pages before document selection."""
    if not candidates:
        return ()
    selected_documents = _documents(question, {identity[0] for identity, _, _ in candidates})
    pages = sorted(
        (item for item in candidates if item[0][0] in selected_documents),
        key=lambda item: (*item[0], item[2].entry_id, item[2].entry_version_id),
    )
    texts = [text.partition("\n")[2] if text.startswith(identity[0] + "\n") else text for identity, text, _ in pages]
    scores = _lexical_scores(texts, _channels(question, selected_documents))
    search_ranks = {hit.entry_version_id: rank for rank, hit in enumerate(hits)}
    for i, (_, _, citation) in enumerate(pages):
        if citation.entry_version_id in search_ranks:
            scores[i] += 0.15 / (20 + search_ranks[citation.entry_version_id])
    order = sorted(range(len(pages)), key=lambda i: (-scores[i], pages[i][0]))
    seen: set[tuple[str, bool, int]] = set()
    result = []
    for i in order:
        identity, _, citation = pages[i]
        page_key = (identity[0], identity[1].startswith("h:"), identity[2])
        if scores[i] <= 0 or page_key in seen:
            continue
        seen.add(page_key)
        result.append(citation)
        if len(result) == limit:
            break
    return tuple(result)

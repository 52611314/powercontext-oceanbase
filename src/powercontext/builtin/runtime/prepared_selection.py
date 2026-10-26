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

"""Rank literal sentence spans of recalled Memory entries for evidence selection.

The ranking is deliberately lexical and offline: it never calls a model or reads any
annotation. It reports half-open character spans into the exact entry text so the Builder can
deliver verbatim evidence with exact citations under a byte budget.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

_SENTENCE_END = re.compile(r"[.!?][\"')\]]*(?=\s+(?![a-z])|\s*$)")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_MIN_PREFIX_MATCH = 3
_MAX_PREFIX_DELTA = 3
_LENGTH_PIVOT = 24
_LENGTH_PENALTY = 0.0015
_DIGIT_BOOST = 1.5
_TITLE_WEIGHT = 0.6
_TITLE_TOKEN_LIMIT = 20
_PAIR_WEIGHT = 0.97
_TRIPLE_WEIGHT = 0.94
_ADJACENCY_WEIGHT = 0.6
_ADJACENCY_DISTANCE = 2
_STOPWORDS = frozenset([
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
])


@dataclass(frozen=True)
class EvidenceCandidate:
    """One ranked literal span of a recalled entry."""

    document_index: int
    start: int
    end: int
    score: float


@dataclass(frozen=True)
class _Sentence:
    start: int
    end: int
    tokens: tuple[str, ...]
    score: float = 0.0


def rank_evidence_candidates(documents: Sequence[str], query: str) -> tuple[EvidenceCandidate, ...]:
    """Rank non-overlapping-friendly spans of every document against the query.

    Single sentences are ranked by an IDF-weighted lexical score that tolerates plural and
    inflection variants. Adjacent positive sentences also produce merged candidates, so two
    related sentences can be delivered behind one citation instead of two. When nothing
    matches, the first sentence of each document is still offered so a selection run never
    invents evidence or returns an unexplained empty result.
    """

    query_terms = _query_terms(query)
    sentences_by_document = tuple(_sentences(text) for text in documents)
    every_sentence = tuple(sentence for sentences in sentences_by_document for sentence in sentences)
    if not every_sentence:
        return ()

    idf = _inverse_document_frequencies(query_terms, every_sentence)
    scored_documents = tuple(
        tuple(
            _scored_sentence(sentences, index, sentence, query_terms, idf) for index, sentence in enumerate(sentences)
        )
        for sentences in sentences_by_document
    )
    candidates = _sentence_candidates(scored_documents)
    if not candidates:
        candidates = _fallback_candidates(sentences_by_document)
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (-candidate.score, candidate.document_index, candidate.start, candidate.end),
        )
    )


def _sentence_candidates(scored_documents: Sequence[Sequence[_Sentence]]) -> list[EvidenceCandidate]:
    """Offer every positive sentence plus merged neighbours that can share one citation."""

    candidates: list[EvidenceCandidate] = []
    for document_index, sentences in enumerate(scored_documents):
        for sentence in sentences:
            if sentence.score > 0.0:
                candidates.append(EvidenceCandidate(document_index, sentence.start, sentence.end, sentence.score))
        for index in range(len(sentences) - 1):
            first, second = sentences[index], sentences[index + 1]
            if first.score > 0.0 and second.score > 0.0:
                candidates.append(
                    EvidenceCandidate(
                        document_index, first.start, second.end, (first.score + second.score) * _PAIR_WEIGHT
                    )
                )
        for index in range(len(sentences) - 2):
            first, second, third = sentences[index], sentences[index + 1], sentences[index + 2]
            if first.score > 0.0 and second.score > 0.0 and third.score > 0.0:
                candidates.append(
                    EvidenceCandidate(
                        document_index,
                        first.start,
                        third.end,
                        (first.score + second.score + third.score) * _TRIPLE_WEIGHT,
                    )
                )
    return candidates


def _fallback_candidates(sentences_by_document: Sequence[Sequence[_Sentence]]) -> list[EvidenceCandidate]:
    """Offer each document's first sentence when the query matches nothing at all."""

    return [
        EvidenceCandidate(document_index, sentences[0].start, sentences[0].end, 1.0)
        for document_index, sentences in enumerate(sentences_by_document)
        if sentences
    ]


def _sentences(text: str) -> tuple[_Sentence, ...]:
    sentences = []
    for start, end in sentence_spans(text):
        sentence = _Sentence(start=start, end=end, tokens=_tokens(text[start:end]))
        if not sentence.tokens:
            continue
        sentences.append(sentence)
    return tuple(sentences)


def sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        if end <= cursor:
            continue
        left = text[cursor:end]
        start = cursor + len(left) - len(left.lstrip())
        if start < end:
            spans.append((start, end))
        cursor = end
    tail = text[cursor:]
    start = cursor + len(tail) - len(tail.lstrip())
    end = cursor + len(tail.rstrip())
    if start < end:
        spans.append((start, end))
    return tuple(spans)


def _tokens(text: str) -> tuple[str, ...]:
    tokens = []
    for token in _WORD.findall(text.lower()):
        if token in _STOPWORDS:
            continue
        if not token.isdigit() and len(token) < 2:
            continue
        tokens.append(token)
    return tuple(tokens)


def _query_terms(query: str) -> tuple[str, ...]:
    seen: set[str] = set()
    terms = []
    for token in _tokens(query):
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return tuple(terms)


def _inverse_document_frequencies(query_terms: Sequence[str], sentences: Sequence[_Sentence]) -> dict[str, float]:
    total = len(sentences)
    frequencies: dict[str, float] = {}
    for term in query_terms:
        matching = sum(1 for sentence in sentences if _matches_term(sentence.tokens, term))
        frequencies[term] = math.log(1.0 + (total - matching + 0.5) / (matching + 0.5))
    return frequencies


def _matches_term(tokens: Sequence[str], term: str) -> bool:
    return any(_token_matches(term, token) for token in tokens)


def _token_matches(term: str, token: str) -> bool:
    if term == token:
        return True
    if term.isdigit() or token.isdigit():
        return False
    if not (term.isascii() and token.isascii()):
        return False
    shorter, longer = (term, token) if len(term) <= len(token) else (token, term)
    return (
        len(shorter) >= _MIN_PREFIX_MATCH
        and longer.startswith(shorter)
        and len(longer) - len(shorter) <= _MAX_PREFIX_DELTA
    )


def _scored_sentence(
    sentences: Sequence[_Sentence],
    index: int,
    sentence: _Sentence,
    query_terms: Sequence[str],
    idf: dict[str, float],
) -> _Sentence:
    if not query_terms:
        return sentence
    positions: dict[str, list[int]] = {}
    for position, token in enumerate(sentence.tokens):
        for term in query_terms:
            if _token_matches(term, token):
                positions.setdefault(term, []).append(position)
    if not positions:
        return sentence
    has_title = index == 0 and len(sentences) > 1 and len(sentence.tokens) <= _TITLE_TOKEN_LIMIT
    matched: dict[str, float] = {}
    for term, hits in positions.items():
        weight = idf[term] * (1.0 + math.log(len(hits)))
        if term.isdigit():
            weight *= _DIGIT_BOOST
        matched[term] = weight
    total_idf = sum(idf.values())
    covered_idf = sum(idf[term] for term in matched)
    coverage = covered_idf / total_idf if total_idf > 0.0 else 0.0
    adjacency = _adjacency_bonus(query_terms, positions, idf)
    score = (sum(matched.values()) + adjacency) * (0.5 + 0.5 * coverage)
    score /= 1.0 + _LENGTH_PENALTY * max(0, len(sentence.tokens) - _LENGTH_PIVOT)
    if has_title:
        score *= _TITLE_WEIGHT
    return _Sentence(start=sentence.start, end=sentence.end, tokens=sentence.tokens, score=score)


def _adjacency_bonus(
    query_terms: Sequence[str],
    positions: dict[str, list[int]],
    idf: dict[str, float],
) -> float:
    """Reward query bigrams that survive as a nearby phrase in the sentence.

    Claims usually preserve a two-word phrase such as ``cobalt sensor`` even when they reword
    the rest of the sentence, so a nearby pair is stronger evidence than two scattered terms.
    """

    bonus = 0.0
    for left, right in pairwise(query_terms):
        if left not in positions or right not in positions:
            continue
        if any(abs(first - second) <= _ADJACENCY_DISTANCE for first in positions[left] for second in positions[right]):
            bonus += _ADJACENCY_WEIGHT * (idf[left] + idf[right])
    return bonus

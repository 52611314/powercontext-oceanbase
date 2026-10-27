from __future__ import annotations

import json
import math
import time
import uuid
from collections import Counter
from datetime import datetime
from typing import Any

from .db import pool
from .retrieval import search
from .text import looks_like_heading, query_terms, split_sentences


def _similar(left: str, right: str) -> bool:
    return left[:30] == right[:30] or left in right or right in left


def select_bullets(
    hits: list[dict[str, Any]],
    question: str,
    max_bullets: int = 5,
    max_per_chunk: int = 2,
) -> list[dict[str, Any]]:
    """Extractive evidence selection: IDF-weighted sentence scoring over retrieved chunks."""
    terms = set(query_terms(question))
    if not terms or not hits:
        return []

    chunk_tokens = [set(query_terms(hit["text"])) for hit in hits]
    total = len(hits)
    document_frequency: Counter[str] = Counter()
    for tokens in chunk_tokens:
        for term in tokens & terms:
            document_frequency[term] += 1
    idf = {term: math.log(1 + total / (1 + document_frequency[term])) for term in terms}
    ascii_terms = {term for term in terms if term.isascii()}

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for hit in hits:
        for sentence in split_sentences(hit["text"]):
            sentence = sentence.strip()
            if len(sentence) < 12:
                continue
            if looks_like_heading(sentence) or sentence == hit["doc_title"]:
                continue
            matched = {term for term in query_terms(sentence) if term in idf}
            if not matched:
                continue
            # Longer n-grams carry more signal than a single common bigram.
            weighted = sum(idf[term] * (1 + 0.35 * (len(term) - 2)) for term in matched)
            if matched & ascii_terms:
                weighted *= 1.5
            score = weighted / (len(sentence) ** 0.75)
            scored.append((score, sentence, hit))
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score = scored[0][0] if scored else 0.0

    bullets: list[dict[str, Any]] = []
    per_chunk: Counter[str] = Counter()
    seen: list[str] = []
    for score, sentence, hit in scored:
        if len(bullets) >= max_bullets:
            break
        if len(bullets) >= 3 and score < top_score * 0.45:
            break
        if per_chunk[hit["chunk_id"]] >= max_per_chunk:
            continue
        if any(_similar(sentence, other) for other in seen):
            continue
        text = sentence if len(sentence) <= 220 else sentence[:220].rstrip() + "…"
        offset = hit["text"].find(sentence)
        char_start = hit.get("char_start", 0) + offset if offset >= 0 else hit.get("char_start", 0)
        char_end = char_start + len(sentence) if offset >= 0 else hit.get("char_end", 0)
        bullets.append(
            {
                "text": text,
                "score": round(score, 4),
                "citation": {
                    "chunk_id": hit["chunk_id"],
                    "doc_id": hit["doc_id"],
                    "doc_title": hit["doc_title"],
                    "page": hit["page"],
                    "section": hit["section"],
                    "char_start": char_start,
                    "char_end": char_end,
                },
                "channels": hit["channels"],
            }
        )
        seen.append(sentence)
        per_chunk[hit["chunk_id"]] += 1
    return bullets


def ask(
    *,
    question: str,
    doc_ids: list[str] | None = None,
    tags: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = search(
        query=question,
        mode="hybrid",
        doc_ids=doc_ids,
        tags=tags,
        year_from=year_from,
        year_to=year_to,
        top_k=8,
        candidate_k=60,
    )
    bullets = select_bullets(result["hits"], question)
    answer_id = uuid.uuid4().hex
    latency_ms = (time.perf_counter() - started) * 1000
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO answers (id, question, search_id, bullets_json, latency_ms) "
            "VALUES (%s,%s,%s,%s,%s)",
            (
                answer_id,
                question[:1000],
                result["search_id"],
                json.dumps(bullets, ensure_ascii=False),
                int(latency_ms),
            ),
        )
    summary_fields = ("chunk_id", "doc_id", "doc_title", "page", "section", "score", "channels")
    return {
        "answer_id": answer_id,
        "question": question,
        "bullets": bullets,
        "search_id": result["search_id"],
        "channel_counts": result["channel_counts"],
        "timings": result["timings"],
        "latency_ms": round(latency_ms, 1),
        "hits": [{field: hit[field] for field in summary_fields} for hit in result["hits"]],
    }


def get_answer(answer_id: str) -> dict[str, Any] | None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, question, search_id, bullets_json, latency_ms, created_at "
            "FROM answers WHERE id=%s",
            (answer_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    row["bullets"] = json.loads(row.pop("bullets_json"))
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return row


def list_answers(limit: int = 20) -> list[dict[str, Any]]:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, question, search_id, latency_ms, created_at FROM answers "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (max(1, min(limit, 100)),),
        )
        rows = cur.fetchall()
    for row in rows:
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return rows


def delete_answer(answer_id: str) -> bool:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM answers WHERE id=%s", (answer_id,))
        return cur.rowcount > 0

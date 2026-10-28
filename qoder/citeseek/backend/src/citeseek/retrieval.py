from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Literal

from .config import settings
from .db import pool
from .embedding import embedder, vector_literal
from .text import find_spans, snippet

Mode = Literal["hybrid", "fts", "vector"]

RRF_K = 60
log_lock = threading.Lock()


def _resolve_doc_ids(
    doc_ids: list[str] | None,
    tags: list[str] | None,
    year_from: int | None,
    year_to: int | None,
) -> list[str] | None:
    clauses: list[str] = []
    params: list[Any] = []
    if doc_ids:
        clauses.append(f"id IN ({','.join(['%s'] * len(doc_ids))})")
        params.extend(doc_ids)
    for tag in tags or []:
        clauses.append("tags LIKE %s")
        params.append(f"%,{tag},%")
    if year_from:
        clauses.append("year >= %s")
        params.append(year_from)
    if year_to:
        clauses.append("year <= %s")
        params.append(year_to)
    if not clauses:
        return None
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id FROM documents WHERE {' AND '.join(clauses)}", params)
        return [row["id"] for row in cur.fetchall()]


def _filter_sql(allowed: list[str] | None) -> tuple[str, list[str]]:
    if allowed is None:
        return "", []
    if not allowed:
        return " AND 1=0", []
    placeholders = ",".join(["%s"] * len(allowed))
    return f" AND c.doc_id IN ({placeholders})", list(allowed)


def build_sql(
    query: str,
    query_vector: str,
    mode: Mode,
    allowed: list[str] | None,
    candidate_k: int,
) -> tuple[str, list[Any]]:
    filter_sql, filter_params = _filter_sql(allowed)
    if mode == "fts":
        sql = f"""
        SELECT 'fts' AS channel, c.id, c.doc_id, c.page, c.section, c.text,
               c.char_start, c.char_end,
               MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score, NULL AS dist
        FROM chunks c
        WHERE MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE){filter_sql}
        ORDER BY score DESC LIMIT %s
        """
        return sql, [query, query, *filter_params, candidate_k]

    if mode == "vector":
        sql = f"""
        SELECT 'vector' AS channel, c.id, c.doc_id, c.page, c.section, c.text,
               c.char_start, c.char_end,
               NULL AS score, COSINE_DISTANCE(c.embedding, %s) AS dist
        FROM chunks c
        WHERE 1=1{filter_sql}
        ORDER BY dist APPROXIMATE LIMIT %s
        """
        return sql, [query_vector, *filter_params, candidate_k]

    sql = f"""
    WITH fts AS (
      SELECT c.id, c.doc_id, c.page, c.section, c.text, c.char_start, c.char_end,
             MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score
      FROM chunks c
      WHERE MATCH(c.text) AGAINST(%s IN NATURAL LANGUAGE MODE){filter_sql}
      ORDER BY score DESC LIMIT %s
    ), vec AS (
      SELECT c.id, c.doc_id, c.page, c.section, c.text, c.char_start, c.char_end,
             COSINE_DISTANCE(c.embedding, %s) AS dist
      FROM chunks c
      WHERE 1=1{filter_sql}
      ORDER BY dist APPROXIMATE LIMIT %s
    )
    SELECT 'fts' AS channel, id, doc_id, page, section, text, char_start, char_end,
           score, NULL AS dist FROM fts
    UNION ALL
    SELECT 'vector', id, doc_id, page, section, text, char_start, char_end, NULL, dist FROM vec
    """
    return sql, [
        query,
        query,
        *filter_params,
        candidate_k,
        query_vector,
        *filter_params,
        candidate_k,
    ]


def _fuse(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_channel.setdefault(row["channel"], []).append(row)

    merged: dict[str, dict[str, Any]] = {}
    for channel, items in by_channel.items():
        for rank, row in enumerate(items, start=1):
            entry = merged.setdefault(
                row["id"],
                {
                    "chunk_id": row["id"],
                    "doc_id": row["doc_id"],
                    "page": row["page"],
                    "section": row["section"],
                    "text": row["text"],
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                    "score": 0.0,
                    "channels": {},
                },
            )
            entry["score"] += 1.0 / (RRF_K + rank)
            entry["channels"][channel] = {
                "rank": rank,
                "score": None if row["score"] is None else round(float(row["score"]), 6),
                "distance": None if row["dist"] is None else round(float(row["dist"]), 6),
            }
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)


def _titles(doc_ids: list[str]) -> dict[str, str]:
    if not doc_ids:
        return {}
    placeholders = ",".join(["%s"] * len(doc_ids))
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id, title FROM documents WHERE id IN ({placeholders})", doc_ids)
        return {row["id"]: row["title"] for row in cur.fetchall()}


def _append_log(payload: dict[str, Any]) -> None:
    path = settings.data_dir / "logs" / "retrieval.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with log_lock, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _save_search(
    *,
    search_id: str,
    query: str,
    mode: Mode,
    filters: dict[str, Any],
    latency_ms: int,
    channels: list[str],
    hit_count: int,
    sql: str,
    candidate_k: int,
    hits: list[dict[str, Any]],
) -> None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO searches
              (id, query, filters, mode, latency_ms, channels, hit_count,
               sql_text, params_json, hits_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                search_id,
                query[:1000],
                json.dumps(filters, ensure_ascii=False)[:1000],
                mode,
                latency_ms,
                ",".join(channels),
                hit_count,
                sql.strip(),
                json.dumps({"filters": filters, "candidate_k": candidate_k}, ensure_ascii=False),
                json.dumps(hits, ensure_ascii=False),
            ),
        )


def search(
    *,
    query: str,
    mode: Mode = "hybrid",
    doc_ids: list[str] | None = None,
    tags: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    top_k: int = 8,
    candidate_k: int = 50,
) -> dict[str, Any]:
    started = time.perf_counter()
    query = query.strip()
    if not query:
        raise ValueError("query 不能为空")

    filters = {
        "doc_ids": doc_ids or [],
        "tags": tags or [],
        "year_from": year_from,
        "year_to": year_to,
    }
    allowed = _resolve_doc_ids(doc_ids, tags, year_from, year_to)

    embed_start = time.perf_counter()
    query_vector = vector_literal(embedder.encode([query])[0])
    embed_ms = (time.perf_counter() - embed_start) * 1000

    sql, params = build_sql(query, query_vector, mode, allowed, candidate_k)

    sql_start = time.perf_counter()
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    sql_ms = (time.perf_counter() - sql_start) * 1000

    merge_start = time.perf_counter()
    hits = _fuse(rows)
    titles = _titles(list({hit["doc_id"] for hit in hits}))
    for hit in hits:
        hit["doc_title"] = titles.get(hit["doc_id"], "")
        hit["spans"] = find_spans(hit["text"], query)
        hit["snippet"] = snippet(hit["text"], query)
        hit["score"] = round(hit["score"], 6)
    hits = hits[: max(1, min(top_k, 50))]
    merge_ms = (time.perf_counter() - merge_start) * 1000

    total_ms = (time.perf_counter() - started) * 1000
    search_id = uuid.uuid4().hex
    channel_counts: dict[str, int] = {}
    for row in rows:
        channel_counts[row["channel"]] = channel_counts.get(row["channel"], 0) + 1

    _save_search(
        search_id=search_id,
        query=query,
        mode=mode,
        filters=filters,
        latency_ms=int(total_ms),
        channels=sorted(channel_counts),
        hit_count=len(hits),
        sql=sql,
        candidate_k=candidate_k,
        hits=hits,
    )
    _append_log(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "search_id": search_id,
            "query": query,
            "mode": mode,
            "filters": filters,
            "latency_ms": round(total_ms, 1),
            "timings": {"embed_ms": round(embed_ms, 1), "sql_ms": round(sql_ms, 1)},
            "channel_counts": channel_counts,
            "sql": " ".join(sql.split()),
            "top_hits": [
                {
                    "chunk_id": hit["chunk_id"],
                    "doc_id": hit["doc_id"],
                    "page": hit["page"],
                    "score": hit["score"],
                    "channels": hit["channels"],
                }
                for hit in hits[:5]
            ],
        }
    )

    return {
        "search_id": search_id,
        "query": query,
        "mode": mode,
        "filters": filters,
        "latency_ms": round(total_ms, 1),
        "timings": {
            "embed_ms": round(embed_ms, 1),
            "sql_ms": round(sql_ms, 1),
            "merge_ms": round(merge_ms, 1),
        },
        "channel_counts": channel_counts,
        "hits": hits,
    }


def get_search(search_id: str) -> dict[str, Any] | None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, query, filters, mode, latency_ms, channels, hit_count, created_at "
            "FROM searches WHERE id=%s",
            (search_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return row


def list_searches(limit: int = 20) -> list[dict[str, Any]]:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, query, mode, filters, latency_ms, channels, hit_count, created_at "
            "FROM searches ORDER BY created_at DESC, id DESC LIMIT %s",
            (max(1, min(limit, 100)),),
        )
        rows = cur.fetchall()
    for row in rows:
        if isinstance(row.get("created_at"), datetime):
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return rows


def get_search_hits(search_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT query, hits_json FROM searches WHERE id=%s", (search_id,))
        row = cur.fetchone()
    if not row:
        return None, []
    return {"query": row["query"]}, json.loads(row["hits_json"] or "[]")


def debug_search(search_id: str) -> dict[str, Any] | None:
    record = get_search(search_id)
    if not record:
        return None
    filters = json.loads(record["filters"]) if record.get("filters") else {}
    allowed = _resolve_doc_ids(
        filters.get("doc_ids"),
        filters.get("tags"),
        filters.get("year_from"),
        filters.get("year_to"),
    )
    query = record["query"]
    mode: Mode = record["mode"]  # type: ignore[assignment]
    query_vector = vector_literal(embedder.encode([query])[0])
    sql, params = build_sql(query, query_vector, mode, allowed, 50)

    with pool.conn() as conn, conn.cursor() as cur:
        final_sql = cur.mogrify(sql, params)
        cur.execute("EXPLAIN " + final_sql)
        explain_lines = [list(row.values())[0] for row in cur.fetchall()]
        cur.execute(sql, params)
        rows = cur.fetchall()

    channel_counts: dict[str, int] = {}
    for row in rows:
        channel_counts[row["channel"]] = channel_counts.get(row["channel"], 0) + 1
    explain = "\n".join(str(line) for line in explain_lines)
    return {
        "search_id": search_id,
        "query": query,
        "mode": mode,
        "filters": filters,
        "sql": " ".join(sql.split()),
        "explain": explain,
        "channel_counts": channel_counts,
        "rows": len(rows),
        "indexes": {
            "fulltext": "TEXT RETRIEVAL SCAN" in explain,
            "vector": "VECTOR INDEX" in explain,
        },
    }

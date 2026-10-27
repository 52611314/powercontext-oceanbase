from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from .db import pool


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_tags(tags: str) -> str:
    items = [item.strip() for item in tags.replace("，", ",").split(",")]
    unique = list(dict.fromkeys(item for item in items if item))
    return "," + ",".join(unique) + "," if unique else ""


def tags_to_list(raw: str) -> list[str]:
    return [item for item in raw.strip(",").split(",") if item]


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["tags"] = tags_to_list(row.get("tags") or "")
    if isinstance(row.get("created_at"), datetime):
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(row.get("updated_at"), datetime):
        row["updated_at"] = row["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
    return row


def find_by_sha(sha256: str) -> dict[str, Any] | None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE sha256=%s", (sha256,))
        row = cur.fetchone()
        return _serialize(row) if row else None


def get_document(doc_id: str) -> dict[str, Any] | None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunk_count
            FROM documents d WHERE d.id=%s
            """,
            (doc_id,),
        )
        row = cur.fetchone()
        return _serialize(row) if row else None


def create_document(
    *,
    title: str,
    source_type: str,
    sha256: str,
    file_name: str,
    file_path: str,
    file_size: int,
    tags: str = "",
) -> dict[str, Any]:
    doc_id = uuid.uuid4().hex
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO documents
              (id, title, source_type, sha256, file_name, file_path, file_size, tags, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')
            """,
            (
                doc_id,
                title[:500],
                source_type,
                sha256,
                file_name[:250],
                file_path,
                file_size,
                normalize_tags(tags),
            ),
        )
    return get_document(doc_id)  # type: ignore[return-value]


def list_documents(
    *,
    q: str = "",
    status: str = "",
    tag: str = "",
    read_status: str = "",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append("(title LIKE %s OR authors LIKE %s OR file_name LIKE %s)")
        params.extend([f"%{q}%"] * 3)
    if status:
        where.append("status=%s")
        params.append(status)
    if read_status:
        where.append("read_status=%s")
        params.append(read_status)
    if tag:
        where.append("tags LIKE %s")
        params.append(f"%,{tag},%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM documents{clause}", params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"""
            SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id=d.id) AS chunk_count
            FROM documents d{clause}
            ORDER BY d.created_at DESC, d.id DESC
            LIMIT %s OFFSET %s
            """,
            [*params, limit, offset],
        )
        items = [_serialize(row) for row in cur.fetchall()]
    return items, total


def update_document(doc_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "title",
        "authors",
        "year",
        "venue",
        "status",
        "read_status",
        "progress",
        "error",
        "tags",
        "page_count",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return get_document(doc_id)
    if "tags" in updates:
        updates["tags"] = normalize_tags(str(updates["tags"]))
    assignments = ", ".join(f"{key}=%s" for key in updates)
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE documents SET {assignments}, updated_at=%s WHERE id=%s",
            [*updates.values(), _now(), doc_id],
        )
    return get_document(doc_id)


def delete_document(doc_id: str) -> dict[str, Any] | None:
    doc = get_document(doc_id)
    if not doc:
        return None
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
        cur.execute("DELETE FROM pages WHERE doc_id=%s", (doc_id,))
        cur.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
    return doc


def clear_index(doc_id: str) -> None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE doc_id=%s", (doc_id,))
        cur.execute("DELETE FROM pages WHERE doc_id=%s", (doc_id,))


def get_page(doc_id: str, page: int) -> dict[str, Any] | None:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT page, section, text FROM pages WHERE doc_id=%s AND page=%s",
            (doc_id, page),
        )
        return cur.fetchone()


def insert_pages(doc_id: str, pages: list[tuple[int, str, str]]) -> None:
    if not pages:
        return
    with pool.conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO pages (doc_id, page, section, text) VALUES (%s,%s,%s,%s)",
            [(doc_id, page, section[:250], text) for page, section, text in pages],
        )


def insert_chunks(rows: list[tuple]) -> None:
    if not rows:
        return
    with pool.conn() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
              (id, doc_id, page, section, chunk_no, text, char_start, char_end, embedding)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows,
        )


def stats() -> dict[str, Any]:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS documents,
                   COALESCE(SUM(page_count),0) AS pages,
                   COALESCE(SUM(status='ready'),0) AS ready,
                   COALESCE(SUM(status='indexing'),0) AS indexing,
                   COALESCE(SUM(status='failed'),0) AS failed
            FROM documents
            """
        )
        doc_stats = cur.fetchone() or {}
        cur.execute("SELECT COUNT(*) AS chunks FROM chunks")
        chunk_stats = cur.fetchone() or {}
    return {**doc_stats, **chunk_stats}

from __future__ import annotations

import contextlib
import queue
import threading

import pymysql
from pymysql.cursors import DictCursor

from .config import settings

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS documents (
      id VARCHAR(32) NOT NULL PRIMARY KEY,
      title VARCHAR(512) NOT NULL,
      authors VARCHAR(1024) NOT NULL DEFAULT '',
      year INT DEFAULT NULL,
      venue VARCHAR(255) NOT NULL DEFAULT '',
      source_type VARCHAR(16) NOT NULL,
      sha256 CHAR(64) NOT NULL,
      file_name VARCHAR(255) NOT NULL DEFAULT '',
      file_path VARCHAR(1024) NOT NULL,
      file_size INT NOT NULL DEFAULT 0,
      page_count INT NOT NULL DEFAULT 0,
      progress INT NOT NULL DEFAULT 0,
      status VARCHAR(16) NOT NULL DEFAULT 'pending',
      read_status VARCHAR(16) NOT NULL DEFAULT 'unread',
      error VARCHAR(512) NOT NULL DEFAULT '',
      tags VARCHAR(512) NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_sha (sha256)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pages (
      doc_id VARCHAR(32) NOT NULL,
      page INT NOT NULL,
      section VARCHAR(255) NOT NULL DEFAULT '',
      text MEDIUMTEXT NOT NULL,
      PRIMARY KEY (doc_id, page)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
      id VARCHAR(64) NOT NULL PRIMARY KEY,
      doc_id VARCHAR(32) NOT NULL,
      page INT NOT NULL,
      section VARCHAR(255) NOT NULL DEFAULT '',
      chunk_no INT NOT NULL DEFAULT 0,
      text MEDIUMTEXT NOT NULL,
      char_start INT NOT NULL DEFAULT 0,
      char_end INT NOT NULL DEFAULT 0,
      embedding VECTOR(512) NOT NULL,
      FULLTEXT INDEX ft_text (text) WITH PARSER ik,
      VECTOR INDEX vidx_emb (embedding) WITH (distance=cosine, type=hnsw, lib=vsag),
      INDEX idx_doc (doc_id),
      INDEX idx_doc_page (doc_id, page)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS searches (
      id VARCHAR(32) NOT NULL PRIMARY KEY,
      query VARCHAR(1024) NOT NULL,
      filters VARCHAR(1024) NOT NULL DEFAULT '',
      mode VARCHAR(16) NOT NULL DEFAULT 'hybrid',
      latency_ms INT NOT NULL DEFAULT 0,
      channels VARCHAR(64) NOT NULL DEFAULT '',
      hit_count INT NOT NULL DEFAULT 0,
      sql_text MEDIUMTEXT,
      params_json MEDIUMTEXT,
      hits_json MEDIUMTEXT,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_created (created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS answers (
      id VARCHAR(32) NOT NULL PRIMARY KEY,
      question VARCHAR(1024) NOT NULL,
      search_id VARCHAR(32) NOT NULL,
      bullets_json MEDIUMTEXT NOT NULL,
      latency_ms INT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_created (created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS favorites (
      id VARCHAR(32) NOT NULL PRIMARY KEY,
      chunk_id VARCHAR(64) NOT NULL,
      doc_id VARCHAR(32) NOT NULL,
      note VARCHAR(512) NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_chunk (chunk_id)
    )
    """,
]


class ConnectionPool:
    def __init__(self, size: int = 6) -> None:
        self._size = size
        self._queue: queue.Queue = queue.Queue(maxsize=size)
        self._semaphore = threading.BoundedSemaphore(size)

    def _connect(self):
        return pymysql.connect(
            host=settings.seekdb_host,
            port=settings.seekdb_port,
            user=settings.seekdb_user,
            password=settings.seekdb_password,
            database=settings.seekdb_db,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
            read_timeout=60,
        )

    @contextlib.contextmanager
    def conn(self):
        self._semaphore.acquire()
        try:
            try:
                conn = self._queue.get_nowait()
            except queue.Empty:
                conn = self._connect()
            try:
                conn.ping(reconnect=True)
                yield conn
            except pymysql.MySQLError:
                with contextlib.suppress(Exception):
                    conn.close()
                raise
            except Exception:
                with contextlib.suppress(Exception):
                    conn.close()
                raise
            else:
                with contextlib.suppress(Exception):
                    self._queue.put_nowait(conn)
        finally:
            self._semaphore.release()

    def close(self) -> None:
        while not self._queue.empty():
            with contextlib.suppress(Exception):
                self._queue.get_nowait().close()


pool = ConnectionPool()


def ensure_database() -> None:
    conn = pymysql.connect(
        host=settings.seekdb_host,
        port=settings.seekdb_port,
        user=settings.seekdb_user,
        password=settings.seekdb_password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{settings.seekdb_db}`")
    finally:
        conn.close()


MIGRATIONS = [
    "ALTER TABLE searches ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'hybrid'",
    "ALTER TABLE searches ADD COLUMN sql_text MEDIUMTEXT",
    "ALTER TABLE searches ADD COLUMN params_json MEDIUMTEXT",
    "ALTER TABLE searches ADD COLUMN hits_json MEDIUMTEXT",
]


def ensure_schema() -> None:
    with pool.conn() as conn, conn.cursor() as cur:
        for ddl in SCHEMA:
            cur.execute(ddl)
        for migration in MIGRATIONS:
            try:
                cur.execute(migration)
            except pymysql.err.OperationalError as exc:
                if exc.args and exc.args[0] != 1060:  # duplicate column
                    raise


def ping() -> bool:
    with pool.conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() is not None

"""记仇小镇 · 记忆服务

为 Dify Chatflow 提供三类能力：

1. OpenAI 兼容的 ``/v1/embeddings``：本地 bge-small-zh-v1.5（512 维），
   供 Dify 知识库与应用自身共用，避免依赖付费 Embedding API。
2. NPC 事件记忆的写入与 seekdb 向量检索：玩家对 NPC 做过的每件事都会
   被编码后写入 seekdb，下次互动时按语义相似度召回。
3. 玩家-NPC 关系状态读写：愤怒值 / 信任值决定 NPC 的态度。

所有记忆与关系数据存储在 seekdb（MySQL 协议），与 Dify 的元数据库、
向量库是同一个 seekdb 实例，但位于独立的 ``town`` 库。
"""

from __future__ import annotations

import os
import time
from typing import Any

import pymysql
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODEL_PATH = os.getenv("MODEL_PATH", "/models/bge-small-zh-v1.5")
SEEKDB_DATABASE = os.getenv("SEEKDB_DATABASE", "town")

app = FastAPI(title="Grudge Town Memory Service", version="1.0.0")

_model = None

# 事件类型对关系值的影响
ANGER_DELTA = {
    "steal_apple": 18,
    "firecracker": 28,
    "rumor": 22,
    "help": -12,
    "apologize": -25,
    "gift": -15,
    "chat": 0,
}
TRUST_DELTA = {
    "steal_apple": -10,
    "firecracker": -16,
    "rumor": -20,
    "help": 15,
    "apologize": 6,
    "gift": 10,
    "chat": 2,
}


def get_model():
    """懒加载 SentenceTransformer，避免健康检查时占用内存。"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_PATH, device="cpu")
    return _model


def connect():
    return pymysql.connect(
        host=os.getenv("SEEKDB_HOST", "seekdb"),
        port=int(os.getenv("SEEKDB_PORT", "2881")),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", "difyai123456"),
        database=SEEKDB_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
        read_timeout=30,
    )


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS npc_profile (
        npc_id VARCHAR(32) PRIMARY KEY,
        name VARCHAR(64) NOT NULL,
        persona TEXT NOT NULL,
        catchphrase VARCHAR(255),
        base_anger INT NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS event_memory (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        player_id VARCHAR(64) NOT NULL,
        npc_id VARCHAR(32) NOT NULL,
        event_type VARCHAR(32) NOT NULL,
        content TEXT NOT NULL,
        severity INT NOT NULL DEFAULT 1,
        embedding VECTOR(512) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY idx_player_npc (player_id, npc_id)
    )""",
    """CREATE TABLE IF NOT EXISTS relation (
        player_id VARCHAR(64) NOT NULL,
        npc_id VARCHAR(32) NOT NULL,
        anger INT NOT NULL DEFAULT 0,
        trust INT NOT NULL DEFAULT 50,
        last_event VARCHAR(255),
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (player_id, npc_id)
    )""",
]

NPC_SEED = {
    "npc_id": "laochen",
    "name": "老陈",
    "persona": (
        "小镇杂货店老板，五十来岁，抠门、嘴硬、记性极好。"
        "谁偷过他一颗糖他都能记三年，但心肠不坏，吃软不吃硬。"
        "说话带点方言味，喜欢用反问句，生气了会翻旧账。"
    ),
    "catchphrase": "我这小店，童叟无欺。",
    "base_anger": 0,
}


def vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def encode(texts: list[str]) -> list[list[float]]:
    return get_model().encode(
        texts, normalize_embeddings=True, batch_size=32
    ).tolist()


def seed(cur) -> None:
    cur.execute(
        "INSERT IGNORE INTO npc_profile (npc_id, name, persona, catchphrase, base_anger) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            NPC_SEED["npc_id"],
            NPC_SEED["name"],
            NPC_SEED["persona"],
            NPC_SEED["catchphrase"],
            NPC_SEED["base_anger"],
        ),
    )


def ensure_schema() -> None:
    """等待 seekdb 就绪并建表，最多重试 60 秒。"""
    last_err: Exception | None = None
    for _ in range(30):
        try:
            with connect() as conn:
                with conn.cursor() as cur:
                    for stmt in SCHEMA:
                        cur.execute(stmt)
                    try:
                        cur.execute(
                            "CREATE VECTOR INDEX idx_event_emb ON event_memory(embedding) "
                            "WITH (distance=l2, type=hnsw)"
                        )
                    except Exception as exc:  # 索引已存在（seekdb 返回 1235）时忽略
                        code = exc.args[0] if getattr(exc, "args", None) else None
                        msg = str(exc)
                        if (
                            code != 1235
                            and "Duplicate" not in msg
                            and "already exists" not in msg
                            and "not supported" not in msg
                        ):
                            raise
                    seed(cur)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2)
    raise RuntimeError(f"seekdb 未就绪: {last_err}")


@app.on_event("startup")
def startup() -> None:
    ensure_schema()


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"status": "ok", "database": "ok", "model_loaded": _model is not None}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


class EmbeddingRequest(BaseModel):
    input: Any
    model: str = "bge-small-zh-v1.5"
    encoding_format: str | None = None


@app.post("/v1/embeddings")
def create_embeddings(req: EmbeddingRequest) -> dict[str, Any]:
    raw = req.input if isinstance(req.input, list) else [req.input]
    texts = [str(x) for x in raw]
    if not texts:
        raise HTTPException(status_code=400, detail="input 不能为空")
    vectors = encode(texts)
    data = [
        {"object": "embedding", "index": i, "embedding": v}
        for i, v in enumerate(vectors)
    ]
    return {
        "object": "list",
        "data": data,
        "model": req.model,
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "bge-small-zh-v1.5",
                "object": "model",
                "owned_by": "local",
                "dimension": 512,
            }
        ],
    }


class EventWrite(BaseModel):
    player_id: str = "player-1"
    npc_id: str = "laochen"
    event_type: str
    content: str
    severity: int = 1


@app.post("/memory/write")
def write_event(ev: EventWrite) -> dict[str, Any]:
    if not ev.content.strip():
        raise HTTPException(status_code=400, detail="content 不能为空")
    vec = encode([ev.content])[0]
    anger_delta = ANGER_DELTA.get(ev.event_type, 0)
    trust_delta = TRUST_DELTA.get(ev.event_type, 0)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO event_memory "
                "(player_id, npc_id, event_type, content, severity, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    ev.player_id,
                    ev.npc_id,
                    ev.event_type,
                    ev.content,
                    ev.severity,
                    vec_literal(vec),
                ),
            )
            event_id = cur.lastrowid
            cur.execute(
                "INSERT IGNORE INTO relation (player_id, npc_id, anger, trust) "
                "VALUES (%s, %s, 0, 50)",
                (ev.player_id, ev.npc_id),
            )
            cur.execute(
                "UPDATE relation SET anger = LEAST(100, GREATEST(0, anger + %s)), "
                "trust = LEAST(100, GREATEST(0, trust + %s)), last_event = %s "
                "WHERE player_id = %s AND npc_id = %s",
                (
                    anger_delta,
                    trust_delta,
                    ev.content[:255],
                    ev.player_id,
                    ev.npc_id,
                ),
            )
    return {
        "ok": True,
        "event_id": event_id,
        "event_type": ev.event_type,
        "anger_delta": anger_delta,
        "trust_delta": trust_delta,
    }


class MemorySearch(BaseModel):
    player_id: str = "player-1"
    npc_id: str = "laochen"
    query: str
    top_k: int = 5


def mood_of(anger: int, trust: int) -> str:
    if anger >= 75:
        return "暴怒"
    if anger >= 45:
        return "记恨"
    if anger >= 20:
        return "不爽"
    if trust >= 80:
        return "亲厚"
    if trust >= 60:
        return "熟络"
    return "平淡"


@app.post("/memory/search")
def search_memory(req: MemorySearch) -> dict[str, Any]:
    if not req.query.strip():
        return {"memories": [], "summary": "（暂无相关记忆）"}
    vec = encode([req.query])[0]
    with connect() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT id, event_type, content, severity, created_at, "
                "l2_distance(embedding, %s) AS distance "
                "FROM event_memory WHERE player_id = %s AND npc_id = %s "
                "ORDER BY distance ASC LIMIT %s",
                (vec_literal(vec), req.player_id, req.npc_id, req.top_k),
            )
            rows = cur.fetchall()
    for row in rows:
        if row.get("created_at") is not None:
            row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M")
        if row.get("distance") is not None:
            row["distance"] = round(float(row["distance"]), 4)
    lines = [
        f"- {r['created_at']} 你{r['content']}（{r['event_type']}）"
        for r in rows
        if r.get("content")
    ]
    return {
        "player_id": req.player_id,
        "npc_id": req.npc_id,
        "memories": rows,
        "summary": "\n".join(lines) if lines else "（暂无相关记忆）",
    }


class RelationGet(BaseModel):
    player_id: str = "player-1"
    npc_id: str = "laochen"


@app.post("/relation/get")
def get_relation(req: RelationGet) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT anger, trust, last_event, updated_at FROM relation "
                "WHERE player_id = %s AND npc_id = %s",
                (req.player_id, req.npc_id),
            )
            row = cur.fetchone()
    if row is None:
        row = {"anger": 0, "trust": 50, "last_event": None, "updated_at": None}
    if row.get("updated_at") is not None:
        row["updated_at"] = row["updated_at"].strftime("%Y-%m-%d %H:%M")
    row["mood"] = mood_of(int(row["anger"]), int(row["trust"]))
    return row


class RelationUpdate(BaseModel):
    player_id: str = "player-1"
    npc_id: str = "laochen"
    anger_delta: int = 0
    trust_delta: int = 0
    last_event: str | None = None


@app.post("/relation/update")
def update_relation(req: RelationUpdate) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO relation (player_id, npc_id, anger, trust) "
                "VALUES (%s, %s, 0, 50)",
                (req.player_id, req.npc_id),
            )
            cur.execute(
                "UPDATE relation SET anger = LEAST(100, GREATEST(0, anger + %s)), "
                "trust = LEAST(100, GREATEST(0, trust + %s)), "
                "last_event = COALESCE(%s, last_event) "
                "WHERE player_id = %s AND npc_id = %s",
                (
                    req.anger_delta,
                    req.trust_delta,
                    (req.last_event or "")[:255] or None,
                    req.player_id,
                    req.npc_id,
                ),
            )
    return get_relation(RelationGet(player_id=req.player_id, npc_id=req.npc_id))


@app.get("/npc/profile")
def npc_profile(npc_id: str = "laochen") -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT npc_id, name, persona, catchphrase, base_anger "
                "FROM npc_profile WHERE npc_id = %s",
                (npc_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="NPC 不存在")
    return row

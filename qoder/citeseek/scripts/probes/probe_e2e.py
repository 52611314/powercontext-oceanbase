import json
import time

import numpy as np
import onnxruntime as ort
import pymysql
from tokenizers import Tokenizer

MODEL_DIR = r"D:\oceanbase\powercontext-oceanbase\stepfun\knowledge-base\public\models\bge-small-zh-v1.5"

DOCS = [
    ("d1#p1", "d1", 1, "FlashAttention 通过分块计算避免实例化完整的注意力矩阵，将显存占用从 O(n^2) 降到 O(n)。"),
    ("d1#p2", "d1", 2, "实验表明，在 8K 上下文下，FlashAttention 的训练吞吐比基线提升约 2.3 倍。"),
    ("d2#p1", "d2", 1, "KV cache 压缩在长文本推理中可以减少 60% 的显存占用，但需要重新计算被驱逐的 token。"),
    ("d2#p2", "d2", 2, "我们对四种 KV cache 策略做了对比：窗口淘汰、量化、低秩分解与动态稀疏。"),
    ("d3#p1", "d3", 1, "本文研究中文文献的混合检索：向量召回负责语义，全文检索负责术语精确匹配。"),
    ("d3#p2", "d3", 2, "在 30 条中文查询上，混合检索的 Recall@10 比单一向量通道高 12 个百分点。"),
]
QUERY = "处理长上下文时显存占用怎么降低？"


def load_embedder():
    tok = Tokenizer.from_file(f"{MODEL_DIR}/tokenizer.json")
    tok.enable_truncation(max_length=512)
    tok.enable_padding(pad_id=0, pad_token="[PAD]")
    sess = ort.InferenceSession(f"{MODEL_DIR}/onnx/model.onnx", providers=["CPUExecutionProvider"])
    return tok, sess


def embed(tok, sess, texts):
    encs = tok.encode_batch(texts)
    ids = np.array([e.ids for e in encs], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
    out = sess.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)})[0]
    cls = out[:, 0, :]
    return cls / np.clip(np.linalg.norm(cls, axis=1, keepdims=True), 1e-12, None)


def vec_literal(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def rrf(rows, k=60):
    scores = {}
    for ch, cid, _text, rank in rows:
        scores.setdefault(cid, {"channels": {}, "score": 0.0})["channels"][ch] = rank
        scores[cid]["score"] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1]["score"])


def main():
    tok, sess = load_embedder()
    vecs = embed(tok, sess, [d[3] for d in DOCS])
    qvec = embed(tok, sess, [QUERY])[0]

    conn = pymysql.connect(host="127.0.0.1", port=2881, user="root", password="", charset="utf8mb4", autocommit=True)
    cur = conn.cursor()
    cur.execute("CREATE DATABASE IF NOT EXISTS probe")
    cur.execute("USE probe")
    cur.execute("DROP TABLE IF EXISTS demo_chunks")
    cur.execute(
        """
        CREATE TABLE demo_chunks (
          id VARCHAR(64) PRIMARY KEY, doc_id VARCHAR(64) NOT NULL, page INT NOT NULL, txt TEXT NOT NULL,
          emb VECTOR(512) NOT NULL,
          FULLTEXT INDEX ft(txt) WITH PARSER ik,
          VECTOR INDEX vi(emb) WITH (distance=cosine, type=hnsw, lib=vsag)
        )
        """
    )
    for (cid, doc_id, page, text), v in zip(DOCS, vecs):
        cur.execute(
            "INSERT INTO demo_chunks VALUES (%s,%s,%s,%s,%s)",
            (cid, doc_id, page, text, vec_literal(v)),
        )

    sql = """
    WITH fts AS (
      SELECT id, doc_id, page, txt,
             MATCH(txt) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score
      FROM demo_chunks WHERE MATCH(txt) AGAINST(%s IN NATURAL LANGUAGE MODE)
      ORDER BY score DESC LIMIT 10
    ), vec AS (
      SELECT id, doc_id, page, txt, COSINE_DISTANCE(emb, %s) AS dist
      FROM demo_chunks ORDER BY dist APPROXIMATE LIMIT 10
    )
    SELECT 'fts' AS ch, id, doc_id, page, txt, score, NULL AS dist FROM fts
    UNION ALL
    SELECT 'vector', id, doc_id, page, txt, NULL, dist FROM vec
    """
    t0 = time.perf_counter()
    cur.execute(sql, (QUERY, QUERY, vec_literal(qvec)))
    rows = cur.fetchall()
    latency = (time.perf_counter() - t0) * 1000

    fused = rrf([(r[0], r[1], r[4], 1) for r in rows])  # rank placeholder, recompute below
    # recompute ranks per channel
    by_ch = {}
    for r in rows:
        by_ch.setdefault(r[0], []).append(r)
    ranked = []
    for ch, items in by_ch.items():
        items = sorted(items, key=lambda r: -(r[5] if r[5] is not None else -r[6]))
        for i, r in enumerate(items, 1):
            ranked.append((ch, r[1], r[4], i))
    fused = rrf(ranked)
    doc_meta = {d[0]: (d[1], d[2]) for d in DOCS}

    print(f"channels={sorted(by_ch)} latency_ms={latency:.1f}")
    results = []
    for cid, info in fused[:5]:
        doc_id, page = doc_meta[cid]
        print(f"  {cid} doc={doc_id} p.{page} score={info['score']:.4f} channels={info['channels']}")
        results.append({"id": cid, "doc": doc_id, "page": page, "score": round(info["score"], 4), "channels": info["channels"]})

    ok = len(by_ch) == 2 and fused and fused[0][0] in {"d1#p1", "d1#p2"}
    print(json.dumps({"pass": bool(ok), "channels": sorted(by_ch), "top": results[:3], "latency_ms": round(latency, 1)}, ensure_ascii=False))
    conn.close()
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()

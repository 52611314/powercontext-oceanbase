"""Stage 2 acceptance: run the three retrieval modes, verify channels and evidence.

Usage: python scripts/verify_search.py [base_url]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PROJECT_DIR = Path(__file__).resolve().parents[1]

QUERIES = [
    ("hybrid", "长上下文推理时显存占用怎么降低？"),
    ("fts", "RRF 融合公式里的 k 取多少"),
    ("vector", "混合检索为什么比单一向量通道更稳"),
]


def main() -> int:
    ok = True
    records = []
    with httpx.Client(base_url=BASE, timeout=120) as client:
        ready = client.get("/api/ready")
        if ready.status_code != 200:
            print("服务未就绪:", ready.text)
            return 1

        for mode, query in QUERIES:
            response = client.post("/api/search", json={"query": query, "mode": mode, "top_k": 5})
            response.raise_for_status()
            result = response.json()
            hits = result["hits"]
            debug = client.get(f"/api/debug/retrieval/{result['search_id']}").json()
            records.append(
                {
                    "mode": mode,
                    "query": query,
                    "search_id": result["search_id"],
                    "latency_ms": result["latency_ms"],
                    "timings": result["timings"],
                    "channel_counts": result["channel_counts"],
                    "indexes": debug["indexes"],
                    "sql": debug["sql"],
                    "explain": debug["explain"],
                }
            )
            print(f"\n[{mode}] {query}")
            print(
                f"  latency={result['latency_ms']}ms timings={result['timings']} "
                f"channels={result['channel_counts']} hits={len(hits)}"
            )
            for hit in hits[:3]:
                print(
                    f"  {hit['score']:.4f} {hit['doc_title']} p.{hit['page']} "
                    f"[{hit['section']}] {sorted(hit['channels'])}"
                )
            print(f"  indexes={debug['indexes']} rows={debug['rows']}")

            if not hits:
                print("  ERROR: 无命中")
                ok = False
            if mode in {"hybrid", "fts"} and not debug["indexes"]["fulltext"]:
                print("  ERROR: EXPLAIN 未出现全文索引扫描")
                ok = False
            if mode in {"hybrid", "vector"} and not debug["indexes"]["vector"]:
                print("  ERROR: EXPLAIN 未出现向量索引扫描")
                ok = False

        history = client.get("/api/searches?limit=5").json()["items"]
        log_path = PROJECT_DIR / "data" / "logs" / "retrieval.jsonl"
        log_lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        print(f"\nhistory={len(history)} retrieval_log_lines={len(log_lines)}")

        out_dir = PROJECT_DIR / "docs" / "evidence"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "search-evidence.json").write_text(
            json.dumps({"runs": records}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"evidence written to {(out_dir / 'search-evidence.json').relative_to(PROJECT_DIR)}")

    print("\nOK" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Retrieval evaluation: Hit@10, MRR@10 and section recall over a fixed query set.

Usage: python scripts/evaluate.py [base_url]
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[1]
QUERIES_PATH = PROJECT_DIR / "samples" / "eval" / "queries.json"
OUT_JSON = PROJECT_DIR / "docs" / "evidence" / "retrieval-eval.json"
OUT_MD = PROJECT_DIR / "docs" / "检索评测.md"
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
MODES = ["hybrid", "fts", "vector"]
TOP_K = 10


def is_relevant(hit: dict, case: dict) -> bool:
    if hit["doc_title"] != case["doc"]:
        return False
    return any(section in hit["section"] for section in case["sections"])


def evaluate_mode(client: httpx.Client, cases: list[dict], mode: str) -> dict:
    hit_count = 0
    rr_sum = 0.0
    recall_sum = 0.0
    latencies: list[float] = []
    details = []

    for case in cases:
        response = client.post(
            "/api/search",
            json={"query": case["query"], "mode": mode, "top_k": TOP_K},
        )
        response.raise_for_status()
        result = response.json()
        latencies.append(result["latency_ms"])
        hits = result["hits"]

        first_rank = None
        found_sections: set[str] = set()
        for rank, hit in enumerate(hits, start=1):
            if is_relevant(hit, case):
                if first_rank is None:
                    first_rank = rank
                for section in case["sections"]:
                    if section in hit["section"]:
                        found_sections.add(section)

        hit_at_k = 1 if first_rank else 0
        reciprocal_rank = 1.0 / first_rank if first_rank else 0.0
        section_recall = len(found_sections) / len(case["sections"])
        hit_count += hit_at_k
        rr_sum += reciprocal_rank
        recall_sum += section_recall
        details.append(
            {
                "query": case["query"],
                "hit": bool(hit_at_k),
                "first_rank": first_rank,
                "section_recall": round(section_recall, 3),
                "top1": hits[0]["doc_title"] if hits else None,
            }
        )

    total = len(cases)
    return {
        "mode": mode,
        "queries": total,
        "hit_at_10": round(hit_count / total, 4),
        "mrr_at_10": round(rr_sum / total, 4),
        "section_recall_at_10": round(recall_sum / total, 4),
        "p50_latency_ms": round(statistics.median(latencies), 1),
        "details": details,
    }


def to_markdown(report: dict) -> str:
    rows = report["modes"]
    lines = [
        "# 检索评测",
        "",
        f"> 查询集：`samples/eval/queries.json`（{report['query_count']} 条），Top-{TOP_K}，"
        f"评测时间：{report['timestamp']}。",
        "> 相关性判定：命中文献正确且章节匹配视为相关；MRR 取第一条相关结果的倒数排名。",
        "",
        "## 总体结果",
        "",
        "| 模式 | Hit@10 | MRR@10 | 章节召回@10 | p50 延迟 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['hit_at_10']:.2%} | {row['mrr_at_10']:.4f} | "
            f"{row['section_recall_at_10']:.2%} | {row['p50_latency_ms']} ms |"
        )
    lines.extend(["", "## 逐条结果（混合检索）", "", "| 查询 | 命中 | 首次排名 | 章节召回 |", "| --- | --- | --- | --- |"])
    hybrid = next((row for row in rows if row["mode"] == "hybrid"), None)
    if hybrid:
        for detail in hybrid["details"]:
            lines.append(
                f"| {detail['query']} | {'是' if detail['hit'] else '否'} | "
                f"{detail['first_rank'] or '—'} | {detail['section_recall']:.0%} |"
            )
    lines.extend(
        [
            "",
            "## 复现",
            "",
            "```bash",
            "python scripts/evaluate.py http://127.0.0.1:8080",
            "```",
            "",
            "结果同时写入 `docs/evidence/retrieval-eval.json`。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    cases = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))
    with httpx.Client(base_url=BASE, timeout=120) as client:
        if client.get("/api/ready").status_code != 200:
            print("服务未就绪")
            return 1
        modes = [evaluate_mode(client, cases, mode) for mode in MODES]

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": BASE,
        "query_count": len(cases),
        "top_k": TOP_K,
        "modes": modes,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(to_markdown(report), encoding="utf-8")

    for row in modes:
        print(
            f"{row['mode']:<7} Hit@10={row['hit_at_10']:.2%} "
            f"MRR@10={row['mrr_at_10']:.4f} recall={row['section_recall_at_10']:.2%} "
            f"p50={row['p50_latency_ms']}ms"
        )
    print(f"report -> {OUT_MD.relative_to(PROJECT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

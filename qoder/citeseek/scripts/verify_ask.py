"""Stage 3 acceptance: ask a question, verify citations and exports.

Usage: python scripts/verify_ask.py [base_url]
"""

from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
QUESTION = "长上下文推理时显存占用怎么降低？"


def main() -> int:
    ok = True
    with httpx.Client(base_url=BASE, timeout=180) as client:
        if client.get("/api/ready").status_code != 200:
            print("服务未就绪")
            return 1

        answer = client.post("/api/ask", json={"question": QUESTION}).json()
        print(f"question: {QUESTION}")
        print(f"answer_id={answer['answer_id']} latency={answer['latency_ms']}ms")
        for bullet in answer["bullets"]:
            citation = bullet["citation"]
            print(
                f"  - {bullet['text'][:50]}...  "
                f"<{citation['doc_title']} p.{citation['page']} {citation['section']}>"
            )
            if not citation.get("doc_id") or citation.get("char_end", 0) <= citation.get("char_start", 0):
                print("  ERROR: 引用缺少可定位的字符区间")
                ok = False
            page = client.get(f"/api/documents/{citation['doc_id']}/pages/{citation['page']}").json()
            fragment = page["text"][citation["char_start"] : citation["char_end"]]
            if fragment[:12] != bullet["text"][:12]:
                print(f"  ERROR: 页码/偏移与原文不一致: {fragment[:30]!r}")
                ok = False

        if len(answer["bullets"]) < 3:
            print("  ERROR: 证据不足 3 条")
            ok = False

        markdown = client.get(f"/api/answers/{answer['answer_id']}/export.md")
        print(f"answer export: {markdown.status_code} {len(markdown.text)} chars")
        if "## 出处" not in markdown.text:
            print("  ERROR: 导出缺少出处")
            ok = False

        search_markdown = client.get(f"/api/searches/{answer['search_id']}/export.md")
        print(f"search export: {search_markdown.status_code} {len(search_markdown.text)} chars")

        documents = client.get("/api/documents").json()["items"]
        document = documents[0]
        file_response = client.get(f"/api/documents/{document['id']}/file")
        print(
            f"file: {file_response.status_code} {file_response.headers.get('content-type')} "
            f"{len(file_response.content)} bytes"
        )
        if file_response.status_code != 200 or not file_response.content.startswith(b"%PDF"):
            print("  ERROR: 原文 PDF 不可访问")
            ok = False

        bib = client.get(f"/api/documents/{document['id']}/citation.bib")
        print(f"bibtex: {bib.status_code} {bib.text.splitlines()[0]}")
        if not bib.text.startswith("@misc{"):
            print("  ERROR: BibTeX 格式异常")
            ok = False

    print("\nOK" if ok else "\nFAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

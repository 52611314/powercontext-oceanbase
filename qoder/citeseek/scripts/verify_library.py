"""Stage 1 acceptance: upload samples, wait for indexing, print the library state.

Usage: python scripts/verify_library.py [base_url]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import httpx

PROJECT_DIR = Path(__file__).resolve().parents[1]
PAPERS = sorted((PROJECT_DIR / "samples" / "papers").glob("*.pdf"))
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.load(response)


def main() -> int:
    if not PAPERS:
        print("没有样本 PDF，请先运行 python scripts/make_samples.py")
        return 1

    with httpx.Client(base_url=BASE, timeout=120) as client:
        files = [("files", (path.name, path.read_bytes(), "application/pdf")) for path in PAPERS]
        response = client.post("/api/documents", files=files, data={"tags": "示例资料"})
        response.raise_for_status()
        items = response.json()["items"]
        print(f"uploaded={len(items)}")

        deadline = time.time() + 180
        while time.time() < deadline:
            documents, _ = get("/api/documents?limit=100")["items"], None
            pending = [doc for doc in documents if doc["status"] in {"pending", "indexing"}]
            if not pending:
                break
            time.sleep(2)

        documents = get("/api/documents?limit=100")["items"]
        failed = [doc for doc in documents if doc["status"] == "failed"]
        ready = [doc for doc in documents if doc["status"] == "ready"]
        for doc in documents:
            print(
                f"  {doc['status']:<8} pages={doc['page_count']:<3} chunks={doc['chunk_count']:<4} "
                f"tags={doc['tags']} {doc['title']}"
            )
        if failed:
            for doc in failed:
                print(f"FAILED {doc['title']}: {doc['error']}")
            return 1
        if len(ready) < 3:
            print(f"expected 3 ready documents, got {len(ready)}")
            return 1

        page = get(f"/api/documents/{ready[0]['id']}/pages/1")
        print(f"page1 section={page['section']!r} chars={len(page['text'])}")
        print("OK")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

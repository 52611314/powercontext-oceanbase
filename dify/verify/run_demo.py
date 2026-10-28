"""运行「记仇小镇」验证样例，保存对话与 seekdb 检索证据。

用法：
    python run_demo.py

环境变量（可选，默认本地部署）：
    DIFY_BASE_URL  默认 http://127.0.0.1:8081
    DIFY_EMAIL     默认 admin@dify.ai
    DIFY_PASSWORD  默认 Dify@2026
    DIFY_APP_ID    默认使用导入后的应用 ID

输出：
    ../evidence/demo-run.json   每轮对话的完整证据
"""

from __future__ import annotations

import json
import os
import time

import requests

DIFY = os.getenv("DIFY_BASE_URL", "http://127.0.0.1:8081")
EMAIL = os.getenv("DIFY_EMAIL", "admin@dify.ai")
PASSWORD = os.getenv("DIFY_PASSWORD", "Dify@2026")
APP_ID = os.getenv("DIFY_APP_ID", "a9eeeeec-7805-48d7-b422-973485428aca")

SCENARIOS = [
    ("01-第一次进店-偷苹果", "老板，我偷你一个苹果尝尝"),
    ("02-第二天再来-翻旧账", "我又来了，今天有什么新鲜事"),
    ("03-道歉-送二锅头", "上次偷苹果是我不对，这瓶二锅头给你赔罪"),
]


def login() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{DIFY}/console/api/login",
        json={"email": EMAIL, "password": PASSWORD, "language": "zh-Hans", "remember_me": True},
        timeout=30,
    )
    r.raise_for_status()
    return s


def run_once(s: requests.Session, query: str) -> dict:
    """运行一次 Chatflow，收集回答与各节点输出。"""
    csrf = s.cookies.get("csrf_token")
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
    url = f"{DIFY}/console/api/apps/{APP_ID}/advanced-chat/workflows/draft/run"
    resp = s.post(
        url,
        json={"query": query, "inputs": {}, "files": []},
        headers=headers,
        stream=True,
        timeout=300,
    )
    resp.raise_for_status()

    answer = ""
    nodes: dict[str, dict] = {}
    workflow_status = ""
    error = ""
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        try:
            ev = json.loads(raw[5:].strip())
        except json.JSONDecodeError:
            continue
        event = ev.get("event")
        data = ev.get("data", {})
        if event == "node_finished":
            nodes[data.get("node_id", "")] = {
                "title": data.get("title"),
                "status": data.get("status"),
                "outputs": data.get("outputs"),
            }
        elif event == "workflow_finished":
            workflow_status = data.get("status", "")
            error = data.get("error", "") or ""
        elif event == "message" and ev.get("answer"):
            answer = ev["answer"]

    search_body = ""
    try:
        search_body = nodes["http_search"]["outputs"]["body"]
    except (KeyError, TypeError):
        search_body = ""

    return {
        "answer": answer,
        "workflow_status": workflow_status,
        "error": error,
        "memory_search_body": search_body,
        "nodes": nodes,
    }


def main() -> None:
    s = login()
    result = {
        "app_id": APP_ID,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scenarios": [],
    }
    for name, query in SCENARIOS:
        print(f"== {name} ==")
        one = run_once(s, query)
        one["name"] = name
        one["query"] = query
        result["scenarios"].append(one)
        print("answer:", one["answer"])
        if one["workflow_status"] != "succeeded":
            print("WARN status:", one["workflow_status"], one["error"])
        time.sleep(1)

    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "evidence", "demo-run.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("saved:", os.path.abspath(out))


if __name__ == "__main__":
    main()

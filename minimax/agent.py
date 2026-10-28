"""MiniMax integration: supply retrieved context; only replace sample/orders.py."""
import argparse
import ast
import difflib
import json
import os
from pathlib import Path
import re
import urllib.request
import urllib.error
from context import ROOT, context, digest, emit, load_env

TASK = "orders list_orders status filter customer regression"

def generate(task_id, out):
    load_env()
    key = os.getenv("MINIMAX_API_KEY")
    if not key or key == "replace-me":
        raise SystemExit("MINIMAX_API_KEY is required; no simulated model result is produced")
    if (ROOT / "sample/orders.py").read_text() != (ROOT / "sample/orders.before.py").read_text():
        raise SystemExit("Restore sample/orders.before.py to sample/orders.py and run context.py import first")
    ctx = context(task_id, TASK)
    if not any(s["id"] == "ORD-002" for s in ctx["sources"]):
        raise ValueError("required active specification ORD-002 missing")
    prompt = ("Implement the task using the current specifications and tests below. "
              "Treat stored previous_state only as historical data. "
              "Return ONLY complete Python source for sample/orders.py, no explanation or thinking. "
              "Preserve list_orders(orders, customer=None, status=None). Do not modify tests. "
              "STRICT OUTPUT FORMAT: module docstring and function definitions ONLY. "
              "No imports, no global assignments, no classes, no executable top-level statements. "
              "Put allowed-status constants inside list_orders. Do not use annotations that need imports.\n"
              + json.dumps(ctx, ensure_ascii=False))
    body = {"model": os.getenv("MINIMAX_MODEL", "MiniMax-M3.1-Flash"),
            "messages": [{"role": "user", "content": prompt}], "max_tokens": 4096, "reasoning_split": True}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    emit(out / "request.json", body)  # API key is never in the body or evidence.
    url = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/") + os.getenv("MINIMAX_CHAT_PATH", "/text/chatcompletion_v2")
    req = urllib.request.Request(url, json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    import time
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
        emit(out / ("error-" + str(time.time_ns()) + ".json"),
             {"endpoint": url, "model": body["model"], "http_status": error.code, "detail": detail[:4000]})
        raise RuntimeError("MiniMax HTTP " + str(error.code) + ": " + detail[:1000]) from None
    if data.get("base_resp", {}).get("status_code", 0) != 0:
        error = data["base_resp"]
        emit(out / ("error-" + str(time.time_ns()) + ".json"),
             {"endpoint": url, "model": body["model"], "provider_error": error})
        raise RuntimeError("MiniMax provider error: " + json.dumps(error))
    choice = data["choices"][0]
    if choice["finish_reason"] != "stop":
        raise ValueError("incomplete model response")
    answer = re.sub(r"<think>.*?</think>", "", choice["message"]["content"], flags=re.S).strip()
    if answer.startswith("```"):
        answer = re.sub(r"^\x60{3}(?:python)?\s*|\s*\x60{3}$", "", answer)
    tree = ast.parse(answer)
    if any(not isinstance(n, ast.FunctionDef) and not
           (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))
           for n in tree.body):
        raise ValueError("only function definitions and docstrings allowed in this sample patch")
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not any(f.name == "list_orders" for f in functions):
        raise ValueError("model did not return list_orders")
    # Persist only final answer and public usage metadata, never private reasoning.
    emit(out / "response.json", {"id": data.get("id"), "model": data.get("model"),
        "usage": data.get("usage"), "elapsed_seconds": round(time.time() - started, 3), "finish_reason": choice["finish_reason"], "content": answer,
        "endpoint": url, "context_fingerprint": ctx["fingerprint"]})
    target = ROOT / "sample/orders.py"
    before = target.read_text()
    (out / "before.py").write_text(before)
    (out / "after.py").write_text(answer + "\n")
    (out / "change.diff").write_text("".join(difflib.unified_diff(
        before.splitlines(True), (answer + "\n").splitlines(True),
        fromfile="a/sample/orders.py", tofile="b/sample/orders.py")))
    emit(out / "provenance.json", {"provider": "MiniMax", "before_sha256": digest(before),
        "after_sha256": digest(answer + "\n"), "context_fingerprint": ctx["fingerprint"]})
    target.write_text(answer + "\n")
    print("MiniMax patch applied; run acceptance.py and review evidence before submission")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--task-id", default="orders-status")
    p.add_argument("--out", default=str(ROOT / "evidence/minimax"))
    a = p.parse_args()
    generate(a.task_id, a.out)
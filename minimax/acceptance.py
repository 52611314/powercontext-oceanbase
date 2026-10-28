"""Reproducible acceptance: fixed tests, real DB, separate CLI processes."""
import argparse
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from context import ROOT, digest, emit, load_env

TASK = "orders list_orders status filter customer regression"

def execute(command, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=120)
    return {"command": command, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}

def business(source, out, label):
    with tempfile.TemporaryDirectory(prefix="minimax-tests-") as tmp:
        work = Path(tmp)
        shutil.copyfile(source, work / "orders.py")
        shutil.copyfile(ROOT / "sample/test_orders.py", work / "test_orders.py")
        result = execute([sys.executable, "-m", "unittest", "-v", "test_orders"], work, os.environ.copy())
        result["source_sha256"] = digest(source.read_text())
        result["tests_sha256"] = digest((ROOT / "sample/test_orders.py").read_text())
        result["git_base"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        emit(out / (label + ".json"), result)
        return result

def check_model(out):
    evidence = ROOT / "evidence/minimax"
    required = ["request.json", "response.json", "before.py", "after.py", "change.diff", "provenance.json"]
    if not all((evidence / p).is_file() for p in required):
        return {"status": "pending", "reason": "真实 MiniMax 调用记录尚未生成；参考实现不能代替模型参与"}
    request = json.loads((evidence / "request.json").read_text())
    response = json.loads((evidence / "response.json").read_text())
    provenance = json.loads((evidence / "provenance.json").read_text())
    expected = ROOT / "sample/orders.py"
    assert response["id"] and response["finish_reason"] == "stop"
    assert provenance["provider"] == "MiniMax"
    assert provenance["after_sha256"] == digest(expected.read_text())
    assert (evidence / "after.py").read_text() == expected.read_text()
    assert (evidence / "before.py").read_text() == (ROOT / "sample/orders.before.py").read_text()
    provided = json.loads(request["messages"][0]["content"].split("\n", 1)[1])
    sources = {s["id"]: s for s in provided["sources"]}
    assert {"ORD-001", "ORD-002", "sample/orders.py", "sample/test_orders.py"} <= sources.keys()
    assert "ORD-OLD" not in sources
    assert provided["fingerprint"] == provenance["context_fingerprint"] == response["context_fingerprint"]
    assert sources["sample/orders.py"]["content"] == (evidence / "before.py").read_text()
    assert sources["sample/test_orders.py"]["sha256"] == digest((ROOT / "sample/test_orders.py").read_text())
    assert provenance["before_sha256"] == digest((evidence / "before.py").read_text())
    assert response["content"].rstrip() + "\n" == expected.read_text()
    previous = provided["previous_state"]
    assert previous and previous["todo"] and previous["validation"]["exit_code"] != 0
    assert previous["fingerprint"] == provided["fingerprint"]
    assert previous["validation"]["source_sha256"] == provenance["before_sha256"]
    expected_diff = "".join(difflib.unified_diff((evidence / "before.py").read_text().splitlines(True),
        (evidence / "after.py").read_text().splitlines(True),
        fromfile="a/sample/orders.py", tofile="b/sample/orders.py"))
    assert (evidence / "change.diff").read_text() == expected_diff
    return {"status": "passed", "model": response["model"], "response_id": response["id"]}

def integration(out):
    # A separate database namespace and an empty PowerContext file prevent use of hidden state.
    with tempfile.TemporaryDirectory(prefix="minimax-context-") as tmp:
        work = Path(tmp)
        shutil.copyfile(ROOT / "context.py", work / "context.py")
        shutil.copytree(ROOT / "sample", work / "sample", ignore=shutil.ignore_patterns("__pycache__"))
        env = os.environ.copy()
        db_name = "minimax_verify_" + uuid.uuid4().hex[:12]
        env["DB_NAME"] = db_name
        calls = []
        def cli(*args):
            result = execute([sys.executable, "context.py", *args], work, env)
            calls.append(result)
            if result["exit_code"]:
                raise AssertionError(result["stderr"])
            return json.loads(result["stdout"])
        try:
            cli("import")
            first = cli("context", "--task-id", "orders-status", "--task", TASK)
            ids = {r["id"] for r in first["sources"]}
            assert {"ORD-001", "ORD-002", "sample/orders.py", "sample/test_orders.py"} <= ids
            assert "ORD-OLD" not in ids
            assert first["previous_state"] is None
            payload = {"done": ["status filter implemented", "7 tests passed"],
                       "todo": ["review patch"], "fingerprint": first["fingerprint"],
                       "validation": json.loads((out / "after-tests.json").read_text())}
            emit(work / "state.json", payload)
            cli("save", "--task-id", "orders-status", "--file", str(work / "state.json"))
            restored = cli("context", "--task-id", "orders-status", "--task", TASK)
            assert restored["previous_state"]["done"] == payload["done"]
            assert restored["validation_current"] is True
            assert restored["next_action"] == "continue from saved todo"
            other = cli("context", "--task-id", "different-task", "--task", TASK)
            assert other["previous_state"] is None and not other["validation_current"]
            # Spec change is genuine file edit and genuine DB re-import.
            spec_path = work / "sample/spec.json"
            spec = json.loads(spec_path.read_text())
            spec["revision"] = 2
            for item in spec["items"]:
                if item["id"] == "ORD-002":
                    item["text"] += " Revision 2: also allow refunded as an exact status."
            emit(spec_path, spec)
            cli("import")
            updated = cli("context", "--task-id", "orders-status", "--task", TASK)
            rule = next(r for r in updated["sources"] if r["id"] == "ORD-002")
            assert "refunded" in rule["content"]
            assert updated["validation_current"] is False
            assert updated["fingerprint"] != first["fingerprint"]
            # Editing code after import must be detected rather than silently using stale contents.
            with (work / "sample/orders.py").open("a") as stream:
                stream.write("\n# version changed\n")
            stale = execute([sys.executable, "context.py", "context", "--task-id", "orders-status", "--task", TASK], work, env)
            calls.append(stale)
            assert stale["exit_code"] != 0 and "index stale" in stale["stderr"]
            cli("import")
            changed = cli("context", "--task-id", "orders-status", "--task", TASK)
            assert changed["validation_current"] is False
            emit(out / "context-initial.json", first)
            emit(out / "context-resumed.json", restored)
            emit(out / "context-updated.json", updated)
            emit(out / "integration.json", {"status": "passed", "isolated_database": db_name,
                "checks": ["necessary sources", "superseded excluded", "empty initial state",
                           "restart recovery", "state used", "spec update", "validation invalidation", "stale import detection"],
                "calls": calls})
            shutil.copyfile(work / ".runtime/events.jsonl", out / "technology-events.jsonl")
        finally:
            # Drop only this uniquely named test database, never the user's production index.
            import pymysql
            conn = pymysql.connect(host=env.get("DB_HOST", "127.0.0.1"),
                port=int(env.get("DB_PORT", "2881")), user=env.get("DB_USER", "root"),
                password=env.get("DB_PASSWORD", ""))
            try:
                with conn.cursor() as cur:
                    cur.execute("DROP DATABASE IF EXISTS " + db_name)
            finally:
                conn.close()

def main():
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="evidence")
    p.add_argument("--require-model", action="store_true")
    a = p.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    before = business(ROOT / "sample/orders.before.py", out, "before-tests")
    after = business(ROOT / "sample/orders.py", out, "after-tests")
    assert before["exit_code"] != 0 and "FAIL:" in before["stderr"]
    assert "ERROR:" not in before["stderr"], "baseline must fail on behavior, not environment"
    for name in ("test_customer", "test_default_sorted", "test_empty", "test_no_mutation"):
        assert "Regression." + name + ") ... ok" in before["stderr"], "baseline regression failed"
    assert after["exit_code"] == 0, after["stderr"]
    assert before["tests_sha256"] == after["tests_sha256"]
    (out / "orders.diff").write_text("".join(difflib.unified_diff(
        (ROOT / "sample/orders.before.py").read_text().splitlines(True),
        (ROOT / "sample/orders.py").read_text().splitlines(True),
        fromfile="a/sample/orders.py", tofile="b/sample/orders.py")))
    shutil.copyfile(ROOT / "sample/orders.py", out / "orders.after.py")
    integration(out)
    model = check_model(out)
    summary = {"time_ns": time.time_ns(), "runnable": "passed", "context": "passed",
        "database": "passed", "state_reuse": "passed", "code_change": "passed",
        "minimax": model, "all_required_passed": model["status"] == "passed"}
    emit(out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if a.require_model and not summary["all_required_passed"]:
        raise SystemExit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        chosen_out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "evidence"
        emit(ROOT / chosen_out / "summary.json", {"all_required_passed": False,
             "error": type(error).__name__ + ": " + str(error)})
        raise
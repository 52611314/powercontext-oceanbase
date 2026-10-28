"""Run the real development workflow, or verify delivered model evidence offline."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from context import ROOT, emit, load_env

TASK = "orders list_orders status filter customer regression"

def main():
    load_env()
    p = argparse.ArgumentParser()
    p.add_argument("--generate", action="store_true", help="restore baseline and call MiniMax; requires API quota")
    args = p.parse_args()
    out = ROOT / "evidence"
    out.mkdir(exist_ok=True)
    commands = []
    def run(*command):
        result = subprocess.run([sys.executable, *command], cwd=ROOT, text=True,
                                capture_output=True, timeout=240)
        commands.append({"command": [sys.executable, *command], "exit_code": result.returncode,
                         "stdout": result.stdout, "stderr": result.stderr})
        emit(out / "workflow.json", {"time_ns": time.time_ns(), "calls": commands})
        if result.returncode:
            raise RuntimeError("Command failed: " + " ".join(command) + "\n" + result.stderr)
        return result.stdout
    def ctx(filename):
        result = json.loads(run("context.py", "context", "--task-id", "orders-status", "--task", TASK))
        emit(out / filename, result)
        return result
    if args.generate:
        if not os.getenv("MINIMAX_API_KEY") or os.getenv("MINIMAX_API_KEY") == "replace-me":
            raise SystemExit("Configure MINIMAX_API_KEY before generating")
        backup = ROOT / ".runtime/pre-model-orders.py"
        backup.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "sample/orders.py", backup)
        shutil.copyfile(ROOT / "sample/orders.before.py", ROOT / "sample/orders.py")
        run("context.py", "import")
        initial = ctx("task-before.json")
        from acceptance import business
        baseline = business(ROOT / "sample/orders.py", out, "development-before-tests")
        assert baseline["exit_code"] != 0 and "FAIL:" in baseline["stderr"] and "ERROR:" not in baseline["stderr"]
        pending = {"done": ["imported current specifications, source and tests", "confirmed target assertions fail before modification"],
                   "todo": ["implement ORD-002 using MiniMax", "run fixed target and regression tests"],
                   "validation": baseline, "fingerprint": initial["fingerprint"]}
        emit(ROOT / ".runtime/pending.json", pending)
        run("context.py", "save", "--task-id", "orders-status", "--file", str(ROOT / ".runtime/pending.json"))
        run("agent.py", "--task-id", "orders-status")
    run("context.py", "import")
    run("acceptance.py", "--require-model")
    current = ctx("task-after.json")
    completed = {"done": ["MiniMax implemented ORD-002", "fixed target and regression tests passed",
                           "all six acceptance items passed"],
                 "todo": [], "validation": json.loads((out / "after-tests.json").read_text()),
                 "fingerprint": current["fingerprint"]}
    emit(ROOT / ".runtime/completed.json", completed)
    run("context.py", "save", "--task-id", "orders-status", "--file", str(ROOT / ".runtime/completed.json"))
    resumed = ctx("task-resumed.json")
    assert resumed["validation_current"] and resumed["previous_state"]["done"] == completed["done"]
    shutil.copyfile(ROOT / ".runtime/events.jsonl", out / "task-events.jsonl")
    print("All six checks passed; real task state saved and reused in a new process. No Git commit or push.")

if __name__ == "__main__":
    main()
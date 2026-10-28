"""Task-specific engineering context backed by seekdb and real PowerContext."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parent

def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def emit(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

def audit(event, **data):
    path = ROOT / ".runtime/events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps({"time_ns": time.time_ns(), "event": event, **data}, ensure_ascii=False) + "\n")

class Index:
    def __init__(self):
        import pymysql
        name = os.getenv("DB_NAME", "minimax_sdd")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", name):
            raise ValueError("invalid DB_NAME")
        self.db = pymysql.connect(
            host=os.getenv("DB_HOST", "127.0.0.1"), port=int(os.getenv("DB_PORT", "2881")),
            user=os.getenv("DB_USER", "root"), password=os.getenv("DB_PASSWORD", ""),
            charset="utf8mb4", autocommit=False,
            cursorclass=pymysql.cursors.DictCursor, connect_timeout=10)
        with self.db.cursor() as cur:
            cur.execute("SELECT VERSION() AS version")
            version = cur.fetchone()["version"]
            if not any(s in version.lower() for s in ("oceanbase", "seekdb")):
                raise ValueError("seekdb or OceanBase required; got " + version)
            audit("database_connection", version=version, database=name)
            cur.execute("CREATE DATABASE IF NOT EXISTS " + name)
            cur.execute("USE " + name)
            cur.execute("""CREATE TABLE IF NOT EXISTS engineering_docs (
                id VARCHAR(255) PRIMARY KEY, kind VARCHAR(16) NOT NULL,
                path VARCHAR(255) NOT NULL, content LONGTEXT NOT NULL,
                sha256 CHAR(64) NOT NULL, links TEXT NOT NULL,
                active INTEGER NOT NULL)""")

    def close(self):
        self.db.close()

    def ingest(self):
        spec = json.loads((ROOT / "sample/spec.json").read_text())
        ids, rows = set(), []
        for item in spec["items"]:
            if item["id"] in ids:
                raise ValueError("duplicate spec ID")
            ids.add(item["id"])
            for path in item["sources"]:
                if not (ROOT / path).is_file() or not (ROOT / path).resolve().is_relative_to(ROOT):
                    raise ValueError("invalid source: " + path)
            text = json.dumps({"revision": spec["revision"], **item}, ensure_ascii=False)
            rows.append((item["id"], "spec", "sample/spec.json#" + item["id"], text,
                         digest(text), json.dumps(item["sources"]), int(item["status"] == "active")))
        for path, kind in [("sample/orders.py", "code"), ("sample/test_orders.py", "test")]:
            text = (ROOT / path).read_text()
            rows.append((path, kind, path, text, digest(text), "[]", 1))
        manifest = (ROOT / "sample/spec.json").read_text()
        rows.append(("@manifest", "manifest", "sample/spec.json", "", digest(manifest), "[]", 0))
        try:
            with self.db.cursor() as cur:
                cur.execute("DELETE FROM engineering_docs")
                cur.executemany("INSERT INTO engineering_docs VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        audit("database_import", rows=[{"id": r[0], "sha256": r[4]} for r in rows])
        return {"imported": len(rows), "spec_revision": spec["revision"]}

    def select(self, task):
        with self.db.cursor() as cur:
            cur.execute("SELECT sha256 FROM engineering_docs WHERE id=%s", ("@manifest",))
            manifest = cur.fetchone()
        if not manifest or manifest["sha256"] != digest((ROOT / "sample/spec.json").read_text()):
            raise ValueError("spec index stale; run import")
        tokens = list(dict.fromkeys(re.findall(r"[\w]+", task.lower())))[:32]
        if not tokens:
            raise ValueError("task must contain searchable words")
        sql = "SELECT * FROM engineering_docs WHERE active=1 AND (" + " OR ".join(
            ["LOWER(content) LIKE %s"] * len(tokens)) + ") ORDER BY id"
        params = ["%" + token.replace("_", "\\_").replace("%", "\\%") + "%" for token in tokens]
        with self.db.cursor() as cur:
            cur.execute(sql, params)
            seeds = cur.fetchall()
            links = sorted({p for row in seeds if row["kind"] == "spec" for p in json.loads(row["links"])})
            if links:
                cur.execute("SELECT * FROM engineering_docs WHERE active=1 AND (path IN (" +
                            ",".join(["%s"] * len(links)) + ") OR kind='spec') ORDER BY id", links)
                linked = [r for r in cur.fetchall() if r["path"] in links or
                          bool(set(json.loads(r["links"])) & set(links))]
            else:
                linked = []
        rows = {r["id"]: r for r in seeds + linked}
        result = list(rows.values())
        # A stale import must never masquerade as current workspace context.
        for row in result:
            if row["kind"] != "spec" and digest((ROOT / row["path"]).read_text()) != row["sha256"]:
                raise ValueError("index stale; run import: " + row["path"])
        live = json.loads((ROOT / "sample/spec.json").read_text())
        for row in result:
            if row["kind"] == "spec":
                current = next((i for i in live["items"] if i["id"] == row["id"]), None)
                text = json.dumps({"revision": live["revision"], **current}, ensure_ascii=False) if current else ""
                if digest(text) != row["sha256"]:
                    raise ValueError("spec index stale; run import")
        audit("database_query", sql=sql, parameters=params, dependency_paths=links,
              results=[{"id": r["id"], "sha256": r["sha256"]} for r in result])
        return result

async def state(task_id, value=None):
    if not task_id.strip():
        raise ValueError("task_id must not be empty")
    if value is not None:
        if not isinstance(value, dict) or not all(isinstance(value.get(k), list) and
                all(isinstance(v, str) for v in value[k]) for k in ("done", "todo")):
            raise ValueError("state requires done and todo string lists")
        if not isinstance(value.get("validation"), dict) or not re.fullmatch(r"[a-f0-9]{64}", value.get("fingerprint", "")):
            raise ValueError("state requires validation object and a context fingerprint")
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
    runtime_dir = ROOT / ".runtime"
    runtime_dir.mkdir(exist_ok=True)
    config = BuiltinConfig(database={"kind": "sqlite", "url": "sqlite+aiosqlite:///" + str(runtime_dir / "powercontext.db")})
    async with open_builtin_runtime(config, scheduler_path=runtime_dir / "scheduler.db") as runtime:
        scope = await runtime.scopes.create(ScopeDraft(title="MiniMax SDD",
            summary="Task progress and verification evidence", idempotency_key="minimax-sdd-v1"))
        memory = runtime.atomic_memory.for_scope(scope.scope_id)
        kind = "task-" + digest(task_id)[:24]
        if value is not None:
            payload = {**value, "task_id": task_id, "saved_ns": time.time_ns()}
            result = await memory.create([AtomicMemoryContent(kind=kind, text=json.dumps(payload, ensure_ascii=False))])
            audit("powercontext_write", scope_id=scope.scope_id, task_id=task_id,
                  ref=result.records[0].ref.model_dump(mode="json"), state=payload)
            return payload
        cursor, candidates = None, []
        while True:
            page = await memory.list(kind=kind, limit=50, cursor=cursor)
            candidates.extend(json.loads(r.artifact.content.text) for r in page.items)
            cursor = page.next_cursor
            if not cursor:
                break
        result = max(candidates, key=lambda r: r["saved_ns"]) if candidates else None
        audit("powercontext_read", scope_id=scope.scope_id, task_id=task_id, state=result)
        return result

def context(task_id, task):
    index = Index()
    try:
        docs = index.select(task)
    finally:
        index.close()
    fingerprint = digest(json.dumps(sorted((r["id"], r["sha256"]) for r in docs)))
    previous = asyncio.run(state(task_id))
    fresh = bool(previous and previous.get("fingerprint") == fingerprint)
    return {"task_id": task_id, "task": task, "fingerprint": fingerprint,
            "sources": docs, "previous_state": previous,
            "validation_current": fresh and previous.get("validation", {}).get("exit_code") == 0,
            "next_action": ("continue from saved todo" if fresh else "review current sources and rerun validation")}

def main():
    load_env()
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import")
    c = sub.add_parser("context")
    c.add_argument("--task-id", required=True)
    c.add_argument("--task", required=True)
    c.add_argument("--out")
    s = sub.add_parser("save")
    s.add_argument("--task-id", required=True)
    s.add_argument("--file", required=True, help="JSON with done, todo, validation, fingerprint")
    args = parser.parse_args()
    if args.command == "import":
        index = Index()
        try:
            result = index.ingest()
        finally:
            index.close()
    elif args.command == "save":
        result = asyncio.run(state(args.task_id, json.loads(Path(args.file).read_text())))
    else:
        result = context(args.task_id, args.task)
        if args.out:
            emit(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
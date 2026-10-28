from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import store
from ..db import ping
from ..embedding import embedder

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/ready")
def ready():
    checks: dict[str, str] = {}
    try:
        ping()
        checks["seekdb"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["seekdb"] = f"error: {exc}"
    try:
        embedder.load()
        checks["embedding"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["embedding"] = f"error: {exc}"
    ok = all(value == "ok" for value in checks.values())
    if not ok:
        raise HTTPException(status_code=503, detail={"code": "NOT_READY", "message": checks})
    return {"ok": True, "checks": checks, "dim": embedder.dim}


@router.get("/stats")
def stats():
    return store.stats()

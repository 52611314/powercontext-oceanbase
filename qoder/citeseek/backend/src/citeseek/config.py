from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    seekdb_host: str = os.getenv("CITESEK_SEEKDB_HOST", "127.0.0.1")
    seekdb_port: int = _env_int("CITESEK_SEEKDB_PORT", 2881)
    seekdb_user: str = os.getenv("CITESEK_SEEKDB_USER", "root")
    seekdb_password: str = os.getenv("CITESEK_SEEKDB_PASSWORD", "")
    seekdb_db: str = os.getenv("CITESEK_SEEKDB_DB", "citeseek")

    model_dir: Path = Path(
        os.getenv("CITESEK_MODEL_DIR", str(BACKEND_DIR / "models" / "bge-small-zh-v1.5"))
    )
    data_dir: Path = Path(os.getenv("CITESEK_DATA_DIR", str(PROJECT_DIR / "data")))
    web_dir: Path = Path(os.getenv("CITESEK_WEB_DIR", str(PROJECT_DIR / "frontend" / "dist")))

    max_upload_mb: int = _env_int("CITESEK_MAX_UPLOAD_MB", 100)
    chunk_size: int = _env_int("CITESEK_CHUNK_SIZE", 420)
    chunk_overlap: int = _env_int("CITESEK_CHUNK_OVERLAP", 80)
    embed_batch: int = _env_int("CITESEK_EMBED_BATCH", 16)
    index_workers: int = _env_int("CITESEK_INDEX_WORKERS", 2)

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


settings = Settings()


def upload_path(file_path: str) -> Path:
    """Stored paths may come from a container or another host; only the file name is stable."""
    return settings.upload_dir / Path(file_path).name

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppConfig:
    database_path: Path
    max_workers: int
    request_timeout: int
    chunk_size: int
    debug: bool
    secret_key: str


def load_config() -> AppConfig:
    base_dir = Path(os.environ.get("MARKSCHECKER_BASE_DIR", "/opt/markschecker3")).expanduser()
    db_path = Path(os.environ.get("MARKSCHECKER_DB_PATH", base_dir / "data" / "markschecker.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        database_path=db_path,
        max_workers=int(os.environ.get("MARKSCHECKER_MAX_WORKERS", "4")),
        request_timeout=int(os.environ.get("MARKSCHECKER_REQUEST_TIMEOUT", "15")),
        chunk_size=int(os.environ.get("MARKSCHECKER_CHUNK_SIZE", "400")),
        debug=os.environ.get("MARKSCHECKER_DEBUG", "false").lower() == "true",
        secret_key=os.environ.get("MARKSCHECKER_SECRET", "change-me"),
    )

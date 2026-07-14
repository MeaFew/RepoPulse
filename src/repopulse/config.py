from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from environment variables."""

    repository: str = "duckdb/duckdb"
    db_path: Path = PROJECT_ROOT / "data" / "processed" / "repopulse.duckdb"
    snapshot_path: Path = PROJECT_ROOT / "data" / "snapshot" / "repopulse.duckdb"
    github_token: str | None = None
    max_pages: int = 10
    demo_mode: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        raw_db_path = Path(os.getenv("REPOPULSE_DB_PATH", "data/processed/repopulse.duckdb"))
        db_path = raw_db_path if raw_db_path.is_absolute() else PROJECT_ROOT / raw_db_path
        raw_snapshot_path = Path(
            os.getenv("REPOPULSE_SNAPSHOT_PATH", "data/snapshot/repopulse.duckdb")
        )
        snapshot_path = (
            raw_snapshot_path
            if raw_snapshot_path.is_absolute()
            else PROJECT_ROOT / raw_snapshot_path
        )
        return cls(
            repository=os.getenv("REPOPULSE_REPOSITORY", "duckdb/duckdb"),
            db_path=db_path,
            snapshot_path=snapshot_path,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            max_pages=max(1, int(os.getenv("REPOPULSE_MAX_PAGES", "10"))),
            demo_mode=os.getenv("REPOPULSE_DEMO_MODE", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )


def validate_repository(repository: str) -> str:
    """Return a normalized owner/name identifier or raise a helpful error."""
    value = repository.strip().strip("/")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("仓库名称必须使用 owner/repository 格式，例如 duckdb/duckdb")
    if any(part in {".", ".."} for part in parts):
        raise ValueError("仓库名称包含非法路径片段")
    return value

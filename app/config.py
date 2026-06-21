"""Application settings (ADR D1, D6 dev demo mode).

All knobs are env-overridable so the same code runs on SQLite locally and
Postgres in production with no edits.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = parent of the app/ package.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEALPREP_", env_file=".env", extra="ignore")

    # Persistence (ADR D1): SQLite by default, swap to Postgres via env only.
    database_url: str = f"sqlite:///{(BASE_DIR / 'dealprep.db').as_posix()}"

    # Tenant-namespaced output root (ADR D7).
    data_dir: Path = BASE_DIR / "data"

    # Scheduler (ADR D6): dev demo mode clamps poll intervals so a scheduled
    # run produces output quickly during the curl walkthrough.
    dev_mode: bool = True
    dev_min_interval_seconds: int = 10

    @property
    def data_dir_path(self) -> Path:
        return Path(self.data_dir)


settings = Settings()

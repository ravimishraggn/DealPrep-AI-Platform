"""File upload connector — watches a directory/blob prefix for new files
(requirement 2b).

Self-contained plugin. For V1 this watches a local directory; the same interface
extends to an S3/GCS prefix by swapping the listing logic, with credentials
resolved from the vault via secret_ref.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.registry import register_connector
from connectors.base import BaseConnector, ConnectorError


class FileUploadConfig(BaseModel):
    # Local directory (or, later, blob prefix) to watch.
    directory: str
    glob: str = "*"
    poll_interval_seconds: int = Field(default=300, ge=1)
    # Optional: present for parity with cloud blob backends that need creds.
    secret_ref: str | None = None
    # Parse *.json files into structured records; otherwise emit file metadata.
    parse_json: bool = True
    max_files_per_run: int = Field(default=1000, ge=1)


@register_connector("file_upload")
class FileUploadConnector(BaseConnector):
    config_schema = FileUploadConfig
    config: FileUploadConfig  # type: ignore[assignment]

    def _dir(self) -> Path:
        return Path(self.config.directory).expanduser()

    def test_connection(self) -> None:
        path = self._dir()
        if not path.exists():
            raise ConnectorError(f"directory does not exist: {path}")
        if not path.is_dir():
            raise ConnectorError(f"not a directory: {path}")
        try:
            next(path.iterdir(), None)  # confirm it's listable
        except OSError as exc:
            raise ConnectorError(f"directory not readable: {path} ({exc})") from exc

    def fetch(self, since_timestamp: datetime | None) -> list[dict[str, Any]]:
        cfg = self.config
        path = self._dir()
        if not path.is_dir():
            raise ConnectorError(f"directory unavailable: {path}")

        records: list[dict[str, Any]] = []
        files = sorted(p for p in path.glob(cfg.glob) if p.is_file())
        for file in files[: cfg.max_files_per_run]:
            mtime = datetime.fromtimestamp(file.stat().st_mtime, tz=timezone.utc)
            if since_timestamp is not None and mtime < since_timestamp:
                continue  # already ingested in a prior run (new-files-only watch)
            records.append(self._read(file, mtime))
        return records

    def _read(self, file: Path, mtime: datetime) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "filename": file.name,
            "path": str(file),
            "size_bytes": file.stat().st_size,
            "modified_at": mtime.isoformat(),
        }
        if self.config.parse_json and file.suffix.lower() == ".json":
            try:
                meta["content"] = json.loads(file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                meta["parse_error"] = str(exc)
        return meta

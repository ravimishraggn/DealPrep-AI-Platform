"""Tenant-isolated output writer (ADR D7, requirement: isolation at write level).

This is the ONLY path that writes ingested data to disk. It is constructed bound
to a single tenant_id and refuses to write anywhere outside that tenant's
namespace — so isolation holds even if a connector or manifest is buggy/hostile.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.identifiers import slugify


class TenantIsolationError(RuntimeError):
    """Raised if a write would escape the tenant's namespace."""


class TenantOutputWriter:
    def __init__(self, tenant_id: str, data_dir: Path | None = None) -> None:
        if not tenant_id or "/" in tenant_id or "\\" in tenant_id or ".." in tenant_id:
            raise TenantIsolationError(f"invalid tenant_id for writer: {tenant_id!r}")
        self.tenant_id = tenant_id
        self._root = (data_dir or settings.data_dir_path).resolve()
        self._tenant_root = (self._root / tenant_id).resolve()
        # Defense in depth: the tenant root must sit inside the data root.
        if not self._is_within(self._tenant_root, self._root):
            raise TenantIsolationError("tenant root escapes data root")

    @staticmethod
    def _is_within(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    def write(self, source_id: str, records: list[dict[str, Any]]) -> str:
        """Write a run's records as one JSON file under the tenant namespace.

        Returns the output path. The path is recomputed from tenant_id every time
        and re-checked, so no caller can redirect the write elsewhere.
        """
        safe_source = slugify(source_id)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        target = (self._tenant_root / safe_source / f"{ts}.json").resolve()
        if not self._is_within(target, self._tenant_root):
            raise TenantIsolationError(f"write target escapes tenant namespace: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tenant_id": self.tenant_id,
            "source_id": source_id,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "records": records,
        }
        target.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return str(target)

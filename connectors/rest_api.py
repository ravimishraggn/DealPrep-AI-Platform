"""REST API connector — polls a paginated JSON endpoint (requirement 2a).

Self-contained plugin: defines its own config schema and registers itself. The
core engine never imports this module by name — auto-discovery finds it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.registry import register_connector
from app.secrets import SecretNotFoundError
from connectors.base import BaseConnector, ConnectorError


class RestApiConfig(BaseModel):
    base_url: HttpUrl
    auth_type: Literal["none", "bearer", "api_key"] = "none"
    # Reference to a secret name in the vault — never the raw credential (ADR D5).
    secret_ref: str | None = None
    api_key_header: str = "X-API-Key"
    poll_interval_seconds: int = Field(default=300, ge=1)

    # Pagination
    page_param: str = "page"
    page_size_param: str | None = "per_page"
    page_size: int = Field(default=100, ge=1, le=1000)
    start_page: int = 1
    max_pages: int = Field(default=50, ge=1, le=10_000)

    # Where the record list lives in the response, e.g. "data.items" (dot path);
    # empty string means the response body itself is the list.
    records_path: str = ""
    # Optional incremental support: server-side since-filter and/or client field.
    since_param: str | None = None
    timestamp_field: str | None = None

    timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _auth_needs_secret(self) -> "RestApiConfig":
        if self.auth_type != "none" and not self.secret_ref:
            raise ValueError(f"auth_type '{self.auth_type}' requires a secret_ref")
        return self


def _dig(payload: Any, dotted_path: str) -> Any:
    if not dotted_path:
        return payload
    node = payload
    for key in dotted_path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConnectorError(f"records_path '{dotted_path}' not found in response")
        node = node[key]
    return node


@register_connector("rest_api")
class RestApiConnector(BaseConnector):
    config_schema = RestApiConfig
    config: RestApiConfig  # type: ignore[assignment]

    def _headers(self) -> dict[str, str]:
        cfg = self.config
        if cfg.auth_type == "none":
            return {}
        try:
            secret = self.vault.get_secret(cfg.secret_ref)  # type: ignore[arg-type]
        except SecretNotFoundError as exc:
            raise ConnectorError(f"secret_ref '{cfg.secret_ref}' not found in vault") from exc
        if cfg.auth_type == "bearer":
            return {"Authorization": f"Bearer {secret}"}
        return {cfg.api_key_header: secret}

    def test_connection(self) -> None:
        """Dry-run: fetch the first page and confirm it yields a record list."""
        cfg = self.config
        params: dict[str, Any] = {cfg.page_param: cfg.start_page}
        if cfg.page_size_param:
            params[cfg.page_size_param] = cfg.page_size
        try:
            resp = httpx.get(
                str(cfg.base_url), params=params, headers=self._headers(),
                timeout=cfg.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"could not reach {cfg.base_url}: {exc}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(f"endpoint returned HTTP {resp.status_code}")
        try:
            records = _dig(resp.json(), cfg.records_path)
        except ValueError as exc:
            raise ConnectorError(f"response was not valid JSON: {exc}") from exc
        if not isinstance(records, list):
            raise ConnectorError(f"expected a list at records_path '{cfg.records_path}'")

    def fetch(self, since_timestamp: datetime | None) -> list[dict[str, Any]]:
        cfg = self.config
        headers = self._headers()
        collected: list[dict[str, Any]] = []
        page = cfg.start_page
        with httpx.Client(timeout=cfg.timeout_seconds) as client:
            for _ in range(cfg.max_pages):
                params: dict[str, Any] = {cfg.page_param: page}
                if cfg.page_size_param:
                    params[cfg.page_size_param] = cfg.page_size
                if cfg.since_param and since_timestamp is not None:
                    params[cfg.since_param] = since_timestamp.isoformat()
                resp = client.get(str(cfg.base_url), params=params, headers=headers)
                if resp.status_code >= 400:
                    raise ConnectorError(f"endpoint returned HTTP {resp.status_code} on page {page}")
                records = _dig(resp.json(), cfg.records_path)
                if not isinstance(records, list) or not records:
                    break
                collected.extend(records)
                if len(records) < cfg.page_size:
                    break  # last (partial) page
                page += 1
        return self._filter_since(collected, since_timestamp)

    def _filter_since(
        self, records: list[dict[str, Any]], since: datetime | None
    ) -> list[dict[str, Any]]:
        # Client-side incremental filter when the server can't do it for us.
        field = self.config.timestamp_field
        if since is None or not field:
            return records
        kept: list[dict[str, Any]] = []
        for rec in records:
            raw = rec.get(field) if isinstance(rec, dict) else None
            if raw is None:
                kept.append(rec)
                continue
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                kept.append(rec)
                continue
            if ts >= since:
                kept.append(rec)
        return kept

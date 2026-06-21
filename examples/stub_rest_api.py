"""Tiny stand-in REST API for the demo / verification.

Serves a paginated JSON list at GET / so the RestApiConnector has something real
to poll without external network access.

Run:  python examples/stub_rest_api.py [port]
Then point a rest_api source at http://127.0.0.1:<port>/

Pagination: ?page=N&per_page=M. Page 1 returns M records; later pages are empty,
so the connector knows to stop. Optional bearer auth is accepted but not required.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PAGE_ONE = [
    {"id": 1, "company": "Acme Corp", "ev_ebitda": 11.2, "updated_at": "2026-06-20T09:00:00+00:00"},
    {"id": 2, "company": "Globex", "ev_ebitda": 9.8, "updated_at": "2026-06-20T10:30:00+00:00"},
    {"id": 3, "company": "Initech", "ev_ebitda": 13.5, "updated_at": "2026-06-21T08:15:00+00:00"},
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        qs = parse_qs(urlparse(self.path).query)
        page = int(qs.get("page", ["1"])[0])
        records = PAGE_ONE if page == 1 else []
        body = json.dumps({"data": records, "page": page}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence per-request logging
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()

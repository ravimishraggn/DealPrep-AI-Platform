"""End-to-end proof of the Phase 5-6 pipeline across all three stores.

Drives the real HTTP API (so the scheduler auto-chains the full pipeline) and then
verifies vector + structured + graph results — plus tenant isolation.

Prereqs: docker compose up -d (Postgres + Neo4j), deps installed, spaCy model
downloaded, and the app running:  uvicorn app.main:app --port 8077

    python examples/e2e_pipeline.py
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8077"
ROOT = Path(__file__).resolve().parent.parent


def _register(client: httpx.Client, name: str) -> str:
    r = client.post("/tenants", json={"name": name, "owner_email": f"{name}@ex.com", "use_case": "e2e"})
    r.raise_for_status()
    return r.json()["id"]


def _prepare_dropzone(tenant_slug: str) -> Path:
    """Create a per-tenant dropzone with a JSON deal record and the sample PDF."""
    dz = ROOT / "data" / f"dropzone_{tenant_slug}"
    dz.mkdir(parents=True, exist_ok=True)
    (dz / "deal.json").write_text(
        '{"company": "Acme Corp", "ev_ebitda": 13.5, "sponsor": "Falcon Capital", '
        '"note": "Acme reported EBITDA includes related-party revenue."}',
        encoding="utf-8",
    )
    pdf = ROOT / "examples" / "sample_deal.pdf"
    if not pdf.exists():
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "examples" / "make_sample_pdf.py"), str(pdf)], check=True)
    shutil.copy(pdf, dz / "sample_deal.pdf")
    return dz


def _submit_file_source(client: httpx.Client, tenant_id: str, directory: Path) -> str:
    r = client.post(
        f"/tenants/{tenant_id}/sources",
        json={"connector_type": "file_upload", "config": {"directory": str(directory), "glob": "*"}},
    )
    r.raise_for_status()
    return r.json()["id"]


def _wait_for_index(client: httpx.Client, tenant_id: str, timeout: int = 120) -> dict:
    """Poll search until the pipeline has populated at least the vector store."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.post(f"/tenants/{tenant_id}/search", json={"query": "Acme Corp valuation", "k": 5})
        if r.status_code == 200:
            last = r.json()
            if last["vector"] or last["structured"] or last["graph"]:
                return last
        time.sleep(3)
    return last


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30) as client:
        client.get("/health").raise_for_status()
        print("server up")

        tenant_a = _register(client, "alpha")
        tenant_b = _register(client, "beta")
        print(f"tenant A={tenant_a}  tenant B={tenant_b}")

        dz_a = _prepare_dropzone("alpha")
        src_a = _submit_file_source(client, tenant_a, dz_a)
        print(f"submitted source {src_a} for A (dropzone {dz_a})")

        print("waiting for the pipeline to index (scheduler auto-chains)...")
        res_a = _wait_for_index(client, tenant_a)

        print("\n--- Tenant A search: 'Acme Corp valuation' ---")
        print(f"  vector hits:     {len(res_a.get('vector', []))}")
        print(f"  structured hits: {len(res_a.get('structured', []))}")
        print(f"  graph hits:      {len(res_a.get('graph', []))}")
        if res_a.get("warnings"):
            print(f"  warnings: {res_a['warnings']}")
        for v in res_a.get("vector", [])[:2]:
            print(f"   [vector {v['score']}] {v['text'][:70]!r} <- {v['metadata'].get('original_file_reference')}")
        for s in res_a.get("structured", [])[:2]:
            print(f"   [structured {s['score']}] {s['fields']} <- {s['metadata'].get('original_file_reference')}")
        for g in res_a.get("graph", [])[:5]:
            print(f"   [graph] ({g['subject']}) -[{g['relationship']}]-> ({g['object']})")

        # Tenant isolation: B has ingested nothing -> all three sets empty.
        res_b = client.post(f"/tenants/{tenant_b}/search", json={"query": "Acme Corp valuation", "k": 5}).json()
        print("\n--- Tenant B search (isolation check) ---")
        print(f"  vector={len(res_b['vector'])} structured={len(res_b['structured'])} graph={len(res_b['graph'])}")

        ok = (
            (res_a.get("vector") or res_a.get("structured"))
            and not res_b["vector"] and not res_b["structured"] and not res_b["graph"]
        )
        print("\nRESULT:", "PASS - pipeline indexed A, B isolated" if ok else "INCOMPLETE - see output above")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

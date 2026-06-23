# ADR 0021 — MCP Tool Exposure (Model Context Protocol)

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering, Product |
| **Phase** | 9 — Production Integrations |
| **PRD reference** | §7 "MCP layer that standardizes how tools are exposed" (cross-cutting concern) |
| **Relates to** | ADR 0013 (multi-agent orchestration), ADR 0020 (authentication) |

---

## Context

### What is MCP?

**Model Context Protocol (MCP)** is an open standard (by Anthropic) that defines a JSON-RPC
interface between LLM applications and tools/data sources.  Think of it as USB-C for AI tools
— a universal connector so any LLM host (Claude Desktop, Cursor, a LangGraph agent, a custom
chatbot) can call your tools without you writing a custom integration for each one.

```
Without MCP:                         With MCP:
──────────────────────────────────   ──────────────────────────────────
Claude app   → custom code           Claude Desktop
Cursor       → custom code      →        ↕
LangGraph    → custom code           MCP Server (DealPrep)
Your chatbot → custom code               ↕
                                     LangGraph orchestrator
                                         ↕
                                     Any future LLM host
```

MCP clients (LLM apps) discover what tools a server offers by calling `tools/list`.  They
invoke tools with `tools/call`.  The protocol is transport-agnostic — it works over stdio
(local process) or HTTP/SSE (networked service).

### Why the PRD calls for it

The PRD architecture diagram lists MCP as a cross-cutting concern at the retrieval/tooling
layer.  Without it:

- A Claude Desktop user cannot directly call DealPrep's vector search or graph lookup
- A Cursor / VS Code copilot cannot query deal data alongside code
- Adding a new LLM host (a client's own model) requires a bespoke integration every time
- The agents inside DealPrep call our own indexers directly — there is no reusable contract

With an MCP server:

- Any MCP-compatible client (Claude Desktop, Claude Code, Cursor, Windsurf, LangChain) can
  discover and call DealPrep's tools
- The LangGraph agents can call the MCP tools via the standard protocol rather than
  importing Python modules directly — this decouples the agent layer from the indexer layer
- External partners (a client's own AI team) can build on top of DealPrep's tools without
  accessing the internal Python codebase

---

## Decision

Implement a **DealPrep MCP Server** that exposes the platform's retrieval and analysis tools
over the MCP protocol via HTTP/SSE transport.  The server runs as a separate endpoint on the
same FastAPI process (`/mcp`).

The MCP server wraps the **existing tools** — it does not duplicate logic.  Each MCP tool is a
thin adapter over an already-tested module.

### Transport choice: HTTP/SSE (not stdio)

| Transport | Use case | Reason |
|---|---|---|
| **stdio** | Local tools (embedded in Claude Desktop) | Runs as a subprocess — good for desktop tools |
| **HTTP/SSE** | Networked services, LangGraph agents, multi-user | Runs as a server — good for a platform |

DealPrep is a network service shared across tenants.  HTTP/SSE is the correct choice.  The
MCP server URL will be `http://localhost:8000/mcp` in dev, `https://dealprep.yourfirm.com/mcp`
in production.

---

## Tools to Expose

Five tools, one for each core capability:

### Tool 1: `search_documents`

Search the deal room's unstructured documents (vector store).

```json
{
  "name": "search_documents",
  "description": "Search deal room documents by semantic meaning. Use for qualitative questions: narrative context, risks, relationships, footnotes, management commentary. Returns ranked document excerpts with source citations.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural-language search query"
      },
      "k": {
        "type": "integer",
        "default": 5,
        "description": "Maximum number of results to return (1–20)"
      }
    },
    "required": ["query"]
  }
}
```

**Underlying call:** `VectorIndexer(tenant_id).search(query, k)`

**Example response:**

```json
{
  "results": [
    {
      "text": "In FY2024, Acme Corp paid $2.1M in management fees to Chen Capital Partners...",
      "score": 0.94,
      "source_file": "acme_cim.pdf",
      "page": 31,
      "chunk_id": "c-00014"
    }
  ],
  "tenant_id": "T-001",
  "tool": "search_documents"
}
```

---

### Tool 2: `query_financials`

Query structured financial data in natural language (NL→SQL via the semantic model).

```json
{
  "name": "query_financials",
  "description": "Query structured financial KPIs — revenue, EBITDA, margins, growth rates — using plain English. Returns computed results from the financial database. Use for any quantitative question.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "question": {
        "type": "string",
        "description": "Plain-English question about financial data"
      }
    },
    "required": ["question"]
  }
}
```

**Underlying call:** `StructuredAgent → SemanticModelAgent → SQL → Postgres`

**Example response:**

```json
{
  "question": "What is Acme Corp EBITDA margin for FY2024?",
  "sql_generated": "SELECT ROUND(ebitda_usd_m / revenue_usd_m * 100, 2) FROM financial_kpis WHERE company_name='Acme Corp' AND period='FY2024'",
  "result": [{"ebitda_margin_pct": 18.95}],
  "answer": "Acme Corp's EBITDA margin for FY2024 is 18.95%"
}
```

---

### Tool 3: `lookup_relationships`

Traverse the knowledge graph to find ownership chains, board overlaps, and related-party
connections.

```json
{
  "name": "lookup_relationships",
  "description": "Look up entity relationships in the knowledge graph. Use to find: who controls an entity, shared investors/board members between companies, related-party ownership chains. Returns entity connections with relationship types.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "entity": {
        "type": "string",
        "description": "Company name, person name, or entity to look up"
      },
      "depth": {
        "type": "integer",
        "default": 1,
        "description": "How many relationship hops to traverse (1 = direct, 2 = indirect)"
      }
    },
    "required": ["entity"]
  }
}
```

**Underlying call:** `Neo4jClient(tenant_id).find_relationships(entity, depth)`

**Example response:**

```json
{
  "entity": "James Chen",
  "relationships": [
    {"type": "HAS_CEO", "direction": "incoming", "from": "Acme Corp"},
    {"type": "CONTROLS", "direction": "outgoing", "to": "Chen Capital Partners"},
    {"type": "OWNS", "direction": "outgoing", "to": "Acme Properties LLC"}
  ],
  "depth": 1
}
```

---

### Tool 4: `run_analysis`

Run the full multi-agent analysis pipeline for a question.

```json
{
  "name": "run_analysis",
  "description": "Run a full AI-powered analysis on the deal — combines document search, financial queries, and graph lookups. Use when a question requires synthesising evidence from multiple sources. Returns a synthesised answer, risk score, and citations. May require human approval for high-risk results.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "question": {
        "type": "string",
        "description": "The analytical question to investigate"
      },
      "orchestrator": {
        "type": "string",
        "enum": ["sequential", "langgraph"],
        "default": "sequential"
      }
    },
    "required": ["question"]
  }
}
```

**Underlying call:** `get_orchestrator().analyze(ctx)` or `get_langgraph_orchestrator().analyze(ctx)`

---

### Tool 5: `list_deal_documents`

List all documents that have been ingested for this deal.

```json
{
  "name": "list_deal_documents",
  "description": "List all documents that have been ingested and indexed for this deal room. Returns file names, ingestion timestamps, and index status. Useful to understand what data is available before asking questions.",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

**Underlying call:** `GET /tenants/{id}/sources` + `GET /tenants/{id}/runs`

---

## Server Implementation

### Using `fastapi-mcp` (recommended)

`fastapi-mcp` is a library that auto-generates an MCP server from FastAPI routes.  The tool
handlers are regular FastAPI endpoints — no separate framework to learn.

```python
# app/mcp_server.py
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

def setup_mcp(app: FastAPI, mcp_app: FastAPI) -> None:
    """Mount an MCP server on /mcp that exposes DealPrep's retrieval tools."""
    mcp = FastApiMCP(
        mcp_app,
        name="DealPrep AI Platform",
        description="AI-powered deal room for M&A due diligence — vector search, financial queries, knowledge graph, and multi-agent analysis.",
        include_operations=["search_documents", "query_financials", "lookup_relationships",
                            "run_analysis", "list_deal_documents"],
    )
    mcp.mount()
```

### Defining tool endpoints

Each MCP tool is a FastAPI endpoint on a sub-app.  They look like normal API endpoints:

```python
# app/routers/mcp_tools.py

router = APIRouter(prefix="/tools", tags=["mcp-tools"])

@router.post("/search_documents")
async def search_documents_tool(
    query: str,
    k: int = 5,
    tenant_id: str = Depends(get_current_tenant),   # auth from ADR 0020
    db: Session = Depends(get_session),
):
    """MCP tool: Search deal documents by semantic meaning."""
    indexer = VectorIndexer(tenant_id=tenant_id)
    results = await asyncio.to_thread(indexer.search, query, k)
    return {
        "results": results,
        "tenant_id": tenant_id,
        "tool": "search_documents",
    }

@router.post("/query_financials")
async def query_financials_tool(
    question: str,
    tenant_id: str = Depends(get_current_tenant),
    db: Session = Depends(get_session),
):
    """MCP tool: Query structured financial data via NL→SQL."""
    agent = SemanticModelAgent()
    ctx = AnalysisContext(tenant_id=tenant_id, query=question)
    state = AnalysisState(context=ctx)
    result = await agent.run(state)
    return result.payload

# ... similar for lookup_relationships, run_analysis, list_deal_documents
```

### Wiring into main.py

```python
# app/main.py
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

app = FastAPI(title="DealPrep AI Platform")
mcp_app = FastAPI(title="DealPrep MCP Server")

# ... existing router includes ...

# Mount MCP server
mcp = FastApiMCP(mcp_app, name="DealPrep")
mcp.mount()
app.mount("/mcp", mcp_app)
```

After this, `GET http://localhost:8000/mcp` returns the MCP server info and `tools/list`.

---

## Authentication on MCP Tools

MCP tools use the same `get_current_tenant` dependency as every other endpoint (ADR 0020).

MCP clients include the API key in the HTTP headers:

```json
{
  "mcpServers": {
    "dealprep": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer dp_live_a3f8b2c1d4e5f6789012345678901234"
      }
    }
  }
}
```

This is the standard MCP HTTP auth pattern — no extra auth layer needed.

---

## Connecting from Claude Desktop

Once the MCP server is running, an analyst can connect Claude Desktop to their deal room:

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "acme-deal-room": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer dp_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
      }
    }
  }
}
```

After restarting Claude Desktop, the analyst sees DealPrep tools in the tool picker.  They can
type in the Claude Desktop chat:

> *"Use the deal room tools to find any related-party transactions in Acme Corp."*

Claude Desktop calls `lookup_relationships("Acme Corp")` and `search_documents("related party transactions")` automatically, then synthesises the answer.

---

## Connecting from LangGraph (Internal Agents)

The LangGraph orchestrator currently imports Python modules directly.  After the MCP server
is running, we can optionally switch to calling it via the MCP client — this decouples the
orchestration layer from the indexer implementations:

```python
# Option A (current): direct Python import
from pipeline.indexing.vector import VectorIndexer
chunks = await asyncio.to_thread(VectorIndexer(tenant_id).search, query, k)

# Option B (MCP): call the MCP server
from anthropic import Anthropic
client = Anthropic()
result = await mcp_client.call_tool("search_documents", {"query": query, "k": k})
chunks = result["results"]
```

Option A is faster (no HTTP round-trip) and stays as the default for internal agents.
Option B becomes available for external integrations.  Both paths produce identical output.

---

## MCP Tool Resource Exposure (future — Phase 10)

MCP also supports **Resources** (read-only context) and **Prompts** (reusable prompt templates).
Phase 10 can add:

- `Resource: deal_summary/{tenant_id}` — returns a Markdown summary of all ingested documents
  for a deal, ready to paste into any LLM context
- `Prompt: due_diligence_template` — a pre-built prompt template for common DD questions
- `Resource: risk_report/{tenant_id}/{session_id}` — returns a formatted risk report for a
  completed analysis

---

## File Plan

| File | Purpose |
|---|---|
| `app/mcp_server.py` | `setup_mcp(app, mcp_app)` — FastApiMCP wiring |
| `app/routers/mcp_tools.py` | Tool endpoint handlers (one per MCP tool) |
| `app/main.py` | Mount MCP sub-app at `/mcp` |
| `requirements.txt` | Add: `fastapi-mcp>=0.3` |
| `docs/mcp-quickstart.md` | 5-minute guide: how to connect Claude Desktop to DealPrep |

---

## Consequences

**Positive:**
- Any MCP-compatible client can call DealPrep tools without a bespoke integration.  This is a
  multiplier on the platform's reach — every analyst who uses Claude Desktop gets access
  without any extra engineering.
- The MCP server becomes the **canonical API surface** for the retrieval layer.  Adding a new
  LLM host takes 5 minutes (register the URL + key), not a sprint.
- The tool descriptions are human-readable and serve as living documentation of what the
  platform can do.

**Negative / Risks:**
- `fastapi-mcp` is a relatively new library (< 1 year old).  If it stops being maintained, the
  MCP server must be re-implemented directly.  Risk mitigated by the fact that MCP tools are
  thin wrappers — re-implementing them without the library is a < 1-day task.
- MCP over HTTP/SSE requires the server to be reachable from the client.  In air-gapped deal
  rooms, the server must be deployed locally (not cloud-hosted).  This is already the case for
  the rest of the platform.
- Exposing `run_analysis` as an MCP tool means an external LLM (Claude Desktop) can trigger
  the full orchestration pipeline.  The budget pre-check (ADR 0015/0016) and auth (ADR 0020)
  are the safeguards — ensure they are active before enabling the MCP server externally.

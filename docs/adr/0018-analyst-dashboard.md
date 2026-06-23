# ADR 0018 — Analyst Dashboard & Deal Room UI

| Field | Value |
|---|---|
| **Status** | Proposed |
| **Date** | 2026-06-23 |
| **Deciders** | Platform Engineering, Product |
| **Phase** | 9 — Production UI |
| **PRD reference** | §7 "Presentation layer — Chat UI, dashboard, Excel/PPT plugin" and §12 Phase 8 "Dashboard" |

---

## Context

The platform currently has a minimal static HTML page (`app/static/pipeline.html`) that lets a
developer call the API via a browser console. It was built for testing, not for use.

A real PE analyst cannot use the platform today. They need:

1. **Deal room view** — see all ingested documents for a deal, their status, when they were last
   synced, and how many chunks/entities were extracted.
2. **Query interface** — type a question, see the answer with citations, risk signals, and which
   agents contributed.
3. **HITL panel** — when an analysis is interrupted (risk ≥ 0.7), the analyst needs a clear UI to
   review findings and approve or reject with feedback.
4. **Session history** — see previous questions asked against this deal and their outcomes.
5. **Cost and usage view** — know how much LLM budget has been used this month.

### Why not a full .NET frontend yet?

The PRD calls for a .NET presentation tier long-term (ADR 0002 — polyglot allocation).  Building
a full .NET SPA now would require a separate repo, build pipeline, and deployment target before
the core AI functionality is proven.

Decision: build a **server-side rendered (SSR) dashboard using FastAPI + Jinja2 templates**.
This ships in the same Python process, requires no build step, and gives analysts a real working
UI.  When the .NET tier arrives (Phase 10+), it calls the same REST API; the Jinja2 pages become
the internal/debug view.

---

## Decision

Implement a **Jinja2-based analyst dashboard** served directly from FastAPI at `/ui/`.
No JavaScript framework, no build step, no CDN dependency.  HTMX is used for dynamic
interactions (partial page refresh without a full SPA) — it is a 15 KB JS file loaded from a
CDN fallback or bundled locally.

The dashboard is scoped to a single tenant at a time (`/ui/tenants/{tenant_id}/`).  No
cross-tenant views are exposed in the UI — tenant selection is at the URL level.

---

## Pages and Components

### Page 1 — Deal Room Overview (`GET /ui/tenants/{tenant_id}/`)

```
┌─────────────────────────────────────────────────────────────┐
│  🏢  Acme Corp   [Deal Room]            [⚙ Settings]  [📊 Usage] │
├──────────────────┬──────────────────────────────────────────┤
│  DOCUMENTS       │  PIPELINE STATUS                         │
│  ─────────────   │  ─────────────────────                   │
│  📄 acme_cim.pdf │  Vector store:  3,847 chunks  ✅         │
│     ingested 2h  │  Structured DB: 12 records    ✅         │
│  📊 financials.  │  Knowledge graph: 34 entities ✅         │
│     ingested 1d  │  Last run: 2026-06-23 12:14   ✅         │
│  📋 cap_table.   │                                          │
│     ingested 1d  │  [▶ Trigger sync now]                    │
│                  │                                          │
│  [+ Add source]  │                                          │
└──────────────────┴──────────────────────────────────────────┘
```

**Data sources:**
- `GET /tenants/{id}` — tenant name
- `GET /tenants/{id}/sources` — source list with last-run timestamps
- `GET /tenants/{id}/vector-stats` — chunk count
- `GET /tenants/{id}/inspect/structured` — record count
- `GET /tenants/{id}/inspect/graph/entities` — entity count

### Page 2 — Analysis Interface (`GET /ui/tenants/{tenant_id}/analyze`)

```
┌─────────────────────────────────────────────────────────────┐
│  Ask a question about this deal                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ What is Acme Corp's normalised EBITDA margin...       │  │
│  └───────────────────────────────────────────────────────┘  │
│  [Orchestrator: Sequential ▾]  [k: 5 ▾]   [  Analyse  ]    │
├─────────────────────────────────────────────────────────────┤
│  ANSWER                              RISK SIGNAL            │
│  ───────────────────────────────     ─────────────────────  │
│  Acme Corp's reported EBITDA         🟠 0.85  HIGH RISK     │
│  margin for FY2024 is 18.95%.        ────────────────────   │
│  ⚠ 2 related-party flags found.     • related_party edge   │
│  ...                                 • management fees kw   │
│                                      • entity overlap       │
│  CITATIONS                                                   │
│  ─────────────────────────────────────────────────────────  │
│  [acme_cim.pdf p.31] "paid $2.1M...    score: 0.94         │
│  [financial_kpis FY2024] ebitda: 21.3  score: 1.00         │
│                                                              │
│  AGENT TIMING                                               │
│  document_researcher: 320ms ✅  structured_agent: 180ms ✅  │
│  graph_agent: 410ms ✅  risk_scorer: 12ms  synthesis: 890ms │
└─────────────────────────────────────────────────────────────┘
```

The form posts via HTMX (`hx-post="/tenants/{id}/analyze"`, `hx-target="#result-panel"`).
The server renders a partial HTML fragment (not a full page) and swaps it in — no page reload.

### Page 3 — HITL Review Panel (interruption state)

When `interrupted: true` is returned, the analysis page transitions to a review panel:

```
┌─────────────────────────────────────────────────────────────┐
│  ⚠️  HUMAN REVIEW REQUIRED                                  │
│  ─────────────────────────────────────────────────────────  │
│  Risk score: 0.85  (threshold: 0.70)                        │
│                                                              │
│  Risk signals:                                               │
│  ● related_party edge in knowledge graph                    │
│  ● management fees keyword in 3 document chunks             │
│  ● entity overlap: James Chen in graph + structured data    │
│  ● recurring risk: 2 of 5 prior analyses flagged            │
│                                                              │
│  Review the evidence above, then:                           │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Your note to the synthesis agent (optional):        │    │
│  │ Add back the $2.1M management fee...                │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  [✅ Approve — proceed to synthesis]  [❌ Reject — abort]   │
└─────────────────────────────────────────────────────────────┘
```

The form posts to `POST /tenants/{id}/analyze/{session_id}/resume` via HTMX.

### Page 4 — Session History (`GET /ui/tenants/{tenant_id}/history`)

Table of previous analyses for this tenant, showing query, risk score (colour-coded), orchestrator,
and a link to the full answer.  Paginated at 20 per page.  Data from `GET /tenants/{id}/audit`.

### Page 5 — Usage & Cost (`GET /ui/tenants/{tenant_id}/usage`)

Bar chart (rendered as SVG, no external chart library) of LLM token spend by day for the current
month.  Model breakdown table.  Budget remaining indicator.
Data from `GET /tenants/{id}/usage?period=YYYY-MM`.

---

## Technology Choices

| Concern | Choice | Reason |
|---|---|---|
| Templating | Jinja2 (already in FastAPI ecosystem) | Zero new dependency |
| Dynamic interaction | HTMX 2.0 | Partial page updates without a full SPA; 15 KB |
| Styling | Plain CSS with CSS variables (reuse existing `--bg`, `--fg`, `--warn`) | No Tailwind/Bootstrap dependency |
| Charts | SVG rendered server-side in Jinja2 template | No ECharts/Chart.js dependency |
| No JS framework | intentional | Keeps the UI deployable anywhere without a Node build step |

HTMX dependency — loaded from bundled local file (not CDN) so the UI works in air-gapped
deal rooms:

```html
<!-- templates/base.html -->
<script src="/static/htmx.min.js"></script>  <!-- bundled in app/static/ -->
```

---

## Template Structure

```
app/
├── templates/
│   ├── base.html              ← navbar, CSS variables, HTMX script
│   ├── deal_room.html         ← Page 1: document + pipeline status
│   ├── analyze.html           ← Page 2: query form + result panel
│   ├── partials/
│   │   ├── result_panel.html  ← HTMX swap target: answer + citations
│   │   ├── hitl_panel.html    ← HTMX swap target: human review
│   │   └── agent_timings.html ← timing table partial
│   ├── history.html           ← Page 4: session history
│   └── usage.html             ← Page 5: cost/usage chart
│
├── routers/
│   └── ui.py                  ← GET /ui/* routes returning TemplateResponse
│
└── static/
    ├── htmx.min.js            ← bundled HTMX 2.0 (15 KB)
    ├── pipeline.html          ← keep existing developer console
    └── style.css              ← extend existing CSS variables
```

### New router: `app/routers/ui.py`

```python
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/ui", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

@router.get("/tenants/{tenant_id}", response_class=HTMLResponse)
async def deal_room(request: Request, tenant_id: str, db=Depends(get_session)):
    # fetch tenant + sources + store stats
    ...
    return templates.TemplateResponse("deal_room.html", {"request": request, ...})

@router.get("/tenants/{tenant_id}/analyze", response_class=HTMLResponse)
async def analyze_page(request: Request, tenant_id: str):
    return templates.TemplateResponse("analyze.html", {"request": request, "tenant_id": tenant_id})

@router.post("/tenants/{tenant_id}/analyze", response_class=HTMLResponse)
async def analyze_submit(request: Request, tenant_id: str, ...):
    # calls the same orchestrator as the JSON API
    # returns partials/result_panel.html or partials/hitl_panel.html
    ...
```

---

## URL Structure

```
/ui/                                   → redirect to tenant selection
/ui/tenants/{id}/                      → deal room overview
/ui/tenants/{id}/analyze               → analysis interface
/ui/tenants/{id}/analyze/{session_id}  → review a specific session (HITL or history)
/ui/tenants/{id}/history               → session history
/ui/tenants/{id}/usage                 → cost and usage
```

---

## Consequences

**Positive:**
- Analysts can use the platform without curl or OpenAPI — this removes the biggest adoption
  barrier for a non-technical user.
- HITL panel makes the human-review flow usable in practice — previously it required a raw
  `curl POST /resume` call.
- Server-side rendering is trivially cacheable (Nginx/CDN) and works in restricted corporate
  network environments.

**Negative / Risks:**
- HTMX is unfamiliar to most backend engineers.  Learning curve is low (it is HTML attributes),
  but the team should read the 20-minute HTMX guide before touching templates.
- Jinja2 templates are not type-checked — a missing template variable produces a 500 error, not
  a compile error.  Use `Optional` defaults in all template contexts.
- No real-time push (WebSocket / SSE).  Long LangGraph runs (> 5 s) will show a spinner until
  the server responds.  Phase 10 can add SSE streaming if needed.

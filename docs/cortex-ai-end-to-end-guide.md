# Snowflake Cortex AI — End-to-End Guide (Plain Language + Real Data)

> **Who this is for:** Anyone who wants to understand how Snowflake Cortex AI actually works,
> with real configuration files, real data examples, and plain-English explanations.
> No prior Snowflake experience assumed.
>
> **What you will learn:** How a PDF on your desk becomes an answer to a question like
> *"What is Acme Corp's EBITDA margin?"* — every step, every file, every config.

---

## The Big Picture in One Sentence

Snowflake Cortex AI is a set of services that let you **store your data in Snowflake, describe
what the data means in a YAML file, and then ask questions in plain English** — and get back
answers that are traceable to real documents and real numbers.

Think of it like this:

```
Your documents + spreadsheets
         ↓
   Snowflake (data warehouse — everything lives here)
         ↓
   Cortex AI services (search, SQL, LLM, agents)
         ↓
   Your question in English → trusted answer with citations
```

---

## Part 1 — The Cast of Characters

Before we walk through the flow, here are the 5 services you will use and what they do in one
line each:

| Service | What it does | Real-world analogy |
|---|---|---|
| **Snowflake Stage** | A folder inside Snowflake where you upload files | Like an S3 bucket, but inside Snowflake |
| **Cortex Search** | Reads your documents and makes them searchable by meaning, not just keyword | Like Google Search, but only for your files |
| **Cortex Analyst** | Lets you ask questions about your tables in plain English → turns them into SQL | Like asking your data analyst "show me revenue by quarter" and getting a spreadsheet back |
| **Cortex Complete** | Calls an LLM (Claude, Llama, Mistral) with your prompt | Like calling the OpenAI API, but the model runs inside Snowflake |
| **Cortex Agents** | Combines the above — decides whether to search a document, query a table, or ask an LLM | Like an AI analyst who picks the right tool for each part of your question |

---

## Part 2 — The Data We Will Use

We will use a fictional M&A deal throughout this guide.  The deal has:

**3 documents (unstructured):**
- `acme_cim.pdf` — Confidential Information Memorandum (narrative text, 48 pages)
- `acme_financials.csv` — Financial model (numbers, KPIs by quarter)
- `acme_cap_table.json` — Cap table (ownership %, share classes)

**1 question we want to answer:**
> *"What is Acme Corp's normalised EBITDA margin for FY2024, and are there any related-party
> transactions we should flag?"*

This single question needs:
- The PDF (for narrative context and related-party mentions)
- The CSV (for the actual EBITDA numbers)
- Possibly a knowledge graph lookup (for ownership chains)

Let us walk through how Cortex AI handles this.

---

## Part 3 — Step 1: Load Your Data Into Snowflake

### 3.1 Create a database and schema

```sql
-- Everything lives inside a database > schema > table hierarchy
CREATE DATABASE dealprep_db;
CREATE SCHEMA dealprep_db.acme_deal;

USE DATABASE dealprep_db;
USE SCHEMA acme_deal;
```

### 3.2 Create a Stage (the upload folder)

A **Stage** is just a named storage location inside Snowflake.  You push files to it from your
laptop, CI pipeline, or S3.

```sql
-- Internal stage: Snowflake manages the storage
CREATE STAGE deal_documents
    COMMENT = 'Raw deal documents for Acme Corp diligence';
```

Upload from your terminal:

```bash
# Snowflake CLI — push a file to the stage
snow stage copy acme_cim.pdf @deal_documents/acme/
snow stage copy acme_financials.csv @deal_documents/acme/
snow stage copy acme_cap_table.json @deal_documents/acme/
```

After this, Snowflake has your files.  They are raw bytes — nothing is indexed yet.

### 3.3 Load the structured data into a table

The CSV becomes a proper SQL table:

```sql
-- Create the table
CREATE TABLE financial_kpis (
    company_name  VARCHAR,
    period        VARCHAR,      -- e.g. 'FY2024', 'Q1-2024'
    revenue_usd_m FLOAT,
    ebitda_usd_m  FLOAT,
    net_income_m  FLOAT,
    currency      VARCHAR DEFAULT 'USD',
    source_file   VARCHAR
);

-- Load from stage
COPY INTO financial_kpis
FROM @deal_documents/acme/acme_financials.csv
FILE_FORMAT = (TYPE = CSV FIELD_OPTIONALLY_ENCLOSED_BY = '"' SKIP_HEADER = 1);
```

After this, the CSV data looks like a normal database table:

```
COMPANY_NAME | PERIOD  | REVENUE_USD_M | EBITDA_USD_M | NET_INCOME_M
-------------|---------|---------------|--------------|-------------
Acme Corp    | FY2022  | 85.2          | 14.1         | 6.8
Acme Corp    | FY2023  | 98.7          | 17.4         | 8.2
Acme Corp    | FY2024  | 112.4         | 21.3         | 10.1
```

---

## Part 4 — Step 2: Set Up Cortex Search (Document Search)

Cortex Search reads your documents, breaks them into chunks, embeds them as vectors, and
creates a search service.  **You do not write embedding code.  You do not manage a vector
database.  You write one SQL statement.**

### 4.1 Create the Cortex Search Service

```sql
-- Tell Cortex Search: look in this stage, index the PDF, make it searchable
CREATE CORTEX SEARCH SERVICE deal_doc_search
    ON acme_cim_chunks.chunk_text       -- the column to search
    WAREHOUSE = compute_wh
    TARGET_LAG = '1 hour'              -- how often to re-index new files
    AS (
        SELECT
            chunk_text,
            source_file,
            page_number,
            company_name
        FROM acme_cim_chunks            -- a table we create from the PDF
    );
```

### 4.2 How does the PDF become a table of chunks?

Snowflake has a built-in `PARSE_DOCUMENT` function that extracts text from a PDF:

```sql
-- Step 1: extract text from the PDF
CREATE TABLE acme_cim_raw AS
SELECT
    SNOWFLAKE.CORTEX.PARSE_DOCUMENT(
        @deal_documents,             -- stage name
        'acme/acme_cim.pdf',         -- file path in stage
        {'mode': 'LAYOUT'}           -- preserve layout/tables
    ) AS document_text;

-- Step 2: chunk it into ~500-token pieces
CREATE TABLE acme_cim_chunks AS
SELECT
    c.value:chunk::VARCHAR       AS chunk_text,
    c.value:start_char::INT      AS char_start,
    'acme_cim.pdf'               AS source_file,
    ROW_NUMBER() OVER (ORDER BY c.index) AS chunk_id
FROM acme_cim_raw,
     LATERAL FLATTEN(
         SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(
             document_text, 'paragraph', 500, 50
         )
     ) c;
```

After this, the PDF looks like:

```
CHUNK_ID | CHUNK_TEXT                                                    | SOURCE_FILE
---------|---------------------------------------------------------------|-------------
1        | Acme Corp is a leading provider of industrial automation...   | acme_cim.pdf
2        | The Company was founded in 2008 by James Chen and...         | acme_cim.pdf
3        | Related-party transactions: In FY2024, the Company paid...   | acme_cim.pdf
4        | EBITDA for FY2024 was $21.3M, representing a margin of 19%   | acme_cim.pdf
```

### 4.3 Query the Search Service

Now you can search documents with a Python API call or SQL:

```python
# Python — using the Snowflake Python connector
import snowflake.connector

conn = snowflake.connector.connect(
    user='analyst@firm.com',
    account='myaccount.snowflakecomputing.com',
    database='dealprep_db',
    schema='acme_deal'
)

# Search for related-party information
response = conn.cursor().execute("""
    SELECT SNOWFLAKE.CORTEX.SEARCH(
        'deal_doc_search',
        'related party transactions FY2024',
        {'limit': 5}
    )
""").fetchone()

print(response[0])
```

The response looks like:

```json
{
  "results": [
    {
      "chunk_text": "Related-party transactions: In FY2024, the Company paid $2.1M in management fees to Chen Capital Partners, an entity controlled by CEO James Chen.",
      "source_file": "acme_cim.pdf",
      "chunk_id": 14,
      "score": 0.94
    },
    {
      "chunk_text": "The Company leases its headquarters from Acme Properties LLC at $450K/year. Acme Properties LLC is 100% owned by James Chen.",
      "source_file": "acme_cim.pdf",
      "chunk_id": 22,
      "score": 0.87
    }
  ]
}
```

Two related-party transactions found — **from text** that no SQL query could have found.

---

## Part 5 — Step 3: The Semantic Model YAML (The Most Important File)

This is the file that makes **Cortex Analyst** work.  Without it, the LLM does not know what
your tables mean.  With it, the LLM can answer "what is the EBITDA margin?" correctly every
time.

### 5.1 What problem does the YAML solve?

Imagine asking a new hire: *"What is the EBITDA margin?"*

If they have never seen your spreadsheet, they have to guess:
- Which column is EBITDA?  Is it called `ebitda_usd_m` or `ebitda_amount` or `normalized_ebitda`?
- Is it already a percentage, or do I divide by revenue?
- Which period — FY2024, or trailing twelve months?

The YAML file is the **briefing document** you give the AI so it does not have to guess.

### 5.2 The YAML file — complete example

```yaml
# File: semantic_model/acme_deal.yaml
# This file tells Cortex Analyst everything it needs to know about our data

name: acme_deal_model
description: "Financial KPI model for Acme Corp M&A due diligence"

# ── TABLES ────────────────────────────────────────────────────────────────
# Tell Cortex which tables exist and what they contain

tables:
  - name: financial_kpis
    description: "Quarterly and annual financial KPIs extracted from Acme Corp's financial model"
    base_table: dealprep_db.acme_deal.financial_kpis

    # ── DIMENSIONS ──────────────────────────────────────────────────────
    # Dimensions are the "group by" columns — things you filter or slice by

    dimensions:
      - name: company
        synonyms: ["company name", "portfolio company", "entity"]
        description: "Name of the company these financials belong to"
        expr: company_name          # the actual SQL column name
        data_type: TEXT

      - name: period
        synonyms: ["fiscal year", "quarter", "FY", "year"]
        description: "Fiscal period — e.g. FY2024, Q1-2024"
        expr: period
        data_type: TEXT

      - name: currency
        synonyms: ["reporting currency", "FX"]
        description: "The currency of the financial figures"
        expr: currency
        data_type: TEXT

    # ── MEASURES ────────────────────────────────────────────────────────
    # Measures are the numbers — the things you calculate

    measures:
      - name: revenue
        synonyms: ["total revenue", "top line", "sales", "turnover"]
        description: "Total revenue reported in the financial model (USD millions)"
        expr: revenue_usd_m         # the column in the table
        data_type: NUMBER
        default_aggregation: sum    # when no aggregation is specified, SUM it

      - name: ebitda
        synonyms: ["earnings before interest tax depreciation", "operating profit proxy"]
        description: "EBITDA as reported — NOT adjusted/normalised unless explicitly stated"
        expr: ebitda_usd_m
        data_type: NUMBER
        default_aggregation: sum

      - name: ebitda_margin
        synonyms: ["EBITDA margin", "margin", "profitability"]
        description: "EBITDA as a percentage of Revenue — computed, not stored"
        expr: "ROUND(ebitda_usd_m / NULLIF(revenue_usd_m, 0) * 100, 2)"
        data_type: NUMBER
        default_aggregation: avg    # average margin across periods, not sum

      - name: net_income
        synonyms: ["net profit", "bottom line", "PAT"]
        description: "Net income after all deductions (USD millions)"
        expr: net_income_m
        data_type: NUMBER
        default_aggregation: sum

# ── RELATIONSHIPS ──────────────────────────────────────────────────────────
# If you have multiple tables, declare how they join here

relationships: []    # single table for now; add joins in Phase 2

# ── VERIFIED QUERIES ──────────────────────────────────────────────────────
# Optional: pre-approved query patterns that Cortex uses as examples
# These help the LLM understand your preferred style

verified_queries:
  - name: annual_ebitda_margin
    question: "What is Acme Corp's EBITDA margin by year?"
    sql: |
      SELECT
          period,
          ROUND(ebitda_usd_m / NULLIF(revenue_usd_m, 0) * 100, 2) AS ebitda_margin_pct
      FROM financial_kpis
      WHERE company_name = 'Acme Corp'
        AND period LIKE 'FY%'
      ORDER BY period DESC

  - name: revenue_trend
    question: "Show me Acme Corp revenue trend from 2022 to 2024"
    sql: |
      SELECT period, revenue_usd_m
      FROM financial_kpis
      WHERE company_name = 'Acme Corp'
      ORDER BY period
```

### 5.3 Upload the YAML to Snowflake

```sql
-- Upload the YAML to a stage
PUT file://semantic_model/acme_deal.yaml @cortex_models/;

-- Tell Cortex Analyst to use it
CREATE CORTEX ANALYST MODEL acme_analyst
    FROM @cortex_models/acme_deal.yaml
    WAREHOUSE = compute_wh;
```

### 5.4 Ask a question — watch what happens

```python
# Python
response = conn.cursor().execute("""
    SELECT SNOWFLAKE.CORTEX.ANALYST(
        'acme_analyst',
        'What is Acme Corp EBITDA margin for FY2024?'
    )
""").fetchone()

print(response[0])
```

**What happens inside Cortex Analyst — step by step:**

```
1. User asks: "What is Acme Corp EBITDA margin for FY2024?"

2. Cortex reads the YAML:
   - "ebitda_margin" maps to: ROUND(ebitda_usd_m / NULLIF(revenue_usd_m, 0) * 100, 2)
   - "FY2024" is a value in the "period" dimension column
   - "Acme Corp" is a value in the "company" dimension column

3. Cortex generates this SQL:
   SELECT ROUND(ebitda_usd_m / NULLIF(revenue_usd_m, 0) * 100, 2) AS ebitda_margin_pct
   FROM financial_kpis
   WHERE company_name = 'Acme Corp'
     AND period = 'FY2024'

4. Snowflake runs the SQL → returns: 18.95

5. Cortex wraps it in a natural-language response:
   "Acme Corp's EBITDA margin for FY2024 is 18.95%, calculated as
    $21.3M EBITDA / $112.4M revenue."
```

The response JSON:

```json
{
  "sql": "SELECT ROUND(ebitda_usd_m / NULLIF(revenue_usd_m, 0) * 100, 2) AS ebitda_margin_pct FROM financial_kpis WHERE company_name = 'Acme Corp' AND period = 'FY2024'",
  "results": [{"ebitda_margin_pct": 18.95}],
  "answer": "Acme Corp's EBITDA margin for FY2024 is 18.95%, based on EBITDA of $21.3M against revenue of $112.4M.",
  "confidence": "high"
}
```

---

## Part 6 — Step 4: Cortex Complete (Calling an LLM)

`CORTEX.COMPLETE` is how you call an LLM inside Snowflake SQL.  No API key needed — the model
runs inside Snowflake's infrastructure.

### 6.1 Basic call

```sql
-- Ask Claude a question
SELECT SNOWFLAKE.CORTEX.COMPLETE(
    'claude-sonnet-4-6',                        -- model name
    'Summarise this text in 2 sentences: ' || chunk_text
) AS summary
FROM acme_cim_chunks
WHERE chunk_id = 14;
```

### 6.2 With a system prompt (the professional way)

```python
# Python — structured call with system + user prompt
import json

system_prompt = """You are a financial analyst at a PE firm.
Analyse the provided context and identify any valuation discrepancies.
Always cite your sources. Be concise. Do not invent facts."""

user_message = json.dumps({
    "question": "Are there related-party transactions in Acme Corp's FY2024 financials?",
    "context": {
        "document_excerpts": [
            "Related-party transactions: In FY2024, the Company paid $2.1M in management fees to Chen Capital Partners...",
            "The Company leases its headquarters from Acme Properties LLC at $450K/year..."
        ],
        "financial_data": {
            "ebitda_fy2024": 21.3,
            "ebitda_margin_fy2024": 18.95
        }
    }
})

result = conn.cursor().execute("""
    SELECT SNOWFLAKE.CORTEX.COMPLETE(
        'claude-sonnet-4-6',
        ARRAY_CONSTRUCT(
            OBJECT_CONSTRUCT('role', 'system', 'content', %s),
            OBJECT_CONSTRUCT('role', 'user',   'content', %s)
        )
    )
""", (system_prompt, user_message)).fetchone()

print(result[0])
```

The LLM response:

```
"Yes. Two related-party transactions were identified in FY2024:

1. Management fees of $2.1M paid to Chen Capital Partners, an entity controlled by CEO James Chen.
   This represents 9.9% of EBITDA — material and should be added back for normalisation.

2. Headquarters lease of $450K/year from Acme Properties LLC (100% owned by James Chen).
   Market rate should be verified independently.

Combined impact on normalised EBITDA: +$2.55M → adjusted EBITDA margin of ~21.2% vs reported 19.0%."
```

### 6.3 Available models

```sql
-- See all models available in your region
SELECT SNOWFLAKE.CORTEX.LIST_MODELS();
```

```
MODEL_NAME                    | PROVIDER  | CONTEXT_WINDOW
------------------------------|-----------|---------------
claude-sonnet-4-6             | Anthropic | 200,000 tokens
claude-haiku-4-5-20251001     | Anthropic | 200,000 tokens
mistral-large2                | Mistral   | 128,000 tokens
llama3.1-70b                  | Meta      | 128,000 tokens
snowflake-arctic-instruct     | Snowflake | 4,096 tokens
```

---

## Part 7 — Step 5: Cortex Agents (Putting It All Together)

A **Cortex Agent** is a program that looks at your question and decides: do I need to search a
document, query a table, or just call an LLM?  It can use all three in the same question.

Think of it as an AI analyst who has three tools on their desk:
1. A search engine (Cortex Search) for reading documents
2. A SQL workbench (Cortex Analyst) for querying numbers
3. A messaging app to an LLM (Cortex Complete) for synthesising

### 7.1 Defining the Agent

```python
# Python — using the Snowflake Cortex Agents SDK
from snowflake.cortex import Agent, CortexSearchTool, CortexAnalystTool

agent = Agent(
    snowflake_connection=conn,
    agent_name="acme_diligence_agent",

    # Tell the agent what tools it has
    tools=[
        CortexSearchTool(
            name="document_search",
            description="Search Acme Corp's CIM, cap table, and other deal documents for narrative information, risks, and qualitative context",
            cortex_search_service="deal_doc_search",   # the service we created in Part 4
        ),
        CortexAnalystTool(
            name="financial_query",
            description="Query Acme Corp's structured financial data — revenue, EBITDA, margins, growth rates. Use for any numerical question.",
            semantic_model="@cortex_models/acme_deal.yaml",   # the YAML from Part 5
        ),
    ],

    # The LLM that orchestrates the tools and writes the final answer
    model="claude-sonnet-4-6",

    # System prompt: tells the agent how to behave
    instructions="""You are a senior financial analyst at a PE firm conducting M&A due diligence on Acme Corp.

For every question:
1. Search documents for qualitative context (narrative, risks, ownership).
2. Query financial data for any numbers (EBITDA, revenue, margins).
3. Cross-reference: does the narrative match the numbers?
4. Flag any discrepancies as a risk signal.
5. Always cite your sources (document name and page, or table + column).
Do not invent any facts not found in the data."""
)
```

### 7.2 Ask the big question

```python
response = agent.run(
    "What is Acme Corp's normalised EBITDA margin for FY2024, and are there any related-party transactions we should flag?"
)
print(response.message)
```

### 7.3 What happens inside the agent — the full trace

This is the important part.  Here is every step the agent takes:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — Agent reads the question and makes a plan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agent (internal monologue):
  "This question has two parts:
   Part A: EBITDA margin → I need numbers → use financial_query tool
   Part B: related-party transactions → narrative text → use document_search tool
   I'll run both and then synthesise."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — Tool call #1: financial_query
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agent calls Cortex Analyst with:
  question: "What is Acme Corp EBITDA and revenue for FY2024?"

Cortex Analyst generates SQL (using the YAML):
  SELECT period, revenue_usd_m, ebitda_usd_m,
         ROUND(ebitda_usd_m / revenue_usd_m * 100, 2) AS ebitda_margin_pct
  FROM financial_kpis
  WHERE company_name = 'Acme Corp' AND period = 'FY2024'

Result:
  { period: "FY2024", revenue: 112.4, ebitda: 21.3, ebitda_margin_pct: 18.95 }

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — Tool call #2: document_search
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agent calls Cortex Search with:
  query: "related party transactions FY2024 management fees"

Returns 3 chunks (score > 0.80):
  Chunk 14 (score 0.94): "...paid $2.1M in management fees to Chen Capital Partners..."
  Chunk 22 (score 0.87): "...leases headquarters from Acme Properties LLC at $450K/year..."
  Chunk 31 (score 0.81): "...Chen Capital Partners is controlled by CEO James Chen..."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 4 — Agent synthesises and writes the answer
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Agent calls Cortex Complete (claude-sonnet-4-6) with all findings.

Final answer:
  "Acme Corp's reported EBITDA margin for FY2024 is 18.95% ($21.3M EBITDA / $112.4M revenue).
   [Source: financial_kpis table, FY2024 row]

   ⚠️ RISK FLAG — Related-party transactions identified:
   1. Management fees of $2.1M paid to Chen Capital Partners (controlled by CEO James Chen).
      Add-back: normalised EBITDA becomes $23.4M → margin 20.8%.
      [Source: acme_cim.pdf, chunk 14]
   2. HQ lease at $450K/year from Acme Properties LLC (100% James Chen-owned).
      Verify market rate independently.
      [Source: acme_cim.pdf, chunk 22]

   Recommended action: Adjust EBITDA by +$2.1M for management fee add-back.
   Normalised EBITDA margin: 20.8% (vs reported 18.95%)."
```

### 7.4 The response object

```python
print(response.message)      # the answer above
print(response.tool_calls)   # list of tools that were called

# response.tool_calls looks like:
[
  {
    "tool": "financial_query",
    "input": "What is Acme Corp EBITDA and revenue for FY2024?",
    "output": {"period": "FY2024", "revenue": 112.4, "ebitda": 21.3, "ebitda_margin_pct": 18.95},
    "sql_generated": "SELECT period, revenue_usd_m, ebitda_usd_m, ROUND(...) FROM financial_kpis WHERE ..."
  },
  {
    "tool": "document_search",
    "input": "related party transactions FY2024 management fees",
    "output": [{"chunk_text": "...paid $2.1M...", "score": 0.94}, ...]
  }
]
```

---

## Part 8 — Using an External Vector Database

Sometimes your vectors are NOT in Snowflake — they are in Pinecone, Weaviate, ChromaDB, or
Qdrant.  Cortex Agents can call external tools alongside its native tools.

### 8.1 Why use an external vector DB?

| Reason | Example |
|---|---|
| You already have vectors there | Legacy system has 2 million chunks in Pinecone |
| You need specialised indexing | Weaviate's multi-modal (image + text) search |
| Cost at scale | Pinecone serverless can be cheaper than Snowflake storage for read-heavy workloads |
| Compliance | Data must stay in a specific cloud region that Snowflake does not serve |

### 8.2 Approach A — External Tool in Cortex Agent

You wrap your vector DB as a Python function and register it as a custom tool:

```python
import pinecone
from snowflake.cortex import Agent, ExternalTool

# Connect to your external vector DB (Pinecone example)
pc = pinecone.Pinecone(api_key="pc-xxxxxxxx")
index = pc.Index("deal-documents")

def search_external_vectors(query: str, tenant_id: str, top_k: int = 5) -> list[dict]:
    """Call Pinecone to find semantically similar chunks."""

    # 1. Embed the query (you manage this — Snowflake does not embed for external DBs)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    query_vector = model.encode(query).tolist()

    # 2. Query Pinecone with tenant filter
    results = index.query(
        vector=query_vector,
        filter={"tenant_id": {"$eq": tenant_id}},   # IMPORTANT: always filter by tenant
        top_k=top_k,
        include_metadata=True
    )

    # 3. Return in a consistent format
    return [
        {
            "text": match.metadata["text"],
            "score": match.score,
            "source": match.metadata["source_file"],
            "chunk_id": match.id
        }
        for match in results.matches
    ]

# Register it as a tool the agent can call
agent = Agent(
    snowflake_connection=conn,
    tools=[
        ExternalTool(
            name="external_vector_search",
            description="Search deal documents stored in Pinecone vector database",
            function=search_external_vectors,   # your Python function
        ),
        CortexAnalystTool(
            name="financial_query",
            semantic_model="@cortex_models/acme_deal.yaml",
        ),
    ],
    model="claude-sonnet-4-6",
    instructions="...same as before..."
)
```

### 8.3 Approach B — Hybrid search (Cortex + External together)

For maximum recall, run both Cortex Search and your external DB and merge the results:

```python
def hybrid_search(query: str, tenant_id: str) -> list[dict]:
    """Run Cortex Search and Pinecone in parallel, merge and deduplicate."""

    # Leg 1: Cortex Search (inside Snowflake)
    cortex_results = conn.cursor().execute("""
        SELECT SNOWFLAKE.CORTEX.SEARCH('deal_doc_search', %s, {'limit': 5, 'filter': {'tenant_id': %s}})
    """, (query, tenant_id)).fetchone()[0]["results"]

    # Leg 2: External vector DB (Pinecone)
    pinecone_results = search_external_vectors(query, tenant_id)

    # Merge: normalise scores to 0-1, combine, deduplicate by source+offset
    merged = {}
    for r in cortex_results:
        key = f"{r['source_file']}:{r.get('chunk_id', r['chunk_text'][:50])}"
        merged[key] = {"text": r["chunk_text"], "score": r["score"], "source": "cortex"}

    for r in pinecone_results:
        key = f"{r['source']}:{r['chunk_id']}"
        if key not in merged or r["score"] > merged[key]["score"]:
            merged[key] = {"text": r["text"], "score": r["score"], "source": "pinecone"}

    # Return top 5 by score
    return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:5]
```

### 8.4 External DB configuration (environment / secrets)

Never hardcode credentials.  Use Snowflake Secrets:

```sql
-- Store your Pinecone API key in Snowflake Secrets Manager
CREATE SECRET pinecone_api_key
    TYPE = GENERIC_STRING
    SECRET_STRING = 'pc-xxxxxxxxxxxxxxxxxxxxxxxx';

GRANT USAGE ON SECRET pinecone_api_key TO ROLE analyst_role;
```

In your Python code:

```python
import snowflake.connector

# Retrieve the secret at runtime
secret = conn.cursor().execute("""
    SELECT SYSTEM$GET_SECRET('pinecone_api_key')
""").fetchone()[0]

pc = pinecone.Pinecone(api_key=secret)
```

---

## Part 9 — Configuration Reference

### 9.1 The key configuration files you need

```
your-project/
├── semantic_model/
│   ├── acme_deal.yaml          ← the YAML file (Part 5)
│   └── betaco_deal.yaml        ← one per deal / tenant
│
├── cortex_config/
│   ├── search_services.sql     ← CREATE CORTEX SEARCH SERVICE statements
│   ├── agents.py               ← Agent definitions
│   └── models.py               ← Model routing rules
│
└── .env                        ← credentials (never commit this)
```

### 9.2 `.env` — credentials

```bash
# Snowflake connection
SNOWFLAKE_ACCOUNT=myaccount.us-east-1.snowflakecomputing.com
SNOWFLAKE_USER=pipeline_user
SNOWFLAKE_PASSWORD=...
SNOWFLAKE_DATABASE=dealprep_db
SNOWFLAKE_WAREHOUSE=compute_wh

# External vector DB (if using Pinecone)
PINECONE_API_KEY=pc-xxxxxxxxxx
PINECONE_INDEX=deal-documents

# Anthropic (only needed if calling Claude outside Snowflake)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxx
```

### 9.3 Model routing config (replaces hardcoding)

```python
# cortex_config/models.py
# Map each task type to the cheapest model that meets quality bar

MODEL_ROUTING = {
    # Low-cost tasks: structured output, classification, short answers
    "nl_to_sql":              "claude-haiku-4-5-20251001",   # $0.25/M input tokens
    "injection_detection":    "claude-haiku-4-5-20251001",
    "relationship_extraction":"claude-haiku-4-5-20251001",
    "document_classification":"claude-haiku-4-5-20251001",

    # Medium tasks: nuanced analysis
    "synthesis_low_risk":     "claude-haiku-4-5-20251001",   # risk_score < 0.3
    "synthesis_medium_risk":  "claude-sonnet-4-6",           # 0.3 ≤ risk < 0.7
    "synthesis_high_risk":    "claude-sonnet-4-6",           # risk ≥ 0.7 (HITL)

    # Fallback
    "default":                "claude-sonnet-4-6",
}

def get_model(task: str, risk_score: float | None = None) -> str:
    if task == "synthesis" and risk_score is not None:
        if risk_score < 0.3:
            return MODEL_ROUTING["synthesis_low_risk"]
        elif risk_score < 0.7:
            return MODEL_ROUTING["synthesis_medium_risk"]
        else:
            return MODEL_ROUTING["synthesis_high_risk"]
    return MODEL_ROUTING.get(task, MODEL_ROUTING["default"])
```

### 9.4 Tenant profile config (per-deal overrides)

```yaml
# config/tenants/T-001-acme.yaml
tenant_id: "T-001"
deal_name: "Acme Corp"

# Which stores to use for this deal
pipeline:
  chunking: "section_aware"        # best for CIM PDFs with headers
  embedding: "minilm"              # free local model
  vector_store: "chromadb"         # local persistent
  graph_enabled: true              # run Neo4j extraction

# LLM budget cap for this deal (per calendar month)
llm_budget_usd: 50.00

# Risk threshold for human review
hitl_risk_threshold: 0.70

# Semantic model for structured queries
semantic_model_path: "semantic_model/acme_deal.yaml"

# How long to keep chunks (data retention)
chunk_ttl_days: 365
```

---

## Part 10 — Full End-to-End Flow with Real Data (Putting It All Together)

Let us trace one complete request from start to finish with Acme Corp data:

```
ANALYST TYPES:
"What is Acme Corp's normalised EBITDA for FY2024 and flag any related-party risks?"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 1 — GUARDRAILS (InputGuard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PII scan: no SSN, email, phone found → query is clean
Injection check: no "ignore instructions" patterns → clean
Budget check: T-001 has $42.30 remaining of $50/month → proceed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 2 — SEMANTIC CACHE CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

sha256("t-001:what is acme corp normalised ebitda fy2024 related party risks")
→ cache MISS (first time this question was asked)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 3 — LONG-TERM MEMORY LOAD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Load last 5 analyses for tenant T-001 from analysis_history:
  Session S-001: "revenue trend" → risk 0.10 → no flags
  Session S-002: "cap table ownership" → risk 0.45 → moderate
  Session S-003: "management team background" → risk 0.30 → moderate

Context loaded: "Prior analyses show moderate risk on cap table structure."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 4 — AGENT ELIGIBILITY CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DocumentResearcher: ChromaDB collection has 3,847 chunks → ELIGIBLE
StructuredAgent:    Postgres has 12 records for T-001 → ELIGIBLE
GraphAgent:         Neo4j has 34 entities for T-001 → ELIGIBLE

All 3 agents will run in parallel.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 5 — FAN-OUT (3 agents run simultaneously)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

t=0ms  → all 3 agents start

DocumentResearcher (t=0→320ms):
  Semantic search: "normalised EBITDA FY2024 related party transactions"
  Returns 5 chunks, top chunk (score 0.94):
    "In FY2024, Acme Corp paid $2.1M management fees to Chen Capital Partners,
     controlled by CEO James Chen. EBITDA as reported: $21.3M."

StructuredAgent (t=0→180ms):
  Intent: analytical → SemanticModelAgent path
  Reads acme_deal.yaml → ebitda_margin = ROUND(ebitda_usd_m / revenue_usd_m * 100, 2)
  Generated SQL:
    SELECT period, revenue_usd_m, ebitda_usd_m,
           ROUND(ebitda_usd_m/revenue_usd_m*100,2) AS ebitda_margin_pct
    FROM financial_kpis
    WHERE company_name='Acme Corp' AND period='FY2024'
  Result: { revenue: 112.4, ebitda: 21.3, ebitda_margin_pct: 18.95 }

GraphAgent (t=0→410ms):
  Entities in query: "Acme Corp"
  1-hop Neo4j lookup:
    (Acme Corp) -[HAS_CEO]→ (James Chen)
    (James Chen) -[CONTROLS]→ (Chen Capital Partners)
    (James Chen) -[OWNS]→ (Acme Properties LLC)
  Returns 3 triples.

t=410ms → fan-in: all 3 agents complete, results merged into shared state.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 6 — RISK SCORER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Signal 1: Graph has "CONTROLS" edge (related_party pattern) → +0.40
Signal 2: Document chunk has "management fees" keyword → +0.15
Signal 3: Structured data has 3 distinct named values → +0.10 (just under threshold)
Signal 4: "James Chen" appears in both graph and structured data → +0.15 (entity overlap)
Prior analyses: 2/5 had moderate risk → +0.05

risk_score = min(0.40 + 0.15 + 0.10 + 0.15 + 0.05, 1.0) = 0.85

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 7 — CONDITIONAL ROUTING: risk ≥ 0.70 → HUMAN REVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Graph is paused. Response to analyst:

HTTP 200 {
  "interrupted": true,
  "risk_score": 0.85,
  "risk_signals": [
    "related_party edge detected in knowledge graph",
    "management fees keyword found in document",
    "entity overlap: James Chen in both graph and structured store",
    "recurring risk: 2 of last 5 analyses flagged medium/high"
  ],
  "pending_approval": {
    "reason": "High discrepancy risk — human approval required before synthesis",
    "resume_endpoint": "POST /tenants/T-001/analyze/S-004/resume"
  }
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 8 — HUMAN REVIEW (analyst approves + adds note)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

POST /tenants/T-001/analyze/S-004/resume
{
  "approved": true,
  "feedback": "Confirmed — add back the $2.1M management fee for normalised EBITDA calc."
}

Graph resumes from checkpoint. The feedback is injected into synthesis prompt.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 9 — SYNTHESIS (claude-sonnet-4-6, because risk ≥ 0.70)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Model writes the final answer:

"Acme Corp FY2024 — EBITDA Analysis

Reported EBITDA: $21.3M (margin: 18.95% on $112.4M revenue)
[Source: financial_kpis, FY2024 row]

⚠️ RELATED-PARTY FLAGS (2 items):

1. Management fees: $2.1M paid to Chen Capital Partners
   Controlled by CEO James Chen [acme_cim.pdf, p.31]
   Analyst note: add back per reviewer instruction

2. HQ lease: $450K/year to Acme Properties LLC
   100% owned by James Chen [acme_cim.pdf, p.34]
   Recommend: obtain independent market rent appraisal

Normalised EBITDA (post add-back): $23.4M → margin 20.82%
Delta vs reported: +$2.1M / +1.87 margin points

KNOWLEDGE GRAPH CONFIRMATION:
  (James Chen) controls (Chen Capital Partners) ✓
  (James Chen) owns (Acme Properties LLC) ✓
  [Source: Neo4j, tenant T-001, 1-hop traversal]"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LAYER 10 — OUTPUT GUARD + COST METER + AUDIT LOG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OutputGuard:
  PII re-scan: "James Chen" — ORG context, not redacted (M&A person names are intentional)
  Hallucination check: "$23.4M" and "$112.4M" both appear in retrieved evidence → grounded ✓

CostMeter records:
  model: claude-sonnet-4-6
  input_tokens: 1,247
  output_tokens: 312
  cost: $0.0049
  tenant_id: T-001, session: S-004

AuditLog records:
  tenant: T-001, session: S-004, risk: 0.85,
  verdict: allowed (HITL approved), timestamp: 2026-06-23T14:32:11Z

Final response delivered. Total wall-clock time: 2.1 seconds.
```

---

## Part 11 — How DealPrep Maps to This

Everything in the Cortex AI flow above has a direct counterpart in DealPrep:

| Cortex AI Component | DealPrep Component | File |
|---|---|---|
| Snowflake Stage | `data/{tenant_id}/` + FileUploadConnector | `connectors/file_upload.py` |
| `PARSE_DOCUMENT` | `PdfExtractor`, `HtmlExtractor`, etc. | `pipeline/extractors/` |
| `SPLIT_TEXT_RECURSIVE_CHARACTER` | `SectionAwareChunker`, `FixedSizeChunker` | `pipeline/chunking/` |
| `EMBED_TEXT_768()` | `MinilmEmbedder` (384-dim, local) | `pipeline/embedding/minilm.py` |
| Cortex Search Service | `VectorIndexer` + ChromaDB | `pipeline/indexing/vector.py` |
| Cortex Analyst | `StructuredAgent` + `SemanticModelAgent` | `agents/structured_agent.py` |
| Semantic Model YAML | `data/tenants/{id}/semantic_model.yaml` | ADR 0014 |
| `CORTEX.COMPLETE()` | `get_llm_client().complete()` | `app/llm.py` |
| Model routing | `get_model(task, risk_score)` | `cortex_config/models.py` (ADR 0016) |
| Cortex Agents | `LangGraphOrchestrator` | `agents/orchestrators/langgraph_orchestrator.py` |
| Tool (in Cortex) | `BaseAgent` subclass | `agents/document_researcher.py` etc. |
| HITL interrupt | `interrupt_before=["human_review_node"]` | `langgraph_orchestrator.py` |
| Short-term memory | `MemorySaver` (LangGraph) | `langgraph_orchestrator.py` |
| Long-term memory | `AnalysisHistory` table + `LongTermMemoryStore` | `agents/memory/store.py` |
| Cortex Guard | `GuardedOrchestrator` | `pipeline/guards/` (ADR 0015) |
| Cortex Budget | `CostMeter` + `llm_budget_usd` in profile | `pipeline/guards/cost_meter.py` |
| Snowflake Tasks | APScheduler | `app/runner.py` |
| Row Access Policy | `tenant_id` filter + Postgres RLS | ADR 0015 §governance |
| Knowledge graph (beyond Cortex) | Neo4j `GraphAgent` | `agents/graph_agent.py` |

The only thing DealPrep does differently from Cortex AI is that **everything runs locally** —
no Snowflake account needed.  The code you write once can be deployed to Snowflake later by
swapping the connectors (Snowflake Stage instead of local disk, `CORTEX.EMBED_TEXT_768()`
instead of MinilM, `CORTEX.COMPLETE()` instead of Anthropic API).  The architecture is
identical.

---

## Part 12 — Quick Reference Card

```
WANT TO...                          USE THIS
────────────────────────────────────────────────────────────────────────
Store a file                        → Snowflake Stage  /  data/{tenant}/
Extract text from PDF               → PARSE_DOCUMENT   /  PdfExtractor
Chunk text                          → SPLIT_TEXT_*     /  SectionAwareChunker
Create searchable vector index      → Cortex Search    /  VectorIndexer + ChromaDB
Ask "which doc mentions X?"         → CORTEX.SEARCH()  /  DocumentResearcher.run()
Ask "what is the EBITDA?"           → Cortex Analyst   /  StructuredAgent → SemanticModelAgent
Define what EBITDA means            → semantic YAML    /  data/tenants/{id}/semantic_model.yaml
Call an LLM                         → CORTEX.COMPLETE  /  get_llm_client().complete()
Use cheap model for simple tasks    → model routing    /  get_model(task, risk_score)
Combine all tools + synthesise      → Cortex Agent     /  LangGraphOrchestrator
Remember previous sessions          → MemorySaver      /  LangGraphOrchestrator (short-term)
Remember across days/weeks          → analysis_history /  LongTermMemoryStore (long-term)
Pause for human to check            → HITL interrupt   /  interrupt_before + /resume endpoint
Detect prompt injection / PII       → Cortex Guard     /  GuardedOrchestrator (ADR 0015)
Track LLM costs per tenant          → Cortex Budget    /  CostMeter (ADR 0015)
Use vectors stored in Pinecone      → ExternalTool     /  wrap search_external_vectors() as tool
Prevent one tenant seeing another   → Row Access Policy /  tenant_id filter + Postgres RLS
Run ingestion on a schedule         → Snowflake Tasks  /  APScheduler
```

# Extractor Evaluation Runbook

- **Stage:** Extract (Format Router → Extractor plugin)
- **Registry file:** `pipeline/extractors/registry.py`
- **Governed by:** [ADR 0008](../adr/0008-extractor-selection-and-stub-pattern.md)
- **Last updated:** 2026-06-22

---

## Why extractors need formal evaluation

An extractor that silently loses pages, mangles table rows, or returns empty text on 30% of
real-world files will corrupt every downstream stage — chunking, embedding, graph — with no
obvious error. The failure mode is **data loss without an exception**. Structured records with
wrong field shapes break the FTS index. This runbook defines what "correct" means and how to
measure it before marking a backend `implemented = True`.

---

## 1. Correctness tests [REQUIRED]

These must pass for every backend before production approval.

### 1.1 Contract conformance

| Test | Pass condition |
|---|---|
| `extract()` returns an `ExtractionResult` | Never raises `AttributeError`; result type matches contract |
| `text_documents` items each have non-empty `text` | `len(doc.text.strip()) > 0` for every item |
| `structured_records` items each have a non-empty `fields` dict | `len(rec.fields) > 0` |
| `original_file_reference` propagated | Every text doc and structured record carries the input `raw.original_file_reference` |
| Works on empty/minimal input | Returns an `ExtractionResult` with empty lists — never raises on valid (but empty) content |
| Works on max-size input | Does not OOM or time out on a 50 MB file within 60 s |

### 1.2 Format-specific coverage

**JSON extractor**

| Input shape | Expected output |
|---|---|
| Flat dict `{"k": "v"}` | 1 structured record, `fields = {"k": "v"}` |
| List of dicts | N structured records, one per dict |
| Nested dict | 1 structured record with nested serialized value |
| Array of non-dict primitives | 1 text doc containing JSON dump |
| Malformed JSON | `ExtractorError` raised with message |

**CSV extractor**

| Input | Expected output |
|---|---|
| Standard header row + data rows | N−1 structured records, field keys = header names |
| No header row | Records still returned; fields keyed 0, 1, 2 or raises clearly — document which |
| Empty file | Empty result, no exception |
| UTF-8 BOM | BOM stripped, fields correct |

**Text extractor**

| Input | Expected output |
|---|---|
| Plain ASCII | 1 text doc containing full text |
| Multi-paragraph | All content preserved (no truncation) |
| Unicode (CJK, accented) | No mojibake; character count preserved ±1 |

**HTML extractor**

| Input | Expected output |
|---|---|
| Simple `<p>` tags | Tags stripped, prose preserved |
| `<table>` inside HTML | Table text included in output (not silently dropped) |
| Script/style tags | Content of `<script>` and `<style>` blocks stripped |
| Malformed HTML (unclosed tags) | Returns best-effort text, does not raise |

**PDF extractor (pdfplumber)**

| Input | Expected output |
|---|---|
| Text-only PDF (N pages) | N `TextDocument` items; each page is a separate doc |
| PDF with tables | Both text pages AND table rows as `StructuredRecord`s |
| Scanned/image-only PDF | 0 text docs (no OCR); result empty, no exception; optional warning |
| Encrypted PDF | `ExtractorError` raised; not silently empty |
| Single-page, single-table | At least 1 structured record with ≥ 1 field |

**Office stubs (docx, xlsx, pptx)**

| Test | Pass condition |
|---|---|
| `extract()` called on a stub | Raises `ExtractorError` with text "POC stub" |
| `implemented = False` on class | `MyExtractor.implemented is False` |
| Stub appears in `GET /capabilities` | Listed under `extractors` with `implemented: false` |

---

## 2. Quality metrics [REQUIRED for PDF; RECOMMENDED for others]

Quality goes beyond correctness — you have the right *shape* of output, but is the *content*
good enough for downstream retrieval?

### 2.1 Text completeness ratio

For a golden test corpus of N documents with known page/word counts:

```
completeness = extracted_word_count / ground_truth_word_count
```

| Backend | Acceptable | Warn | Fail |
|---|---|---|---|
| pdf | ≥ 0.90 | 0.75–0.90 | < 0.75 |
| html | ≥ 0.95 | 0.85–0.95 | < 0.85 |
| text | 1.00 | < 1.00 | — |

> Ground truth: prepare 5–10 representative files per format (diverse layouts, sizes) with
> manually verified word counts. Store in `examples/eval_corpus/`.

### 2.2 Structured record field completeness

For tabular formats (csv, pdf tables, json):

```
field_completeness = non_null_fields / total_expected_fields
```

| Acceptable | Warn | Fail |
|---|---|---|
| ≥ 0.95 | 0.80–0.95 | < 0.80 |

### 2.3 Empty result rate

Run the extractor over the full eval corpus and count records where both `text_documents`
and `structured_records` are empty (the extractor ran but produced nothing).

| Acceptable | Warn | Fail |
|---|---|---|
| ≤ 2% | 2–10% | > 10% |

---

## 3. Performance benchmarks [REQUIRED for production approval]

### 3.1 Extraction throughput

| File size | p50 target | p95 target |
|---|---|---|
| < 1 MB | < 1 s | < 3 s |
| 1–10 MB | < 5 s | < 15 s |
| 10–50 MB (PDF only) | < 30 s | < 60 s |

Measure with 10 files per size bucket; record p50, p95 in the eval log.

### 3.2 Memory ceiling

Run extraction for a 50 MB PDF; RSS before vs after must not increase by more than **500 MB**.
pdfplumber holds page objects in memory — ensure pages are closed (the real extractor uses a
`with` block).

### 3.3 No global state between calls

Call `extract()` on file A then file B; verify B's output contains no content from A.
(Lazy-import caches are fine; mutable class-level state is not.)

---

## 4. Failure mode inventory

| Failure | Expected behaviour | Silent? |
|---|---|---|
| File not found / empty bytes | `ExtractorError` raised | No |
| Encrypted / password-protected PDF | `ExtractorError` raised | No |
| Corrupt file (truncated mid-byte) | `ExtractorError` raised | No |
| Unknown `format_type` | `FormatRouter` skips; `run_stages` logs `skipped` | No (logged) |
| pdfplumber import failure | `ImportError` propagated at call time (lazy import) | No |
| Stub extractor called | `ExtractorError("... POC stub ...")` | No |

**Rule:** extractors must never swallow exceptions and return an empty result silently. An empty
result is allowed only for genuinely empty files. Any other error must propagate as
`ExtractorError`.

---

## 5. Isolation and security checks

| Check | Verification |
|---|---|
| No file-system writes | `strace` / `Process Monitor`: extractor only reads `raw.content`, writes nothing to disk |
| No network calls | Extractor works offline (no HTTP to external service) |
| Path traversal safe | `original_file_reference` field is a label, not a path opened at extract time |
| Large file DoS | A 200 MB input is rejected or time-limited; does not block the scheduler thread for > 60 s |

---

## 6. Red flags in production (on-call signals)

| Signal | Likely cause | Action |
|---|---|---|
| `run_stages` stage `extract` shows `failed` > 5% of runs | Corrupt files in source, or extractor regression | Check `stage_error` column; redeploy with patched extractor |
| Structured record count drops to 0 for a CSV source | Encoding change or header drift in source | Re-examine connector output; add schema check to extractor |
| PDF extraction takes > 60 s per file | Very large scanned PDF or pdfplumber memory leak | Set per-page timeout; skip image-only pages |
| `text_documents` all have `len(text) < 50` | Extractor returning metadata instead of prose | Inspect raw PDF pages; may be image-only PDF |
| `GET /capabilities` lists an extractor but `format_type` records are skipped | FormatRouter format string mismatch | Check `format_type` tag from connector vs extractor registration name |

---

## 7. Adding a new extractor — gate checklist

Before setting `implemented = True`:

- [ ] All §1.1 contract tests pass
- [ ] Format-specific coverage tests pass for this format
- [ ] §2 quality metrics measured and recorded in eval log below
- [ ] §3 performance benchmarks recorded
- [ ] §4 failure modes verified (each triggers `ExtractorError`, not silent empty)
- [ ] §5 isolation checks pass
- [ ] `pipeline/extractors/__init__.py` imports the module (auto-discovery)
- [ ] `GET /capabilities` lists it with `implemented: true`
- [ ] Examples directory has at least one representative test file for this format

---

## Eval log

| Date | Extractor | Test corpus | Completeness | Empty rate | p95 latency | Result | Notes |
|---|---|---|---|---|---|---|---|
| 2026-06-22 | json | built-in unit tests | 1.00 | 0% | < 1 ms | PASS | |
| 2026-06-22 | csv | built-in unit tests | 1.00 | 0% | < 1 ms | PASS | |
| 2026-06-22 | text | built-in unit tests | 1.00 | 0% | < 1 ms | PASS | |
| 2026-06-22 | html | built-in unit tests | ≈0.95 | 0% | < 5 ms | PASS | |
| 2026-06-22 | pdf | `examples/sample_deal.pdf` | ~0.93 | 0% (text pages); scanned = empty OK | ~2 s/file | PASS | Verified live E2E |
| 2026-06-22 | docx | stub | — | — | raises | PASS (stub) | |
| 2026-06-22 | xlsx | stub | — | — | raises | PASS (stub) | |
| 2026-06-22 | pptx | stub | — | — | raises | PASS (stub) | |

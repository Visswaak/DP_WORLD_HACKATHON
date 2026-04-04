# actual_fk.md

Every file, every decision, every data movement — explained plainly.

---

## The one-line version of what this system does

A user uploads a shipment document. The system reads the text out of it, asks an LLM to extract structured fields (importer, exporter, HS codes, value, etc.), runs those fields through Indian customs compliance rules, retrieves relevant regulations from a database, and sends back a risk score, compliance issues, duty estimate, and broker guidance — all in one API response.

---

## Data flow, start to finish

```
Browser (App.jsx)
  │
  │  POST /api/analyze  (multipart form — raw file bytes)
  ▼
nginx  →  rate limit check (5r/m per IP) → client_max_body_size 85M enforced here
  │         rejects oversized/abusive requests before Python sees them
  ▼
main.py  →  reads files into memory, creates job_id, fires background task
  │
  ▼
jobs.py  →  stores job as QUEUED in _store dict
  │
  ▼
analysis.py  →  _analyze_single() per file, concurrency-gated by DOCUMENT_SEMAPHORE
  │
  ├──▶  ocr.py          →  raw bytes → plain text string
  ├──▶  llm.py          →  text → document type label (classify_document)
  ├──▶  llm.py          →  text → ExtractedShipmentData (extract_structured_data)
  ├──▶  compliance.py   →  ExtractedShipmentData → ComplianceResult (DB queries)
  ├──▶  retrieval.py    →  ExtractedShipmentData → regulation text chunks (pgvector)
  └──▶  retrieval.py    →  chunks + compliance → assistant_summary string
  │
  ▼
main.py  →  save_analyses() → single INSERT into analysis_records
  │
  ▼
jobs.py  →  updates job to DONE, stores result list
  │
Browser polls GET /api/jobs/{job_id} every 2s  (through nginx)
  │
  ▼
Browser renders results
```

**Network topology in Docker:**
```
Host browser
    │
    │  port 80
    ▼
 nginx (gateway)          — only container with a public port
    ├── /api/*  → backend:8000   (internal Docker network only)
    ├── /health → backend:8000
    └── /*      → frontend:80   (internal Docker network only)
```
Backend and frontend are not reachable from the host directly. All traffic enters through nginx.

---

## File-by-file breakdown

---

### `nginx/nginx.conf`

**What it is:** The gateway sitting in front of every container. The only service that exposes a port to the host.

**What it does:**

**Rate limiting:**
```nginx
limit_req_zone $binary_remote_addr zone=upload_limit:10m rate=5r/m;
```
Tracks request counts per client IP in a 10MB shared memory zone. The `/api/` location enforces this limit — 5 upload requests per minute per IP, with a burst of 5 before requests are dropped with a 429. The `/health` and `/` routes are unrestricted. This protects the OCR + LLM pipeline from being hammered without adding any application code.

**Upload size enforcement:**
```nginx
client_max_body_size 85M;
```
Enforced at the gateway before bytes reach Python. If a request exceeds 85MB (8 files × 10MB + headroom), nginx rejects it with 413 immediately. Without this, the file would travel all the way into FastAPI's `UploadFile` parser before being rejected — wasting memory and connection time.

**Upstream keepalive:**
```nginx
upstream api {
    server backend:8000;
    keepalive 32;
}
```
Nginx maintains a pool of 32 persistent TCP connections to Uvicorn. Without this, every proxied request would open and close a TCP connection — expensive under frequent uploads. `proxy_http_version 1.1` + `proxy_set_header Connection ""` are required for this to work (HTTP/1.0 sends `Connection: close` by default).

**Timeout aligned to pipeline:**
```nginx
proxy_read_timeout 120s;
```
The default nginx timeout is 60 seconds. The document pipeline can take up to 30s OCR + 20s LLM + queue wait time. 120s covers the worst case without nginx killing in-flight jobs. The health check uses a tighter 10s timeout since it should always be fast.

**Routing:**
- `location /api/` → `upstream api` (backend:8000) — with rate limit applied
- `location /health` → `upstream api` — no rate limit, short timeout
- `location /` → `upstream frontend` (frontend:80) — the React SPA

**Why backend and frontend aren't exposed directly:**  
In `docker-compose.yml`, backend and frontend use `expose` (Docker internal network only) instead of `ports` (host-mapped). A browser on the host cannot reach `localhost:8000` or `localhost:5173`. All traffic must go through nginx on port 80. This means rate limiting and size enforcement can't be bypassed by hitting the backend directly.

**`VITE_API_URL=http://localhost`:**  
The React JS runs in the browser on the host, not inside Docker. It calls the API at the URL baked in at build time. With nginx on port 80, `http://localhost` routes through nginx to the backend. The `VITE_API_URL` build arg in `docker-compose.yml` sets this — it overrides the default `http://localhost:8000` which would bypass nginx entirely.

---

### `frontend/src/App.jsx`

**What it is:** The entire frontend. One React component file — no router, no Redux, no extra pages.

**What it does:**
- Holds state: `files` (selected by user), `results` (from API), `loading`, `error`, `pipeline` (the 5 stage indicators)
- On submit: sends `FormData` with files to `POST /api/analyze`, gets back a `job_id`
- Polls `GET /api/jobs/{job_id}` every 2 seconds until `status === "done"` or `"failed"`
- Renders three result panels: Extracted Data, Broker Guidance (AI assistant), Risk Analysis

**Why polling instead of WebSocket:** Simpler to implement and debug. The job takes 3–15 seconds — polling every 2 seconds means at most 7 round trips. A WebSocket connection would be overkill for this traffic pattern.

**Why a single file:** This is a prototype. One component file is faster to work with than a full component hierarchy. The tradeoff is it becomes hard to maintain beyond ~500 lines.

**Data it sends:**
```
FormData { files: [File, File, ...] }
```

**Data it receives:**
```json
{
  "job_id": "uuid",
  "status": "done",
  "result": [{ "filename": "...", "compliance": {...}, ... }]
}
```

---

### `backend/app/main.py`

**What it is:** The FastAPI application entry point. Handles HTTP — nothing else.

**What it does:**

1. **Lifespan handler** — runs once at startup:
   - Calls `init_db()` to create tables, indexes, and extensions if they don't exist
   - Logs a warning if `DATABASE_URL` is not set — persistence is silently skipped otherwise
   - Logs a warning if no LLM API key is set (`GROQ_API_KEY`, `GEMINI_API_KEY`, or `OPENAI_API_KEY`)
   - No cache warmup — compliance data is queried directly from the DB on every request

2. **CORS middleware** — allows the frontend origin (`http://localhost:5173`) to call the API from the browser. Without this, the browser blocks the request before it even leaves the tab.

3. **Request metrics middleware** — wraps every request to count it, time it, and attach `X-Process-Time-Ms` to the response header. Runs before any route logic.

4. **Exception handlers** — converts `FileValidationError` → 413, `AnalysisError` → 400, so the frontend gets a clean error message rather than a 500 stack trace.

5. **`POST /api/analyze`** — reads all file bytes eagerly (before the response, because `UploadFile` objects are request-scoped and become unreadable after), creates a job, fires `_run_job` as a background task, returns `{ job_id, status }` immediately. The client doesn't wait.

6. **`GET /api/jobs/{job_id}`** — looks up the job in `jobs.py`, returns its current status and result if done.

**Why files are read before the background task:** FastAPI's `UploadFile` is a thin wrapper around the HTTP request body. Once the response is sent, the request is closed and the file data is gone. Reading bytes eagerly into `list[tuple[str, bytes]]` lets the background task work with the data safely.

**Data in:** Raw HTTP multipart form upload  
**Data out:** `{ job_id: str, status: str }` immediately; results available via polling

---

### `backend/app/jobs.py`

**What it is:** An in-memory job store. A plain Python dict.

**Why it exists:** The document pipeline takes 3–15 seconds. HTTP connections have timeouts and holding them open for long-running work is fragile. The pattern used here is: accept the request instantly → return a job ID → let the client poll for completion. `jobs.py` is the place where job state lives between the POST and the GET.

**What it stores per job:**
```python
@dataclass
class Job:
    id: str           # UUID
    status: JobStatus # QUEUED → PROCESSING → DONE or FAILED
    result: list[dict] | None   # the full analysis output when done
    error: str | None           # error message when failed
    created_at: datetime
    updated_at: datetime
```

**TTL:** Jobs older than 1 hour are pruned on the next `create_job()` call. This prevents the dict from growing unboundedly in long-running processes.

**The limitation:** This is per-process memory. Restarting the server loses all jobs. Multiple processes (multiple Uvicorn workers or Kubernetes pods) each have their own separate dict — a job created on worker A doesn't exist on worker B. For a production system you'd use Redis or a DB-backed job queue instead.

---

### `backend/app/config.py`

**What it is:** All configuration in one place. Read from environment variables at import time.

**`.env` loading:** `config.py` imports `python-dotenv` and calls `load_dotenv()` pointing directly at `../../.env` (the monorepo root). This means running `uvicorn` locally from `backend/` picks up all variables automatically — no need to `source` or export anything manually. In Docker, `env_file: .env` in `docker-compose.yml` injects the same variables as OS-level env vars before the process starts, so `load_dotenv()` is a no-op there (it never overwrites existing env vars). This pattern works correctly in both environments.

**Why this matters for persistence:** Before `python-dotenv` was added, `DATABASE_URL` was `None` when running locally, which made `engine = None`, `SessionLocal = None`, and `save_analyses()` silently return `[]` — analyses ran fine but nothing was ever stored in Supabase. The bug was invisible because no exception was raised.

**Why not Pydantic Settings:** The code uses a plain `Settings` class with `os.getenv`. It works and is now paired with `python-dotenv` for `.env` loading. A future improvement could migrate to `pydantic-settings` for type validation.

**Key decisions baked in here:**

- **Multi-provider LLM support:** Priority order is Groq → Gemini → OpenAI. Whichever key is set first wins. The `active_api_key` and `active_api_base_url` are computed once here, so the rest of the code doesn't need to know which provider it's talking to — they all speak the OpenAI API format.
  ```
  GROQ_API_KEY set   → uses api.groq.com, model llama-3.3-70b-versatile
  GEMINI_API_KEY set → uses generativelanguage.googleapis.com, model gemini-2.0-flash
  neither set        → uses api.openai.com, model gpt-4.1-mini
  ```

- **Concurrency knobs:** `MAX_PARALLEL_DOCUMENTS`, `MAX_PARALLEL_OCR`, `MAX_PARALLEL_LLM` are all tunable without code changes. Defaults are conservative for a single machine.

- **`USD_TO_INR`:** The exchange rate for duty estimates. Hardcoded default of 84.0. In production this should be updated weekly to match CBIC's notified rate. It's an env var so you can update it without redeploying.

---

### `backend/app/schemas.py`

**What it is:** Pydantic models that define the data contract between layers.

**Why this matters:** Every piece of data that crosses a boundary (LLM output → compliance engine, compliance engine → API response, API response → frontend) is validated here. If the LLM returns `shipment_value_usd` as a string instead of a float, Pydantic coerces it or raises a clear error — not a silent wrong value downstream.

**Key models:**

- `ExtractedShipmentData` — what the LLM produces. Importer, exporter, incoterm, port, value, currency, and a list of `ShipmentItem`s (each with HS code, quantity, origin, value).
- `ComplianceResult` — what the compliance engine produces. Score 0–100, risk level, clearance prediction, list of `ComplianceIssue`s, suggestions, and a `DutyEstimate`.
- `DutyEstimate` — BCD + SWS + IGST broken out in INR, plus effective rate.
- `DocumentAnalysisResponse` — the full response per document. Everything above plus filename, classification, text preview, assistant summary, processing stages, performance metadata.
- `JobResponse` — wraps a list of `DocumentAnalysisResponse` with job_id, status, error, timestamps.

**Data flow role:** `ExtractedShipmentData` is the central handoff object. It's produced by `llm.py`, consumed by `compliance.py`, consumed again by `retrieval.py` to build the RAG query, and embedded in the final response.

---

### `backend/app/errors.py`

**What it is:** Two custom exception classes.

```python
class AnalysisError(Exception): ...
class FileValidationError(AnalysisError): ...
```

**Why:** FastAPI's exception handlers in `main.py` catch these specific types and map them to HTTP status codes (413 for file validation, 400 for analysis errors). Using specific exception types instead of bare `raise HTTPException(...)` inside service code keeps the services decoupled from HTTP concerns — services don't need to import FastAPI.

---

### `backend/app/observability.py`

**What it is:** A logger and a thread-safe metrics collector.

**`logger`** — standard Python `logging.Logger`. Outputs structured key=value lines like:
```
analysis_started filename=invoice.pdf size_bytes=24800
analysis_finished filename=invoice.pdf risk=High mode=openai duration_ms=4231.00
```

**`Metrics`** — an in-memory `Counter` wrapped in a `threading.Lock`. Tracks:
- `documents_received`, `documents_processed`, `rejected_files`, `fallback_extractions`
- `ocr_ms`, `llm_ms`, `compliance_ms` — cumulative timing totals

**Why threading.Lock here:** The metrics object is shared across asyncio tasks AND across ThreadPoolExecutor threads (where OCR runs). asyncio primitives don't protect against thread-level concurrent access — only a real threading lock does.

**Where to see it:** `GET /health` returns `metrics.snapshot()` — a dict of all counters and timing totals since startup.

**The limitation:** Per-process, non-persistent, resets on restart. For production you'd emit these to Prometheus or Datadog.

---

### `backend/app/database.py`

**What it is:** All database models, indexes, and the async SQLAlchemy engine.

**Tables:**

| Table | Purpose |
|---|---|
| `analysis_records` | Audit log. Every processed document is stored here with its full result JSON. |
| `hs_tariffs` | BCD + IGST rates per HS code or chapter. Used for duty estimation. |
| `hs_pgas` | PGA (Partner Government Agency) rules. Which HS prefixes trigger FSSAI, BIS, WPC, SCOMET checks. |
| `country_rules` | Per-country restriction rules — sanctions, trade restrictions, FTA eligibility. |
| `world_ports` | Port risk levels. Indian entry ports and major foreign ports. |
| `regulation_chunks` | RAG corpus. 25 regulation text chunks + their 1536-dimension pgvector embeddings. |

**Indexes — why they matter at scale:**

| Table | Index | Type | Purpose |
|---|---|---|---|
| `hs_pgas` | `(hs_prefix, is_active)` | B-tree composite | Every compliance query filters both columns. At 5,000+ rows, covers the full WHERE clause so Postgres never touches the heap for inactive rows. |
| `country_rules` | `(country_name, is_active)` | B-tree composite | Same reasoning — filters active rules for the specific origins in this shipment only. |
| `world_ports` | `(city gin_trgm_ops)` | GIN trigram | Makes `ILIKE '%chennai%'` fast against 100k+ rows. Without this, every port lookup is a full sequential scan. |
| `world_ports` | `(port_name gin_trgm_ops)` | GIN trigram | Same — covers free-text port name matching from LLM extraction. |
| `hs_tariffs` | `hs_code` (primary key) | B-tree | Already the PK — all tariff lookups are exact point reads. |

GIN trigram indexes require the `pg_trgm` extension. `init_db()` creates it with `CREATE EXTENSION IF NOT EXISTS pg_trgm` then creates both port indexes with `IF NOT EXISTS`. If `pg_trgm` is unavailable (older Postgres), it logs a warning and continues — port lookups still work, just slower.

**Why async SQLAlchemy:** FastAPI is async. Blocking DB calls inside an async route freeze the event loop and prevent other requests from being handled. `create_async_engine` + `async_sessionmaker` keeps all DB I/O non-blocking.

**`pool_pre_ping=True`:** Before each connection is used, SQLAlchemy sends a cheap `SELECT 1` to check the connection is alive. Without this, stale connections from the pool (e.g. after DB restart) cause errors on first use.

**`pool_size=10, max_overflow=20`:** The engine maintains up to 10 persistent connections and can create up to 20 more under burst load. At 30 total connections per process, a 4-worker deployment uses up to 120 Postgres connections.

**pgvector init:** `init_db()` tries `CREATE EXTENSION IF NOT EXISTS vector`. If it fails (pgvector not installed), it sets `rag_available = False` and skips creating `regulation_chunks`. The rest of the system continues without RAG.

**`engine = ... if settings.database_url else None`:** If no `DATABASE_URL` is set, no engine is created. Every downstream check is `if not SessionLocal: return` — the entire DB layer silently no-ops. The startup warning in `main.py` makes this visible instead of silent.

**Tables that were dropped:** `regulatory_sources`, `source_documents`, `hs_policy_rules`, `agency_requirements`, `change_events`, `review_queue`, `document_chunks` — these were created by a bootstrap script for a future regulatory scraping pipeline. No app code ever read or wrote them. They were dropped from Supabase and their associated files (`bootstrap_supabase_compliance.py`, `sync_regulatory_documents.py`, `data/regulatory_sources.json`, `sql/supabase_compliance_schema.sql`) were deleted.

---

### `backend/app/cache.py`

**What it is:** A no-op stub. The in-process cache was removed.

**Why it was removed:** The original cache loaded the entire `hs_pgas`, `country_rules`, `world_ports`, and `hs_tariffs` tables into module-level Python lists at startup. This worked at prototype scale but breaks with a real compliance DB:

- A full ITC-HS tariff schedule is 12,000+ rows. Full PGA coverage is 5,000+ rules. UN/LOCODE ports exceed 100,000 rows. Loading all of this per-process consumes hundreds of MB of RAM per worker.
- The Python list scans that replaced DB queries (`[p for p in _cache.get_pgas() if p.hs_prefix in prefixes]`) become slower than an indexed DB query at that row count — the cache was making things worse, not better.
- Cache state was per-process. With multiple workers or pods, a rule update only refreshed whichever process received the reload request. Other processes served stale compliance rules silently — a correctness problem for a regulatory system.

**What replaced it:** Targeted indexed DB queries in `compliance.py` on every request. The DB handles the scale; the app stays stateless. The stub file is kept so any stray import doesn't break.

---

### `backend/app/services/analysis.py`

**What it is:** The orchestrator. Coordinates all the service calls for each document.

**`validate_uploads()`:** Checks that at least one file was submitted and that the count doesn't exceed `MAX_FILES_PER_REQUEST`. Runs before any expensive work.

**`analyze_uploads()`:** Creates one asyncio Task per file and gathers them concurrently with `asyncio.gather(return_exceptions=True)`. Each task runs `_analyze_single()`. If one file fails, the others still complete — `gather` with `return_exceptions=True` collects exceptions rather than cancelling the batch. `FileValidationError` (file too large) re-raises and aborts the whole batch; other per-file errors are logged and that file is skipped.

**`DOCUMENT_SEMAPHORE`:** Limits how many `_analyze_single()` calls run simultaneously. Set to `MAX_PARALLEL_DOCUMENTS` (default 4). Without this, uploading 8 files would launch 8 concurrent OCR + LLM pipelines simultaneously, potentially exhausting CPU, memory, and API rate limits.

**`_analyze_single()` — what actually happens per document:**

```
1. Acquire DOCUMENT_SEMAPHORE slot
2. Validate file type (extension whitelist)
3. Read bytes from UploadFile
4. Validate file size
5. extract_text()       → plain text string
6. classify_document()  → document type label (no I/O, pure string matching)
7. extract_structured_data() → ExtractedShipmentData + raw LLM metadata
8. evaluate_compliance()     → ComplianceResult
9. retrieve_regulations()    → list of regulation text chunks
10. generate_grounded_summary() → assistant_summary string
11. Build DocumentAnalysisResponse
12. Build AnalysisRecord (for DB write — not written yet)
13. Return AnalysisArtifacts(response, record)
```

**`AnalysisArtifacts`:** A dataclass holding both the API response object and the DB record. They're kept together so `main.py` can batch-write all records in one DB transaction after all files are processed, instead of one transaction per file.

**`_build_processing_stages()`:** Creates the list of stage statuses sent to the frontend. If LLM extraction fell back to regex, the "LLM extraction" stage is marked "degraded" instead of "completed" — the frontend could use this to show a warning indicator.

---

### `backend/app/services/ocr.py`

**What it is:** Text extraction from uploaded files.

**The decision tree inside `_extract_text_sync()`:**

```
Is it .txt / .csv / .json / .xml?
  YES → decode bytes as UTF-8 directly (no OCR needed)
  NO  → try to open as image with Pillow
          SUCCESS → run Tesseract → if text extracted, return it
          FAIL    → try UTF-8 decode of raw bytes (catches PDFs with embedded text)
                      still empty? → return FALLBACK_TEXT (hardcoded sample invoice)
```

**Why `asyncio.to_thread` + `ThreadPoolExecutor`:** Tesseract is a synchronous C library. Calling it directly inside an async function blocks the event loop — no other requests can be processed while OCR is running. `loop.run_in_executor(_ocr_pool, ...)` offloads it to a background thread, freeing the event loop.

**Why a bounded `ThreadPoolExecutor` instead of the default executor:** `asyncio.to_thread` uses Python's default thread pool which is unbounded. If 20 files arrive simultaneously, 20 Tesseract threads could start. OCR is CPU-heavy — that many threads would saturate the machine. The bounded pool (`max_workers=MAX_PARALLEL_OCR`, default 2) enforces a hard ceiling on concurrent Tesseract processes.

**`asyncio.wait_for` with `ocr_timeout_seconds`:** If Tesseract takes longer than the timeout, the coroutine is cancelled and `FALLBACK_TEXT` is returned. The Tesseract thread itself continues until it finishes (threads can't be killed), but it's no longer blocking the pipeline.

**`FALLBACK_TEXT`:** A hardcoded plausible invoice string. This exists so the demo still produces a result even if Tesseract isn't installed. In production, this would be replaced by a real error.

**Data in:** `filename: str`, `content: bytes`  
**Data out:** `str` (extracted text, best-effort)

---

### `backend/app/services/llm.py`

**What it is:** Document classification and structured data extraction.

**`classify_document()`:**  
Pure keyword matching — no LLM, no I/O. Checks the document text (then filename as fallback) against a fixed list of rules:
```python
("Bill of Lading",      ("bill of lading", "bol"))
("Commercial Invoice",  ("invoice",))
("Packing List",        ("packing",))
("Certificate of Origin", ("certificate",))
```
Returns "Shipping Document" if nothing matches. This label is shown in the UI and stored in `analysis_records` for filtering.

**`extract_structured_data()`:**  
Tries OpenAI first (if `active_api_key` is set), falls back to regex if not, or if OpenAI times out or throws.

**OpenAI path (`_extract_with_openai`):**
- Sanitizes the text (strips dangerous control characters, preserves newlines and tabs so invoice table structure survives into the prompt)
- Truncates to `MODEL_TEXT_LIMIT` (default 12,000 chars) — LLMs have context limits and most invoices don't need more than this
- Sends a structured prompt asking for specific JSON keys
- The prompt has explicit field extraction rules (e.g. "don't confuse FOB Value subtotals with the incoterm") — these were added to fix real extraction mistakes
- Uses `response_format={"type": "json_object"}` to force the model to return valid JSON
- Validates the JSON response against `ExtractedShipmentData` using Pydantic

**`asyncio.wait_for` + `asyncio.to_thread`:** The OpenAI SDK is synchronous. Same pattern as OCR — offload to thread, apply timeout, handle cancellation.

**`OPENAI_SEMAPHORE`:** Shared across `llm.py` and `retrieval.py`. All outbound LLM and embedding calls count against this single semaphore. This is the global budget for OpenAI API concurrency. Default 4 means at most 4 OpenAI calls in flight at any moment across all documents and all retrieval requests.

**Regex fallback (`_extract_with_fallback`):**  
Uses simple regex patterns (IMPORTER_PATTERN, HS_PATTERN, VALUE_PATTERN, etc.) against the raw text. Works for the demo's sample documents but would miss most real-world invoice formats. The `raw_model_output["mode"]` field is set to `"fallback"` so callers know the extraction quality is degraded.

**Singleton client `_get_openai_client()`:**  
The OpenAI client is created once and reused. Creating it on every call would waste connection setup overhead. The `base_url` is set from `settings.active_api_base_url` — this is how Groq and Gemini work through the same client (they expose an OpenAI-compatible API).

**Data in:** `filename: str`, `text: str`  
**Data out:** `tuple[ExtractedShipmentData, dict]` — the structured data and metadata about how it was extracted (mode, any errors, response excerpt)

---

### `backend/app/services/compliance.py`

**What it is:** The customs risk scoring engine.

**`evaluate_compliance()` — the entry point:**
```python
if SessionLocal is not None:
    try:
        return await _evaluate_from_db(data)
    except Exception:
        logger.warning(...)
# falls through to:
return _evaluate_in_memory(data)
```
DB path first, in-memory fallback second (only runs when `DATABASE_URL` is not set).

**`_evaluate_from_db()` — always queries the DB directly:**  
Opens a single session (one connection from the pool) and fires four targeted queries. No cache, no Python list scans, no full table loads. The compliance DB can grow to any size without this function changing.

```
Session opened (1 connection from pool)
  ├── SELECT FROM hs_pgas      WHERE hs_prefix IN (prefixes of this shipment's HS codes)
  ├── SELECT FROM country_rules WHERE country_name IN (origins in this shipment)
  ├── SELECT FROM hs_tariffs   WHERE hs_code IN (8-digit codes + chapters)
  └── _lookup_port()           → point lookup or ILIKE (see below)
Session closed
```

Each query returns only the rows relevant to this specific shipment — a shipment with 3 line items from China generates at most ~12 prefix strings and 1 origin string. Postgres resolves each against its composite index and returns a tiny result set regardless of how large the underlying tables are.

**`_lookup_port(session, port_of_entry)` — targeted port lookup:**  
The LLM extracts `port_of_entry` as free text ("JNPT Mumbai", "Chennai", "IN BOM"). This function resolves it without loading the ports table:

1. **Exact match on `port_code`** (UN/LOCODE primary key) — sub-millisecond B-tree lookup
2. **`ILIKE` on `city` or `port_name`** — uses the GIN trigram indexes created in `init_db()`. Fast at 100k+ rows. Without the trigram index this would be a sequential scan.

Returns a single `WorldPort` row or `None`. Never fetches more than one row.

**`_check_port_match(matched)` — generates issues from the looked-up port row:**  
Indian entry port with HIGH/SCRUTINY risk → adds a scrutiny delay issue. Foreign port with MEDIUM/HIGH risk → adds an origin due-diligence note.

**Per-item checks:**
- **PGA rules:** For each item's HS code, finds all `hs_pgas` rows whose `hs_prefix` is a prefix of that code. The query already filters by prefix — the Python loop just deduplicates by agency (one BIS issue per item, not one per matching prefix length).
- **Incomplete HS code:** Under 8 digits → can't be filed on a Bill of Entry. Medium severity.
- **Country rules:** Sanctions/restrictions → `ComplianceIssue`. FTA eligibility → `suggestion` string (no penalty, shown as a tip).

**Shipment-level checks (`_shipment_level_checks`):**
- Value ≥ $10,000 → enhanced scrutiny (medium, −10)
- Non-CIF incoterm → valuation adjustment needed (low, −5)
- Port in hardcoded `HIGH_SCRUTINY_PORTS` set → allow for delay (low, −5). This set is a fast fallback used when the port isn't found in the DB.

**Scoring:**
```
score = 100 − sum(penalties)
SEVERITY_WEIGHT = { critical: 35, high: 20, medium: 10, low: 5 }
```
Risk tiers: Critical issues present → Critical; score < 55 → High; < 80 → Moderate; ≥ 80 → Low.

**Duty estimation (`_estimate_duty`):**  
Tries to find the item's HS code or chapter in the `tariff_map` returned by the DB query. Falls back to the hardcoded `_CHAPTER_DUTY` dict (chapter-level approximations built into the code). Formula: `BCD = assessable_INR × bcd_rate`, `SWS = BCD × 10%`, `IGST = (assessable + BCD + SWS) × igst_rate`.

**`_evaluate_in_memory()` — the no-DB fallback:**  
Runs only when `DATABASE_URL` is not set. Checks against hardcoded Python sets: `RESTRICTED_ORIGINS`, `UN_SANCTIONED_ORIGINS`, `HIGH_RISK_ORIGINS`, `BIS_MANDATORY_PREFIXES`, `FSSAI_CHAPTERS`, `WPC_PREFIXES`, `SCOMET_PREFIXES`. This is a small subset of what the real compliance DB contains — it exists to keep the system runnable without Postgres, not as a production compliance path.

**`build_assistant_summary()`:**  
Deterministic string template. Used when RAG is not available. Example output: *"Shipment for Acme Corp is classified as high risk with a score of 45/100. 3 issue(s) detected. Indicative duty: BCD ₹41,580 + SWS ₹4,158 + IGST ₹54,723 = ₹100,461 total (20.3% effective rate). Expected outcome: Second appraisement or examination expected."*

**Data in:** `ExtractedShipmentData`  
**Data out:** `ComplianceResult`

---

### `backend/app/services/retrieval.py`

**What it is:** The RAG (Retrieval-Augmented Generation) pipeline. Produces the "Broker guidance" text in the UI.

**Why RAG instead of just prompting the LLM directly:** A bare LLM call for compliance guidance would hallucinate regulations — it would cite rules that sound plausible but may be wrong or outdated. RAG constrains the LLM to only cite text that was explicitly retrieved from a known corpus. The prompt says: *"Using ONLY the regulation excerpts provided below... if the excerpts do not cover a flag, say so rather than inventing a citation."*

**`retrieve_regulations()` — step by step:**

1. **Guard clause:** If `SessionLocal` is None (no DB), `rag_available` is False (no pgvector), or no API key is set — return `[]` immediately. RAG is fully optional.

2. **Build query string (`_build_query`):** Constructs a human-readable sentence from the shipment's HS codes, origins, port, value, and incoterm. Example:
   ```
   HS codes: 85171200, 84713000. Countries of origin: China.
   Port of entry: JNPT Mumbai. Shipment value USD 48,500.00. Incoterm: FOB.
   ```

3. **Embed the query (`_embed`):** Calls OpenAI's embedding API (`text-embedding-3-small` by default) to convert the query string into a 1536-dimension float vector. This runs through `OPENAI_SEMAPHORE` — same concurrency budget as LLM calls.

4. **Vector search (`_search`):** Queries pgvector:
   ```sql
   SELECT source, title, text FROM regulation_chunks
   ORDER BY embedding <=> $query_vector  -- cosine distance
   LIMIT 5
   ```
   Returns the 5 regulation chunks whose embeddings are closest in meaning to the shipment query. Prepends each with its citation header: `[source]\ntitle\n\ntext`.

**`generate_grounded_summary()` — what happens with the chunks:**

If chunks are empty or no API key: returns `build_assistant_summary()` (the deterministic template).

Otherwise, sends a prompt to the LLM containing:
- Shipment details (importer, exporter, origin, HS codes, value, port, incoterm)
- Compliance flags already detected (from the `ComplianceResult`)
- The 5 retrieved regulation excerpts, separated by `---`
- Instruction: max 5 sentences, cite sources in brackets, don't invent citations

The LLM responds with the `assistant_summary` string shown in the UI's "Broker guidance" panel.

**`OPENAI_SEMAPHORE` is imported from `llm.py`:**  
Embedding calls and grounded summary LLM calls both count against the same semaphore. Per document, the pipeline could make 3 OpenAI calls total: extraction, embedding, and grounded summary. The semaphore prevents these from stacking up across concurrent documents.

**Data in:** `ExtractedShipmentData`, `ComplianceResult`  
**Data out:** `str` (the assistant summary)

---

### `backend/data/regulations.json` + `backend/scripts/ingest_regulations.py`

**`regulations.json`:** 25 curated regulation text chunks. Each has a `source` (e.g. "CBIC Circular 2023"), `title`, and `text`. These are the only regulations the RAG system knows about. The corpus is static — adding new regulations means adding to this file and re-running ingestion.

**`ingest_regulations.py`:** A one-time script. Reads `regulations.json`, calls the embedding API for each chunk, stores the `(source, title, text, embedding)` row into `regulation_chunks`. Run once after DB setup. Run again whenever the corpus changes. Guards against missing API key using `settings.active_api_key` (accepts Groq, Gemini, or OpenAI key).

**Note on Groq + RAG:** Groq has no embeddings API. If only `GROQ_API_KEY` is set, `ingest_regulations.py` will pass the guard (key is present) but fail at the API call — Groq's endpoint doesn't support the embeddings route. `retrieve_regulations()` handles this gracefully: it catches the exception, logs a warning, and returns `[]`, falling back to the deterministic summary. To populate `regulation_chunks` you need an OpenAI or Gemini key.

**`seed_compliance_tables.py`:** Seeds `hs_tariffs`, `hs_pgas`, `country_rules`, and `world_ports` in one idempotent run. Contains the full Indian customs dataset: chapter-level BCD/IGST rates, all PGA agency rules (FSSAI, BIS, WPC, SCOMET, CDSCO, AERB, Animal/Plant Quarantine), country restrictions and FTAs, and Indian + major global ports. Run once after DB setup, re-run whenever rules change.

**`load_cbic_tariff.py`:** A utility for loading real CBIC tariff data from a CSV. When you obtain the actual ITC-HS tariff schedule, this script upserts it into `hs_tariffs` at 8-digit granularity. The seed script uses chapter-level approximations; this script gives you exact rates.

**Why pre-embed instead of embedding at query time:** Embedding 25 chunks takes ~2 seconds and costs API money. Doing it once and storing the vectors means every search is just a vector distance computation — sub-millisecond.

---

### `backend/app/config.py` — LLM provider switching

Worth calling out specifically. The system supports three providers through one unified code path:

```
GROQ_API_KEY    → api.groq.com/openai/v1          → llama-3.3-70b-versatile
GEMINI_API_KEY  → generativelanguage.googleapis.com → gemini-2.0-flash
OPENAI_API_KEY  → api.openai.com                   → gpt-4.1-mini
```

Groq and Gemini both expose an OpenAI-compatible REST API. The OpenAI Python SDK supports a `base_url` parameter. So by setting `base_url` to Groq's or Gemini's endpoint, the same `client.chat.completions.create()` call works for all three. No provider-specific code anywhere except `config.py`.

**Priority: Groq first** because it's the fastest and cheapest for this use case (Llama 3.3 70B is a strong model for structured extraction and runs at ~400 tokens/second on Groq's inference hardware).

---

## Concurrency model — how all the limits interact

```
Incoming requests (no limit at HTTP layer)
         │
         ▼
  DOCUMENT_SEMAPHORE (max_parallel_documents=4)
         │
         ├──▶ OCR ThreadPoolExecutor (max_workers=2)   ← CPU-bound, real threads
         │
         └──▶ OPENAI_SEMAPHORE (max_parallel_llm=4)   ← I/O-bound, async
                  ├── LLM extraction calls
                  ├── Embedding calls
                  └── Grounded summary calls
```

If 8 files arrive simultaneously:
- All 8 tasks are created
- Only 4 acquire the `DOCUMENT_SEMAPHORE` and start
- Of those 4, only 2 can run Tesseract at once
- All 4 LLM/embedding calls compete for 4 semaphore slots (so in the worst case they queue too)
- The other 4 files wait at the `DOCUMENT_SEMAPHORE` until a slot opens

This prevents the server from being overwhelmed by a large upload while still processing multiple files concurrently.

---

## What the DB actually stores vs. what stays in memory

| Data | Where it lives | Why |
|---|---|---|
| Job status & results | `jobs.py` in-memory dict | Temporary, polled by frontend, expires in 1h |
| Compliance rules (pgas, countries, ports, tariffs) | Postgres — queried per request via targeted indexed queries | Tables are too large to load into memory; DB handles scale via composite + trigram indexes |
| Regulation embeddings | `regulation_chunks` DB table (pgvector) | Vector search can't be done in plain Python lists |
| Analysis audit trail | `analysis_records` DB table | Permanent record of every processed document |
| Metrics & timings | `observability.py` in-memory Counter | Lightweight, resets on restart, exposed at `/health` |

---

## What happens with no API key, no DB, no Tesseract

The system still runs and returns a result:

| Missing | Effect |
|---|---|
| No Tesseract | `FALLBACK_TEXT` (hardcoded sample invoice) used as OCR output |
| No API key | Regex extraction used instead of LLM; deterministic summary instead of RAG |
| No DATABASE_URL | Startup warning logged. Compliance uses hardcoded in-memory rules; no persistence; no RAG |
| No pgvector | RAG disabled; deterministic summary used; rest of pipeline unaffected |

Every stage has an explicit fallback. The system never hard-fails on a single document — it degrades and continues.

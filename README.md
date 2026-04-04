# Customs Clearance Copilot

An AI-assisted customs clearance workflow. Users upload shipment documents (commercial invoices, packing lists, bills of lading); the backend extracts structured data via OCR + LLM, evaluates Indian customs compliance (CBIC/DGFT/BIS/FSSAI/WPC/SCOMET), computes indicative BCD+SWS+IGST duty estimates, and returns clearance predictions aligned with ICEGATE filing requirements.

## Stack

| Layer | Technology |
|---|---|
| Gateway | Nginx (rate limiting, TLS termination, request routing) |
| Frontend | React, Vite, Tailwind CSS |
| Backend | Python 3.11+, FastAPI, Uvicorn |
| OCR | Tesseract OCR (with regex fallback) |
| LLM | OpenAI-compatible API (Groq / Gemini / OpenAI, with deterministic fallback) |
| Database | PostgreSQL + pgvector (optional) |
| Container | Docker Compose |

---

## Prerequisites (local development without Docker)

- **Python 3.11+**
- **Node.js 18+**
- **Tesseract OCR**
  ```bash
  brew install tesseract          # macOS
  sudo apt install tesseract-ocr  # Debian/Ubuntu
  ```
- **PostgreSQL with pgvector** — optional; enables persistence and RAG
  ```bash
  # Use the Docker Compose setup — pgvector is pre-installed in the pg image
  ```

---

## Quickstart — Docker (recommended)

API keys are already configured in `.env`. Start everything with:

```bash
docker compose up --build
```

All services are accessible through the nginx gateway on a **single port**:

| URL | What it is |
|---|---|
| `http://localhost` | React frontend |
| `http://localhost/api/analyze` | Document upload endpoint |
| `http://localhost/api/jobs/{id}` | Job status polling |
| `http://localhost/health` | Backend health check |
| `localhost:5432` | PostgreSQL direct access (seeding, migrations) |

Backend (`:8000`) and frontend container (`:80`) are **not** exposed directly — all browser traffic goes through nginx.

After the **first** startup, ingest the regulation corpus to enable RAG:

```bash
docker compose exec backend python scripts/ingest_regulations.py
```

---

## Quickstart — Local (no Docker)

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload      # runs on http://localhost:8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                        # runs on http://localhost:5173
```

The frontend defaults to `http://localhost:8000` for the API. Override with:

```bash
export VITE_API_URL=http://localhost:8000
```

### 3. Verify

```bash
curl http://localhost:8000/health
```

---

## Environment Variables

Copy from `.env.example` as a starting point. All variables are optional unless marked required.

### LLM (provider priority: Groq → Gemini → OpenAI)

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | — | Fastest and cheapest. Uses `llama-3.3-70b-versatile`. Takes priority if set. |
| `GEMINI_API_KEY` | — | Uses `gemini-2.0-flash`. Second priority. |
| `OPENAI_API_KEY` | — | Uses `gpt-4.1-mini`. Fallback if neither Groq nor Gemini are set. |
| `OPENAI_MODEL` | provider default | Override the model name for whichever provider is active. |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Used for RAG embeddings (OpenAI only). |

If no key is set, the system falls back to regex extraction and deterministic summaries.

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | `postgresql+psycopg://user:pass@host:5432/db`. Persistence and RAG disabled if unset. pgvector must be installed for RAG. |

### OCR

| Variable | Default | Description |
|---|---|---|
| `TESSERACT_CMD` | `tesseract` (PATH) | Full path to Tesseract binary. Set if not on PATH (e.g. `/opt/homebrew/bin/tesseract` on macOS). |

### API & Concurrency

| Variable | Default | Description |
|---|---|---|
| `MAX_FILES_PER_REQUEST` | `8` | Max files per upload |
| `MAX_FILE_SIZE_BYTES` | `10485760` | Per-file size limit (10 MB) |
| `MAX_PARALLEL_DOCUMENTS` | `4` | Concurrent document pipelines |
| `MAX_PARALLEL_OCR` | `2` | Concurrent Tesseract threads |
| `MAX_PARALLEL_LLM` | `4` | Concurrent LLM/embedding API calls |
| `API_TIMEOUT_SECONDS` | `20` | LLM API call timeout |
| `OCR_TIMEOUT_SECONDS` | `30` | Per-file OCR timeout |

### RAG

| Variable | Default | Description |
|---|---|---|
| `RAG_TOP_K` | `5` | Regulation chunks retrieved per request |
| `GROUNDED_SUMMARY_MAX_TOKENS` | `350` | Max tokens for broker guidance summary |

### India-specific

| Variable | Default | Description |
|---|---|---|
| `USD_TO_INR` | `84.0` | Exchange rate for duty estimates. Update weekly to match CBIC's notified rate. |

### CORS

| Variable | Default | Description |
|---|---|---|
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated allowed frontend origins. In Docker, nginx handles CORS at the gateway so this is less critical. |

---

## RAG Setup (one-time)

RAG is active when both `DATABASE_URL` and an LLM API key are configured. Run once after the DB is up:

```bash
# Docker:
docker compose exec backend python scripts/ingest_regulations.py

# Local:
cd backend && source .venv/bin/activate
python scripts/ingest_regulations.py
```

Embeds the 25 regulation chunks from `backend/data/regulations.json` into pgvector. Re-run whenever the corpus changes.

---

## Network Architecture (Docker)

```
Host browser
    │  port 80
    ▼
 nginx (gateway)                ← only container with a public port
    ├── /api/*  → backend:8000  ← internal Docker network
    ├── /health → backend:8000
    └── /*      → frontend:80  ← internal Docker network
```

**nginx responsibilities:**
- Rate limiting — 5 upload requests per minute per IP (burst 5)
- `client_max_body_size 85M` — rejects oversized uploads before Python sees them
- `proxy_read_timeout 120s` — covers full OCR + LLM pipeline duration
- Keepalive connection pool to backend — avoids TCP handshake per request
- Routes `/api/` to backend, everything else to the React SPA

---

## End-to-End Request Flow

```
POST /api/analyze (multipart files) via nginx
  └─ validate_uploads()
  └─ asyncio.gather() → _analyze_single() per file [DOCUMENT_SEMAPHORE]
      ├─ extract_text()               [ThreadPoolExecutor + Tesseract]  → text
      ├─ classify_document()          [keyword rules]                   → doc type
      ├─ extract_structured_data()    [LLM → regex fallback]            → ExtractedShipmentData
      ├─ evaluate_compliance()        [targeted DB queries]             → ComplianceResult
      ├─ retrieve_regulations()       [pgvector cosine search]          → chunks
      └─ generate_grounded_summary()  [LLM + retrieved chunks]          → summary
  └─ save_analyses() → batched INSERT into analysis_records
  └─ return job result via GET /api/jobs/{job_id}
```

**Degradation chain** — the system never hard-fails on a document:

| Missing | Effect |
|---|---|
| No Tesseract | Hardcoded sample invoice text used |
| No LLM API key | Regex extraction; deterministic summary |
| No `DATABASE_URL` | Hardcoded in-memory compliance rules; no persistence; no RAG |
| No pgvector | RAG skipped; deterministic summary used |

---

## Project Structure

```
nginx/
  nginx.conf          Gateway config (rate limiting, routing, timeouts)

backend/
  app/
    main.py           FastAPI app, routing, CORS, lifespan
    config.py         All env vars with defaults (multi-provider LLM switching)
    schemas.py        Pydantic request/response contracts
    database.py       SQLAlchemy async ORM, indexes, pgvector/pg_trgm init
    jobs.py           In-memory async job store (TTL-based pruning)
    errors.py         FileValidationError, AnalysisError
    observability.py  Thread-safe counters/timers, structured logging
    cache.py          No-op stub (cache removed — see architecture notes)
    services/
      analysis.py     Orchestrator: validates, fans out, collects results
      ocr.py          Tesseract extraction with fallback
      llm.py          Classification + structured LLM extraction
      compliance.py   Rule-based risk scoring with targeted DB queries
      retrieval.py    RAG: embed query → pgvector search → grounded summary
  data/
    regulations.json  25 curated regulation chunks
  scripts/
    ingest_regulations.py  One-time corpus ingestion into pgvector

frontend/
  src/
    App.jsx           Single-page React UI (upload, pipeline, results)
    index.css         Global styles + Tailwind config
  nginx.conf          Internal static file server config (inside frontend container)
```

---

## Operational Notes

- `/health` returns in-memory request counters and cumulative timing totals since startup.
- Analysis results are polled via `GET /api/jobs/{job_id}` — the POST returns a `job_id` immediately.
- Jobs expire from the in-memory store after 1 hour.
- The compliance DB is queried directly on every request via targeted indexed queries — no in-process cache. Adding rules to the DB takes effect immediately without restarting.
- Composite B-tree indexes on `hs_pgas(hs_prefix, is_active)` and `country_rules(country_name, is_active)`, and GIN trigram indexes on `world_ports(city, port_name)` are created automatically by `init_db()` on first startup.

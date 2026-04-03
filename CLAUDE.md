# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

**Customs Clearance Copilot** — an AI-assisted document processing system for Indian customs clearance workflows. Users upload shipment documents (commercial invoices, packing lists, bills of lading); the system extracts structured data via OCR + LLM, runs Indian customs compliance checks (CBIC/DGFT/BIS/FSSAI/WPC/SCOMET), computes indicative BCD+SWS+IGST duty estimates, and returns clearance predictions aligned with ICEGATE filing requirements.

## Running the Project

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # runs on :8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                            # runs on :5173
```

### Environment Variables

```bash
# Required for LLM extraction (falls back to regex if absent)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini             # default

# Optional database persistence (in-memory only if unset)
# pgvector must be installed on the Postgres server to enable RAG
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/customs_ai

# OCR binary path (required if Tesseract not on PATH)
TESSERACT_CMD=/opt/homebrew/bin/tesseract

# Frontend API target
VITE_API_URL=http://localhost:8000    # default

# Concurrency tuning
MAX_PARALLEL_DOCUMENTS=4
MAX_PARALLEL_OCR=2
MAX_PARALLEL_LLM=4
MAX_FILES_PER_REQUEST=8
MAX_FILE_SIZE_BYTES=10485760
API_TIMEOUT_SECONDS=20

# RAG settings (only active when DATABASE_URL + OPENAI_API_KEY are set)
EMBEDDING_MODEL=text-embedding-3-small  # default
RAG_TOP_K=5                             # chunks retrieved per request
```

### RAG Setup (one-time, after setting DATABASE_URL and OPENAI_API_KEY)

```bash
cd backend
source .venv/bin/activate
python scripts/ingest_regulations.py
```

This embeds the 25 regulation chunks in `backend/data/regulations.json` into pgvector. Re-run whenever the corpus changes.

### Docker (full stack)

```bash
cp .env.example .env          # fill in OPENAI_API_KEY and ALLOWED_ORIGINS
docker compose up --build

# After first startup, ingest the regulation corpus:
docker compose exec backend python scripts/ingest_regulations.py
```

The `pgvector/pgvector:pg16` image includes the pgvector extension pre-installed. `init_db()` creates the extension and tables automatically on first startup.

### Health Check

```bash
curl http://localhost:8000/health
```

## Architecture

### Backend Layer Stack (`backend/app/`)

```
main.py          → FastAPI app, routing, CORS, exception handlers, DB batching
config.py        → Pydantic Settings, all env vars with defaults
schemas.py       → Pydantic API contracts (request/response shapes)
database.py      → SQLAlchemy async ORM, AnalysisRecord + RegulationChunk models, pgvector init
errors.py        → FileValidationError, AnalysisError
observability.py → Thread-safe counters/timers, structured logging

services/
  analysis.py    → Orchestrator: validates uploads, fans out per-file tasks, collects results
  ocr.py         → Text extraction: bounded ThreadPoolExecutor → Tesseract → UTF-8 → fallback text
  llm.py         → Document classification (keyword rules) + structured extraction (OpenAI → regex fallback)
  compliance.py  → Rule-based risk scoring → ComplianceResult with score, risk_level, issues, suggestions
  retrieval.py   → RAG: embed shipment query → pgvector similarity search → grounded LLM summary

data/
  regulations.json → 25 curated regulation chunks (OFAC, EAR, ITAR, CBP, ISF, Section 301, etc.)

scripts/
  ingest_regulations.py → One-time script: embeds corpus and stores in pgvector
```

### End-to-End Request Flow

```
POST /api/analyze (multipart files)
  └─ validate_uploads()
  └─ asyncio.gather() → _analyze_single() per file [DOCUMENT_SEMAPHORE]
      ├─ extract_text()              [ThreadPoolExecutor, OCR_TIMEOUT]  → text string
      ├─ classify_document()         [keyword rules]                    → doc type string
      ├─ extract_structured_data()   [OPENAI_SEMAPHORE]                 → ExtractedShipmentData
      ├─ evaluate_compliance()       [pure rules]                       → ComplianceResult
      ├─ retrieve_regulations()      [pgvector cosine search]           → list[str] chunks
      └─ generate_grounded_summary() [OpenAI, retrieved chunks]         → assistant_summary
  └─ save_analyses()  → single batched DB transaction (skipped if no DATABASE_URL)
  └─ return list[DocumentAnalysisResponse]
```

### RAG Architecture (`services/retrieval.py`)

`retrieve_regulations()` builds a query string from the shipment's HS codes, origins, port, value, and incoterm, embeds it with `text-embedding-3-small`, and runs a cosine similarity search against the `regulation_chunks` pgvector table. Returns the top-k chunk texts with their source citations prepended.

`generate_grounded_summary()` passes the retrieved chunks to the LLM with a strict "cite only what is in the excerpts" prompt. Falls back to `build_assistant_summary()` (deterministic template) when chunks are empty, pgvector is unavailable, or OpenAI fails.

**Degradation chain:** pgvector unavailable → `rag_available=False` set at startup → `retrieve_regulations()` returns `[]` → `generate_grounded_summary()` uses deterministic template. The rest of the pipeline is unaffected.

### Concurrency Model

- `DOCUMENT_SEMAPHORE` — limits total concurrent document pipelines
- `ThreadPoolExecutor(max_workers=MAX_PARALLEL_OCR)` — true OCR capacity limit; timed-out threads hold their slot until they exit
- `OPENAI_SEMAPHORE` — limits concurrent LLM + embedding API calls
- `EMBEDDING_SEMAPHORE` — same limit applied to embedding calls in retrieval service

Both Tesseract and OpenAI SDK calls are blocking; they run via `asyncio.to_thread()` / `run_in_executor()` to keep the event loop free.

### Graceful Degradation

The system never hard-fails on individual document processing:
- OCR failure → UTF-8 decode of raw bytes → deterministic sample text
- OpenAI timeout/error → regex extraction against raw text → synthetic defaults
- Missing `DATABASE_URL` → analysis completes, results returned, nothing persisted

### Compliance Scoring (`services/compliance.py`)

Starts at 100, subtracts penalties per rule:
| Rule | Severity | Penalty |
|---|---|---|
| Embargoed origin (Iran, N.Korea, Syria, Crimea) | Critical | −35 |
| Sensitive HS prefix (8542, 9301, 9302) | High | −20 |
| Incomplete HS code (< 6 digits) | Medium | −10 |
| Shipment value ≥ $25,000 | Medium | −10 |
| Watchlist port (Miami, Long Beach, L.A.) | Low | −5 |

Risk tiers: Critical issues present → "Critical"; score < 55 → "High"; < 80 → "Moderate"; ≥ 80 → "Low".

### Database Models

`AnalysisRecord` — stores `filename`, `classification`, `risk_level`, `clearance_prediction`, and the full `DocumentAnalysisResponse` serialized as `payload` JSON. All datetime/risk/classification columns are indexed.

`RegulationChunk` — stores `source`, `title`, `text`, and a 1536-dimension `embedding` vector (pgvector). Created only if the pgvector extension is available; the `rag_available` module flag in `database.py` is set at startup and read by `retrieval.py` to skip retrieval gracefully when pgvector is absent.

### Frontend (`frontend/src/`)

Single-page React app (`App.jsx`). Key state: `files`, `results`, `loading`, `error`, `pipeline`. Submits `FormData` to `POST /api/analyze`, animates pipeline stages, renders three result panels: Extracted Data, AI Assistant Guidance, Risk Analysis (with `RiskMeter` component).

Custom Tailwind palette uses `ember` (orange), `mint` (cyan), `sand` (beige), `sky` (blue) on dark `ink-*` backgrounds with glassmorphism (`glass` utility class).

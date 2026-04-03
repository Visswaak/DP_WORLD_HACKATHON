# Customs Clearance Copilot Prototype

This project is a prototype for an AI-assisted customs clearance workflow. A user uploads shipment documents from the web UI, the backend extracts and classifies the content, a compliance engine evaluates risk, and the UI renders findings, suggestions, and a clearance prediction.

## Stack

- Frontend: React, Vite, Tailwind CSS
- Backend: Python, FastAPI
- OCR: Tesseract OCR with deterministic fallback parsing
- LLM: OpenAI-compatible extraction with fallback extraction
- Database: PostgreSQL via SQLAlchemy async engine

## Project Structure

```text
frontend/   React dashboard and upload UI
backend/    FastAPI API, orchestration, services, persistence
```

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Optional environment variables:

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=gpt-4.1-mini
export DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/customs_ai
export TESSERACT_CMD=/opt/homebrew/bin/tesseract
export MAX_FILES_PER_REQUEST=8
export MAX_FILE_SIZE_BYTES=10485760
export MAX_PARALLEL_DOCUMENTS=4
export MAX_PARALLEL_OCR=2
export MAX_PARALLEL_LLM=4
export API_TIMEOUT_SECONDS=20
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend expects the API at `http://localhost:8000` by default. Override with:

```bash
export VITE_API_URL=http://localhost:8000
```

## End-to-End Flow

### 1. Frontend startup

The frontend starts in [frontend/src/main.jsx](/Users/wishaak/dp_world/frontend/src/main.jsx). It mounts the React app and loads global styles from [frontend/src/index.css](/Users/wishaak/dp_world/frontend/src/index.css).

The main screen is implemented in [frontend/src/App.jsx](/Users/wishaak/dp_world/frontend/src/App.jsx). This component:

- stores selected files in React state
- shows the upload panel and pipeline cards
- sends a `multipart/form-data` request to `POST /api/analyze`
- renders the returned extraction, compliance, and assistant summary

This is where the user-facing flow starts.

### 2. Backend process startup

The backend entry point is [backend/app/main.py](/Users/wishaak/dp_world/backend/app/main.py).

When FastAPI starts:

- it constructs the app with a lifespan handler
- the lifespan handler calls `init_db()` from [backend/app/database.py](/Users/wishaak/dp_world/backend/app/database.py)
- if `DATABASE_URL` is configured, SQLAlchemy creates tables for `analysis_records`

Why it starts here:

- `main.py` is the API boundary
- startup work is centralized so initialization happens once per process
- DB setup is separated from route logic

### 3. Request enters the API

When the frontend submits files, the request reaches `analyze_documents()` in [backend/app/main.py](/Users/wishaak/dp_world/backend/app/main.py).

Before the route logic runs, the HTTP middleware `add_request_metrics()` executes:

- increments request counters
- measures total request time
- adds `X-Process-Time-Ms` to the response
- logs unexpected failures

Why this matters:

- observability is attached at the HTTP boundary
- timing every request helps identify slow endpoints under load
- the route stays focused on business logic

### 4. Route delegates orchestration

`analyze_documents()` does not directly run OCR, LLM calls, or compliance checks. Instead, it calls `analyze_uploads(files)` in [backend/app/services/analysis.py](/Users/wishaak/dp_world/backend/app/services/analysis.py).

Why:

- keeps the controller layer thin
- moves business orchestration into a reusable service
- makes the request path easier to test and evolve

### 5. Upload validation happens first

Inside `analyze_uploads()`:

- `validate_uploads()` checks that files exist
- it enforces `MAX_FILES_PER_REQUEST`
- each file is later checked against `MAX_FILE_SIZE_BYTES`

Why:

- rejecting oversized or excessive uploads early protects memory and throughput
- validation before deeper processing avoids wasting OCR or LLM capacity

### 6. Documents are processed concurrently with limits

`analyze_uploads()` creates one async task per upload and awaits them with `asyncio.gather()`.

Each document passes through `_analyze_single()`, which is guarded by `DOCUMENT_SEMAPHORE`.

Why:

- files in the same request can make progress concurrently
- the semaphore prevents unbounded concurrency from overwhelming CPU, OCR, or outbound API capacity
- this improves throughput while keeping latency predictable

### 7. File bytes are read

Inside `_analyze_single()`:

- the file is read from `UploadFile`
- the filename is normalized
- the file size is validated
- counters and logs are emitted

Why:

- the rest of the pipeline operates on bytes
- size checks are cheap and should happen before expensive work

### 8. OCR stage runs

Text extraction is performed by `extract_text()` in [backend/app/services/ocr.py](/Users/wishaak/dp_world/backend/app/services/ocr.py).

The flow is:

- `_analyze_single()` acquires `OCR_SEMAPHORE`
- `extract_text()` offloads synchronous OCR work to a worker thread via `asyncio.to_thread`
- `_extract_text_sync()` decides whether to decode directly or run Tesseract
- if OCR fails, it falls back to decoding text content
- if that also fails, it returns a deterministic sample text

Why:

- Tesseract is blocking, so it should not run on the event loop
- limiting OCR concurrency avoids CPU starvation
- the fallback path keeps the demo alive even when Tesseract is unavailable

### 9. Document classification runs

Once raw text is available, `classify_document()` in [backend/app/services/llm.py](/Users/wishaak/dp_world/backend/app/services/llm.py) classifies the document using lightweight keyword rules.

Why:

- classification is cheap, deterministic, and fast
- it does not need an LLM round trip
- doing this before extraction gives the UI a stable label quickly

### 10. Structured extraction runs

Structured extraction is handled by `extract_structured_data()` in [backend/app/services/llm.py](/Users/wishaak/dp_world/backend/app/services/llm.py).

The flow is:

- if an OpenAI API key exists, the function enters `OPENAI_SEMAPHORE`
- `_extract_with_openai()` builds the prompt, truncates text to `MODEL_TEXT_LIMIT`, and sends the request
- the synchronous OpenAI SDK call is moved to a thread with `asyncio.to_thread`
- `asyncio.wait_for()` enforces `API_TIMEOUT_SECONDS`
- JSON is extracted and validated into `ExtractedShipmentData`
- if anything fails, the code falls back to `_extract_with_fallback()`

Why:

- LLM calls are the slowest and least predictable part of the system
- semaphores, timeouts, and fallback logic keep the system responsive under heavy load
- validation converts model output into a typed structure before downstream logic uses it

### 11. Compliance evaluation runs

The extracted shipment data is sent to `evaluate_compliance()` in [backend/app/services/compliance.py](/Users/wishaak/dp_world/backend/app/services/compliance.py).

This function:

- checks for embargoed origins
- checks high-risk HS code prefixes
- checks incomplete HS codes
- checks shipment value thresholds
- checks watchlist ports
- accumulates a total penalty score
- derives `risk_level` and `clearance_prediction`

Then `build_assistant_summary()` creates a short natural-language summary for the dashboard.

Why:

- customs-style rule evaluation is deterministic business logic
- it should live outside the API layer and outside the LLM
- separating rules from extraction makes it easier to audit and update

### 12. Response object is built

Back in `_analyze_single()`, the system constructs `DocumentAnalysisResponse` from [backend/app/schemas.py](/Users/wishaak/dp_world/backend/app/schemas.py).

It includes:

- filename
- classification
- extracted text preview
- structured shipment data
- compliance result
- assistant summary
- stage statuses
- limited raw model metadata
- performance metadata

Why:

- a typed response model makes the API contract explicit
- the frontend gets everything it needs in one response
- performance flags make degraded behavior visible

### 13. Persistence is prepared

Still inside `_analyze_single()`, the code prepares an `AnalysisRecord` model from [backend/app/database.py](/Users/wishaak/dp_world/backend/app/database.py).

The record is not immediately inserted. Instead, it is returned as part of `AnalysisArtifacts`.

Why:

- avoiding one DB transaction per file reduces overhead
- batching writes improves throughput under multi-file requests

### 14. Batched persistence runs after analysis

Back in `analyze_documents()` in [backend/app/main.py](/Users/wishaak/dp_world/backend/app/main.py):

- all `AnalysisArtifacts` are collected
- database records are filtered
- `save_analyses(records)` is called once
- the batch is inserted in a single async DB transaction
- generated IDs are copied back into each response

Why:

- batched persistence is cheaper than repeated flush/commit cycles
- the route returns the response only after analysis and persistence are complete
- if the DB is disabled, the rest of the flow still works

### 15. Response returns to the frontend

The API returns a list of `DocumentAnalysisResponse` objects.

The frontend:

- stores them in state
- marks the pipeline completed
- shows extracted shipment fields
- renders compliance issues, risk score, and clearance prediction
- shows assistant suggestions and summary cards

This is the end of the primary flow.

## Why the Code Is Structured This Way

- `main.py` handles HTTP concerns only
- `services/analysis.py` handles orchestration across OCR, LLM, compliance, and persistence preparation
- `services/ocr.py` isolates OCR and file-to-text concerns
- `services/llm.py` isolates classification and extraction logic
- `services/compliance.py` isolates customs business rules
- `database.py` isolates persistence infrastructure
- `schemas.py` defines typed contracts between layers
- `observability.py` isolates logging and metrics behavior
- `config.py` centralizes operational tuning knobs

This separation makes the system easier to scale, test, and optimize independently.

## Operational Notes

- If OpenAI or Tesseract are not configured, the backend still runs using deterministic fallback logic.
- PostgreSQL persistence is optional. If `DATABASE_URL` is unset, analyses are returned without storage.
- The API uses semaphores to cap document, OCR, and LLM concurrency.
- `/health` returns in-memory counters and cumulative timing totals.
- Large or excessive uploads are rejected early to protect throughput and memory usage.

## Related Document

For the architectural reasoning and trade-offs behind these choices, see [tradeoff.md](/Users/wishaak/dp_world/tradeoff.md).

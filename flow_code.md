# dp_world Flow

## 1. What this codebase is

`dp_world` is a document-intake and customs-risk analysis app.

- The `frontend/` is a single-page React UI where users upload shipment documents.
- The `backend/` is a FastAPI service that validates files, extracts text, converts text into structured shipment data, runs compliance checks, optionally retrieves regulation context, and returns a broker-style summary.
- PostgreSQL stores past analyses and optional compliance / tariff / regulation knowledge tables.

## 2. End-to-end runtime flow

### Frontend entry

- `frontend/src/main.jsx` mounts the React app.
- `frontend/src/App.jsx` is the main screen and owns the user flow:
  - select files
  - build `FormData`
  - `POST` to `/api/analyze`
  - show pipeline progress
  - render extracted shipment data, compliance issues, risk score, and summary

### Backend entry

- `backend/app/main.py` creates the FastAPI app.
- On startup, `lifespan()` calls `init_db()` from `backend/app/database.py`.
- `init_db()` creates core tables and enables pgvector tables only if the extension is available.

### Request path

When the user uploads files:

1. The browser sends `multipart/form-data` to `POST /api/analyze`.
2. `main.py` passes the files to `analyze_uploads()` in `backend/app/services/analysis.py`.
3. `analysis.py` validates batch size, file type, and file size.
4. Each file is processed concurrently through `_analyze_single()` under a document semaphore.
5. `ocr.py` extracts text from image-like files with Tesseract, or directly decodes text-based files, with fallback sample text if OCR fails.
6. `llm.py` classifies the document using deterministic keyword rules.
7. `llm.py` then extracts structured shipment data:
   - preferred path: LLM call through the OpenAI-compatible client
   - fallback path: regex / heuristic extraction
8. `compliance.py` evaluates the structured shipment:
   - DB-backed rules if compliance tables are available
   - in-memory rule engine if DB lookups fail
9. `retrieval.py` optionally:
   - embeds a shipment query
   - searches `regulation_chunks` with pgvector similarity
   - asks the LLM for a grounded summary
   - otherwise falls back to a deterministic summary from `build_assistant_summary()`
10. `analysis.py` assembles `DocumentAnalysisResponse`, records performance metadata, and builds an `AnalysisRecord`.
11. `main.py` saves successful records through `save_analyses()` and returns the response list to the frontend.
12. `App.jsx` renders the first result in the dashboard panels.

## 3. Data flow by shape

The main data transformations are:

- `UploadFile` -> raw file bytes
- raw file bytes -> extracted text
- extracted text -> `ExtractedShipmentData`
- `ExtractedShipmentData` -> `ComplianceResult`
- `ExtractedShipmentData` + `ComplianceResult` + optional regulation chunks -> assistant summary
- all of the above -> `DocumentAnalysisResponse`
- `DocumentAnalysisResponse` -> persisted `AnalysisRecord.payload`

The schemas for this contract live in `backend/app/schemas.py`. That file is the typed boundary of the backend.

## 4. Persistence and offline data flow

The database has two roles:

- operational storage:
  - `analysis_records` stores completed analysis payloads
- knowledge storage:
  - `hs_tariffs`
  - `hs_pgas`
  - `country_rules`
  - `world_ports`
  - `regulation_chunks`

Supporting scripts:

- `backend/scripts/seed_compliance_tables.py`
  - seeds tariff chapters, PGA rules, country rules, and world port metadata
- `backend/scripts/load_cbic_tariff.py`
  - loads more specific tariff rows from CSV into `hs_tariffs`
- `backend/scripts/ingest_regulations.py`
  - embeds `backend/data/regulations.json` and stores chunks in `regulation_chunks`

So there are really two flows in the repo:

- online flow: upload -> analyze -> respond -> save result
- offline flow: seed rules / tariffs / regulation embeddings -> improve compliance + retrieval quality

## 5. Why the main components were used

### React

Used because the UI is stateful but still small:

- file selection, upload state, loading state, error state, and result state all live naturally in one client component
- React makes conditional rendering of pipeline stages and result cards simple
- the app currently does not need heavier client architecture

### FastAPI

Used because the backend is an async request orchestrator:

- file uploads are first-class
- async endpoints fit OCR, DB, and LLM orchestration well
- response models via Pydantic keep the API typed and predictable

### Pydantic schemas

Used to make the pipeline safe between stages:

- LLM output is validated into `ExtractedShipmentData`
- compliance and API responses have explicit structure
- invalid model output fails early instead of corrupting later logic

### Service-layer split (`analysis`, `ocr`, `llm`, `compliance`, `retrieval`)

Used to separate concerns cleanly:

- `analysis.py` orchestrates the whole request
- `ocr.py` owns text extraction only
- `llm.py` owns classification and structured extraction
- `compliance.py` owns rules, scoring, and duty estimation
- `retrieval.py` owns RAG-style lookup and grounded summaries

This keeps the route thin and makes each stage replaceable.

### PostgreSQL + SQLAlchemy async

Used because the app needs durable records and structured rule tables:

- analysis history fits relational storage
- tariff, port, and country rules are easier to query and maintain as tables
- async SQLAlchemy matches the FastAPI async stack

### pgvector

Used only for regulation retrieval:

- regulation chunks can be embedded once
- request-time similarity search stays in the same database
- grounded summaries can cite retrieved rule text instead of relying only on generic model memory

### OpenAI-compatible client

Used because the app wants one interface for multiple providers:

- `config.py` can switch between Groq, Gemini, and OpenAI-compatible endpoints
- the same chat-completions style path is reused for extraction and grounded summaries

### OCR with Tesseract + fallback parsing

Used because shipment documents are not always text-native:

- scanned invoices and images need OCR
- text files can bypass OCR
- fallback text extraction keeps the demo resilient even when OCR or LLM paths degrade

### Semaphores, timeouts, and fallbacks

Used to make the system robust under load:

- OCR and LLM are the slowest parts
- bounded concurrency prevents resource spikes
- timeouts stop requests from hanging
- fallback extraction and deterministic summaries keep the pipeline usable

### Tailwind CSS

Used because the frontend is a single-page dashboard with custom visual styling:

- fast iteration on layout and status cards
- low overhead compared to a bigger component library
- enough structure for a custom branded UI without adding many abstractions

## 6. Architectural summary

The codebase is built around one main orchestration idea:

- keep the frontend thin
- keep the API layer thin
- put business steps into dedicated backend services
- use typed schemas between stages
- prefer deterministic fallbacks whenever OCR, DB, or LLM features are unavailable

That design makes the app practical for a prototype: it demonstrates AI-assisted extraction and retrieval, but it still keeps a usable baseline when advanced dependencies are missing.

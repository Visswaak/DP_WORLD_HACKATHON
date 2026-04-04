# File-by-File Explanation

This file explains what each important file in `dp_world` does.

I am describing the actual project files, not Git internals, virtualenv files, `node_modules`, `__pycache__`, or build artifacts.

## Root

### `.env`

- Local runtime environment file.
- Holds real machine-specific values like API keys, DB URL, and runtime settings.
- Used by Docker Compose and local runs.
- Should not be committed.

### `.env.example`

- Safe template for `.env`.
- Shows which environment variables the project expects.
- Used as the starting point when someone sets up the project.

### `.gitignore`

- Tells Git which files and folders should not be tracked.
- Excludes local secrets, Python cache files, frontend build output, and installed dependencies.

### `README.md`

- Main setup and usage document for the project.
- Explains what the app does, how to run it locally or with Docker, environment variables, RAG setup, and high-level flow.

### `CLAUDE.md`

- Project note/instruction file for AI-assisted coding workflows.
- Not part of the runtime system.

### `docker-compose.yml`

- Starts the full stack together:
  - `db` using PostgreSQL + pgvector
  - `backend` FastAPI service
  - `frontend` built static React app served by Nginx
- Central file for container-based local development/demo runs.

### `flow_code.md`

- Architecture and flow explanation document.
- Describes the current end-to-end data flow, architectural decisions, and tradeoffs.

### `explain.md`

- This file.
- Gives a file-by-file explanation of the codebase.

## Backend

### `backend/requirements.txt`

- Python dependency list for the backend.
- Includes FastAPI, SQLAlchemy, OpenAI client, Tesseract bindings, Pillow, psycopg, and pgvector.

### `backend/Dockerfile`

- Container build file for the backend.
- Uses a multi-stage build:
  - installs Python dependencies in a builder image
  - copies them into a smaller runtime image
- Installs Tesseract OCR in the runtime image.
- Starts the API with Uvicorn on port `8000`.

## Backend App Package

### `backend/app/__init__.py`

- Marks `app/` as a Python package.
- No runtime logic by itself.

### `backend/app/main.py`

- Main FastAPI entrypoint.
- Creates the API app.
- Configures:
  - CORS
  - middleware for request metrics
  - exception handlers
  - startup lifespan hooks
- Defines API endpoints:
  - `GET /health`
  - `POST /api/analyze`
  - `GET /api/jobs/{job_id}`
  - `POST /api/cache/reload`
- Reads uploads, creates background jobs, and schedules analysis work.

### `backend/app/config.py`

- Central runtime configuration module.
- Reads environment variables and assigns defaults.
- Controls:
  - model/provider selection
  - DB URL
  - OCR binary path
  - timeouts
  - concurrency limits
  - RAG parameters
  - allowed frontend origins

### `backend/app/database.py`

- Database layer and ORM definitions.
- Defines SQLAlchemy models for:
  - `AnalysisRecord`
  - `HsTariff`
  - `HsPga`
  - `CountryRule`
  - `WorldPort`
  - `RegulationChunk`
- Creates the async engine and session factory.
- Initializes tables at startup.
- Enables pgvector tables when the extension is available.
- Saves completed analysis records.


### `backend/app/errors.py`

- Defines custom exceptions used by the analysis pipeline.
- Currently:
  - `AnalysisError`
  - `FileValidationError`

### `backend/app/observability.py`

- Logging and lightweight metrics module.
- Configures the backend logger.
- Implements a thread-safe in-memory metrics collector.
- Tracks counters and accumulated timing values.
- Used by request middleware and analysis services.

### `backend/app/schemas.py`

- Pydantic models for typed application data.
- Defines the main contracts used across the backend and API responses:
  - shipment item data
  - extracted shipment data
  - compliance issues and results
  - duty estimate
  - job response
  - document analysis response

### `backend/app/jobs.py`

- In-memory job tracking module.
- Stores background job state in a Python dictionary.
- Defines:
  - job status enum
  - job dataclass
  - create / get / update job functions
- Also prunes old jobs with a TTL so the dictionary does not grow forever.

### `backend/app/cache.py`

- In-process compliance cache.
- Loads compliance-related DB tables into memory at startup:
  - PGA rules
  - ports
  - country rules
  - tariffs
- Lets compliance checks avoid repeated DB queries after warmup.
- Supports manual cache reload after reseeding.

## Backend Services

### `backend/app/services/__init__.py`

- Marks the `services/` folder as a Python package.
- No business logic by itself.

### `backend/app/services/analysis.py`

- Main orchestration layer for document analysis.
- Validates uploads.
- Runs one async analysis task per file.
- Controls per-document concurrency.
- For each file, coordinates:
  - file validation
  - OCR/text extraction
  - document classification
  - structured extraction
  - compliance evaluation
  - regulation retrieval
  - summary generation
- Builds the final response object and DB record payload.

### `backend/app/services/ocr.py`

- OCR and text extraction module.
- Uses Tesseract through `pytesseract` for image-like documents.
- Uses direct UTF-8 decoding for text-based files.
- Runs OCR in a bounded thread pool so blocking OCR work does not block the event loop.
- Returns fallback text if OCR times out or cannot extract usable text.

### `backend/app/services/llm.py`

- Document classification and structured extraction module.
- Does two things:
  - classifies document type using deterministic keyword rules
  - extracts structured shipment data using an OpenAI-compatible model
- If LLM extraction fails, times out, or no API key exists, it falls back to regex/heuristic extraction.
- Validates model output into `ExtractedShipmentData`.

### `backend/app/services/compliance.py`

- Customs/compliance engine.
- Evaluates extracted shipment data against customs-style rules.
- Produces:
  - issues
  - suggestions
  - risk level
  - clearance prediction
  - duty estimate
- Uses cached DB-backed rules when available.
- Falls back to direct DB queries if cache is cold.
- Falls back again to hardcoded in-memory rules if DB-backed evaluation fails.
- Also builds the deterministic assistant summary when RAG is unavailable.

### `backend/app/services/retrieval.py`

- RAG layer.
- Builds an embedding query from shipment data.
- Retrieves the most relevant regulation chunks from `regulation_chunks` using pgvector similarity search.
- Calls the LLM again to generate a grounded broker-style summary using those retrieved excerpts.
- Falls back to the deterministic summary if retrieval or the LLM path is unavailable.

## Backend Data

### `backend/data/regulations.json`

- Local regulation corpus used for RAG ingestion.
- Contains regulation text chunks with titles and sources.
- These chunks are embedded and stored in `regulation_chunks` for retrieval.

## Backend Scripts

### `backend/scripts/seed_compliance_tables.py`

- Seeds the compliance-related DB tables with baseline data.
- Populates:
  - chapter-level tariffs
  - PGA rules
  - country rules
  - world ports
- Used when setting up the knowledge base for compliance evaluation.

### `backend/scripts/load_cbic_tariff.py`

- Loads tariff data from a CSV file into `hs_tariffs`.
- Used to replace rough chapter-level defaults with more specific tariff entries.
- Performs upserts so existing tariff rows can be updated.

### `backend/scripts/ingest_regulations.py`

- Loads `backend/data/regulations.json`, creates embeddings for each chunk, and inserts them into `regulation_chunks`.
- Enables the RAG retrieval path.

## Frontend

### `frontend/package.json`

- Frontend project manifest.
- Defines scripts like:
  - `npm run dev`
  - `npm run build`
  - `npm run preview`
- Lists React/Vite/Tailwind dependencies.

### `frontend/package-lock.json`

- Exact dependency lockfile for npm.
- Ensures reproducible installs.

### `frontend/vite.config.js`

- Vite bundler configuration.
- Enables the React plugin.

### `frontend/tailwind.config.js`

- Tailwind CSS configuration.
- Defines where Tailwind should scan for class names.
- Extends the theme with custom colors, fonts, and shadow tokens used by the UI.

### `frontend/postcss.config.js`

- PostCSS configuration for frontend CSS processing.
- Supports Tailwind compilation pipeline.

### `frontend/index.html`

- HTML shell for the React app.
- Contains the root element where React mounts.

### `frontend/.env`

- Frontend-local environment file.
- Usually used to define `VITE_API_URL` or other Vite-exposed runtime build variables.
- Not intended as core source code.

### `frontend/src/main.jsx`

- Frontend entrypoint.
- Mounts the React app into the DOM.
- Imports the root app component and global styles.

### `frontend/src/App.jsx`

- Main frontend UI and workflow component.
- Handles:
  - file selection
  - form submission
  - job polling
  - loading/error/result state
  - pipeline progress visualization
  - result rendering
- Also includes small presentational components like cards and the error boundary.

### `frontend/src/index.css`

- Global frontend CSS entry file.
- Imports Tailwind layers.
- Defines global page styling such as background, base font, and glass effect styles.

### `frontend/Dockerfile`

- Container build file for the frontend.
- Builds the React app with Node/Vite.
- Copies the final static files into an Nginx image for serving.

### `frontend/nginx.conf`

- Nginx config used inside the frontend container.
- Serves the built frontend.
- Includes `try_files` fallback so client-side routing works correctly.
- Disables caching for `index.html` so new deploys show up immediately.

## Files That Exist But Are Not Core Source

### `frontend/dist/index.html`

- Built output generated by `npm run build`.
- Not the source of the frontend; the real source is under `frontend/src/`.

### `backend/.venv/pyvenv.cfg`

- Python virtual environment metadata.
- Created locally when the backend virtualenv is set up.
- Not part of the application logic.

### `.git/...`

- Git repository metadata.
- Tracks version-control history and Git settings.
- Not part of the product runtime.

## Simple Mental Model

If you want to think about the codebase quickly:

- `frontend/src/App.jsx` = user interaction
- `backend/app/main.py` = API boundary
- `backend/app/services/analysis.py` = pipeline orchestrator
- `backend/app/services/ocr.py` = text extraction
- `backend/app/services/llm.py` = classification + structured extraction
- `backend/app/services/compliance.py` = rules engine
- `backend/app/services/retrieval.py` = RAG
- `backend/app/database.py` = persistence + knowledge tables
- `backend/app/jobs.py` = background job state
- `backend/app/cache.py` = in-memory compliance acceleration

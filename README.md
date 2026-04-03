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



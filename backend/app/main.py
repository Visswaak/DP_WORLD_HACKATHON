from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import cache
from app import jobs as jobs_module
from app.config import settings
from app.database import init_db, save_analyses
from app.errors import AnalysisError, FileValidationError
from app.jobs import JobStatus
from app.observability import logger, metrics
from app.schemas import DocumentAnalysisResponse, JobResponse
from app.services.analysis import analyze_uploads


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await cache.warm_cache()          # loads PGA, ports, country rules, tariffs into memory
    if not settings.active_api_key:
        logger.warning(
            "No LLM API key set (GEMINI_API_KEY or OPENAI_API_KEY) — "
            "LLM extraction and RAG retrieval are disabled. "
            "The system will use regex fallback extraction and deterministic summaries."
        )
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


# ── Request metrics middleware ────────────────────────────────────────────────

@app.middleware("http")
async def add_request_metrics(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    started = perf_counter()
    metrics.incr("http_requests_total")
    try:
        response = await call_next(request)
    except Exception:
        metrics.incr("http_requests_failed")
        logger.exception("request_failed path=%s method=%s", request.url.path, request.method)
        raise
    response.headers["X-Process-Time-Ms"] = str(round((perf_counter() - started) * 1000, 2))
    return response


# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(FileValidationError)
async def file_validation_exception_handler(_: Request, exc: FileValidationError) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(AnalysisError)
async def analysis_exception_handler(_: Request, exc: AnalysisError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "cache_warm": cache.is_warm(),
        "metrics": metrics.snapshot(),
    }


# ── File adapter ──────────────────────────────────────────────────────────────

class _MockUpload:
    """Minimal UploadFile-compatible adapter for pre-read file bytes.

    UploadFile objects are bound to the HTTP request and become unusable
    after the response is sent. We read all content eagerly in the endpoint
    and wrap it in this adapter so background tasks can use the existing
    analyze pipeline unchanged.
    """
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


# ── Background job runner ─────────────────────────────────────────────────────

async def _run_job(job_id: str, file_data: list[tuple[str, bytes]]) -> None:
    jobs_module.update_job(job_id, JobStatus.PROCESSING)
    try:
        mock_files = [_MockUpload(fname, content) for fname, content in file_data]
        artifacts = await analyze_uploads(mock_files)  # type: ignore[arg-type]

        artifacts_with_records = [(i, a) for i, a in enumerate(artifacts) if a.record is not None]
        records = [a.record for _, a in artifacts_with_records]
        record_ids = await save_analyses(records)
        for (_, artifact), db_id in zip(artifacts_with_records, record_ids):
            artifact.response.analysis_id = db_id

        result = [a.response.model_dump(mode="json") for a in artifacts]
        jobs_module.update_job(job_id, JobStatus.DONE, result=result)

    except Exception as exc:
        logger.exception("job_failed job_id=%s", job_id)
        jobs_module.update_job(job_id, JobStatus.FAILED, error=str(exc))


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.post("/api/analyze")
async def submit_analysis(
    files: list[UploadFile] = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> dict[str, str]:
    """Accept documents, return a job_id immediately.

    The caller polls GET /api/jobs/{job_id} for status and results.
    Files are read into memory here (before the response) because
    UploadFile objects are request-scoped and become invalid afterwards.
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Limit is {settings.max_files_per_request} per request.",
        )

    file_data: list[tuple[str, bytes]] = []
    for f in files:
        content = await f.read()
        file_data.append((f.filename or "unnamed-document", content))

    job_id = jobs_module.create_job()
    background_tasks.add_task(_run_job, job_id, file_data)
    return {"job_id": job_id, "status": JobStatus.QUEUED}


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """Poll for job status and result."""
    job = jobs_module.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    result: list[DocumentAnalysisResponse] | None = None
    if job.result is not None:
        result = [DocumentAnalysisResponse.model_validate(r) for r in job.result]

    return JobResponse(
        job_id=job.id,
        status=job.status,
        result=result,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@app.post("/api/cache/reload")
async def reload_cache() -> dict[str, str]:
    """Force-reload compliance cache from DB. Call after re-seeding tables."""
    await cache.reload_cache()
    return {"status": "ok"}

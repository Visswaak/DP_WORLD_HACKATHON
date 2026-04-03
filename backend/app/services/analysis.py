from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from fastapi import UploadFile

from app.config import settings
from app.database import AnalysisRecord
from app.errors import AnalysisError, FileValidationError
from app.observability import logger, metrics
from app.schemas import DocumentAnalysisResponse
from app.services.compliance import evaluate_compliance
from app.services.llm import classify_document, extract_structured_data
from app.services.ocr import extract_text
from app.services.retrieval import generate_grounded_summary, retrieve_regulations


DOCUMENT_SEMAPHORE = asyncio.Semaphore(settings.max_parallel_documents)
# OCR concurrency is now enforced by the bounded ThreadPoolExecutor in ocr.py


@dataclass(slots=True)
class AnalysisArtifacts:
    response: DocumentAnalysisResponse
    record: AnalysisRecord | None


def validate_uploads(files: list[UploadFile]) -> None:
    if not files:
        raise FileValidationError("At least one file is required.")
    if len(files) > settings.max_files_per_request:
        raise FileValidationError(
            f"Too many files in one request. Limit is {settings.max_files_per_request}."
        )


async def analyze_uploads(files: list[UploadFile]) -> list[AnalysisArtifacts]:
    validate_uploads(files)
    tasks = [asyncio.create_task(_analyze_single(upload)) for upload in files]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    artifacts: list[AnalysisArtifacts] = []
    for upload, result in zip(files, results):
        if isinstance(result, FileValidationError):
            # Validation errors (file too large, too many files) abort the whole batch
            raise result
        elif isinstance(result, BaseException):
            filename = upload.filename or "unnamed-document"
            logger.error("per_file_analysis_failed filename=%s error=%s: %s", filename, type(result).__name__, result)
            metrics.incr("rejected_files")
        else:
            artifacts.append(result)

    if not artifacts:
        raise AnalysisError("All documents failed to process.")

    return artifacts


async def _analyze_single(upload: UploadFile) -> AnalysisArtifacts:
    async with DOCUMENT_SEMAPHORE:
        filename = upload.filename or "unnamed-document"
        request_started = perf_counter()
        _validate_file_type(filename)
        content = await upload.read()
        _validate_file_size(filename, len(content))

        metrics.incr("documents_received")
        logger.info("analysis_started filename=%s size_bytes=%s", filename, len(content))

        text_started = perf_counter()
        text = await extract_text(filename, content)
        metrics.timing("ocr_ms", (perf_counter() - text_started) * 1000)

        classification = classify_document(filename, text)
        extraction_started = perf_counter()
        extracted_data, raw_model_output = await extract_structured_data(filename, text)
        metrics.timing("llm_ms", (perf_counter() - extraction_started) * 1000)

        compliance_started = perf_counter()
        compliance = await evaluate_compliance(extracted_data)
        regulation_chunks = await retrieve_regulations(extracted_data)
        assistant_summary = await generate_grounded_summary(extracted_data, compliance, regulation_chunks)
        metrics.timing("compliance_ms", (perf_counter() - compliance_started) * 1000)

        response = DocumentAnalysisResponse(
            filename=filename,
            classification=classification,
            extracted_text_preview=text[: settings.extracted_preview_length],
            extracted_data=extracted_data,
            compliance=compliance,
            assistant_summary=assistant_summary,
            processing_stages=_build_processing_stages(raw_model_output["mode"]),
            raw_model_output=raw_model_output,
            performance={
                "total_ms": round((perf_counter() - request_started) * 1000, 2),
                "text_chars": len(text),
                "degraded": raw_model_output["mode"] != "openai",
            },
        )

        record = AnalysisRecord(
            filename=response.filename,
            classification=response.classification,
            risk_level=response.compliance.risk_level,
            clearance_prediction=response.compliance.clearance_prediction,
            payload=response.model_dump(mode="json"),
        )

        metrics.incr("documents_processed")
        if raw_model_output["mode"] != "openai":
            metrics.incr("fallback_extractions")

        logger.info(
            "analysis_finished filename=%s classification=%s risk=%s mode=%s duration_ms=%.2f",
            filename,
            classification,
            compliance.risk_level,
            raw_model_output["mode"],
            response.performance["total_ms"],
        )
        return AnalysisArtifacts(response=response, record=record)


_ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp",
    ".txt", ".csv", ".json", ".xml",
}


def _validate_file_type(filename: str) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        metrics.incr("rejected_files")
        raise FileValidationError(
            f"{filename}: unsupported file type '{ext}'. "
            f"Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )


def _validate_file_size(filename: str, size_bytes: int) -> None:
    if size_bytes > settings.max_file_size_bytes:
        metrics.incr("rejected_files")
        raise FileValidationError(
            f"{filename} exceeds the file size limit of {settings.max_file_size_bytes} bytes."
        )


def _build_processing_stages(mode: str) -> list[dict[str, str]]:
    extraction_status = "degraded" if mode != "openai" else "completed"
    return [
        {"name": "Upload", "status": "completed"},
        {"name": "OCR extraction", "status": "completed"},
        {"name": "AI classification", "status": "completed"},
        {"name": "LLM extraction", "status": extraction_status},
        {"name": "Compliance engine", "status": "completed"},
        {"name": "Prediction", "status": "completed"},
    ]

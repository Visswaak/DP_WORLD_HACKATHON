from __future__ import annotations

import asyncio
from typing import Any

from openai import OpenAI
from sqlalchemy import select

import app.database as _db
from app.config import settings
from app.database import RegulationChunk, SessionLocal
from app.observability import logger
from app.schemas import ComplianceResult, ExtractedShipmentData
from app.services.compliance import build_assistant_summary
from app.services.llm import OPENAI_SEMAPHORE  # shared budget for all outbound OpenAI calls

_openai_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(
            api_key=settings.active_api_key,
            base_url=settings.active_api_base_url,
        )
    return _openai_client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def retrieve_regulations(data: ExtractedShipmentData) -> list[str]:
    """Return top-k regulation text chunks most relevant to this shipment.

    Returns an empty list when the database, pgvector, or OpenAI is not
    configured — callers should treat an empty list as "no retrieval available"
    and fall back to the deterministic summary.
    """
    if not SessionLocal or not _db.rag_available or not settings.active_api_key:
        return []

    try:
        query = _build_query(data)
        embedding = await _embed(query)
        return await _search(embedding)
    except Exception as exc:
        logger.warning("retrieval_failed error=%s: %s", type(exc).__name__, exc)
        return []


async def generate_grounded_summary(
    data: ExtractedShipmentData,
    compliance: ComplianceResult,
    chunks: list[str],
) -> str:
    """Generate a cited, regulation-grounded broker summary.

    Falls back to the deterministic templated summary when chunks are empty
    or the OpenAI API is unavailable.
    """
    if not chunks or not settings.active_api_key:
        return build_assistant_summary(data, compliance)

    try:
        return await _grounded_llm_call(data, compliance, chunks)
    except Exception as exc:
        logger.warning("grounded_summary_failed error=%s: %s", type(exc).__name__, exc)
        return build_assistant_summary(data, compliance)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_query(data: ExtractedShipmentData) -> str:
    hs_codes = ", ".join(item.hs_code for item in data.items)
    origins = ", ".join(sorted({item.country_of_origin for item in data.items}))
    return (
        f"HS codes: {hs_codes}. Countries of origin: {origins}. "
        f"Port of entry: {data.port_of_entry}. "
        f"Shipment value USD {data.shipment_value_usd:,.2f}. "
        f"Incoterm: {data.incoterm}."
    )


async def _embed(query: str) -> list[float]:
    client = _get_client()
    async with OPENAI_SEMAPHORE:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.embeddings.create,
                model=settings.embedding_model,
                input=query,
            ),
            timeout=settings.api_timeout_seconds,
        )
    return response.data[0].embedding


async def _search(embedding: list[float]) -> list[str]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(RegulationChunk.source, RegulationChunk.title, RegulationChunk.text)
            .order_by(RegulationChunk.embedding.cosine_distance(embedding))
            .limit(settings.rag_top_k)
        )
        rows = result.fetchall()

    # Format each chunk with its citation header so the LLM can reference it
    return [f"[{row.source}]\n{row.title}\n\n{row.text}" for row in rows]


async def _grounded_llm_call(
    data: ExtractedShipmentData,
    compliance: ComplianceResult,
    chunks: list[str],
) -> str:
    client = _get_client()

    issues_text = "\n".join(
        f"- [{issue.severity.upper()}] {issue.title}: {issue.detail}"
        for issue in compliance.issues
    ) or "No compliance flags detected."

    regulation_text = "\n\n---\n\n".join(chunks)

    origins = ", ".join(sorted({item.country_of_origin for item in data.items}))
    hs_codes = ", ".join(item.hs_code for item in data.items)

    prompt = (
        "You are a licensed U.S. customs broker. Using ONLY the regulation excerpts provided below, "
        "give specific, actionable clearance guidance for this shipment. "
        "For each requirement you identify, cite the regulation source in brackets. "
        "If the excerpts do not cover a flag, say so rather than inventing a citation. "
        "Maximum 5 sentences.\n\n"
        "SHIPMENT DETAILS:\n"
        f"  Importer: {data.importer}\n"
        f"  Exporter: {data.exporter}\n"
        f"  Country of Origin: {origins}\n"
        f"  HS Codes: {hs_codes}\n"
        f"  Declared Value: USD {data.shipment_value_usd:,.2f}\n"
        f"  Port of Entry: {data.port_of_entry}\n"
        f"  Incoterm: {data.incoterm}\n\n"
        "COMPLIANCE FLAGS:\n"
        f"{issues_text}\n\n"
        "REGULATION EXCERPTS:\n"
        f"{regulation_text}\n\n"
        "BROKER GUIDANCE:"
    )

    response = await asyncio.wait_for(
        asyncio.to_thread(
            client.chat.completions.create,
            model=settings.openai_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=settings.grounded_summary_max_tokens,
        ),
        timeout=settings.api_timeout_seconds,
    )
    return (response.choices[0].message.content or "").strip()

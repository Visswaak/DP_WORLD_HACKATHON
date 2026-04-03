#!/usr/bin/env python3
"""
Ingest regulation chunks into pgvector for RAG retrieval.

Usage:
    cd backend
    source .venv/bin/activate
    OPENAI_API_KEY=sk-... DATABASE_URL=postgresql+psycopg://... python scripts/ingest_regulations.py

The script is idempotent: it clears existing chunks before reinserting,
so it can be re-run safely when the corpus changes.

Requires:
    - DATABASE_URL pointing to a Postgres instance with pgvector installed
    - OPENAI_API_KEY for generating embeddings
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make app imports work when running from the backend directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.config import settings
from app.database import RegulationChunk, SessionLocal, engine, init_db
from app.services.retrieval import _embed

CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "regulations.json"


async def ingest() -> None:
    if not engine:
        print("ERROR: DATABASE_URL is not set. Set it before running this script.")
        sys.exit(1)

    if not settings.openai_api_key:
        print("ERROR: OPENAI_API_KEY is not set. Embeddings require the OpenAI API.")
        sys.exit(1)

    chunks = json.loads(CORPUS_PATH.read_text())
    print(f"Loaded {len(chunks)} regulation chunks from {CORPUS_PATH}")

    await init_db()

    if not SessionLocal:
        print("ERROR: Database session could not be created.")
        sys.exit(1)

    async with SessionLocal() as session:
        async with session.begin():
            await session.execute(text("DELETE FROM regulation_chunks"))
            print("Cleared existing regulation chunks.")

            for i, chunk in enumerate(chunks, 1):
                print(f"  [{i}/{len(chunks)}] Embedding: {chunk['title']}")
                embedding = await _embed(chunk["text"])
                session.add(RegulationChunk(
                    source=chunk["source"],
                    title=chunk["title"],
                    text=chunk["text"],
                    embedding=embedding,
                ))

    print(f"\nDone. {len(chunks)} regulation chunks ingested successfully.")
    print("The API server will use these for RAG retrieval on the next request.")


if __name__ == "__main__":
    asyncio.run(ingest())

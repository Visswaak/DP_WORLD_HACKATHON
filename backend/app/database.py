from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

from app.config import settings
from app.observability import logger


class Base(DeclarativeBase):
    pass


class AnalysisRecord(Base):
    __tablename__ = "analysis_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), index=True)
    classification: Mapped[str] = mapped_column(String(100), index=True)
    risk_level: Mapped[str] = mapped_column(String(40), index=True)
    clearance_prediction: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class HsTariff(Base):
    __tablename__ = "hs_tariffs"

    hs_code: Mapped[str] = mapped_column(String(8), primary_key=True)   # 2-digit chapter OR 8-digit ITC-HS
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    bcd_rate: Mapped[float] = mapped_column(Numeric(6, 4))
    igst_rate: Mapped[float] = mapped_column(Numeric(6, 4))
    compensation_cess_rate: Mapped[float] = mapped_column(Numeric(6, 4), server_default="0")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HsPga(Base):
    __tablename__ = "hs_pgas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hs_prefix: Mapped[str] = mapped_column(String(8), index=True)   # 2, 4, 6, or 8 digit prefix
    agency: Mapped[str] = mapped_column(String(50))                 # FSSAI, BIS_CRS, WPC, CDSCO, etc.
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    detail_template: Mapped[str] = mapped_column(Text)              # use {hs_code} as placeholder
    regulation_ref: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class CountryRule(Base):
    __tablename__ = "country_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country_name: Mapped[str] = mapped_column(String(100), index=True)   # lowercase
    rule_type: Mapped[str] = mapped_column(String(30))   # SANCTIONED, RESTRICTED, HIGH_RISK, FTA
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    regulation_ref: Mapped[str] = mapped_column(Text)
    fta_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class WorldPort(Base):
    __tablename__ = "world_ports"

    port_code: Mapped[str] = mapped_column(String(10), primary_key=True)   # UN/LOCODE
    port_name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(100))
    country_code: Mapped[str] = mapped_column(String(2))
    region: Mapped[str] = mapped_column(String(50))
    port_type: Mapped[str] = mapped_column(String(20))                     # SEA, AIR, LAND, ICD
    is_indian_entry_port: Mapped[bool] = mapped_column(Boolean, server_default="false")
    risk_level: Mapped[str] = mapped_column(String(20))                    # LOW, MEDIUM, HIGH, SCRUTINY
    risk_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    annual_teu_millions: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class RegulationChunk(Base):
    __tablename__ = "regulation_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(400))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(1536))


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
) if settings.database_url else None

SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False) if engine else None

# Tracks whether pgvector is available; set during init_db
rag_available: bool = False


async def init_db() -> None:
    global rag_available
    if not engine:
        return

    async with engine.begin() as connection:
        # Core tables: always create
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[
                    AnalysisRecord.__table__,
                    HsTariff.__table__,
                    HsPga.__table__,
                    CountryRule.__table__,
                    WorldPort.__table__,
                ],
            )
        )

        # pgvector table: only if the extension is available in Postgres
        try:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(
                lambda conn: Base.metadata.create_all(conn, tables=[RegulationChunk.__table__])
            )
            rag_available = True
        except Exception as exc:
            logger.warning(
                "pgvector_unavailable reason=%s — regulation retrieval disabled. "
                "Install pgvector on your Postgres server to enable RAG.",
                exc,
            )


async def save_analyses(records: Sequence[AnalysisRecord]) -> list[int]:
    if not SessionLocal or not records:
        return []

    async with SessionLocal() as session:
        session: AsyncSession
        async with session.begin():
            session.add_all(records)
            await session.flush()
            return [record.id for record in records]

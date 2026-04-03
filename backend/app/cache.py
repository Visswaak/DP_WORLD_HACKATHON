from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.database import CountryRule, HsPga, HsTariff, SessionLocal, WorldPort
from app.observability import logger

_pgas: list[HsPga] = []
_ports: list[WorldPort] = []
_country_rules: list[CountryRule] = []
_tariff_map: dict[str, HsTariff] = {}
_warm: bool = False
_lock = asyncio.Lock()


async def warm_cache() -> None:
    """Load all compliance tables into process memory at startup.

    Called once from the FastAPI lifespan. After this, every compliance
    evaluation runs entirely in-process — zero DB queries per request.
    """
    global _pgas, _ports, _country_rules, _tariff_map, _warm

    if not SessionLocal:
        logger.info("compliance_cache_skipped reason=no_database")
        return

    async with _lock:
        async with SessionLocal() as session:
            _pgas = list(
                (await session.execute(select(HsPga).where(HsPga.is_active.is_(True)))).scalars().all()
            )
            _ports = list(
                (await session.execute(select(WorldPort).where(WorldPort.is_active.is_(True)))).scalars().all()
            )
            _country_rules = list(
                (await session.execute(select(CountryRule).where(CountryRule.is_active.is_(True)))).scalars().all()
            )
            _tariff_map = {
                row.hs_code: row
                for row in (await session.execute(select(HsTariff))).scalars().all()
            }

        _warm = True
        logger.info(
            "compliance_cache_warmed pgas=%d ports=%d country_rules=%d tariffs=%d",
            len(_pgas),
            len(_ports),
            len(_country_rules),
            len(_tariff_map),
        )


async def reload_cache() -> None:
    """Force-reload the cache from DB. Call after seeding or bulk updates."""
    global _warm
    _warm = False
    await warm_cache()


def is_warm() -> bool:
    return _warm


def get_pgas() -> list[HsPga]:
    return _pgas


def get_ports() -> list[WorldPort]:
    return _ports


def get_country_rules() -> list[CountryRule]:
    return _country_rules


def get_tariff_map() -> dict[str, HsTariff]:
    return _tariff_map

"""cache.py — intentionally empty.

The in-process compliance cache was removed because it cannot scale to a
production-sized compliance DB (12,000+ tariff rows, 5,000+ PGA rules,
100,000+ ports). Loading full tables into per-process memory:
  - blows up pod RAM linearly with data growth
  - causes staleness across workers and pods with no reliable invalidation
  - replaces fast indexed DB queries with slow Python list scans at scale

All compliance lookups now go directly to Postgres via targeted indexed queries
in services/compliance.py. The DB handles the scale; the app stays stateless.

These stubs are kept so nothing breaks if the module is imported elsewhere.
"""


async def warm_cache() -> None:
    pass


async def reload_cache() -> None:
    pass


def is_warm() -> bool:
    return False

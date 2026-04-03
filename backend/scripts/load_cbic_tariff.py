#!/usr/bin/env python3
"""
Load 8-digit CBIC tariff data from a CSV file into the hs_tariffs table.

Usage:
    cd backend
    source .venv/bin/activate
    DATABASE_URL=postgresql+psycopg://... python scripts/load_cbic_tariff.py path/to/tariff.csv

CSV columns (header row required):
    hs_code,description,bcd_rate,igst_rate[,compensation_cess_rate][,notes]

    - hs_code             : 2–8 digit HS / ITC-HS code (string, leading zeros preserved)
    - description         : Human-readable description of the tariff line
    - bcd_rate            : Basic Customs Duty rate as a decimal (e.g. 0.10 for 10%)
    - igst_rate           : IGST rate as a decimal (e.g. 0.18 for 18%)
    - compensation_cess_rate (optional): Compensation cess rate, defaults to 0
    - notes (optional)    : Free-text notes or exemption notification references

Rows are upserted: existing hs_code rows are updated, new rows are inserted.

Requires:
    - DATABASE_URL pointing to a Postgres instance (tables must already exist — run
      seed_compliance_tables.py first or start the API server once to create tables)
"""
from __future__ import annotations

import asyncio
import csv
import sys
from pathlib import Path

# Make app imports work when running from the backend directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import HsTariff, SessionLocal, engine, init_db

REQUIRED_COLUMNS = {"hs_code", "description", "bcd_rate", "igst_rate"}
OPTIONAL_COLUMNS = {"compensation_cess_rate", "notes"}


def _load_csv(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            print("ERROR: CSV file appears to be empty or has no header row.")
            sys.exit(1)

        headers = {h.strip().lower() for h in reader.fieldnames}
        missing = REQUIRED_COLUMNS - headers
        if missing:
            print(f"ERROR: CSV is missing required columns: {', '.join(sorted(missing))}")
            print(f"       Found columns: {', '.join(sorted(headers))}")
            sys.exit(1)

        rows = []
        for line_num, raw_row in enumerate(reader, start=2):
            # Normalise keys
            row = {k.strip().lower(): v.strip() for k, v in raw_row.items() if k}

            hs_code = row.get("hs_code", "").strip()
            if not hs_code:
                print(f"  WARNING: Skipping row {line_num} — empty hs_code.")
                continue
            if not (2 <= len(hs_code) <= 8):
                print(
                    f"  WARNING: Skipping row {line_num} — hs_code '{hs_code}' must be "
                    f"2–8 characters."
                )
                continue

            try:
                bcd_rate = float(row["bcd_rate"])
                igst_rate = float(row["igst_rate"])
            except (ValueError, KeyError) as exc:
                print(f"  WARNING: Skipping row {line_num} — invalid numeric value: {exc}")
                continue

            compensation_cess_rate_str = row.get("compensation_cess_rate", "").strip()
            try:
                compensation_cess_rate = float(compensation_cess_rate_str) if compensation_cess_rate_str else 0.0
            except ValueError:
                print(
                    f"  WARNING: Row {line_num} — invalid compensation_cess_rate "
                    f"'{compensation_cess_rate_str}', defaulting to 0."
                )
                compensation_cess_rate = 0.0

            rows.append({
                "hs_code": hs_code,
                "description": row.get("description") or None,
                "bcd_rate": bcd_rate,
                "igst_rate": igst_rate,
                "compensation_cess_rate": compensation_cess_rate,
                "notes": row.get("notes") or None,
            })

    return rows


async def load(csv_path: Path) -> None:
    if not engine:
        print("ERROR: DATABASE_URL is not set. Set it before running this script.")
        sys.exit(1)

    await init_db()

    if not SessionLocal:
        print("ERROR: Database session could not be created.")
        sys.exit(1)

    rows = _load_csv(csv_path)
    if not rows:
        print("No valid rows found in CSV. Nothing to load.")
        sys.exit(0)

    print(f"Loaded {len(rows)} valid rows from {csv_path}.")

    upserted = 0
    async with SessionLocal() as session:
        async with session.begin():
            for row in rows:
                stmt = (
                    pg_insert(HsTariff)
                    .values(**row)
                    .on_conflict_do_update(
                        index_elements=["hs_code"],
                        set_={
                            "description": row["description"],
                            "bcd_rate": row["bcd_rate"],
                            "igst_rate": row["igst_rate"],
                            "compensation_cess_rate": row["compensation_cess_rate"],
                            "notes": row["notes"],
                        },
                    )
                )
                await session.execute(stmt)
                upserted += 1

    print(f"Done. {upserted} row(s) upserted into hs_tariffs.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"ERROR: File not found: {csv_path}")
        sys.exit(1)
    if not csv_path.is_file():
        print(f"ERROR: Not a file: {csv_path}")
        sys.exit(1)

    asyncio.run(load(csv_path))

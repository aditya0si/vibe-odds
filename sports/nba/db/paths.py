"""Filesystem layout for the NBA adapter (imported by every ingest module)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # repo root
DATA = ROOT / "sports" / "nba" / "data"
DB = DATA / "nba.sqlite"
RAW = DATA / "raw"
PARQUET = DATA / "parquet"
SCHEMA = ROOT / "sports" / "nba" / "db" / "schema.sql"

for _d in (DATA, RAW, PARQUET):
    _d.mkdir(parents=True, exist_ok=True)

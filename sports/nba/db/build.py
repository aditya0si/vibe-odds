"""Create / inspect the NBA database.

    python -m sports.nba.db.build init          # create schema if missing (idempotent)
    python -m sports.nba.db.build from-empty    # wipe and rebuild the schema
    python -m sports.nba.db.build status        # row counts per table
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone

from sports.nba.db import paths


def connect() -> sqlite3.Connection:
    paths.DATA.mkdir(parents=True, exist_ok=True)
    # timeout + busy_timeout: ingest jobs run as separate processes (e.g. box scores while odds
    # ingest), and SQLite allows one writer at a time - wait for it instead of failing. 120s is
    # deliberate: a season-sized odds write can legitimately hold the lock for tens of seconds.
    con = sqlite3.connect(paths.DB, timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 120000")
    return con


def init(verbose: bool = True) -> sqlite3.Connection:
    """Apply the schema (CREATE IF NOT EXISTS everywhere, so this is safe to re-run)."""
    con = connect()
    # Skip the schema script when the DB already has tables: executescript needs a write lock,
    # and a concurrent ingest (separate process) legitimately holds one. This keeps parallel
    # ingest jobs from fighting at startup.
    have_tables = con.execute(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='games'").fetchone()["n"] > 0
    if not have_tables:
        con.executescript(paths.SCHEMA.read_text(encoding="utf-8"))
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('db_built_at', ?)",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    con.commit()
    if verbose:
        print(f"schema applied -> {paths.DB}")
    return con


def from_empty() -> sqlite3.Connection:
    if paths.DB.exists():
        paths.DB.unlink()
        print(f"removed {paths.DB}")
    for suffix in ("-wal", "-shm"):
        p = paths.DB.with_name(paths.DB.name + suffix)
        if p.exists():
            p.unlink()
    return init()


def status() -> int:
    if not paths.DB.exists():
        print(f"no database at {paths.DB} (run: python -m sports.nba.db.build init)")
        return 1
    con = connect()
    tables = [r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    width = max(len(t) for t in tables)
    total = 0
    for t in tables:
        n = con.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        total += n
        print(f"  {t:<{width}}  {n:>10,}")
    print(f"  {'TOTAL':<{width}}  {total:>10,}  ({paths.DB})")
    flags = con.execute("SELECT key, value FROM meta WHERE key LIKE 'season:%'").fetchall()
    if flags:
        done = [r["key"].split(":")[1] for r in flags if r["key"].endswith(":game_logs:complete")]
        print(f"  seasons with complete game logs: {len(done)} ({', '.join(sorted(done)[:6])}...)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NBA database build/status tooling")
    ap.add_argument("command", choices=("init", "from-empty", "status"))
    args = ap.parse_args(argv)
    if args.command == "init":
        init()
        return 0
    if args.command == "from-empty":
        from_empty()
        return 0
    return status()


if __name__ == "__main__":
    sys.exit(main())

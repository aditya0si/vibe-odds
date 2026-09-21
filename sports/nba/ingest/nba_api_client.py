"""Shared nba_api transport: retries, raw caching, idempotent units of work.

Why this exists (measured in Phase 0, see docs/phase0/verification-notes.md):
  * nba_api calls occasionally return 0-byte / non-JSON bodies. It is content
    flakiness, not rate limiting - 0 HTTP 429s in 150+ calls - so a short
    retry ladder fixes it and no sleep is needed between calls.
  * v3 endpoints have no ``resultSets`` key: use ``get_data_sets(endpoint)``.
"""

from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from sports.nba.db import paths

BACKOFF = (1.0, 3.0, 8.0)          # seconds between attempts (short: see note below)
# Note on timeouts: stats.nba.com intermittently stalls for 20-60s and then answers
# normally (measured: 25.13s timeout followed by 0.75s success on the same URL).
# So a SHORT timeout with cheap retries beats a long timeout: a 60s timeout with a
# 3-attempt ladder costs ~200s per stalled endpoint, which is fatal at 27k games.
CALL_TIMEOUT = 20
SEASONS = [f"{y}-{str(y + 1)[-2:]}" for y in range(2005, 2026)]   # 2005-06 .. 2025-26


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def call_with_retry(fn: Callable[[], object], *, label: str = "", attempts: int = 4,
                    verbose: bool = False) -> tuple[object | None, list[str]]:
    """Call fn with the backoff ladder. Returns (result, errors)."""
    errors: list[str] = []
    for i in range(attempts):
        try:
            return fn(), errors
        except Exception as exc:  # noqa: BLE001 - every failure mode is recorded
            errors.append(f"{type(exc).__name__}: {exc}")
            if verbose:
                print(f"    retry {i + 1}/{attempts} {label}: {type(exc).__name__}: {exc}")
            if i < attempts - 1:
                time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])
    return None, errors


def cache_path(endpoint: str, key: str) -> Path:
    d = paths.RAW / endpoint
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json.gz"


def write_cache(endpoint: str, key: str, payload: object) -> Path:
    p = cache_path(endpoint, key)
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    return p


def read_cache(endpoint: str, key: str) -> object | None:
    p = cache_path(endpoint, key)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def prune_cache(endpoint: str, keys: Iterable[str]) -> int:
    """Delete cached payloads for completed units (the DB is the artifact now)."""
    n = 0
    for key in keys:
        p = cache_path(endpoint, key)
        if p.exists():
            p.unlink()
            n += 1
    return n


def mark_complete(con, unit: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (f"{unit}:complete", now_iso()))
    con.commit()


def is_complete(con, unit: str) -> bool:
    row = con.execute("SELECT 1 FROM meta WHERE key = ?", (f"{unit}:complete",)).fetchone()
    return row is not None


def log_failures(unit: str, rows: list[dict]) -> Path | None:
    if not rows:
        return None
    p = paths.DATA / f"{unit}.failures.jsonl"
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"ts": now_iso(), **r}) + "\n")
    return p

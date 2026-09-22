"""The Odds API client mechanics (sport-neutral core, map step 7).

Fetch + cache + quota guard + stale-quote sanitizer + accountability drop log.
Sport wiring (tennis: ``backend.providers.the_odds_api``) owns its store paths
and passes them at call time so tests can redirect them (tests/conftest.py);
fixtures (``mock_events``) are injected the same way.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://api.the-odds-api.com/v4"
CACHE_TTL_SEC = 60  # pre-match polling 60s is plenty
QUOTA_RESERVE = 25  # stop live fetches when fewer than this remain

# Fallback store paths (deployments usually inject their own).
CACHE_PATH: Path | None = None
QUOTA_PATH: Path | None = None
DROPPED_LOG: Path | None = None
ARCHIVE_DIR: Path | None = None

# Last-fetch diagnostics, surfaced via /api/health.
_LAST_STATUS: dict = {"mode": "init", "detail": "no fetch yet"}


def configure(cache_path: Path | None = None, quota_path: Path | None = None,
              dropped_log: Path | None = None, archive_dir: Path | None = None,
              base: str | None = None) -> None:
    """Bind default store paths for a deployment (call-time args still win)."""
    global CACHE_PATH, QUOTA_PATH, DROPPED_LOG, ARCHIVE_DIR, BASE
    if cache_path is not None:
        CACHE_PATH = Path(cache_path)
    if quota_path is not None:
        QUOTA_PATH = Path(quota_path)
    if dropped_log is not None:
        DROPPED_LOG = Path(dropped_log)
    if archive_dir is not None:
        ARCHIVE_DIR = Path(archive_dir)
    if base is not None:
        BASE = base


def last_status() -> dict:
    return dict(_LAST_STATUS)

def _set_status(mode: str, detail: str = "", **extra) -> None:
    _LAST_STATUS.update({"mode": mode, "detail": detail, **extra})

@contextlib.contextmanager
def _locked(path: Path, timeout: float = 5.0):
    """Cross-process lock via an adjacent .lock directory (stdlib only)."""
    lock = path.with_suffix(path.suffix + ".lock")
    start = time.time()
    while True:
        try:
            lock.mkdir(exist_ok=False)
            break
        except FileExistsError:
            if time.time() - start > timeout:
                raise TimeoutError(f"could not lock {path}")
            # stale lock from a crashed process: reap after 30s
            try:
                if time.time() - lock.stat().st_mtime > 30:
                    lock.rmdir()
            except FileNotFoundError:
                pass
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock.rmdir()

def _load_cache(cache_path: Path | None = None) -> dict:
    try:
        return json.loads((cache_path or CACHE_PATH).read_text())
    except Exception:
        return {}


def _save_cache(cache_path: Path | None, cache: dict) -> None:
    p = cache_path or CACHE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, p)


def _load_quota(quota_path: Path | None = None) -> dict:
    try:
        return json.loads((quota_path or QUOTA_PATH).read_text())
    except Exception:
        return {}


def _save_quota(quota_path: Path | None, q: dict) -> None:
    p = quota_path or QUOTA_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(q))
    os.replace(tmp, p)


def quota(quota_path: Path | None = None) -> dict:
    """Persisted credit state: remaining / used / when observed."""
    return _load_quota(quota_path)


def cache_age_s(sport_key: str, markets: str = "h2h,spreads,totals",
                regions: str = "us,eu,uk", cache_path: Path | None = None) -> float | None:
    """Age of the cached snapshot in seconds (None = no cache)."""
    try:
        hit = _load_cache(cache_path).get(f"{sport_key}|{markets}|{regions}")
        return time.time() - hit["ts"] if hit else None
    except Exception:
        return None


def _mock_for(sport_key: str, mock_events: list | None) -> list[dict]:
    evs = mock_events or []
    return [e for e in evs if e["sport_key"] == sport_key] or list(evs)


def fetch_odds(sport_key: str, markets: str = "h2h,spreads,totals",
               regions: str = "us,eu,uk", mock: bool = False,
               mock_events: list | None = None,
               cache_path: Path | None = None, quota_path: Path | None = None,
               archive_dir: Path | None = None) -> list[dict]:
    """Fetch odds for one sport_key. Returns list of events in Odds-API shape."""
    cache_path = Path(cache_path) if cache_path is not None else CACHE_PATH
    quota_path = Path(quota_path) if quota_path is not None else QUOTA_PATH
    if cache_path is None or quota_path is None:
        raise ValueError("fetch_odds: cache/quota paths not configured (pass cache_path=/quota_path=)")
    api_key = os.getenv("ODDS_API_KEY", "")
    if mock or not api_key:
        _set_status("mock", "mock requested or ODDS_API_KEY unset", sport_key=sport_key)
        return _mock_for(sport_key, mock_events)

    with _locked(cache_path):
        cache = _load_cache(cache_path)
        ck = f"{sport_key}|{markets}|{regions}"
        hit = cache.get(ck)
        if hit and time.time() - hit["ts"] < CACHE_TTL_SEC:
            _set_status("cache", f"fresh cache ({int(CACHE_TTL_SEC - (time.time() - hit['ts']))}s left)",
                        sport_key=sport_key)
            return hit["events"]

        q = _load_quota(quota_path)
        remaining = q.get("remaining")
        if remaining is not None and remaining <= QUOTA_RESERVE:
            if hit:
                _set_status("cache", f"quota low ({remaining} left): serving stale cache",
                            sport_key=sport_key, remaining=remaining)
                return hit["events"]
            _set_status("mock", f"quota low ({remaining} left) and no cache: mock",
                        sport_key=sport_key, remaining=remaining)
            return _mock_for(sport_key, mock_events)

        try:
            r = requests.get(f"{BASE}/sports/{sport_key}/odds/", params={
                "apiKey": api_key, "regions": regions, "markets": markets,
                "oddsFormat": "decimal", "dateFormat": "iso",
            }, timeout=20)
            r.raise_for_status()
            events = r.json()
            if isinstance(events, dict):
                events = events.get("data", events)
            try:
                rem = r.headers.get("x-requests-remaining")
                used = r.headers.get("x-requests-used")
                if rem is not None or used is not None:
                    _save_quota(quota_path,
                                {"remaining": int(rem) if rem is not None else None,
                                 "used": int(used) if used is not None else None,
                                 "updated": time.time(), "sport_key": sport_key})
                    remaining = int(rem) if rem is not None else remaining
            except (ValueError, TypeError):
                pass
            cache[ck] = {"ts": time.time(), "events": events}
            _save_cache(cache_path, cache)
            _set_status("live", "fetched from The Odds API",
                        sport_key=sport_key, remaining=remaining)
            try:  # W1 archive: raw responses are evidence; timestamps can't be rebuilt
                arch = Path(archive_dir) if archive_dir is not None else ARCHIVE_DIR
                if arch is not None:
                    from core.providers.archive import store as _arch
                    _arch(sport_key, events, {"markets": markets, "regions": regions,
                                              "remaining": remaining}, arch_dir=arch)
            except Exception:
                pass
            return events
        except Exception as e:
            # Network blocked (e.g. gambling filter) or bad key -> mock fallback
            print(f"[warn] live fetch failed for {sport_key}: {type(e).__name__}, using mock")
            if hit:
                _set_status("cache", f"fetch failed ({type(e).__name__}): stale cache",
                            sport_key=sport_key)
                return hit["events"]
            _set_status("mock", f"fetch failed ({type(e).__name__}): mock",
                        sport_key=sport_key)
            return _mock_for(sport_key, mock_events)


def to_books_map(event: dict, market_key: str = "h2h",
                 dropped_log: Path | None = None) -> dict[str, dict[str, float]]:
    """Convert one event -> {book: {outcome: decimal_odds}} for a market.

    Sanitizes feed garbage (observed in the wild: quotes of exactly 1.0 from
    suspended books, and 40x outliers like 61.0 on a ~1.44 shot):
    - drops non-positive / <= 1.0 quotes (invalid decimal odds)
    - drops per-outcome quotes beyond 4x the cross-book median (stale ticks)
    Single choke point: every consumer (compare/predict/board) gets clean books.
    """
    raw: dict[str, dict[str, float]] = {}
    for bm in event.get("bookmakers", []):
        for m in bm.get("markets", []):
            if m.get("key") != market_key:
                continue
            raw.setdefault(bm.get("title", bm.get("key", "?")), {})
            for o in m.get("outcomes", []):
                try:
                    p = float(o["price"])
                except Exception:
                    continue
                if p <= 1.0:
                    continue  # suspended/missing quote, not a price
                raw[bm.get("title", bm.get("key"))][o["name"]] = p
    if not raw:
        return raw
    med: dict[str, float] = {}
    for o in {o for b in raw.values() for o in b}:
        qs = sorted(b[o] for b in raw.values() if o in b)
        med[o] = qs[(len(qs) - 1) // 2]  # lower median: an extreme quote can't elect itself center
    books: dict[str, dict[str, float]] = {}
    dropped = []
    for bk, outs in raw.items():
        for o, p in outs.items():
            m = med[o]
            if m > 0 and (p > 4 * m or p < m / 4):
                dropped.append({"book": bk, "outcome": o, "price": p, "median": round(m, 3)})
                continue  # stale/garbage tick
            books.setdefault(bk, {})[o] = p
    log = Path(dropped_log) if dropped_log is not None else DROPPED_LOG
    if dropped and log is not None:
        try:  # accountability: every killed quote is logged, never silent
            with open(log, "a", encoding="utf-8") as f:
                for d in dropped:
                    f.write(json.dumps({"ts": time.time(), **d}) + "\n")
        except Exception:
            pass
    return books


def list_props(event: dict) -> list[dict]:
    """Return non-h2h/spreads/totals markets as props-ish rows for the UI."""
    rows = []
    for bm in event.get("bookmakers", []):
        for m in bm.get("markets", []):
            if m.get("key") in ("h2h", "spreads", "totals"):
                continue
            for o in m.get("outcomes", []):
                rows.append({"book": bm.get("title"), "market": m.get("key"),
                             "outcome": o.get("name"), "price": o.get("price"),
                             "point": o.get("point")})
    return rows

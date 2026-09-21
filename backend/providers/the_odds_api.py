"""The Odds API v4 adapter with file cache + mock fallback.

Tennis (men's singles) only. Docs: https://the-odds-api.com/liveapi/guides/v4/
Get free key: https://the-odds-api.com/ -> .env ODDS_API_KEY (500 req/mo free).

Reliability features:
- Locked, atomic file cache (no torn writes under concurrent requests).
- Quota guard: tracks ``x-requests-remaining`` and stops live fetches when
  credits run low, serving fresh cache then mock instead of burning the key.
- Every fetch records a status entry so /api/health can say whether the
  app is live / cached / mock and why.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

import requests

BASE = "https://api.the-odds-api.com/v4"
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache.json"
QUOTA_PATH = Path(__file__).resolve().parents[2] / "data" / "quota.json"
# Accountability log for quotes killed by the stale-tick sanitizer. Module-level
# so tests can redirect it (tests/conftest.py): the suite must never mutate
# evidence under data/.
DROPPED_LOG = Path(__file__).resolve().parents[2] / "data" / "dropped_quotes.jsonl"
CACHE_TTL_SEC = 60  # pre-match polling 60s is plenty
QUOTA_RESERVE = 25  # stop live fetches when fewer than this remain

SPORT_GROUPS = {
    # Mens singles only — no WTA, no doubles/mixed. US Open first.
    "tennis": ["tennis_atp_singles"],
    "usopen": ["tennis_atp_us_open"],
}
DEFAULT_GROUP = "usopen"

MOCK_EVENTS = [
    {
        "id": "mock_usopen_m1",
        "sport_key": "tennis_atp_us_open",
        "sport_title": "ATP US Open",
        "commence_time": "2026-09-07T18:00:00Z",
        "home_team": "Jannik Sinner",
        "away_team": "Carlos Alcaraz",
        "bookmakers": [
            {"key": "draftkings", "title": "DraftKings",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Jannik Sinner", "price": 1.95},
                 {"name": "Carlos Alcaraz", "price": 1.87}]},
                {"key": "totals", "outcomes": [
                 {"name": "Over", "price": 1.91, "point": 38.5},
                 {"name": "Under", "price": 1.91, "point": 38.5}]}]},
            {"key": "fanduel", "title": "FanDuel",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Jannik Sinner", "price": 1.91},
                 {"name": "Carlos Alcaraz", "price": 1.91}]},
                {"key": "spreads", "outcomes": [
                 {"name": "Jannik Sinner", "price": 1.91, "point": -1.5},
                 {"name": "Carlos Alcaraz", "price": 1.91, "point": 1.5}]}]},
            {"key": "pinnacle", "title": "Pinnacle",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Jannik Sinner", "price": 1.97},
                 {"name": "Carlos Alcaraz", "price": 1.89}]},
                {"key": "totals", "outcomes": [
                 {"name": "Over", "price": 1.93, "point": 38.5},
                 {"name": "Under", "price": 1.89, "point": 38.5}]}]},
        ],
    },
    {
        "id": "mock_usopen_m2",
        "sport_key": "tennis_atp_us_open",
        "sport_title": "ATP US Open",
        "commence_time": "2026-09-07T20:00:00Z",
        "home_team": "Novak Djokovic",
        "away_team": "Daniil Medvedev",
        "bookmakers": [
            {"key": "draftkings", "title": "DraftKings",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Novak Djokovic", "price": 1.72},
                 {"name": "Daniil Medvedev", "price": 2.15}]}]},
            {"key": "betmgm", "title": "BetMGM",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Novak Djokovic", "price": 1.70},
                 {"name": "Daniil Medvedev", "price": 2.20}]}]},
            {"key": "pinnacle", "title": "Pinnacle",
             "markets": [{"key": "h2h", "outcomes": [
                 {"name": "Novak Djokovic", "price": 1.74},
                 {"name": "Daniil Medvedev", "price": 2.16}]}]},
        ],
    },
]

# Last-fetch diagnostics, surfaced via /api/health.
_LAST_STATUS: dict = {"mode": "init", "detail": "no fetch yet"}


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


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text())
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, CACHE_PATH)


def _load_quota() -> dict:
    try:
        return json.loads(QUOTA_PATH.read_text())
    except Exception:
        return {}


def _save_quota(q: dict) -> None:
    QUOTA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUOTA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(q))
    os.replace(tmp, QUOTA_PATH)


def quota() -> dict:
    """Persisted credit state: remaining / used / when observed."""
    return _load_quota()


def cache_age_s(sport_key: str, markets: str = "h2h,spreads,totals",
                regions: str = "us,eu,uk") -> float | None:
    """Age of the cached snapshot in seconds (None = no cache)."""
    try:
        hit = _load_cache().get(f"{sport_key}|{markets}|{regions}")
        return time.time() - hit["ts"] if hit else None
    except Exception:
        return None


def _mock_for(sport_key: str) -> list[dict]:
    return [e for e in MOCK_EVENTS if e["sport_key"] == sport_key] or MOCK_EVENTS


def fetch_odds(sport_key: str, markets: str = "h2h,spreads,totals",
               regions: str = "us,eu,uk", mock: bool = False) -> list[dict]:
    """Fetch odds for one sport_key. Returns list of events in Odds-API shape."""
    api_key = os.getenv("ODDS_API_KEY", "")
    if mock or not api_key:
        _set_status("mock", "mock requested or ODDS_API_KEY unset", sport_key=sport_key)
        return _mock_for(sport_key)

    with _locked(CACHE_PATH):
        cache = _load_cache()
        ck = f"{sport_key}|{markets}|{regions}"
        hit = cache.get(ck)
        if hit and time.time() - hit["ts"] < CACHE_TTL_SEC:
            _set_status("cache", f"fresh cache ({int(CACHE_TTL_SEC - (time.time() - hit['ts']))}s left)",
                        sport_key=sport_key)
            return hit["events"]

        q = _load_quota()
        remaining = q.get("remaining")
        if remaining is not None and remaining <= QUOTA_RESERVE:
            if hit:
                _set_status("cache", f"quota low ({remaining} left): serving stale cache",
                            sport_key=sport_key, remaining=remaining)
                return hit["events"]
            _set_status("mock", f"quota low ({remaining} left) and no cache: mock",
                        sport_key=sport_key, remaining=remaining)
            return _mock_for(sport_key)

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
                    _save_quota({"remaining": int(rem) if rem is not None else None,
                                 "used": int(used) if used is not None else None,
                                 "updated": time.time(), "sport_key": sport_key})
                    remaining = int(rem) if rem is not None else remaining
            except (ValueError, TypeError):
                pass
            cache[ck] = {"ts": time.time(), "events": events}
            _save_cache(cache)
            _set_status("live", "fetched from The Odds API",
                        sport_key=sport_key, remaining=remaining)
            try:  # W1 archive: raw responses are evidence; timestamps can't be rebuilt
                from backend.providers.archive import store as _arch
                _arch(sport_key, events, {"markets": markets, "regions": regions,
                                          "remaining": remaining})
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
            return _mock_for(sport_key)


def fetch_group(group: str, mock: bool = False) -> list[dict]:
    if group not in SPORT_GROUPS:
        raise ValueError(f"unknown group {group!r}: {sorted(SPORT_GROUPS)}")
    out: list[dict] = []
    for sk in SPORT_GROUPS[group]:
        try:
            out.extend(fetch_odds(sk, mock=mock))
        except Exception:
            continue
    return out


def to_books_map(event: dict, market_key: str = "h2h") -> dict[str, dict[str, float]]:
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
    if dropped:
        try:  # accountability: every killed quote is logged, never silent
            with open(DROPPED_LOG, "a", encoding="utf-8") as f:
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

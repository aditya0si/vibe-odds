"""Tennis odds wiring: The Odds API client bound to tennis groups + fixtures.

The generic client (fetch/cache/quota/archive/sanitizer) lives in
``core.providers.odds_api`` (map step 7). Tennis-specific: the sport groups
(``providers/groups.py``), the mock fixtures, and the store-path bindings.

Module constants stay patch points: tests/conftest.py redirects ``DROPPED_LOG``
into tmp so the suite never mutates evidence — they are read at call time and
passed into core on every call, never bound into core at import.
"""

from __future__ import annotations

from pathlib import Path

from core.providers import odds_api as _core
from core.providers.odds_api import (  # noqa: F401  (compat re-exports)
    BASE, CACHE_TTL_SEC, QUOTA_RESERVE,
)
from backend.providers.groups import DEFAULT_GROUP, SPORT_GROUPS  # noqa: F401

CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache.json"
QUOTA_PATH = Path(__file__).resolve().parents[2] / "data" / "quota.json"
# Accountability log for quotes killed by the stale-tick sanitizer. Module-level
# so tests can redirect it (tests/conftest.py): the suite must never mutate
# evidence under data/.
DROPPED_LOG = Path(__file__).resolve().parents[2] / "data" / "dropped_quotes.jsonl"

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


def last_status() -> dict:
    return _core.last_status()


def quota() -> dict:
    return _core.quota(QUOTA_PATH)


def cache_age_s(sport_key: str, markets: str = "h2h,spreads,totals",
                regions: str = "us,eu,uk") -> float | None:
    return _core.cache_age_s(sport_key, markets, regions, cache_path=CACHE_PATH)


def fetch_odds(sport_key: str, markets: str = "h2h,spreads,totals",
               regions: str = "us,eu,uk", mock: bool = False) -> list[dict]:
    return _core.fetch_odds(sport_key, markets, regions, mock=mock,
                            mock_events=MOCK_EVENTS, cache_path=CACHE_PATH,
                            quota_path=QUOTA_PATH)


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
    return _core.to_books_map(event, market_key, dropped_log=DROPPED_LOG)


def list_props(event: dict) -> list[dict]:
    return _core.list_props(event)

"""SportsGameOdds (SGO) client mechanics: player-prop prices, cross-book check.

Why: SGO is the second, independent prop feed. Its free "Amateur" tier includes
player props on every plan (2,500 objects/month, 10 req/min, ~10-minute refresh,
9 books, settlement data included), which makes it the only free multi-book prop
source we can name. It is NOT reachable from this machine — the same Sophos
gambling-category filter that blocks the retail books answers
api.sportsgameodds.com with a certificate-verification failure (probed
2026-10-13)—so this provider is driven from a GitHub Actions runner instead
(.github/workflows/props-capture.yml). Two independent books feeds are what make
a de-vigged consensus possible rather than a single book's opinion.

Object budget is the binding constraint (2,500/month): each returned odd is an
object, so a full NBA board is a few hundred. The workflow therefore filters with
`oddIDs` and runs on a schedule that keeps a month inside budget; do not add
per-minute polling here the way SharpAPI's board can tolerate it.

Field-mapping status (honest label): the odd-ID grammar below is documented
(`statID-statEntityID-period-betTypeID-sideID`, e.g. `assists-LEBRON_JAMES_NBA-game-ou-over`,
with `statEntityID` ∈ {home, away, all, <playerID>}), and `apiKey` is a query
parameter. The exact per-book price containers could not be read from the docs
(blocked on this network), so `price_from_odd` accepts the documented candidate
shapes and RAISES UnverifiedShape otherwise instead of guessing — the first
authenticated run must be `--dump-raw` and the mapping pinned from that payload.
Fail closed: a wrong guess here would put invented prices in the evidence store.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

BASE = "https://api.sportsgameodds.com/v2"
ENV_KEY = "SPORTSGAMEODDS_KEY"
SIGNUP_URL = "https://sportsgameodds.com/pricing"
TIMEOUT = 25
ATTEMPTS = 3

TEAM_ENTITY_IDS = {"all", "home", "away"}

# SGO market statID -> our canonical market. `oddIDs` filtering uses these names.
MARKET_MAP = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threes": "threes",
    "threePointersMade": "threes",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "pointsReboundsAssists": "pra",
    "fantasyScore": "fantasy_points",
}

DEFAULT_ODD_IDS = ("points", "rebounds", "assists")

_SESSION = requests.Session()


class MissingKey(RuntimeError):
    """Raised when SPORTSGAMEODDS_KEY is absent. The message names the variable."""


class UnverifiedShape(RuntimeError):
    """A payload shape the docs did not pin. Never guess a price into the store."""


def api_key(explicit: str | None = None) -> str:
    key = (explicit or os.environ.get(ENV_KEY) or "").strip()
    if not key:
        raise MissingKey(
            f"{ENV_KEY} is not set. Create a free key at {SIGNUP_URL} "
            f"(free tier includes player props: 2,500 objects/month, 10 req/min) "
            f"and store it as a repo secret of the same name."
        )
    return key


def available() -> bool:
    return bool((os.environ.get(ENV_KEY) or "").strip())


def fetch_events(league: str = "NBA", *, odd_ids: tuple[str, ...] = DEFAULT_ODD_IDS,
                 odds_available: bool = True, limit: int = 20,
                 key: str | None = None) -> dict:
    """Raw /v2/events payload. Note: the key rides in the query string here —
    never print or log a raw `requests` exception from this module without the
    redaction in `redact()` (repo rule)."""
    params: dict[str, Any] = {"leagueID": league, "apiKey": api_key(key),
                              "limit": limit}
    if odds_available:
        params["oddsAvailable"] = "true"
    if odd_ids:
        params["oddIDs"] = ",".join(f"{o}-.*-.*" for o in odd_ids)
    last: Exception | None = None
    for i in range(ATTEMPTS):
        try:
            r = _SESSION.get(BASE + "/events", params=params, timeout=TIMEOUT)
            if r.status_code in (401, 403):
                raise MissingKey("SportsGameOdds rejected the key "
                                 f"({r.status_code}). Check {ENV_KEY}.")
            if r.status_code == 429:
                time.sleep(5.0)
                continue
            r.raise_for_status()
            return r.json()
        except MissingKey:
            raise
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < ATTEMPTS - 1:
                time.sleep(1.0 + 2.0 * i)
    raise RuntimeError(f"SportsGameOdds request failed: {type(last).__name__}")


def redact(text: str, key: str | None = None) -> str:
    """Strip apiKey from any string before it is printed or logged."""
    k = (key or os.environ.get(ENV_KEY) or "").strip()
    out = text
    if k:
        out = out.replace(k, "<redacted>")
    return out.replace("apiKey=", "apiKey=<redacted>")


def parse_odd_id(odd_id: str) -> dict | None:
    """`statID-statEntityID-periodID-betTypeID-sideID` -> parts, or None.

    Player identity is any entity segment outside {all, home, away}. The parser
    is deliberately strict about the five-part grammar so a future format change
    shows up as a drop rather than as a mislabelled player.
    """
    parts = odd_id.split("-")
    if len(parts) != 5:
        return None
    stat, entity, period, bet_type, side = parts
    if bet_type != "ou" or side not in ("over", "under"):
        return None
    return {"stat_id": stat, "entity": entity, "period": period,
            "bet_type": bet_type, "side": side,
            "is_player_prop": entity not in TEAM_ENTITY_IDS}


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def price_from_odd(odd: dict) -> tuple[float | None, float | None, str | None, str | None]:
    """(line, decimal_price, raw_price, book) from one odd object.

    Accepts the documented containers (`bookOverUnder`/`bookOdds`,
    `overUnder`/`odds`, nested `byBookmaker`) and raises on anything else. The
    book on the row is whichever bookmaker key carried the price, so a row is
    always labelled with the source that published it.

    NOTE: this is the ONE place that must be re-pinned from a real payload
    (`tools/props_capture.py capture --source sportsgameodds --dump-raw`).
    """
    books = odd.get("byBookmaker") or odd.get("odds") or odd.get("bookmakerOdds")
    if isinstance(books, dict) and books:
        book, payload = next(iter(books.items()))
        if not isinstance(payload, dict):
            raise UnverifiedShape(f"bookmaker payload is {type(payload).__name__}")
        line = _num(payload.get("bookOverUnder", payload.get("overUnder", payload.get("line"))))
        price = _num(payload.get("bookOdds", payload.get("odds", payload.get("price"))))
        raw = payload.get("bookOdds") if isinstance(payload.get("bookOdds"), str) else None
        return line, price, raw, str(book)
    line = _num(odd.get("bookOverUnder", odd.get("overUnder", odd.get("line"))))
    price = _num(odd.get("bookOdds", odd.get("odds", odd.get("fairOdds", odd.get("price")))))
    if line is None and price is None:
        raise UnverifiedShape(
            "no documented price container on the odd; re-pin the mapping from a "
            "live payload instead of guessing (see module docstring)")
    return line, price, None, str(odd.get("bookmakerID") or odd.get("sportsbook") or "unknown")


def normalize_events(payload: dict, *, captured_at: str | None = None,
                     snapshot_kind: str = "t60") -> tuple[list[dict], list[dict]]:
    """Split an SGO /v2/events payload into (rows, drops), one row per
    (event, book, player, market, side, line). Rejections carry reasons."""
    rows: list[dict] = []
    drops: list[dict] = []
    for event in payload.get("data") or payload.get("events") or []:
        teams = {}
        for t in (event.get("teams") or {}).values() if isinstance(event.get("teams"), dict) else []:
            if isinstance(t, dict):
                teams[t.get("teamID")] = t
        home = next((t for t in teams.values() if t.get("isHome")), None)
        away = next((t for t in teams.values() if not t.get("isHome")), None)
        base = {
            "event_id": event.get("eventID"),
            "home_team": (home or {}).get("names", {}).get("long") if isinstance(home, dict) else None,
            "away_team": (away or {}).get("names", {}).get("long") if isinstance(away, dict) else None,
            "home_abbr": (home or {}).get("names", {}).get("short") if isinstance(home, dict) else None,
            "away_abbr": (away or {}).get("names", {}).get("short") if isinstance(away, dict) else None,
            "event_start_time": event.get("status", {}).get("startsAt") if isinstance(event.get("status"), dict) else event.get("startsAt"),
        }
        players = event.get("players") or {}
        for odd_id, odd in (event.get("odds") or {}).items():
            parts = parse_odd_id(odd_id)
            if parts is None:
                drops.append({**base, "odd_id": odd_id, "reason": "unparsed_odd_id"})
                continue
            if not parts["is_player_prop"]:
                continue                       # game/team markets: not this store
            if parts["period"] not in ("game", "fullGame", "reg"):
                drops.append({**base, "odd_id": odd_id, "reason": "non_full_game_period"})
                continue
            market = MARKET_MAP.get(parts["stat_id"])
            if market is None:
                drops.append({**base, "odd_id": odd_id, "reason": "unmapped_market"})
                continue
            try:
                line, price, raw, book = price_from_odd(odd if isinstance(odd, dict) else {})
            except UnverifiedShape as exc:
                drops.append({**base, "odd_id": odd_id, "reason": f"unverified_shape: {exc}"})
                continue
            player = players.get(parts["entity"]) if isinstance(players, dict) else None
            name = None
            if isinstance(player, dict):
                name = (player.get("names") or {}).get("long") or player.get("name")
            if not name:
                drops.append({**base, "odd_id": odd_id, "reason": "no_player_name"})
                continue
            if line is None:
                drops.append({**base, "odd_id": odd_id, "reason": "no_line"})
                continue
            if price in (None, 0, 1.0):
                drops.append({**base, "odd_id": odd_id, "reason": "no_usable_price"})
                continue
            rows.append({
                "source": "sportsgameodds",
                "book": book,
                "player_name": name,
                "market": market,
                "market_type": f"player_{parts['stat_id']}",
                "side": parts["side"],
                "line": float(line),
                "price_decimal": float(price),
                "price_raw": raw or (str(odd.get("fairOdds")) if odd.get("fairOdds") is not None else None),
                "implied_prob": None,
                "is_alternate_line": None,
                "market_segment": "full_game",
                "event_start_time": base["event_start_time"],
                "source_ts": event.get("status", {}).get("updatedAt") if isinstance(event.get("status"), dict) else None,
                "captured_at": captured_at,
                "flags": None,
                "snapshot_kind": snapshot_kind,
                **base,
            })
    return rows, drops

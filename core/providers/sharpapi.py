"""SharpAPI client mechanics (sport-neutral core): player-prop prices.

Why this provider (NBA props capture, decided 2026-10-13): every prop-price
source the public pick sites resell is blocked on this network — a Sophos
gambling-category filter answers api.the-odds-api.com, api.sportsgameodds.com,
DraftKings, FanDuel, BetMGM, ESPN BET, PrizePicks (geo-block), plus props.cash,
bettingpros, outlier, unabated, rithmm and the rest, with either a block page or
a DNS failure. SharpAPI is reachable, documents `player_prop` market types, and
its free tier ($0/mo, 12 req/min, 2 books, 60s delay, pre-match only) has no
documented monthly request cap — the rate limit is the only ceiling, which is
what a pre-tip board needs. Its odds refresh server-side, so the local depth of
the network filter never enters the request path.

Key handling: SHARPAPI_KEY travels in the `X-API-Key` HEADER and never in the
query string, so a `requests` traceback cannot leak it (contrast
core/providers/odds_api.py, whose key rides in the URL and must be redacted
before printing). Do not add `api_key=` to any request in `_params`.

Schema verified against docs.sharpapi.io/en/api-reference/odds/ (fetched
2026-10-13): on `player_*` markets one row is one (player, side, line);
`selection` holds the SIDE there and `player_name` holds the identity;
`odds_decimal` / `odds_american` / `odds_probability` carry the price;
`stat_category` carries the stat; `market_segment` distinguishes full_game from
period markets; `timestamp` is the provider's refresh time, NOT our capture time.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

BASE = "https://api.sharpapi.io/api/v1"
ENV_KEY = "SHARPAPI_KEY"
SIGNUP_URL = "https://sharpapi.io/pricing"
TIMEOUT = 25
ATTEMPTS = 3

# `props` is the documented shorthand for every `player_*` market. Naming them
# explicitly is the safer long-run choice (see the docs note that `props` is
# narrower than is_player_prop and that named types are the robust filter).
DEFAULT_MARKETS = (
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_threes",
)

# source market_type -> our canonical market. Unknown `player_*` types fall back
# to the suffix after `player_`, so a new market never silently disappears.
MARKET_MAP = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "threes",
    "player_three_pointers_made": "threes",
    "player_points_rebounds_assists": "pra",
    "player_points_rebounds": "pr",
    "player_points_assists": "pa",
    "player_rebounds_assists": "ra",
    "player_steals": "steals",
    "player_blocks": "blocks",
    "player_turnovers": "turnovers",
    "player_fantasy_points": "fantasy_points",
}

_SESSION = requests.Session()


class MissingKey(RuntimeError):
    """Raised when SHARPAPI_KEY is absent. The message names the variable."""


class RateLimited(RuntimeError):
    def __init__(self, retry_after: int | None):
        super().__init__(f"rate limited (retry_after={retry_after}s)")
        self.retry_after = retry_after


class AuthFailed(RuntimeError):
    pass


def api_key(explicit: str | None = None) -> str:
    key = (explicit or os.environ.get(ENV_KEY) or "").strip()
    if not key:
        raise MissingKey(
            f"{ENV_KEY} is not set. Create a free key at {SIGNUP_URL} "
            f"(free tier: 12 req/min, 2 books, pre-match) and put it in .env "
            f"or the repo secret of the same name."
        )
    return key


def available() -> bool:
    """True when a key is present. Used by callers that want to skip, not fail."""
    return bool((os.environ.get(ENV_KEY) or "").strip())


def _get(path: str, params: dict[str, Any], key: str | None = None) -> dict:
    url = f"{BASE}{path}"
    headers = {"X-API-Key": api_key(key), "Accept": "application/json",
               "User-Agent": "vibe-odds/1.0 (props capture)"}
    last: Exception | None = None
    for i in range(ATTEMPTS):
        try:
            r = _SESSION.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if r.status_code == 401:
                raise AuthFailed("SharpAPI rejected the key (401). Check SHARPAPI_KEY.")
            if r.status_code == 429:
                body = {}
                try:
                    body = r.json().get("error") or {}
                except Exception:  # noqa: BLE001
                    pass
                raise RateLimited(body.get("retry_after"))
            r.raise_for_status()
            return r.json()
        except (AuthFailed, RateLimited):
            raise
        except Exception as exc:  # noqa: BLE001 — retry transient network/5xx
            last = exc
            if i < ATTEMPTS - 1:
                time.sleep(1.0 + 2.0 * i)
    raise RuntimeError(f"SharpAPI request failed: {type(last).__name__}")


def fetch_props(markets: tuple[str, ...] = ("props",), *,
                sportsbook: str | None = None, event_id: str | None = None,
                limit: int = 200, offset: int = 0, key: str | None = None) -> dict:
    """Fetch raw prop rows. `markets=("props",)` is the documented shorthand for
    every `player_*` market in ONE request (the filter is not documented as
    comma-list capable, so do not join several types here). Pass an explicit tuple
    such as `DEFAULT_MARKETS` only if `props` ever proves too broad.

    The response is returned untouched: parsing lives in `normalize_rows` so the
    raw payload can be archived for audit (repo rule: archive third-party inputs
    at fetch time; a lost timestamp cannot be reconstructed later).
    """
    params: dict[str, Any] = {"market_type": ",".join(markets), "limit": limit,
                              "offset": offset}
    if sportsbook:
        params["sportsbook"] = sportsbook
    if event_id:
        params["event_id"] = event_id
    return _get("/odds", params, key=key)


def _decimal_from_american(a: float | None) -> float | None:
    if a is None:
        return None
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return 1.0 + (a / 100.0) if a > 0 else 1.0 + (100.0 / abs(a))


def _side(row: dict) -> str | None:
    """Side for a player-prop row. On `player_*` it is `selection`; compound
    `selection_type` values (home_over) carry a team axis we do not price."""
    for value in (row.get("selection_type"), row.get("selection")):
        s = (value or "").strip().lower()
        if s in ("over", "under"):
            return s
        if s.endswith("_over") or s.endswith("_under"):
            return s.rsplit("_", 1)[1]
    return None


def canonical_market(market_type: str) -> str:
    mt = (market_type or "").strip().lower()
    if mt in MARKET_MAP:
        return MARKET_MAP[mt]
    return mt[len("player_"):] if mt.startswith("player_") else mt


def normalize_rows(payload: dict, *, captured_at: str | None = None,
                   snapshot_kind: str = "t60") -> tuple[list[dict], list[dict]]:
    """Split a SharpAPI payload into (rows, drops).

    Every rejected row is returned with a reason — an accountability drop log,
    not a silent filter: a board that shrinks without explanation is how a
    capture pipeline quietly stops collecting. Nothing is invented here: a row
    with no usable price is dropped, never stored at a placeholder 0/1.0
    (placeholder quotes such as exactly 1.0 are what a suspended book sends).
    """
    rows: list[dict] = []
    drops: list[dict] = []
    items = payload.get("data") if isinstance(payload, dict) else None
    if items is None:
        items = payload.get("odds") if isinstance(payload, dict) else None
    if items is None and isinstance(payload, list):
        items = payload
    for raw in items or []:
        mt = (raw.get("market_type") or "").strip()
        name = (raw.get("player_name") or "").strip()
        side = _side(raw)
        line = raw.get("line")
        price = raw.get("odds_decimal")
        if price in (None, 0, 1.0):
            price = _decimal_from_american(raw.get("odds_american"))
        drop = {
            "market_type": mt, "player_name": name, "side": side, "line": line,
            "event_id": raw.get("event_id"), "book": raw.get("sportsbook"),
        }
        if not raw.get("is_player_prop", mt.startswith("player_")):
            drops.append({**drop, "reason": "not_player_prop"})
            continue
        if not mt.startswith("player_"):
            # Scorer / period-scoped / most_*_player markets: identity rides on
            # `selection` and the market name is the book's own. Out of scope for
            # the first board; recorded so the exclusion is visible.
            drops.append({**drop, "reason": "unmapped_market_family"})
            continue
        if not name:
            drops.append({**drop, "reason": "no_player_name"})
            continue
        if side is None:
            drops.append({**drop, "reason": "non_over_under_side"})
            continue
        if line is None:
            drops.append({**drop, "reason": "no_line"})
            continue
        if price is None:
            drops.append({**drop, "reason": "no_usable_price"})
            continue
        if raw.get("is_active") is False:
            # Suspended market: the price is frozen, not quoted.
            drops.append({**drop, "reason": "inactive_market"})
            continue
        segment = raw.get("market_segment") or "full_game"
        flags = {k: raw.get(k) for k in
                 ("is_stale_pregame_price", "is_alternate_line", "is_main_line",
                  "market_name") if raw.get(k) is not None}
        rows.append({
            "source": "sharpapi",
            "book": raw.get("sportsbook") or "unknown",
            "player_name": name,
            "market": canonical_market(mt),
            "market_type": mt,
            "side": side,
            "line": float(line),
            "price_decimal": float(price),
            "price_raw": str(raw.get("odds_american")) if raw.get("odds_american") is not None else None,
            "implied_prob": raw.get("odds_probability"),
            "is_alternate_line": int(bool(raw.get("is_alternate_line"))) if raw.get("is_alternate_line") is not None else None,
            "market_segment": segment,
            "event_start_time": raw.get("event_start_time"),
            "source_ts": raw.get("timestamp"),
            "captured_at": captured_at if raw.get("captured_at") else None,
            "flags": json.dumps(flags, sort_keys=True) if flags else None,
            "snapshot_kind": snapshot_kind,
            "event_id": raw.get("event_id"),
            "home_team": raw.get("home_team"),
            "away_team": raw.get("away_team"),
            "home_abbr": ((raw.get("home") or {}) if isinstance(raw.get("home"), dict) else {}).get("abbreviation"),
            "away_abbr": ((raw.get("away") or {}) if isinstance(raw.get("away"), dict) else {}).get("abbreviation"),
        })
    return rows, drops

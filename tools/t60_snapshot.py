"""T-60 odds capture for 2026-27 (plan Task 5). Cron-able; nothing is bought.

    python -m tools.t60_snapshot --schedule games.json --now 2026-10-27T22:30:00Z
    python -m tools.t60_snapshot --schedule games.json --dry-run --mock

At roughly T-60 per game (the pre-registered T2″/T3 information cutoff), fetch
h2h odds through the EXISTING ODDS_API_KEY path - core.providers.odds_api with
the same shared quota guard and cache as the tennis provider (backend/
providers/the_odds_api.py) - and store one row per (book, side) into
odds_snapshots with snapshot_kind='t60' and asof_ts = capture time.

Pre-tip discipline (extended from sports/nba.live_log, same exception): a t60 row
whose asof_ts is not strictly BEFORE tip-off is REJECTED - a post-tip price is a
result, not a T-60 snapshot. The schedule input is a JSON list of
{game_id, tipoff_ts, home_team, away_team} (the Task-8 daily runner produces it
from the schedule feed); team names are matched to The Odds API event names.

Tests: tests/tools/test_t60_snapshot.py.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.providers import odds_api as _core
from sports.nba.db import build, paths
from sports.nba.live_log import require_pre_tip

NBA_SPORT_KEY = "basketball_nba"
MARKETS = "h2h"
# The shared v1 quota/cache bindings (same ODDS_API_KEY, same quota guard).
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "cache.json"
QUOTA_PATH = Path(__file__).resolve().parents[1] / "data" / "quota.json"
T60_WINDOW_MIN = 50      # minutes before tip-off: capture window opens
T60_WINDOW_MAX = 70      # ...and closes. "T-60" is the target, cron runs in between.

MOCK_NBA_EVENTS = [
    {"id": "mock_nba_g1", "sport_key": NBA_SPORT_KEY, "sport_title": "NBA",
     "commence_time": "2026-10-27T23:30:00Z",
     "home_team": "Los Angeles Lakers", "away_team": "Golden State Warriors",
     "bookmakers": [
         {"key": "draftkings", "title": "DraftKings", "markets": [{"key": "h2h", "outcomes": [
             {"name": "Los Angeles Lakers", "price": 1.91},
             {"name": "Golden State Warriors", "price": 1.91}]}]},
         {"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "h2h", "outcomes": [
             {"name": "Los Angeles Lakers", "price": 1.95},
             {"name": "Golden State Warriors", "price": 1.87}]}]},
     ]},
]


def fetch_nba_h2h(mock: bool = False) -> list[dict]:
    """The existing ODDS_API_KEY path (same quota guard/cache as tennis v1)."""
    return _core.fetch_odds(NBA_SPORT_KEY, markets=MARKETS, mock=mock,
                            mock_events=MOCK_NBA_EVENTS,
                            cache_path=CACHE_PATH, quota_path=QUOTA_PATH)


def quota() -> dict:
    return _core.quota(QUOTA_PATH)


def _ts(v) -> datetime:
    from sports.nba.live_log import _ts as ts
    return ts(v)


def upcoming_t60(events: list[dict], now, window: tuple[int, int] = (T60_WINDOW_MIN, T60_WINDOW_MAX)) -> list[dict]:
    """Events tipping within [now+window_min, now+window_max] minutes."""
    now = _ts(now)
    lo, hi = timedelta(minutes=window[0]), timedelta(minutes=window[1])
    out = []
    for ev in events:
        tip = _ts(ev["commence_time"])
        if lo <= (tip - now) <= hi:
            out.append(ev)
    return out


def h2h_rows(event: dict, captured_at) -> list[dict]:
    """Parse one event's h2h book prices into odds_snapshots row dicts
    (one per book x side). Sides follow the event's home/away names."""
    ts = require_pre_tip("t60 snapshot", event.get("id", "?"), event["commence_time"], captured_at)
    at_iso = ts.isoformat()
    rows = []
    for bm in event.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for out in mkt.get("outcomes", []):
                name = out.get("name", "")
                if name == event["home_team"]:
                    side = "home"
                elif name == event["away_team"]:
                    side = "away"
                else:
                    continue
                rows.append({"source": "the_odds_api", "book": bm["key"],
                             "market": "h2h", "side": side,
                             "price_decimal": float(out["price"]), "price_raw": None,
                             "line": None, "captured_at": at_iso,
                             "snapshot_kind": "t60", "asof_ts": at_iso})
    return rows


def store_t60(con: sqlite3.Connection, game_id: str, tipoff_ts, rows: list[dict],
              captured_at) -> int:
    """Persist parsed rows for one game. Raises LatePrediction if captured_at is
    not strictly before tip-off (the t60 row would not be pre-game evidence)."""
    require_pre_tip("t60 snapshot", game_id, tipoff_ts, captured_at)
    con.executemany(
        """INSERT INTO odds_snapshots(game_id, source, book, market, side, price_decimal,
                                      price_raw, line, captured_at, snapshot_kind, asof_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["source"], r["book"], r["market"], r["side"], r["price_decimal"],
          r["price_raw"], r["line"], r["captured_at"], r["snapshot_kind"], r["asof_ts"])
         for r in rows])
    con.commit()
    return len(rows)


def run(con: sqlite3.Connection, games: list[dict], now, events: list[dict] | None = None,
        window: tuple[int, int] = (T60_WINDOW_MIN, T60_WINDOW_MAX), mock: bool = False,
        dry_run: bool = False) -> dict:
    """One cron tick: capture t60 rows for every game inside the window."""
    events = fetch_nba_h2h(mock=mock) if events is None else events
    tips = {(_norm(g["home_team"]), _norm(g["away_team"])): g for g in games}
    stored, skipped = 0, []
    for ev in upcoming_t60(events, now, window):
        key = (_norm(ev["home_team"]), _norm(ev["away_team"]))
        g = tips.get(key)
        if g is None:
            skipped.append({"event": ev.get("id"), "reason": "no scheduled game for these teams"})
            continue
        rows = h2h_rows(ev, now)
        if dry_run:
            skipped.append({"event": ev.get("id"), "game_id": g["game_id"],
                            "reason": f"dry-run: {len(rows)} rows not written"})
            continue
        stored += store_t60(con, g["game_id"], g["tipoff_ts"], rows, captured_at=now)
    return {"stored_rows": stored, "skipped": skipped,
            "quota": quota() if not dry_run else None}


def _norm(name: str) -> str:
    return " ".join(str(name).split()).lower()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T-60 h2h odds capture (2026-27)")
    ap.add_argument("--schedule", required=True,
                    help="JSON list of {game_id, tipoff_ts, home_team, away_team}")
    ap.add_argument("--now", default=None, help="ISO capture time (default: now, UTC)")
    ap.add_argument("--window", nargs=2, type=int, default=[T60_WINDOW_MIN, T60_WINDOW_MAX],
                    metavar=("MIN", "MAX"))
    ap.add_argument("--mock", action="store_true", help="mock events (no key spend)")
    ap.add_argument("--dry-run", action="store_true", help="report; write nothing")
    a = ap.parse_args(argv)
    games = json.loads(Path(a.schedule).read_text(encoding="utf-8"))
    now = a.now or datetime.now(timezone.utc).isoformat()
    con = build.init(verbose=False)
    out = run(con, games, now, window=tuple(a.window), mock=a.mock, dry_run=a.dry_run)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

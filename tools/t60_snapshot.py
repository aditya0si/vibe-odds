"""Multi-kind NBA odds capture for the 2026-27 season (plan Task 5, ESPN edition).

    python -m tools.t60_snapshot tick --schedule games.json --now 2026-10-27T22:30:00Z
    python -m tools.t60_snapshot postgame --date 2026-10-21        # harvest closes
    python -m tools.t60_snapshot map                               # (re)build event map

WHY ESPN (deviation, docs/preregistration.md 9): the originally planned source
(The Odds API, ODDS_API_KEY path) is blocked on this network - a Sophos web
filter answers api.the-odds-api.com with a gambling-category block page
(cat=21), so no capture could ever succeed there. ESPN's core API is reachable,
free, key-less, and - decisively - is the SAME provider the historical
2013-14..2025-26 odds corpus came from (sports/nba/ingest/odds_espn.py), so the
season's open/close prices are structurally consistent with the data the claim
is measured against.

Kinds stored per (game, book, side), all moneyline:
  open  <- the book item's `.open.moneyLine` (the provider's opening price;
           harvested opportunistically at capture time; captured_at=None, like
           the historical ingest - it is the provider's record, not our observation)
  t60   <- the book's QUOTED price at our capture, stored only when the capture
           is strictly before tip-off (require_pre_tip; a post-tip price is a result)
  close <- the book item's `.close.moneyLine` (the provider's closing price; it
           only populates at/after the game, so the post-game `postgame` pass
           harvests it; captured_at=None, matching the historical convention)

Dedupe: each (game, kind) is captured once. A tick skips a game that already has
t60 rows, so the 20-minute cron costs at most one fetch per game per kind.
In-play items ("... - Live Odds") are excluded: they encode the game state.

Tests: tests/tools/test_t60_snapshot.py.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):                   # script mode (cron / manual): repo importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from sports.nba.db import build, paths
from sports.nba.ingest.odds_espn import ESPN_TEAM_TO_NBA, _american
from sports.nba.live_log import _ts, require_pre_tip

ESPN_BASE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
EVENT_MAP_PATH = paths.DATA / "espn_events.json"
T60_WINDOW_MIN = 45      # capture window opens this many minutes before tip
T60_WINDOW_MAX = 75      # ...and closes. "T-60" is the target; the 20-min cron
                         # grid is guaranteed to land inside a 30-minute window.
POSTGAME_HOURS = 30      # a close is harvested for games tipped within this lookback

_session = requests.Session()


def espn_get(url: str, attempts: int = 3) -> dict | None:
    for i in range(attempts):
        try:
            r = _session.get(url, headers={"User-Agent": UA}, timeout=25)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            if i < attempts - 1:
                time.sleep(1.0 + 2.0 * i)
    return None


def _eid(ref: str) -> str:
    return ref.rstrip("/").split("/")[-1].split("?")[0]


def espn_season(season: str) -> int:
    return int(season[:4]) + 1                    # '2026-27' -> ESPN season 2027


# ------------------------------------------------------------------ event map
def build_event_map(con: sqlite3.Connection, season: str, verbose: bool = False) -> dict:
    """{game_id: {eid, espn_date}} for a season: list ESPN events, match to our games.

    Matching mirrors sports/nba/ingest/odds_espn.py::match_game: same team ids,
    date within +/-1 day (UTC vs arena-local). Cached at EVENT_MAP_PATH.
    """
    year = espn_season(season)
    refs: list[str] = []
    for page in range(1, 30):
        js = espn_get(f"{ESPN_BASE}/seasons/{year}/types/2/events?limit=100&page={page}")
        items = (js or {}).get("items") or []
        refs += [_eid(i["$ref"]) for i in items if i.get("$ref")]
        if len(items) < 100:
            break
    comps: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(espn_get, f"{ESPN_BASE}/events/{eid}/competitions/{eid}"): eid
                   for eid in refs}
        for fut in as_completed(futures):
            c = fut.result()
            if c:
                comps[futures[fut]] = c
    out: dict[str, dict] = {}
    for eid, comp in comps.items():
        date_iso = (comp.get("date") or "")[:10]
        home = away = None
        for c in comp.get("competitors", []):
            tid = ESPN_TEAM_TO_NBA.get(int(c.get("id", 0)))
            if c.get("homeAway") == "home":
                home = tid
            elif c.get("homeAway") == "away":
                away = tid
        if not (date_iso and home and away):
            continue
        d = datetime.fromisoformat(date_iso).date()
        for delta in (0, -1, 1):
            row = con.execute(
                "SELECT game_id FROM games WHERE season=? AND game_date=? AND home_team_id=? AND away_team_id=?",
                (season, (d + timedelta(days=delta)).isoformat(), home, away)).fetchone()
            if row:
                out[row["game_id"]] = {"eid": eid, "espn_date": date_iso}
                break
    if verbose:
        print(f"event map: {len(out)} games matched to ESPN events ({len(refs)} listed)")
    EVENT_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVENT_MAP_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def load_event_map(con: sqlite3.Connection, season: str, refresh: bool = False) -> dict:
    if not refresh and EVENT_MAP_PATH.exists():
        try:
            return json.loads(EVENT_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return build_event_map(con, season)


# ------------------------------------------------------------------ capture
def fetch_event_odds(eid: str) -> dict | None:
    return espn_get(f"{ESPN_BASE}/events/{eid}/competitions/{eid}/odds")


def parse_kinds(eid: str, odds: dict | None, captured_at, tipoff_ts) -> list[dict]:
    """ESPN odds payload -> row dicts. t60 rows are pre-tip enforced; open/close are
    the provider's own records (captured_at=None), like the historical ingest."""
    ts = require_pre_tip("t60 snapshot", eid, tipoff_ts, captured_at)
    at_iso = ts.isoformat()
    rows: list[dict] = []

    def add(book: str, side: str, kind: str, price, raw, captured: str | None):
        if price is None or price <= 1.0:
            return
        rows.append({"source": "espn", "book": book, "market": "moneyline", "side": side,
                     "price_decimal": float(price), "price_raw": str(raw), "line": None,
                     "captured_at": captured, "snapshot_kind": kind, "asof_ts": at_iso})

    for item in (odds or {}).get("items", []):
        book = (item.get("provider") or {}).get("name") or "?"
        if "live" in book.lower():
            continue                              # in-play feed: encodes the game state
        h, a = item.get("homeTeamOdds") or {}, item.get("awayTeamOdds") or {}
        add(book, "home", "t60", _american(h.get("moneyLine")), h.get("moneyLine"), at_iso)
        add(book, "away", "t60", _american(a.get("moneyLine")), a.get("moneyLine"), at_iso)
        add(book, "home", "open", _american((h.get("open") or {}).get("moneyLine")),
            (h.get("open") or {}).get("moneyLine"), None)
        add(book, "away", "open", _american((a.get("open") or {}).get("moneyLine")),
            (a.get("open") or {}).get("moneyLine"), None)
        add(book, "home", "close", _american((h.get("close") or {}).get("moneyLine")),
            (h.get("close") or {}).get("moneyLine"), None)
        add(book, "away", "close", _american((a.get("close") or {}).get("moneyLine")),
            (a.get("close") or {}).get("moneyLine"), None)
    return rows


def has_kind(con: sqlite3.Connection, game_id: str, kind: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM odds_snapshots WHERE game_id=? AND snapshot_kind=? LIMIT 1",
        (game_id, kind)).fetchone())


def store_rows(con: sqlite3.Connection, game_id: str, rows: list[dict], kinds: tuple[str, ...]) -> int:
    keep = [r for r in rows if r["snapshot_kind"] in kinds]
    if not keep:
        return 0
    con.executemany(
        """INSERT INTO odds_snapshots(game_id, source, book, market, side, price_decimal,
                                      price_raw, line, captured_at, snapshot_kind, asof_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(game_id, r["source"], r["book"], r["market"], r["side"], r["price_decimal"],
          r["price_raw"], r["line"], r["captured_at"], r["snapshot_kind"], r["asof_ts"])
         for r in keep])
    con.commit()
    return len(keep)


def in_window(tipoff_ts, now, window: tuple[int, int] = (T60_WINDOW_MIN, T60_WINDOW_MAX)) -> bool:
    delta = (_ts(tipoff_ts) - _ts(now)).total_seconds() / 60.0
    return window[0] <= delta <= window[1]


def run(con: sqlite3.Connection, games: list[dict], now, season: str,
        window: tuple[int, int] = (T60_WINDOW_MIN, T60_WINDOW_MAX),
        dry_run: bool = False) -> dict:
    """One cron tick: capture t60+open for every game inside the window (once each)."""
    emap = load_event_map(con, season)
    stored, skipped, fetched = 0, [], 0
    for g in games:
        gid = g["game_id"]
        if not in_window(g["tipoff_ts"], now, window):
            continue
        if has_kind(con, gid, "t60"):
            skipped.append({"game_id": gid, "reason": "t60 already captured"})
            continue
        meta = emap.get(gid)
        if not meta:
            skipped.append({"game_id": gid, "reason": "no ESPN event mapped"})
            continue
        if dry_run:
            skipped.append({"game_id": gid, "eid": meta["eid"], "reason": "dry-run: not fetched"})
            continue
        odds = fetch_event_odds(meta["eid"])
        fetched += 1
        if odds is None:
            skipped.append({"game_id": gid, "reason": "ESPN odds fetch failed"})
            continue
        rows = parse_kinds(meta["eid"], odds, captured_at=now, tipoff_ts=g["tipoff_ts"])
        kinds = ("t60",) + (() if has_kind(con, gid, "open") else ("open",))
        stored += store_rows(con, gid, rows, kinds)
    return {"stored_rows": stored, "fetched": fetched, "skipped": skipped}


def postgame(con: sqlite3.Connection, season: str, date: str | None = None,
             hours: int = POSTGAME_HOURS) -> dict:
    """Harvest close (and any missed open) rows for recently completed games."""
    ref = _ts(date) if date else datetime.now(timezone.utc)
    lo = (ref - timedelta(hours=hours)).isoformat()
    hi = (ref + timedelta(hours=6)).isoformat()
    emap = load_event_map(con, season)
    done, skipped = 0, []
    for r in con.execute(
            """SELECT game_id, tipoff_ts FROM games
               WHERE season=? AND season_type='regular' AND tipoff_ts >= ? AND tipoff_ts <= ?
                 AND home_score IS NOT NULL""", (season, lo, hi)):
        gid = r["game_id"]
        if has_kind(con, gid, "close"):
            skipped.append({"game_id": gid, "reason": "close already captured"})
            continue
        meta = emap.get(gid)
        if not meta:
            skipped.append({"game_id": gid, "reason": "no ESPN event mapped"})
            continue
        odds = fetch_event_odds(meta["eid"])
        if odds is None:
            skipped.append({"game_id": gid, "reason": "ESPN odds fetch failed"})
            continue
        rows = parse_kinds(meta["eid"], odds,
                           captured_at=_ts(r["tipoff_ts"]) - timedelta(minutes=1),
                           tipoff_ts=r["tipoff_ts"])
        kinds = ("close",) + (() if has_kind(con, gid, "open") else ("open",))
        done += store_rows(con, gid, rows, kinds)
    return {"stored_rows": done, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ESPN multi-kind NBA odds capture (2026-27)")
    sub = ap.add_subparsers(dest="cmd")
    t = sub.add_parser("tick", help="T-60 capture for games in the window")
    t.add_argument("--schedule", required=True,
                   help="JSON list of {game_id, tipoff_ts, home_team, away_team}")
    t.add_argument("--season", default="2026-27")
    t.add_argument("--now", default=None, help="ISO capture time (default: now, UTC)")
    t.add_argument("--window", nargs=2, type=int, default=[T60_WINDOW_MIN, T60_WINDOW_MAX],
                   metavar=("MIN", "MAX"))
    t.add_argument("--dry-run", action="store_true", help="report; fetch/write nothing")
    p = sub.add_parser("postgame", help="harvest close rows for completed games")
    p.add_argument("--season", default="2026-27")
    p.add_argument("--date", default=None, help="ISO reference time (default: now)")
    p.add_argument("--hours", type=int, default=POSTGAME_HOURS)
    m = sub.add_parser("map", help="(re)build the ESPN event map")
    m.add_argument("--season", default="2026-27")
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0].startswith("-"):           # bare --schedule call = a tick
        argv = ["tick"] + argv
    a = ap.parse_args(argv)
    con = build.init(verbose=False)
    if a.cmd == "tick":
        games = json.loads(Path(a.schedule).read_text(encoding="utf-8"))
        now = a.now or datetime.now(timezone.utc).isoformat()
        out = run(con, games, now, a.season, window=tuple(a.window), dry_run=a.dry_run)
    elif a.cmd == "postgame":
        out = postgame(con, a.season, a.date, a.hours)
    else:
        out = {"mapped": len(build_event_map(con, a.season, verbose=True))}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

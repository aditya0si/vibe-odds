"""Ingest historical NBA odds from ESPN's core API (free, 2013-14 -> 2025-26).

    python -m sports.nba.ingest.odds_espn --season 2013-14 --limit 20   # canary
    python -m sports.nba.ingest.odds_espn --all --workers 4

What the probe established (docs/phase0/probe-odds.md, verification-notes.md):
  * listing:  /v2/sports/basketball/leagues/nba/seasons/{start_year}/types/2/events?limit=N&page=P
              (returns $refs only - the competition payload carries date + competitors)
  * odds:     .../events/{id}/competitions/{id}/odds
  * movement: .../odds/1002/history/{0|1|2}/movement  -> TIMESTAMPED moneyline series
              (providerId 1002 = teamrankings only, roughly 2013-14..2022-23)
  * the quoted price is the CLOSING price (cross-checked against SBR's Close column), and from
    2023-24 each book item also carries `.open`/`.close` objects.

Snapshot kinds we store per (game, book, market, side):
    open  <- ESPN 'Opening' pseudo-provider (2013-14..2016-17), item-level `.open` (2023-24+),
             or the first point of the movement series (2017-18..2022-23)
    mid   <- the middle point of the movement series when it has >= 3 points
    close <- the book's quoted price (all seasons), or the last movement point

Only moneyline is ingested here. Spreads/totals are left for a follow-up: the plan's targets are
moneyline-only, and the ESPN payload's spread fields change shape across eras (raw values are kept
in the odds_snapshots.price_raw/line columns for whichever fields we do parse).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import requests

from core.odds import american_to_decimal
from sports.nba.db import build, paths
from sports.nba.ingest import nba_api_client as api

BASE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ESPN franchise id -> NBA team id. Franchise-stable, so relocated franchises stay joined
# (25 = Seattle/OKC, 17 = New Jersey/Brooklyn, 3 = NOH/NOP, 30 = CHH/CHA).
ESPN_TEAM_TO_NBA = {
    1: 1610612737, 2: 1610612738, 3: 1610612740, 4: 1610612741, 5: 1610612739, 6: 1610612742,
    7: 1610612743, 8: 1610612765, 9: 1610612744, 10: 1610612745, 11: 1610612754, 12: 1610612746,
    13: 1610612747, 14: 1610612748, 15: 1610612749, 16: 1610612750, 17: 1610612751, 18: 1610612752,
    19: 1610612753, 20: 1610612755, 21: 1610612756, 22: 1610612757, 23: 1610612758, 24: 1610612759,
    25: 1610612760, 26: 1610612762, 27: 1610612764, 28: 1610612761, 29: 1610612763, 30: 1610612766,
}
MOVEMENT_PROVIDER = "1002"          # teamrankings: the only provider with a movement history

_session = requests.Session()


def _get(url: str, attempts: int = 4) -> dict | None:
    for i in range(attempts):
        try:
            r = _session.get(url, headers={"User-Agent": UA}, timeout=25)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            if i < attempts - 1:
                time.sleep(api.BACKOFF[min(i, len(api.BACKOFF) - 1)])
    return None


def _american(v) -> float | None:
    """American price -> decimal, tolerating the payload's several shapes.

    ESPN sends 0 (and 0.0) as a placeholder for "no quote" - notably in the movement
    series and for books that did not post a line - so 0 must map to None, never to a
    price. (american_to_decimal(0) would divide by zero.)
    """
    if v is None:
        return None
    if isinstance(v, dict):
        for key in ("american", "value"):
            raw = v.get(key)
            if raw is not None:
                try:
                    num = float(str(raw).replace("+", ""))
                except (TypeError, ValueError):
                    continue
                if num == 0:
                    continue
                return american_to_decimal(num)
        if v.get("decimal") is not None:
            try:
                dec = float(v["decimal"])
            except (TypeError, ValueError):
                return None
            return dec if dec > 1.0 else None
        return None
    try:
        num = float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if num == 0:
        return None
    return american_to_decimal(num)


def _eid(ref: str) -> str:
    return ref.rstrip("/").split("/")[-1].split("?")[0]


def list_events(start_year: int, limit_pages: int = 20) -> list[str]:
    refs: list[str] = []
    for page in range(1, limit_pages + 1):
        js = _get(f"{BASE}/seasons/{start_year}/types/2/events?limit=100&page={page}")
        items = (js or {}).get("items") or []
        refs += [_eid(i["$ref"]) for i in items if i.get("$ref")]
        if len(items) < 100:
            break
    return refs


def fetch_event(eid: str) -> dict:
    """Competition + odds + movement for one ESPN event."""
    out: dict = {"eid": eid, "error": None, "game": None, "odds": None, "movement": None}
    comp = _get(f"{BASE}/events/{eid}/competitions/{eid}")
    if comp is None:
        out["error"] = "competition fetch failed"
        return out
    home = away = None
    for c in comp.get("competitors", []):
        tid = ESPN_TEAM_TO_NBA.get(int(c.get("id", 0)))
        if c.get("homeAway") == "home":
            home = tid
        elif c.get("homeAway") == "away":
            away = tid
    out["game"] = {"date": (comp.get("date") or "")[:10], "home_team_id": home,
                   "away_team_id": away, "neutral": bool(comp.get("neutralSite"))}
    out["odds"] = _get(f"{BASE}/events/{eid}/competitions/{eid}/odds")
    mv = _get(f"{BASE}/events/{eid}/competitions/{eid}/odds/{MOVEMENT_PROVIDER}/history/0/movement")
    if mv:
        out["movement"] = [p for p in (mv.get("items") or []) if p.get("homeOdds") or p.get("awayOdds")]
    return out


def match_game(con: sqlite3.Connection, date_iso: str, home_id: int | None, away_id: int | None) -> str | None:
    """Our game for an ESPN event: same teams, date within +/-1 day (UTC vs arena-local)."""
    if not (home_id and away_id and date_iso):
        return None
    d = date.fromisoformat(date_iso)
    for delta in (0, -1, 1):
        row = con.execute(
            "SELECT game_id FROM games WHERE game_date=? AND home_team_id=? AND away_team_id=?",
            ((d + timedelta(days=delta)).isoformat(), home_id, away_id)).fetchone()
        if row:
            return row["game_id"]
    return None


def parse_odds(game_id: str, eid: str, odds: dict | None, movement: list[dict] | None) -> list[tuple]:
    """-> rows for odds_snapshots: (game_id, source, book, market, side, price_decimal, price_raw,
    line, captured_at, snapshot_kind, asof_ts)"""
    ts = api.now_iso()
    rows: list[tuple] = []

    def add(book: str, side: str, kind: str, price_dec: float | None, raw, captured: str | None = None):
        if price_dec is None:
            return
        rows.append((game_id, "espn", book, "moneyline", side, price_dec, str(raw), None,
                     captured, kind, ts))

    for item in (odds or {}).get("items", []):
        book = (item.get("provider") or {}).get("name") or "?"
        home_odds = item.get("homeTeamOdds") or {}
        away_odds = item.get("awayTeamOdds") or {}
        quoted_home, quoted_away = _american(home_odds.get("moneyLine")), _american(away_odds.get("moneyLine"))

        if book.lower().startswith("opening"):
            add(book, "home", "open", quoted_home, home_odds.get("moneyLine"))
            add(book, "away", "open", quoted_away, away_odds.get("moneyLine"))
            continue

        add(book, "home", "close", quoted_home, home_odds.get("moneyLine"))
        add(book, "away", "close", quoted_away, away_odds.get("moneyLine"))
        # 2023-24+: per-book open/close objects
        add(book, "home", "open", _american((home_odds.get("open") or {}).get("moneyLine")),
            (home_odds.get("open") or {}).get("moneyLine"))
        add(book, "away", "open", _american((away_odds.get("open") or {}).get("moneyLine")),
            (away_odds.get("open") or {}).get("moneyLine"))
        add(book, "home", "close", _american((home_odds.get("close") or {}).get("moneyLine")),
            (home_odds.get("close") or {}).get("moneyLine"))
        add(book, "away", "close", _american((away_odds.get("close") or {}).get("moneyLine")),
            (away_odds.get("close") or {}).get("moneyLine"))

    if movement:
        pts = sorted(movement, key=lambda p: p.get("lineDate") or "")
        picks = [("open", pts[0])]
        if len(pts) >= 3:
            picks.append(("mid", pts[len(pts) // 2]))
        picks.append(("close", pts[-1]))
        for kind, p in picks:
            book = f"teamrankings({MOVEMENT_PROVIDER})"
            add(book, "home", kind, _american(p.get("homeOdds")), p.get("homeOdds"), p.get("lineDate"))
            add(book, "away", kind, _american(p.get("awayOdds")), p.get("awayOdds"), p.get("lineDate"))
    return rows


def ingest(con: sqlite3.Connection, seasons: list[str], workers: int = 4, limit: int | None = None,
           force: bool = False, verbose: bool = True) -> dict:
    all_rows: list[tuple] = []
    failures: list[dict] = []
    t0 = time.time()
    for season in seasons:
        unit = f"season:{season}:odds_espn"
        if api.is_complete(con, unit) and not force:
            if verbose:
                print(f"  {season}: already complete (skip)")
            continue
        start_year = int(season[:4]) + 1          # '2013-14' -> ESPN season 2014
        refs = list_events(start_year)
        if limit:
            refs = refs[:limit]
        print(f"  {season}: {len(refs)} ESPN events", flush=True)
        matched = 0
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(fetch_event, e) for e in refs]
            for i, fut in enumerate(as_completed(futures), 1):
                ev = fut.result()
                if ev["error"]:
                    failures.append({"event": ev["eid"], "season": season, "error": ev["error"]})
                    continue
                g = ev["game"]
                gid = match_game(con, g["date"], g["home_team_id"], g["away_team_id"])
                if not gid:
                    failures.append({"event": ev["eid"], "season": season,
                                     "error": f"no game match for {g['date']} {g['away_team_id']}@{g['home_team_id']}"})
                    continue
                matched += 1
                all_rows += parse_odds(gid, ev["eid"], ev["odds"], ev["movement"])
                if i % 100 == 0:
                    print(f"    {i}/{len(refs)} events, {matched} matched, {len(all_rows)} rows", flush=True)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for i in range(0, len(all_rows), 5000):     # chunked: never hold the write lock for a season
            con.executemany(
                """INSERT OR REPLACE INTO odds_snapshots(game_id, source, book, market, side,
                       price_decimal, price_raw, line, captured_at, snapshot_kind, asof_ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                all_rows[i:i + 5000])
            con.commit()
        api.mark_complete(con, unit)
        print(f"  {season}: matched {matched}/{len(refs)} events, {len(all_rows)} snapshot rows", flush=True)
    p = api.log_failures("odds_espn", failures)
    print(f"done in {(time.time()-t0)/60:.1f} min; failures: {len(failures)}" + (f" -> {p.name}" if p else ""))
    return {"rows": len(all_rows), "failures": len(failures)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest NBA moneyline odds from ESPN's core API")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--season", action="append", default=[])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="cap events per season (canary)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    seasons = [s for s in api.SEASONS if int(s[:4]) >= 2013] if args.all else args.season
    if not seasons:
        ap.error("give --all or --season YYYY-YY (ESPN odds start 2013-14)")
    con = build.init(verbose=False)
    ingest(con, seasons, workers=args.workers, limit=args.limit, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())

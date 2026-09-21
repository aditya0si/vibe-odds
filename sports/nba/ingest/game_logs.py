"""Ingest regular-season game logs for every season (nba_api LeagueGameFinder).

    python -m sports.nba.ingest.game_logs --all
    python -m sports.nba.ingest.game_logs --season 2005-06 --season 2012-13
    python -m sports.nba.ingest.game_logs --all --prune      # drop raw cache after parse

One season = one unit of work, because LeagueGameFinder caps at 30,000 rows and
returns newest-first: an all-seasons query silently drops the oldest seasons
(Phase 0 finding). Re-running a completed unit is a no-op unless --force.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date

from sports.nba.db import build, paths
from sports.nba.ingest import nba_api_client as api

ENDPOINT = "leaguegamefinder"
REGULAR_SEASON = "Regular Season"


def _parse_date(raw: str) -> str:
    """LeagueGameFinder returns ISO dates, but older payloads used 'NOV 01, 2005'."""
    raw = (raw or "").strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y%m%d"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(raw.title() if "," in raw else raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unparseable game date: {raw!r}")


def fetch_season(season: str, verbose: bool = False) -> list[dict]:
    from nba_api.stats.endpoints import leaguegamefinder

    def call():
        return leaguegamefinder.LeagueGameFinder(
            season_nullable=season, league_id_nullable="00",
            season_type_nullable=REGULAR_SEASON, timeout=60,
        )

    resp, errors = api.call_with_retry(call, label=f"leaguegamefinder {season}", verbose=verbose)
    if resp is None:
        raise RuntimeError(f"{season}: all attempts failed: {errors[-1] if errors else 'unknown'}")
    rows = resp.get_normalized_dict()["LeagueGameFinderResults"]
    api.write_cache(ENDPOINT, season, rows)
    return rows


def season_row_count_expected(season: str) -> int | None:
    """Known regular-season game counts (from Phase 0 probes) for the integrity gate."""
    return {
        "2005-06": 1230, "2006-07": 1230, "2007-08": 1230, "2008-09": 1230, "2009-10": 1230,
        "2010-11": 1230, "2011-12": 990, "2012-13": 1229, "2013-14": 1230, "2014-15": 1230,
        "2015-16": 1230, "2016-17": 1230, "2017-18": 1230, "2018-19": 1230, "2019-20": 1059,
        "2020-21": 1080, "2021-22": 1230, "2022-23": 1230, "2023-24": 1230, "2024-25": 1230,
        "2025-26": 1230,
    }.get(season)


def to_games(rows: list[dict], season: str) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Split the two-rows-per-game payload into (games, teams, season_meta)."""
    games: dict[str, dict] = {}
    teams: dict[int, tuple] = {}
    for r in rows:
        gid = str(r.get("GAME_ID") or "")
        if not gid:
            continue
        matchup = r.get("MATCHUP") or ""
        is_home = "vs." in matchup
        teams[int(r["TEAM_ID"])] = (int(r["TEAM_ID"]), r.get("TEAM_ABBREVIATION"),
                                   r.get("TEAM_NAME"), api.now_iso())
        g = games.setdefault(gid, {
            "game_id": gid, "season": season, "season_type": "regular",
            "game_date": _parse_date(r.get("GAME_DATE")), "tipoff_ts": None,
            "home_team_id": None, "away_team_id": None, "home_score": None, "away_score": None,
            "source": ENDPOINT, "asof_ts": api.now_iso(),
        })
        pts = r.get("PTS")
        if is_home:
            g["home_team_id"] = int(r["TEAM_ID"])
            g["home_score"] = int(pts) if pts is not None else None
        else:
            g["away_team_id"] = int(r["TEAM_ID"])
            g["away_score"] = int(pts) if pts is not None else None

    game_rows = [(g["game_id"], g["season"], g["season_type"], g["game_date"], g["tipoff_ts"],
                  g["home_team_id"], g["away_team_id"], g["home_score"], g["away_score"],
                  g["source"], g["asof_ts"]) for g in games.values()]
    season_meta = [(season, int(season[:4]), int("20" + season[-2:]), len(games), api.now_iso())]
    return game_rows, list(teams.values()), season_meta


def ingest_season(con: sqlite3.Connection, season: str, *, force: bool = False,
                  prune: bool = False, verbose: bool = True) -> dict:
    unit = f"season:{season}:game_logs"
    if api.is_complete(con, unit) and not force:
        if verbose:
            print(f"  {season}: already complete (skip)")
        return {"season": season, "skipped": True}

    rows = fetch_season(season, verbose=verbose)
    game_rows, team_rows, season_meta = to_games(rows, season)

    con.executemany(
        """INSERT INTO teams(team_id, abbr, full_name, asof_ts) VALUES (?,?,?,?)
           ON CONFLICT(team_id) DO UPDATE SET abbr=excluded.abbr, full_name=excluded.full_name""",
        team_rows,
    )
    con.executemany(
        """INSERT INTO seasons(season, start_year, end_year, n_games_known, asof_ts) VALUES (?,?,?,?,?)
           ON CONFLICT(season) DO UPDATE SET n_games_known=excluded.n_games_known""",
        season_meta,
    )
    con.executemany(
        """INSERT INTO games(game_id, season, season_type, game_date, tipoff_ts, home_team_id,
                             away_team_id, home_score, away_score, source, asof_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET
             home_score=excluded.home_score, away_score=excluded.away_score,
             game_date=excluded.game_date, source=excluded.source,
             source_updated_at=excluded.asof_ts""",
        game_rows,
    )
    # a game with a final score is also a settled result
    con.executemany(
        """INSERT INTO results(game_id, home_win, final_margin, settled_at)
           SELECT game_id, (home_score > away_score), (home_score - away_score), ?
           FROM games WHERE game_id = ? AND home_score IS NOT NULL AND away_score IS NOT NULL
           ON CONFLICT(game_id) DO UPDATE SET home_win=excluded.home_win,
             final_margin=excluded.final_margin, settled_at=excluded.settled_at""",
        [(api.now_iso(), r[0]) for r in game_rows],
    )
    con.commit()
    api.mark_complete(con, unit)

    n = len(game_rows)
    expected = season_row_count_expected(season)
    if prune:
        api.prune_cache(ENDPOINT, [season])
    if verbose:
        flag = "" if expected in (None, n) else f"  <-- expected {expected}"
        print(f"  {season}: {n} games ingested{flag}")
    return {"season": season, "games": n, "expected": expected}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest NBA regular-season game logs")
    ap.add_argument("--all", action="store_true", help="every season 2005-06..2025-26")
    ap.add_argument("--season", action="append", default=[], help="e.g. --season 2005-06")
    ap.add_argument("--force", action="store_true", help="re-ingest even if marked complete")
    ap.add_argument("--prune", action="store_true", help="delete raw cache after a successful parse")
    args = ap.parse_args(argv)

    seasons = api.SEASONS if args.all else args.season
    if not seasons:
        ap.error("give --all or at least one --season")

    con = build.init(verbose=False)
    print(f"db: {paths.DB}")
    summary = []
    for s in seasons:
        try:
            summary.append(ingest_season(con, s, force=args.force, prune=args.prune))
        except Exception as exc:  # noqa: BLE001
            print(f"  {s}: FAILED {type(exc).__name__}: {exc}")
            summary.append({"season": s, "error": str(exc)})
    total = sum(r.get("games", 0) for r in summary)
    bad = [r["season"] for r in summary if r.get("expected") not in (None, r.get("games"))]
    print(f"done: {len([r for r in summary if 'games' in r])} seasons, {total} games")
    if bad:
        print(f"count mismatches vs known totals: {bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

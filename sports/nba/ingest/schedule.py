"""Ingest the UPCOMING season schedule (scheduleleaguev2) - the T-60 cron's input.

    python -m sports.nba.ingest.schedule --season 2026-27
    python -m sports.nba.ingest.schedule --season 2026-27 --dry-run

Why this exists: leaguegamefinder (game_logs.py) only surfaces COMPLETED games,
so it cannot seed an upcoming season. scheduleleaguev2 returns the full schedule
with UTC tip-off times and team ids BEFORE any game is played - exactly what the
T-60 odds capture and the sealed-arm prediction log need.

What it writes: regular-season games (gameId prefix 002) with BOTH teams assigned,
into `games` (game_date, tipoff_ts, home/away team ids, is_neutral, source).
TBD NBA Cup knockout placeholders (no teams yet) are skipped and reported; they
are picked up on a later run once the league assigns teams. Scores stay NULL
until box_scores/game_logs settle them - this module never touches results.

Idempotent: re-running upserts the same rows (ON CONFLICT game_id).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from sports.nba.db import build, paths
from sports.nba.ingest import nba_api_client as api

ENDPOINT = "scheduleleaguev2"
REGULAR_PREFIX = "002"          # 001=preseason, 002=regular, 006=cup-knockout(TBD)


def fetch_schedule(season: str, verbose: bool = False) -> list[dict]:
    from nba_api.stats.endpoints import scheduleleaguev2

    def call():
        return scheduleleaguev2.ScheduleLeagueV2(season=season, league_id="00", timeout=60)

    resp, errors = api.call_with_retry(call, label=f"scheduleleaguev2 {season}", verbose=verbose)
    if resp is None:
        raise RuntimeError(f"{season}: all attempts failed: {errors[-1] if errors else 'unknown'}")
    game_dates = resp.get_dict().get("leagueSchedule", {}).get("gameDates", [])
    games = [g for gd in game_dates for g in gd.get("games", [])]
    api.write_cache(ENDPOINT, season, games)
    return games


def _iso_utc(raw: str | None) -> str | None:
    """'2026-10-20T19:00:00Z' -> canonical ISO (already UTC); None passes through."""
    if not raw:
        return None
    return raw if raw.endswith("Z") else raw + "Z"


def to_game_rows(games: list[dict], season: str) -> tuple[list[tuple], list[dict]]:
    """Regular-season games with both teams assigned -> games rows.

    Returns (rows, skipped_tbd). Rows match the `games` schema columns used by
    game_logs.to_games plus tipoff_ts and is_neutral (which game_logs leaves NULL).
    """
    rows: list[tuple] = []
    skipped: list[dict] = []
    ts = api.now_iso()
    for g in games:
        gid = str(g.get("gameId") or "")
        if not gid.startswith(REGULAR_PREFIX):
            continue                              # preseason (001) / cup-final (006)
        home = g.get("homeTeam") or {}
        away = g.get("awayTeam") or {}
        hid, aid = home.get("teamId"), away.get("teamId")
        if not (hid and aid):
            skipped.append({"game_id": gid, "label": g.get("gameLabel"),
                            "reason": "teams TBD (knockout placeholder)"})
            continue
        tipoff = _iso_utc(g.get("gameDateTimeUTC"))
        game_date = (g.get("gameDateUTC") or "")[:10] or (tipoff or "")[:10]
        rows.append((gid, season, "regular", game_date, tipoff,
                     int(hid), int(aid), None, None,          # scores: unsettled
                     1 if g.get("isNeutral") else 0, ENDPOINT, ts))
    return rows, skipped


def ensure_season(con: sqlite3.Connection, season: str, n_games: int) -> None:
    """The games.season FK needs a seasons row; seed it for a new season."""
    con.execute(
        """INSERT INTO seasons(season, start_year, end_year, n_games_known, asof_ts)
           VALUES (?,?,?,?,?)
           ON CONFLICT(season) DO UPDATE SET n_games_known=excluded.n_games_known""",
        (season, int(season[:4]), int("20" + season[-2:]), n_games, api.now_iso()))
    con.commit()


def upsert_games(con: sqlite3.Connection, rows: list[tuple]) -> int:
    con.executemany(
        """INSERT INTO games(game_id, season, season_type, game_date, tipoff_ts,
                             home_team_id, away_team_id, home_score, away_score,
                             is_neutral, source, asof_ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET
             game_date=excluded.game_date, tipoff_ts=excluded.tipoff_ts,
             home_team_id=excluded.home_team_id, away_team_id=excluded.away_team_id,
             is_neutral=excluded.is_neutral, source=excluded.source,
             source_updated_at=excluded.asof_ts""",
        rows,
    )
    con.commit()
    return len(rows)


def ingest_season(con: sqlite3.Connection, season: str, *, dry_run: bool = False,
                  verbose: bool = True) -> dict:
    games = fetch_schedule(season, verbose=verbose)
    rows, skipped = to_game_rows(games, season)
    if not dry_run:
        ensure_season(con, season, len(rows))
    n = 0 if dry_run else upsert_games(con, rows)
    if verbose:
        print(f"  {season}: {len(rows)} regular-season games scheduled"
              + (" (dry-run: not written)" if dry_run else f" (upserted {n})"))
        if skipped:
            print(f"    skipped {len(skipped)} TBD placeholder(s) "
                  f"(teams not yet assigned; picked up on a later run)")
    return {"season": season, "scheduled": len(rows), "written": n,
            "skipped_tbd": len(skipped), "dry_run": dry_run}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ingest an upcoming season schedule (scheduleleaguev2)")
    ap.add_argument("--season", required=True, help="e.g. --season 2026-27")
    ap.add_argument("--dry-run", action="store_true", help="fetch + report; write nothing")
    args = ap.parse_args(argv)
    con = build.init(verbose=False)
    print(f"db: {paths.DB}")
    out = ingest_season(con, args.season, dry_run=args.dry_run)
    print(f"done: {out['scheduled']} scheduled, {out['written']} written, "
          f"{out['skipped_tbd']} TBD skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

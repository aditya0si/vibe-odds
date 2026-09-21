"""Parse the cached LeagueGameFinder payloads into team-level game lines.

    python -m sports.nba.ingest.team_stats

Why this exists: the per-game box-score ingest is the slow, rate-limited path
(stats.nba.com throttles), but the game-log payloads we already cached contain the
full team line for every game (FGM/FGA/FTA/OREB/TOV/...). Possessions, pace and
ORtg/DRtg all follow from those, so team-strength features do not need any
additional API calls. Zero network: this reads sports/nba/data/raw/leaguegamefinder/*.json.gz.
"""

from __future__ import annotations

import gzip
import json
import sys

from sports.nba.db import build, paths
from sports.nba.ingest import nba_api_client as api
from sports.nba.ingest.game_logs import ENDPOINT, _parse_date

COLUMNS = ("MIN", "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB",
           "AST", "STL", "BLK", "TOV", "PF", "PLUS_MINUS")


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def parse_season(season: str) -> list[tuple]:
    p = paths.RAW / ENDPOINT / f"{season}.json.gz"
    if not p.exists():
        return []
    with gzip.open(p, "rt", encoding="utf-8") as f:
        rows = json.load(f)
    ts = api.now_iso()
    out = []
    for r in rows:
        gid = str(r.get("GAME_ID") or "")
        matchup = r.get("MATCHUP") or ""
        if not gid or not matchup:
            continue
        is_home = 1 if "vs." in matchup else 0
        out.append((gid, int(r["TEAM_ID"]), is_home, *[_num(r.get(c)) for c in COLUMNS], ts))
    return out


def main(argv: list[str] | None = None) -> int:
    con = build.init(verbose=False)
    total = 0
    for season in api.SEASONS:
        rows = parse_season(season)
        if not rows:
            print(f"  {season}: no cached payload (skipped)")
            continue
        con.executemany(
            """INSERT OR REPLACE INTO game_team_stats(game_id, team_id, is_home, minutes, pts, fgm, fga,
                   fg3m, fg3a, ftm, fta, oreb, dreb, reb, ast, stl, blk, tov, pf, plus_minus, asof_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        con.commit()
        total += len(rows)
        print(f"  {season}: {len(rows)} team-game rows")
    n = con.execute("SELECT COUNT(*) n FROM game_team_stats").fetchone()["n"]
    print(f"done: {total} rows written this run, {n} in table")
    return 0


if __name__ == "__main__":
    sys.exit(main())

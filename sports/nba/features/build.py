"""Build as-of feature rows for every NBA game (no network, no market data).

    python -m sports.nba.features.build --version v1
    python -m sports.nba.features.build --version v2   # v1 + pre-game availability
    python -m sports.nba.features.build --check-leakage     # proves the pass is as-of

The pass walks games in chronological order and, for each game, emits a feature row from
state that was updated only by EARLIER games; the game's own result is applied afterwards.
That ordering is the whole as-of guarantee, so the leakage check re-runs the same pass over a
truncated game list and asserts every overlapping row is byte-identical.

Features (all market-free):
  elo_home, elo_away, elo_diff      - Elo before the game, season-regressed at season boundaries
  hca                               - home-court advantage in Elo points, estimated from the
                                      EXPANDING home-win rate of PRIOR seasons (time-varying, and
                                      never from the season it is used in)
  form_margin_{home,away}           - mean point margin over the last 10 games
  pace_{home,away}                  - mean possessions per team-game over the last 10
  ortg_{home,away}, drtg_{home,away}- points per 100 possessions (last 10)
  rest_{home,away}, b2b_{home,away} - days since last game; back-to-back flag
  games_last7_{home,away}           - schedule density
  is_neutral, season_game_no, era flags (covid, empty_arena, rule_break_14s, season_length)

Versions:
  v1 - the keys above (frozen: the published A5 arm depends on v1 rows being
       byte-identical, so the v1 path must never change).
  v2 - every v1 key plus the pre-game availability keys from features/availability.py
       (avail_missing_home/away, avail_diff, avail_top_out_home/away,
       avail_n_inactive_home/away). v2 rows are stored under feature_version "v2",
       so v1 rows are untouched.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date as _date, timedelta

from sports.nba.db import build, paths
from sports.nba.features import availability as AV
from sports.nba.features import ratings as R

FEATURE_VERSION = "v1"
AVAIL_VERSION = "v2"
EMPTY_ARENA_SEASON = "2020-21"
COVID_SEASONS = {"2019-20", "2020-21"}
RULE_BREAK_FROM = "2018-19"      # 14-second offensive-rebound reset
SEASON_LENGTH = {}


def load_games(con: sqlite3.Connection, upto: str | None = None) -> list[sqlite3.Row]:
    sql = """SELECT g.game_id, g.season, g.game_date, g.home_team_id, g.away_team_id,
                    g.home_score, g.away_score, g.is_neutral
             FROM games g
             WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL"""
    params: tuple = ()
    if upto:
        sql += " AND g.game_date <= ?"
        params = (upto,)
    sql += " ORDER BY g.game_date, g.game_id"
    return list(con.execute(sql, params))


def load_team_lines(con: sqlite3.Connection) -> dict[tuple[str, int], sqlite3.Row]:
    return {(r["game_id"], r["team_id"]): r
            for r in con.execute("""SELECT game_id, team_id, pts, fga, fta, oreb, tov
                                    FROM game_team_stats""")}


def build_rows(con: sqlite3.Connection, upto: str | None = None,
               verbose: bool = True, version: str = FEATURE_VERSION) -> tuple[dict[str, dict], dict]:
    if version not in (FEATURE_VERSION, AVAIL_VERSION):
        raise ValueError(f"unknown feature version {version!r} (want 'v1' or 'v2')")
    games = load_games(con, upto)
    lines = load_team_lines(con)
    # v2 merges the pre-game availability pass. It takes the same `upto` cut, and it is
    # as-of by the same construction (state updated only after each game's row is
    # emitted), so the merged rows inherit the leakage guarantee - see test_asof_v2.
    avail = AV.build_availability(con, upto=upto, verbose=False) if version == AVAIL_VERSION else {}
    elo = R.EloState()
    hist: dict[int, R.TeamHistory] = defaultdict(R.TeamHistory)

    season_home_wins: dict[str, list[int]] = defaultdict(list)   # non-neutral home results
    season_length: dict[str, int] = defaultdict(int)
    hca_by_season: dict[str, float] = {}
    rows: dict[str, dict] = {}
    cur_season: str | None = None

    for g in games:
        season = g["season"]
        if season != cur_season:
            if cur_season is not None:
                elo.new_season()
            # HCA from PRIOR seasons only (expanding mean of non-neutral home win rate)
            prior = [w for s, ws in season_home_wins.items() if s < season for w in ws]
            p_home = (sum(prior) / len(prior)) if prior else 0.60
            hca_by_season[season] = round(R.win_prob_to_elo(p_home), 1)
            cur_season = season

        home_id, away_id = g["home_team_id"], g["away_team_id"]
        season_length[season] += 1
        line_h = lines.get((g["game_id"], home_id))
        line_a = lines.get((g["game_id"], away_id))
        fh, fa = hist[home_id].features(), hist[away_id].features()

        def rest_of(team_id: int) -> tuple[int | None, int, int]:
            h = hist[team_id]
            rd = R.rest_days(h.last_date, g["game_date"])
            recent = sum(1 for d in h.recent_dates if _date.fromisoformat(d) > _date.fromisoformat(g["game_date"]) - timedelta(days=7))
            return rd, 1 if rd == 1 else 0, recent

        rest_h, b2b_h, g7_h = rest_of(home_id)
        rest_a, b2b_a, g7_a = rest_of(away_id)

        rows[g["game_id"]] = {
            "feature_version": version,
            "game_id": g["game_id"], "season": season, "game_date": g["game_date"],
            "home_team_id": home_id, "away_team_id": away_id,
            "home_win": int(g["home_score"] > g["away_score"]),
            "elo_home": round(elo.rating(home_id), 2), "elo_away": round(elo.rating(away_id), 2),
            "elo_diff": round(elo.rating(home_id) - elo.rating(away_id), 2),
            "hca": hca_by_season[season],
            "form_margin_home": fh["form_margin"], "form_margin_away": fa["form_margin"],
            "pace_home": fh["pace"], "pace_away": fa["pace"],
            "ortg_home": fh["ortg"], "ortg_away": fa["ortg"],
            "drtg_home": fh["drtg"], "drtg_away": fa["drtg"],
            "games_played_home": fh["games_played"], "games_played_away": fa["games_played"],
            "rest_home": rest_h, "rest_away": rest_a, "b2b_home": b2b_h, "b2b_away": b2b_a,
            "games_last7_home": g7_h, "games_last7_away": g7_a,
            "is_neutral": int(g["is_neutral"] or 0),
            "season_game_no": season_length[season],
            "era_covid": 1 if season in COVID_SEASONS else 0,
            "era_empty_arena": 1 if season == EMPTY_ARENA_SEASON else 0,
            "era_rule_break_14s": 1 if season >= RULE_BREAK_FROM else 0,
        }
        if version == AVAIL_VERSION:
            # v1 keys above are untouched (byte-identical to the v1 pass); the
            # availability keys are appended after them.
            for k in AV.AVAIL_KEYS:
                rows[g["game_id"]][k] = avail.get(g["game_id"], {}).get(k)

        # ---- state updates (only now may the current game's result be used) ----
        margin = float(g["home_score"] - g["away_score"])
        home_won = g["home_score"] > g["away_score"]
        if not g["is_neutral"]:
            season_home_wins[season].append(1 if home_won else 0)
        elo.update(home_id, away_id, home_won, margin, hca_by_season[season])
        if line_h is not None and line_a is not None:
            ph, pa = R.possessions(line_h["fga"], line_h["fta"], line_h["oreb"], line_h["tov"]), \
                     R.possessions(line_a["fga"], line_a["fta"], line_a["oreb"], line_a["tov"])
            if ph and pa:
                hist[home_id].add(g["game_date"], line_h["pts"], line_a["pts"], ph, pa)
                hist[away_id].add(g["game_date"], line_a["pts"], line_h["pts"], pa, ph)
        else:
            hist[home_id].last_date = g["game_date"]
            hist[away_id].last_date = g["game_date"]

    evidence = {
        "feature_version": version,
        "n_games": len(rows),
        "seasons": {s: {"games": n, "hca_elo": hca_by_season.get(s)} for s, n in sorted(season_length.items())},
        "home_win_rate_by_season": {s: (sum(w) / len(w) if w else None) for s, w in sorted(season_home_wins.items())},
        "elo_final_top10": sorted(((t, round(r, 1)) for t, r in elo.ratings.items()), key=lambda x: -x[1])[:10],
    }
    if verbose:
        print(f"built {len(rows)} feature rows for {len(season_length)} seasons")
    return rows, evidence


def write_rows(con: sqlite3.Connection, rows: dict[str, dict]) -> int:
    from sports.nba.ingest import nba_api_client as api
    ts = api.now_iso()
    con.executemany(
        """INSERT OR REPLACE INTO features(game_id, feature_version, built_at, asof_ts, payload)
           VALUES (?,?,?,?,?)""",
        [(gid, r["feature_version"], ts, ts, json.dumps(r)) for gid, r in rows.items()])
    con.commit()
    return len(rows)


def leakage_check(con: sqlite3.Connection, version: str = FEATURE_VERSION) -> int:
    """Re-run the pass over a truncated game list; every overlapping row must be identical."""
    games = load_games(con)
    if not games:
        print("no games")
        return 1
    cut = games[int(len(games) * 0.6)]["game_date"]
    full, _ = build_rows(con, verbose=False, version=version)
    trunc, _ = build_rows(con, upto=cut, verbose=False, version=version)
    overlap = [gid for gid, r in trunc.items() if r["game_date"] <= cut]
    bad = [gid for gid in overlap if json.dumps(full[gid], sort_keys=True) != json.dumps(trunc[gid], sort_keys=True)]
    print(f"leakage check (version {version}): {len(overlap)} rows rebuilt from a truncated history (cut {cut}); mismatches: {len(bad)}")
    for gid in bad[:5]:
        print(f"  MISMATCH {gid}: {json.dumps(full[gid])[:200]} vs {json.dumps(trunc[gid])[:200]}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build as-of NBA feature rows")
    ap.add_argument("--version", default=FEATURE_VERSION, choices=(FEATURE_VERSION, AVAIL_VERSION),
                    help="v1 (frozen) or v2 (v1 + pre-game availability)")
    ap.add_argument("--check-leakage", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="build and report without touching the DB")
    args = ap.parse_args(argv)

    con = build.init(verbose=False)
    if args.check_leakage:
        return leakage_check(con, version=args.version)

    rows, evidence = build_rows(con, version=args.version)
    ev_path = paths.DATA / f"features_{args.version}_evidence.json"
    ev_path.write_text(json.dumps(evidence, indent=1), encoding="utf-8")
    print(f"evidence -> {ev_path.name}")
    hwr = evidence["home_win_rate_by_season"]
    print("home win rate (non-neutral) by season: "
          + ", ".join(f"{s}:{v:.3f}" for s, v in hwr.items() if v) [ :200])
    if not args.no_write:
        n = write_rows(con, rows)
        print(f"wrote {n} rows to features (version {args.version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

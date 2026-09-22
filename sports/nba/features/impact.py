"""Phase-2 C1: player-IMPACT value weights for inactive players (A7 arm inputs).

A6 (features/availability.py) weights absences by a box production score
(pts + 1.5ast + reb - tov). Two blind spots this module fixes:

  1. plus_minus is team-context impact - what a production total is a proxy for
     at best (it overvalues ball-dominant scorers and undervalues defenders);
  2. SIGN. Missing a BAD player helps. A quality share can never express that
     (shares are >= 0); an impact sum can (a negative-value absence is positive
     news for the team).

Derived features per game (only for games with a PUBLISHED inactive list -
the same authoritative rule as features/availability.py, reused verbatim):

  impact_out_home / impact_out_away : summed as-of impact value of the team's
                                      inactive players (positive = good players
                                      out = bad for that team)
  impact_out_diff                   : away_out - home_out (positive favours the
                                      home team, matching avail_diff's sign)

Value model: a shrunk rolling mean of plus_minus per appearance over the last
PM_WINDOW games with minutes > 0. No appearance history -> the neutral prior
0.0 ("an unknown absence is neutral"); it must NOT be REPLACEMENT_LEVEL, which
belongs to the production-share scale. Values are winsorised at +/-PM_CLIP.

As-of discipline identical to features/availability.py: state is updated only
after a game's row is emitted, and the box load is cut at `upto`, so
build_impact(upto=...) reproduces the full build's prefix exactly.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict, deque

from sports.nba.features.availability import games_with_authoritative_inactives

PM_WINDOW = 40        # a player's last N appearances for the impact estimate
PM_SHRINK = 12.0      # shrinkage strength toward the neutral prior (0.0)
PM_CLIP = 10.0        # winsorised at +/-10: single-game plus/minus beyond this is noise

IMPACT_KEYS = ("impact_out_home", "impact_out_away", "impact_out_diff")


def player_value(hist: deque) -> float:
    """Shrunk rolling plus/minus per appearance. No history -> 0.0 (neutral)."""
    n = len(hist)
    if n == 0:
        return 0.0
    mean = sum(hist) / n
    v = mean * (n / (n + PM_SHRINK))
    return max(-PM_CLIP, min(PM_CLIP, v))


def build_impact(con: sqlite3.Connection, upto: str | None = None,
                 verbose: bool = False) -> dict[str, dict]:
    """Chronological pass over games -> {game_id: impact features}."""
    sql = """SELECT g.game_id, g.season, g.game_date, g.home_team_id, g.away_team_id
             FROM games g
             WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL"""
    params: tuple = ()
    if upto:
        sql += " AND g.game_date <= ?"
        params = (upto,)
    sql += " ORDER BY g.game_date, g.game_id"
    games = list(con.execute(sql, params))

    inactive_by_game: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for r in con.execute("SELECT game_id, team_id, player_id FROM game_inactives"):
        inactive_by_game[r["game_id"]][r["team_id"]].add(r["player_id"])

    # Defense in depth (as in availability.py): a truncated rebuild must not even
    # LOAD box rows dated after the cut. The chronological state-update loop is
    # the real as-of guarantee.
    box_sql = ("SELECT game_id, team_id, player_id, minutes, plus_minus "
               "FROM game_traditional")
    box_params: tuple = ()
    if upto:
        box_sql += " WHERE game_id IN (SELECT game_id FROM games WHERE game_date <= ?)"
        box_params = (upto,)
    box_sql += " ORDER BY game_id"
    boxes: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in con.execute(box_sql, box_params):
        boxes[r["game_id"]].append(r)

    player_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=PM_WINDOW))
    authoritative = games_with_authoritative_inactives(con)
    out: dict[str, dict] = {}

    for g in games:
        gid, home, away = g["game_id"], g["home_team_id"], g["away_team_id"]
        if gid not in authoritative:
            # No published list -> unknown, never 0.0 (same completeness trap as A6).
            out[gid] = {k: None for k in IMPACT_KEYS}
        else:
            feats: dict[str, float | None] = {}
            for side, tid in (("home", home), ("away", away)):
                inactive = inactive_by_game.get(gid, {}).get(tid, set())
                feats[f"impact_out_{side}"] = round(
                    sum(player_value(player_hist[p]) for p in inactive), 4)
            feats["impact_out_diff"] = round(
                feats["impact_out_away"] - feats["impact_out_home"], 4)
            out[gid] = feats

        # ---- state updates, after emitting the row ----
        for r in boxes.get(gid, []):
            if (r["minutes"] or 0) > 0 and r["plus_minus"] is not None:
                player_hist[r["player_id"]].append(float(r["plus_minus"]))

    if verbose:
        known = sum(1 for v in out.values() if v["impact_out_home"] is not None)
        print(f"impact: {len(out)} games ({known} with authoritative lists)")
    return out

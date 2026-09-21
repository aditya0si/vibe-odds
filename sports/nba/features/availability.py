"""Availability features: how much player quality a team is missing, known BEFORE tip-off.

This is the signal the literature says actually moves NBA predictions (missing 2 of a team's top 3
costs ~9pp of win rate; facing such a team is worth ~+15% relative). It is also the signal most
published models omit, and the only one our formula has not yet been given.

Two ingredients, both strictly as-of:
  1. The PRE-GAME inactive list for the game itself (`game_inactives`, from the box-score summary
     payload; the league publishes it before tip-off). Never the box score's per-player `comment`
     field (DNP/DND), which is a post-game fact.
  2. A player-quality estimate from that player's PRIOR games only - a rolling mean of a simple
     production score over their last 20 appearances.

Derived features per game:
  avail_missing_home / avail_missing_away : share of the as-of roster's quality that is inactive (0-1)
  avail_diff                             : away_missing - home_missing (positive favours the home team)
  avail_top_out_home / avail_top_out_away: is one of the roster's top-3 rated players inactive?
  avail_n_inactive_home / _away          : count of inactive players

The as-of property is enforced the same way as the main feature pass: state is updated only after a
game's features have been emitted, so `build_availability(upto=...)` reproduces identical rows.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque

ROSTER_WINDOW = 10        # team games used to define the as-of roster
RATING_WINDOW = 20        # a player's last N appearances for the quality estimate
TOP_N = 3


def production(row: sqlite3.Row) -> float:
    """Simple, explainable player production score (points + playmaking + rebounding - turnovers)."""
    pts = row["pts"] or 0
    ast = row["ast"] or 0
    reb = row["reb"] or 0
    tov = row["tov"] or 0
    return float(pts) + 1.5 * float(ast) + 1.0 * float(reb) - 1.0 * float(tov)


def build_availability(con: sqlite3.Connection, upto: str | None = None,
                       verbose: bool = False) -> dict[str, dict]:
    """Chronological pass over games -> {game_id: availability features}."""
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

    boxes: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in con.execute("""SELECT game_id, team_id, player_id, pts, ast, reb, tov, minutes
                            FROM game_traditional ORDER BY game_id"""):
        boxes[r["game_id"]].append(r)

    player_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=RATING_WINDOW))
    team_recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=ROSTER_WINDOW))
    out: dict[str, dict] = {}

    for g in games:
        gid, home, away = g["game_id"], g["home_team_id"], g["away_team_id"]
        feats: dict[str, float | int | None] = {}
        for side, tid in (("home", home), ("away", away)):
            roster = set()
            for players in team_recent.get(tid, []):
                roster |= players
            ratings = {p: (sum(player_hist[p]) / len(player_hist[p])) if player_hist[p] else 0.0
                       for p in roster}
            total = sum(ratings.values())
            inactive = inactive_by_game.get(gid, {}).get(tid, set())
            missing = sum(ratings.get(p, 0.0) for p in inactive)
            top = sorted(ratings.items(), key=lambda kv: -kv[1])[:TOP_N]
            feats[f"avail_missing_{side}"] = round(missing / total, 4) if total > 0 else None
            feats[f"avail_n_inactive_{side}"] = len(inactive)
            feats[f"avail_top_out_{side}"] = 1 if any(p in inactive for p, _ in top) else 0
        mh, ma = feats.get("avail_missing_home"), feats.get("avail_missing_away")
        feats["avail_diff"] = round(ma - mh, 4) if (mh is not None and ma is not None) else None
        out[gid] = feats

        # ---- state updates, after emitting the row ----
        for r in boxes.get(gid, []):
            if (r["minutes"] or 0) > 0:
                player_hist[r["player_id"]].append(production(r))
        played: dict[int, set[int]] = defaultdict(set)
        for r in boxes.get(gid, []):
            if (r["minutes"] or 0) > 0:
                played[r["team_id"]].add(r["player_id"])
        for tid, players in played.items():
            team_recent[tid].append(players)
    if verbose:
        have = sum(1 for f in out.values() if f.get("avail_missing_home") is not None)
        print(f"availability built for {len(out)} games; usable on {have}")
    return out


def coverage(con: sqlite3.Connection) -> dict:
    """Coverage report: how many games have a usable inactive list (per season)."""
    rows = con.execute("""
        SELECT g.season,
               COUNT(DISTINCT g.game_id) games,
               COUNT(DISTINCT i.game_id) games_with_inactives
        FROM games g LEFT JOIN game_inactives i ON i.game_id = g.game_id
        WHERE g.season_type='regular'
        GROUP BY g.season ORDER BY g.season""").fetchall()
    return {r["season"]: {"games": r["games"], "with_inactives": r["games_with_inactives"],
                          "share": round(r["games_with_inactives"] / r["games"], 3) if r["games"] else None}
            for r in rows}

"""A9 Stage 0.1 - data audit for the player-value model.

Per completed regular season: games, games covered by >=1 game_traditional row,
player-rows, rows/game, distinct players, played-row share (minutes > 0; NULL
minutes = a DNP row with all-zero stats, a normal pattern, not missing data) and
plus_minus completeness among played rows. Read-only.

Failure criteria: game coverage < 95%, played share < 70%, or any played row
missing plus_minus.

Usage: .venv/Scripts/python.exe scratch/pv_audit.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

con = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "sports" / "nba" / "data" / "nba.sqlite"))
con.row_factory = sqlite3.Row

rows = con.execute("""
    SELECT g.season,
           COUNT(DISTINCT g.game_id)                                 AS games,
           COUNT(DISTINCT CASE WHEN t.player_id IS NOT NULL THEN g.game_id END) AS covered,
           COUNT(t.player_id)                                        AS player_rows,
           COUNT(DISTINCT t.player_id)                               AS players,
           SUM(CASE WHEN t.minutes > 0 THEN 1 ELSE 0 END)            AS played,
           SUM(CASE WHEN t.minutes > 0 AND t.plus_minus IS NOT NULL THEN 1 ELSE 0 END) AS pm_played_ok
    FROM games g LEFT JOIN game_traditional t ON t.game_id = g.game_id
    WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
    GROUP BY g.season ORDER BY g.season""").fetchall()

print(f"{'season':>8} {'games':>6} {'cov':>6} {'rows':>7} {'r/g':>5} {'players':>7} {'played%':>7} {'pm%':>7}")
bad = []
for r in rows:
    cov = r["covered"] / r["games"] if r["games"] else 0
    rpg = r["player_rows"] / r["covered"] if r["covered"] else 0
    playedpct = r["played"] / r["player_rows"] if r["player_rows"] else 0
    pmpct = r["pm_played_ok"] / r["played"] if r["played"] else 0
    print(f"{r['season']:>8} {r['games']:>6} {cov:>5.1%} {r['player_rows']:>7} {rpg:>5.1f} "
          f"{r['players']:>7} {playedpct:>6.1%} {pmpct:>7.1%}")
    if cov < 0.95 or playedpct < 0.70 or pmpct < 0.995:
        bad.append(r["season"])

print()
if bad:
    print("SEASONS BELOW 95% COVERAGE:", bad)
    sys.exit(1)
print(f"all {len(rows)} seasons pass: full game coverage, played rows fully populated")

"""Is there any pre-tip (unsettled) feature row? i.e. can cmd_predict ever score a game?

features rows are written only for completed games (build.load_games filters home_score IS
NOT NULL). If no row joins an unplayed game, the predict tick stays structurally empty even
once the cron hours are fixed.
"""
import sqlite3

con = sqlite3.connect("sports/nba/data/nba.sqlite")
con.row_factory = sqlite3.Row

q = """
SELECT COUNT(*) n,
       SUM(CASE WHEN g.home_score IS NULL THEN 1 ELSE 0 END) unplayed
  FROM features f JOIN games g ON g.game_id = f.game_id
 WHERE f.feature_version = 'v3'
"""
r = con.execute(q).fetchone()
print(f"features v3 joined: n={r['n']} rows_for_unplayed_games={r['unplayed']}")

r = con.execute("SELECT COUNT(*) n FROM games WHERE season='2026-27' AND home_score IS NULL").fetchone()
print(f"2026-27 games still unplayed: {r['n']}")

r = con.execute("SELECT COUNT(*) n FROM games WHERE season='2026-27'").fetchone()
print(f"2026-27 games total: {r['n']}")

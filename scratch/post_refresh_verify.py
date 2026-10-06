"""Post-refresh verification for the nba-feature-refresh tick (read-only)."""
import sqlite3

con = sqlite3.connect("sports/nba/data/nba.sqlite")
con.row_factory = sqlite3.Row
c = con.cursor()

print("== features by version ==")
for r in c.execute(
    "SELECT feature_version, COUNT(*) n, MIN(asof_ts) min_asof, MAX(asof_ts) max_asof "
    "FROM features GROUP BY feature_version ORDER BY feature_version"
):
    print(dict(r))

print("== completed games (home_score not null) ==")
for r in c.execute(
    "SELECT season, COUNT(*) n, MIN(game_date) lo, MAX(game_date) hi FROM games "
    "WHERE home_score IS NOT NULL GROUP BY season ORDER BY season DESC LIMIT 4"
):
    print(dict(r))

print("== 2026-27 games ==")
for r in c.execute(
    "SELECT COUNT(*) total, "
    "SUM(CASE WHEN home_score IS NOT NULL THEN 1 ELSE 0 END) completed, "
    "MIN(game_date) lo, MAX(game_date) hi FROM games WHERE season='2026-27'"
):
    print(dict(r))

print("== v3 features joined to 2026-27 games ==")
for r in c.execute(
    "SELECT COUNT(*) n FROM features f JOIN games g ON g.game_id = f.game_id "
    "WHERE f.feature_version='v3' AND g.season='2026-27'"
):
    print(dict(r))

print("== active feature_version in config, if any ==")
try:
    for r in c.execute("SELECT * FROM feature_meta"):
        print(dict(r))
except Exception as e:
    print("feature_meta:", type(e).__name__, e)

con.close()

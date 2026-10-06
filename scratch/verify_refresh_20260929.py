"""Feature-refresh verification probe (runtime-derived dates, no hardcoded game dates).

Answers: did the refresh see any NEW completed games, and is the DB-wide count flat?
"""
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "sports" / "nba" / "data" / "nba.sqlite"

IST = timezone(timedelta(hours=5, minutes=30))
now = datetime.now(timezone.utc)
print("now_utc:", now.isoformat())
print("now_ist:", datetime.now(IST).isoformat())

con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row
cur = con.cursor()

# Completed games overall + latest date
r = cur.execute(
    "SELECT COUNT(*) AS n, MIN(game_date) AS lo, MAX(game_date) AS hi "
    "FROM games WHERE home_score IS NOT NULL"
).fetchone()
print(f"completed_games_all: n={r['n']} range={r['lo']}..{r['hi']}")

# Completed games per season
for row in cur.execute(
    "SELECT season, COUNT(*) AS n, MIN(game_date) AS lo, MAX(game_date) AS hi "
    "FROM games WHERE home_score IS NOT NULL GROUP BY season ORDER BY season DESC LIMIT 4"
):
    print(f"  season={row['season']} completed={row['n']} range={row['lo']}..{row['hi']}")

# 2026-27 slate totals
r = cur.execute(
    "SELECT COUNT(*) AS total, "
    "SUM(CASE WHEN home_score IS NOT NULL THEN 1 ELSE 0 END) AS completed "
    "FROM games WHERE season='2026-27'"
).fetchone()
print(f"season_2026_27: scheduled={r['total']} completed={r['completed']}")

# features table state
for row in cur.execute(
    "SELECT feature_version, COUNT(*) AS n, MAX(asof_ts) AS latest_asof "
    "FROM features GROUP BY feature_version ORDER BY feature_version"
):
    print(f"  features[{row['feature_version']}]: n={row['n']} latest_asof={row['latest_asof']}")

# v3 features joined to games: max game date covered
r = cur.execute(
    "SELECT COUNT(*) AS n, MAX(g.game_date) AS hi FROM features f "
    "JOIN games g ON g.game_id = f.game_id WHERE f.feature_version='v3'"
).fetchone()
print(f"features_v3_joined: n={r['n']} max_game_date={r['hi']}")

# any v3 rows for 2026-27?
r = cur.execute(
    "SELECT COUNT(*) AS n FROM features f JOIN games g ON g.game_id=f.game_id "
    "WHERE f.feature_version='v3' AND g.season='2026-27'"
).fetchone()
print(f"features_v3_2026_27_rows: {r['n']}")

con.close()

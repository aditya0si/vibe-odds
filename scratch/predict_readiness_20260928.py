"""Read-only: can the sealed-prediction tick log anything for 2026-27 yet?

Reports features rows by version (DB-wide and for 2026-27 games) and how many
2026-27 games are completed. 0 v3 rows for 2026-27 => the tick is structurally
empty on game days (pre-tip feature builder missing), not a capture bug.
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "sports" / "nba" / "data" / "nba.sqlite"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

print("features by version (DB-wide):")
for r in con.execute(
    "SELECT feature_version, COUNT(*) AS n FROM features GROUP BY feature_version ORDER BY 1"
):
    print(f"  {r['feature_version']}: {r['n']}")

print("\nfeatures rows for 2026-27 games, by version:")
for r in con.execute(
    """SELECT f.feature_version AS v, COUNT(*) AS n
       FROM features f JOIN games g ON g.game_id = f.game_id
       WHERE g.season = '2026-27' GROUP BY 1 ORDER BY 1"""
):
    print(f"  {r['v']}: {r['n']}")

r = con.execute(
    """SELECT COUNT(*) AS total,
              SUM(CASE WHEN home_score IS NOT NULL THEN 1 ELSE 0 END) AS completed
       FROM games WHERE season = '2026-27'"""
).fetchone()
print(f"\n2026-27 games: total={r['total']} completed={r['completed']}")
con.close()

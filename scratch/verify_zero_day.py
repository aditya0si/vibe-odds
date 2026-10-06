import sys, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.season_ops import today_games
con = sqlite3.connect("sports/nba/data/nba.sqlite")
con.row_factory = sqlite3.Row
for d in ("2026-09-24", "2026-09-25", "2026-10-20"):
    g = today_games(con, d)
    print(f"{d}: n={len(g)}", sorted(x.get("home") or x.get("home_name") or "?" for x in g)[:5])
print("season rows:", con.execute("SELECT COUNT(*) c FROM games WHERE season='2026-27'").fetchone()["c"])
print("range:", tuple(con.execute("SELECT MIN(game_date) a, MAX(game_date) b FROM games WHERE season='2026-27'").fetchone()))
print("kinds:", [tuple(r) for r in con.execute("SELECT snapshot_kind, COUNT(*) c FROM odds_snapshots GROUP BY snapshot_kind").fetchall()[:5]])

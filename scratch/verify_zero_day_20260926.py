"""Zero-game-day probe for the 2026-09-26 T-60 tick.

today_games() reads rows as dicts -> row_factory MUST be sqlite3.Row.
Prints today (UTC + IST) vs the 2026-10-20 opener control, plus season row count/range.
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.season_ops import today_games  # noqa: E402

DB = "sports/nba/data/nba.sqlite"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

utc_date = datetime.now(timezone.utc).date().isoformat()
ist_date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date().isoformat()

for label, d in (("today(UTC)", utc_date), ("today(IST)", ist_date), ("CONTROL opener", "2026-10-20")):
    try:
        g = today_games(con, d)
        print(f"{label:16s} {d} -> n_games={len(g)} ids={[x['game_id'] for x in g][:6]}")
    except Exception as e:
        print(f"{label:16s} {d} -> ERROR {type(e).__name__}: {e}")

row = con.execute(
    "SELECT COUNT(*) n, MIN(game_date) lo, MAX(game_date) hi FROM games WHERE season='2026-27'"
).fetchone()
print(f"season rows: n={row['n']} range={row['lo']} .. {row['hi']}")

row = con.execute(
    "SELECT snapshot_kind, COUNT(*) n FROM odds_snapshots GROUP BY snapshot_kind"
).fetchall()
print("odds_snapshots:", {r["snapshot_kind"]: r["n"] for r in row} or "empty")

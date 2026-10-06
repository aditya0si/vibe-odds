"""Which 2026-27 games can EVER get a sealed predict tick under the live cron grid?

Variant of t60_cron_coverage.py for the `nba-sealed-prediction-log` job, whose expr is
`30 17-23,0-2 * * *` (LOCAL/IST hours, one tick at minute :30 per hour). Predicting a game
needs a tick inside the T-60 window [tip-70, tip-50] on the game's own UTC date, exactly as
the capture check measures - the predict tick uses the same today_games() date keying.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
CRON_HOURS_LOCAL = (17, 18, 19, 20, 21, 22, 23, 0, 1, 2)
GRID = [datetime(2026, 6, 1, h, 30, tzinfo=IST) for h in CRON_HOURS_LOCAL]
WINDOW_LO, WINDOW_HI = timedelta(minutes=50), timedelta(minutes=70)

season = sys.argv[1] if len(sys.argv) > 1 else "2026-27"
con = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "sports" / "nba" / "data" / "nba.sqlite"))
rows = con.execute(
    "SELECT game_id, game_date, tipoff_ts FROM games "
    "WHERE season=? AND season_type='regular' AND tipoff_ts IS NOT NULL",
    (season,),
).fetchall()


def grid_near(dt_ist):
    for delta in (-1, 0, 1):
        d = dt_ist + timedelta(days=delta)
        for t in GRID:
            yield t.replace(year=d.year, month=d.month, day=d.day)


logged, date_drop, no_tick = [], [], []
for gid, gdate, tip in rows:
    tip_dt = datetime.fromisoformat(tip.replace("Z", "+00:00"))
    lo, hi = tip_dt - WINDOW_HI, tip_dt - WINDOW_LO
    in_window = [t for t in grid_near(tip_dt.astimezone(IST)) if lo <= t <= hi]
    if any(t.astimezone(UTC).date().isoformat() == gdate for t in in_window):
        logged.append(gid)
    elif in_window:
        date_drop.append((gid, gdate, tip))
    else:
        no_tick.append((gid, gdate, tip))

n = len(rows) or 1
print(f"predict grid `30 17-23,0-2` (IST) vs season {season}: {len(rows)} games")
print(f"  tick inside window, right UTC date       : {len(logged)} ({100*len(logged)/n:.1f}%)")
print(f"  tick in window, game_date filter drops   : {len(date_drop)} ({100*len(date_drop)/n:.1f}%)")
print(f"  no predict tick inside [tip-70, tip-50]  : {len(no_tick)} ({100*len(no_tick)/n:.1f}%)")
if no_tick:
    print(f"  first no-tick example                    : {no_tick[0]}")

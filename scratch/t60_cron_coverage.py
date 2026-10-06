"""Season-runway check: which games can EVER get a t60 row under the real cron grid?

Why: the NBA season crons are 5-field expressions evaluated in LOCAL (IST) time, not UTC
(verified against cron/executions.db: nba-t60-odds-capture `*/20 17-23,0-2` fires at IST
minutes {00,20,40}). That puts the tick block at UTC 11:30-21:10, while NBA T-60 windows
sit at UTC 22:50-02:40 (tipoffs 04:30-08:30 IST).

Usage:
    .venv/Scripts/python.exe scratch/t60_cron_coverage.py            # 2026-27
    .venv/Scripts/python.exe scratch/t60_cron_coverage.py 2025-26

A capture needs a tick inside [tipoff-70, tipoff-50] minutes AND
date(tick, UTC) == games.game_date (season_ops.today_games keys on game_date using the
UTC current date). Prints the two failure modes separately so neither is guessed at.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
CRON_HOURS_LOCAL = (17, 18, 19, 20, 21, 22, 23, 0, 1, 2)      # as written in the job config
GRID = [datetime(2026, 6, 1, h, m, tzinfo=IST) for h in CRON_HOURS_LOCAL for m in (0, 20, 40)]
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


captured, date_drop, no_tick = [], [], []
for gid, gdate, tip in rows:
    tip_dt = datetime.fromisoformat(tip.replace("Z", "+00:00"))
    lo, hi = tip_dt - WINDOW_HI, tip_dt - WINDOW_LO
    in_window = [t for t in grid_near(tip_dt.astimezone(IST)) if lo <= t <= hi]
    if any(t.astimezone(UTC).date().isoformat() == gdate for t in in_window):
        captured.append(gid)
    elif in_window:
        date_drop.append((gid, gdate, tip, [t.astimezone(UTC).strftime("%H:%MZ") for t in in_window]))
    else:
        no_tick.append((gid, gdate, tip))

n = len(rows) or 1
print(f"season {season}: {len(rows)} regular-season games with a published tipoff")
print(f"  capturable                            : {len(captured)} ({100*len(captured)/n:.1f}%)")
print(f"  tick in window, game_date filter drops : {len(date_drop)} ({100*len(date_drop)/n:.1f}%)")
print(f"  no tick inside [tip-70, tip-50] at all : {len(no_tick)} ({100*len(no_tick)/n:.1f}%)")
print(f"  tick block (UTC)                       : 11:30 .. 21:10")
if no_tick:
    print(f"  first no-tick example                  : {no_tick[0]}")
if date_drop:
    print(f"  first date-drop example                : {date_drop[0]}")

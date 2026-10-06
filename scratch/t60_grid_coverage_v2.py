"""Season-runway check v2: the deployed */20 all-day grid must cover every T-60 window.

v1 (t60_cron_coverage.py, 2026-09-28) found the OLD grid (`*/20 17-23,0-2`, evaluated
in LOCAL/IST time) covered only 10.9% of games: its tick block sat at UTC 11:30-21:10
while NBA T-60 windows sit at UTC 22:50-02:40. The deployed fix is structural:
  * the tick runs `*/20 * * * *` - minutes {0,20,40} of EVERY hour, all day (a 20-minute
    grid always lands inside the 30-minute [T-75, T-45] capture window);
  * the tool decides per tick whether any game is inside the window - no date filter
    anywhere on the path (season_ops.games_near is window-keyed: tipoff in
    [now-1h, now+6h], because tip-offs run past midnight UTC).

This probe re-verifies, over the real schedule, that for EVERY game the deployed grid
provides >=1 tick inside [tipoff-75, tipoff-45] AND that tick's games_near output
contains the game (the two failure modes of v1: no-tick and date-drop).

Usage: .venv/Scripts/python.exe scratch/t60_grid_coverage_v2.py [season]
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
GRID_MINUTES = (0, 20, 40)                      # `*/20 * * * *` in local (IST) time
WINDOW = (45, 75)                               # capture window: [T-75, T-45] minutes

season = sys.argv[1] if len(sys.argv) > 1 else "2026-27"
con = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "sports" / "nba" / "data" / "nba.sqlite"))
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT game_id, game_date, tipoff_ts FROM games "
    "WHERE season=? AND season_type='regular' AND tipoff_ts IS NOT NULL",
    (season,)).fetchall()
print(f"season {season}: {len(rows)} regular-season games with a published tipoff")
if rows:
    print(f"  sample tipoff_ts (raw): {rows[0]['tipoff_ts']}")

from tools.season_ops import games_near, _ts    # noqa: E402  (the deployed emitter)


def ticks_near(dt_utc):
    """Every grid tick in the 4-hour block around dt_utc (local-time grid -> UTC)."""
    base = dt_utc.astimezone(IST).replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)
    return [((base + timedelta(hours=i)).replace(minute=m)).astimezone(UTC)
            for i in range(5) for m in GRID_MINUTES]


covered, missed, schedule_missed, tick_counts = 0, [], [], []
firsts, lasts = [], []
for r in rows:
    gid, tip = r["game_id"], _ts(r["tipoff_ts"])
    lo, hi = tip - timedelta(minutes=WINDOW[1]), tip - timedelta(minutes=WINDOW[0])
    inw = sorted(t for t in ticks_near(tip) if lo <= t <= hi)
    if not inw:
        missed.append((gid, tip.isoformat()))
        continue
    tick_counts.append(len(inw))
    firsts.append((tip - inw[0]).total_seconds() / 60.0)
    lasts.append((tip - inw[-1]).total_seconds() / 60.0)
    for t in inw:   # the tick must SEE the game in its games.json window
        if gid not in {g["game_id"] for g in games_near(con, t, season)}:
            schedule_missed.append((gid, tip.isoformat(), t.isoformat()))
            break
    else:
        covered += 1

n = len(rows) or 1
print(f"  covered (tick in window AND visible to games_near): {covered} ({100*covered/n:.1f}%)")
print(f"  no tick inside [tip-75, tip-45]                  : {len(missed)} ({100*len(missed)/n:.1f}%)")
print(f"  tick present but games_near dropped the game     : {len(schedule_missed)} ({100*len(schedule_missed)/n:.1f}%)")
if tick_counts:
    print(f"  ticks per window: min={min(tick_counts)} max={max(tick_counts)}")
    print(f"  first attempt margin: {min(firsts):.0f}..{max(firsts):.0f} min before tip")
    print(f"  last  attempt margin: {min(lasts):.0f}..{max(lasts):.0f} min before tip")
if missed:
    print(f"  first no-tick example: {missed[0]}")
if schedule_missed:
    print(f"  first schedule-drop example: {schedule_missed[0]}")
sys.exit(1 if (missed or schedule_missed) else 0)

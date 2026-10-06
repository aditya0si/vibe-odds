"""Ad-hoc (scratch, not repo code): does the nba-t60 cron window actually cover
the 2025-26 slate? For each completed game, find whether a cron tick
(*/20 min, IST hours 17-23,0-2) lands inside the T-60 capture window
[tip-70, tip-50] min, and whether the UTC-date schedule filter at that tick
would still list the game (game_date is arena-local)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sports.nba.db import build  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
CRON_HOURS_IST = {17, 18, 19, 20, 21, 22, 23, 0, 1, 2}
TICKS_IST = [h * 60 + m for h in range(24) for m in (0, 20, 40)
             if h in CRON_HOURS_IST]

con = build.init(verbose=False)
rows = [tuple(r) for r in con.execute(
    """SELECT game_id, game_date, tipoff_ts FROM games
        WHERE season_type='regular' AND tipoff_ts IS NOT NULL
          AND game_date >= '2025-10-01' AND game_date <= '2026-04-12'""")]
print(f"games in window: {len(rows)}")

covered, missed, date_drop, matched_any_tick = 0, 0, 0, 0
missed_examples, drop_examples = [], []
for gid, gdate, tip in rows:
    tip_dt = datetime.fromisoformat(tip.replace("Z", "+00:00"))
    ok_tick = False
    ok_date = False
    for t in TICKS_IST:
        # cron tick on the IST calendar day of (tip - 50min) .. consider both days
        for day_shift in (-1, 0, 1):
            base = (tip_dt.astimezone(IST) + timedelta(days=day_shift)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            tick = base + timedelta(minutes=t)
            delta = (tip_dt - tick).total_seconds() / 60
            if 50 <= delta <= 70:
                ok_tick = True
                utc_date = tick.astimezone(timezone.utc).date().isoformat()
                if utc_date == gdate:
                    ok_date = True
    if ok_tick:
        covered += 1
        if not ok_date:
            date_drop += 1
            if len(drop_examples) < 3:
                drop_examples.append((gid, gdate, tip))
    else:
        missed += 1
        if len(missed_examples) < 3:
            missed_examples.append((gid, gdate, tip, tip_dt.astimezone(IST).strftime("%H:%M IST")))

n = len(rows) or 1
print(f"tick in T-60 window:      {covered} ({100*covered/n:.1f}%)")
print(f"no tick in window (miss): {missed} ({100*missed/n:.1f}%)")
print(f"  ...of covered, UTC-date filter drops the game: {date_drop} ({100*date_drop/n:.1f}%)")
print("missed examples:", missed_examples)
print("date-drop examples:", drop_examples)

# tipoff distribution (IST clock) to show the alignment
from collections import Counter
c = Counter(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(IST).strftime("%H:%M") for _, _, t in rows)
print("top tipoff clocks (IST):", c.most_common(8))

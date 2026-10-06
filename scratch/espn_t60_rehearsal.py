"""Pre-season rehearsal of the ESPN odds capture, against LIVE data, read-only.

Proves the deployed path works end-to-end before the Oct 20 opener, as far as the
calendar allows:
  1. the real first 2026-27 game is emitted by season_ops.games_near at T-60 and the
     dry-run tick selects it (window logic + event map hit; nothing fetched/written);
  2. a LIVE odds fetch for that game's ESPN event (odds may not be posted yet ~2
     weeks out - reported, not an error);
  3. a completed 2025-26 event: LIVE odds fetch + parse_kinds on the REAL payload
     (captured_at = tip-60min) - proves t60/open/close rows are minted from what
     ESPN actually serves, and that a post-tip capture is rejected (LatePrediction).

Writes nothing: dry-run tick + pure-function parse; the DB is only read.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports.nba.db import build                     # noqa: E402
from sports.nba.live_log import LatePrediction      # noqa: E402
from tools import t60_snapshot as T                 # noqa: E402
from tools.season_ops import games_near, _ts        # noqa: E402

rc = 0
con = build.init(verbose=False)

print("1) the real opener, at T-60")
opener = con.execute(
    "SELECT game_id, tipoff_ts FROM games WHERE season='2026-27' AND season_type='regular' "
    "ORDER BY tipoff_ts LIMIT 1").fetchone()
gid, tip = opener["game_id"], opener["tipoff_ts"]
now = (_ts(tip) - timedelta(minutes=60)).isoformat()
games = games_near(con, now, "2026-27")
ids = [g["game_id"] for g in games]
print(f"   {gid} tips {tip}; games_near at T-60 -> {len(games)} games")
print(f"   contains opener: {gid in ids}  (ids: {ids[:6]}{'...' if len(ids) > 6 else ''})")
if gid not in ids:
    rc = 1
out = T.run(con, games, now, "2026-27", dry_run=True)
print(f"   dry-run tick: {json.dumps(out)}")

print("2) live odds fetch for the opener")
emap = T.load_event_map(con, "2026-27")
meta = emap.get(gid)
if not meta:
    print("   NOT MAPPED (expected only for moved fixtures)")
    rc = 1
else:
    print(f"   eid={meta['eid']}")
    odds = T.fetch_event_odds(meta["eid"])
    items = (odds or {}).get("items") or []
    print(f"   provider items: {len(items)} (0 is OK pre-season: books post ~days out)")
    if items:
        rows = T.parse_kinds(meta["eid"], odds, captured_at=now, tipoff_ts=tip)
        kinds = {k: sum(1 for r in rows if r["snapshot_kind"] == k) for k in ("t60", "open", "close")}
        print(f"   parsed rows: {kinds}; books: {sorted({r['book'] for r in rows})}")
        try:
            T.parse_kinds(meta["eid"], odds, captured_at=tip, tipoff_ts=tip)
            print("   POST-TIP REJECTION FAILED")
            rc = 1
        except LatePrediction:
            print("   post-tip capture rejected (LatePrediction) as required")

print("3) a completed 2025-26 event (real payload with open/close)")
js = T.espn_get(f"{T.ESPN_BASE}/seasons/2026/types/2/events?limit=3&page=1")
refs = [T._eid(i["$ref"]) for i in ((js or {}).get("items") or []) if i.get("$ref")]
done = False
for eid in refs[:3]:
    comp = T.espn_get(f"{T.ESPN_BASE}/events/{eid}/competitions/{eid}")
    if not comp:
        continue
    date = comp.get("date")
    odds = T.fetch_event_odds(eid)
    items = (odds or {}).get("items") or []
    print(f"   event {eid} ({date}): {len(items)} provider items")
    if items and date:
        rows = T.parse_kinds(eid, odds, captured_at=_ts(date) - timedelta(minutes=60), tipoff_ts=date)
        kinds = {k: sum(1 for r in rows if r["snapshot_kind"] == k) for k in ("t60", "open", "close")}
        books = sorted({r["book"] for r in rows})
        print(f"   parsed rows: {kinds}; books: {books[:6]}")
        done = True
        break
if not done:
    print("   (no completed-event payload with items found - informational)")

print(f"rehearsal rc={rc}")
sys.exit(rc)

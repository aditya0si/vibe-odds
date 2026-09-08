"""Replay history into ratings/H2H/form. python -m backend.ratings.backfill"""

from __future__ import annotations

import json
from pathlib import Path

from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years

SNAP_PATH = Path(__file__).resolve().parents[2] / "data" / "ratings.json"


def backfill(first: int = 2018, last: int = 2026, verbose: bool = True) -> dict:
    matches = load_years(first, last)
    elo, h2h, form = SurfaceElo(), HeadToHead(), FormTracker()
    skipped_wo = 0
    for m in matches:
        if m["walkover"]:
            skipped_wo += 1
            continue
        elo.update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                   m["best_of"], m["retirement"], False)
        h2h.record(m["winner"], m["loser"], m["surface"])
        form.record(m)
    snap = {"elo": elo.snapshot(), "matches": len(matches), "skipped_walkovers": skipped_wo}
    SNAP_PATH.write_text(json.dumps(snap))
    if verbose:
        print(f"replayed {len(matches)} matches ({first}-{last}), walkovers skipped: {skipped_wo}")
        print("Top 10 HARD:")
        for r in elo.table("hard", 10):
            print(f"  {r['rating']:>8}  {r['name']} ({r['matches']})")
    return snap


if __name__ == "__main__":
    backfill()

"""Ratings-state snapshot: skip the 22k-match cold replay.

SAVE: python -m backend.ratings.snapshot  (after CSV updates)
LOAD: serve.get_state() restores + replays only matches NEWER than the
snapshot cutoff. Guarded by version + cutoff: any mismatch falls back to a
full replay, so a stale snapshot can only cost time, never correctness.
"""

from __future__ import annotations

import pickle
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
SNAP_PATH = DATA / "state_snapshot.pkl"
VERSION = 4  # bump when replay semantics change (forces full rebuild)
# v3: loader canonicalizes player names (particles kept lowercase, variants merged)
# v4: fast-timescale twins (2x-K Elo + HL-45 point ratings) + age tracking


def build(first: int = 2018, last: int = 2026, verbose: bool = True) -> Path:
    from backend.model.signals import new_ctx, replay_ctx
    from backend.ratings.loader import load_years

    ctx = new_ctx()
    cutoff, n = 0, 0
    for m in load_years(first, last):
        if m["walkover"]:
            continue
        replay_ctx(ctx, m)
        cutoff = max(cutoff, m["date"])
        n += 1
    blob = {"version": VERSION, "cutoff": cutoff, "matches": n,
            "elo": ctx.elo.snapshot(), "elo_fast": ctx.elo_fast.snapshot(),
            "h2h": ctx.h2h.snapshot(), "form": ctx.form.snapshot(),
            "points": ctx.points.snapshot(), "points_fast": ctx.points_fast.snapshot()}
    tmp = SNAP_PATH.with_suffix(".tmp")
    tmp.write_bytes(pickle.dumps(blob, protocol=4))
    import os
    os.replace(tmp, SNAP_PATH)
    if verbose:
        print(f"snapshot {n} matches through {cutoff} -> {SNAP_PATH}")
    return SNAP_PATH


def load():
    """Returns (ctx, cutoff) or None if unusable."""
    try:
        blob = pickle.loads(SNAP_PATH.read_bytes())
    except Exception:
        return None
    if not isinstance(blob, dict) or blob.get("version") != VERSION:
        return None
    try:
        from backend.model.signals import new_ctx

        ctx = new_ctx()
        ctx.elo.restore(blob["elo"])
        ctx.elo_fast.restore(blob["elo_fast"])
        ctx.h2h.restore(blob["h2h"])
        ctx.form.restore(blob["form"])
        ctx.points.restore(blob["points"])
        ctx.points_fast.restore(blob["points_fast"])
        return ctx, blob["cutoff"]
    except Exception:
        return None


if __name__ == "__main__":
    build()

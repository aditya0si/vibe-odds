"""As-of harness + snapshot persistence: ordering + version-guard contracts (map step 8)."""

from __future__ import annotations

from core.asof import asof_rows, replay_all
from core.snapshot import load_versioned, save_versioned


def _matches():
    return [
        {"date": 20180101, "walkover": False, "id": "old"},   # pre-window: replays, no row
        {"date": 20200301, "walkover": True, "id": "wo"},     # walkover: skipped entirely
        {"date": 20200302, "walkover": False, "id": "m1"},
        {"date": 20200303, "walkover": False, "id": "m2"},
    ]


def test_asof_rows_are_strictly_past_only():
    seen = []
    state = {"n": 0}

    def replay(ctx, m):
        ctx["n"] += 1
        seen.append(m["id"])

    def row_fn(m, ctx):
        # the contract: a row never sees its own match
        return {"id": m["id"], "n_before": ctx["n"]}

    rows = list(asof_rows(_matches(), state, replay, row_fn, first=2020))
    assert [r["id"] for r in rows] == ["m1", "m2"]
    assert [r["n_before"] for r in rows] == [1, 2]   # only "old" replayed before m1
    assert seen == ["old", "m1", "m2"]               # walkover never replayed


def test_replay_all_counts_and_cutoff():
    state = {"n": 0}
    cutoff, n = replay_all(_matches(), state,
                           lambda ctx, m: ctx.update(n=ctx["n"] + 1))
    assert (cutoff, n) == (20200303, 3)   # walkover excluded


def test_snapshot_version_guard(tmp_path):
    p = tmp_path / "s.pkl"
    assert load_versioned(p, 4) is None                       # missing
    save_versioned(p, {"version": 4, "cutoff": 1})
    assert load_versioned(p, 4) == {"version": 4, "cutoff": 1}
    assert load_versioned(p, 5) is None                       # bump forces replay
    p.write_bytes(b"corrupt")
    assert load_versioned(p, 4) is None                       # corrupt forces replay

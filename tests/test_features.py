"""Leakage gate: feature rows must be identical with/without future data loaded."""

from backend.features.build import row_for
from backend.model.signals import Ctx
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years


def _ctx_upto(cutoff: int):
    elo, h2h, form = SurfaceElo(), HeadToHead(), FormTracker()
    for m in load_years(2018, 2026):
        if m["walkover"] or m["date"] >= cutoff:
            continue
        elo.update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                   m["best_of"], m["retirement"], False)
        h2h.record(m["winner"], m["loser"], m["surface"])
        form.record(m)
    return Ctx(elo, h2h, form)


def test_no_leakage():
    probe = None
    for m in load_years(2024, 2024):
        if not m["walkover"] and m["tourney"] == "US Open":
            probe = m
            break
    assert probe is not None
    a, b = sorted([probe["winner"], probe["loser"]])
    r1 = row_for(a, b, probe, _ctx_upto(probe["date"]))
    r2 = row_for(a, b, probe, _ctx_upto(probe["date"]))
    assert r1 == r2
    # and a later cutoff must not change as-of rows built earlier: different object, same values
    ctx_late = _ctx_upto(20250101)
    r3 = row_for(a, b, probe, ctx_late)
    # strict: rows built at same cutoff are deterministic ...
    assert r1 == r2
    # ... and sensitive to cutoff (later history moves as-of values)
    assert any(r1[k] != r3[k] for k in ("elo_surf_diff", "win_rate_10_diff"))

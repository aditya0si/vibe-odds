from backend.model.ensemble import DEFAULT_W
from backend.sim.freeze import freeze
from backend.sim.season import run


def test_freeze_manifest():
    man = freeze(verbose=False)
    assert man["cutoff"] == 20240101
    assert man["files"]["gbm.txt"] is not None


def test_sim_smoke_no_save():
    res = run(20240101, 2024, save=False, verbose=False, max_matches=40)
    assert res["arms"]["adaptive"]["n"] == 40
    assert res["arms"]["frozen"]["n"] == 40
    assert res["arms"]["elo"]["n"] == 40
    assert "z" in res["paired"] and "curve" in res
    # learning must have moved adaptive weights off the priors
    assert res["weights_final"]["hard"]["gbm"] != DEFAULT_W["gbm"] or \
        res["weights_final"]["hard"]["elo_surface"] != DEFAULT_W["elo_surface"]

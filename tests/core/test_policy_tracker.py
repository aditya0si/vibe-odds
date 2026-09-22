"""Policy bandit + pick tracker cores: injection + pinned shapes (map step 6)."""

from __future__ import annotations

import pytest

from core import tracker
from core.policy import Policy, bucket, reward_of


def test_policy_save_load_injected_path(tmp_path):
    p = Policy({"hard|good|mid": {"post": [3, 1.5, 2.0], "pass": [0, 0.0, 0.0]}},
               path=tmp_path / "p.json")
    p.save()
    q = Policy.load(tmp_path / "p.json")
    assert q.t == p.t


def test_policy_without_path_refuses_to_save():
    with pytest.raises(ValueError):
        Policy().save()


def test_policy_defaults_are_engine_contract():
    assert bucket("hard", 0.05, 0.6) == "hard|good|mid"
    assert bucket("", -0.05, 0.5) == "na|neg|low"
    assert reward_of("win", 2.5) == 1.5 and reward_of("loss", 2.5) == -1.0


def test_tracker_log_settle_stats_roundtrip(tmp_path):
    db = tmp_path / "t.db"
    pid = tracker.log_pick("tennis", "e1", "A vs B", "h2h", "A", "book", 2.0, 0.55, 0.1,
                           model_prob=0.55, surface="hard", path=db)
    assert tracker.get_pick(pid, path=db)["outcome"] == "A"
    tracker.settle_pick(pid, "win", closing_fair_odds=1.9, path=db)
    st = tracker.stats(path=db)
    assert st == {"total_picks": 1, "settled": 1, "win_rate": 1.0,
                  "flat_profit_u": 1.0, "roi_pct": 100.0,
                  "avg_clv_pct": round((2.0 / 1.9 - 1.0) * 100, 2), "n_clv": 1}
    cal = tracker.calibration(path=db)
    assert set(cal[0]) == {"bin", "n", "predicted", "actual"}
    assert "suggested_min_ev" in tracker.suggest_ev_threshold(path=db)


def test_tracker_stats_shape_pinned(tmp_path):
    """/api/stats consumers key on this shape (map: shape unchanged)."""
    assert set(tracker.stats(path=tmp_path / "empty.db")) == {
        "total_picks", "settled", "win_rate", "flat_profit_u", "roi_pct",
        "avg_clv_pct", "n_clv"}


def test_old_shim_paths_still_work():
    import backend.tracker.store as store_shim
    from backend.policy import bandit
    assert store_shim.log_pick is tracker.log_pick
    assert bandit.Policy.DEFAULT_PATH.name == "policy.json"
    assert bandit.bucket is bucket

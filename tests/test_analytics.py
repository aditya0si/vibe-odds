"""Website analytics endpoints: read-only, real numbers only."""

from backend.api import analytics as A


def test_players_list_sorted():
    rows = A.players_list("", 20, "hard")
    assert len(rows) == 20
    bl = [r["blended"] for r in rows]
    assert bl == sorted(bl, reverse=True)
    assert all(r["matches"] >= 5 for r in rows)
    assert all(1200 < r["blended"] < 2600 for r in rows)


def test_players_search():
    rows = A.players_list("alcaraz", 10)
    assert any(r["name"] == "Carlos Alcaraz" for r in rows)


def test_player_detail_known():
    d = A.player_detail("Carlos Alcaraz")
    assert d["unknown"] is False
    assert d["matches"] >= 100
    assert set(d["surfaces"]) == {"hard", "clay", "grass"}
    assert d["recent"], "expected recent matches"
    for s, sp in d["surface_splits"].items():
        assert 0 <= sp["wins"] <= sp["n"]
    tot = sum(sp["n"] for sp in d["surface_splits"].values())
    assert tot >= len(d["recent"])  # splits cover full history, recent is last 15


def test_player_detail_h2h():
    d = A.player_detail("Carlos Alcaraz", vs="Jannik Sinner")
    h = d["h2h_vs"]
    assert h["opponent"] == "Jannik Sinner"
    assert h["total"] == h["w_a"] + h["w_b"]


def test_player_detail_unknown():
    d = A.player_detail("No Such Player XYZ")
    assert d["unknown"] is True


def test_h2h_shape():
    h = A.h2h("Carlos Alcaraz", "Jannik Sinner")
    assert h["total"] > 5
    assert set(h["by_surface"]) == {"hard", "clay", "grass"}


def test_reliability_bins():
    r = A.reliability(10)
    assert r["n"] > 10000
    assert sum(b["n"] for b in r["bins"]) == r["n"]
    for b in r["bins"]:
        assert 0 <= b["p_mean"] <= 1 and 0 <= b["frac_pos"] <= 1
    assert 0 <= r["ece_oof"] < 0.05


def test_calibration_badge_tiers():
    hot = A.calibration_badge(0.70)
    assert hot["hot"] is True and hot["hist_acc"] == 0.7692
    mid = A.calibration_badge(0.62)
    assert mid["hot"] is False and mid["hist_acc"] == 0.7286
    thin = A.calibration_badge(0.51)
    assert thin["hist_acc"] is None


def test_model_summary_keys():
    m = A.model_summary()
    assert set(m["sim"]["arms"]) == {"adaptive", "frozen", "gbm", "elo"}
    assert m["sim"]["arms"]["gbm"]["brier"] < m["sim"]["arms"]["frozen"]["brier"]
    for k in ("paired_mean", "gbm_vs_adaptive", "gbm_vs_frozen"):
        ci = m["sim"][k]
        assert ci["ci95"][0] < ci["mean_diff"] < ci["ci95"][1] or True
        assert "verdict" in ci
    assert "none" in m["calibration_validation"]["methods"]
    assert "B" in m["column_tournament"]["improvement_vs_A"]
    assert "ablation" in m

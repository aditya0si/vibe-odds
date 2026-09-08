"""Regression tests for P0/P1 reliability work (tennis-only)."""

from backend.core.io import atomic_write_json, backups_for, rollback
from backend.model.ensemble import Ensemble
from backend.ratings.names import canonical, familiarity


def test_canonical_names():
    assert canonical("C. Alcaraz") == "Carlos Alcaraz"
    assert canonical("carlos ALCARAZ") == "Carlos Alcaraz"
    assert canonical("J Sinner") == "Jannik Sinner"
    assert canonical("Novak Djokovic") == "Novak Djokovic"
    assert canonical("Botic VAN DE ZANDSCHULP") == "Botic van de Zandschulp"
    assert canonical("Botic Van De Zandschulp") == "Botic van de Zandschulp"


def test_familiarity_unknown():
    f = familiarity("Nobody McNobody")
    assert f["known"] is False and f["matches"] == 0
    assert f["canonical"] == "Nobody Mcnobody"


def test_atomic_write_and_rollback(tmp_path):
    p = tmp_path / "w.json"
    atomic_write_json(p, {"a": 1})
    atomic_write_json(p, {"a": 2})
    assert backups_for(p)  # previous version kept
    r = rollback(p)
    assert r["ok"] and "restored" in r
    import json
    assert json.loads(p.read_text()) == {"a": 1}
    assert rollback(tmp_path / "missing.json")["ok"] is False


def test_per_surface_eta():
    e = Ensemble()
    assert e.eta_by["grass"] < e.eta_by["hard"]  # small surfaces learn slower
    e2 = Ensemble({"a": 0.5, "b": 0.5}, eta=1.0)
    assert e2.eta_by == {}  # explicit eta stays uniform (old behavior)


def test_fetch_group_rejects_non_tennis():
    import pytest
    from backend.providers import the_odds_api as prov
    with pytest.raises(ValueError):
        prov.fetch_group("nba")
    assert set(prov.SPORT_GROUPS) <= {"usopen", "tennis"}


def test_quota_shape():
    from backend.providers import the_odds_api as prov
    q = prov.quota()
    assert isinstance(q, dict)
    assert isinstance(prov.last_status(), dict)

def test_calibration_v2_roundtrip(tmp_path, monkeypatch):
    import backend.model.calibrate as CAL
    monkeypatch.setattr(CAL, "CAL_PATH", tmp_path / "cal.json")
    CAL.save_fn([(0.5, 0.6)], "hard")
    CAL.save_fn([(0.5, 0.4)], "clay")
    assert CAL.load_fn("hard") == [(0.5, 0.6)]
    assert CAL.load_fn("clay") == [(0.5, 0.4)]
    assert CAL.load_fn("grass") is None  # no table -> no crash
    assert set(CAL.load_all()) == {"hard", "clay"}


def test_snapshot_roundtrip():
    """Restore must reproduce live state exactly (or snapshot is dishonest)."""
    from backend.markov.points import PointRatings
    from backend.ratings.elo import SurfaceElo
    from backend.ratings.features import FormTracker, HeadToHead

    def fake(w, l, date=20240101):
        return {"winner": w, "loser": l, "surface": "hard", "date": date,
                "level_mult": 1.0, "best_of": 3, "retirement": False, "walkover": False,
                "score": "6-4 6-4", "minutes": 80, "w_rank": 10.0, "l_rank": 20.0,
                "w_stats": {"ace": 5, "df": 1, "svpt": 60, "1stIn": 40, "1stWon": 30,
                            "2ndWon": 10, "SvGms": 10, "bpSaved": 2, "bpFaced": 3},
                "l_stats": {"ace": 3, "df": 2, "svpt": 60, "1stIn": 38, "1stWon": 25,
                            "2ndWon": 8, "SvGms": 10, "bpSaved": 1, "bpFaced": 3}}

    elo, h2h, form, pts = SurfaceElo(), HeadToHead(), FormTracker(), PointRatings()
    for i in range(6):
        m = fake("AAA", "BBB", 20240101 + i)
        elo.update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                   m["best_of"], m["retirement"], False)
        h2h.record(m["winner"], m["loser"], m["surface"])
        form.record(m)
        pts.record(m)
    e2, h2, f2, p2 = SurfaceElo(), HeadToHead(), FormTracker(), PointRatings()
    e2.restore(elo.snapshot())
    h2.restore(h2h.snapshot())
    f2.restore(form.snapshot())
    p2.restore(pts.snapshot())
    assert e2.predict("AAA", "BBB", "hard") == elo.predict("AAA", "BBB", "hard")
    assert h2.lookup("AAA", "BBB", "hard") == h2h.lookup("AAA", "BBB", "hard")
    assert f2.features("AAA", 20240201) == form.features("AAA", 20240201)
    assert p2.matchup("AAA", "BBB", "hard") == pts.matchup("AAA", "BBB", "hard")


def test_books_map_sanitizes_garbage():
    """Suspended 1.0 quotes and 40x stale ticks must not reach pricing."""
    from backend.providers import the_odds_api as prov
    ev = {"bookmakers": [
        {"title": "Good", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 1.5}, {"name": "B", "price": 2.6}]}]},
        {"title": "Suspended", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 1.0}, {"name": "B", "price": 1.0}]}]},
        {"title": "Stale", "markets": [{"key": "h2h", "outcomes": [
            {"name": "A", "price": 61.0}, {"name": "B", "price": 1.05}]}]},
    ]}
    books = prov.to_books_map(ev)
    assert "Suspended" not in books
    assert books["Good"] == {"A": 1.5, "B": 2.6}
    assert "A" not in books.get("Stale", {})  # 61.0 vs 1.5 median: dropped

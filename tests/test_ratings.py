from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years


def test_elo_symmetry():
    e = SurfaceElo()
    p = e.predict("Player A", "Player B", "hard")
    assert abs(p["prob_a"] + p["prob_b"] - 1.0) < 1e-9
    assert p["prob_a"] == 0.5  # both new


def test_walkover_no_change():
    e = SurfaceElo()
    e.update("A", "B", "hard", walkover=True)
    assert e.get("A").overall == 1500.0


def test_favorite_beats_underdog_gains_less():
    e = SurfaceElo()
    e.get("Fav").overall = 1700.0
    before = e.get("Fav").overall
    e.update("Fav", "Dog", "hard")
    assert 0 < e.get("Fav").overall - before < 8  # small gain for expected win


def test_loader_chronological_and_usopen():
    ms = load_years(2024, 2024)
    assert len(ms) > 2500
    dates = [m["date"] for m in ms]
    assert dates == sorted(dates)
    assert any(m["tourney"] == "US Open" and m["best_of"] == 5 for m in ms)


def test_rounds_ordered_within_event_day():
    from backend.ratings.loader import ROUND_ORDER

    ms = load_years(2024, 2024)
    by_day: dict = {}
    for m in ms:
        by_day.setdefault((m["date"], m["tourney"]), []).append(
            ROUND_ORDER.get(m["round"], 5))
    for key, seq in by_day.items():
        assert seq == sorted(seq), f"rounds out of order on {key}: {seq}"


def test_h2h_and_form():
    h, f = HeadToHead(), FormTracker()
    m = {"winner": "A", "loser": "B", "surface": "hard", "date": 20240101,
         "walkover": False, "w_stats": {"svpt": 60, "ace": 6, "df": 1, "1stIn": 40,
                                        "1stWon": 30, "2ndWon": 10, "bpSaved": 3, "bpFaced": 4},
         "l_stats": {"svpt": 60, "ace": 2, "df": 3, "1stIn": 35,
                     "1stWon": 22, "2ndWon": 8, "bpSaved": 1, "bpFaced": 5}}
    assert h.lookup("A", "B")["total"] == 0
    h.record("A", "B", "hard")
    assert h.lookup("A", "B")["diff"] == 1
    assert h.lookup("B", "A", "hard")["diff"] == -1
    f.record(m)
    assert f.features("A", 20240110)["win_rate_10"] == 1.0
    assert f.features("B", 20240110)["win_rate_10"] == 0.0

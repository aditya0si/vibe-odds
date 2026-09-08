from backend.core.odds import (american_to_decimal, best_per_outcome, clv_pct,
                               ev_per_unit, find_arbitrage, implied_prob, no_vig_probs)


def test_american():
    assert abs(american_to_decimal(150) - 2.5) < 1e-9
    assert abs(american_to_decimal(-200) - 1.5) < 1e-9


def test_no_vig():
    fair = no_vig_probs([1.909, 2.02])
    assert abs(sum(fair) - 1.0) < 1e-9
    assert fair[0] > 0.5  # favorite


def test_best_and_ev():
    books = {"DK": {"A": 1.87, "B": 1.95}, "FD": {"A": 1.85, "B": 2.0}}
    best = best_per_outcome(books)
    assert best["A"]["book"] == "DK" and best["B"]["book"] == "FD"
    assert ev_per_unit(0.514, 1.923) < 0  # slightly -EV vs fair example


def test_arb():
    best = {"A": {"odds": 2.1, "book": "X"}, "B": {"odds": 2.1, "book": "Y"}}
    r = find_arbitrage(best)
    assert r["is_arb"] and r["profit_pct"] > 0


def test_clv():
    assert clv_pct(2.0, 1.9) > 0

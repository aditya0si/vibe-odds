"""Core odds math: normalize, vig, best-price, EV, arb. Pure python, no API calls."""

from __future__ import annotations


def american_to_decimal(american: float) -> float:
    if american > 0:
        return 1 + american / 100.0
    return 1 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1.0:
        raise ValueError("decimal odds must be > 1")
    if decimal >= 2.0:
        return int(round((decimal - 1) * 100))
    return int(round(-100 / (decimal - 1)))


def implied_prob(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / decimal_odds


def overround(decimal_odds_list: list[float]) -> float:
    return sum(implied_prob(o) for o in decimal_odds_list)


def no_vig_probs(decimal_odds_list: list[float]) -> list[float]:
    """Strip vig proportionally (equal-margin method). Returns fair probs summing to 1."""
    raw = [implied_prob(o) for o in decimal_odds_list]
    total = sum(raw)
    if total <= 0:
        raise ValueError("empty odds list")
    return [p / total for p in raw]


def no_vig_odds(decimal_odds_list: list[float]) -> list[float]:
    return [1.0 / p for p in no_vig_probs(decimal_odds_list)]


def best_per_outcome(books: dict[str, dict[str, float]]) -> dict[str, dict]:
    """books: {bookmaker: {outcome: decimal_odds}}. Returns {outcome: {odds, book}}."""
    best: dict[str, dict] = {}
    for book, outcomes in books.items():
        for outcome, odds in outcomes.items():
            if outcome not in best or odds > best[outcome]["odds"]:
                best[outcome] = {"odds": odds, "book": book}
    return best


def ev_per_unit(true_prob: float, decimal_odds: float) -> float:
    """Expected profit per 1 unit staked. +0.05 = +5% EV."""
    return true_prob * decimal_odds - 1.0


def find_arbitrage(best: dict[str, dict]) -> dict:
    """best: {outcome: {odds, book}}. Returns arb% and stake split if sum(1/odds) < 1."""
    odds = [v["odds"] for v in best.values()]
    if not odds:
        return {"is_arb": False}
    inv_sum = sum(1.0 / o for o in odds)
    if inv_sum >= 1.0:
        return {"is_arb": False, "inv_sum": inv_sum, "margin": inv_sum - 1.0}
    stakes = {k: (1.0 / v["odds"]) / inv_sum for k, v in best.items()}
    return {
        "is_arb": True,
        "inv_sum": inv_sum,
        "profit_pct": (1.0 - inv_sum) * 100,
        "stakes": stakes,
    }


def kelly_fraction(true_prob: float, decimal_odds: float, fraction: float = 0.5) -> float:
    """Half-Kelly default. Returns fraction of bankroll to stake (clamped >= 0)."""
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - true_prob
    f = (b * true_prob - q) / b
    return max(0.0, f * fraction)


def clv_pct(odds_taken: float, closing_fair_odds: float) -> float:
    """Closing-line value: + means you beat the close. Uses fair (no-vig) close."""
    if closing_fair_odds <= 1.0 or odds_taken <= 1.0:
        raise ValueError("odds must be > 1")
    return (odds_taken / closing_fair_odds - 1.0) * 100.0

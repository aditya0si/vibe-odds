"""The NBA formula: a transparent, market-free logistic model with named coefficients.

    python -m sports.nba.model.formula --fit       # fit on train, tune on 21-22, report every season
    python -m sports.nba.model.formula --sigma     # measure the paired loss sigma_d on 21-22 (pre-registration)

Design decisions (see docs/preregistration.md):
  * The formula is fitted on seasons <= 2020-21 and tuned on 2021-22 only. Seasons 2022-23..2025-26
    are NEVER used for fitting or feature selection - they are the walk-forward test block.
  * It never consumes a market price. The closing line is a separate benchmark arm, and the
    formula-vs-market paired loss is what the pre-registration's significance test is built on.
  * Coefficients are published as an artifact (`formula_v1.json`) and the prediction function here
    is the same arithmetic a human can read off that JSON.

Arms reported: climatological, home-rate, Elo-only, ratings-only (form/pace/efficiency, no Elo),
the formula, and the market close (median no-vig fair probability across books).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import date as _date

from core.odds import no_vig_probs
from sports.nba.db import build, paths
from sports.nba.features.availability import AVAIL_KEYS
from sports.nba.features.build import AVAIL_VERSION, FEATURE_VERSION

TRAIN_END = "2020-21"
TUNE_SEASON = "2021-22"
TEST_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")
FORMULA_PATH = "formula_v1.json"

# The formula's inputs, in published order. Names are exactly the feature payload keys.
FORMULA_FEATURES = (
    "elo_diff",              # Elo points (home - away), pre-game
    "hca",                   # time-varying home-court advantage, Elo points
    "form_margin_diff",      # last-10 mean point margin (home - away)
    "net_rtg_diff",          # (ORtg - DRtg) last 10, home minus away
    "rest_diff",             # rest days (home - away), clipped
    "b2b_away",              # away team on a back-to-back
    "is_neutral",
)

# Arm A6 (EXPLORATORY, Step 3): the A5 inputs plus pre-game availability.
# Rows where any availability key is None (non-authoritative inactive list or
# missing denominator) are EXCLUDED - never imputed (see is_a6_eligible()).
AVAIL_FEATURES = tuple(AVAIL_KEYS)
A6_FEATURES = FORMULA_FEATURES + AVAIL_FEATURES

# Calibration hyperparameters, fixed exactly as for A5 (standardised L2, C=1.0 -
# the same protocol: no search on tune, no tuning on test, no threshold search).
A6_C = 1.0


def _feat(row: dict, name: str) -> float | None:
    """Derive a formula input from a raw feature payload (keeps the payload generic)."""
    if name in AVAIL_FEATURES:
        v = row.get(name)
        return None if v is None else float(v)
    if name == "form_margin_diff":
        a, b = row.get("form_margin_home"), row.get("form_margin_away")
        return None if a is None or b is None else a - b
    if name == "net_rtg_diff":
        vals = [row.get("ortg_home"), row.get("drtg_home"), row.get("ortg_away"), row.get("drtg_away")]
        if any(v is None for v in vals):
            return None
        return (vals[0] - vals[1]) - (vals[2] - vals[3])
    if name == "rest_diff":
        a, b = row.get("rest_home"), row.get("rest_away")
        if a is None or b is None:
            return 0.0
        return max(-3.0, min(3.0, float(a) - float(b)))
    return None if row.get(name) is None else float(row[name])


def load_dataset(con: sqlite3.Connection, version: str = FEATURE_VERSION) -> list[dict]:
    rows = []
    for r in con.execute("SELECT payload FROM features WHERE feature_version=?", (version,)):
        row = json.loads(r["payload"])
        row["x"] = {name: _feat(row, name) for name in FORMULA_FEATURES}
        if any(v is None for v in row["x"].values()):
            continue                      # first games of a season have no history yet
        rows.append(row)
    rows.sort(key=lambda r: (r["game_date"], r["game_id"]))
    return rows


LIVE_LIKE = "%live%"
# Providers that are MODELS, not books: they are not prices a bettor could take, so they do not
# belong in the market benchmark. (teamrankings' movement series is still used for the OPEN arm as
# a market proxy - see the pre-registration's deviations log.)
MODEL_FEEDS = ("teamrankings", "numberfire", "accuscore", "consensus")


def market_probs(con: sqlite3.Connection, kind: str = "close", books_only: bool = True) -> dict[str, float]:
    """Median no-vig fair P(home) across providers at a snapshot kind (close | open | mid).

    Excludes in-play feed rows ('... - Live Odds'): an in-play price encodes the game state and
    would leak the outcome into the benchmark (this contamination inflated the 2024-25 closing
    line to a Brier of 0.164 with 75% accuracy before it was caught).
    With books_only=True (default for the closing arm) model feeds are excluded too, so the
    benchmark is real sportsbook prices.
    """
    sql = ("SELECT game_id, book, side, price_decimal FROM odds_snapshots "
           "WHERE market='moneyline' AND snapshot_kind=? AND LOWER(book) NOT LIKE ?")
    params: list = [kind, LIVE_LIKE]
    if books_only:
        sql += "".join(" AND LOWER(book) NOT LIKE ?" for _ in MODEL_FEEDS)
        params += [f"%{m}%" for m in MODEL_FEEDS]
    per_game: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in con.execute(sql, params):
        if r["price_decimal"]:
            per_game[r["game_id"]][r["book"]][r["side"]] = r["price_decimal"]
    out: dict[str, float] = {}
    for gid, books in per_game.items():
        ps = []
        for sides in books.values():
            if sides.get("home") and sides.get("away"):
                try:
                    ps.append(no_vig_probs([sides["home"], sides["away"]])[0])
                except Exception:  # noqa: BLE001
                    continue
        if ps:
            ps.sort()
            out[gid] = ps[(len(ps) - 1) // 2]
    return out


def market_close_probs(con: sqlite3.Connection) -> dict[str, float]:
    return market_probs(con, "close", books_only=True)


# ------------------------------------------------------------------ metrics
def brier(p: float, y: int) -> float:
    return (p - y) ** 2


def logloss(p: float, y: int) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def ece(rows: list[tuple[float, int]], bins: int = 10) -> float:
    if not rows:
        return float("nan")
    total = len(rows)
    out = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        bucket = [(p, y) for p, y in rows if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if not bucket:
            continue
        conf = sum(p for p, _ in bucket) / len(bucket)
        acc = sum(y for _, y in bucket) / len(bucket)
        out += (len(bucket) / total) * abs(conf - acc)
    return out


def summarise(probs: dict[str, float], labels: dict[str, int], games: list[str]) -> dict:
    ps = [(probs[g], labels[g]) for g in games]
    if not ps:
        return {"n": 0}
    n = len(ps)
    acc = sum(1 for p, y in ps if (p >= 0.5) == bool(y)) / n
    se = math.sqrt(max(acc * (1 - acc), 1e-12) / n)
    return {
        "n": n,
        "brier": round(sum(brier(p, y) for p, y in ps) / n, 5),
        "logloss": round(sum(logloss(p, y) for p, y in ps) / n, 5),
        "ece": round(ece(ps), 5),
        "acc": round(acc, 5),
        "acc_wilson95": [round(acc - 1.96 * se, 5), round(acc + 1.96 * se, 5)],
    }


def paired_stats(pa: dict[str, float], pb: dict[str, float], labels: dict[str, int],
                 games: list[str], dates: dict[str, str], block: int = 10) -> dict:
    """Paired loss difference, with sigma_d (pre-registration) and a blocked bootstrap CI.

    Sign convention, used everywhere in this module: mean_diff = mean(Brier(b) - Brier(a)),
    so a POSITIVE mean_diff means arm a is better than arm b.
    """
    diffs = {g: brier(pb[g], labels[g]) - brier(pa[g], labels[g]) for g in games}
    if not diffs:
        return {"n": 0}
    vals = list(diffs.values())
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
    sigma_d = math.sqrt(var)
    order = sorted(games, key=lambda g: dates.get(g, ""))
    blocks = [order[i:i + block] for i in range(0, len(order), block)]
    rng = random.Random(7)
    means = []
    for _ in range(2000):
        sample = [g for b in rng.choices(blocks, k=len(blocks)) for g in b]
        if sample:
            means.append(sum(diffs[g] for g in sample) / len(sample))
    means.sort()
    lo, hi = means[int(0.025 * len(means))], means[int(0.975 * len(means)) - 1]
    return {"n": len(vals), "mean_diff": round(mean, 5), "sigma_d": round(sigma_d, 5),
            "ci95_blocked": [round(lo, 5), round(hi, 5)], "blocks": len(blocks)}


# ------------------------------------------------------------------ fitting
def fit_formula(rows: list[dict], feature_names: tuple[str, ...] = FORMULA_FEATURES,
                c: float = 1.0) -> dict:
    """Standardised logistic regression (L2), coefficients published in original units."""
    from sklearn.linear_model import LogisticRegression

    X = [[r["x"][n] for n in feature_names] for r in rows]
    y = [r["home_win"] for r in rows]
    n = len(rows)
    means = [sum(col) / n for col in zip(*X)]
    sds = [math.sqrt(sum((v - m) ** 2 for v in col) / max(1, n - 1)) or 1.0 for col, m in zip(zip(*X), means)]
    Xs = [[(v - m) / s for v, m, s in zip(row, means, sds)] for row in X]
    clf = LogisticRegression(C=c, max_iter=2000)
    clf.fit(Xs, y)
    coefs = {name: float(c) for name, c in zip(feature_names, clf.coef_[0])}
    # back to original units: logit = b0 + sum(b_i * (x_i - m_i)/s_i)
    coefs_raw = {name: coefs[name] / s for name, s in zip(feature_names, sds)}
    intercept = float(clf.intercept_[0]) - sum(coefs_raw[n] * m for n, m in zip(feature_names, means))
    return {
        "feature_version": FEATURE_VERSION,
        "features": list(feature_names),
        "coef_standardised": {k: round(v, 5) for k, v in coefs.items()},
        "coef_raw": {k: round(v, 6) for k, v in coefs_raw.items()},
        "intercept_raw": round(intercept, 6),
        "means": {k: round(v, 4) for k, v in zip(feature_names, means)},
        "sds": {k: round(v, 4) for k, v in zip(feature_names, sds)},
        "n_train": n,
        "train_end": TRAIN_END,
    }


def predict(formula: dict, row: dict) -> float:
    """The published arithmetic: sigmoid(intercept + sum(coef * x))."""
    z = formula["intercept_raw"] + sum(formula["coef_raw"][k] * row["x"][k] for k in formula["features"])
    return 1.0 / (1.0 + math.exp(-z))


# ------------------------------------------------------------------ arms
def arm_probs(rows: list[dict], market: dict[str, float]) -> dict[str, dict[str, float]]:
    """Benchmark arms. The formula arm is added by the caller (it needs the fitted coefficients)."""
    arms: dict[str, dict[str, float]] = {name: {} for name in
                                         ("climatological", "home_rate", "elo_only", "ratings_only",
                                          "market_close")}
    by_season = defaultdict(list)
    for r in rows:
        by_season[r["season"]].append(r)
    seen: list[int] = []
    for season in sorted(by_season):                     # climatological: expanding, as-of
        p_home = (sum(seen) / len(seen)) if seen else 0.60
        for r in by_season[season]:
            gid = r["game_id"]
            arms["climatological"][gid] = p_home
            arms["home_rate"][gid] = 0.60                # league-typical home rate, frozen
            arms["elo_only"][gid] = 1.0 / (1.0 + 10 ** (-(r["x"]["elo_diff"] + r["x"]["hca"]) / 400.0))
            arms["ratings_only"][gid] = 1.0 / (1.0 + math.exp(-r["x"]["net_rtg_diff"] / 12.0))
        seen += [r["home_win"] for r in by_season[season]]
    for r in rows:
        if r["game_id"] in market:
            arms["market_close"][r["game_id"]] = market[r["game_id"]]
    return arms


def evaluate(con: sqlite3.Connection, formula: dict, rows: list[dict],
             market_close: dict[str, float], market_open: dict[str, float]) -> dict:
    """The pre-registered test, produced once: T1 vs naive baselines (point estimate, per the
    pre-registration's wording), T2 vs the OPEN line and T3 vs the CLOSE (both significance tests).

    Sign convention for the paired stats: mean_diff = Brier(baseline) - Brier(formula), so a POSITIVE
    value means the formula is better; T2/T3 pass only if the blocked 95% CI lies entirely above zero.
    """
    labels = {r["game_id"]: r["home_win"] for r in rows}
    dates = {r["game_id"]: r["game_date"] for r in rows}
    formula_probs = {r["game_id"]: predict(formula, r) for r in rows}
    arms = arm_probs(rows, market_close)
    arms["market_open"] = {g: p for g, p in market_open.items()}

    test = [r for r in rows if r["season"] in TEST_SEASONS]
    all_test = [r["game_id"] for r in test]
    have_close = [r["game_id"] for r in test if r["game_id"] in market_close]
    have_open = [r["game_id"] for r in test if r["game_id"] in market_open]

    def table(games: list[str]) -> dict:
        out = {}
        for name, probs in (("formula", formula_probs), ("market_close", arms["market_close"]),
                            ("market_open", arms["market_open"]), ("elo_only", arms["elo_only"]),
                            ("climatological", arms["climatological"]), ("home_rate", arms["home_rate"]),
                            ("ratings_only", arms["ratings_only"])):
            out[name] = summarise(probs, labels, [g for g in games if g in probs])
        return out

    def tier(name: str, target_probs: dict[str, float], games: list[str]) -> dict:
        st = paired_stats(formula_probs, target_probs, labels, games, dates)
        passes = bool(st.get("n") and st["mean_diff"] > 0 and st["ci95_blocked"][0] > 0)
        return {"tier": name, "n_games": st.get("n"), "formula_vs": st, "passes": passes}

    pooled = table(all_test)
    t1 = {
        "criterion": "point estimate: formula Brier below climatological AND below Elo-only "
                     "on the pooled pre-registered test block (no significance gate, per the pre-registration)",
        "formula_brier": pooled["formula"].get("brier"),
        "climatological_brier": pooled["climatological"].get("brier"),
        "elo_only_brier": pooled["elo_only"].get("brier"),
        "passes": bool(pooled["formula"].get("brier") is not None
                       and pooled["formula"]["brier"] < pooled["climatological"].get("brier", 9)
                       and pooled["formula"]["brier"] < pooled["elo_only"].get("brier", 9)),
        "supporting_paired_tests": {
            "vs_climatological": paired_stats(formula_probs, arms["climatological"], labels, all_test, dates),
            "vs_elo_only": paired_stats(formula_probs, arms["elo_only"], labels, all_test, dates),
        },
    }

    ledger = {
        "generated_by": "python -m sports.nba.model.formula --evaluate",
        "formula": {"features": formula["features"], "coef_raw": formula["coef_raw"],
                    "intercept_raw": formula["intercept_raw"], "n_train": formula["n_train"],
                    "test_seasons": list(TEST_SEASONS), "train_end": TRAIN_END,
                    "tuned_on": formula.get("tuned_on")},
        "n_test_games": {"all": len(test), "with_close": len(have_close), "with_open": len(have_open)},
        "pooled_all_test": pooled,
        "pooled_with_close": table(have_close),
        "pooled_with_open": table(have_open),
        "tiers": {
            "T1_beats_naive_baselines": t1,
            "T2_beats_opening_line": tier("T2", arms["market_open"], have_open),
            "T3_beats_closing_line": tier("T3", arms["market_close"], have_close),
        },
        "notes": [
            "T2/T3 are paired tests at alpha=0.05 with 2,000-sample blocked bootstrap CIs (blocks of 10 "
            "games by date); a tier passes only if the CI excludes zero in the formula's favour.",
            "sigma_d was locked on 2021-22 before this ledger was produced (docs/preregistration.md).",
            "Per-season descriptive metrics may have been visible during development; this ledger is the "
            "claim-bearing artifact and the exposure is disclosed in the pre-registration.",
            "If a tier fails, the failure is published with the same prominence as a pass.",
        ],
    }
    out = paths.DATA / "nba_walkforward_v1.json"
    out.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    print(f"ledger -> {out}")
    return ledger


# ------------------------------------------------------------------ arm A6
# Arm A6 (Step 3) is EXPLORATORY: the formula plus pre-game availability. It is
# never a registered claim (the T1/T2/T3 tiers are about A5 and stand as
# published in data/nba_walkforward_v1.json, which this arm must not rewrite).
A6_DISCLAIMER = "exploratory arm, not a registered claim"


def is_a6_eligible(payload: dict) -> bool:
    """True iff every A6 input is present. A single None availability key makes
    the row ineligible - callers must EXCLUDE it, never impute (no zero-fill)."""
    if _feat(payload, "form_margin_diff") is None or _feat(payload, "net_rtg_diff") is None:
        return False
    for name in A6_FEATURES:
        if _feat(payload, name) is None:
            return False
    return True


def load_availability_dataset(con: sqlite3.Connection,
                              version: str = AVAIL_VERSION) -> tuple[list[dict], dict]:
    """v2 rows with complete A6 inputs, plus the row counts actually used.

    Exclusion (never imputation): rows missing early-season history or carrying
    any None availability key (non-authoritative inactive list) are dropped and
    counted in `excluded_*` - no value is ever filled in.
    """
    eligible: list[dict] = []
    excluded_no_history = 0
    excluded_avail_none = 0
    for r in con.execute("SELECT payload FROM features WHERE feature_version=?", (version,)):
        payload = json.loads(r["payload"])
        a5_ok = (_feat(payload, "form_margin_diff") is not None
                 and _feat(payload, "net_rtg_diff") is not None
                 and all(_feat(payload, n) is not None for n in FORMULA_FEATURES))
        if not a5_ok:
            excluded_no_history += 1
            continue
        if any(payload.get(k) is None for k in AVAIL_FEATURES):
            excluded_avail_none += 1
            continue
        payload["x"] = {name: _feat(payload, name) for name in A6_FEATURES}
        eligible.append(payload)
    eligible.sort(key=lambda row: (row["game_date"], row["game_id"]))
    counts = {
        "v2_rows_scanned": excluded_no_history + excluded_avail_none + len(eligible),
        "eligible": len(eligible),
        "excluded_no_history": excluded_no_history,
        "excluded_avail_none": excluded_avail_none,
    }
    return eligible, counts


def split_a6(rows: list[dict]) -> dict[str, list[dict]]:
    """Frozen windows, same protocol as A5: fit on seasons <= 2020-21, tune on
    2021-22 only, evaluate once on the frozen test block 2022-23..2025-26."""
    return {
        "train": [r for r in rows if r["season"] <= TRAIN_END],
        "tune": [r for r in rows if r["season"] == TUNE_SEASON],
        "test": [r for r in rows if r["season"] in TEST_SEASONS],
    }


def _exploratory_verdict(st: dict, a: str, b: str) -> str:
    """One-line verdict for a paired test where positive mean_diff favours `a`."""
    if not st.get("n"):
        return f"inconclusive: no overlapping games for {a} vs {b}"
    lo, hi = st["ci95_blocked"]
    if lo > 0:
        return f"{a} better than {b} (blocked 95% CI above zero, exploratory)"
    if hi < 0:
        return f"{b} better than {a} (blocked 95% CI below zero, exploratory)"
    return f"inconclusive: {a} vs {b} CI straddles zero (exploratory)"


def evaluate_availability(con: sqlite3.Connection, formula_a5: dict, rows_a6: list[dict],
                          market_close: dict[str, float], market_open: dict[str, float]) -> dict:
    """Fit A6 on seasons <= 2020-21, describe it on 2021-22, evaluate once on the
    frozen test block, and write data/nba_availability_v1.json (never touching
    the published A5 ledger). All paired tests reuse paired_stats() - the same
    blocked bootstrap (blocks of 10 games by date) as the published ledger."""
    parts = split_a6(rows_a6)
    train, tune, test = parts["train"], parts["tune"], parts["test"]
    assert all(r["season"] <= TRAIN_END for r in train), "A6 fit window leaked past 2020-21"
    assert all(r["season"] == TUNE_SEASON for r in tune)
    assert all(r["season"] in TEST_SEASONS for r in test)

    a6 = fit_formula(train, A6_FEATURES, c=A6_C)
    a6["tuned_on"] = TUNE_SEASON
    a6["C"] = A6_C
    a6["arm"] = "A6"

    labels = {r["game_id"]: r["home_win"] for r in rows_a6}
    dates = {r["game_id"]: r["game_date"] for r in rows_a6}
    probs_a6 = {r["game_id"]: predict(a6, r) for r in rows_a6}
    probs_a5 = {r["game_id"]: predict(formula_a5, r) for r in rows_a6}

    def side_by_side(games: list[str]) -> dict:
        return {"A5": summarise(probs_a5, labels, games),
                "A6": summarise(probs_a6, labels, games)}

    seasons = [TUNE_SEASON, *TEST_SEASONS]
    by_season = {}
    for season in seasons:
        games = [r["game_id"] for r in rows_a6 if r["season"] == season]
        entry = side_by_side(games)
        entry["n_eligible"] = len(games)
        by_season[season] = entry
    test_games = [r["game_id"] for r in test]
    pooled = side_by_side(test_games)
    tune_games = [r["game_id"] for r in tune]

    have_close = [g for g in test_games if g in market_close]
    have_open = [g for g in test_games if g in market_open]
    st_a6_vs_a5 = paired_stats(probs_a6, probs_a5, labels, test_games, dates)
    st_a6_vs_open = paired_stats(probs_a6, market_open, labels,
                                 [g for g in have_open if g in probs_a6], dates)
    st_a6_vs_close = paired_stats(probs_a6, market_close, labels,
                                  [g for g in have_close if g in probs_a6], dates)
    tune_check = paired_stats(probs_a6, probs_a5, labels, tune_games, dates)

    brier_a5 = pooled["A5"].get("brier")
    brier_a6 = pooled["A6"].get("brier")
    open_tab = summarise(market_open, labels, [g for g in have_open if g in probs_a6])
    gap_a5 = (brier_a5 - open_tab["brier"]) if brier_a5 and open_tab.get("brier") else None
    gap_a6 = (brier_a6 - open_tab["brier"]) if brier_a6 and open_tab.get("brier") else None
    if gap_a5 is not None and gap_a6 is not None:
        if gap_a6 < gap_a5 - 1e-9:
            verdict = (
                f"Availability narrows the pooled test Brier gap to the opening line from "
                f"{gap_a5:.5f} (A5) to {gap_a6:.5f} (A6), but does not close it (exploratory).")
        elif gap_a6 > gap_a5 + 1e-9:
            verdict = (
                f"Availability does not close the pooled test Brier gap to the opening line: "
                f"{gap_a5:.5f} (A5) vs {gap_a6:.5f} (A6) (exploratory).")
        else:
            verdict = ("Availability leaves the pooled test Brier gap to the opening line "
                       "unchanged (exploratory).")
    else:
        verdict = "inconclusive: missing Brier values for the gap comparison (exploratory)."

    artifact = {
        "disclaimer": A6_DISCLAIMER,
        "exploratory": True,
        "generated_by": "python -m sports.nba.model.formula --availability",
        "arm": "A6",
        "registered_claims_untouched": "T1/T2/T3 are about A5; see data/nba_walkforward_v1.json",
        "windows": {"fit": f"seasons <= {TRAIN_END}", "tune": TUNE_SEASON,
                    "test": list(TEST_SEASONS)},
        "hyperparameters": {"C": A6_C, "procedure": "standardised L2 logistic, fixed as for A5; "
                            "tune season used for descriptive validation only; no test tuning, "
                            "no threshold search"},
        "formula_a6": {"features": a6["features"], "coef_raw": a6["coef_raw"],
                       "intercept_raw": a6["intercept_raw"], "means": a6["means"], "sds": a6["sds"],
                       "n_train": a6["n_train"], "train_end": TRAIN_END, "tuned_on": TUNE_SEASON},
        "formula_a5_reference": {"features": formula_a5["features"],
                                 "coef_raw": formula_a5["coef_raw"],
                                 "intercept_raw": formula_a5["intercept_raw"],
                                 "n_train": formula_a5.get("n_train")},
        "row_counts": {
            "eligible_train": len(train), "eligible_tune": len(tune),
            "eligible_test": len(test),
            "test_with_close": len(have_close), "test_with_open": len(have_open),
        },
        "by_season_A5_vs_A6": by_season,
        "pooled_test_A5_vs_A6": pooled,
        "tune_descriptive_A6_vs_A5": tune_check,
        "paired_tests": {
            "A6_vs_A5": {"comparison": "A6 vs A5 (does availability improve the formula?)",
                         "stats": st_a6_vs_a5,
                         "verdict": _exploratory_verdict(st_a6_vs_a5, "A6", "A5")},
            "A6_vs_market_open": {"comparison": "A6 vs market open (EXPLORATORY T2-prime; "
                                  "the registered T2 is about A5 and stands as published)",
                                  "stats": st_a6_vs_open,
                                  "verdict": _exploratory_verdict(st_a6_vs_open, "A6", "market open")},
            "A6_vs_market_close": {"comparison": "A6 vs market close (EXPLORATORY T3-prime; "
                                   "the registered T3 is about A5 and stands as published)",
                                   "stats": st_a6_vs_close,
                                   "verdict": _exploratory_verdict(st_a6_vs_close, "A6", "market close")},
        },
        "brier_gap_to_open": {"A5_minus_open": gap_a5, "A6_minus_open": gap_a6,
                              "open_test_brier": open_tab.get("brier"),
                              "open_test_n": open_tab.get("n")},
        "verdict": verdict,
    }
    out = paths.DATA / "nba_availability_v1.json"
    out.write_text(json.dumps(artifact, indent=1), encoding="utf-8")
    print(f"availability ledger -> {out}")
    return artifact


def ablation(v1_rows: list[dict], a6_rows: list[dict]) -> dict:
    """Drop-one-signal ablation (Step 4a): refit without each feature on the fit
    window, record pooled + per-season test Brier delta vs the full model
    (positive delta = the dropped signal helped). Fixed C, no test tuning."""
    out: dict = {
        "disclaimer": A6_DISCLAIMER,
        "exploratory": True,
        "generated_by": "python -m sports.nba.model.formula --availability",
        "protocol": {"fit": f"seasons <= {TRAIN_END}", "test": list(TEST_SEASONS),
                     "C": A6_C, "delta": "Brier(dropped) - Brier(full) on the test block"},
    }

    def run(rows: list[dict], features: tuple[str, ...]) -> dict:
        train = [r for r in rows if r["season"] <= TRAIN_END]
        test = [r for r in rows if r["season"] in TEST_SEASONS]
        labels = {r["game_id"]: r["home_win"] for r in rows}

        def briers(model: dict) -> dict:
            probs = {r["game_id"]: predict(model, r) for r in rows}
            res = {}
            games_all = [r["game_id"] for r in test]
            ps = [probs[g] for g in games_all]
            ys = [labels[g] for g in games_all]
            res["pooled"] = round(sum(brier(p, y) for p, y in zip(ps, ys)) / len(ps), 5)
            for season in TEST_SEASONS:
                gs = [r["game_id"] for r in test if r["season"] == season]
                res[season] = round(
                    sum(brier(probs[g], labels[g]) for g in gs) / len(gs), 5)
            res["n_test"] = len(games_all)
            return res

        full = fit_formula(train, features, c=A6_C)
        base = briers(full)
        entry = {"n_train": full["n_train"], "full_test_brier": base, "by_feature": {}}
        for drop in features:
            kept = tuple(f for f in features if f != drop)
            refit = fit_formula(train, kept, c=A6_C)
            got = briers(refit)
            entry["by_feature"][drop] = {
                "delta_pooled": round(got["pooled"] - base["pooled"], 5),
                "delta_by_season": {s: round(got[s] - base[s], 5) for s in TEST_SEASONS},
                "dropped_test_brier_pooled": got["pooled"],
            }
        return entry

    out["A5"] = run(v1_rows, FORMULA_FEATURES)
    out["A6"] = run(a6_rows, A6_FEATURES)
    dest = paths.DATA / "nba_ablation_v1.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"ablation -> {dest}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NBA formula: fit, evaluate, measure sigma_d")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--sigma", action="store_true")
    ap.add_argument("--evaluate", action="store_true",
                    help="produce the pre-registered T1/T2/T3 ledger (run ONCE, on the full test block)")
    ap.add_argument("--availability", action="store_true",
                    help="fit/evaluate the EXPLORATORY A6 arm + ablation; writes "
                         "nba_availability_v1.json and nba_ablation_v1.json, never "
                         "touches nba_walkforward_v1.json")
    ap.add_argument("--version", default=FEATURE_VERSION)
    args = ap.parse_args(argv)

    con = build.init(verbose=False)
    rows = load_dataset(con, args.version)
    market = market_close_probs(con)
    print(f"dataset: {len(rows)} games with complete features; market close for {len(market)} games")

    train = [r for r in rows if r["season"] <= TRAIN_END]
    tune = [r for r in rows if r["season"] == TUNE_SEASON]
    formula = fit_formula(train)
    formula["tuned_on"] = TUNE_SEASON
    print(f"fitted on {formula['n_train']} games (<= {TRAIN_END}); tuning season {TUNE_SEASON} n={len(tune)}")

    arms = arm_probs(rows, market)
    labels = {r["game_id"]: r["home_win"] for r in rows}
    dates = {r["game_id"]: r["game_date"] for r in rows}

    if args.fit:
        out = paths.DATA / FORMULA_PATH
        out.write_text(json.dumps(formula, indent=1), encoding="utf-8")
        print(f"formula -> {out}")
        print("coefficients (raw units):")
        for k, v in formula["coef_raw"].items():
            print(f"   {k:<18} {v:+.6f}")
        print(f"   {'intercept':<18} {formula['intercept_raw']:+.6f}")

        seasons = sorted({r["season"] for r in rows})
        formula_probs = {r["game_id"]: predict(formula, r) for r in rows}
        report: dict = {"formula": {"features": list(FORMULA_FEATURES)}, "by_season": {}}
        for season in seasons:
            games = [r["game_id"] for r in rows if r["season"] == season]
            entry = {}
            for arm, probs in arms.items():
                if arm == "formula":
                    probs = formula_probs
                subset = [g for g in games if g in probs]      # market arms cover fewer seasons
                label = f"{arm} (market)" if arm == "market_close" else arm
                entry[label] = summarise(probs, labels, subset)
            entry["formula"] = summarise(formula_probs, labels, games)
            report["by_season"][season] = entry
            f = entry["formula"]
            m = entry.get("market_close (market)", {})
            print(f"  {season}: formula brier={f['brier']} acc={f['acc']} | market brier={m.get('brier')} "
                  f"| n={f['n']}")
        report["pooled_test"] = {}
        test_games = [r["game_id"] for r in rows if r["season"] in TEST_SEASONS and r["game_id"] in market]
        formula_probs = {r["game_id"]: predict(formula, r) for r in rows}
        for arm, probs in (("formula", formula_probs), ("market_close", arms["market_close"]),
                           ("elo_only", arms["elo_only"]), ("climatological", arms["climatological"])):
            report["pooled_test"][arm] = summarise(probs, labels, test_games)
        report["pooled_test_paired_formula_vs_market"] = paired_stats(
            formula_probs, arms["market_close"], labels, test_games, dates)
        tune_games = [r["game_id"] for r in tune if r["game_id"] in market]
        report["tuning_season_paired_formula_vs_market"] = paired_stats(
            formula_probs, arms["market_close"], labels, tune_games, dates)
        (paths.DATA / "formula_v1_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        print(f"\nreport -> {paths.DATA / 'formula_v1_report.json'}")
        print("pooled test (22-23..25-26, market games only):",
              json.dumps(report["pooled_test"], indent=1)[:600])
        print("formula vs market close, pooled test:",
              json.dumps(report["pooled_test_paired_formula_vs_market"]))
        return 0

    if args.sigma:
        formula_probs = {r["game_id"]: predict(formula, r) for r in rows}
        tune_games = [r["game_id"] for r in tune if r["game_id"] in market]
        st = paired_stats(formula_probs, arms["market_close"], labels, tune_games, dates)
        print(f"sigma_d measured on {TUNE_SEASON}: {st['sigma_d']}  (n={st['n']})")
        n_needed = math.ceil(((1.96 + 0.8416) ** 2) * st["sigma_d"] ** 2 / 0.005 ** 2)
        print(f"-> games needed to detect delta=0.005 Brier at 80% power: {n_needed:,}")
        print("   (write this sigma_d into docs/preregistration.md before evaluating test seasons)")
        return 0

    if args.evaluate:
        # closing arm: real sportsbook prices only; opening arm: books + the teamrankings movement
        # proxy (the only free source of openers for 2017-18..2022-23)
        market_open = market_probs(con, "open", books_only=False)
        market_close = market_probs(con, "close", books_only=True)
        ledger = evaluate(con, formula, rows, market_close, market_open)
        print(f"test games: all={ledger['n_test_games']['all']}, with close={ledger['n_test_games']['with_close']}, "
              f"with open={ledger['n_test_games']['with_open']}")
        print("T1 (vs naive baselines):", ledger["tiers"]["T1_beats_naive_baselines"]["passes"],
              "| formula", ledger["tiers"]["T1_beats_naive_baselines"]["formula_brier"],
              "vs climatological", ledger["tiers"]["T1_beats_naive_baselines"]["climatological_brier"],
              "vs elo", ledger["tiers"]["T1_beats_naive_baselines"]["elo_only_brier"])
        print("T2 (vs opening line):", ledger["tiers"]["T2_beats_opening_line"])
        print("T3 (vs closing line):", ledger["tiers"]["T3_beats_closing_line"])
        return 0

    if args.availability:
        # EXPLORATORY arm A6 + ablation. The A5 side reuses the PUBLISHED
        # coefficients (formula_v1.json) as pure arithmetic - A5 is not refit
        # and the registered ledger is never rewritten by this path.
        formula_a5 = json.loads((paths.DATA / FORMULA_PATH).read_text(encoding="utf-8"))
        assert list(formula_a5["features"]) == list(FORMULA_FEATURES)
        v1_rows = load_dataset(con, FEATURE_VERSION)
        a6_rows, a6_counts = load_availability_dataset(con, AVAIL_VERSION)
        print(f"availability dataset: {a6_counts['eligible']} eligible v2 rows "
              f"(excluded no-history={a6_counts['excluded_no_history']}, "
              f"excluded avail-None={a6_counts['excluded_avail_none']})")
        market_open = market_probs(con, "open", books_only=False)
        market_close = market_probs(con, "close", books_only=True)
        artifact = evaluate_availability(con, formula_a5, a6_rows, market_close, market_open)
        abl = ablation(v1_rows, a6_rows)
        pooled = artifact["pooled_test_A5_vs_A6"]
        print("\nA5 vs A6 on the frozen test block (A6-eligible games only):")
        print(f"  {'season':<10} {'n':>5}  {'A5 brier':>9} {'A6 brier':>9}  "
              f"{'A5 acc':>7} {'A6 acc':>7}  {'A5 ece':>7} {'A6 ece':>7}")
        for season, entry in artifact["by_season_A5_vs_A6"].items():
            a5s, a6s = entry["A5"], entry["A6"]
            print(f"  {season:<10} {entry['n_eligible']:>5}  {a5s['brier']:>9} {a6s['brier']:>9}  "
                  f"{a5s['acc']:>7} {a6s['acc']:>7}  {a5s['ece']:>7} {a6s['ece']:>7}")
        print(f"  {'pooled':<10} {pooled['A5']['n']:>5}  {pooled['A5']['brier']:>9} "
              f"{pooled['A6']['brier']:>9}  {pooled['A5']['acc']:>7} {pooled['A6']['acc']:>7}  "
              f"{pooled['A5']['ece']:>7} {pooled['A6']['ece']:>7}")
        for name, t in artifact["paired_tests"].items():
            s = t["stats"]
            print(f"{name}: mean_diff={s['mean_diff']} ci95={s['ci95_blocked']} n={s['n']} "
                  f"-> {t['verdict']}")
        print("verdict:", artifact["verdict"])
        print("ablation deltas (pooled test Brier, dropped - full):")
        for arm in ("A5", "A6"):
            ds = {f: v["delta_pooled"] for f, v in abl[arm]["by_feature"].items()}
            print(f"  {arm}: " + ", ".join(f"{f}={d:+.5f}" for f, d in ds.items()))
        return 0

    print("nothing to do: pass --fit or --sigma")
    return 0


if __name__ == "__main__":
    sys.exit(main())

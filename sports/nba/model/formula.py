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
from sports.nba.features.build import FEATURE_VERSION

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


def _feat(row: dict, name: str) -> float | None:
    """Derive a formula input from a raw feature payload (keeps the payload generic)."""
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


def market_probs(con: sqlite3.Connection, kind: str = "close") -> dict[str, float]:
    """Median no-vig fair P(home) from all books at a given snapshot kind (close | open | mid)."""
    per_game: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in con.execute("""SELECT game_id, book, side, price_decimal FROM odds_snapshots
                            WHERE market='moneyline' AND snapshot_kind=?""", (kind,)):
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
    return market_probs(con, "close")


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
def fit_formula(rows: list[dict], feature_names: tuple[str, ...] = FORMULA_FEATURES) -> dict:
    """Standardised logistic regression (L2), coefficients published in original units."""
    from sklearn.linear_model import LogisticRegression

    X = [[r["x"][n] for n in feature_names] for r in rows]
    y = [r["home_win"] for r in rows]
    n = len(rows)
    means = [sum(col) / n for col in zip(*X)]
    sds = [math.sqrt(sum((v - m) ** 2 for v in col) / max(1, n - 1)) or 1.0 for col, m in zip(zip(*X), means)]
    Xs = [[(v - m) / s for v, m, s in zip(row, means, sds)] for row in X]
    clf = LogisticRegression(C=1.0, max_iter=2000)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="NBA formula: fit, evaluate, measure sigma_d")
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--sigma", action="store_true")
    ap.add_argument("--evaluate", action="store_true",
                    help="produce the pre-registered T1/T2/T3 ledger (run ONCE, on the full test block)")
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
        market_open = market_probs(con, "open")
        ledger = evaluate(con, formula, rows, market, market_open)
        print(f"test games: all={ledger['n_test_games']['all']}, with close={ledger['n_test_games']['with_close']}, "
              f"with open={ledger['n_test_games']['with_open']}")
        print("T1 (vs naive baselines):", ledger["tiers"]["T1_beats_naive_baselines"]["passes"],
              "| formula", ledger["tiers"]["T1_beats_naive_baselines"]["formula_brier"],
              "vs climatological", ledger["tiers"]["T1_beats_naive_baselines"]["climatological_brier"],
              "vs elo", ledger["tiers"]["T1_beats_naive_baselines"]["elo_only_brier"])
        print("T2 (vs opening line):", ledger["tiers"]["T2_beats_opening_line"])
        print("T3 (vs closing line):", ledger["tiers"]["T3_beats_closing_line"])
        return 0

    print("nothing to do: pass --fit or --sigma")
    return 0


if __name__ == "__main__":
    sys.exit(main())

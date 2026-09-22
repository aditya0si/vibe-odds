"""Season simulator: frozen end-2023 system walks 2024-25 with per-match RL.

Arms (identical fixtures, identical frozen start):
  adaptive - Hedge weights + calibration + ratings all update per match
  frozen   - Hedge weights + calibration FROZEN, ratings still advance
             (ratings are state, not learned weights: freezing them too
             would confound "weight learning" with "stale Elo")
  elo      - surface Elo only, ratings update (classic baseline)
  market   - closing-odds proxy, only if data/market_close_24_25.csv exists

Paper-trading (P1): with no historical book prices, the betting leg uses a
declared LINE as the fair price (no vig):
  line_source="market" when the close CSV covers the match, else "elo"
  (the Elo arm's fair price). Edge/Kelly/CLV-style numbers are computed vs
  that line and LABELED as such — they measure skill vs the line, not real
  profit. Real-money validation needs the market_close CSV (tennis-only:
  date,a,b,prob_a rows).

Order per match is predict-THEN-update, enforced structurally.
python -m backend.sim.season [--since 20240101] [--last 2025] [--no-save]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
FROZEN = DATA / "sim_frozen"
LEDGER = DATA / "sim_2024_2025.json"
CUTOFF = 20240101
CAL_MIN_N = 500


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else None


def check_frozen() -> dict:
    """The live GBM must still be the frozen one, else the sim is dishonest."""
    man = json.loads((FROZEN / "manifest.json").read_text())
    live = _sha(DATA / "gbm.txt")
    if man["files"].get("gbm.txt") != live:
        raise SystemExit(
            f"live gbm.txt ({live}) != frozen ({man['files'].get('gbm.txt')}). "
            "Retrain happened after freeze: re-run backend.sim.freeze first.")
    return man


def load_market() -> dict:
    p = DATA / "market_close_24_25.csv"
    if not p.exists():
        return {}
    out = {}
    with p.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(int(r["date"]), r["a"], r["b"])] = float(r["prob_a"])
            except (KeyError, ValueError):
                continue
    return out


def new_state(weights: dict | None):
    from backend.model.ensemble import Ensemble
    from backend.model.signals import new_ctx

    return {"ctx": new_ctx(), "ens": Ensemble(weights, None)}


def replay(state: dict, m: dict) -> None:
    from backend.model.signals import replay_ctx

    replay_ctx(state["ctx"], m)


def arm_probs(arm: dict, a: str, b: str, m: dict) -> dict:
    from backend.model.signals import SIGNALS

    if arm["kind"] == "elo":
        return {"elo": arm["elo"].predict(a, b, m["surface"])["prob_a"]}
    ctx = arm["state"]["ctx"]
    probs = {k: fn(a, b, m["surface"], ctx, date=m["date"], best_of=m["best_of"])
             for k, fn in SIGNALS.items() if k not in ("market", "news")}
    if arm["kind"] == "gbm":
        return {"gbm": probs.get("gbm")}
    return probs


def arm_predict(arm: dict, probs: dict, surface: str) -> float:
    if arm["kind"] == "elo":
        return probs["elo"]
    if arm["kind"] == "gbm":
        return probs.get("gbm", 0.5)
    out = arm["state"]["ens"].predict(probs, surface)
    p = out["p_a"]
    fn = (arm.get("cal") or {}).get(surface) or arm.get("cal_global")
    if fn:
        from backend.model.calibrate import apply_isotonic
        p = apply_isotonic(fn, p)
    return p


def _block_ci(arms: dict, a: str, b: str, n_boot: int = 2000, seed: int = 7) -> dict:
    """Block-bootstrap 95% CI on mean Brier(a) - mean Brier(b), negative favors a.

    Blocks are (year-month, tournament): resampling matches would pretend
   Serial form/player dependence away. Deterministic seed.
    """
    import random as _random

    ha = [h for h in arms[a]["hist"] if h is not None]
    hb = [h for h in arms[b]["hist"] if h is not None]
    n = min(len(ha), len(hb))
    ha, hb = ha[:n], hb[:n]
    blocks: dict[tuple, list] = defaultdict(list)
    for i in range(n):
        t = ha[i][6] if len(ha[i]) > 6 else "?"
        blocks[(ha[i][0] // 100, t)].append(i)
    keys = list(blocks)
    rng = _random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        idx = []
        for _ in range(len(keys)):
            idx.extend(blocks[keys[rng.randrange(len(keys))]])
        da = sum(ha[i][2] for i in idx) / len(idx)
        db = sum(hb[i][2] for i in idx) / len(idx)
        diffs.append(da - db)
    diffs.sort()
    mean = sum(ha[i][2] for i in range(n)) / n - sum(hb[i][2] for i in range(n)) / n
    lo, hi = diffs[int(0.025 * n_boot)], diffs[int(0.975 * n_boot) - 1]
    return {"arms": f"{a}-vs-{b}", "n": n, "blocks": len(keys),
            "mean_diff": round(mean, 5), "ci95": [round(lo, 5), round(hi, 5)],
            "verdict": "a better" if hi < 0 else
                       ("b better" if lo > 0 else "no significant difference")}


def run(since: int = CUTOFF, last: int = 2025, save: bool = True,
        verbose: bool = True, max_matches: int = 0, use_cal: bool = False,
        haircut: float = 0.0, price_slip: float = 0.0) -> dict:
    """haircut: fraction shaved off winning payouts (costs/taxes).
    price_slip: fraction worsening the taken price (stale/unavailable best).
    Both default 0 (clean paper). Nonzero runs are STRESS experiments: they
    only add res['stress'], never replace the main betting leg."""
    from core import calibration as CAL
    from backend.policy.bandit import Policy, bucket
    from backend.ratings.elo import SurfaceElo
    from backend.ratings.loader import load_years

    man = check_frozen()
    frozen_w = None
    market = load_market()

    matches = load_years(2018, last)
    adaptive, frozen = new_state(frozen_w), new_state(frozen_w)
    gbm_state = new_state(frozen_w)  # own ratings replay; weights unused
    elo_arm = {"kind": "elo", "elo": SurfaceElo()}
    arms = {"adaptive": {"kind": "ens", "state": adaptive, "cal": {}, "cal_global": None, "hist": []},
            "frozen": {"kind": "ens", "state": frozen, "cal": {}, "cal_global": None, "hist": []},
            "gbm": {"kind": "gbm", "state": gbm_state, "hist": []},
            "elo": {"kind": "elo", "elo": elo_arm["elo"], "hist": []}}
    use_market = bool(market)
    if use_market:
        arms["market"] = {"kind": "market", "hist": []}

    # warmup: identical history into every learning state, no scoring
    for m in matches:
        if m["walkover"] or m["date"] >= since:
            continue
        replay(adaptive, m)
        replay(frozen, m)
        replay(gbm_state, m)
        elo_arm["elo"].update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                              m["best_of"], m["retirement"], False)

    policy = Policy()  # fresh: exercises the live post/pass loop on adaptive
    cur_month = None
    scored = 0
    upsets = 0
    bet = []  # paper-trading ledger (adaptive, bandit-gated)
    stress_bet = []  # bandit posts under haircut/slip (only when flags set)
    pol_ledgers: dict[str, list] = {"post_all": [], "ev003": [], "ev003_conf60": []}
    passes = []  # counterfactual: what bandit passes WOULD have done (only observable in sim)
    for m in matches:
        if m["walkover"] or m["date"] < since:
            continue
        a, b = sorted([m["winner"], m["loser"]])
        y = 1 if m["winner"] == a else 0
        probs_by_arm = {}
        p_by_arm = {}
        for name, arm in arms.items():
            if name == "market":
                p = market.get((m["date"], a, b))
                if p is None:
                    q = market.get((m["date"], b, a))
                    p = (1 - q) if q is not None else None
                arm["hist"].append(None if p is None else (m["date"], (p - y) ** 2, (p >= 0.5) == (y == 1)))
                continue
            probs = arm_probs(arm, a, b, m)
            probs_by_arm[name] = probs
            p = arm_predict(arm, probs, m["surface"])
            p_by_arm[name] = p
            arm["hist"].append((m["date"], m["surface"], (p - y) ** 2, (p >= 0.5) == (y == 1), p, y,
                                m["tourney"]))
            if name == "adaptive":
                arm["state"]["ens"].update(probs, m["winner"] == a, m["surface"])
                if max(p, 1 - p) >= 0.6 and (p >= 0.5) != (y == 1):
                    upsets += 1
        # --- paper trading on adaptive vs the declared line ---
        p_ad = p_by_arm.get("adaptive")
        if p_ad is not None:
            mp = market.get((m["date"], a, b))
            if mp is None:
                q = market.get((m["date"], b, a))
                mp = (1 - q) if q is not None else None
            if mp is not None:
                line_a, line_src = mp, "market"
            else:
                line_a, line_src = p_by_arm.get("elo", 0.5), "elo"
            line_a = min(0.97, max(0.03, line_a))
            pick_a = p_ad >= 0.5
            p_pick = p_ad if pick_a else 1 - p_ad
            line_pick = line_a if pick_a else 1 - line_a
            odds = 1.0 / line_pick
            edge = p_pick * odds - 1.0
            conf = max(p_ad, 1 - p_ad)
            dec = policy.decide(m["surface"], edge, conf)
            bk = bucket(m["surface"], edge, conf)
            from core.odds import kelly_fraction
            stake = kelly_fraction(p_pick, odds)  # half-Kelly units
            won = (p_ad >= 0.5) == (y == 1)
            profit = stake * (odds - 1.0) if won else -stake
            # W3 stress leg (accounting only): worse price + payout haircut
            stress_profit = None
            if haircut or price_slip:
                odds_s = odds * (1 - price_slip)
                stress_profit = (stake * (odds_s - 1.0) * (1 - haircut) if won
                                 else -stake)
            # W3: frozen threshold baselines on the SAME matches (bandit must beat these)
            frozen_posts = {"post_all": True, "ev003": edge >= 0.03,
                            "ev003_conf60": edge >= 0.03 and conf >= 0.6}
            for pname, do_post in frozen_posts.items():
                if do_post and stake > 0:
                    pol_ledgers[pname].append(profit)
            if dec["action"] == "post":
                policy.update(bk, "post", (odds - 1.0) if won else -1.0)
                bet.append({"date": m["date"], "surface": m["surface"], "edge": round(edge, 4),
                            "conf": round(conf, 4), "stake": round(stake, 4),
                            "profit": round(profit, 4), "won": won, "line": line_src})
                if stress_profit is not None:
                    stress_bet.append(stress_profit)
            else:
                passes.append(profit)
        # monthly per-surface calibration refit for the adaptive arm
        month = m["date"] // 100
        if use_cal and month != cur_month:
            cur_month = month
            tails: dict[str, list] = defaultdict(list)
            for h in arms["adaptive"]["hist"][-4000:]:
                if h is not None:
                    tails[h[1]].append(h)
            new_cal = {}
            for s, tail in tails.items():
                if len(tail) >= CAL_MIN_N:
                    h = len(tail) // 2
                    new_cal[s] = CAL.fit_isotonic([t[4] for t in tail[:h]],
                                                  [t[5] for t in tail[:h]])
            if new_cal:
                arms["adaptive"]["cal"] = new_cal
        replay(adaptive, m)
        replay(frozen, m)  # ratings advance; only weights/cal stay frozen
        replay(gbm_state, m)
        elo_arm["elo"].update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                              m["best_of"], m["retirement"], False)
        scored += 1
        if max_matches and scored >= max_matches:
            break

    def summ(hist):
        s = [h for h in hist if h is not None]
        n = len(s)
        return {"n": n,
                "acc": round(sum(h[3] for h in s) / n, 4) if n else 0,
                "brier": round(sum(h[2] for h in s) / n, 4) if n else 0}

    def per_surface(hist):
        out = {}
        by_s: dict[str, list] = defaultdict(list)
        for h in hist:
            if h is not None:
                by_s[h[1]].append(h)
        for s, ss in by_s.items():
            out[s] = {"n": len(ss),
                      "acc": round(sum(h[3] for h in ss) / len(ss), 4),
                      "brier": round(sum(h[2] for h in ss) / len(ss), 4)}
        return out

    def conf_table(hist):
        s = [h for h in hist if h is not None]
        out = {}
        for thresh in (0.55, 0.60, 0.65):
            sel = [h for h in s if max(h[4], 1 - h[4]) >= thresh]
            out[f">={thresh}"] = {"n": len(sel),
                                  "acc": round(sum(h[3] for h in sel) / len(sel), 4) if sel else 0,
                                  "brier": round(sum(h[2] for h in sel) / len(sel), 4) if sel else 0}
        return out

    res = {"cutoff": since, "last": last, "use_market": use_market,
           "line_note": "betting leg uses market close where present, else elo-arm fair price (no vig). Skill-vs-line, not real profit.",
           "arms": {k: summ(v["hist"]) for k, v in arms.items()},
           "per_surface": {k: per_surface(v["hist"]) for k, v in arms.items() if k != "market"},
           "confidence": {k: conf_table(v["hist"]) for k, v in arms.items() if k != "market"},
           "adaptive_upsets": upsets,
           "weights_final": {s: {k: round(v, 3) for k, v in w.items()}
                             for s, w in adaptive["ens"].surf.items()}}
    # paired sign test on PER-MATCH Brier (labeled: this is NOT mean-Brier
    # evidence — wins-small-often/loses-big-rarely passes it while losing the mean)
    ah = arms["adaptive"]["hist"]
    fh = arms["frozen"]["hist"]
    pairs = [(x, z) for x, z in zip(ah, fh) if x is not None and z is not None]
    wins = sum(1 for x, z in pairs if x[2] < z[2])
    ties = sum(1 for x, z in pairs if x[2] == z[2])
    n_eff = len(pairs) - ties
    z = ((wins - n_eff / 2) / math.sqrt(n_eff / 4)) if n_eff else 0.0
    res["paired"] = {"n": len(pairs), "adaptive_brier_wins": wins, "ties": ties,
                     "z": round(z, 2),
                     "test": "sign test on per-match Brier differences (median signal, not mean evidence)",
                     "verdict": "adaptive learns" if z > 1.96 else
                                ("frozen better" if z < -1.96 else "no significant difference")}
    res["paired_mean"] = _block_ci(arms, "adaptive", "frozen")
    res["gbm_vs_adaptive"] = _block_ci(arms, "gbm", "adaptive")
    res["gbm_vs_frozen"] = _block_ci(arms, "gbm", "frozen")
    # paper-trading summary (policy-gated, adaptive only)
    if bet:
        profit = sum(b["profit"] for b in bet)
        staked = sum(b["stake"] for b in bet)
        wins_b = sum(1 for b in bet if b["won"])
        eq, peak, maxdd = 0.0, 0.0, 0.0
        for b in bet:
            eq += b["profit"]
            peak = max(peak, eq)
            maxdd = max(maxdd, peak - eq)
        by_edge: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0])
        for b in bet:
            k = "thin<3%" if b["edge"] < 0.03 else ("good<8%" if b["edge"] < 0.08 else "big>=8%")
            by_edge[k][0] += b["edge"] * b["stake"]
            by_edge[k][1] += b["profit"]
            by_edge[k][2] += 1
        res["betting"] = {"n_posts": len(bet), "pass_rate": round(1 - len(bet) / scored, 4),
                          "win_rate": round(wins_b / len(bet), 4),
                          "profit_u": round(profit, 2),
                          "roi_on_staked_pct": round(profit / staked * 100, 2) if staked else 0,
                          "max_drawdown_u": round(maxdd, 2),
                          "by_edge": {k: {"n": v[2], "expected_u": round(v[0], 2),
                                          "realized_u": round(v[1], 2)} for k, v in by_edge.items()}}
    else:
        res["betting"] = {"n_posts": 0, "note": "policy passed everything"}
    if passes:
        res["betting"]["passed_n"] = len(passes)
        res["betting"]["passed_profit_u"] = round(sum(passes), 2)  # +lost profit, -avoided loss
    # W3 policy comparison: bandit (above) vs frozen rules on identical matches
    res["policies"] = {"bandit": {"n_posts": len(bet),
                                  "profit_u": round(sum(b["profit"] for b in bet), 2)}}
    for pname, ledger in pol_ledgers.items():
        res["policies"][pname] = {"n_posts": len(ledger),
                                  "profit_u": round(sum(ledger), 2)}
    bandit_u = res["policies"]["bandit"]["profit_u"]
    best_frozen = max(v["profit_u"] for k, v in res["policies"].items() if k != "bandit")
    res["policies"]["bandit_beats_frozen"] = bool(bandit_u > best_frozen)
    if haircut or price_slip:
        res["stress"] = {"haircut": haircut, "price_slip": price_slip,
                         "n_posts": len(stress_bet),
                         "profit_u": round(sum(stress_bet), 2),
                         "survives": bool(sum(stress_bet) > 0)}
    # rolling curves (every 50th scored match, adaptive + baselines)
    curve = []
    for i in range(0, len(ah), 50):
        win = {k: [h for h in v["hist"][:i + 50] if h is not None][-500:]
               for k, v in arms.items()}
        pt = {"i": i}
        for k, w in win.items():
            pt[k] = round(sum(h[2 if k != "market" else 1] for h in w) / len(w), 4) if w else None
        curve.append(pt)
    res["curve"] = curve
    if save:
        from core.io import atomic_write_json
        atomic_write_json(LEDGER, res, backup=True)
    if verbose:
        print(f"sim {since}->{last}: " +
              " | ".join(f"{k} acc {v['acc']} brier {v['brier']} (n={v['n']})"
                         for k, v in res["arms"].items()))
        print("paired adaptive-vs-frozen:", res["paired"])
        print("mean-Brier block CI adaptive-vs-frozen:", res["paired_mean"])
        print("mean-Brier block CI gbm-vs-adaptive:", res["gbm_vs_adaptive"])
        print("mean-Brier block CI gbm-vs-frozen:", res["gbm_vs_frozen"])
        print("policies (identical matches):", res["policies"])
        if "stress" in res:
            print("stress:", res["stress"])
        print("final adaptive weights:", res["weights_final"].get("hard"))
        print("betting (policy-gated, vs declared line):", res["betting"])
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=CUTOFF)
    ap.add_argument("--last", type=int, default=2025)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--max-matches", type=int, default=0)
    ap.add_argument("--cal", action="store_true",
                    help="enable monthly calibration refit (experiments only: "
                         "2026-09-07 sim showed the refit HURTS, -0.003 Brier)")
    ap.add_argument("--haircut", type=float, default=0.0)
    ap.add_argument("--price-slip", type=float, default=0.0)
    a = ap.parse_args()
    run(a.since, a.last, save=not a.no_save, max_matches=a.max_matches,
        use_cal=a.cal, haircut=a.haircut, price_slip=a.price_slip)

"""Self-improvement trainer. Replays recent history, tunes per-surface weights,
fits PER-SURFACE calibration, autopsies upsets.

python -m backend.model.learn [--since 20260101] [--no-save]
Local CSV only — costs zero API credits. Run nightly + after every session.

Calibration is per surface (hard/clay/grass/carpet) with a 200-sample
minimum; surfaces below that fall back to the global table. The old single
global table mis-calibrated clay/grass verdicts with hard-court numbers.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from backend.markov.points import PointRatings
from backend.model import calibrate as CAL
from backend.model.ensemble import Ensemble
from backend.model.signals import SIGNALS, Ctx
from backend.model.upsets import analyze, log_upset, pattern_summary, recent_upsets
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years

CAL_MIN_N = 200


def _seen_upset_keys() -> set:
    return {(u.get("date"), u.get("event"), u.get("winner")) for u in recent_upsets(500)}


def run(since: int = 20260101, last: int = 2026, save: bool = True,
        verbose: bool = True) -> dict:
    matches = load_years(2018, last)
    from backend.model.signals import new_ctx, replay_ctx
    ctx = new_ctx()
    ens = Ensemble.load()
    seen = _seen_upset_keys()
    scored, acc, brier = 0, 0, 0.0
    sig_b, sig_n = {}, {}
    surf_ps: dict[str, list] = defaultdict(list)
    surf_ys: dict[str, list] = defaultdict(list)
    surf_acc: dict[str, list] = defaultdict(lambda: [0, 0])
    new_upsets = 0
    for m in matches:
        if m["walkover"]:
            continue
        if m["date"] >= since:
            a, b = sorted([m["winner"], m["loser"]])
            y = 1 if m["winner"] == a else 0
            probs = {k: fn(a, b, m["surface"], ctx, date=m["date"], best_of=m["best_of"])
                     for k, fn in SIGNALS.items() if k not in ("market", "news")}
            out = ens.predict(probs, m["surface"])
            p = out["p_a"]
            acc += ((p >= 0.5) == (y == 1))
            brier += (p - y) ** 2
            scored += 1
            surf_ps[m["surface"]].append(p)
            surf_ys[m["surface"]].append(y)
            surf_acc[m["surface"]][0] += ((p >= 0.5) == (y == 1))
            surf_acc[m["surface"]][1] += 1
            for k, pv in probs.items():
                if pv is not None:
                    sig_b[k] = sig_b.get(k, 0.0) + (pv - y) ** 2
                    sig_n[k] = sig_n.get(k, 0) + 1
            ens.update(probs, m["winner"] == a, m["surface"])
            rep = analyze(a, b, m["surface"], probs, out["weights_used"], p,
                          m["winner"], f"{m['winner']} d {m['loser']} ({m['tourney']} {m['round']})",
                          m["date"])
            if rep and (rep["date"], rep["event"], rep["winner"]) not in seen:
                log_upset(rep)
                seen.add((rep["date"], rep["event"], rep["winner"]))
                new_upsets += 1
        replay_ctx(ctx, m)
    all_ps = [p for ps in surf_ps.values() for p in ps]
    all_ys = [y for ys in surf_ys.values() for y in ys]
    res = {
        "scored": scored,
        "accuracy": round(acc / scored, 4) if scored else 0,
        "brier": round(brier / scored, 4) if scored else 0,
        "ece": round(CAL.ece(all_ps, all_ys), 4) if scored else 0,
        "per_surface": {s: {"n": surf_acc[s][1],
                            "acc": round(surf_acc[s][0] / surf_acc[s][1], 4) if surf_acc[s][1] else 0,
                            "brier": round(sum((p - y) ** 2 for p, y in
                                               zip(surf_ps[s], surf_ys[s])) / len(surf_ps[s]), 4) if surf_ps[s] else 0,
                            "ece": round(CAL.ece(surf_ps[s], surf_ys[s]), 4) if surf_ps[s] else 0}
                       for s in surf_ps},
        "per_signal_brier": {k: round(sig_b[k] / sig_n[k], 4) for k in sig_b},
        "weights": {s: {k: round(v, 3) for k, v in w.items()} for s, w in ens.surf.items()},
        "new_upsets": new_upsets,
        "upset_patterns": pattern_summary(),
    }
    if scored > 500:
        cal_report = {}
        for s, ps in surf_ps.items():
            ys = surf_ys[s]
            if len(ps) < CAL_MIN_N:
                cal_report[s] = {"n": len(ps), "status": "fallback-global (too few)"}
                continue
            h = len(ps) // 2
            fn = CAL.fit_isotonic(ps[:h], ys[:h])
            cal = [CAL.apply_isotonic(fn, p) for p in ps[h:]]
            cal_report[s] = {"n": len(ps),
                             "ece_raw": round(CAL.ece(ps[h:], ys[h:]), 4),
                             "ece_cal": round(CAL.ece(cal, ys[h:]), 4)}
            if save:
                CAL.save_fn(fn, s)
        # global fallback table from all surfaces
        if len(all_ps) >= CAL_MIN_N:
            h = len(all_ps) // 2
            gfn = CAL.fit_isotonic(all_ps[:h], all_ys[:h])
            gcal = [CAL.apply_isotonic(gfn, p) for p in all_ps[h:]]
            cal_report["_global"] = {"n": len(all_ps),
                                     "ece_raw": round(CAL.ece(all_ps[h:], all_ys[h:]), 4),
                                     "ece_cal": round(CAL.ece(gcal, all_ys[h:]), 4)}
            if save:
                CAL.save_fn(gfn, "_global")
        res["calibration"] = cal_report
        res["ece_calibrated"] = round(CAL.ece(
            [CAL.apply_isotonic(CAL.fit_isotonic(all_ps[:len(all_ps)//2], all_ys[:len(all_ps)//2]), p)
             for p in all_ps[len(all_ps)//2:]], all_ys[len(all_ps)//2:]), 4)
    if save:
        ens.save()
    if verbose:
        print(f"scored {scored} since {since}: acc {res['accuracy']} brier {res['brier']} ece {res['ece']}")
        print("per-surface:", {s: v for s, v in res["per_surface"].items()})
        print("per-signal brier:", res["per_signal_brier"])
        for s, w in res["weights"].items():
            print(f"weights[{s}]:", w)
        if "calibration" in res:
            print("calibration:", res["calibration"])
        print(f"new upsets logged: {new_upsets}", "| patterns:", res["upset_patterns"])
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=20260101)
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    run(a.since, save=not a.no_save)

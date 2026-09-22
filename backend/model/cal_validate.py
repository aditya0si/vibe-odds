"""W3 calibration validation (PREREGISTERED 2026-09-07): fit on 2022-23 GBM probs,
test on 2024-25. Contenders: none / temperature (1-param) / logistic (2-param) /
isotonic (PAVA). Metrics pooled + per-surface + action region (conf>=0.6):
Brier and ECE.

Adopt a calibrator only if it lowers pooled out-of-time Brier AND does not
degrade the action region. Monthly refit stays dead regardless.
python -m backend.model.cal_validate
"""

from __future__ import annotations

from core import calibration as CAL


def _ece(ps, ys, n_bins: int = 10) -> float:
    return CAL.ece(list(ps), list(ys), n_bins)


def _brier(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def run(verbose: bool = True) -> dict:
    import math

    from sklearn.linear_model import LogisticRegression

    from backend.model.signals import new_ctx, replay_ctx, sig_gbm
    from backend.ratings.loader import load_years

    def logit(p):
        p = min(0.999, max(0.001, p))
        return math.log(p / (1 - p))

    def sig(x):
        return 1.0 / (1.0 + math.exp(-x))

    ctx = new_ctx()
    fit, test = [], []
    for m in load_years(2018, 2026):
        if m["walkover"]:
            continue
        if 20220101 <= m["date"] < 20260101:
            a, b = sorted([m["winner"], m["loser"]])
            y = 1 if m["winner"] == a else 0
            try:
                p = sig_gbm(a, b, m["surface"], ctx, date=m["date"], best_of=m["best_of"])
            except Exception:
                p = None
            if p is not None:
                (fit if m["date"] < 20240101 else test).append((p, y, m["surface"]))
        replay_ctx(ctx, m)

    pf = [p for p, _, _ in fit]
    yf = [y for _, y, _ in fit]
    Xf = [[logit(p)] for p in pf]
    temp = LogisticRegression(C=1e6).fit(Xf, yf)
    ta, tb = float(temp.coef_[0][0]), float(temp.intercept_[0])
    # 2-param logistic with L2 (temperature is the C->inf special case)
    lr = LogisticRegression(C=1.0).fit(Xf, yf)
    la, lb = float(lr.coef_[0][0]), float(lr.intercept_[0])
    iso = CAL.fit_isotonic(pf, yf)

    def apply(name, p):
        if name == "none":
            return p
        if name == "temp":
            return sig(ta * logit(p))
        if name == "logistic":
            return sig(la * logit(p) + lb)
        return CAL.apply_isotonic(iso, p)

    res = {"n_fit": len(fit), "n_test": len(test),
           "temp_ab": [round(ta, 3), round(tb, 3)],
           "logistic_ab": [round(la, 3), round(lb, 3)],
           "iso_blocks": len(iso), "methods": {}}
    for name in ("none", "temp", "logistic", "isotonic"):
        ps = [apply(name, p) for p, _, _ in test]
        ys = [y for _, y, _ in test]
        act = [(p, y) for p, y in zip(ps, ys) if max(p, 1 - p) >= 0.6]
        by_surf = {}
        for s in ("hard", "clay", "grass"):
            sp = [apply(name, p) for p, _, ss in test if ss == s]
            sy = [y for _, y, ss in test if ss == s]
            by_surf[s] = {"n": len(sp), "brier": round(_brier(sp, sy), 5)} if sp else {}
        res["methods"][name] = {
            "brier": round(_brier(ps, ys), 5), "ece": round(_ece(ps, ys), 4),
            "action_n": len(act),
            "action_brier": round(_brier([p for p, _ in act], [y for _, y in act]), 5) if act else None,
            "by_surface": by_surf}
    base = res["methods"]["none"]["brier"]
    for name in ("temp", "logistic", "isotonic"):
        m = res["methods"][name]
        m["gain_vs_none"] = round(base - m["brier"], 5)
        m["adopt"] = bool(m["gain_vs_none"] > 0 and m["action_brier"] is not None
                          and res["methods"]["none"]["action_brier"] is not None
                          and m["action_brier"] <= res["methods"]["none"]["action_brier"] + 0.0005)
    if verbose:
        import json
        print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    run()

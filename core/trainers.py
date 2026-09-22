"""Trainer math shared by the self-improvement + validation loops
(map step 13). Verbatim from backend/model/{cal_validate,learn}.py (map R4).
"""

from __future__ import annotations

import math


def logit(p: float) -> float:
    p = min(0.999, max(0.001, p))
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def brier_pairs(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def fit_logistic_scale(ps, ys, c: float):
    """(a, b) for sigmoid(a*logit(p) + b): sklearn LR on logits with L2 strength C.
    C->inf is the temperature special case (cal_validate's temp arm)."""
    from sklearn.linear_model import LogisticRegression
    Xf = [[logit(p)] for p in ps]
    lr = LogisticRegression(C=c).fit(Xf, ys)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def refit_isotonic(ps, ys, min_n: int):
    """Split-half isotonic refit (fit first half, eval second): (report, fn).
    fn is None below min_n — the caller falls back to the global table."""
    from core import calibration as CAL
    if len(ps) < min_n:
        return {"n": len(ps), "status": "fallback-global (too few)"}, None
    h = len(ps) // 2
    fn = CAL.fit_isotonic(ps[:h], ys[:h])
    cal = [CAL.apply_isotonic(fn, p) for p in ps[h:]]
    report = {"n": len(ps),
              "ece_raw": round(CAL.ece(ps[h:], ys[h:]), 4),
              "ece_cal": round(CAL.ece(cal, ys[h:]), 4)}
    return report, fn

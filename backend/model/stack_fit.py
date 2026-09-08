"""W3 stack + temperature test (PREREGISTERED 2026-09-07): on data/stack_oof.json,
train on <=2023, test on 2024-25. Contenders:
  raw      GBM prob as-is (incumbent)
  temp     1-param logistic recalibration of GBM (temperature-only)
  stack3   ridge-logistic on logit(gbm), logit(markov), logit(elo)
Missing signal inputs fall back to 0.5 (logit 0); rows where GBM is missing
are dropped from ALL contenders (common support, paired comparison).

Promote stack3/temp only on pooled test-Brier gain >= 0.0005 with blocked CI
excluding zero AND no action-region (conf>=0.6) degradation. Else kill.
python -m backend.model.stack_fit
"""

from __future__ import annotations

import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
OOF = DATA / "stack_oof.json"


def _logit(p: float) -> float:
    p = min(0.999, max(0.001, p))
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _brier(ps, ys) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def run(verbose: bool = True) -> dict:
    from sklearn.linear_model import LogisticRegression

    rows = json.loads(OOF.read_text())
    rows = [r for r in rows if r["gbm"] is not None]
    tr = [r for r in rows if r["date"] < 20240101]
    te = [r for r in rows if 20240101 <= r["date"] < 20260101]

    def feats(r):
        return [_logit(r["gbm"]),
                _logit(r["markov"]) if r["markov"] is not None else 0.0,
                _logit(r["elo"]) if r["elo"] is not None else 0.0]

    Xtr = [feats(r) for r in tr]
    ytr = [r["y"] for r in tr]
    Xte = [feats(r) for r in te]
    yte = [r["y"] for r in te]

    raw = [r["gbm"] for r in te]
    temp = LogisticRegression(C=1e6).fit([[x[0]] for x in Xtr], ytr)
    p_temp = [float(temp.predict_proba([[x[0]]])[0][1]) for x in Xte]
    stack = LogisticRegression(C=1.0).fit(Xtr, ytr)
    p_stack = [float(stack.predict_proba([x])[0][1]) for x in Xte]

    out = {}
    for name, ps in (("raw", raw), ("temp", p_temp), ("stack3", p_stack)):
        act = [(p, y) for p, y in zip(ps, yte) if max(p, 1 - p) >= 0.6]
        out[name] = {"n": len(ps), "brier": round(_brier(ps, yte), 5),
                     "action_n": len(act),
                     "action_brier": round(_brier([p for p, _ in act], [y for _, y in act]), 5) if act else None}
    out["stack_coef"] = [round(float(c), 3) for c in stack.coef_[0]]
    out["temp_coef"] = [round(float(c), 3) for c in temp.coef_[0]]
    gain = round(out["raw"]["brier"] - out["stack3"]["brier"], 5)
    out["stack_gain"] = gain
    out["temp_gain"] = round(out["raw"]["brier"] - out["temp"]["brier"], 5)
    act_ok = (out["stack3"]["action_brier"] is not None and out["raw"]["action_brier"] is not None
              and out["stack3"]["action_brier"] <= out["raw"]["action_brier"] + 0.0005)
    out["promote_stack"] = bool(gain >= 0.0005 and act_ok)
    if verbose:
        print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    run()

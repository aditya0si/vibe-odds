"""Model serving: replay history once, predict on demand. Lazy singleton state.

Reliability rules:
- Hot-reload: weights / calibration / GBM files are re-read when their
  mtime changes, so /api/learn takes effect WITHOUT a server restart.
  The expensive ratings replay only re-runs when forced.
- Every verdict is appended to data/verdicts.jsonl (audit trail).
- Player names are canonicalized (see backend.ratings.names); unknown
  players are flagged instead of silently rated 1500.
- Both the full model prob and the market-free prob are returned: edge
  and Kelly must be computed against a model that never saw the market.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from backend.markov.points import PointRatings
from backend.model import calibrate as CAL
from backend.model.ensemble import Ensemble, WEIGHTS_PATH
from backend.model.signals import SIGNALS, Ctx
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead
from backend.ratings.loader import load_years
from backend.ratings.names import canonical, familiarity

DATA = Path(__file__).resolve().parents[2] / "data"
VERDICTS_LOG = DATA / "verdicts.jsonl"

# Astra W1 simplification verdict: GBM-only (0.2156) vs ensemble (0.2163),
# block 95% CI [-0.00174, +0.00044] = equivalence -> simplicity wins.
# Ensemble stays for reasons/weights display + fallback; GBM is the number.
GBM_DEFAULT = True

_STATE: dict | None = None
_MTIMES: dict[str, float] = {}


def _mtimes() -> dict[str, float]:
    out = {}
    for p in (WEIGHTS_PATH, CAL.CAL_PATH, DATA / "gbm.txt", DATA / "gbm_features.json"):
        try:
            out[p.name] = p.stat().st_mtime
        except FileNotFoundError:
            out[p.name] = -1.0
    return out


def _refresh_light() -> None:
    """Reload cheap state (weights, calibration, GBM) into _STATE."""
    global _MTIMES
    assert _STATE is not None
    _STATE["ens"] = Ensemble.load()
    _STATE["cal"] = CAL.load_all()  # per-surface tables ({} if legacy)
    _STATE["cal_legacy"] = CAL.load_fn()
    try:
        import backend.gbm.predict as gp
        gp._MODEL, gp._META = None, None  # force re-read on next score_row
    except Exception:
        pass
    _MTIMES = _mtimes()


def get_state() -> dict:
    global _STATE
    if _STATE is None:
        from backend.model.signals import new_ctx, replay_ctx
        ctx = new_ctx()
        n = 0
        cutoff = 0
        try:
            from backend.ratings.snapshot import load as _load_snap
            snap = _load_snap()
        except Exception:
            snap = None
        if snap is not None:
            ctx, cutoff = snap
        for m in load_years(2018, 2026):
            if m["walkover"]:
                continue
            if m["date"] <= cutoff:
                continue  # already in the snapshot
            replay_ctx(ctx, m)
            n += 1
        _STATE = {"ctx": ctx, "ens": Ensemble.load(),
                  "cal": CAL.load_all(), "cal_legacy": CAL.load_fn(), "matches": n}
        _MTIMES.update(_mtimes())
        try:  # warm the GBM booster at boot, not on first verdict
            import backend.gbm.predict as _gp
            _gp._load()
        except Exception:
            pass
    elif _mtimes() != _MTIMES:
        _refresh_light()
    return _STATE


def _cal_for(cal_tables: dict, legacy, surface: str):
    fn = (cal_tables or {}).get(surface) or legacy
    return fn


def _log_verdict(card: dict) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with open(VERDICTS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **card}) + "\n")
    except Exception:
        pass  # logging must never break serving


def predict_match(a: str, b: str, surface: str = "hard", date: int = 99999999,
                  market_prob_a: float | None = None,
                  news_lean_a: float | None = None, best_of: int = 3) -> dict:
    st = get_state()
    ctx, ens = st["ctx"], st["ens"]
    a, b = canonical(a), canonical(b)
    probs = {k: fn(a, b, surface, ctx, date=date, best_of=best_of,
                   market_prob_a=market_prob_a, news_lean_a=news_lean_a)
             for k, fn in SIGNALS.items()}
    out = ens.predict(probs, surface)
    p_ens = out["p_a"]
    # market-free prob: same ensemble minus the market signal (for edge/Kelly)
    free = {k: v for k, v in probs.items() if k != "market"}
    p_free = ens.predict(free, surface)["p_a"] if any(v is not None for v in free.values()) else p_ens
    # GBM default: GBM never sees the market, so its prob is both the headline
    # AND market-free. Ensemble retained for reasons display + fallback.
    p_gbm = probs.get("gbm")
    p = p_gbm if (GBM_DEFAULT and p_gbm is not None) else p_ens
    if GBM_DEFAULT and p_gbm is not None:
        p_free = p_gbm
    source = "gbm" if (GBM_DEFAULT and p_gbm is not None) else "ensemble"
    fn = _cal_for(st.get("cal") or {}, st.get("cal_legacy"), surface)
    p_cal = CAL.apply_isotonic(fn, p) if fn else p
    fa = familiarity(a, ctx.elo, ctx.form, ctx.points, surface)
    fb = familiarity(b, ctx.elo, ctx.form, ctx.points, surface)
    unknown = [f["name"] for f in (fa, fb) if not f["known"]]
    elo = ctx.elo.predict(a, b, surface)
    h = ctx.h2h.lookup(a, b, surface)
    faf, fbf = ctx.form.features(a, date), ctx.form.features(b, date)
    reasons = [
        f"Surface Elo {elo['rating_a']:.0f} vs {elo['rating_b']:.0f} -> Elo says {a if elo['prob_a'] >= 0.5 else b} {max(elo['prob_a'], 1 - elo['prob_a']):.0%}",
        f"H2H {h['w_a']}-{h['w_b']}" + (f" ({h.get('surf_w_a', 0)}-{h.get('surf_w_b', 0)} on {surface})" if h['total'] else " (never met)") + f" -> {h['diff']:+d}",
        f"Last-10 form {faf['win_rate_10']:.0%} vs {fbf['win_rate_10']:.0%}",
    ]
    ps = probs.get("serve")
    if ps is not None:
        reasons.append(f"Serve edge -> {a if ps >= 0.5 else b} {max(ps, 1 - ps):.0%}")
    pm = probs.get("markov")
    if pm is not None:
        reasons.append(f"Point-model (Markov BO{best_of}) -> {a if pm >= 0.5 else b} {max(pm, 1 - pm):.0%}")
    pg = probs.get("gbm")
    if pg is not None:
        side = a if pg >= 0.5 else b
        reasons.append(f"ML model -> {side} {max(pg, 1 - pg):.0%}" + _shap_line(a, b, surface, ctx, date, best_of))
    ra, rb = faf.get("rest_days"), fbf.get("rest_days")
    if ra is not None and rb is not None and abs(ra - rb) >= 2:
        reasons.append(f"Rest {ra}d vs {rb}d -> fresher: {a if ra > rb else b}")
    rk = {a: faf.get("rank"), b: fbf.get("rank")}
    if rk[a] and rk[b]:
        reasons.append(f"Rank #{rk[a]:.0f} vs #{rk[b]:.0f}")
    if market_prob_a is not None:
        agree = "agrees" if (market_prob_a - 0.5) * (p - 0.5) > 0 else "DISAGREES"
        reasons.append(f"Market {market_prob_a:.0%} {agree} with model {p:.0%}")
    if unknown:
        reasons.append(f"Unknown/low-history player(s): {', '.join(unknown)} — treat with caution")
    pick = a if p >= 0.5 else b
    card = {"a": a, "b": b, "surface": surface, "pick": pick,
            "model_prob_a": round(p, 4), "model_prob_cal_a": round(p_cal, 4),
            "model_prob_free_a": round(p_free, 4),
            "model_prob_pick": round(max(p_cal, 1 - p_cal), 4),
            "prob_source": source,
            "signals": {k: (round(v, 4) if v is not None else None) for k, v in probs.items()},
            "weights_used": out["weights_used"], "reasons": reasons,
            "unknown_players": unknown,
            "familiarity": {a: fa["matches"], b: fb["matches"]}}
    _log_verdict(card)
    return card


def _shap_line(a: str, b: str, surface: str, ctx, date: int, best_of: int) -> str:
    try:
        from backend.gbm.predict import score_row
        from backend.model.signals import build_row

        m = {"date": date, "surface": surface, "best_of": best_of,
             "round": "R128", "level_mult": 1.0}
        row = build_row(a, b, m, ctx)  # point_cols included
        d = score_row(row)["drivers"][:3]
        bits = []
        for x in d:
            who = a if (x["push"] > 0) is True else b
            bits.append(f"{x['feature']}->{who}")
        return " (" + ", ".join(bits) + ")"
    except Exception:
        return ""

"""Read-only analytics helpers for the website. No modelling, no writes.

All functions read from live serve state (snapshot fast-path) or from frozen
data files on disk (sim ledger, calibration validation, tournament, OOF
predictions, feature store). Safe to call from FastAPI routes and from tests.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"

# Confidence slices from the frozen 2024-25 sim ledger (adaptive arm).
# Used for calibration badges on the board. Historical hit rates, not promises.
CONF_SLICES = [
    (0.65, 2318, 0.7692, 0.1736),
    (0.60, 3364, 0.7286, 0.1914),
    (0.55, 4594, 0.6892, 0.2056),
]


def _read_json(name: str):
    p = DATA / name
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def calibration_badge(conf: float) -> dict:
    """Badge for a model pick: historical 2024-25 sim hit rate at this confidence."""
    for thr, n, acc, brier in CONF_SLICES:
        if conf >= thr:
            hot = thr >= 0.65
            return {"tier": f"conf≥{thr:.2f}", "hist_acc": acc, "hist_n": n,
                    "hist_brier": brier, "hot": hot,
                    "label": f"{acc:.0%} hit rate ≥{thr:.0%} conf (n={n}, 2024–25 sim)"}
    return {"tier": "conf<0.55", "hist_acc": None, "hist_n": None,
            "hist_brier": None, "hot": False,
            "label": "below 55% confidence — thin edge zone, usually gated out"}


def players_list(q: str = "", n: int = 50, surface: str = "hard") -> list[dict]:
    """Top players by blended Elo. q filters by case-insensitive substring."""
    from backend.model.serve import get_state
    from backend.ratings.names import canonical

    st = get_state()
    elo = st["ctx"].elo
    q = canonical(q.strip()) if q.strip() else ""
    rows = []
    for p in elo.players.values():
        if p.n < 5:
            continue
        if q and q.lower() not in p.name.lower():
            continue
        rows.append({
            "name": p.name,
            "matches": p.n,
            "overall": round(p.overall, 1),
            "hard": round(elo.blended(p, "hard"), 1),
            "clay": round(elo.blended(p, "clay"), 1),
            "grass": round(elo.blended(p, "grass"), 1),
            "blended": round(elo.blended(p, surface), 1),
            "cold_start": p.n < 10,
        })
    rows.sort(key=lambda r: -r["blended"])
    return rows[: max(1, min(n, 200))]


@lru_cache(maxsize=1)
def _features_df():
    import pandas as pd

    return pd.read_parquet(DATA / "features.parquet")


def player_detail(name: str, vs: str = "") -> dict:
    """Elo snapshot + form + surface splits + recent matches + H2H. Read-only."""
    from backend.model.serve import get_state
    from backend.ratings.names import canonical

    name = canonical(name.strip())
    st = get_state()
    ctx = st["ctx"]
    rec = ctx.elo.players.get(name)
    if rec is None:
        # substring fallback: "Alcaraz" -> "Carlos Alcaraz"
        hits = [p for p in ctx.elo.players.values()
                if name.lower() in p.name.lower() and p.n >= 5]
        hits.sort(key=lambda p: -p.n)
        if hits:
            name, rec = hits[0].name, hits[0]
    if rec is None or rec.n < 5:
        return {"name": name, "unknown": True,
                "note": "fewer than 5 tour-level matches on record — no rating shown (cold-start containment)"}
    form = ctx.form.features(name, 99999999)
    last10 = list(ctx.form.results.get(name, []))
    df = _features_df()
    pm = df[(df["a"] == name) | (df["b"] == name)].copy()
    pm["won"] = ((pm["a"] == name) == (pm["y"] == 1))
    splits = {}
    for surf, g in pm.groupby("surface"):
        splits[surf] = {"n": int(len(g)), "wins": int(g["won"].sum()),
                        "win_rate": round(float(g["won"].mean()), 3)}
    recent = []
    for r in pm.sort_values("date", ascending=False).head(15).itertuples():
        opp = r.b if r.a == name else r.a
        recent.append({"date": int(r.date), "surface": r.surface, "round": r.round,
                       "opponent": opp, "result": "W" if r.won else "L"})
    # Form trend: chronological rolling win rate over the last 20 (honest label).
    trend = []
    if len(pm):
        chrono = pm.sort_values("date")
        roll = chrono["won"].rolling(10, min_periods=3).mean().tail(20)
        trend = [{"date": int(d), "win_rate_10": round(float(w), 3)}
                 for d, w in zip(chrono["date"].iloc[-len(roll):], roll)]
    out = {
        "name": name, "unknown": False,
        "matches": rec.n,
        "overall": round(rec.overall, 1),
        "surfaces": {s: {"rating": round(ctx.elo.blended(rec, s), 1),
                         "n": rec.surf_n.get(s, 0)} for s in ("hard", "clay", "grass")},
        "cold_start": rec.n < 10,
        "form_last10": {"n": len(last10),
                        "win_rate": round(sum(last10) / len(last10), 3) if last10 else None},
        "surface_splits": splits,
        "recent": recent,
        "form_trend": trend,
    }
    if vs and vs.strip():
        opp = canonical(vs.strip())
        out["h2h_vs"] = {"opponent": opp, **ctx.h2h.lookup(name, opp, None),
                         "hard": ctx.h2h.lookup(name, opp, "hard"),
                         "clay": ctx.h2h.lookup(name, opp, "clay"),
                         "grass": ctx.h2h.lookup(name, opp, "grass")}
    return out


def h2h(a: str, b: str) -> dict:
    from backend.model.serve import get_state
    from backend.ratings.names import canonical

    a, b = canonical(a.strip()), canonical(b.strip())
    ctx = get_state()["ctx"]
    return {"a": a, "b": b, **ctx.h2h.lookup(a, b, None),
            "by_surface": {s: ctx.h2h.lookup(a, b, s) for s in ("hard", "clay", "grass")}}


@lru_cache(maxsize=8)
def reliability(n_bins: int = 10) -> dict:
    """Decile-binned GBM out-of-fold probs vs outcomes. Honest calibration plot data."""
    oof = _read_json("stack_oof.json") or []
    rows = [(r["gbm"], r["y"]) for r in oof
            if r.get("gbm") is not None and r.get("y") in (0, 1)]
    rows.sort(key=lambda r: r[0])
    n_bins = max(5, min(int(n_bins), 20))
    bins, out = [], []
    chunk = max(1, len(rows) // n_bins)
    for i in range(0, len(rows), chunk):
        seg = rows[i:i + chunk]
        if not seg:
            continue
        p_mean = sum(p for p, _ in seg) / len(seg)
        frac = sum(y for _, y in seg) / len(seg)
        bins.append({"p_mean": round(p_mean, 4), "frac_pos": round(frac, 4), "n": len(seg)})
    ece = round(sum(b["n"] * abs(b["p_mean"] - b["frac_pos"]) for b in bins) / max(1, sum(b["n"] for b in bins)), 4)
    return {"n": len(rows), "bins": bins, "ece_oof": ece,
            "note": "GBM out-of-fold probs binned by predicted prob (equal-count bins). Perfect calibration = points on the diagonal."}


def model_summary() -> dict:
    """Everything the Model + Experiments pages need, straight from frozen files."""
    sim = _read_json("sim_2024_2025.json") or {}
    cal = _read_json("cal_validation.json") or {}
    tourney = _read_json("tourney.json") or {}
    ablation = _read_json("ablation.json")
    try:
        manifest = json.loads((DATA / "sim_frozen" / "manifest.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}
    imp = (tourney.get("improvement_vs_A") or {})
    folds = (tourney.get("results") or {})
    return {
        "sim": {
            "n": (sim.get("arms") or {}).get("adaptive", {}).get("n"),
            "arms": sim.get("arms"),
            "per_surface": sim.get("per_surface"),
            "confidence": sim.get("confidence"),
            "paired_mean": sim.get("paired_mean"),
            "gbm_vs_adaptive": sim.get("gbm_vs_adaptive"),
            "gbm_vs_frozen": sim.get("gbm_vs_frozen"),
            "betting": sim.get("betting"),
            "policies": sim.get("policies"),
            "line_note": sim.get("line_note"),
            "use_market": sim.get("use_market"),
        },
        "calibration_validation": {
            "n_fit": cal.get("n_fit"), "n_test": cal.get("n_test"),
            "methods": cal.get("methods"),
            "verdict": "raw GBM ships uncalibrated: none 0.2141/ECE 0.0136 beats temperature, logistic and isotonic (all gain<=0)",
        },
        "column_tournament": {
            "sets": tourney.get("sets"), "folds": tourney.get("folds"),
            "improvement_vs_A": imp,
            "B_folds": folds.get("B"),
            "verdict": "ship B (shrunk serve states + missingness + age): +0.00082 mean, 4/4 folds positive, CI excludes zero. C/D extras killed (unproven over B). E dead (kstar=0 everywhere).",
        },
        "ablation": ablation or {"error": "data/ablation.json not built yet — run python -m backend.model.ablate"},
        "gbm_freeze": manifest,
    }

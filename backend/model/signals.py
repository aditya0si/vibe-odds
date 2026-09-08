"""Per-signal win probabilities. Each signal: (a, b, surface, ctx, **kw) -> prob_a or None.

Signals cover every parameter family the data supports. Weak ones earn ~zero
weight automatically via the Hedge updater instead of being hand-deleted.
"""

from __future__ import annotations

import math

from backend.markov.match import match_p
from backend.markov.points import PointRatings
from backend.ratings.elo import SurfaceElo
from backend.ratings.features import FormTracker, HeadToHead


class Ctx:
    def __init__(self, elo: SurfaceElo, h2h: HeadToHead, form: FormTracker,
                 points: PointRatings | None = None,
                 elo_fast: SurfaceElo | None = None,
                 points_fast: PointRatings | None = None):
        self.elo = elo
        self.h2h = h2h
        self.form = form
        self.points = points or PointRatings()
        # fast-timescale twins (W2-D): 2x-K Elo + HL-45 point ratings
        self.elo_fast = elo_fast or SurfaceElo(base_k=48.0, surface_k=64.0,
                                               blend_threshold=20)
        self.points_fast = points_fast or PointRatings(half_life_days=45)


def new_ctx() -> Ctx:
    """One call builds the full replay state (slow + fast twins)."""
    return Ctx(SurfaceElo(), HeadToHead(), FormTracker(), PointRatings(),
               SurfaceElo(base_k=48.0, surface_k=64.0, blend_threshold=20),
               PointRatings(half_life_days=45))


def replay_ctx(ctx: Ctx, m: dict) -> None:
    """Advance ALL replay state by one match (walkovers skipped by callers)."""
    ctx.elo.update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                   m["best_of"], m["retirement"], False)
    ctx.elo_fast.update(m["winner"], m["loser"], m["surface"], m["level_mult"],
                        m["best_of"], m["retirement"], False)
    ctx.h2h.record(m["winner"], m["loser"], m["surface"])
    ctx.form.record(m)
    ctx.points.record(m)
    ctx.points_fast.record(m)


DIFF_KEYS = ["win_rate_10", "first_won", "second_won", "bp_saved", "ace_rate",
             "df_rate", "tb_rate", "minutes_14d", "grind_10", "rest_days"]


def _num(x, default=0.0) -> float:
    return float(x) if x is not None else default


def _shrunk_diff(fa: dict, fb: dict, key: str, k: float = 10.0) -> float:
    """Serve-stat diff shrunk toward 0 by coverage (W2-B): thin samples vote ~0."""
    n = min(fa.get("serve_n", 0) or 0, fb.get("serve_n", 0) or 0)
    return (_num(fa.get(key)) - _num(fb.get(key))) * (n / (n + k))


def point_cols(ctx: "Ctx", a: str, b: str, surface: str, best_of: int) -> dict:
    """Point-model columns, slow + fast twins. SINGLE source of truth for
    build_row, sig_gbm and serve._shap_line (they must never drift apart)."""
    from backend.markov.match import match_p

    mu = ctx.points.matchup(a, b, surface)
    slow_ok = mu["sample"] >= 200
    out = {
        "markov_p": match_p(mu["fAB"], mu["fBA"], best_of) if slow_ok else 0.5,
        "serve_edge": mu["fAB"] - mu["fBA"],
        "pt_sample": mu["sample"],
        "return_state_diff": _state_diff(ctx.points, a, b, surface)[1],
    }
    mf = ctx.points_fast.matchup(a, b, surface)
    fast_ok = mf["sample"] >= 60  # shorter window earns trust sooner
    out["markov_p_fast"] = match_p(mf["fAB"], mf["fBA"], best_of) if fast_ok else 0.5
    out["serve_edge_fast"] = mf["fAB"] - mf["fBA"]
    out["state_n_min"] = min(mu["sample"], mf["sample"])  # weaker leg binds
    return out


def _state_diff(pts: PointRatings, a: str, b: str, surface: str) -> tuple[float, float]:
    """Shrunk (serve, return) state diffs: sA-sB, rA-rB."""
    sA, rA, _ = pts._shrunk((a, surface))
    sB, rB, _ = pts._shrunk((b, surface))
    return sA - sB, rA - rB


def build_row(a: str, b: str, m: dict, ctx: "Ctx") -> dict:
    """As-of feature row, a-perspective. Same columns as training frame."""
    import math as _math

    fa, fb = ctx.form.features(a, m["date"]), ctx.form.features(b, m["date"])
    elo = ctx.elo
    pa, pb = elo.get(a), elo.get(b)
    pfa, pfb = ctx.elo_fast.get(a), ctx.elo_fast.get(b)
    h = ctx.h2h.lookup(a, b, m["surface"])
    row = {
        "date": m["date"], "surface": m["surface"], "best_of": m["best_of"],
        "round": m["round"], "level_mult": m["level_mult"],
        "a": a, "b": b,
        "elo_surf_diff": elo.blended(pa, m["surface"]) - elo.blended(pb, m["surface"]),
        "elo_overall_diff": pa.overall - pb.overall,
        "elo_fast_diff": (elo.blended(pfa, m["surface"]) - elo.blended(pfb, m["surface"])),
        "h2h_diff": h["diff"], "h2h_total": h["total"],
        "surf_h2h_diff": h.get("surf_diff", 0),
        "rank_diff": _num(fb.get("rank"), 1500) - _num(fa.get("rank"), 1500),
        "rank_missing": int(fa.get("rank") is None or fb.get("rank") is None),
        "age_diff": _num(fa.get("age"), 30) - _num(fb.get("age"), 30),
        "exp_diff": _math.log1p(fa.get("career_n", 0)) - _math.log1p(fb.get("career_n", 0)),
        "form_n_min": min(fa["form_n"], fb["form_n"]),
        "serve_n_min": min(fa.get("serve_n", 0), fb.get("serve_n", 0)),
        "serve_missing": int((fa.get("serve_n", 0) or 0) == 0 or (fb.get("serve_n", 0) or 0) == 0),
        "s1_shrunk_diff": _shrunk_diff(fa, fb, "first_won"),
        "s2_shrunk_diff": _shrunk_diff(fa, fb, "second_won"),
        "ace_shrunk_diff": _shrunk_diff(fa, fb, "ace_rate"),
        "df_shrunk_diff": _shrunk_diff(fa, fb, "df_rate"),
    }
    for k in DIFF_KEYS:
        row[f"{k}_diff"] = _num(fa.get(k)) - _num(fb.get(k))
    row.update(point_cols(ctx, a, b, m["surface"], m["best_of"]))
    return row


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def sig_elo_surface(a: str, b: str, surface: str, ctx: Ctx, **kw) -> float:
    return ctx.elo.predict(a, b, surface)["prob_a"]


def sig_elo_overall(a: str, b: str, surface: str, ctx: Ctx, **kw) -> float:
    pa, pb = ctx.elo.get(a), ctx.elo.get(b)
    return SurfaceElo.expected(pa.overall, pb.overall)


def sig_h2h(a: str, b: str, surface: str, ctx: Ctx, **kw) -> float:
    h = ctx.h2h.lookup(a, b, surface)
    tot, d = h["total"], h["diff"]
    stot = h.get("surf_w_a", 0) + h.get("surf_w_b", 0)
    sd = h.get("surf_diff", 0)
    eff_n = tot + 2 * stot
    excess = d / 2.0 + sd
    return (2 + eff_n / 2.0 + excess) / (4 + eff_n)


def sig_form(a: str, b: str, surface: str, ctx: Ctx, date: int = 0, **kw) -> float:
    fa, fb = ctx.form.features(a, date), ctx.form.features(b, date)
    edge = (fa["win_rate_10"] - fb["win_rate_10"]) * 2.0
    if fa["form_n"] < 3 or fb["form_n"] < 3:
        edge *= 0.5
    return _logistic(3.0 * edge)


def sig_serve(a: str, b: str, surface: str, ctx: Ctx, date: int = 0, **kw):
    fa, fb = ctx.form.features(a, date), ctx.form.features(b, date)
    if fa.get("serve_n", 0) < 3 or fb.get("serve_n", 0) < 3:
        return None
    edge = 0.0
    for k, w in (("first_won", 1.5), ("second_won", 1.2), ("bp_saved", 0.8), ("ace_rate", 0.5)):
        va, vb = fa.get(k), fb.get(k)
        if va is None or vb is None:
            continue
        edge += (va - vb) * w
    return _logistic(4.0 * edge)


def sig_rest(a: str, b: str, surface: str, ctx: Ctx, date: int = 0, **kw):
    fa, fb = ctx.form.features(a, date), ctx.form.features(b, date)
    ra, rb = fa.get("rest_days"), fb.get("rest_days")
    if ra is None or rb is None:
        return None
    lean = max(-0.06, min(0.06, (rb - ra) * 0.008))  # rested player gains, capped
    return 0.5 + lean


def sig_rank(a: str, b: str, surface: str, ctx: Ctx, date: int = 0, **kw):
    fa, fb = ctx.form.features(a, date), ctx.form.features(b, date)
    ra, rb = fa.get("rank"), fb.get("rank")
    if ra is None or rb is None:
        return None
    return 0.5 + max(-0.35, min(0.35, (rb - ra) * 0.004))


def sig_experience(a: str, b: str, surface: str, ctx: Ctx, date: int = 0, **kw):
    fa, fb = ctx.form.features(a, date), ctx.form.features(b, date)
    diff = math.log1p(fa.get("career_n", 0)) - math.log1p(fb.get("career_n", 0))
    return 0.5 + max(-0.10, min(0.10, diff * 0.05))


def sig_market(a: str, b: str, surface: str, ctx: Ctx, market_prob_a: float | None = None, **kw):
    return market_prob_a


def sig_markov(a: str, b: str, surface: str, ctx: Ctx, best_of: int = 3, **kw):
    mu = ctx.points.matchup(a, b, surface)
    if mu["sample"] < 200:
        return None  # insufficient point history; shrinkage would just vote 0.5
    return match_p(mu["fAB"], mu["fBA"], best_of)


def sig_news(a: str, b: str, surface: str, ctx: Ctx, news_lean_a: float | None = None, **kw):
    if news_lean_a is None:
        return None  # stub until news pipeline lands
    return _logistic(2.0 * news_lean_a)


def sig_gbm(a: str, b: str, surface: str, ctx: Ctx, date: int = 0, best_of: int = 3,
            round_: str = "R128", level_mult: float = 1.0, **kw):
    try:
        from backend.gbm.predict import score_row
    except ImportError:
        return None
    m = {"date": date, "surface": surface, "best_of": best_of,
         "round": round_, "level_mult": level_mult}
    row = build_row(a, b, m, ctx)  # point_cols included; single source of truth
    try:
        p1 = score_row(row)["p"]
    except Exception:
        return None
    # Orientation symmetrization: LightGBM is not an odd function, but the
    # matchup is — average both perspectives (variance reduction, exact
    # swap symmetry for every pair, not just tolerance). Single choke point:
    # sim, learn, ablation and serve all share it.
    try:
        row2 = build_row(b, a, m, ctx)
        p2 = score_row(row2)["p"]
        return (p1 + 1 - p2) / 2
    except Exception:
        return p1


SIGNALS = {
    "elo_surface": sig_elo_surface,
    "elo_overall": sig_elo_overall,
    "h2h": sig_h2h,
    "form": sig_form,
    "serve": sig_serve,
    "rest": sig_rest,
    "rank": sig_rank,
    "experience": sig_experience,
    "markov": sig_markov,
    "gbm": sig_gbm,
    "market": sig_market,
    "news": sig_news,
}

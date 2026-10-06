"""A11 (Phase-3 candidate): schedule-adjusted team ratings (SRS-style), features v6.

The absence channel and Elo both exist; this pass asks whether an explicit
opponent-adjusted TEAM strength rating adds anything beyond them. Mechanism (a
team-level version of the player-value ridge, pre-stated):

    y_g  = home margin, winsorised to +/-MARGIN_CLIP (blowout noise; same clip as
           player_value.py)
    x_g  = team dummies with side signs (+1 home, -1 away) + intercept (absorbs HCA)
    R_t  = argmin ||y - X R||^2 + alpha ||R||^2,   alpha = SRS_ALPHA = 1.0
           (dense, well-identified system: ~30 columns, >= 1000 games per window)

Refits at season boundaries on strictly PRIOR seasons (expanding window, NO decay -
the A10 result: decay never helped). A team with no prior appearance takes 0.0
(replacement level, the house convention). Emitted keys (SRS_KEYS):

    srs_home / srs_away   as-of ratings (points per game of presence)
    srs_diff              srs_home - srs_away (positive favours home)

AS-OF DISCIPLINE: identical construction to the other passes - state (ratings)
updated only at season boundaries from games strictly before the boundary, so
build_srs(upto=cut) reproduces the full build's prefix exactly (asserted in
tests/sports/nba/test_team_ratings.py).
"""
from __future__ import annotations

import sqlite3

import numpy as np

MARGIN_CLIP = 20.0
SRS_ALPHA = 1.0
SRS_KEYS = ("srs_home", "srs_away", "srs_diff")


def build_srs(con: sqlite3.Connection, upto: str | None = None,
              verbose: bool = False, info_out: dict | None = None) -> dict[str, dict]:
    """Chronological pass over scored regular-season games -> {game_id: srs_* keys}."""
    sql = """SELECT g.game_id, g.season, g.game_date, g.home_team_id, g.away_team_id,
                    g.home_score, g.away_score
             FROM games g
             WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL"""
    params: tuple = ()
    if upto:
        sql += " AND g.game_date <= ?"
        params = (upto,)
    sql += " ORDER BY g.game_date, g.game_id"
    games = list(con.execute(sql, params))

    first_idx: dict[str, int] = {}
    for i, g in enumerate(games):
        first_idx.setdefault(g[1], i)

    ratings: dict[int, float] = {}
    refits: list[dict] = []
    out: dict[str, dict] = {}

    for i, g in enumerate(games):
        if i == first_idx[g[1]] and i > 0:
            prior = games[:i]
            teams = sorted({t for gg in prior for t in (gg[3], gg[4])})
            col = {t: j for j, t in enumerate(teams)}
            n = len(prior)
            X = np.zeros((n, len(teams)), dtype=float)
            y = np.zeros(n, dtype=float)
            for k, gg in enumerate(prior):
                X[k, col[gg[3]]] += 1.0
                X[k, col[gg[4]]] -= 1.0
                y[k] = np.clip(gg[5] - gg[6], -MARGIN_CLIP, MARGIN_CLIP)
            from sklearn.linear_model import Ridge

            model = Ridge(alpha=SRS_ALPHA).fit(X, y)
            ratings = {t: float(model.coef_[col[t]]) for t in teams}
            refits.append({
                "season": g[1], "n_prior_games": n, "n_teams": len(teams),
                "intercept_hca": round(float(model.intercept_), 3),
            })

        rh, ra = ratings.get(g[3], 0.0), ratings.get(g[4], 0.0)
        out[g[0]] = {
            "srs_home": round(rh, 4),
            "srs_away": round(ra, 4),
            "srs_diff": round(rh - ra, 4),
        }

    if info_out is not None:
        top = sorted(ratings.items(), key=lambda kv: -kv[1])[:10]
        info_out.update({
            "n_games": len(games),
            "n_refits": len(refits),
            "srs_alpha": SRS_ALPHA,
            "margin_clip": MARGIN_CLIP,
            "refits": refits,
            "final_top10": [(int(t), round(v, 2)) for t, v in top],
        })
    if verbose:
        print(f"srs built for {len(out)} games; {len(refits)} refits")
    return out

"""Season-sim core (sport-neutral, map step 12).

Pure pieces of the season simulator, verbatim from ``backend/sim/season.py``
(map R4: expressions and roundings unchanged — the frozen ledger must
reproduce): hist-summary reducers, the block-bootstrap mean-Brier CI, the
per-match sign test, and the declared-line paper-trading leg (EvModel).

The arm orchestration (signals, ensemble updates, policy, calibration refit)
stays tennis-side: it is sport state, not sim mechanics.
``FractionalKelly`` is ``core.odds.kelly_fraction`` (core since phase 0).
"""

from __future__ import annotations

import math
from collections import defaultdict


def _hist_key(h) -> tuple:
    """7-tuple rows (date, surface, brier, acc, p, y, tourney); market rows are
    3-tuples (date, brier, acc) — index accordingly (pre-season.py crash fix)."""
    return (h[2], h[3]) if len(h) > 6 else (h[1], h[2])


def summ(hist) -> dict:
    s = [h for h in hist if h is not None]
    n = len(s)
    if not n:
        return {"n": 0, "acc": 0, "brier": 0}
    br, ac = zip(*(_hist_key(h) for h in s))
    return {"n": n,
            "acc": round(sum(ac) / n, 4),
            "brier": round(sum(br) / n, 4)}


def per_surface(hist) -> dict:
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


def conf_table(hist) -> dict:
    s = [h for h in hist if h is not None]
    out = {}
    for thresh in (0.55, 0.60, 0.65):
        sel = [h for h in s if max(h[4], 1 - h[4]) >= thresh]
        out[f">={thresh}"] = {"n": len(sel),
                              "acc": round(sum(h[3] for h in sel) / len(sel), 4) if sel else 0,
                              "brier": round(sum(h[2] for h in sel) / len(sel), 4) if sel else 0}
    return out


def block_ci(ha, hb, label: str, n_boot: int = 2000, seed: int = 7) -> dict:
    """Block-bootstrap 95% CI on mean Brier(a) - mean Brier(b), negative favors a.

    Blocks are (year-month, tournament): resampling matches would pretend
    serial form/player dependence away. Deterministic seed.
    """
    import random as _random
    ha = [h for h in ha if h is not None]
    hb = [h for h in hb if h is not None]
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
    return {"arms": label, "n": n, "blocks": len(keys),
            "mean_diff": round(mean, 5), "ci95": [round(lo, 5), round(hi, 5)],
            "verdict": "a better" if hi < 0 else
                       ("b better" if lo > 0 else "no significant difference")}


def sign_test(ha, hb, a_name: str = "a", b_name: str = "b") -> dict:
    """Paired sign test on PER-MATCH Brier (labeled: this is NOT mean-Brier
    evidence — wins-small-often/loses-big-rarely passes it while losing the mean)."""
    pairs = [(x, z) for x, z in zip(ha, hb) if x is not None and z is not None]
    wins = sum(1 for x, z in pairs if x[2] < z[2])
    ties = sum(1 for x, z in pairs if x[2] == z[2])
    n_eff = len(pairs) - ties
    z = ((wins - n_eff / 2) / math.sqrt(n_eff / 4)) if n_eff else 0.0
    return {"n": len(pairs), "wins": wins, "ties": ties,
            "z": round(z, 2),
            "test": "sign test on per-match Brier differences (median signal, not mean evidence)",
            "verdict": f"{a_name} learns" if z > 1.96 else
                       (f"{b_name} better" if z < -1.96 else "no significant difference")}


class EvModel:
    """Declared-line paper-trading leg (P1): EV + fractional Kelly vs a FAIR
    line the caller declares (market close when present, else the Elo arm's
    fair price). Outcomes are LABELED skill-vs-line, not real profit.

    Verbatim math from backend/sim/season.py (map step 12).
    """
    LINE_MIN = 0.03
    LINE_MAX = 0.97

    def line_for(self, market_p: float | None, elo_p: float) -> tuple[float, str]:
        """(line_a, source): "market" when the close covers the match, else "elo"."""
        if market_p is not None:
            return min(self.LINE_MAX, max(self.LINE_MIN, market_p)), "market"
        return min(self.LINE_MAX, max(self.LINE_MIN, elo_p)), "elo"

    def score_row(self, p_ad: float, line_a: float, y: int) -> dict:
        """One paper row: pick, edge vs the line, half-Kelly stake, P&L."""
        from core.odds import kelly_fraction
        pick_a = p_ad >= 0.5
        p_pick = p_ad if pick_a else 1 - p_ad
        line_pick = line_a if pick_a else 1 - line_a
        odds = 1.0 / line_pick
        edge = p_pick * odds - 1.0
        conf = max(p_ad, 1 - p_ad)
        stake = kelly_fraction(p_pick, odds)  # half-Kelly units
        won = (p_ad >= 0.5) == (y == 1)
        profit = stake * (odds - 1.0) if won else -stake
        return {"pick_a": pick_a, "p_pick": p_pick, "line_pick": line_pick,
                "odds": odds, "edge": edge, "conf": conf, "stake": stake,
                "won": won, "profit": profit}

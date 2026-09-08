"""Opponent-adjusted serve/return point ratings (Barnett-Clarke style).

- Time-decayed accumulators (half-life 150d) per player per surface.
- Efron-Morris-style shrinkage toward the surface mean by sample size.
- Matchup: fAB = mean + (serveA - mean) - (returnB - mean_return).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

HALF_LIFE_DAYS = 150
SHRINK_K = 400.0  # points of pseudo-sample at the mean


def _ord(yyyymmdd: int) -> int | None:
    try:
        s = f"{int(yyyymmdd):08d}"
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8])).toordinal()
    except (ValueError, TypeError):
        return None


class PointRatings:
    def __init__(self, half_life_days: float = HALF_LIFE_DAYS):
        self.half_life = half_life_days
        # (name, surface) -> [serve_won, serve_tot, ret_won, ret_tot, last_ord]
        self.acc: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, None])
        # surface -> [serve_won, serve_tot, ret_won, ret_tot] (tour means)
        self.tour: dict = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])

    def _decay(self, key, now_ord: int | None):
        cell = self.acc[key]
        last = cell[4]
        if last is not None and now_ord is not None and now_ord > last:
            f = 0.5 ** ((now_ord - last) / self.half_life)
            for i in range(4):
                cell[i] *= f
        cell[4] = now_ord

    def record(self, match: dict) -> None:
        if match.get("walkover"):
            return
        w, l, surf = match["winner"], match["loser"], match["surface"]
        now = _ord(match["date"])
        ws, ls = match["w_stats"], match["l_stats"]
        w_sv, w_sw = (ws.get("svpt") or 0), (ws.get("1stWon") or 0) + (ws.get("2ndWon") or 0)
        l_sv, l_sw = (ls.get("svpt") or 0), (ls.get("1stWon") or 0) + (ls.get("2ndWon") or 0)
        if not w_sv or not l_sv:
            return
        # winner's serve + return; loser's serve + return
        for key, sw, st, rw, rt in (
                ((w, surf), w_sw, w_sv, l_sv - l_sw, l_sv),
                ((l, surf), l_sw, l_sv, w_sv - w_sw, w_sv)):
            self._decay(key, now)
            c = self.acc[key]
            c[0] += sw
            c[1] += st
            c[2] += rw
            c[3] += rt
        t = self.tour[surf]
        t[0] += w_sw + l_sw
        t[1] += w_sv + l_sv
        t[2] += (l_sv - l_sw) + (w_sv - w_sw)
        t[3] += l_sv + w_sv

    def _shrunk(self, key) -> tuple[float, float, float]:
        c = self.acc[key]
        surf = key[1]
        t = self.tour[surf]
        mean_s = (t[0] / t[1]) if t[1] else 0.64
        mean_r = (t[2] / t[3]) if t[3] else 0.36
        n_s, n_r = c[1], c[3]
        raw_s = (c[0] / n_s) if n_s else mean_s
        raw_r = (c[2] / n_r) if n_r else mean_r
        s = (n_s * raw_s + SHRINK_K * mean_s) / (n_s + SHRINK_K)
        r = (n_r * raw_r + SHRINK_K * mean_r) / (n_r + SHRINK_K)
        return s, r, n_s + n_r

    def matchup(self, a: str, b: str, surface: str) -> dict:
        """Point-win probs on own serve: fAB (A serving vs B), fBA."""
        sA, rA, nA = self._shrunk((a, surface))
        sB, rB, nB = self._shrunk((b, surface))
        t = self.tour[surface]
        mean_s = (t[0] / t[1]) if t[1] else 0.64
        mean_r = (t[2] / t[3]) if t[3] else 0.36
        fAB = min(0.95, max(0.05, mean_s + (sA - mean_s) - (rB - mean_r)))
        fBA = min(0.95, max(0.05, mean_s + (sB - mean_s) - (rA - mean_r)))
        return {"fAB": fAB, "fBA": fBA, "serve_edge": (sA - sB),
                "sample": min(nA, nB)}

    def snapshot(self) -> dict:
        return {"half_life": self.half_life,
                "acc": {k: list(v) for k, v in self.acc.items()},
                "tour": {k: list(v) for k, v in self.tour.items()}}

    def restore(self, snap: dict) -> None:
        from collections import defaultdict as _dd
        self.half_life = snap.get("half_life", self.half_life)
        self.acc = _dd(lambda: [0.0, 0.0, 0.0, 0.0, None],
                       {tuple(k): list(v) for k, v in snap.get("acc", {}).items()})
        self.tour = _dd(lambda: [0.0, 0.0, 0.0, 0.0],
                        {k: list(v) for k, v in snap.get("tour", {}).items()})

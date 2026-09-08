"""Hedge ensemble with per-surface weight tables.

Upsets on clay shift clay weights without touching hard-court weights.
Sleeping (abstaining) signals keep their exact share.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "weights.json"
FLOOR = 0.02

# Grass/carpet see ~1/5 the matches of hard: same eta overfits them.
DEFAULT_ETA_BY = {"hard": 0.10, "clay": 0.07, "grass": 0.05, "carpet": 0.05}

DEFAULT_W = {
    "elo_surface": 0.22, "elo_overall": 0.06, "h2h": 0.08, "form": 0.08,
    "serve": 0.06, "rest": 0.03, "rank": 0.06, "experience": 0.02,
    "markov": 0.08, "gbm": 0.10, "market": 0.19, "news": 0.02,
}


class Ensemble:
    def __init__(self, weights: dict | None = None,
                 surfaces: dict[str, dict] | None = None, eta: float = 0.1,
                 eta_by: dict[str, float] | None = None):
        base = dict(weights or DEFAULT_W)
        self.global_w = base
        self.surf: dict[str, dict] = {s: dict(t) for s, t in (surfaces or {}).items()}
        self.eta = eta
        # Explicit eta wins (tests, experiments); otherwise per-surface defaults.
        if eta_by is not None:
            self.eta_by = dict(eta_by)
        elif eta != 0.1:
            self.eta_by = {}
        else:
            self.eta_by = dict(DEFAULT_ETA_BY)
        self.history: list[dict] = []

    def table(self, surface: str) -> dict:
        return self.surf.setdefault(surface, dict(self.global_w))

    def predict(self, probs: dict[str, float | None], surface: str = "hard") -> dict:
        w = self.table(surface)
        avail = {k: p for k, p in probs.items() if p is not None}
        if not avail:
            return {"p_a": 0.5, "weights_used": {}, "abstained": True}
        tot = sum(w.get(k, 0) for k in avail)
        wu = {k: (w.get(k, 0) / tot if tot else 1 / len(avail)) for k in avail}
        return {"p_a": sum(wu[k] * avail[k] for k in avail),
                "weights_used": {k: round(v, 3) for k, v in wu.items()},
                "abstained": False}

    @staticmethod
    def brier(p: float, outcome: int) -> float:
        return (p - outcome) ** 2

    def _step(self, w: dict, probs: dict, y: int, eta: float) -> dict:
        awake = [k for k, p in probs.items() if p is not None]
        mass = sum(w.get(k, 0) for k in awake)
        losses = {}
        for k in awake:
            losses[k] = self.brier(probs[k], y)  # type: ignore[index]
            w[k] = w.get(k, 0.05) * math.exp(-eta * losses[k])
        d = sum(w[k] for k in awake)
        if d > 0 and mass > 0:
            for k in awake:
                w[k] *= mass / d
        for k in awake:  # floor so signals can always come back
            w[k] = max(w[k], FLOOR)
        fsum = sum(w[k] for k in awake)
        if fsum > 0:
            for k in awake:
                w[k] *= mass / fsum
        return losses

    def update(self, probs: dict[str, float | None], winner_is_a: bool,
               surface: str = "hard") -> dict:
        y = 1 if winner_is_a else 0
        eta = self.eta_by.get(surface, self.eta)
        losses = self._step(self.table(surface), probs, y, eta)
        self._step(self.global_w, probs, y, self.eta * 0.3)  # slow global drift
        snap = {"surface": surface,
                "weights": {k: round(v, 4) for k, v in self.table(surface).items()},
                "losses": {k: round(v, 4) for k, v in losses.items()}}
        self.history.append(snap)
        return snap

    def save(self) -> None:
        from backend.core.io import atomic_write_json
        atomic_write_json(WEIGHTS_PATH, {"global": self.global_w, "surfaces": self.surf,
                                         "eta": self.eta, "eta_by": self.eta_by})

    @classmethod
    def load(cls) -> "Ensemble":
        def coerce(saved: dict | None) -> dict:
            base = dict(DEFAULT_W)
            for k, v in (saved or {}).items():
                if k in base:
                    base[k] = v
            s = sum(base.values()) or 1.0
            return {k: v / s for k, v in base.items()}

        try:
            d = json.loads(WEIGHTS_PATH.read_text())
            if "global" in d:
                surf = {s: coerce(t) for s, t in (d.get("surfaces") or {}).items()}
                return cls(coerce(d["global"]), surf, d.get("eta", 0.1), d.get("eta_by"))
            # legacy file with old signal names -> map onto new keys
            legacy = d.get("weights", {})
            base = dict(DEFAULT_W)
            if "elo" in legacy:
                base["elo_surface"] = legacy["elo"]
            for k in ("market", "news"):
                if k in legacy:
                    base[k] = legacy[k]
            return cls(base, None, d.get("eta", 0.1))
        except Exception:
            return cls()

"""Hedge ensemble engine (sport-neutral core, map step 5).

Exponentiated-gradient weight learning over a signal registry: each awake
signal keeps its share of mass, losing signals shrink by ``exp(-eta * brier)``,
a floor keeps every signal able to come back, and sleeping (abstaining) signals
keep their exact share.

Adapter contract: pass your own weight registry, per-context eta defaults and
weight path (tennis: ``backend.model.ensemble`` keeps ``DEFAULT_W``,
``DEFAULT_ETA_BY``, the ``weights.json`` save/load and the legacy loader).
"""

from __future__ import annotations

import math

FLOOR = 0.02


class HedgeEnsemble:
    def __init__(self, weights: dict, surfaces: dict[str, dict] | None = None,
                 eta: float = 0.1, eta_by: dict[str, float] | None = None,
                 default_eta_by: dict[str, float] | None = None):
        base = dict(weights)
        self.global_w = base
        self.surf: dict[str, dict] = {s: dict(t) for s, t in (surfaces or {}).items()}
        self.eta = eta
        # Explicit eta wins (tests, experiments); otherwise per-surface defaults.
        if eta_by is not None:
            self.eta_by = dict(eta_by)
        elif eta != 0.1:
            self.eta_by = {}
        else:
            self.eta_by = dict(default_eta_by or {})
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

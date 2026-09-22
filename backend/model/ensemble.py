"""Tennis ensemble wiring: signal registry defaults + weight persistence.

The Hedge learning engine lives in ``core.ensemble`` (map step 5).
Tennis-specific parts stay here: the published ``DEFAULT_W`` /
``DEFAULT_ETA_BY`` literals (frozen evidence — the sim and docs quote them),
``weights.json`` save/load, and the legacy signal-name loader.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.ensemble import FLOOR, HedgeEnsemble  # noqa: F401  (FLOOR historical)

WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "weights.json"

# Grass/carpet see ~1/5 the matches of hard: same eta overfits them.
DEFAULT_ETA_BY = {"hard": 0.10, "clay": 0.07, "grass": 0.05, "carpet": 0.05}

DEFAULT_W = {
    "elo_surface": 0.22, "elo_overall": 0.06, "h2h": 0.08, "form": 0.08,
    "serve": 0.06, "rest": 0.03, "rank": 0.06, "experience": 0.02,
    "markov": 0.08, "gbm": 0.10, "market": 0.19, "news": 0.02,
}


class Ensemble(HedgeEnsemble):
    def __init__(self, weights: dict | None = None,
                 surfaces: dict[str, dict] | None = None, eta: float = 0.1,
                 eta_by: dict[str, float] | None = None):
        super().__init__(dict(weights or DEFAULT_W), surfaces, eta, eta_by,
                         default_eta_by=DEFAULT_ETA_BY)

    def save(self) -> None:
        from core.io import atomic_write_json
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

"""Contextual bandit for post/pass + stake gating (sport-neutral, map step 6).

Learns from settled picks. Context bucket = (surface, edge_bin, conf_bin).
Per bucket/arm we track (n, sum, sumsq) of unit-profit rewards and decide by
UCB. Pass always scores exactly 0, so the policy only posts where posting has
paid. Starts uniform: post iff edge > 0.

Paths are injected: adapters bind their own store path (tennis:
``backend.policy.bandit.PATH`` -> ``data/policy.json``).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

C = 0.7  # UCB exploration constant (reward units ~ +-1..3)

def bucket(surface: str, edge: float, conf: float) -> str:
    eb = "neg" if edge < 0 else ("thin" if edge < 0.03 else ("good" if edge < 0.08 else "big"))
    cb = "low" if conf < 0.55 else ("mid" if conf < 0.65 else "high")
    return f"{surface or 'na'}|{eb}|{cb}"

def _blank():
    return {"post": [0, 0.0, 0.0], "pass": [0, 0.0, 0.0]}

class Policy:
    #: Adapters bind their default store path (tennis: data/policy.json).
    DEFAULT_PATH: Path | None = None

    def __init__(self, table: dict | None = None, path: Path | None = None):
        self.t = table or {}
        self._path = Path(path) if path is not None else self.DEFAULT_PATH

    def _arm(self, b: str, arm: str) -> list:
        return self.t.setdefault(b, _blank())[arm]

    @staticmethod
    def _mean(arm) -> float:
        return arm[1] / arm[0] if arm[0] else 0.0

    def decide(self, surface: str, edge: float, conf: float) -> dict:
        b = bucket(surface, edge, conf)
        post = self.t.get(b, _blank())["post"]
        n = post[0]
        if n < 2:
            action = "post" if edge > 0 else "pass"  # prior: trust +EV
            return {"action": action, "bucket": b, "reason": "prior (few samples)",
                    "n": n}
        mean = post[1] / n
        ucb = mean + C * math.sqrt(math.log(n + 1) / n)
        action = "post" if ucb > 0 else "pass"
        return {"action": action, "bucket": b,
                "reason": f"post UCB {ucb:+.2f} (mean {mean:+.2f}, n={n}) vs pass 0.00",
                "n": n}

    def update(self, b: str, action: str, reward: float) -> None:
        arm = self._arm(b, action)
        arm[0] += 1
        arm[1] += reward
        arm[2] += reward * reward

    def save(self, path: Path | None = None) -> None:
        from core.io import atomic_write_json
        target = Path(path) if path is not None else self._path
        if target is None:
            raise ValueError("policy.save: no store path configured (pass path=...)")
        atomic_write_json(target, self.t)

    @classmethod
    def load(cls, path: Path | None = None) -> "Policy":
        target = Path(path) if path is not None else cls.DEFAULT_PATH
        try:
            return cls(json.loads(Path(target).read_text()))
        except Exception:
            return cls()

def reward_of(result: str, odds_taken: float) -> float:
    if result == "win":
        return odds_taken - 1.0
    if result == "loss":
        return -1.0
    return 0.0

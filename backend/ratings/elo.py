"""Surface-aware Elo for men's singles. Overall + per-surface ratings with blending."""

from __future__ import annotations

from dataclasses import dataclass, field

SURFACES = ("hard", "clay", "grass", "carpet")


@dataclass
class PlayerRating:
    name: str
    overall: float = 1500.0
    surf: dict[str, float] = field(default_factory=lambda: {s: 1500.0 for s in SURFACES})
    surf_n: dict[str, int] = field(default_factory=lambda: {s: 0 for s in SURFACES})
    n: int = 0


class SurfaceElo:
    def __init__(self, base_k: float = 24.0, surface_k: float = 32.0,
                 blend_threshold: int = 50, bo5_boost: float = 1.15,
                 retire_fraction: float = 0.5):
        self.base_k = base_k
        self.surface_k = surface_k
        self.blend_threshold = blend_threshold
        self.bo5_boost = bo5_boost
        self.retire_fraction = retire_fraction
        self.players: dict[str, PlayerRating] = {}

    def get(self, name: str) -> PlayerRating:
        return self.players.setdefault(name, PlayerRating(name))

    @staticmethod
    def expected(ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))

    def blended(self, p: PlayerRating, surface: str) -> float:
        n = p.surf_n.get(surface, 0)
        w = n / (n + self.blend_threshold)
        return w * p.surf.get(surface, 1500.0) + (1 - w) * p.overall

    def predict(self, a: str, b: str, surface: str = "hard") -> dict:
        pa, pb = self.get(a), self.get(b)
        ra, rb = self.blended(pa, surface), self.blended(pb, surface)
        prob_a = self.expected(ra, rb)
        return {"a": a, "b": b, "surface": surface,
                "rating_a": round(ra, 1), "rating_b": round(rb, 1),
                "prob_a": prob_a, "prob_b": 1 - prob_a,
                "elo_diff": round(ra - rb, 1)}

    def update(self, winner: str, loser: str, surface: str = "hard",
               level_mult: float = 1.0, best_of: int = 3,
               retirement: bool = False, walkover: bool = False) -> dict | None:
        if walkover:
            return None  # no contest, no rating change
        k_mult = level_mult * (self.bo5_boost if best_of == 5 else 1.0)
        if retirement:
            k_mult *= self.retire_fraction
        pw, pl = self.get(winner), self.get(loser)
        exp_w = self.expected(pw.overall, pl.overall)
        ok = self.base_k * k_mult
        pw.overall += ok * (1 - exp_w)
        pl.overall += ok * (0 - (1 - exp_w))
        exp_ws = self.expected(pw.surf[surface], pl.surf[surface])
        sk = self.surface_k * k_mult
        pw.surf[surface] += sk * (1 - exp_ws)
        pl.surf[surface] += sk * (0 - (1 - exp_ws))
        pw.n += 1
        pl.n += 1
        pw.surf_n[surface] += 1
        pl.surf_n[surface] += 1
        return {"exp_w": round(exp_w, 4), "k": round(ok, 2)}

    def table(self, surface: str | None = None, top: int = 20) -> list[dict]:
        rows = []
        for p in self.players.values():
            if p.n < 5:
                continue
            r = self.blended(p, surface) if surface else p.overall
            rows.append({"name": p.name, "rating": round(r, 1), "matches": p.n})
        return sorted(rows, key=lambda x: -x["rating"])[:top]

    def snapshot(self) -> dict:
        return {name: {"overall": p.overall, "surf": dict(p.surf),
                       "surf_n": dict(p.surf_n), "n": p.n}
                for name, p in self.players.items()}

    def restore(self, snap: dict) -> None:
        for name, s in snap.items():
            p = PlayerRating(name, overall=s["overall"], n=s.get("n", 0))
            p.surf.update(s.get("surf", {}))
            p.surf_n.update(s.get("surf_n", {}))
            self.players[name] = p

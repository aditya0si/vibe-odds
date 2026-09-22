"""Player-name normalization for tennis: alias registry + history familiarity.

The sport-neutral pipeline (accents/initials/particles/title-case) lives in
``core.names`` (map step 4). Tennis-specific parts stay here: the hand-curated
alias registry and ``familiarity()``, which reads the tennis rating tables
(Elo, form, point ratings) to say how much history backs a name.

Every rating table (Elo, H2H, form, point ratings) is keyed on names, so a
book feed spelling ("C. Alcaraz") that differs from history ("Carlos
Alcaraz") silently fragments a player into a fresh 1500 Elo nobody. ALL
lookups of player history go through ``canonical()``, and ``familiarity()``
tells callers when a name has too little history to trust.
"""

from __future__ import annotations

from core.names import canonical as _canonical

# Hand-curated aliases: feed spelling -> canonical history spelling.
# Add rows here when the unknown-player flag fires on a real alias.
ALIASES = {
    "C Alcaraz": "Carlos Alcaraz",
    "J Sinner": "Jannik Sinner",
    "N Djokovic": "Novak Djokovic",
    "D Medvedev": "Daniil Medvedev",
    "R Nadal": "Rafael Nadal",
    "C Ruud": "Casper Ruud",
    "S Tsitsipas": "Stefanos Tsitsipas",
    "A Zverev": "Alexander Zverev",
    "T Fritz": "Taylor Fritz",
    "B Shelton": "Ben Shelton",
    # observed history-file variants (pre-canonicalization rows)
    "Botic Van De Zandschulp": "Botic van de Zandschulp",
}


def canonical(name: str) -> str:
    """Map any feed spelling to the canonical history key (tennis registry)."""
    return _canonical(name, ALIASES)


def familiarity(name: str, elo=None, form=None, points=None, surface: str = "hard") -> dict:
    """How much history backs this (canonical) name? Never raises."""
    key = canonical(name)
    try:
        elo_n = elo.get(key).n if elo is not None else 0
    except Exception:
        elo_n = 0
    try:
        career_n = (form.played.get(key, 0) if form is not None else 0)
    except Exception:
        career_n = 0
    try:
        sample = 0
        if points is not None:
            sample = points.matchup(key, key, surface).get("sample", 0)
    except Exception:
        sample = 0
    n = min(elo_n or 0, career_n or 0)
    return {"name": name, "canonical": key, "matches": n,
            "point_sample": sample, "known": n >= 5}

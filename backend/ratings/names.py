"""Player-name normalization: one spelling in, one canonical key out.

Every rating table (Elo, H2H, form, point ratings) is keyed on names, so a
book feed spelling ("C. Alcaraz") that differs from history ("Carlos
Alcaraz") silently fragments a player into a fresh 1500 Elo nobody. This
module is the single choke point: ALL lookups of player history go through
``canonical()``, and ``familiarity()`` tells callers when a name has too
little history to trust.

Tennis-only by design.
"""

from __future__ import annotations

import re
import unicodedata

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

_WS = re.compile(r"\s+")
_INITIAL = re.compile(r"^([A-Z])\.?\s+(.*)$")

# Particles stay lowercase mid-name ("Botic van de Zandschulp", not "Van De").
PARTICLES = {"van", "de", "der", "den", "ter", "ten", "von", "da", "di", "del",
             "della", "la", "le", "du", "dos", "das", "do", "el", "al", "bin"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def canonical(name: str) -> str:
    """Map any feed spelling to the canonical history key."""
    if not name:
        return name
    n = _strip_accents(name).strip()
    n = _WS.sub(" ", n)
    m = _INITIAL.match(n)
    if m:
        n = f"{m.group(1)} {m.group(2)}"  # "C. Alcaraz" -> "C Alcaraz"
    # stable keys: title-case, except mid-name particles
    words = n.split(" ")
    out = []
    for i, w in enumerate(words):
        if i > 0 and w.lower() in PARTICLES:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower() if w else w)
    n = " ".join(out)
    return ALIASES.get(n, n)


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

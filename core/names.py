"""Name canonicalization pipeline (sport-neutral core, map step 4).

One spelling in, one canonical key out. Every rating table is keyed on names,
so a feed spelling ("C. Alcaraz") that differs from history ("Carlos Alcaraz")
silently fragments a competitor into a fresh unknown. This pipeline is the
sport-neutral choke point: accents, whitespace, initials, mid-name particles,
title-casing.

Sport adapters add their own curated alias registry on top (tennis:
``backend.ratings.names.ALIASES``) and their own history-depth checks
(tennis: ``familiarity()``).
"""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_INITIAL = re.compile(r"^([A-Z])\.?\s+(.*)$")

# Particles stay lowercase mid-name ("Botic van de Zandschulp", not "Van De").
PARTICLES = {"van", "de", "der", "den", "ter", "ten", "von", "da", "di", "del",
             "della", "la", "le", "du", "dos", "das", "do", "el", "al", "bin"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def canonical(name: str, aliases: dict[str, str] | None = None) -> str:
    """Map any feed spelling to the canonical history key.

    ``aliases`` is the sport's curated registry (feed spelling -> canonical
    history spelling); add rows there when the unknown flag fires on a real
    alias. Pass None (default) for registry-free canonicalization.
    """
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
    return (aliases or {}).get(n, n)

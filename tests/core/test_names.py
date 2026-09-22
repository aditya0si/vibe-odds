"""Name pipeline core: registry-free canonicalization + alias injection (map step 4)."""

from __future__ import annotations

from core.names import canonical


def test_pipeline_without_registry():
    """Accents, initials, particles, title-case — no sport registry involved."""
    assert canonical("  Novak  DJOKOVIC ") == "Novak Djokovic"
    assert canonical("Botic Van De Zandschulp") == "Botic van de Zandschulp"
    assert canonical("Jo-Wilfried Tsonga") == "Jo-wilfried Tsonga"  # title-case contract
    assert canonical("") == ""
    assert canonical("Nadal") == "Nadal"


def test_registry_injection():
    """Adapters plug in their own alias map; unknown names fall through."""
    reg = {"C Alcaraz": "Carlos Alcaraz"}
    assert canonical("C. Alcaraz", reg) == "Carlos Alcaraz"
    assert canonical("C. Alcaraz") == "C Alcaraz"        # no registry -> no alias
    assert canonical("J Sinner", reg) == "J Sinner"       # registry miss -> pipeline key
    assert canonical("carlos ALCARAZ", reg) == "Carlos Alcaraz"

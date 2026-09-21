"""Sport-agnostic core of the prediction engine.

Everything in this package must work for any sport. Sport-specific code lives
under ``sports/<sport>/`` and may only *use* this package — never the other way
round. The first tenants are:

* ``core.io``    — atomic JSON persistence with backups + rollback
* ``core.odds``  — pure odds math (conversion, no-vig, EV, arbitrage)

Extracted from ``backend/core/`` (tennis-only tree) as step 1 of the refactor
documented in ``nba-prediction-research/05-vibe-odds-refactor-map.md``: the
tennis test suite and every frozen evidence hash must stay unchanged by moves
into this package.
"""

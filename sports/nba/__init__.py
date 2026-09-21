"""NBA adapter: the second sport on the shared core.

Layout:
  sports/nba/db/      schema + build/status tooling
  sports/nba/ingest/  nba_api and odds ingestors (idempotent, resumable)
  sports/nba/data/    sqlite DB, parquet mirrors, raw cache (git-ignored)

Rule from the refactor map: this package may only *use* ``core/``. Anything it
needs that core lacks is written here first and promoted to core only when the
tennis adapter needs it too.
"""

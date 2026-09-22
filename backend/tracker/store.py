"""Back-compat shim — the pick store moved to ``core.tracker`` (map step 6).

Old import paths keep working during the migration window; importers are being
rewired to ``core.tracker`` and this shim will be deleted at the end.
Note: monkeypatch ``core.tracker.DB_PATH`` (or pass ``path=``) to redirect —
patching this shim's ``DB_PATH`` name does not move the store.
"""

from core.tracker import (  # noqa: F401
    DB_PATH,
    SCHEMA,
    _db,
    calibration,
    get_pick,
    log_pick,
    settle_pick,
    stats,
    suggest_ev_threshold,
)

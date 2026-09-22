"""Back-compat shim — the module moved to ``core.calibration`` (map step 2).

Old import paths keep working during the migration window; importers are being
rewired to ``core.calibration`` and this shim will be deleted at the end.
"""

from core.calibration import (  # noqa: F401
    CAL_PATH,
    apply_isotonic,
    ece,
    fit_isotonic,
    load_all,
    load_fn,
    save_fn,
)

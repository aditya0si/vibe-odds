"""Core calibration tables: round-trip, missing-surface tolerance, injection.

Moved here from tests/test_reliability.py (map step 2) and extended to cover
the injected-``path`` API that keeps adapters off shared evidence.
"""

from __future__ import annotations

import backend.model.calibrate as SHIM
import core.calibration as CAL


def test_calibration_v2_roundtrip(tmp_path):
    """Per-surface tables round-trip; unknown surfaces return None, not crash."""
    p = tmp_path / "cal.json"
    CAL.save_fn([(0.5, 0.6)], "hard", path=p)
    CAL.save_fn([(0.5, 0.4)], "clay", path=p)
    assert CAL.load_fn("hard", path=p) == [(0.5, 0.6)]
    assert CAL.load_fn("clay", path=p) == [(0.5, 0.4)]
    assert CAL.load_fn("grass", path=p) is None  # no table -> no crash
    assert set(CAL.load_all(path=p)) == {"hard", "clay"}


def test_cal_path_default_still_monkeypatchable(tmp_path, monkeypatch):
    """Back-compat: monkeypatching CAL_PATH keeps working (pre-injection API)."""
    monkeypatch.setattr(CAL, "CAL_PATH", tmp_path / "cal.json")
    CAL.save_fn([(0.5, 0.6)], "hard")
    assert CAL.load_fn("hard") == [(0.5, 0.6)]
    assert CAL.load_fn("hard", path=CAL.CAL_PATH) == [(0.5, 0.6)]


def test_legacy_single_file_layout(tmp_path):
    """Pre-v2 files were a bare list of pairs; readers must still understand them."""
    p = tmp_path / "cal.json"
    p.write_text("[[0.5, 0.62]]")
    assert CAL.load_fn(path=p) == [(0.5, 0.62)]
    assert CAL.load_all(path=p) == {}


def test_old_import_path_shim_reexports():
    """backend.model.calibrate must alias core.calibration during migration."""
    assert SHIM.fit_isotonic is CAL.fit_isotonic
    assert SHIM.save_fn is CAL.save_fn
    assert SHIM.ece is CAL.ece

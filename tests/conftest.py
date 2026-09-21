"""Test sandbox for frozen evidence.

Everything under ``data/`` is *published evidence*: the README, the site and
``backend/api/analytics.py`` quote numbers derived from those files, and
``tests/evidence/test_frozen_evidence.py`` hashes them. A test run must not be
able to change them.

Three writers used to touch ``data/`` during the suite:

* ``backend.model.serve._log_verdict``  -> ``data/verdicts.jsonl``
* ``backend.providers.the_odds_api.to_books_map`` -> ``data/dropped_quotes.jsonl``
* ``backend.sim.freeze.freeze``         -> ``data/sim_frozen/*``

Each routes through a module-level path constant, so this conftest redirects
them into a session temp dir before any test runs, and a second fixture fails
the session if anything tracked under ``data/`` changes while tests run.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _tracked_evidence() -> list[str]:
    """Paths of git-tracked files under data/ (published evidence)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "data"],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        paths = [p for p in out.split("\0") if p]
        if paths:
            return paths
    except Exception:
        pass
    # Not a git checkout: fall back to every file under data/.
    return [str(p.relative_to(ROOT)) for p in sorted(DATA.rglob("*")) if p.is_file()]


def _hashes(rels: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in rels:
        p = ROOT / rel
        if p.exists():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture(scope="session", autouse=True)
def evidence_sandbox(tmp_path_factory):
    """Point every evidence writer at a session tmp dir."""
    sandbox = tmp_path_factory.mktemp("evidence")

    import backend.model.serve as serve
    import backend.providers.the_odds_api as odds_api
    import backend.sim.freeze as freeze_mod

    serve.VERDICTS_LOG = sandbox / "verdicts.jsonl"
    odds_api.DROPPED_LOG = sandbox / "dropped_quotes.jsonl"
    freeze_mod.FROZEN = sandbox / "sim_frozen"

    return sandbox


@pytest.fixture(scope="session", autouse=True)
def data_dir_is_frozen(evidence_sandbox):
    """Canary: tracked evidence must be byte-identical after the suite."""
    rels = _tracked_evidence()
    before = _hashes(rels)
    yield
    after = _hashes(rels)
    changed = sorted(
        set(before) ^ set(after)
        | {k for k in before.keys() & after.keys() if before[k] != after[k]}
    )
    assert not changed, (
        "the test suite mutated tracked evidence under data/: "
        f"{changed}. Redirect the writer to tmp_path instead "
        "(see tests/conftest.py)."
    )

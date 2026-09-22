"""Tennis policy wiring: the bandit bound to data/policy.json.

The bandit engine (bucket, UCB decide, reward_of) lives in ``core.policy``
(map step 6). Tennis-specific: the store path binding.
"""

from __future__ import annotations

from pathlib import Path

from core.policy import C, Policy as _Policy, _blank, bucket, reward_of  # noqa: F401

PATH = Path(__file__).resolve().parents[2] / "data" / "policy.json"


class Policy(_Policy):
    """Bandit bound to the tennis policy store (data/policy.json)."""
    DEFAULT_PATH = PATH

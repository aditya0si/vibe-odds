"""SportAdapter protocol (sport-neutral, map step 11).

One site engine, per-sport configs: the page shell reads window.__ADAPTER__
(injected by ``core.site`` at serve time) instead of hardcoding titles,
surfaces, groups or routes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SportAdapter(Protocol):
    """Everything the site shell needs to render one sport."""

    title: str
    short: str

    @property
    def config(self) -> dict:
        """JSON-serializable page config — becomes window.__ADAPTER__."""
        ...

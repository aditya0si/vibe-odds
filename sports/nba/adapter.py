"""NBA SportAdapter: the page config injected as window.__ADAPTER__."""

from __future__ import annotations


class NbaAdapter:
    title = "NBA Winner Formula — evidence"
    short = "nba"

    @property
    def config(self) -> dict:
        from sports.nba.evidence import TABS
        return {
            "title": self.title,
            "sport": self.short,
            "tabs": TABS,
            "api": {"site_meta": "/api/site_meta", "board": "/api/board",
                    "predict": "/api/predict", "model": "/api/model",
                    "experiments": "/api/experiments", "teams": "/api/teams",
                    "reliability": "/api/reliability", "sim": "/api/sim",
                    "stats": "/api/stats", "live_log": "/api/live/log"},
        }

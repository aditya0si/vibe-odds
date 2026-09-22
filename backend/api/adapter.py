"""Tennis SportAdapter (map step 11): the page config injected as
window.__ADAPTER__ — titles, surfaces, groups and routes the SPA reads instead
of hardcoding them."""

from __future__ import annotations


class TennisAdapter:
    title = "Vibe-Odds — ATP tennis analytics"
    short = "tennis"

    @property
    def config(self) -> dict:
        return {
            "title": self.title,
            "sport": self.short,
            "surfaces": ["hard", "clay", "grass"],
            "groups": ["usopen", "tennis"],
            "api": {"health": "/api/health", "board": "/api/board",
                    "players": "/api/players", "model": "/api/model",
                    "stats": "/api/stats"},
        }

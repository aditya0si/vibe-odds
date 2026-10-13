"""Tennis SportAdapter (map step 11): the page config injected as
window.__ADAPTER__ — titles, surfaces, groups and routes the SPA reads instead
of hardcoding them.

Court Evidence extends the route dictionary explicitly: predict/reliability/
player_detail were served by the API but were NOT injected before this change.
``player_detail`` is a template — the app substitutes the encoded player name
locally and never fetches the literal ``{name}``. Existing api keys/surfaces/
groups are preserved byte-for-byte.
"""

from __future__ import annotations


class TennisAdapter:
    title = "Court Evidence — ATP tennis"
    short = "tennis"

    @property
    def config(self) -> dict:
        return {
            "title": self.title,
            "sport": self.short,
            "surfaces": ["hard", "clay", "grass"],
            "groups": ["usopen", "tennis"],
            "tabs": ["Matches", "Players", "Model", "Experiments", "Methodology"],
            "api": {"health": "/api/health", "board": "/api/board",
                    "predict": "/api/predict", "players": "/api/players",
                    "player_detail": "/api/players/{name}",
                    "model": "/api/model", "stats": "/api/stats",
                    "reliability": "/api/reliability"},
        }

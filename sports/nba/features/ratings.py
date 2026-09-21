"""Rating engines for the NBA adapter: Elo and pace-adjusted efficiency.

All state here is updated ONLY after a game has been predicted, so a feature row
built from this module can never see the game it describes (see features/build.py
for the chronological pass that guarantees it).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


def expected_home(rating_home: float, rating_away: float, hca: float) -> float:
    """Logistic Elo expectation for the home team."""
    return 1.0 / (1.0 + 10 ** (-(rating_home + hca - rating_away) / 400.0))


def win_prob_to_elo(p: float) -> float:
    """Elo points that produce win probability p (used to turn observed home win rates into HCA)."""
    from math import log10
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 400.0 * log10(p / (1.0 - p))


def margin_multiplier(margin: float, elo_diff: float) -> float:
    """538-style margin-of-victory multiplier: a blowout moves ratings more, with a
    damping term so heavy favourites gain little from beating up on weak teams.

    NOTE: margin is signed (negative when the home team lost), and Python returns a
    COMPLEX number for a negative base with a fractional exponent - so the magnitude is
    what must be exponentiated. (This bug produced complex Elo ratings the first time.)
    """
    return ((abs(margin) + 3.0) ** 0.8) / (7.5 + 0.006 * abs(elo_diff))


@dataclass
class EloState:
    """Per-team Elo with season-boundary regression toward the mean."""

    k: float = 20.0
    start: float = 1500.0
    season_regress: float = 0.25
    ratings: dict[int, float] = field(default_factory=dict)
    games: dict[int, int] = field(default_factory=dict)

    def rating(self, team_id: int) -> float:
        return self.ratings.get(team_id, self.start)

    def new_season(self) -> None:
        for t in list(self.ratings):
            self.ratings[t] = self.start + (1.0 - self.season_regress) * (self.ratings[t] - self.start)
        self.games = {t: 0 for t in self.games}

    def update(self, home_id: int, away_id: int, home_won: bool, margin: float, hca: float) -> None:
        rh, ra = self.rating(home_id), self.rating(away_id)
        exp_home = expected_home(rh, ra, hca)
        s = 1.0 if home_won else 0.0
        mov = margin_multiplier(margin, abs(rh - ra))
        delta = self.k * mov * (s - exp_home)
        self.ratings[home_id] = rh + delta
        self.ratings[away_id] = ra - delta
        self.games[home_id] = self.games.get(home_id, 0) + 1
        self.games[away_id] = self.games.get(away_id, 0) + 1


@dataclass
class TeamHistory:
    """Rolling view of a team's recent games (points, possessions) for form/efficiency features."""

    window: int = 10
    pts: deque = field(default_factory=lambda: deque(maxlen=10))
    opp_pts: deque = field(default_factory=lambda: deque(maxlen=10))
    poss: deque = field(default_factory=lambda: deque(maxlen=10))
    opp_poss: deque = field(default_factory=lambda: deque(maxlen=10))
    dates: deque = field(default_factory=lambda: deque(maxlen=10))
    last_date: str | None = None
    recent_dates: deque = field(default_factory=lambda: deque(maxlen=20))

    def add(self, date: str, pts: float, opp_pts: float, poss: float, opp_poss: float) -> None:
        self.pts.append(pts)
        self.opp_pts.append(opp_pts)
        self.poss.append(poss)
        self.opp_poss.append(opp_poss)
        self.dates.append(date)
        self.recent_dates.appendleft(date)
        self.last_date = date

    def features(self) -> dict:
        n = len(self.pts)
        if n == 0:
            return {"games_played": 0, "form_margin": None, "pace": None, "ortg": None, "drtg": None}
        poss = sum(self.poss) or 1.0
        opp_poss = sum(self.opp_poss) or 1.0
        return {
            "games_played": n,
            "form_margin": sum(p - o for p, o in zip(self.pts, self.opp_pts)) / n,
            "pace": (poss + opp_poss) / (2 * n),
            "ortg": 100.0 * sum(self.pts) / poss,
            "drtg": 100.0 * sum(self.opp_pts) / opp_poss,
        }


def possessions(fga, fta, oreb, tov) -> float | None:
    """Standard possession estimate; None if the line is incomplete."""
    if None in (fga, fta, oreb, tov):
        return None
    return float(fga) + 0.44 * float(fta) - float(oreb) + float(tov)


def rest_days(prev_date: str | None, date: str) -> int | None:
    """Days since the team's previous game (1 = back-to-back)."""
    if not prev_date:
        return None
    from datetime import date as _d
    d0, d1 = _d.fromisoformat(prev_date), _d.fromisoformat(date)
    return (d1 - d0).days

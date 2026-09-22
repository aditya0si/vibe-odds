"""Phase-2 C3: schedule spots (plan Task 3) - the one channel with a real T3 story
(the literature says NBA prices don't fully absorb scheduling into the close).

Features (game-level, strictly as-of):
  travel_5d_home / travel_5d_away : miles between the team's consecutive game
      venues (incl. arrival at THIS game) over the last 5 calendar days
  alt_venue        : game at an altitude venue (Denver / Utah)
  four_in_5_home / _away : this is the team's 5th game in 5 calendar days
  three_in_4_away  : the away team's 3rd game in 4 calendar days
  homestand_home   : home team's consecutive HOME games including this one
  roadtrip_away    : away team's consecutive ROAD games including this one

Venue approximation (documented): a game's venue is the HOME team's arena
coordinates; neutral-site games are attributed to the home team's arena for
travel, count as ROAD for both homestand/roadtrip runs, and score alt_venue=0
(a neutral "home" at an altitude franchise is not played in that air). Team
coordinates come from the static table below (or the `coords` argument, which
tests inject). A team with no known coordinates has travel_* = None, never 0.0.

Team ids are the dataset's franchise ids (verified against the `teams` table:
Utah is 1610612762 here). As-of discipline identical to features/availability.py:
per-team state is updated only AFTER the game's row is emitted, so
build_spots(upto=...) reproduces the full build's prefix exactly.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict, deque
from datetime import date

# team_id -> (lat, lon). City coordinates: the travel signal is about distance
# between games, not precise addresses.
TEAM_COORDS: dict[int, tuple[float, float]] = {
    1610612737: (33.75, -84.39),    # Atlanta
    1610612738: (42.36, -71.06),    # Boston
    1610612751: (40.68, -73.98),    # Brooklyn
    1610612766: (35.23, -80.85),    # Charlotte
    1610612741: (41.88, -87.63),    # Chicago
    1610612739: (41.50, -81.69),    # Cleveland
    1610612742: (32.79, -96.81),    # Dallas
    1610612743: (39.75, -105.01),   # Denver (altitude venue)
    1610612765: (42.33, -83.05),    # Detroit
    1610612744: (37.75, -122.20),   # Golden State
    1610612745: (29.75, -95.36),    # Houston
    1610612754: (39.77, -86.16),    # Indiana
    1610612746: (34.04, -118.27),   # LA Clippers
    1610612747: (34.04, -118.27),   # LA Lakers
    1610612763: (35.14, -90.05),    # Memphis
    1610612748: (25.78, -80.19),    # Miami
    1610612749: (43.04, -87.92),    # Milwaukee
    1610612750: (44.98, -93.27),    # Minnesota
    1610612740: (29.95, -90.08),    # New Orleans
    1610612752: (40.75, -73.99),    # New York
    1610612760: (35.47, -97.52),    # Oklahoma City
    1610612753: (28.54, -81.38),    # Orlando
    1610612755: (39.95, -75.17),    # Philadelphia
    1610612756: (33.45, -112.07),   # Phoenix
    1610612757: (45.52, -122.70),   # Portland
    1610612758: (38.58, -121.50),   # Sacramento
    1610612759: (29.43, -98.44),    # San Antonio
    1610612761: (43.64, -79.38),    # Toronto
    1610612762: (40.77, -111.90),   # Utah (altitude venue)
    1610612764: (38.90, -77.02),    # Washington
}

ALTITUDE_TEAMS = frozenset({1610612743, 1610612762})   # Denver, Utah

SPOTS_KEYS = (
    "travel_5d_home", "travel_5d_away",
    "alt_venue",
    "four_in_5_home", "four_in_5_away", "three_in_4_away",
    "homestand_home", "roadtrip_away",
)


def _days(d0: str, d1: str) -> int:
    """Calendar-day distance d1 - d0 (dates are ISO strings, sortable)."""
    return (date.fromisoformat(d1) - date.fromisoformat(d0)).days


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 3958.8 * math.asin(min(1.0, math.sqrt(h)))


def build_spots(con: sqlite3.Connection, upto: str | None = None,
                coords: dict[int, tuple[float, float]] | None = None,
                verbose: bool = False) -> dict[str, dict]:
    """Chronological pass over games -> {game_id: schedule-spot features}.

    As-of discipline identical to features/availability.py: per-team state (last
    venue, rolling legs, home/road runs) is updated only AFTER the game's row is
    emitted, so build_spots(upto=...) reproduces the full build's prefix.
    """
    # Deferred: features.build imports this module at import time (v4 wiring), so a
    # module-level back-import would be circular. By call time build is complete.
    from sports.nba.features.build import load_games

    coords = TEAM_COORDS if coords is None else coords
    games = load_games(con, upto)

    last_date: dict[int, str] = {}
    last_venue: dict[int, tuple[float, float]] = {}
    recent_dates: dict[int, deque] = defaultdict(lambda: deque(maxlen=8))
    travel_win: dict[int, deque] = defaultdict(deque)   # (date, miles) legs taken
    homestand: dict[int, int] = defaultdict(int)   # consecutive HOME games incl. current
    roadtrip: dict[int, int] = defaultdict(int)    # consecutive ROAD games incl. current
    out: dict[str, dict] = {}

    for g in games:
        gid = g["game_id"]
        home, away = g["home_team_id"], g["away_team_id"]
        d = g["game_date"]
        neutral = bool(g["is_neutral"])
        venue = coords.get(home)          # documented approximation (incl. neutral)

        feats: dict[str, float | int | None] = {}
        # --- as-of windows computed from PRIOR games only ---
        for side, tid in (("home", home), ("away", away)):
            # 5-in-5 / 3-in-4 counting THIS game
            prior = [d0 for d0 in recent_dates[tid] if 0 < _days(d0, d) <= 4]
            feats[f"four_in_5_{side}"] = 1 if len(prior) >= 4 else 0
            if side == "away":
                feats["three_in_4_away"] = 1 if len(
                    [d0 for d0 in prior if _days(d0, d) <= 3]) >= 2 else 0
            # 5-day travel: legs already taken inside the window + today's arrival
            known = venue is not None and coords.get(tid) is not None
            if known:
                miles = sum(m0 for (d0, m0) in list(travel_win[tid])
                            if 0 < _days(d0, d) <= 5)
                arr = last_venue.get(tid)
                if arr is not None:
                    miles += haversine_miles(arr, venue)
                feats[f"travel_5d_{side}"] = round(miles, 1)
            else:
                feats[f"travel_5d_{side}"] = None   # unknown venue/coords: never 0.0

        feats["alt_venue"] = 1 if (home in ALTITUDE_TEAMS and not neutral) else 0

        # homestand / roadtrip runs, THIS game included
        for side, tid in (("home", home), ("away", away)):
            at_home = (not neutral) and (tid == home)
            if at_home:
                homestand[tid] += 1
                roadtrip[tid] = 0
            else:
                roadtrip[tid] += 1
                homestand[tid] = 0
        feats["homestand_home"] = homestand[home]
        feats["roadtrip_away"] = roadtrip[away]
        out[gid] = feats

        # ---- state updates, after emitting the row ----
        for tid in (home, away):
            if venue is not None and coords.get(tid) is not None:
                if last_date.get(tid) is not None and last_venue.get(tid) is not None:
                    travel_win[tid].append((d, haversine_miles(last_venue[tid], venue)))
                while travel_win[tid] and _days(travel_win[tid][0][0], d) > 30:
                    travel_win[tid].popleft()
                last_venue[tid] = venue
            last_date[tid] = d
            recent_dates[tid].append(d)

    if verbose:
        print(f"spots: {len(out)} games")
    return out

"""Availability features: how much player quality a team is missing, known BEFORE tip-off.

This is the signal the literature says actually moves NBA predictions (missing 2 of a team's top 3
costs ~9pp of win rate; facing such a team is worth ~+15% relative). It is also the signal most
published models omit, and the only one our formula has not yet been given.

Two ingredients, both strictly as-of:
  1. The PRE-GAME inactive list for the game itself (`game_inactives`, from the box-score summary
     payload; the league publishes it before tip-off). Never the box score's per-player `comment`
     field (DNP/DND), which is a post-game fact.
  2. A player-quality estimate from that player's PRIOR games only - a rolling mean of a simple
     production score over their last 20 appearances.

Derived features per game:
  avail_missing_home / avail_missing_away : share of the as-of roster's quality that is inactive (0-1)
  avail_diff                             : away_missing - home_missing (positive favours the home team)
  avail_top_out_home / avail_top_out_away: is one of the roster's top-3 rated players inactive?
  avail_n_inactive_home / _away          : count of inactive players

COMPLETENESS RULE (the trap this module must not fall into): a team with NO rows in
`game_inactives` for a game is ambiguous - it either means "nobody is out" (the league
published an empty list for that team) or "the list was never ingested" (unknown). These
must not be conflated, because scoring the unknown case as 0.0 silently zeroes the
feature for every game whose inactive list is missing.

PUBLICATION RULE (authoritative vs unknown). A game's lists count as authoritative -
and are attributed to BOTH teams - when either of two markers is present in the DB:

  * it has >= 1 row in `game_inactives` (a published list that named at least one
    player); or
  * it has >= 1 row in `game_officials`.

The second marker is the reason an empty list is NOT treated as unknown. Both tables
are written by the SAME parse of the SAME pre-game summary payload
(`sports/nba/ingest/box_scores.py::_parse_summary` reads
`boxScoreSummary.<team>.inactives` and `.officials` together). The v3 summary either
returns the full nine-dataset structure - which includes the `InactivePlayers` set,
possibly with zero rows - or, when throttled/unavailable, none of it. Phase-0
verification recorded exactly that: game `0020500001` (2005-06) parsed with
`Officials = 3, InactivePlayers = 0` (`docs/phase0/probe-nba-api-raw.json`) and
`docs/phase0/db-design.md` records "one 2005-06 game has the set but 0 rows". So an
officials row PROVES the summary parsed, hence the (empty) inactive lists were
published. The converse does not hold and is not assumed: an authoritative game can
lack officials rows (2 games do) - officials presence is sufficient, never necessary.

Consequences:

  * an authoritative game yields numeric avail_* values for BOTH teams; a team with
    zero inactive rows in such a game is "nobody is out" (missing share 0.0);
  * a game with NEITHER marker is a parse gap (no summary was fetched, e.g. the ingest
    timed out) and is "unknown": avail_missing_home/away, avail_top_out_home/away,
    avail_n_inactive_home/away and avail_diff are ALL None for that game - never 0.0.

REPLACEMENT-LEVEL PRIOR (missing denominator). avail_missing_* is a share, so it needs
a non-zero denominator even for a team whose as-of roster is empty. Every player with
no rated history (no appearance in the current RATING_WINDOW) is therefore assigned the
fixed, documented constant REPLACEMENT_LEVEL - never estimated from the data, which
would let a game's own season leak backwards. Players named inactive but not on the
as-of roster count at replacement level too. The denominator is the union of the as-of
roster and this game's inactive list; if that union is empty (a season opener with no
prior games and nobody listed out) a single replacement-level slot keeps it defined.
The result: avail_missing_* is None ONLY for a game with no published list, never for a
missing denominator. avail_diff is None whenever either side's missing share is None
(equivalently: for an unknown game).

See games_with_authoritative_inactives() for the membership test and
coverage_summary() for the counts this rule produces.

The as-of property is enforced the same way as the main feature pass: state is updated only after a
game's features have been emitted, so `build_availability(upto=...)` reproduces identical rows.
Both the inactive/officials membership tables and the box-score lookup are loaded up front, but in
the safe direction: membership depends only on a game's own rows, and the box load is now cut at
`upto` as well. The load is not the guarantee - the chronological state-update loop is - so
`tests/sports/nba/test_leakage_deletion.py` proves it the hard way, by rebuilding from a scratch
DB that PHYSICALLY lacks the future rows and comparing against the full build.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque

ROSTER_WINDOW = 10        # team games used to define the as-of roster
RATING_WINDOW = 20        # a player's last N appearances for the quality estimate
TOP_N = 3

# Fixed, as-of-safe production prior for a player with no rated history (no
# appearance yet in the RATING_WINDOW). It is a hard-coded constant, NEVER
# estimated from the data - estimation would let the games being scored leak into
# their own features. The value ~2.0 is roughly the league's 10th-percentile
# player-game production (a two-point / one-assist, end-of-bench contribution):
# low enough to keep the prior conservative, high enough to keep the denominator
# defined whenever an inactive list is authoritative. See the module docstring.
REPLACEMENT_LEVEL = 2.0

AVAIL_KEYS = (
    "avail_missing_home", "avail_missing_away", "avail_diff",
    "avail_top_out_home", "avail_top_out_away",
    "avail_n_inactive_home", "avail_n_inactive_away",
)


def production(row: sqlite3.Row) -> float:
    """Simple, explainable player production score (points + playmaking + rebounding - turnovers)."""
    pts = row["pts"] or 0
    ast = row["ast"] or 0
    reb = row["reb"] or 0
    tov = row["tov"] or 0
    return float(pts) + 1.5 * float(ast) + 1.0 * float(reb) - 1.0 * float(tov)


def games_with_authoritative_inactives(con: sqlite3.Connection) -> set[str]:
    """Game IDs whose inactive lists were published (game-level publication rule).

    A game is authoritative iff it has >= 1 row in `game_inactives` OR >= 1 row in
    `game_officials`. The two tables come from the same parse of the same pre-game
    summary payload, so an officials row proves the summary parsed and the
    (possibly empty) InactivePlayers set was published - the marker that turns an
    empty list into "nobody is out" instead of "unknown" (see the module
    docstring and docs/phase0/probe-nba-api-raw.json, game 0020500001).

    An authoritative game yields numeric avail_* values for BOTH teams (the side
    with zero rows scores "nobody is out"). A game with neither marker is unknown
    and must score None everywhere - never 0.0.

    As-of safe: membership of a game depends only on that game's own rows, so this
    set may be computed once up front even for truncated (`upto=...`) rebuilds;
    games past the cut are never consulted.
    """
    return {r["game_id"] for r in con.execute(
        "SELECT DISTINCT game_id FROM game_inactives "
        "UNION SELECT DISTINCT game_id FROM game_officials")}


def build_availability(con: sqlite3.Connection, upto: str | None = None,
                       verbose: bool = False) -> dict[str, dict]:
    """Chronological pass over games -> {game_id: availability features}."""
    sql = """SELECT g.game_id, g.season, g.game_date, g.home_team_id, g.away_team_id
             FROM games g
             WHERE g.season_type='regular' AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL"""
    params: tuple = ()
    if upto:
        sql += " AND g.game_date <= ?"
        params = (upto,)
    sql += " ORDER BY g.game_date, g.game_id"
    games = list(con.execute(sql, params))

    inactive_by_game: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for r in con.execute("SELECT game_id, team_id, player_id FROM game_inactives"):
        inactive_by_game[r["game_id"]][r["team_id"]].add(r["player_id"])

    # Defense in depth: a truncated rebuild must not even LOAD box rows dated after the
    # cut. The chronological state-update loop is the real as-of guarantee (and the
    # deletion-style test exercises it), but restricting the load removes the class of
    # bug where a future row is folded into state before the loop (see GAP F).
    box_sql = ("SELECT game_id, team_id, player_id, pts, ast, reb, tov, minutes "
               "FROM game_traditional")
    box_params: tuple = ()
    if upto:
        box_sql += " WHERE game_id IN (SELECT game_id FROM games WHERE game_date <= ?)"
        box_params = (upto,)
    box_sql += " ORDER BY game_id"
    boxes: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in con.execute(box_sql, box_params):
        boxes[r["game_id"]].append(r)

    player_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=RATING_WINDOW))
    team_recent: dict[int, deque] = defaultdict(lambda: deque(maxlen=ROSTER_WINDOW))
    authoritative = games_with_authoritative_inactives(con)
    out: dict[str, dict] = {}

    for g in games:
        gid, home, away = g["game_id"], g["home_team_id"], g["away_team_id"]
        if gid not in authoritative:
            # Completeness trap: no published list for this game -> unknown, never 0.0.
            out[gid] = {k: None for k in AVAIL_KEYS}
        else:
            feats: dict[str, float | int | None] = {}
            for side, tid in (("home", home), ("away", away)):
                roster = set()
                for players in team_recent.get(tid, []):
                    roster |= players
                inactive = inactive_by_game.get(gid, {}).get(tid, set())
                # As-of roster quality; unrated players get the fixed prior.
                rated = {p: (sum(player_hist[p]) / len(player_hist[p])) if player_hist[p]
                         else REPLACEMENT_LEVEL for p in roster}
                # The denominator is the union of the as-of roster and this game's
                # inactive list: a player named out but not on the roster (newly
                # signed, long-term injured) is still part of the team's known
                # quality and counts at replacement level. This keeps the share in
                # [0, 1] and the denominator positive.
                known = dict(rated)
                for p in inactive:
                    known.setdefault(p, REPLACEMENT_LEVEL)
                total = sum(known.values())
                if total <= 0:
                    # No as-of roster and nobody listed out (a season opener with no
                    # prior games): one nominal replacement-level slot keeps the
                    # denominator defined. avail_missing_* must not be None here -
                    # the list is authoritative, only the denominator was missing.
                    total = REPLACEMENT_LEVEL
                missing = sum(known[p] for p in inactive)
                top = sorted(rated.items(), key=lambda kv: -kv[1])[:TOP_N]
                feats[f"avail_missing_{side}"] = round(missing / total, 4)
                feats[f"avail_n_inactive_{side}"] = len(inactive)
                feats[f"avail_top_out_{side}"] = 1 if any(p in inactive for p, _ in top) else 0
            mh, ma = feats.get("avail_missing_home"), feats.get("avail_missing_away")
            feats["avail_diff"] = round(ma - mh, 4) if (mh is not None and ma is not None) else None
            out[gid] = feats

        # ---- state updates, after emitting the row ----
        for r in boxes.get(gid, []):
            if (r["minutes"] or 0) > 0:
                player_hist[r["player_id"]].append(production(r))
        played: dict[int, set[int]] = defaultdict(set)
        for r in boxes.get(gid, []):
            if (r["minutes"] or 0) > 0:
                played[r["team_id"]].add(r["player_id"])
        for tid, players in played.items():
            team_recent[tid].append(players)
    if verbose:
        have = sum(1 for f in out.values() if f.get("avail_missing_home") is not None)
        print(f"availability built for {len(out)} games; usable on {have}")
    return out


def coverage(con: sqlite3.Connection) -> dict:
    """Coverage report: how many games have a usable inactive list (per season)."""
    rows = con.execute("""
        SELECT g.season,
               COUNT(DISTINCT g.game_id) games,
               COUNT(DISTINCT i.game_id) games_with_inactives
        FROM games g LEFT JOIN game_inactives i ON i.game_id = g.game_id
        WHERE g.season_type='regular'
        GROUP BY g.season ORDER BY g.season""").fetchall()
    return {r["season"]: {"games": r["games"], "with_inactives": r["games_with_inactives"],
                          "share": round(r["games_with_inactives"] / r["games"], 3) if r["games"] else None}
            for r in rows}


PUBLICATION_RULE = (
    "An authoritative game has >=1 row in game_inactives OR >=1 row in game_officials "
    "(both parsed from the same pre-game box-score summary; an officials row proves the "
    "summary parsed and the possibly-empty inactive list was published). A game with "
    "neither marker is a parse gap and its avail_* keys are None. avail_missing_* is "
    "never None for an authoritative game: unrated players take the fixed "
    "REPLACEMENT_LEVEL prior, so the denominator is always defined."
)


def coverage_summary(con: sqlite3.Connection) -> dict:
    """Counts behind the publication/replacement-level rule, for the v2 coverage artifact.

    Games are completed regular-season rows. ``authoritative_with_inactive_rows`` are
    games that named at least one inactive player; ``authoritative_empty_list`` are
    games that published an empty list (proved by officials rows, same summary parse);
    ``unknown_no_summary`` are games whose summary was never parsed (the only rows for
    which avail_* stays None).
    """
    inact = {r["game_id"] for r in con.execute("SELECT DISTINCT game_id FROM game_inactives")}
    off = {r["game_id"] for r in con.execute("SELECT DISTINCT game_id FROM game_officials")}
    auth_inact = 0
    auth_via_officials = 0
    unknown_by_season: dict[str, int] = defaultdict(int)
    total = 0
    for r in con.execute("""SELECT game_id, season FROM games
                            WHERE season_type='regular' AND home_score IS NOT NULL
                              AND away_score IS NOT NULL"""):
        total += 1
        gid = r["game_id"]
        if gid in inact:
            auth_inact += 1
        elif gid in off:
            auth_via_officials += 1
        else:
            unknown_by_season[r["season"]] += 1
    return {
        "rule": PUBLICATION_RULE,
        "replacement_level": REPLACEMENT_LEVEL,
        "n_completed_games": total,
        "authoritative": auth_inact + auth_via_officials,
        "authoritative_with_inactive_rows": auth_inact,
        "authoritative_empty_list_via_officials": auth_via_officials,
        "unknown_no_summary": sum(unknown_by_season.values()),
        "unknown_by_season": dict(sorted(unknown_by_season.items())),
    }

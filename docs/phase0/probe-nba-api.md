# nba_api (stats.nba.com) endpoint probe — 2005-06 → 2025-26 game database

**Question this answers:** which `nba_api` endpoints can actually build a complete NBA game
database (game log + box scores + pre-game inactives + officials) for **2005-06 → 2025-26**,
which 2025-26 ingest endpoint to use, and how fast the ingest can run.

**Probe date:** 2026-09-21 (all calls made live from this machine on that date).
**Host / interpreter:** Windows 11, git-bash, `python 3.11.16`
(`C:\Users\oliad\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`), **`nba_api 1.11.4`**.
**Script:** [`probe_nba_api.py`](./probe_nba_api.py) — re-runnable per phase:
`python docs/phase0/probe_nba_api.py season-log era-boxscores era-sweep modern-v3 modern-v2 raw-http v2-boundary scoreboard-raw v3-extra throttle integrity inactive-sample gap cap v3-old`
**Raw evidence:** [`probe-nba-api-raw.json`](./probe-nba-api-raw.json) — 150 probe records covering
145 distinct game ids, with per-call wall times, warnings, result-set names, row counts, byte sizes
and raw response heads.

> This supersedes the "stats.nba.com was unreachable / UNVERIFIED live" caveat in
> `nba-prediction-research/01-data-and-odds-sources.md` §1: **nba_api works from this host** even
> though raw `requests.get` to `stats.nba.com` times out. Everything below is a live call, not a
> docstring reading.

---

## 0. Verdicts (the four questions, answered)

| # | Question | Answer |
|---|---|---|
| 1 | Does `LeagueGameFinder(season_nullable='YYYY-YY', league_id_nullable='00')` return complete regular-season logs? | **YES — 8/8 seasons, 0 exceptions.** Every sampled season returns every played regular-season game, each exactly twice (home + away row), with no game having a missing partner row. It also returns preseason/all-star/playoffs/play-in in the same payload, so filter on `SEASON_ID == '2' + YYYY`. Two caveats: a **hard 30,000-row cap** makes one call per season mandatory, and the 2012-13 total is **1229 games, not 1230, because game `0021201214` was never played** (see §1.3/§1.4). `leaguegamelog` is a working alternative and is the only endpoint that still lists that cancelled game. |
| 2 | Do the box-score endpoints expose the **pre-game inactive list** for each era? | **YES, for the whole history — use `boxscoresummaryv3`.** Verified on 2005-06, 2012-13, 2018-19 and 2025-26: 9 datasets, `InactivePlayers` present with rows, `Officials` = 3 rows. A wider sample (10 games × 8 seasons = 80 games) found inactives in **79/80 games** and exactly 3 officials in **80/80**. `boxscoresummaryv2` is equally good *up to 2025-04-09* and then silently returns **empty** `InactivePlayers`/`Officials` sets. ⇒ **player-availability features are buildable across all 21 seasons**, but the ingester must read v3 and tolerate 0-row inactive sets. |
| 3 | Which endpoint should the 2025-26 ingest use? | **The v3 family: `boxscoresummaryv3` + `boxscoretraditionalv3` + `boxscoreadvancedv3` (+ `playbyplayv3`).** All four PASS on a 2025-26 game with full row counts. `boxscoretraditionalv2` returns 3 result sets with **0 rows**; `boxscoreadvancedv2` and `playbyplayv2` return a literal **`{}` (2 bytes)**; `boxscoresummaryv2` returns 9 result sets with `Officials`, `InactivePlayers`, `OtherStats`, `SeasonSeries` all **empty**. v3 also works on the oldest seasons (verified down to 2005-06), so one code path covers all 21 seasons. |
| 4 | Rate limits / practical throttling | **No HTTP 429 and no timeout in 150 probe records.** 10 sequential `boxscoresummaryv3` calls: median **0.43 s**, total 4.25 s. 10 sequential box-score-v3 calls: `boxscoretraditionalv3` ≈ **0.43 s**, `boxscoreadvancedv3` ≈ **0.77 s**, total 6.08 s. Derived ingest sizing: ~1.74 s per game for the 3-call box-score triple (+0.41 s for PBP) ⇒ **≈12.2 h (≈15.1 h with PBP) single-threaded for the ~25,268 games of 2005-06 → 2025-26, ≈2.0–2.5 h at 6 workers**. The failures that do occur are *content* failures, not throttling: dead v2 endpoints (`{}` body), v2 empty sets from 2025-04-10, and flaky 0-byte bodies from `scoreboardv2` (never from a v3 endpoint) ⇒ retry per call, not rate-limit backoff. |

---

## 1. Game logs: `leaguegamefinder.LeagueGameFinder`

### 1.1 Exact call and result

```python
from nba_api.stats.endpoints import leaguegamefinder

d = leaguegamefinder.LeagueGameFinder(
    season_nullable="2005-06",        # "YYYY-YY"
    league_id_nullable="00",          # NBA
    player_or_team_abbreviation="T",  # team-level rows: one per team per game
    timeout=40,
).get_dict()

rs = d["resultSets"][0]               # name "LeagueGameFinderResults"
headers, rows = rs["headers"], rs["rowSet"]
```

One row per **team** per game ⇒ 2 rows per game. Regular-season filter used throughout this probe:
`SEASON_ID == "2" + season[:4]` (`"22005"` for 2005-06).

### 1.2 Per-season result (live, 2026-09-21)

| Season | rows (all types) | distinct GAME_IDs | regular-season rows | **games (rows/2)** | SEASON_ID `2…` check | first date | last date | call |
|---|---|---|---|---|---|---|---|---|
| 2005-06 | 2871 | 1435 | 2460 | **1230** | ✔ | 2005-10-10 | 2006-06-20 | 4.34 s |
| 2008-09 | 2866 | 1433 | 2460 | **1230** | ✔ | 2008-10-05 | 2009-06-14 | 2.32 s |
| 2012-13 | 2866 | 1433 | 2458 | **1229** | ✔ | 2012-10-05 | 2013-06-20 | 1.65 s |
| 2016-17 | 2829 | 1414 | 2460 | **1230** | ✔ | 2016-10-01 | 2017-06-12 | 1.61 s |
| 2019-20 | 2516 | 1258 | 2118 | **1059** | ✔ | 2019-09-30 | 2020-10-11 | 1.56 s |
| 2020-21 | 2442 | 1221 | 2160 | **1080** | ✔ | 2020-12-11 | 2021-07-20 | 3.46 s |
| 2023-24 | 2795 | 1397 | 2460 | **1230** | ✔ | 2023-10-05 | 2024-06-17 | 1.61 s |
| 2025-26 | 2805 | 1401 | 2460 | **1230** | ✔ | 2025-10-02 | 2026-06-13 | 0.49 s |

**No season failed and no exception was raised** (each season was retried once on failure; no retry
was needed). 1230 = 82 × 30 / 2 and 1080 = 72 × 30 / 2 (schedule arithmetic); 2019-20's 1059 reflects
the COVID-shortened season; 2025-26 is complete through 2026-06-13.

Game-ID prefixes returned in the same payload (`prefix: rows`), i.e. the endpoint is **not**
regular-season-only — the ingest must filter:

| Season | 001 preseason | 002 regular | 003 all-star | 004 playoffs | 005 play-in | 006 |
|---|---|---|---|---|---|---|
| 2005-06 | 227 | 2460 | 6 | 178 | – | – |
| 2008-09 | 230 | 2460 | 6 | 170 | – | – |
| 2012-13 | 232 | 2458 | 6 | 170 | – | – |
| 2016-17 | 204 | 2460 | 7 | 158 | – | – |
| 2019-20 | 224 | 2118 | 6 | 166 | 2 | – |
| 2020-21 | 98 | 2160 | 2 | 170 | 12 | – |
| 2023-24 | 147 | 2460 | 10 | 164 | 12 | 2 |
| 2025-26 | 145 | 2460 | 16 | 170 | 12 | 2 |

### 1.3 Completeness check (row-level, not just counts)

For 2005-06, 2012-13, 2019-20 and 2025-26 every regular-season `GAME_ID` appears **exactly twice**
(`games_without_exactly_2_rows = 0`).

**The 2012-13 "missing" game — resolved.** `LeagueGameFinder` returns 1229 games where the schedule
has 1230. The gap is `0021201214` (scheduled 2013-04-16, BOS vs. IND):

| probe | result |
|---|---|
| `leaguegamelog` (2012-13, Regular Season) | **2 rows** for it — `GAME_DATE 2013-04-16`, `MATCHUP "BOS vs. IND"` / `"IND @ BOS"`, **`WL = None`, `PTS = 0`, `MIN = 0`** |
| `leaguegamefinder` (2012-13) | **not present** at all |
| `boxscoresummaryv3(game_id="0021201214")` | JSON body with `"boxScoreSummary": {"gameId": null, "gameStatus": 0, ...}` → nba_api raises `AttributeError: 'NoneType' object has no attribute 'get'` |

⇒ It is a **scheduled game that was never played**, not a fetch bug. For a game database:
`LeagueGameFinder` gives the *played* games; `LeagueGameLog` additionally carries cancelled
placeholders with null/zero stats that must be dropped (filter `WL isnull`/`PTS == 0`/`MIN == 0`).

### 1.4 Working alternative + two hard limits

**Alternative endpoint (verified, same ID set apart from the cancelled game):**

```python
from nba_api.stats.endpoints import leaguegamelog

d = leaguegamelog.LeagueGameLog(
    season="2012-13",
    season_type_all_star="Regular Season",   # explicit "Regular Season"
    league_id="00",
    timeout=40,
).get_dict()
rs = d["resultSets"][0]        # name "LeagueGameLog"
```

| Season | LeagueGameLog rows / ids | same ID set as LeagueGameFinder? | difference |
|---|---|---|---|
| 2005-06 | 2460 / 1230 | ✔ identical | – |
| 2012-13 | 2460 / 1230 | ✖ | only in gamelog: `0021201214` (cancelled, never played) |
| 2019-20 | 2118 / 1059 | ✔ identical | – |
| 2025-26 | 2460 / 1230 | ✔ identical | – |

**Limit A — 30,000-row hard cap.** One unfiltered multi-season call
(`league_id_nullable="00", date_from_nullable="10/01/2005", date_to_nullable="06/30/2026",
player_or_team_abbreviation="T"`) returned **exactly 30,000 rows** spanning only
**2015-10-22 → 2026-06-13**, i.e. it silently truncates older data (newest-first delivery).
⇒ **Query season-by-season. Never a "give me everything" call.**

**Limit B — `game_id_nullable` alone does not filter.** Passing `game_id_nullable="0021201214"`
without a season returned the same 30,000-row, 2015→2026 payload (filter ignored).
⇒ Always scope by `season_nullable` (then select the game client-side).

---

## 2. Pre-game inactive lists and officials — per era (the decisive question)

### 2.1 One game from each era, both endpoints, side by side

```python
from nba_api.stats.endpoints import boxscoresummaryv2, boxscoresummaryv3

v3 = boxscoresummaryv3.BoxScoreSummaryV3(game_id="0020501226", timeout=40)
v2 = boxscoresummaryv2.BoxScoreSummaryV2(game_id="0020501226", timeout=40)
```

| Era | game id | endpoint | result sets | `InactivePlayers` rows | `Officials` rows |
|---|---|---|---|---|---|
| 2005-06 | `0020501226` | **boxscoresummaryv3** | 9: `GameSummary, GameInfo, ArenaInfo, Officials, LineScore, InactivePlayers, LastFiveMeetings, OtherStats, AvailableVideo` | **6** | **3** |
| 2005-06 | `0020501226` | boxscoresummaryv2 | 9: `GameSummary, OtherStats, Officials, InactivePlayers, GameInfo, LineScore, LastMeeting, SeasonSeries, AvailableVideo` | **6** | **3** |
| 2012-13 | `0021201218` | **boxscoresummaryv3** | same 9 as above | **5** | **3** |
| 2012-13 | `0021201218` | boxscoresummaryv2 | same 9 as above | **5** | **3** |
| 2018-19 | `0021801227` | **boxscoresummaryv3** | same 9 as above | **8** | **3** |
| 2018-19 | `0021801227` | boxscoresummaryv2 | same 9 as above | **8** | **3** |
| 2025-26 | `0022501191` | **boxscoresummaryv3** | same 9 as above | **8** | **3** |
| 2025-26 | `0022501191` | boxscoresummaryv2 | 9, **but `Officials`=0, `InactivePlayers`=0, `OtherStats`=0, `SeasonSeries`=0 rows** | **0** | **0** |

Columns exposed (v3, camelCase; v2 uses the SCREAMING_SNAKE legacy names):

| dataset | v3 headers | v2 headers |
|---|---|---|
| `InactivePlayers` | `gameId, teamId, personId, firstName, familyName, jerseyNum` (6) | `PLAYER_ID, FIRST_NAME, LAST_NAME, JERSEY_NUM, TEAM_ID, TEAM_CITY, TEAM_NAME, TEAM_ABBREVIATION` (8) |
| `Officials` | `gameId, personId, name, nameI, firstName, familyName, jerseyNum` (7) | `OFFICIAL_ID, FIRST_NAME, LAST_NAME, JERSEY_NUM` (4) |

Sample rows (2005-06, `0020501226`): inactive → `['0020501226', 1610612742, 1496, 'Keith', 'Van Horn', '2']`;
official → `['0020501226', 1163, 'Bernie Fryer', 'B. Fryer', 'Bernie', 'Fryer', '7']`.

### 2.2 v3 sweep: 3 games per season × 8 seasons (`inactive_rows/officials_rows`)

| Season | first game | middle game | last game |
|---|---|---|---|
| 2005-06 | `0020500001` — **0**/3 | `0020500616` — 5/3 | `0020501230` — 4/3 |
| 2008-09 | `0020800001` — 6/3 | `0020800616` — 6/3 | `0020801230` — 5/3 |
| 2012-13 | `0021200001` — 5/3 | `0021200615` — 4/3 | `0021201230` — 5/3 |
| 2016-17 | `0021600001` — 4/3 | `0021600616` — 4/3 | `0021601230` — 5/3 |
| 2019-20 | `0021900001` — 7/3 | `0021900530` — 9/3 | `0021901318` — 10/3 |
| 2020-21 | `0022000001` — 6/3 | `0022000541` — 9/3 | `0022001080` — 4/3 |
| 2023-24 | `0022300001` — 7/3 | `0022300616` — 9/3 | `0022301230` — 8/3 |
| 2025-26 | `0022500001` — 8/3 | `0022500616` — 8/3 | `0022501230` — 8/3 |

**23/24 games have a non-empty inactive list and 24/24 have exactly 3 officials.** The single
exception is `0020500001`, the 2005-06 season opener (2005-11-01): the `InactivePlayers` **set is
present but has 0 rows** — not an error, an empty list.

### 2.3 Wider population sample: 10 games per season (80 games, evenly spaced)

| Season | games with inactives | games with 3 officials | min/max inactive rows | empty games |
|---|---|---|---|---|
| 2005-06 | 9/10 | 10/10 | 0 / 6 | `0020500001` |
| 2008-09 | 10/10 | 10/10 | 3 / 6 | – |
| 2012-13 | 10/10 | 10/10 | 2 / 6 | – |
| 2016-17 | 10/10 | 10/10 | 3 / 5 | – |
| 2019-20 | 10/10 | 10/10 | 7 / 11 | – |
| 2020-21 | 10/10 | 10/10 | 4 / 9 | – |
| 2023-24 | 10/10 | 10/10 | 6 / 10 | – |
| 2025-26 | 10/10 | 10/10 | 6 / 11 | – |

**79/80 (98.8%) of sampled games expose inactives; 80/80 expose 3 officials.** Inactive-list length
grows over time (≈0–6 in 2005-06 → 6–11 in 2025-26), consistent with the two-way-contract era.

### 2.4 Where `boxscoresummaryv2` actually breaks (the 2025-04-10 warning, measured)

| Season | game date | v2 inactive / officials | v3 inactive / officials |
|---|---|---|---|
| 2024-25 | 2024-10-22 (`0022400062`) | 8 / 3 | 8 / 3 |
| 2024-25 | 2024-10-22 (`0022400061`) | 7 / 3 | 7 / 3 |
| 2024-25 | **2025-04-10** (`0022401168`) | **0 / 0** | 7 / 3 |
| 2024-25 | **2025-04-10** (`0022401161`) | **0 / 0** | 12 / 3 |
| 2024-25 | **2025-04-10** (`0022401170`) | **0 / 0** | 6 / 3 |
| 2025-26 | 2025-10-21 (`0022500002`) | 9 / 3 *(still works!)* | 9 / 3 |
| 2025-26 | 2026-01-16 (`0022500591`) | **0 / 0** | 8 / 3 |
| 2025-26 | 2026-04-12 (`0022501199`) | **0 / 0** | 8 / 3 |

The boundary is exactly as the package warns: **v2 loses officials + inactives from 2025-04-10
onward**, and its 2025-26 coverage is *inconsistent* (the 2025-10-21 game still had data, January and
April games did not). Critically, v2 **fails silently** — it still returns 9 structurally valid result
sets, just empty. Any v2-based ingest would quietly write 0 inactives and 0 officials for the newest
season. **Use v3.**

### 2.5 Implementation gotcha (cost me one re-probe — read this)

v2 endpoints expose rows as `response["resultSets"][i]["rowSet"]`. **v3 endpoints have no
`resultSets` key at all** (top-level keys are `["meta", "boxScoreSummary"]` etc.), and calling
`get_dict()["resultSets"]` on them returns **nothing without raising** — the dataset looks empty
while the data is present. v3 rows live in named datasets reachable via
`obj.nba_response.get_data_sets(endpoint)` → `{name: {"headers": [...], "data": [[...]]}}`
(or the attributes `.inactive_players`, `.officials`, …). Read them like this:

```python
def datasets(obj):
    """Works for BOTH v2 (resultSets/rowSet) and v3 (named datasets) nba_api objects."""
    raw = obj.get_dict()
    if "resultSets" in raw or "resultSet" in raw:
        sets = raw.get("resultSets") or [raw["resultSet"]]
        if isinstance(sets, dict):
            sets = [sets]
        return {s["name"]: {"headers": s["headers"], "data": s["rowSet"]} for s in sets}
    got = obj.nba_response.get_data_sets(obj.endpoint)          # v3 path
    return {name: {"headers": d["headers"], "data": d["data"]} for name, d in got.items()}
```

---

## 3. 2025-26 ingest: the v2 family is dead, v3 works (and works on old seasons too)

Test game: `0022501191` (2025-26).

| Endpoint | Result on the 2025-26 game | Raw body | Verdict |
|---|---|---|---|
| `boxscoresummaryv3` | 9 datasets — `GameSummary` 1, `GameInfo` 1, `ArenaInfo` 1, **`Officials` 3**, `LineScore` 2, **`InactivePlayers` 8**, `LastFiveMeetings` 5, `OtherStats` 2, `AvailableVideo` 1 | `{"meta":…,"boxScoreSummary":{…}}` | **PASS — use** |
| `boxscoretraditionalv3` | `PlayerStats` **26 rows × 34 cols**, `TeamStats` 2 × 26, `TeamStarterBenchStats` 4 × 26 | `{"meta":…,"boxScoreTraditional":{…}}` | **PASS — use** |
| `boxscoreadvancedv3` | `PlayerStats` **26 rows × 37 cols**, `TeamStats` 2 × 30 | `{"meta":…,"boxScoreAdvanced":{…}}` | **PASS — use** |
| `playbyplayv3` | `PlayByPlay` **486 rows**, `AvailableVideo` 1 | – | **PASS — use** |
| `boxscoretraditionalv2` | 3 result sets (`PlayerStats`, `TeamStats`, `TeamStarterBenchStats`) with **0 rows each** | 961 bytes, valid JSON | **FAIL (empty)** |
| `boxscoreadvancedv2` | nba_api raises `KeyError: 'resultSet'` | **`{}` — 2 bytes** | **FAIL (dead)** — also `{}` for a 2012-13 control game ⇒ not 2025-26-specific, the service returns nothing for any game right now |
| `playbyplayv2` | nba_api raises `KeyError: 'resultSet'` | **`{}` — 2 bytes** | **FAIL (dead)** |
| `boxscoresummaryv2` | 9 result sets but `Officials` 0, `InactivePlayers` 0, `OtherStats` 0, `SeasonSeries` 0 | 2604 bytes, valid JSON | **FAIL for 2025-26 (silently empty)** |

Exact deprecation texts shipped by `nba_api 1.11.4` (quoted from the installed package and echoed as
Python warnings at runtime):

- `boxscoretraditionalv2`: *"This endpoint is deprecated. Please use BoxScoreTraditionalV3 instead. Data is no longer being published for BoxScoreTraditionalV2 as of the 2025-26 NBA season."*
- `playbyplayv2`: *"This endpoint is deprecated. Please use PlayByPlayV3 instead. The NBA API no longer returns data for PlayByPlayV2 (returns empty JSON)."* — matches the measured `{}` body.
- `boxscoresummaryv2` (runtime warning, verbatim incl. the original typo): *"BoxScoreSummaryV2 has known data availability issues. Data may be missing for games on or after 4/10/2025. Users should moving to BoxScoreSummaryV3 or verify data completeness for their specific use cases and implement appropriate error handling."*
- `scoreboardv2`: *"This endpoint has known issues with line score data for games between 2025-10-22 and 2025-12-25. Please use ScoreboardV3 instead."*

**v3 is not modern-only.** Verified on older games as well:

| Season | `boxscoretraditionalv3` | `boxscoreadvancedv3` | `boxscoresummaryv3` | `playbyplayv3` |
|---|---|---|---|---|
| 2005-06 | `0020501226`: PlayerStats 24, TeamStats 2, Bench 4 | PlayerStats 24, TeamStats 2 | 9 sets, inactives 6, officials 3 | `0020501226`: PlayByPlay **438 rows** |
| 2012-13 | `0021201218`: 25 / 2 / 4 | 25 / 2 | 9 sets, inactives 5, officials 3 | – |
| 2016-17 | `0021600616`: 26 / 2 / 4 | 26 / 2 | 9 sets, inactives 4, officials 3 | – |
| 2019-20 | `0021900530`: 25 / 2 / 4 | 25 / 2 | 9 sets, inactives 9, officials 3 | – |
| 2025-26 | `0022501191`: 26 / 2 / 4 | 26 / 2 | 9 sets, inactives 8, officials 3 | PlayByPlay 486 rows |

**Recommendation: one v3 code path for 2005-06 → 2025-26** (`boxscoresummaryv3` +
`boxscoretraditionalv3` + `boxscoreadvancedv3` + `playbyplayv3`), because (a) it is the only family
that still works for ≥2025-04-10, (b) it works on the oldest games too, and (c) mixing families would
mean two column schemas. Keep v2 only as a cross-check for pre-2025 games where it still works.

**Per-game error handling is mandatory:** for the never-played game `0021201214`,
`boxscoresummaryv3` returns a *valid JSON envelope with a null-filled summary*
(`"boxScoreSummary": {"gameId": null, "gameStatus": 0, …}`) and nba_api then raises
`AttributeError: 'NoneType' object has no attribute 'get'`. Treat per-game exceptions as "no data"
and record them; do not abort the run.

---

## 4. Rate limits, throttling and ingest sizing

### 4.1 10 sequential calls, twice (no sleeps between calls)

| Block | endpoints | per-call seconds | stats |
|---|---|---|---|
| A | `boxscoresummaryv3` × 10 (10 different 2025-26 games) | 0.33, 0.45, 0.43, 0.48, 0.41, 0.43, 0.43, 0.41, 0.44, 0.44 | min 0.33 / median **0.43** / mean 0.43 / max 0.48 / **total 4.25 s** |
| B | `boxscoretraditionalv3` (`i` even) / `boxscoreadvancedv3` (`i` odd) × 10 | 0.44, 0.77, 0.41, 0.75, 0.42, 0.76, 0.46, 0.78, 0.43, 0.86 | traditional ≈ **0.43** median (0.41–0.46); advanced ≈ **0.77** median (0.75–0.86); **total 6.08 s** |

**Zero failures, zero HTTP 429s, zero timeouts in these 20 calls** — and no 429 or timeout anywhere in the 150 probe records.
Per-call wall times for the other endpoints: `boxscoresummaryv3` 0.41–0.72 s (34 samples, median
**0.53 s**), `boxscoretraditionalv3` 0.41–0.64 s, `boxscoreadvancedv3` 0.37–0.98 s (median **0.76 s**),
`playbyplayv3` 0.37–0.45 s, season-level `LeagueGameFinder` 0.49–4.34 s (median ≈1.6 s).

**The failure mode to design for is not 429 — it is a flaky empty body.** `scoreboardv2` returned
HTTP-200-shaped responses with a **0-byte body** (`valid_json = False`) for `01/15/2013` (twice, in one
run), then returned a full 10,461-byte / 9-result-set payload for the *same date* in a later run, while
`01/14/2013`, `01/16/2013` and `11/02/2005` came back 0-byte in that same later run. **`scoreboardv3`
answered all 7 dates** (16,408 / 21,328 / 26,714 / 33,911 / 20,170 bytes). So: retry-with-backoff on
*empty/decode errors*, and prefer v3 endpoints everywhere.

```python
def robust(fn, tries=3, **kw):
    for attempt in range(1, tries + 1):
        try:
            return fn(**kw)                       # nba_api raises on {} / 0-byte bodies
        except Exception:
            if attempt == tries:
                raise
            time.sleep(2 * attempt)               # 2s, 4s
```

### 4.2 Derived sizing for the overnight ingest (single-threaded, sequential)

Medians: summary 0.53 s + traditional 0.45 s + advanced 0.76 s = **1.74 s per game** for the box-score
triple; **2.15 s** including `playbyplayv3`.

| Workload | Per season (1230 games) | 2005-06 → 2025-26 (**≈25,268 played games**: 17 full 82-game seasons = 20,910, plus 2011-12 lockout 990, 2012-13 1229 (one game never played), 2019-20 1059, 2020-21 1080) |
|---|---|---|
| summary + traditional + advanced | ≈ 36 min | **≈ 12.2 h** (≈ 2.0 h at 6 workers) |
| + play-by-play | ≈ 44 min | **≈ 15.1 h** (≈ 2.5 h at 6 workers) |
| season game logs (LeagueGameFinder) | 1 call, ≈ 1.6 s | 21 calls, ≈ 1 min |
| v2 inactives/officials re-sweep (cross-check) | — | ≈ 3.8 h (not needed — v3 covers both) |

These are extrapolations from the measured single-threaded medians (labelled as such, not measured
end-to-end). With no observed 429s at ≈0.5 s/call, 4–8 concurrent workers with per-call retry is a
reasonable overnight plan; keep the sequential path as the fallback.

---

## 5. Summary matrix — PASS / FAIL by season and data type

`PASS` = endpoint returned non-empty, correct-shaped data for live game(s) in that season.
`FAIL` = returned empty/dead. `n/p` = not probed in this phase. Probe counts in the legend below.

| Season | game log (LeagueGameFinder) | game log (LeagueGameLog alt) | inactives (v3) | officials (v3) | inactives (v3) **sample rate** | inactives/officials (v2) | box score trad (v3) | box score adv (v3) | PBP (v3) |
|---|---|---|---|---|---|---|---|---|---|
| 2005-06 | PASS (1230 g) | PASS | PASS | PASS | **9/10 games** | PASS (1 game) | PASS | PASS | PASS |
| 2008-09 | PASS (1230 g) | n/p | PASS | PASS | **10/10 games** | n/p | n/p | n/p | n/p |
| 2012-13 | PASS (1229 g) | PASS | PASS | PASS | **10/10 games** | PASS (1 game) | PASS | PASS | n/p |
| 2016-17 | PASS (1230 g) | n/p | PASS | PASS | **10/10 games** | n/p | PASS | PASS | n/p |
| 2018-19 | n/p | n/p | PASS | PASS | n/p | PASS (1 game) | n/p | n/p | n/p |
| 2019-20 | PASS (1059 g) | PASS | PASS | PASS | **10/10 games** | n/p | PASS | PASS | n/p |
| 2020-21 | PASS (1080 g) | n/p | PASS | PASS | **10/10 games** | n/p | n/p | n/p | n/p |
| 2023-24 | PASS (1230 g) | n/p | PASS | PASS | **10/10 games** | n/p | n/p | n/p | n/p |
| 2024-25 | n/p | n/p | PASS | PASS | n/p | **FAIL from 2025-04-10** (3/3 games 0 rows); PASS before (2/2 games) | n/p | n/p | n/p |
| 2025-26 | PASS (1230 g) | PASS | PASS | PASS | **10/10 games** | **FAIL** (0 rows for 2 of 3 games; 1 of 3 OK) | PASS in v3 / **FAIL v2 (0 rows)** | PASS in v3 / **FAIL v2 (`{}`)** | PASS in v3 / **FAIL v2 (`{}`)** |

Legend — definitive per-season probes: game log = the 8-season `LeagueGameFinder` run; sample rate =
10 evenly spaced games per season via `boxscoresummaryv3` (§2.3); "1 game" entries in the v2 column
come from the era probes in §2.1; v3/adv/trad/PBP per-season coverage is the list in §3; 2024-25 and
2025-26 v2 behaviour is §2.4.

**Bottom line for the DB build:** `LeagueGameFinder(season)` for game logs (season-by-season,
`SEASON_ID`-filtered), `boxscoresummaryv3` for inactives + officials, `boxscoretraditionalv3` +
`boxscoreadvancedv3` for box scores, `playbyplayv3` if PBP is wanted — **the same v3 code path for
every season from 2005-06 to 2025-26**, with per-game exception handling and retry-on-empty-body.

---

## 6. Working snippets (copy-paste; each one is what actually ran here)

```python
# --- 1. season game log (complete for 2005-06 .. 2025-26, 8/8 seasons verified)
from nba_api.stats.endpoints import leaguegamefinder
d = leaguegamefinder.LeagueGameFinder(season_nullable="2025-26", league_id_nullable="00",
                                      player_or_team_abbreviation="T", timeout=40).get_dict()
rs = d["resultSets"][0]
h, rows = rs["headers"], rs["rowSet"]
i_sid, i_gid = h.index("SEASON_ID"), h.index("GAME_ID")
regular = [r for r in rows if str(r[i_sid]) == "22025"]        # 2460 rows = 1230 games
```

```python
# --- 1b. alternative game list (only endpoint that also shows cancelled games)
from nba_api.stats.endpoints import leaguegamelog
d = leaguegamelog.LeagueGameLog(season="2012-13", season_type_all_star="Regular Season",
                                league_id="00", timeout=40).get_dict()
rows = d["resultSets"][0]["rowSet"]
played = [r for r in rows if r[h.index("WL")] is not None]      # 1229 games after dropping 0021201214
```

```python
# --- 2. inactives + officials, EVERY era: boxscoresummaryv3
from nba_api.stats.endpoints import boxscoresummaryv3
obj = boxscoresummaryv3.BoxScoreSummaryV3(game_id="0020501226", timeout=40)   # 2005-06 game
sets = obj.nba_response.get_data_sets(obj.endpoint)     # {'InactivePlayers': {...}, 'Officials': {...}, ...}
s_in  = sets["InactivePlayers"]        # headers: gameId, teamId, personId, firstName, familyName, jerseyNum
s_off = sets["Officials"]              # headers: gameId, personId, name, nameI, firstName, familyName, jerseyNum
inactive_rows, officials_rows = s_in["data"], s_off["data"]     # 6 and 3 for this game
# NOTE: obj.get_dict()["resultSets"] does NOT exist on v3 endpoints and returns nothing silently.
```

```python
# --- 2b. the pre-2025 equivalent (still the only v2 call worth keeping)
from nba_api.stats.endpoints import boxscoresummaryv2
d = boxscoresummaryv2.BoxScoreSummaryV2(game_id="0021201218", timeout=40).get_dict()   # 2012-13
sets = {s["name"]: s for s in d["resultSets"]}
inactive = sets["InactivePlayers"]["rowSet"]     # 5 rows
officials = sets["Officials"]["rowSet"]          # 3 rows
# Do NOT use this for game dates >= 2025-04-10: it returns the same 9 result sets with 0 rows.
```

```python
# --- 3. 2025-26 ingest: the v3 triple
from nba_api.stats.endpoints import boxscoreadvancedv3, boxscoretraditionalv3
gid = "0022501191"
trad = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=gid, timeout=40)
adv  = boxscoreadvancedv3.BoxScoreAdvancedV3(game_id=gid, timeout=40)
trad.nba_response.get_data_sets("boxscoretraditionalv3")   # PlayerStats 26, TeamStats 2, TeamStarterBenchStats 4
adv.nba_response.get_data_sets("boxscoreadvancedv3")       # PlayerStats 26, TeamStats 2
# or via attributes: trad.player_stats / trad.team_stats / adv.player_stats / adv.team_stats
```

```python
# --- 3b. play-by-play, both ends of the history
from nba_api.stats.endpoints import playbyplayv3
pbp = playbyplayv3.PlayByPlayV3(game_id="0020501226", timeout=40)     # 2005-06 -> 438 events
pbp = playbyplayv3.PlayByPlayV3(game_id="0022501191", timeout=40)     # 2025-26 -> 486 events
events = pbp.nba_response.get_data_sets("playbyplayv3")["PlayByPlay"]["data"]
```

```python
# --- 4. retry wrapper for the flaky-empty-body failure mode (and any per-game failure)
import time
def robust(fn, tries=3, **kw):
    for attempt in range(1, tries + 1):
        try:
            return fn(**kw)
        except Exception:
            if attempt == tries:
                raise
            time.sleep(2 * attempt)
```

---

## 7. What this probe does **not** prove (be honest downstream)

- **Sampling, not exhaustive enumeration.** Full-payload checks were done for 8 seasons of game logs
  and 145 distinct game ids across the sampled seasons (of which 80 games carry the inactive/official
  population statistics, plus a 3-games-per-season sweep). Rare holes (e.g. the 2005-06 opener) exist;
  the ingester must record empty-inactive games rather than assume they are errors.
- **Only these seasons were probed** (2005-06, 2008-09, 2012-13, 2016-17, 2018-19, 2019-20, 2020-21,
  2023-24, 2024-25, 2025-26). Other seasons are inferred from the two endpoints' behaviour, not
  measured. Re-run `python docs/phase0/probe_nba_api.py season-log` with an edited `SEASONS` list to
  extend coverage — it is a one-line change.
- **The 30,000-row cap was observed once** (two independent calls hit it); the exact cap value is an
  inference from `len(rowSet) == 30000` twice. The ingest rule "query season-by-season" holds either way.
- **Wall-time numbers are from 2026-09-21 on this machine/network only.** stats.nba.com latency and
  flakiness vary by time of day and are known to degrade on game nights.
- **Roster/referee semantics of the columns are as delivered**; I did not reconcile `personId` values
  against `commonplayerinfo` or check that inactive rows are exactly "pre-game" filings (they are the
  official box-score inactive list, which is what the pipeline wants, retrieved post-hoc).
- **No 429 was ever seen**, but that does not prove a rate limit does not exist — it proves that the
  ~150 live calls recorded in this one session did not trigger one.

## 8. Raw evidence index (`probe-nba-api-raw.json`)

| key | contents |
|---|---|
| `season_log` | per-season `LeagueGameFinder` row counts, prefix counts, date range, seconds, warnings |
| `game_ids` | first/last regular-season game id per probed season |
| `era_boxscores` | 2005-06 / 2012-13 / 2018-19: v3 + v2 result sets, inactive/official rows, headers, samples |
| `era_sweep` | v3 across 8 seasons × 3 games (first/middle/last) |
| `inactive_sample` | 10 games × 8 seasons, per-game inactive/official rows + per-season summary |
| `modern_v3` | 2025-26 `boxscoresummaryv3` / `boxscoretraditionalv3` / `boxscoreadvancedv3`, headers, top-level keys |
| `modern_v2` | 2025-26 `boxscoretraditionalv2` / `boxscoreadvancedv2` / `boxscoresummaryv2` / `playbyplayv2` |
| `v2_v3_boundary` | v2 vs v3 inactives/officials across 2025-04-10 (2024-25 and 2025-26 games) |
| `v3_old` | `boxscoretraditionalv3` / `boxscoreadvancedv3` on 2005-06, 2012-13, 2016-17, 2019-20 |
| `raw_http` | raw response bodies/byte lengths for the v2 endpoints that return `{}` or empty rows |
| `scoreboard_raw` | `scoreboardv2` vs `scoreboardv3` across 7 dates, incl. the 0-byte-body cases |
| `throttle` | the two 10-call timing blocks, per-call seconds, failure/429 counts |
| `cap_check` | the 30,000-row multi-season call (dates covered, delivered first/last rows) |
| `gap_2012_13` | the never-played game `0021201214`: gamelog rows, gamefinder absence, v3 null summary |
| `probes` | flat 150-record log: phase, probe name, PASS/FAIL, detail, seconds, warnings |

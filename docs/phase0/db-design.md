# Phase 0 — DB and storage design (as-of by construction)

Decisions locked here so the ingest code has a target. Measured storage/rate numbers are appended in §8 once the
environment probe lands.

## 1. Engine and layout

| Item | Decision | Why |
|---|---|---|
| Engine | **SQLite** (stdlib `sqlite3`) | No new dependency (DuckDB is not installed), single file, transactional, plenty for ~27k games |
| File | `sports/nba/data/nba.sqlite` | Per-sport data dir; tennis `data/` stays untouched (refactor step 14 skipped deliberately) |
| Mirrors | `sports/nba/data/parquet/*.parquet` for feature and odds tables | Fast reload, human-inspectable, cheap to diff |
| Raw cache | `sports/nba/data/raw/<endpoint>/<key>.json.gz` — **pruned after parse** | Reproducibility during a run without keeping 20+ GB of API payloads |
| Migrations | `sports/nba/db/schema.sql` + `meta.schema_version` | One-command rebuild; schema diffs are reviewable |
| Rebuild | `python -m sports.nba.db.build --from-empty` (idempotent, resumable) | The Phase 2 gate |

Why not Postgres: single-user analysis, no server, no deployment target yet. If the site later needs concurrent
writes we revisit — not now (YAGNI).

## 2. As-of discipline (the rule everything else bends to)

1. Every row carries `asof_ts` (UTC): the moment the fact became knowable.
2. A feature for game *G* may read only rows with `asof_ts < tipoff_ts(G)`. Enforced by the leakage property test:
   rebuild features from a DB truncated at `tipoff_ts(G)` and assert equality with the full-DB value.
3. `predictions` carries `logged_at`, and the invariant `logged_at < tipoff_ts` is enforced in code and asserted by
   the integrity suite (the pre-registration's "no retro-fitting" rule, made mechanical).
4. Banned as inputs (per pre-registration §8): `ESPN summary.injuries` (returned current-day data for a 2020 game —
   verified), any in-game event, any post-hoc participation fact.

## 3. Tables

```
teams(team_id PK, abbr, name, city, conference, division)
players(player_id PK, full_name, first_name, last_name, from_year, to_year, is_active)
seasons(season PK, start_year, end_year, n_games_known, regime_flags JSON)   -- covid, empty_arena, rule_break, partial
games(game_id PK, season FK, season_type, game_date, tipoff_ts, home_team_id, away_team_id,
      home_score, away_score, ot_count, arena_id, attendance, officials_available BOOL, asof_ts)
game_traditional(game_id, team_id, player_id, started BOOL, min, pts, reb, ast, stl, blk, tov, pf,
      fgm, fga, fg3m, fg3a, ftm, fta, plus_minus, asof_ts)      PK(game_id, player_id)
game_advanced(game_id, team_id, player_id, off_rating, def_rating, net_rating, ts_pct, usg_pct,
      pace, pie, asof_ts)                                        PK(game_id, player_id)
game_inactives(game_id, team_id, player_id, reason TEXT NULL, asof_ts)   -- PRE-GAME state
game_officials(game_id, official_id, first_name, last_name, jersey_num, asof_ts)
rosters(player_id, team_id, from_date, to_date, asof_ts)          -- as-of roster membership
transactions(txn_id PK, txn_date, player_id, from_team_id, to_team_id, type, asof_ts)
odds_snapshots(game_id, source, book, market, side, price_decimal, price_raw TEXT,
      captured_at, snapshot_kind IN ('open','mid','close'), asof_ts)
      PK(game_id, source, book, market, side, snapshot_kind)
ratings_daily(team_id, as_of_date, rating_kind, value, games_played, asof_ts)
features(game_id PK, season, feature_version, payload JSON/parquet-ref, built_at, asof_ts)
predictions(game_id, arm, p_home, p_away, logged_at, model_version, feature_version, notes)
      PK(game_id, arm, model_version)
results(game_id, home_win BOOL, final_margin, settled_at)
sentiment_obs(entity, entity_key, obs_date, source, value, asof_ts)   -- Phase 5 arm only
meta(key PK, value)
```

Notes:
* `game_inactives` is the availability pillar. **Verified present for 2005-06, 2012-13, 2018-19 and 2024-25 games**
  via the box-score summary payload (6/5/8 inactive rows respectively), so availability is buildable across the
  whole history — the pre-registration's 2017-18 fallback is not needed for the *fact* of unavailability, only for
  the *reason/duration* metadata.
* `odds_snapshots` keeps raw strings alongside parsed decimals so a parsing bug is auditable.
* One row per (game, arm, model_version) in `predictions` means re-running an arm cannot silently overwrite an
  earlier logged prediction — the live 2026-27 log depends on that.

## 4. Ingest endpoint strategy (from the probes, updated after verification)

**Single code path: the v3 endpoint family, for every season 2005-06 → 2025-26.**

| Data | Endpoint | Verified coverage |
|---|---|---|
| Game logs | `leaguegamefinder.LeagueGameFinder(season_nullable='YYYY-YY', league_id_nullable='00')` | PASS 8/8 sampled seasons; rows/2 = games (1230 typical, 1229 in 2012-13 — one game was cancelled, 1059 in 2019-20, 1080 in 2020-21) |
| Box score + inactives + officials | `boxscoresummaryv3.BoxScoreSummaryV3` | 9 datasets incl. `inactive_players` + `officials` for 2005-06, 2012-13, 2018-19 and 2025-26 |
| Player box score / advanced | `boxscoretraditionalv3`, `boxscoreadvancedv3` | PASS, including 2025-26 |
| Play-by-play (not ingested in Phase 2) | `playbyplayv3` | PASS; 438 events for a 2005-06 game |

v2 is dead: `boxscoretraditionalv2` returns 0 rows for 2025-26, `boxscoreadvancedv2`/`playbyplayv2` return literal `{}`,
and `boxscoresummaryv2` silently returns empty Officials/InactivePlayers after 2025-04-09 (sporadic before).
**Do not build a v2 path.** Filter game logs to regular season with `SEASON_ID == '2' + YYYY`.

Hard limits found:

* **LeagueGameFinder caps at 30,000 rows and returns newest-first** — an all-seasons call silently drops the oldest
  seasons. Always query season-by-season (21 calls; a single-season call is ~5 s for ~15k rows).
* v3 responses have **no `resultSets` key**; read them via `nba_response.get_data_sets(endpoint)`.
* Flakiness is content-level, not rate-level: 0-byte bodies occur occasionally (e.g. scoreboardv2 3/7 in one run) and
  succeed on retry. No HTTP 429 and no timeout in 150+ probe calls. Every call gets 3 attempts (2s/5s/15s backoff).

Odds sources (free; full detail in `probe-odds.md`):

| Seasons | Closing line | Opening price | Route |
|---|---|---|---|
| 2005-06, 2006-07 | none | none | burn-in (training only) |
| 2007-08 → 2012-13 | SBR `Close` (ML + spread) | open spread only (**no opening ML**) | SBR HTML via the r.jina.ai reader |
| 2013-14 → 2016-17 | ESPN book quotes | ESPN `'Opening'` pseudo-provider (ML, spread, total) | `sports.core.api.espn.com` |
| 2017-18 → 2022-23 | ESPN book quotes | teamrankings movement series (timestamped ML open→close) | core API `/odds/1002/history/0/movement` |
| 2023-24 → 2025-26 | ESPN book quotes (thin: 2024-25 = ESPN BET only, 2025-26 = DraftKings only) | per-book `.open` | core API |

So the closing-line benchmark is testable 2007-08 → 2025-26, and the **opening-line benchmark (Tier 2) is testable
2013-14 → 2025-26** — wider than the pre-registration assumed (see its deviations log).

Transport rules, both measured the hard way:

1. **Use `nba_api`** (or its exact `STATS_HEADERS`: 12 headers including `Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`,
   `Sec-Fetch-Dest`, `Connection`, `Host`, `Accept-Encoding`). A partial header set produces a **~20 s silent stall
   that looks exactly like an outage** — this cost two probe rounds to root-cause.
2. **Reuse a `requests.Session`.** `raw.githubusercontent.com` costs ~21 s per fresh TCP connection and 0.05 s when
   the connection is reused — a downloader that skips session reuse turns a 3-minute job into hours.

SBR parser rule (from its HTML): the favourite's row carries the spread and the underdog's row carries the total —
**decide by the sign of ML**, not by the V/H column.


## 5. Idempotency and resume

* Every ingest writes into `raw/` first, then parses into the DB inside one transaction per game (or per season for
  game logs).
* Re-running a completed unit is a no-op (PK conflict → skip), so a crashed 6-hour ingest resumes by re-running.
* `meta` records `season:<year>:<table>:complete = true` per unit; the build command reports which units are done.

## 6. Pruning policy

| Tier | Kept? |
|---|---|
| Game logs, box scores, inactives, officials, odds | **Yes** — these are the DB |
| Raw API JSON | Pruned after successful parse; retained only for games whose parse failed |
| Play-by-play | **Not ingested in Phase 2** — only if a feature later proves it needs it (it also multiplies the DB) |
| Social dumps | Never stored (streamed, aggregated, discarded) — per the ~3.46 TB corpus reality |

## 7. Verification (Phase 2 gate)

1. Per-season game counts match known totals: 1,230 typical, 990 (2011-12), 1,229 (2012-13), 1,059 (2019-20),
   1,080 (2020-21), 1,230 (2021-22+).
2. No null `home_score`/`away_score`; `tipoff_ts` non-null for every game.
3. PK uniqueness holds on every table (test asserts a second ingest inserts nothing).
4. `predictions.logged_at < games.tipoff_ts` for every row.
5. Leakage property test: for N sampled games per era, rebuild features from a truncated DB and assert equality.
6. `game_inactives` coverage report per season (rows per game, min/median/max) — must be ≥ 1 for ~all games post-2005.

## 8. Measured numbers (environment probe, 2026-09-21/22)

| Quantity | Value | Note |
|---|---|---|
| free disk | **73 GB** on a single drive (93% full) | measured, not assumed |
| box-score summary payload | mean **20,377 B/game** | v3 |
| inactives | mean 619 B/game; **79/80 sampled games have ≥ 1 row** | one 2005-06 game has the set but 0 rows |
| parquet compression | ratio 0.32 (box scores), 0.12 (PBP) | measured |
| DB size, logs + box + inactives, 21 seasons | ~545 MB raw / **~190 MB parquet** | target size |
| + play-by-play | ~7.2 GB raw / ~1.0 GB parquet | not ingested in Phase 2 |
| + raw JSONL caches at peak | ~8.5 GB = 11.7% of free disk | worst case, before pruning |
| nba_api latency | mean 0.70 s incl. cold start; **0.50 s steady state** | 10-call sample |
| ingest, box scores only (27.5k games) | 3.8 h no sleep / 8.4 h at 0.6 s sleeps | single-threaded |
| ingest, summary + traditional + advanced | **~12.2 h single-threaded; ~2–2.5 h at 6 workers** | the number that matters |
| HTTP 429 / timeouts in 150+ probe calls | **0** | the risk is content flakiness, not throttling |

Pruning policy: prune each season's raw cache once its parquet is validated (peak raw 7.4 GB → ~360 MB).

Interpreter: the repo had **no venv** — dependencies were resolving to Hermes's own interpreter, so the ingest would
not have been reproducible and could be broken by another process upgrading a shared env. `.venv/` is now created
from the pinned `requirements.txt`; use `.venv/Scripts/python.exe -m pytest` (see `verification-notes.md`).


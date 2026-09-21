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

## 4. Ingest endpoint strategy (from the probes)

| Data | Endpoint | Era notes |
|---|---|---|
| Game logs | `leaguegamefinder.LeagueGameFinder(season_nullable=...)` | Primary bulk path; per-season, resumable |
| Box score + inactives + officials | `boxscoresummaryv2.BoxScoreSummaryV2` | Works 2005-06 → ~2025-04-10 |
| Modern seasons | `boxscoresummaryv3.BoxScoreSummaryV3` | Required after the V2 cut-off; returns `inactive_players`/`officials` |
| Player box score | `boxscoretraditionalv2` / `…v3` | V2 no longer published for 2025-26 — use V3 there |

Retry policy: nba_api calls have shown **cold-start flakiness** (first V3 call and the first 2013 scoreboard call
returned empty/non-JSON, then succeeded on retry). Every call gets 3 attempts with 2s/5s/15s backoff, and a
`<endpoint>.failures.jsonl` log so a partial ingest is visible rather than silently incomplete.

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

## 8. Measured numbers (filled from the environment probe)

| Quantity | Value | Source |
|---|---|---|
| bytes per game (summary payload) | _pending_ | env probe |
| projected DB size, 21 seasons, no PBP | _pending_ | env probe |
| seconds per nba_api call (10-call sample) | _pending_ | env probe |
| projected full ingest wall-clock | _pending_ | env probe |
| free disk at design time | 62 GB on a single drive | measured 2026-09-21 |

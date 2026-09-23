# Data cards — the six major NBA datasets

Every row below is queryable in `sports/nba/data/nba.sqlite`. Counts are from
the as-ingested DB at v1 freeze time (re-verify with
`python -m pytest tests/evidence -q`).

## 1. `games` — 25,268 rows

- **Span**: 2005-06 → 2025-26, regular season only (no playoffs, no play-in).
- **Grain**: one row per game (game_id, game_date, season, teams, scores,
  tipoff_ts, season_type).
- **Source**: nba_api `leaguegamefinder` (21 season-by-season calls — the
  all-seasons call silently drops old seasons at the 30k row cap) +
  `boxscoresummaryv3` completion.
- **Gaps**: 2005-06 & 2006-07 have no odds anywhere (burn-in seasons). 87
  games lack inactive rows. 2025-26's final games were already known at
  commit time (documented bias; only 2022-23→2024-25 is strictly
  hindsight-free).

## 2. `odds_snapshots` — 421,101 rows (14,402 games with open+close)

- **Span**: closing 2007-08 → 2025-26; opening 2013-14 → 2025-26.
- **Grain**: one row per book per game per snapshot (open/mid/close), moneyline
  American odds + book identity + `asof_ts`.
- **Source**: The Odds API (historical), a local SBR-historical reader
  (2007-08→2012-13 closes), ESPN odds providers v3 (2013-14+), teamrankings.com
  line-movement series (opener proxy).
- **Gaps**: thin book coverage 2024-25/2025-26; the 2017-18→2022-23 "opening"
  prices are a teamrankings **proxy**, not true openers (paid data would fix —
  nothing gets bought without the owner's explicit yes). SBR has no opening
  moneyline (its ML column tracks the close). Contamination guard:
  `LIVE_LIKE`/`MODEL_FEEDS` book filters keep post-tip market data out of
  pre-tip arms (regression-tested).

## 3. `team_game_stats` — 50,536 rows (box-score layer)

- **Span**: 2005-06 → 2025-26 (25,268 games).
- **Grain**: one row per team per game (traditional box: shooting, rebounds,
  turnovers, etc.).
- **Source**: `boxscoretraditionalv3` / `boxscoresummaryv3` (v3 only — the v2
  endpoints return 0 rows or literal `{}` for recent seasons).
- **Gaps**: none material; v2 dead (verified), so no v2 cross-check exists.

## 4. `game_inactives` — 25,181 rows

- **Span**: 2005-06 → 2025-26 (whole history — verified 79/80 sampled games
  from every era have inactive rows).
- **Grain**: one row per inactive player per game.
- **Source**: `boxscoresummaryv3.inactive_players`.
- **Gaps**: 87 games without rows (treated as *unknown*, never as zero).

## 5. `game_officials` — 67,332 rows

- **Span**: 2005-06 → 2025-26 (≈3 officials/game everywhere).
- **Grain**: one row per referee per game.
- **Source**: `boxscoresummaryv3.officials`.

## 6. `features` — 25,258 rows (as-of, v1 and v2)

- **Span**: 2005-06 → 2025-26.
- **Grain**: one row per game; the seven formula features (v1: elo_diff, hca,
  form_margin_diff, net_rtg_diff, rest_diff, b2b_away, is_neutral) and the
  v2 availability extension (avail_diff).
- **Construction**: replayed strictly as-of — each row is built from history
  *before* its own tip-off; walkovers update nothing; availability unknowns
  are `None`, never 0.0 (replacement-level prior instead).
- **Provenance**: `tests/core/test_asof_snapshot.py` + the map-8 gate rebuilt
  `features.parquet` byte-identically (hash `ffe140a039db4f24`, 16,165×42).

## Excluded sources (ToS)

- **Basketball-Reference** — its data-use page forbids creating a competing
  database and using its data to support machine-learning prediction
  (verified verbatim in Phase 0).
- **OddsPortal** — scraping excluded on the same class of grounds.
- **Kaggle mirror** — captcha wall + wrong seasons + no moneyline after Jan
  2023; unusable here without owner-supplied credentials.

## T-60 injury-report channel — acquisition probe (plan Task 6, 2026-09-23)

Question: can the T-60 official injury report (pre-game PROBABLE / QUESTIONABLE
statuses) be acquired **free** — historical archive for gating, live capture for
2026-27? Probe result:

- **Live capture (2026-27): FREE.** The official NBA injury report is published
  daily (official.nba.com; scrapable HTML/PDF) and team final reports are public.
  A free daily capture can run inside the Task-8 live-ops loop as descriptive
  logging. Cost: zero. (Nothing here is bought — the ruling is free-only.)
- **Historical archive (needed to backtest the channel and pass the §13
  arm-admission gate on 2021-22): NO CLEAN FREE SOURCE.**
  - Official NBA: publishes daily; **no official archive**.
  - Kaggle scraped sets (inactive-list activity 2010–2020; injury stats
    1951–2023): transaction-type records of inactive-list moves, **not** daily
    pre-game reports with statuses — and the "who actually sat" side is already
    covered by our box-score `game_inactives` (25,181 rows).
  - Wayback Machine snapshots of the official report page: coverage unverified
    and known to be sporadic (the CDX probe timed out from here; even complete
    CDX coverage would be a partial, unaligned sample).
  - **Paid only**: Sportradar NBA Daily Injuries (per-date history behind a paid
    key), SportsDataIO Vault ("10+ years historical — contact sales"; free trial
    is scrambled/fictional data, explicitly not for analysis), Swish/similar.
- **Consequence under the free-only ruling**: the T-60 injury channel cannot be
  gated on tune 2021-22 (no data), therefore **it cannot enter the Phase-2
  freeze and cannot be a claim arm**. Live capture may still run during 2026-27
  as descriptive evidence for follow-up work. Reopening this requires an
  explicit yes to a paid archive (fresh decision; nothing gets bought without it).

# Pre-registration — NBA moneyline winner model

**Committed before any model fit.** This file defines what we will measure, on what data, and what we will
be allowed to claim. Once the test seasons (2022-23 → 2025-26) are evaluated, nothing in the *Frozen* section
below may change. Anything that does change must be appended to §9 Deviations with a date and a reason.

Status: Phase 0 · repo `vibe-odds` (sport-agnostic refactor) · owner: Aditya Singh · written 2026-09-21.

---

## 1. Task and scope

| Item | Value |
|---|---|
| Target | NBA **moneyline game winner** (incl. overtime) |
| Block | **Regular season only** — no playoffs, no play-in |
| History | seasons 2005-06 → 2025-26 (games from 2005-06 onward) |
| Burn-in | 2005-06 and 2006-07: training data only, **no market benchmark** (no free odds exist for them) |
| Market benchmark | closing line (no-vig fair probability), with the opening line as a separate comparison |
| Out of scope | totals, spreads as a target, player props, in-play, betting execution, other leagues |

## 2. Split (frozen)

| Block | Seasons | Use |
|---|---|---|
| Train | 2005-06 → 2020-21 | fit formula structure and ratings |
| **Tune** | **2021-22** | hyperparameters, feature choices, recency half-life, σ_d measurement |
| Test | 2022-23, 2023-24, 2024-25, 2025-26 | walk-forward evaluation only — **no tuning, ever** |

Honesty note: these test seasons are **pseudo-out-of-sample** — we know their outcomes today, and Diebold's own
warning applies (pseudo-OOS methods "expand the scope for data mining... because one can also mine over the sample
split point"). The split is therefore pre-registered here, and a **genuine** forward record begins with the
2026-27 season, logged pre-tip by the running service (§7).

## 3. Arms (frozen)

A1 climatological · A2 home-rate · A3 Elo-only · A4 ratings-only (no market, no availability) ·
**A5 the formula** (market-free, transparent logistic with named coefficients) · A6 formula + availability ·
A7 LightGBM · A8 Hedge ensemble · A9 market (closing line) · A10 external benchmark (538's published game archive,
recomputed locally; never redistributed).

The formula arm never consumes a market price. Market prices appear only in A9 and in the CLV diagnostic.

## 4. Metrics (frozen)

1. **Brier score** (primary) and **log loss** — proper scoring rules, per game, averaged per season and pooled.
2. **Calibration:** expected calibration error + reliability bins (out-of-fold deciles), per season.
3. **Accuracy** with a **Wilson score interval** (reported, never the headline; 1pp ≈ 15,400 games to detect).
4. **Confidence slices:** ≥ 0.60 and ≥ 0.65 predicted probability (n, accuracy, Brier).
5. **CLV** on logged predictions vs the closing line (low-variance diagnostic; converges in ~50-100 observations).
6. **Ablation:** drop-one-signal ΔBrier, pooled and per season.

Accuracy-only claims are prohibited; the headline is Brier + calibration against the market.

## 5. σ_d lock (frozen procedure)

The paired per-game loss difference `d_t = L_market,t − L_model,t` is the input to the significance test.
Its standard deviation σ_d **must be measured on 2021-22, written into the table below, and committed before any
test season is evaluated.** It may not be re-estimated on test seasons.

| Quantity | Value | Measured on |
|---|---|---|
| σ_d (formula A5 vs market close) | **0.08497** (n = 1,227) | 2021-22, committed 2026-09-22 |
| needed n for δ = 0.005 at α=.05, 80% power | **n ≈ 2,267 games** (= 7.84·σ_d²/0.005²) | derived from the locked σ_d |

Measured with `python -m sports.nba.model.formula --sigma` (paired per-game Brier difference
`d_t = L_market,t − L_model,t` on the tuning season). Our test block (2022-23 → 2025-26, ≈ 4,900 games with
market prices) is therefore powered to detect δ ≈ 0.005 — the pre-registered test is feasible.

**Disclosure (recorded 2026-09-22):** the development report printed per-season and pooled *descriptive*
metrics in the same run that measured σ_d, so those numbers were visible before this lock. The
claim-bearing statistic is still produced once, by the pre-registered procedure below, from the frozen
ledger — but the exposure is disclosed here rather than hidden.

## 6. Significance test (frozen)

* Test: **paired bootstrap Diebold-Mariano** on per-game Brier differences, **blocked by game date**
  (block length ≈ T^(1/3) ≈ 10 games) to respect same-day correlation, plus a sign test as a median-sign check.
* α = 0.05 two-sided; report effect size and CI, not just a p-value.
* Pooled over 2022-23 → 2025-26 (~6,000 games) for the headline test; per-season results reported but not
  cherry-picked.
* **No peeking:** the fixed-sample test is read once. If we monitor during the season, we use a sequential
  procedure (Wald SPRT), never repeated peeks at a fixed-sample p-value.

## 7. Claim tiers (frozen)

| Tier | Claim | Pass condition |
|---|---|---|
| **T1** | Calibrated and better than naive baselines | Brier below climatological (≈0.248 at a 54.3% home rate) and below Elo-only on the pooled test seasons |
| **T2** | Beats the **opening** line in the availability/information zone (2017-18+, where pre-game availability data exists) | pooled paired test favours the formula at α=0.05 |
| **T3** | Beats the **closing** line | pooled blocked paired test favours the formula at α=0.05 — **claimed only if it does**; otherwise the measured gap is published |

If a tier fails, the failure is published in the README with the same prominence as a pass. No tier may be
redefined after seeing results.

## 8. Leakage rules (frozen)

1. Every ingested row carries `asof_ts`; a feature may only read rows with `asof_ts < tipoff_ts`.
2. **Banned sources:** `ESPN summary.injuries` (returns current-day data for old games — verified), any in-game
   event, any post-hoc participation fact (who actually played, minutes played, mid-game injuries).
   Availability may use only pre-tip state (inactive lists, rosters, prior-games histories).
3. The 2025-26 season is included in the test block; its final games are known at commit time, which is stated
   openly in the README rather than hidden.
4. The leakage property test (rebuild features from a DB truncated at each game's tip-off, assert equality) is a
   required gate, not an optional check.

## 9. Deviations log

Any change to a frozen item is recorded here with date and reason.

| Date | Item | Change | Reason |
|---|---|---|---|
| 2026-09-22 | §7 Tier 2 window | "2017-18+" → **"2013-14 → 2025-26"** | Phase 0 probes verified that opening prices are obtainable for 2013-14→2016-17 (ESPN `'Opening'` provider) and 2017-18→2022-23 (teamrankings timestamped movement series) as well as 2023-24+ (per-book `.open`), and that pre-game inactive lists exist for every season back to 2005-06. The old window was set by an assumption about injury-report availability that turned out to be unnecessary. No model has been fitted; no results have been seen. |
| 2026-09-22 | §4 market arm data | closing-line coverage fixed as **2007-08 → 2025-26** (SBR via reader 2007-08→2012-13; ESPN core 2013-14→2025-26); 2005-06/2006-07 remain burn-in with no market benchmark | Phase 0 probe results (`docs/phase0/probe-odds.md`, `verification-notes.md`). |

## 10. Licensing and data ethics (frozen)

* Basketball-Reference and OddsPortal are **excluded** (their terms forbid this use); we do not scrape them.
* Third-party datasets are used only under their licence; nothing third-party is redistributed in the repo
  unless the licence permits it.
* Predictions are logged and published with timestamps. No retro-fitting, no post-hoc pick selection.
* This is an information/measurement project: **no betting execution, no staking advice, no ROI claims.**

## 11. Frozen artifacts

Per-season ledgers (JSON), the coefficient tables, the ablation table and the significance output are written to
`data/` with sha256 manifests and guarded by `tests/evidence/` — a number may only move in a commit that rebaselines
the hash **and** says in the message which number moved and why.

## 12. Relation to the guard work already committed

The evidence guard (`tests/evidence/`, `tools/check_claims.py`, conftest write-redirection) was committed *before*
this file. That is deliberate and disclosed: the guard is infrastructure protecting the tennis project's published
numbers, not a model fit. **No NBA model has been fitted, tuned or evaluated at the time of this commit.**

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
| 2026-09-22 | §4 market arm *definition* + ledger regeneration | The closing arm now excludes (a) in-play feed rows (`… - Live Odds`) and (b) model feeds (teamrankings/numberfire/accuscore/consensus); the opening arm keeps the teamrankings movement series as the only free opener proxy for 2017-18→2022-23, with that limitation stated. The T1/T2/T3 ledger was regenerated after the fix. | **Bug found by executing the test:** including in-play providers inflated the 2024-25 closing line to a Brier of 0.164 with 75.3% accuracy — impossible for a real market, because an in-play price encodes the game state. Both ledgers are kept; the pre-fix version is `nba_walkforward_v1_contaminated.json`. The direction of every verdict is unchanged by the fix (the market gets *worse* without the leaked rows, so our gaps narrow). |
| 2026-09-22 | §5–§7 exploratory A6 arm + test-statistic blocking | The availability arm (A6) is reported as **exploratory only** (separate artifact `nba_availability_v1.json`, carrying a disclaimer field); its paired tests use **490 blocks of 10 consecutive games by date** rather than the season × month blocks of the registered A5 ledger. Registered T1/T2/T3 verdicts remain about A5 and stand as published. | The arm was specified in the plan before fitting, but its blocking scheme was not frozen in this file. Logged at first reporting of A6 results: A6 beats A5 (+0.00263, CI [0.00146, 0.00383]), the opening line still beats A6 (−0.00425, CI [−0.00678, −0.00188]); gap to the opener narrows 0.00723 → 0.00460 without closing. |
| 2026-09-22 | σ_d **lock derivation** | The registered lock **σ_d = 0.08497** was computed on the tuning season against the *pre-fix* (contaminated) market arm. Recomputed against the books-only arm with current code it is **0.08522**. The lock stands as registered — locks do not move — and both values are carried with their provenance in `tests/evidence/nba_published_numbers.py` (SIGMA_D_LOCK / SIGMA_D_CURRENT_CODE). | Found by the clean-room re-fit audit. A lock is a lock: the pre-registration freezes the value computed at lock time. No verdict changes — the claim-ledger CIs use per-test σ_d, and the claim ledger regenerates byte-identically from code. Disclosed rather than silently recomputed. |
| 2026-09-22 | `formula_v1_report.json` staleness (§11 rebase) | One frozen artifact was generated at 00:55 — mid-way through the odds ingest (which completed 01:05) and before the market-arm contamination fix — so it carried market rows of n=0 for 2024-25/2025-26 and the pre-fix tuning stats. It is regenerated and its sha256 re-based in the manifest, with the moved numbers (tuning σ_d 0.08497→0.08522, tuning mean_diff −0.01215→−0.01171, 2024-25/2025-26 market rows 0→1218/1219) named in the commit message per §11. | Audit finding: a clean-room re-fit reproduced 8 of 9 artifacts byte-identically (including every claim ledger); this secondary report was the stale one. Published claims were never sourced from it. A staleness-consistency regression test now guards this failure class. |
| 2026-09-22 | Calibration **operating decision** | The bake-off's pre-stated rule ("ship a calibrator only if it beats raw on the tune season") selected isotonic (A5) and Platt (A6). The single frozen-test evaluation shows **raw probabilities are better**: A5 raw Brier 0.21303/ECE 0.01307 vs isotonic 0.21452/0.02883 (paired −0.00149, CI [−0.00236, −0.00065], significant for raw); A6 raw 0.21053/0.01561 vs Platt 0.21091/0.0255 (paired −0.00038, CI [−0.00088, 0.00013], inconclusive). **We operate raw for both arms** and report the calibrators as tested-and-rejected. | The rule's evaluation set was the same season the calibrators were fitted on — in-sample optimism (isotonic reached a tune ECE of 0.0 by construction). The honest out-of-sample verdict is the test block, where the calibrators lose or tie with worse ECE. Logged rather than quietly relabelled; `nba_calibration_v1.json` carries both numbers unmodified. |
| 2026-09-22 | §4 calibration | Calibration bake-off run on the 2021-22 tune season only (raw / Platt / isotonic, fitted and scored on 2021-22; A5 and A6). Locked rule applied: A5 ships **isotonic** (tune Brier 0.21682 < raw 0.22058, ECE 0.0 ≤ 0.02288) and A6 ships **Platt** (0.21558 < 0.21591, ECE 0.0238 ≤ 0.02645). Both shipped variants are worse than raw on the single frozen-test evaluation (A5 0.21452 vs 0.21303; A6 0.21091 vs 0.21053). Recorded in a new artifact `nba_calibration_v1.json`; the registered A5 ledger is untouched. | Last modelling decision before the refactor. The decision was made on the tune season only; the registered T1/T2/T3 verdicts stay about A5 and are not restated. |

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

## 13. Phase 2: closing the market gap (registered 2026-09-23, before any Phase-2 fit)

**Goal.** Pass T2 (beat the opening line) and T3 (beat the closing line) on paired Brier, prospectively.

**Burned-window rule (non-negotiable).** We have seen 2022-23 → 2025-26 results. No Phase-2 claim is ever tested on
that window: Phase-2 re-scores there are published as **retrospective estimates** with
`"burned_test_set": true, "claim_eligible": false`, never as claim tests. Fit windows stay ≤2020-21, tune 2021-22.

**Tiers.**
* **T2′ (primary):** formula@T-60 vs the market **open** price (continuity with T2).
* **T2″ (fair same-info):** formula@T-60 vs the market price **at T-60** (mid snapshot). A T2′ pass that fails
  T2″ is labelled **news capture, not skill**.
* **T3 (unchanged):** formula@T-60 vs the **close**.
* **T2g / T3g:** the nonlinear arm (monotone GBM + Hedge stack) registered as a **separate** tier — it never
  carries the transparent-formula claim. "Formula" = the locked transparent weighted logistic.

**Arm admission gate.** An arm enters the Phase-2 freeze only if it beats A6 on the 2021-22 tune season
(pre-registered accept rule; recorded in a Phase-2 ledger).

**Arm admission log** (each a tune-only gate run; outcomes, not claims):

| arm | features | tune Brier 21-22 | vs A6 (0.21591) | verdict |
| --- | --- | --- | --- | --- |
| A7 | A6 + player-impact absence values (impact_out_*) | 0.21573 | −0.00018 | **enter** (marginal; the prospective read judges it) |
| A8 | A7 + schedule spots (travel/altitude/4-in-5/homestand) | 0.21598 | +0.00007 | **rejected** — not admitted to the Phase-2 freeze |

A8's rejection is the registered rule working as intended: the schedule-fatigue
story (the one channel with a real T3 mechanism) does not survive as a feature
arm here. Per the pre-registered plan its T3 prior is lowered accordingly; the
T3 claim test will still run exactly once on the 2026-27 season, and a null
result is a first-class, publishable outcome.

**Read schedule and power.** One primary read at the 2026-27 regular-season end (n ≈ 1,230; σ_d lock 0.08497;
detectable Δ ≈ 0.0068 at 80% power). Pre-registered extension: if p < 0.10 but the CI crosses 0, extend to
2027-28 (n ≈ 2,460, detectable Δ ≈ 0.0048) and read once on the pooled window. σ_d re-locked once on 2021-22
before the read. Paired blocked bootstrap as in §7 (10-game blocks, Random(7)). The claim read runs **once**.

**Data decision (frozen 2026-09-23).** Phase 2 is **free-data only**. The X/Twitter firehose was evaluated and
is not free: since 2026-02-06 the X API has no free tier for new developers (pay-per-use ≈ $0.005/read) and
full-archive search / the firehose are Enterprise-only (from ≈ $42,000/month). The pre-registered sentiment arm
therefore remains **expected-null and unbuilt**. Nothing is purchased without explicit approval.

**Kill criteria.** (a) an arm failing the tune gate does not enter the freeze; (b) after the full read window, if
the T2′ / T3 paired CIs still sit below zero → **publish the null and stop** ("cannot beat the close with public
pre-tip information").

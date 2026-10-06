# Phase-3 candidates — A9 (player-value availability)

**Status: unregistered.** Nothing in this document may inform any claim until a Phase-3
registration exists. The 2026-27 read (sealed A7 arm, digest `2bf068c9…`) is untouched by
this work; every fit here uses only seasons ≤ 2025-26, and every burned-window number is
retrospective (`burned_test_set: true`, `claim_eligible: false`).

## What was built (2026-10-07)

A9 = the transparent formula + a regularized player-value absence family (`pv_*`, features
v5), replacing the two crude absence metrics (production share; rolling plus/minus):

- sparse ridge lineup values `V_j` (`margin ~ Σ V_j · minutes-share`, opponent/teammate
  controlled), λ frozen at **3.0** by 5-fold time-blocked CV at the first refit — the
  stability-preferring rule (largest λ within 2% of the CV minimum); pilot split-half
  reliability 0.675 → 0.872 → 0.908 across λ = 1 / 10 / 100;
- k-means archetype shrinkage (K=8, seed 7, m=16 game-equivalents) — empirical-Bayes
  regularization of a sparse estimation problem, not a partition of the signal;
- 20 season-boundary refits, per-refit priors (25th percentile), the same publication rule
  as `availability.py` (authoritative ⇔ ≥1 `game_inactives` or `game_officials` row).

Producers: `sports/nba/features/player_value.py` (v5), `sports/nba/model/phase3.py` (gate),
`sports/nba/model/retro.py --a9` (burned-window diagnostics). Stage-0 feasibility:
`scratch/pv_feasibility.py` — split-half reliability 0.872 at the frozen λ; top-3-by-V vs
top-3-by-production disagreement 97.1% (the family is not a relabelling).

## Gate outcome (tune 2021-22, fit ≤ 2020-21; one run)

| arm | features | tune Brier | vs A6 (0.21591) | verdict |
|---|---|---|---|---|
| **A9** | formula + `pv_*` (replacement) | 0.2162 | +0.00029 | **rejected** |
| **A9b** | A7 + `pv_*` (additive) | **0.21521** | −0.00070 | informational only (never gates) |
| A6 | baseline | 0.21591 | — | reference |
| A7 | Phase-2 freeze arm | 0.21573 | −0.00018 | entered (2026-27 claim) |

Record: `nba_phase3_gate_a9.json`. The replacement form does **not subsume** the old metrics;
the additive form produced the best tune number any arm has shown.

## Burned-window diagnostics (retrospective; 4,897 games)

| arm | Brier | vs market open | vs market close |
|---|---|---|---|
| A9 | 0.21023 | −0.00374 [−0.00650, −0.00099] | −0.00776 [−0.00983, −0.00563] |
| A9b | 0.21026 | −0.00377 [−0.00648, −0.00106] | −0.00777 [−0.00979, −0.00568] |
| A7 | 0.21039 | (see `nba_phase2_retro.json`) | −0.00788 |
| market | open 0.20186 · close 0.20219 | | |

`a9b_vs_a7`: **+0.00013, CI [−0.00034, +0.00065] — indistinguishable.** Record:
`nba_phase3_retro_a9.json`.

## Honest reading

- The additive tune advantage (−0.00070 vs A6) does **not replicate** on the burned window
  (vs A7: +0.00013, CI straddling zero). Treat A9b as a **low-prior candidate**, not a
  demonstrated improvement.
- Neither form closes a meaningful share of the ~0.008 market gap.
- Process note: the deletion test (`test_player_value_leakage.py`) caught two real bug
  classes before any gate ran — a position-keyed value lookup that silently degraded to the
  prior, and a shape-dependent `sparse_cg` float path that leaked the future into
  4th-decimal rounding (fixed by restricting each refit to active columns; the pass is now
  build-invariant, proved by both deletion cuts).

## Registration options (user decision; none executed)

- **R1 — register Phase 3** (e.g. claim season 2027-28, fit ≤ 2026-27 expanding, tune 21-22
  reused): A9b is the strongest tune candidate; the registration should state the low prior
  and the failed burned-window replication explicitly.
- **R2 — extension first**: if the Phase-2 extension triggers (p < 0.10, CI crosses 0), the
  pooled 2026-27 + 2027-28 read (A7, refit) takes priority; Phase-3 waits.
- **R3 — continue exploration** (each with its own pre-stated gate, no shopping):
  recency-weighted fitting; λ/K/m sensitivity work on the value model (diagnostics only
  until a variant is chosen a priori); an opponent-adjusted team-ratings (SRS-style) arm.

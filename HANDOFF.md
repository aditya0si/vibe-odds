# Session Handoff — Vibe-Odds (tennis men's singles prediction system)

Date: 2026-09-07. Project: `C:\Users\oliad\Desktop\vibe-odds`. Status: P0-P3
reliability + honesty overhaul landed, tests 35 green (was 27), sim re-run,
GBM improved and re-frozen. Tennis-only (NBA/soccer paths removed).

## 1. What this is
Odds comparison + ML verdict system for tennis men's singles (US Open first).
Owner posts verdicts to Twitter; bettors are US + EU. 18+, bet responsibly.

## 2. Run it
```bash
cd C:\Users\oliad\Desktop\vibe-odds
pip install -r requirements.txt      # PINNED versions; bump one at a time + pytest
python -m pytest tests/ -q           # must be 35 passed
python -m backend.ratings.snapshot   # one-off state snapshot (kills 3s cold start)
python -m uvicorn backend.api.server:app --port 8000   # UI: http://localhost:8000
```
`.env` holds `ODDS_API_KEY` (The Odds API; quota guard stops live fetch < 25
credits left). `VIBE_API_KEY` (optional): when set, POST /track /settle /learn
/rollback require `X-API-Key` header. No odds key = mock mode (UI shows `feed: mock`).
US Open live keys: `tennis_atp_us_open` (BO5) + `tennis_atp_singles` (BO3).

## 3. Architecture map (changed files marked *)
```
backend/core/odds.py        pure math: conversions, no-vig, best odds, EV, arb, Kelly, CLV
backend/core/io.py        * atomic JSON writes + data/backups/ + rollback()
backend/providers/the_odds_api.py * TENNIS ONLY, locked cache, quota guard, last_status()
backend/ratings/names.py  * canonical() + ALIASES + familiarity() (unknown-player flag)
backend/ratings/snapshot.py * pickle snapshot of elo/h2h/form/points (cold start ~instant)
backend/ratings/            loader (ROUND_ORDER sort), elo, features (+snapshot/restore), names
backend/features/           score parser + as-of feature-row builder -> data/features.parquet
backend/markov/             points.py (+snapshot/restore) + match.py (exact DP, BO3/BO5, TB10)
backend/model/              signals.py (12 signals) + ensemble.py (* per-surface eta_by)
                            + calibrate.py (* per-surface tables v2, backward-compat load)
                            + learn.py (* per-surface fit/report, 200-min-N, global fallback)
                            + serve.py (* hot-reload, verdicts.jsonl log, market-free prob,
                                         canonical names, unknown flag, snapshot fast-path)
                            + upsets.py (atomic log) + backtest.py + ablate.py (* NEW: LOO)
backend/gbm/                train.py (* monotone constraints, Brier early-stop, 2 time-folds)
                            + predict.py (unchanged)
backend/policy/             bandit.py (atomic save; update() still post-only by design)
backend/sim/                freeze.py + season.py (* ratings advance in frozen arm,
                              per-surface cal refit, paper-trading vs declared line,
                              policy exercise + pass counterfactuals, per-surface +
                              confidence tables, --no-cal flag, atomic ledger)
backend/tracker/            store.py (* calibration bins MODEL prob, result/surface indexes)
backend/api/server.py     * tennis-only, auth, /api/rollback, market-free edge/Kelly,
                              health shows provider+quota, feed mode in responses
frontend/index.html       * tennis-only dropdown, free-prob + unknown + feed badges,
                              sim card shows per-surface/confidence/paper-trading
tests/test_reliability.py * 8 tests: names, io/rollback, per-surface eta, group
                              rejection, quota shape, cal-v2 roundtrip, snapshot roundtrip
```

## 4. Key numbers (2026-09-07 re-run, DO NOT REGRESS BLINDLY)
- GBM test (>=2024, n=6114): acc 64.8%, Brier **0.2159**, ECE 0.0143 (was 64.4%/0.2176).
  Fold A (train<=21/valid22) test Brier 0.2170 — stable, adopted + re-frozen
  (gbm.txt sha 9fc7bca18d88; manifest + sim_frozen/ refreshed).
  SUPERSEDED by W2: production is now B columns (shrunk serve states +
  missingness + age), test Brier **0.2155** both folds, sha fd1cd137b8fc.
  Live inference symmetrizes GBM orientation (exact swap symmetry); sim arm
  with symmetrized B: acc 65.31% / Brier **0.2141**.
- Sim 2024-25 (5,978, saved ledger): adaptive acc 0.6499/brier 0.2157 |
  frozen 0.6484/0.2177 | gbm 0.6531/**0.2141** | elo 0.6375/0.2196.
  Block CIs: adaptive-vs-frozen -0.00199 (a better); gbm-vs-adaptive -0.00161
  (a better — GBM now significantly beats the ensemble, not just ties it);
  gbm-vs-frozen -0.00360.
  Monthly calibration refit is OFF by default (hurts -0.003; --cal for
  experiments only). Live calibration.json QUARANTINED (unvalidated).
- Ablation 2024-25 (fresh Hedge, no cal): only no_gbm hurts (+0.0018 Brier).
  All other drops are noise (+/-0.0002); no_elo_surface even helps (-0.0001):
  GBM already contains elo_surf_diff (top gain feature) -> double counting
  confirmed. Next: redundancy-aware ensemble (stacking) or drop dead signals.
- Confidence (adaptive): >=0.60 -> 72.4% acc/0.192 brier (n=3311); >=0.65 ->
  76.7%/0.174 (n=2296). The model knows when it knows: policy should lean here.
- Paper trading (half-Kelly vs ELO fair line, NO VIG — skill-vs-line only):
  1522 posts, win 64.3%, +11.04u, ROI-on-staked 17.6%, maxDD 0.5u.
  by_edge: big(>=8%) expected 5.1u / realized 9.3u (conservative, healthy);
  thin(<3%) +0.1/+0.2u (gate thin edges OUT until market closes land);
  passes (4575) would-be +7.9u (policy leaves money vs elo line; with vig
  thin passes likely negative — needs market-close CSV to settle).
- Per-surface adaptive: hard 0.2160 (n=3605), clay 0.2182 (1754), grass 0.2123 (619).

## 5. Gotchas / scar tissue (read before touching)
1. Round sorting: NEVER alphabetical (`F` < `R128` leaked finals -> fake 81%).
   `ROUND_ORDER` + test.
2. Markov B-serves: A's win prob is `1-g`. Symmetry + Monte Carlo tests.
3. Hedge abstention: sleeping signals keep exact share; floor 0.02.
4. Bandit pass arm: pass reward is deterministic 0; prior trusts +EV until n>=2.
5. GBM time splits + freeze hash-check (`check_frozen` aborts on drift).
   Retrain protocol: train -> freeze -> sim -> compare. NEVER retrain without re-freeze.
6. Data licensing: TML mirror NON-COMMERCIAL; swap to licensed feed before charging.
7. Network: gambling-filter allowlist; provider falls back cache->mock with status.
8. Windows console cp1252: no emoji in terminal output. `.env.example` must NEVER
   contain a real key (was sanitized 2026-09-07: ROTATE the key that was in it).
9. serve.py: keep hot-reload light path cheap (weights/cal/GBM only, no replay).
10. NEW: calibration.json may be legacy list OR {"_v":2,"tables":{...}} — load_fn
    handles both; serve prefers per-surface table with global fallback.
11. NEW: sim frozen arm advances RATINGS (only weights/cal frozen) — deliberate.
12. NEW: edge/Kelly ALWAYS use model_prob_free_a (market-free). Full prob is for
    accuracy display only.

## 6. Live learned state (in `data/`, do not delete)
`weights.json` (+eta_by), `calibration.json`, `gbm.txt` + `gbm_features.json`,
`tracker.db`, `policy.json` (bandit), `upsets.json` (cap 200), `verdicts.jsonl`
(every served verdict), `sim_2024_2025.json` (ledger + curves), `sim_frozen/`,
`state_snapshot.pkl` (rebuild after CSV updates: `python -m backend.ratings.snapshot`),
`data/backups/` (auto-kept previous versions for /api/rollback).

## 7. Astra W1 audit (2026-09-07, in progress)
- Full report: `C:/Users/oliad/astra_full.md`. Model used: gpt-6-astra.
- Expert activity (`python -m backend.model.activity`, 2024-25): market + news
 INERT (never fire in sim — their weights/ablations are meaningless);
 GBM standalone Brier 0.2156; elo_overall/markov corr w/ GBM 0.91/0.84 (echoes);
 rest corr w/ outcome 0.01 (noise, as expected).
- GBM-only sim arm: acc 0.6475 / Brier **0.2156** — beats adaptive ensemble
 (0.2163) and frozen (0.2179). Simplification validated: GBM is the incumbent.
- Paired stats fixed: z stays but is LABELED as Brier-sign test; new
  `_block_ci` gives 95% CIs on mean Brier (tournament-month blocks, seed 7):
  adaptive-vs-frozen mean -0.00158 CI [-0.00229,-0.00086] (a better);
  gbm-vs-adaptive mean -0.00067 CI [-0.00174,+0.00044] (no significant
  difference -> simplicity rule: GBM is the default serve path, `GBM_DEFAULT`,
  ensemble retained for reasons/fallback); gbm-vs-frozen -0.00226 (a better).
- Integrity tests: `tests/test_integrity.py` (row antisymmetry, neutral labels,
 swap complementarity, unknown flagged end-to-end). Suite now 40 green.
- Cold-start containment live: <10 tour matches for either player forces
 policy pass (shadow logging continues); board shows COLD-START tag.
- Odds archive: every live fetch stored to `data/odds_archive/` + manual CLI.
- Sanitizer accountability: dropped quotes logged to `data/dropped_quotes.jsonl`.
- Entity fix (live bug found via board): canonical() title-casing split Dutch
  names ("Van De" vs history "van de"), fragmenting ratings. Fixed: particles
  stay lowercase + ALIASES for observed variants + loader canonicalizes
  winner/loser at ingest. Cascade re-run complete: features -> GBM (test
  0.2159 unchanged) -> freeze -> snapshot v3 -> sim (betting +10.61u, same).
  Verified: Zandschulp resolves to 225 matches, Gea correctly unknown (1).
  Live board now shows Gea VAL+COLD->pass (containment working).
- Rights manifest: `data/RIGHTS.md` (§1 licensed-feed replacement still the
  human-owned blocker, deferred per owner until all stages complete).

## 9. W2 tournament — PREREGISTERED 2026-09-07 (do not edit after results)
Candidates (column sets over one shared feature store; identical GBM
hyperparams: leaves 63, min_data 150, lr default, L2 5, monotone constraints,
Brier early-stop patience 100, seed 7):
- A (incumbent): current 27 columns.
- B (shrunk states): A + s1_shrunk_diff, s2_shrunk_diff, ace_shrunk_diff,
  df_shrunk_diff, serve_missing, age_diff.
- C (opponent-adjusted): B + serve_state_diff, return_state_diff, state_n_min
  (shrunk opponent-adjusted serve/return means from PointRatings).
- D (timescales): C + elo_fast_diff (Kx2 Elo), markov_p_fast + serve_edge_fast
  (HL-45 point ratings). Preregistered timescales, not a tuned grid (noted
  deviation from Astra's decay-grid proposal).
- E: TBD after A-D (winner columns + cold-start prior treatment).
Outer folds, fixed rule: train [2020..T-2], valid year T-1, test year T. Folds:
  2022 (tr 2020-21, va 2021 — thin, kept for completeness),
  2023 (tr 2020-22, va 2022), 2024 (tr 2020-23, va 2023), 2025 (tr 2020-24, va 2024).
  2024-25 are development-examined (Astra L2 caveat recorded); final word stays
  prospective. 2026-YTD held as a bonus 5th fold if n>=100.
Promotion rule (fixed in advance): mean test-Brier improvement vs A >= 0.0005
  AND positive in >=3 of 4 folds AND blocked-95% CI (same _block_ci blocks)
  excludes zero on pooled 2022-25. Else kill. E defined only from a promoted
  winner; if none, W2 verdict = null result, ship A.
RESULTS 2026-09-07 (data/tourney.json, pooled month-block CIs):
  B: folds +0.0002/+0.0006/+0.0002/+0.0023, mean +0.00082, 4/4 positive,
     CI [+0.00020,+0.00135] -> PROMOTES.
  C: +0.0004/+0.0002/-0.0001/+0.0019, mean +0.00060, 3/4,
     CI [+0.00003,+0.00109] -> promotes vs A, but delta-vs-B is noise.
  D: +0.0007/+0.0019/-0.0003/+0.0013, mean +0.00090, 3/4,
     CI [+0.00024,+0.00147] -> promotes vs A, but delta-vs-B (+0.00008) is
     far below the bar and CIs overlap fully.
  VERDICT: ship B (shrunk serve states + missingness + age — consistent in
  every fold, simplest). C/D extras KILLED (unproven over B). E = B columns +
  valid-tuned low-history output shrinkage (cold-start prior); needs its own
  fold validation before it touches production.
  E RESULT 2026-09-07: kstar=0 in ALL folds; any shrinkage hurts pooled Brier
  (0.2144 -> 0.2183+) AND sparse logloss (0.59 -> 0.65+). E DEAD — honest null
  (sparse probs already self-moderate via shrunk features). Ship plain B:
  `train --set B` (SETS live in train.py; PROD_SET="B").

## 8. Next steps (in order)

## 10. W3 verdicts (2026-09-07, code-side complete; market-join waits on CSV)
- Stack (ridge on gbm/markov/elo OOF, train<=23 test 24-25): 0.21474 vs raw
  0.21410, gain -0.00064. Temperature: -0.00041. BOTH KILLED. Coefs showed
  stack amplifying GBM (1.265) and zeroing markov (-0.034) — nothing to learn.
- Calibration (fit 22-23, test 24-25, data/cal_validation.json): none 0.21410
  / ECE 0.0136 beats temperature, logistic AND isotonic (all gain<=0).
  GBM is naturally calibrated — no calibrator ships, monthly refit stays dead.
- Policy (identical sim matches): bandit +11.04u < post_all +21.12u <
  ev003 +20.57u. BANDIT DEMOTED to shadow per kill rule. Live action is now
  frozen ev>=0.03 + bettable + cold-start guards; bandit vote kept as
  `policy_bandit` for shadow comparison. (Caveat: ev003 threshold was
  eyeballed on this window — re-verify on prospective data.)
- Stress (2% payout haircut + 2% price slip): +8.95u, survives. Edge is not
  purely execution-fantasy, but line is still Elo-fair, not books.
- Sim ledger now carries `policies` + `stress` keys; sim defaults use_cal=False.
- Remaining W3 item: historical odds join -> market arm + p_S/p_M/p_F residual
  (BLOCKED on tennis-data.co.uk CSV download -> data/market_close_24_25.csv).
1. **Calibration, properly**: validate learn.py's long-window per-surface fit
   on a holdout (predict-then-update, Brier + ECE per surface). Only restore
   calibration.json live if it beats uncalibrated. Monthly refit stays OFF.
2. **Market-proxy arm**: download tennis-data.co.uk 2024-25 closing odds ->
   `data/market_close_24_25.csv` ((date,a,b,prob_a)); sim auto-enables 4th arm;
   re-run betting leg vs REAL closes (kills the no-vig caveat).
3. **Redundancy**: ablation says 11/12 signals are passengers. Try (a) drop to
   {gbm, elo_surface, market} + recalibrate, or (b) stacking (logistic on OOF
   signal probs) instead of Hedge; judge on sim Brier + betting, not accuracy.
4. **News sentiment** (`sig_news` stubbed): RSS -> lean -> logistic, 0.02 start,
   ablation-gated. Still stubbed; fine to defer until (2) lands.
5. **Nightly job**: settle -> learn -> snapshot rebuild. Policy needs 20+ settled.
6. **In-play RL**: paper-only until 500+ sim matches beat pre-match net of spread.
7. Commercial blockers: licensed feed, Odds API redistribution OK, gaming lawyer,
   affiliate disclosure, responsible-gambling pages. ROTATE the exposed API key.

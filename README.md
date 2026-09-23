# Vibe-Odds — a sport-agnostic prediction engine (ATP tennis + NBA moneyline)

<!-- claims: modelling=103 evidence=88 nba=99 total=290 -->

One engine, two sports: transparent, evidence-first prediction with frozen
ledgers, pre-registered claims, and honest failure reporting. `core/` holds the
sport-agnostic engine; `sports/tennis/` and `sports/nba/` hold each sport's
bindings.

18+ only. Check your state/country laws. Data for information, not financial
advice. Bet responsibly. If gambling stops being fun, US: 1-800-GAMBLER.

---

# NBA moneyline winner (the v1 model)

Transparent weighted logistic over seven published coefficients. Regular
season only (no playoffs, no play-in, no totals), winners including OT.
Trained 2005-06→2020-21 (19,100 games), structure locked on tune season
2021-22, tested walk-forward 2022-23→2025-26. Pre-registered protocol:
`docs/preregistration.md` (the claim test ran exactly once).

## Claim ladder (pre-registered; A5 = the formula, A6 = +availability, exploratory)

| Tier | Claim | A5 | A6 | Market | Verdict | What failed |
|---|---|---|---|---|---|---|
| T1 | Beats climatology & Elo-only baselines | 0.21303 vs 0.24766 / 0.21518 Brier | — | — | **HOLDS** | — |
| T2 | Beats the **opening** line | −0.00681 [−0.00925, −0.00450] | −0.00409 [−0.00659, −0.00173] | open 0.20584 | **FAILS** | openers already price most of our information |
| T3 | Beats the **closing** line | −0.01077 [−0.01324, −0.00823] | −0.00800 [−0.01015, −0.0058] | close 0.20190 | **FAILS** | closing line aggregates injury/lineup news we don't model |

Market-structure control: open→close −0.00395 [−0.00543, −0.00236] (the
market improves toward tip — verified). Formula 0.21265 sits between open and
close on odds-covered games. Calibration is *not* the gap: formula ECE 0.0131
vs open 0.0110 / close 0.0123; accuracy 65.8% vs 68.5%. **Headline: the gap is
information, not calibration.** A6 (availability signals, exploratory) improves
on A5 by +0.00279 [0.0016, 0.00401] (n=4,897) and closes 38.6% of the pooled
Brier gap to the opening line (0.00723 → 0.00444), but is still short of both
the open (−0.00409 [−0.00659, −0.00173], n=4,875) and the close
(−0.00800 [−0.01015, −0.0058], n=4,877). A6 is exploratory — it was never a
registered tier.

σ_d (paired-test noise scale) locked on 2021-22: 0.08497 (0.08522 under
current code — both recorded in `docs/preregistration.md` §5/§9).

## The formula (published)

`p(home) = σ(−0.6844 + Σ coef_i · x_i)` over seven features (home/away
perspective). Coefficients fit on ≤2020-21 by logistic MLE (scipy, L2 1e-3):

| feature | coef | +1 unit |
|---|---|---|
| elo_diff (home Elo − away Elo) | +0.004333 | ×1.004 |
| hca (time-varying home edge, Elo pts) | +0.015176 | ×1.015 |
| form_margin_diff (last-10 pts) | +0.009746 | ×1.010 |
| net_rtg_diff (as-of net rating) | +0.007698 | ×1.008 |
| rest_diff (home rest − away rest, days) | +0.047173 | ×1.048 |
| b2b_away (away on back-to-back) | +0.175843 | ×1.192 |
| is_neutral (neutral floor) | 0.000000 | ×1.000 |

Structure frozen after 2021-22; weights refit annually on an expanding window
(mechanism only — no refit has run yet).

### Worked example — 2023-12-13, Philadelphia 76ers @ Detroit Pistons (`0022300310`)

All features as-of before tip (2023-12-14T00:00:00Z); training moments over
the ≤2020-21 window (n=19,100) for the 3σ bands:

| term | value | coef | contribution | p if +1 | p if −1 | fit-window 3σ band |
|---|---|---|---|---|---|---|
| intercept | — | — | −0.6844 | — | — | — |
| elo_diff | −408.64 | +0.004333 | **−1.7706** | 0.120 | 0.118 | [−505, +504] |
| hca | +59.50 | +0.015176 | **+0.9030** | 0.121 | 0.117 | [61.2, 75.8] |
| form_margin_diff | −26.90 | +0.009746 | −0.2622 | 0.120 | 0.118 | [−26.4, +26.2] |
| net_rtg_diff | −24.38 | +0.007698 | −0.1877 | 0.120 | 0.118 | [−28.5, +28.2] |
| rest_diff | 0.00 | +0.047173 | 0.0000 | 0.124 | 0.114 | [−2.8, +3.3] |
| b2b_away | 0.00 | +0.175843 | 0.0000 | 0.139 | 0.102 | — (0/1) |
| is_neutral | 0.00 | 0.000000 | 0.0000 | 0.119 | 0.119 | — (0/1) |

**logit = −0.6844 − 1.7706 + 0.9030 − 0.2622 − 0.1877 = −2.0020**
**p(home win) = σ(−2.0020) = 0.119 → pick: Philadelphia.**
Result: Philadelphia 129–111 (label 0). Single-game Brier **0.0142** — a
confident, correct call. Verdicts like this are exactly what the live pre-tip
log records before tip-off (`logged_at < tipoff_ts`, enforced).

(Deviation note: the plan named "2023-12-05 PHI@DET" as the worked example;
the real PHI@DET closest to that date in the DB is 2023-12-13 — used here.
Also note 2023-24's hca sits just below the fit window's 3σ band: home
advantage shrank after 2020 and the time-varying hca tracks it.)

## What failed

**Experiments (the honest ones):**
1. **T2 — beat the open: FAILED** (−0.00681, 95% CI strictly below 0).
2. **T3 — beat the close: FAILED** (−0.01077). The closing line is the
   mandatory benchmark and it wins.
3. **Calibration layer: REJECTED.** Isotonic/Platt tested on the tune-only
   rule; the test block judged raw probabilities better for A5
   (−0.00149 [−0.00236, −0.00065] favouring raw). **Raw probabilities ship.**
   The tune rule alone had picked isotonic (in-sample ECE 0.0) — a trap we
   logged in `docs/preregistration.md` §9.
4. **A6 (availability arm) does not beat the open either** (−0.00409
   [−0.00659, −0.00173], n=4,875) — and
   it was born after a market-arm contamination bug (an early benchmark
   mixed post-tip market data into pre-tip arms). Fixed (`LIVE_LIKE`/
   `MODEL_FEEDS` filters, §4 deviation), verdicts re-derived clean.
5. **Claim tiers are blunt instruments by design**: two of three tiers failed
   and that is the finding — a transparent formula trained on public history
   captures most of what *pre-open* prices contain and none of what *close*
   prices learn from injuries and lineups.

**Removed from scope (would have been features):**
6. **Auto-ingest drift** — no self-updating data pipe; ingests are run
   explicitly and reviewed.
7. **Auto-retraining** — weights refit annually, by hand, on an expanding
   window. Nothing retrains itself.
8. **Sentiment arm** — cut from v1 (pre-registered as a capped expected-null
   arm; it starts only after the core is frozen — phase 2).
9. **T-60min odds (after lineups + official injury report)** — phase 2,
   explicitly after testing; it is the most promising single upgrade to the
   information gap.
10. **The "vibe-odds" name lock** — the repo was revamed in place rather than
    split into per-sport repos.

## Data cards (six major datasets — full cards in `docs/data-cards.md`)

| dataset | rows | span | gaps & honesty |
|---|---|---|---|
| games | 25,268 | 2005-06→2025-26 | 2 seasons have no odds anywhere (burn-in) |
| odds_snapshots | 421,101 | 2007-08→2025-26 | thin books 2024-25/25-26; opener proxy 2017-18→2022-23 |
| team_game_stats (box) | 50,536 | 2005-06→2025-26 | v3 endpoints only (v2 died) |
| game_inactives | 25,181 | 2005-06→2025-26 | 87 games without inactive rows |
| game_officials | 67,332 | 2005-06→2025-26 | — |
| features (as-of) | 25,258 | 2005-06→2025-26 | rows built strictly before their own tip |

Sources: nba_api (stats.nba.com) + The Odds API + historical odds readers.
**Excluded by ToS: Basketball-Reference** (data-use page forbids building a
competing database and supporting ML prediction) **and OddsPortal**. Full
disclosure list below.

## Honest disclosures

- 2005-06 & 2006-07 have no odds → market benchmarking from 2007-08 (ESPN
  core coverage 2013-14+); open-line tiers from 2013-14.
- Thin book coverage 2024-25/2025-26; teamrankings.com opener *proxy* for
  2017-18→2022-23 (paid data would fix this — nothing gets bought without the
  owner's explicit yes).
- 2025-26's final games were already known at commit time — treat 2025-26
  rows with care (only 2022-23→2024-25 is strictly untouched by hindsight).
- The from-empty rebuild + `v1.0` freeze is the final gate (step 9): until it
  lands, this README documents a machine that has been rebuilt from its own
  scripts but not yet from a fresh clone.

## NBA quick start

```bash
cd vibe-odds
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
copy .env.example .env            # optional: ODDS_API_KEY for live odds
python -m pytest -q               # 251 tests, must be green
python -m sports.nba.server       # evidence site + read-only API on :8010
python -m sports.nba.live_log --game-id 0022300310 --tipoff 2023-12-14T00:00:00Z --prob 0.119
# post-tip predictions are REJECTED (LatePrediction): pre-registration is enforced in code
```

NBA API (read-only except the keyed settle): `GET /api/site_meta /api/model
/api/experiments /api/sim /api/stats /api/teams /api/reliability
/api/predict?game_id= /api/board /api/live/log`; `POST /api/live/settle`
(needs `X-API-Key` once `NBA_API_KEY` is set). Every number is derived from
the frozen ledgers (`tests/evidence/nba_frozen_manifest.json`, 10 artifacts)
or the as-ingested DB — nothing on the site is hand-typed.

Evidence regeneration + `tools/check_claims.py` re-derive every claim above;
`tests/evidence/test_nba_frozen_evidence.py` fails on any byte drift.

---

# Tennis — ATP analytics: model vs the market (published 2026-09-07)

Best-price comparison + ML verdicts + full model evidence for ATP men's
tennis (US Open best-of-5 + ATP Tour best-of-3).

## Verified numbers (2026-09-07 frozen ledgers, n=5978 matches 2024–25)

| Arm | Accuracy | Brier |
|---|---|---|
| GBM (served default) | 65.31% | **0.2141** |
| Adaptive ensemble | 64.99% | 0.2157 |
| Frozen ensemble | 64.84% | 0.2177 |
| Elo only | 63.75% | 0.2196 |

- Paired block 95% CIs (tournament-month blocks, seed 7): adaptive−frozen
  −0.00199 [−0.00274,−0.00126]; gbm−adaptive −0.00161 [−0.00273,−0.00048];
  gbm−frozen −0.00360 [−0.00524,−0.00192].
- Calibration validation (fit 2022–23, test 2024–25): raw GBM 0.2141 /
  ECE 0.0136 beats temperature, logistic and isotonic (all gain ≤ 0) — ships uncalibrated.
- Ablation (walk-forward 2024–25): only dropping GBM hurts (+0.0024 Brier);
  all other signals are noise (±0.0002).
- Confidence: ≥0.60 → 72.9% acc / 0.191 Brier (n=3364); ≥0.65 → 76.9% /
  0.174 (n=2318). The model knows when it knows.
- Paper trading, half-Kelly vs Elo fair line **no vig** (skill-vs-line, not
  profit): 1522 posts, win 64.3%, +11.04u, ROI-on-staked 17.6%, maxDD 0.48u.
  Policy bake-off on identical matches: bandit +11.04u < post_all +21.12u <
  frozen ev≥0.03 +20.57u → bandit demoted to shadow.
- Column tournament (4 time folds): ship set **B** (shrunk serve states +
  missingness + age), +0.00082 mean Brier, 4/4 folds positive. C/D extras
  killed, E dead (k*=0).
- Tests: **49 modelling tests + evidence guards** (`python -m pytest -q`), modelling code untouched.

## Quick start
```bash
cd vibe-odds
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
# pinned; use THIS venv - the suite and the ingest must not resolve to a shared interpreter.
# (bump one pin at a time, then run the suite)
copy .env.example .env            # add ODDS_API_KEY from https://the-odds-api.com/
python -m pytest -q               # must be green before anything else
python -m backend.ratings.snapshot  # one-off: kills the 3s cold-start replay
python -m uvicorn backend.api.server:app --port 8000
# open http://localhost:8000
```
No key? App runs in mock mode (2 mock USO matches, UI says `feed: mock`).
Live keys: `tennis_atp_us_open` (slams, BO5) + `tennis_atp_singles`. No WTA/doubles.

## Website tour (http://localhost:8000)

- **Matches** — every upcoming match with model vs market probs, edge vs the
  market-free prob, Kelly½ staking, VALUE / MODEL≠MARKET / COLD-START /
  NOT-BETTABLE tags, plus a calibration badge per pick (HOT ≥65% conf =
  76.9% historical hit rate, n=2318; SOLID ≥60% = 72.9%, n=3364 — 2024–25 sim).
- **Players** — live blended surface-Elo rankings with search; click through
  for per-surface ratings, surface win-rate splits, rolling form trend,
  last-15 matches, and head-to-head lookup (overall + per surface).
- **Model** — frozen evidence: Brier/ECE cards, GBM reliability diagram
  (out-of-fold deciles), calibration-method bake-off, signal ablation table,
  column-set tournament, confidence slices.
- **Experiments** — 2024–25 frozen-vs-adaptive replay: arm scorecard,
  paired block-95% CIs, per-surface table, policy bake-off (bandit is shadow),
  paper-trading leg with the skill-vs-line caveat stated.
- **Methodology** — pipeline, gates, fair-price rule, limitations, business
  notes, responsible-gambling note, plus the operational Lab
  (tracker/weights/upsets/learner).

## API (all GETs below are read-only)

`GET /api/board /api/predict /api/compare /api/events /api/props`
`GET /api/stats` (tracker) · `GET /api/weights` · `GET /api/upsets`
`GET /api/sim` (frozen 2024–25 ledger)
`GET /api/players?q=&n=&surface=` · `GET /api/players/{name}?vs=`
`GET /api/h2h?a=&b=` · `GET /api/model` (all frozen evidence, one call)
`GET /api/reliability?n_bins=` (binned OOF probs for the diagram)

## Self-improving loop
1. UI `Log model pick` -> sqlite `data/tracker.db` (with model prob + surface)
2. After the match, `POST /api/settle {id, result, closing_fair_odds}`
3. `GET /api/stats` -> win_rate, ROI, avg CLV, calibration bins, suggested min-EV threshold
4. Nightly: `POST /api/learn` -> per-surface Hedge weights + per-surface
   calibration refresh. Poisoned state? `POST /api/rollback {target}` restores
   the newest backup (`weights` | `calibration` | `policy`).

Write endpoints (`/track`, `/settle`, `/learn`, `/rollback`) need
`X-API-Key` once `VIBE_API_KEY` is set in `.env`.

## From-empty rebuild (the v1 gate)

The machine is reproducible from an empty checkout — this is the gate the
`v1.0` tag stands on:

```bash
git clone <repo> && cd vibe-odds
mkdir -p sports/nba/data
cp ../nba.sqlite ../nba_walkforward_v1_contaminated.json sports/nba/data/
cp ../.env .
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -c "import sqlite3; c=sqlite3.connect('sports/nba/data/nba.sqlite'); c.execute('DELETE FROM features'); c.commit()"
.venv/Scripts/python.exe -m sports.nba.features.build --version v1
.venv/Scripts/python.exe -m sports.nba.features.build --version v2
.venv/Scripts/python.exe -m sports.nba.features.build --check-leakage --no-write
.venv/Scripts/python.exe -m sports.nba.model.formula --fit
.venv/Scripts/python.exe -m sports.nba.model.formula --sigma
.venv/Scripts/python.exe -m sports.nba.model.formula --evaluate
.venv/Scripts/python.exe -m sports.nba.model.formula --availability
.venv/Scripts/python.exe -m sports.nba.model.formula --calibrate
.venv/Scripts/python.exe tools/check_claims.py
.venv/Scripts/python.exe -m pytest
```

It regenerates the features from the ingested DB first (the `DELETE FROM
features` step — nothing is copied forward) and every frozen artifact
comes back **byte-identical** to `tests/evidence/nba_frozen_manifest.json`.
Inputs given to the gate: the ingested `nba.sqlite` (network fetches are
not re-run), `.env`, and `nba_walkforward_v1_contaminated.json` — the one
preserved-bug ledger, which is history: regenerating it would mean
reintroducing the bug it documents.

The gate found and we fixed two real reproducibility holes in `443e9c9`:
Windows `autocrlf` corrupted `data/` evidence on fresh checkout (the
committed `data/gbm.txt` then fatals LightGBM's model parser), and
`nba_market_structure_v1.json` had no producer in the pipeline. Both now
reproduce byte-exactly.

## How the model works (tennis only)
- **Ratings**: surface-blended Elo + H2H + last-10 form/serve/fatigue + decayed
  opponent-adjusted point ratings (Barnett-Clarke style).
- **Markov**: exact point->game->tiebreak->set->match DP (BO3/BO5, TB10 deciders).
- **GBM**: LightGBM on as-of features, time splits only (train<=2022/valid 2023/
  test>=2024), monotone constraints, Brier early-stopping. Gate: test Brier
  < 0.215 and ECE < 0.03 or it ships as REVIEW (ensemble member, not replacement).
- **Ensemble**: per-surface Hedge (eta hard 0.10 / clay 0.07 / grass+carpet 0.05).
  Sleeping signals keep exact share. Weights + calibration hot-reload into the
  running server; every verdict is logged to `data/verdicts.jsonl`.
- **Edge**: computed vs the MARKET-FREE model prob (the full prob saw the
  market at ~0.19 weight — edge-vs-full would dilute every signal).
- **Policy**: contextual bandit (surface, edge, confidence) gates post/pass,
  learns from settled picks. The sim exercises it with pass counterfactuals.
- **Eval**: `python -m backend.sim.season` replays 2024-25 frozen-vs-adaptive
  with a paired test, per-surface + confidence tables, and a paper-trading leg
  (half-Kelly vs market close where present, else the Elo fair line, no vig —
  skill-vs-line, not real profit). `python -m backend.model.ablate` shows which
  signals earn their weight.

## Odds feed notes
- 60s locked file cache; quota guard stops live fetches under 25 remaining
  credits (serves cache, then mock — `/api/health` always says which).
- Fair price = Pinnacle when it agrees with the field, else consensus median
  (flags >12pt disagreement as possibly stale).

## Business notes
- You are selling *information*, not taking bets: no sportsbook license, but you need: data-feed commercial rights, affiliate disclosure if you link books, state-by-state promo rules, responsible-gambling pages.
- Talk to a gaming lawyer before charging. Get written OK from The Odds API for commercial redistribution.
- History CSVs are a non-commercial mirror: fine for local testing, must swap to a licensed feed before charging.

# Vibe-Odds — ATP tennis analytics: model vs the market

Best-price comparison + ML verdicts + full model evidence for ATP men's
tennis (US Open best-of-5 + ATP Tour best-of-3).

18+ only. Check your state/country laws. Data for information, not financial advice. Bet responsibly.
If gambling stops being fun, US: 1-800-GAMBLER.

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
- Tests: **49 passed** (`python -m pytest -q`), modelling code untouched.

## Quick start
```bash
cd vibe-odds
pip install -r requirements.txt   # pinned; bump one pin at a time, then pytest
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
`X-API-Key: $VIBE_API_KEY` once `VIBE_API_KEY` is set in `.env`.

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

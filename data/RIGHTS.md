# Data & rights manifest (Astra W1) — deployment gate, not documentation.

Every input below lists source, retrieval state, license/terms as understood,
and what must change before commercial launch. When in doubt, assume the
stricter reading. This file is reviewed, not auto-generated.

## 1. Match history CSVs — `data/raw/tennis_atp/*.csv`
- What: ATP match results + serve statistics 2018-2026, Sackmann schema.
- Source: public non-commercial mirror of Jeff Sackmann's tennis_atp
  (https://github.com/JeffSackmann/tennis_atp), license CC BY-NC-SA 4.0.
- Status: RESEARCH ONLY. Must be replaced by a licensed feed (or explicit
  clearance) before any commercial use, including serving predictions to
  paying users. Derived artifacts affected: features.parquet, gbm.txt,
  weights.json, ratings snapshots, sim ledger.
- Action owner: human. No code change can fix this.

## 2. Live odds — The Odds API (`backend/providers/the_odds_api.py`)
- What: pre-match bookmaker quotes; free tier 500 req/mo.
- Terms: https://the-odds-api.com/ — free tier is evaluation-scale by design.
  Commercial redistribution / display of odds requires written OK (see README).
- Status: key present, ~490 credits left as of 2026-09-06; quota guard stops
  live fetch under 25 remaining. Raw responses now archived to
  `data/odds_archive/` with timestamps (prospective evidence).
- Historical odds endpoint is a paid product — not yet purchased.

## 3. Historical odds (planned) — tennis-data.co.uk
- What: downloadable annual results+odds files for research joins.
- Status: NOT downloaded. Free download ≠ commercial license; field timing
  semantics (open vs close) must be confirmed per file before any ROI claim.
  Keep research-only rows with uncertain timestamps out of executable backtests.

## 4. Derived/model artifacts — `data/*.json`, `gbm.txt`, `*.parquet`, `*.pkl`
- Status: inherit the most restrictive license of their inputs (currently
  non-commercial via §1). Rebuild from licensed inputs before launch.

## 5. Frontend/blog content, tweets
- Odds displayed with book attribution; affiliate links (if ever added) need
  disclosure; responsible-gambling pages required pre-launch. Gaming lawyer
  review still open (see README business notes).

## Review log
- 2026-09-07: manifest created. Blockers standing: §1 replacement, §2
  commercial OK, gaming-lawyer review.

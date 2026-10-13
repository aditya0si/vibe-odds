# Court Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an evidence-first static NBA frontend, then a separately contracted ATP tennis frontend, without changing frozen evidence or inventing predictions.

**Architecture:** Keep the existing backend-served `/` and `/static` roots. Each sport receives small local ES modules for transport, request lifecycle, validation, and rendering; Python adapters supply sport-specific route configuration. The two milestones are independently reviewable and neither changes model serving or data generation.

**Tech Stack:** Existing FastAPI/Python, browser-native HTML/CSS/ES modules; pytest with direct ASGI invocation; Node built-in test runner for pure JavaScript tests; approved existing browser tooling for rendered verification.

**Spec:** `docs/superpowers/specs/2026-10-13-court-evidence-design.md`

## Global Constraints

- Rename visible page titles, brand, and adapter presentation titles only. Preserve vibe-odds repository identity, artifact paths, hashes, model IDs, API paths, coefficients, preregistration, and operational scripts.
- Browser traffic is GET only; remove tracking, settling, learning, rollback, and promotional sharing controls.
- No framework migration, cross-origin aggregator, account system, paid feed, training, scheduled job, or backend prediction generation is included.
- No rebaseline is permitted for a UI change.
- Historical A5 evaluation does not establish prospective A7 prediction performance.
- No market advantage/profit claim; retrospective paper simulation is not actual profit.
- Plan-only handoff: no services, installs, commits, branches, or implementation are authorized now. The commit checkpoints below are review checkpoints, not instructions to mutate git under this request.
- Later implementation, after written review/approval, may be delegated solely to `opencode-go/deepseek-v4.1-flash`; Hermes independently reviews outputs.

## Review Focus

1. A delayed group/search request resolves after a newer request: only newest generation can render or change fetchedAt (Tasks 1 and 5).
2. HTTP 200 carries error or the wrong event/game ID: no plausible prediction detail appears (Tasks 1, 2, 4).
3. A provider fallback reports mock despite an installed API key, or feed provenance is unknown: live cards stay suppressed (Task 4).
4. Scheduled NULL-score database rows and past unsettled log rows: do not show completed win rates or inferred losses (Task 2).
5. Missing values, hostile strings, invalid dates, and empty reliability bins: no zero fabrication, injected markup, or inaccessible chart-only evidence (Tasks 2, 5, 6).

## File map and ownership

Existing modifications:
- `sports/nba/site/index.html`: semantic NBA shell and local module entry (replace inline boot/render logic).
- `sports/nba/adapter.py`: visible title `Court Evidence — NBA`, preserve tabs/sport/routes.
- `frontend/index.html`: semantic ATP shell, remove operational controls/inline app logic; retain/migrate guarded historical disclosures.
- `backend/api/adapter.py`: visible title `Court Evidence — ATP tennis`; explicitly add predict/reliability/player-detail route keys consumed by UI.
- `tests/sports/nba/test_site_api.py`: adapter/static wiring and error/empty contracts; replace inline-string endpoint expectations with asset checks.
- `tests/core/test_site.py`: title/config serving assertions, preserving injection tests.
- `tests/evidence/test_frozen_evidence.py`: only if necessary, move presentation-only literal assertions to equivalent runtime/API-derived assertions; retain all hash and ledger/README guards. Prefer keeping nonpromotional historical disclosure to avoid weakening this file.

New NBA modules under `sports/nba/site/`: `court-evidence.css`, `transport.js`, `request-state.js`, `nba-contract.js`, `nba-app.js`.
New tennis modules under `frontend/`: `court-evidence.css`, `transport.js`, `request-state.js`, `tennis-contract.js`, `tennis-app.js`.
New tests: `tests/frontend/transport.test.mjs`, `tests/frontend/nba-contract.test.mjs`, `tests/frontend/tennis-contract.test.mjs`, `tests/frontend/request-state.test.mjs`, `tests/frontend/court-evidence.browser.test.mjs`, `tests/test_tennis_site_api.py`.

No change needed to `core/api.py`, `core/site.py`, `sports/nba/evidence.py`, `sports/nba/live_log.py`, `backend/api/server.py`, models, operational data, or manifests. ES modules remain .js; since no package.json exists, pure Node tests import module source via data URLs (readFile + base64) for dependency-free modules. Tests load each sport copy; app modules are exercised by the browser, avoiding changes to repository package mode. If a runner requires installation, stop for approval rather than inventing a successful browser result.

## Execution prerequisites and immutable baseline

Planning inspected HEAD `328b977c33860d65705a0db4262224a0d5c4a096`; both manifests matched 41/25 entries. `.venv/Scripts/python.exe` exists. Actual tests were not run in the documentation task. No NBA live log exists here.

Before future execution, read both documents; check `git status --short` and `git rev-parse HEAD`. Use existing `.venv/Scripts/python.exe -m pytest` from `C:/Users/oliad/Desktop/vibe-odds`, not system python/pip. Discover `node --version` and existing browser automation capability; no installs by default. Save a full SHA256 baseline of both manifests and every listed artifact to the Hermes scratch directory (not repository data), including NBA sizes. Compare full hashes after every task and final verification; expected inventories remain 41 and 25. Manifest tests alone must not excuse a changed manifest.

## Milestone 1 — complete NBA UI

### Task 1: Read client, lifecycle, and board validation

**Files:** Create NBA `transport.js`, `request-state.js`, `nba-contract.js`; create transport/request-state/nba-contract Node tests.

**Interfaces:** `createReadClient({fetchImpl,now,timeoutMs:10000}).getJson(path,{signal}={})` returns the spec tagged result; `createRequestSlot()` returns begin/isCurrent/cancel; `parseBoard(payload,{fetchedAt,now})` and `parseHistoricalPrediction(payload,requestedId)` return the spec results. Pure functions do not touch the DOM or filesystem.

- [ ] Write failing named tests: `get_only_same_origin`, `http_error_is_not_empty`, `invalid_json`, `network_failure`, `timeout`, `abort_is_not_user_failure`, `newest_generation_wins`, `stale_error_cannot_clear_new_spinner`, `empty_board`, `count_mismatch_rejected`, `probability_range_and_finiteness`, `pick_threshold_consistency`, `timezone_required`, `equal_tipoff_rejected`, `partial_invalid_rows_quarantined`, `historical_error_200`, `historical_id_mismatch`.
- [ ] Run `node --test tests/frontend/transport.test.mjs tests/frontend/request-state.test.mjs tests/frontend/nba-contract.test.mjs`; expect failure for missing module/interfaces, not environment failure. For probability boundaries assert 0 and 1 valid, -0.01 and 1.01 invalid; 0.5 picks home. For latest-request test resolve B before A and assert B data/timestamp remain.
- [ ] Implement read transport with deadline/AbortController, validated paths, checked HTTP/JSON; adapters classify application error bodies. Require board n equals raw row count, not surviving valid count; maintain rejected count separately. Implement independent request generations by resource.
- [ ] Rerun the same command; require all tests pass and no network requests or repository writes from pure tests.
- [ ] Hermes contract review checkpoint; compare immutable baseline. Do not advance on a passing test that never loads actual module code.

### Task 2: Complete NBA information architecture and evidence views

**Files:** Modify NBA index/adapter; create NBA CSS/app; extend NBA contract tests and `tests/sports/nba/test_site_api.py`, `tests/core/test_site.py`.

**Interfaces:** `nba-app.js` consumes Task 1, adapter config, and spec NBA endpoint shapes; `mountNbaApp({root,adapter,client,now}) -> {dispose()}`. It owns resource state and renders text nodes. Export `parseModel`, `parseTeams`, `parseStats`, `parseReliability` from nba-contract.js with tagged ready/invalid results; preserve nested sample IDs/cohorts.

- [ ] Add failing tests `nba_shell_adapter_title_and_static_assets`, `nba_empty_log_contract`, `nba_historical_error_http200`, `nba_tiers_use_passes`, `nba_null_bins_preserved`, `nba_teams_rates_suppressed`, `nba_scheduled_counts_are_separate`. Update existing `test_index_headless_load`: assert configured endpoints from injected config and GET of `/static/nba-app.js` and CSS; do not require every endpoint literal still inside index HTML.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/sports/nba/test_site_api.py tests/core/test_site.py -q` and NBA Node tests; expect new title/assets tests fail while existing API data guards remain meaningful.
- [ ] Implement header/skip link/navigation, all five views, explicit resource states, refresh/retry, and responsive evidence layout. Model renders coefficients, windows, pooled cohorts, `passes` verdicts and paired confidence intervals; experiments use structured evidence cards with raw JSON only secondary. Show completed/scheduled counts from stats, omit team rate headlines and per-team rates. Historical lookup calls predict only on explicit user input and always retains Historical reconstruction provenance.
- [ ] Build prediction filters from valid logged rows using timezone-aware now: settled overrides; future tipoff is upcoming; passed tipoff un-settled is awaiting settlement. No source name or odds join is invented; fallback display is game ID, Home/Away.
- [ ] Rerun Python and Node commands; require all pass. Inspect copy for no Tonight default, no A7 projection, no hardcoded tier success, no zero fallback when absent.
- [ ] Hermes full NBA UI review checkpoint and immutable hash comparison.

### Task 3: NBA rendered milestone gate

**Files:** Create `tests/frontend/court-evidence.browser.test.mjs` with NBA cases using an available approved browser harness. Future screenshots/reports belong in scratch, not evidence directories.

**Interfaces:** Browser cases exercise real `/` and `/static` delivery plus controlled read API responses. `COURT_NBA_URL` is an explicit approved service base, not an assumed port listener. Test fixture routes are labeled test-only and never product demo data.

- [ ] Write failing browser cases `nba_all_tabs_render`, `nba_empty_without_fake_cards`, `nba_refresh_failure_keeps_last_success`, `nba_historical_is_not_prospective`, `nba_all_requests_get`, `nba_keyboard_navigation`, `nba_responsive_layout`, `nba_hostile_text_is_literal`. Assert actual DOM text and network method logs; settle data remains test fixture only.
- [ ] After explicit execution authorization, launch/discover only approved local server; no listener was discovered during planning. Configure URL, run `node --test tests/frontend/court-evidence.browser.test.mjs` with NBA cases enabled; record initial failures.
- [ ] Resolve UI defects in NBA files only. Verify no unresolved console errors, visible retry on offline/timeout, every tab renders independently when another endpoint fails, no animation requirement, and stable focus after refresh.
- [ ] Capture 390x844, 768x1024, 1280x900 screenshots of Matches empty/populated/error and all evidence tabs. Check 200% zoom, keyboard-only traversal, labeled scrollable tables, computed contrast, 44px targets, chart/table parity. Record outcomes and defects, not “looks good”.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/core/test_api.py tests/core/test_site.py tests/sports/nba/test_site_api.py tests/evidence/test_nba_frozen_evidence.py tests/evidence/test_frozen_evidence.py -q`; run NBA Node tests and hash comparison. NBA milestone is complete only when all five tabs and rendered states pass; do not begin tennis with a shell-only NBA implementation.

## Milestone 2 — tennis adapter and UX with real API tests

### Task 4: Tennis ASGI contract and feed containment

**Files:** Create `tests/test_tennis_site_api.py`, `frontend/tennis-contract.js`, tennis contract tests; modify tennis Python adapter; create local transport/request-state copies covered by existing Node tests.

**Interfaces:** `parseBoard(payload,{fetchedAt,health,now})`, `parsePrediction(payload,requestedId)`, `parsePlayers`, `parsePlayerDetail`, `parseModel`, `parseReliability` in tennis-contract.js. Added config keys: predict `/api/predict`, reliability `/api/reliability`, player_detail `/api/players/{name}`; substitute the encoded name locally, never fetch a literal template. Existing api keys/surfaces/groups remain unchanged.

- [ ] Write real ASGI tests `test_tennis_adapter_routes_and_assets`, `test_tennis_board_missing_key_forces_mock`, `test_tennis_board_error_card_shape`, `test_tennis_group_validation`, `test_tennis_predict_unknown_id_falls_back`, `test_tennis_reliability_equal_count_contract`, `test_tennis_players_surface_and_unknown_detail`. Drive `backend.api.server.app` via direct ASGI, copying the established helper's query-string handling; do not replace the app or routes with a fake server.
- [ ] Sandbox provider CACHE_PATH, QUOTA_PATH, DROPPED_LOG and serve.VERDICTS_LOG into pytest tmp_path; unset ODDS_API_KEY per missing-key case. Patch only provider fetch/model state seams for deterministic external inputs; invoke real endpoint handlers and `_build_card` at least once with controlled valid predictor output. No paid network or production cache writes. Explicitly distinguish controlled-input integration from an unstubbed live-feed smoke.
- [ ] Add Node cases `mock_with_key_is_blocked`, `missing_key_live_payload_is_unverified`, `unknown_feed_is_unverified`, `cache_age_not_fetch_age`, `age_60_boundary`, `negative_or_null_age_unknown`, `demo_never_live_counts`, `exactly_two_outcomes`, `pick_must_be_outcome`, `predict_event_id_mismatch`, `tennis_error_card_not_prediction`, `equal_count_bins_not_nba_bins`. Cache age 60 is not stale; >60 is stale; no per-event fresh verdict.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_tennis_site_api.py -q` and `node --test tests/frontend/tennis-contract.test.mjs tests/frontend/transport.test.mjs tests/frontend/request-state.test.mjs`; require initial expected failures.
- [ ] Implement injected tennis routes and dedicated parsers. Board mock gate is payload-led; health can downgrade trust but never override mock. Live/unknown group-wide state never becomes per-event provenance. Validate returned ID for detail; do not fix backend fallback by displaying the first match.
- [ ] Rerun exact commands; require deterministic passing tests and immutable hash baseline. Confirm tests exercise real handlers rather than only manually typed JSON fixtures.

### Task 5: Complete tennis UI and historical evidence integrity

**Files:** Modify tennis index, create tennis CSS/app; extend tennis Node and browser tests; adjust presentation-only frozen test assertions only if unavoidable as described in file map.

**Interfaces:** `mountTennisApp({root,adapter,client,now}) -> {dispose()}` uses Task 4 parsers; resource slots separate board/group, player list/detail, model/reliability/stats. Group changes abort prior board request; player search debounces 250ms and generation-checks responses. Existing reliability keys p_mean/frac_pos/ece_oof remain distinct from NBA.

- [ ] Add failing rendered tests `tennis_demo_opt_in_only`, `tennis_no_write_controls`, `tennis_group_race`, `tennis_player_search_race`, `tennis_surface_only_affects_ratings`, `tennis_probability_series_named`, `tennis_unknown_player`, `tennis_historical_policy_disclaimer`, `tennis_missing_model_artifact`, `tennis_hostile_reason_is_text`. Query A then B, resolve B first, and ensure A never overwrites results or source labels.
- [ ] Run tennis Node/ASGI tests and browser tennis cases; record RED outputs.
- [ ] Implement Matches and opt-in demo compartment, Players and canonical detail, Model, Experiments and Methodology using specified palette/layout. Label hardcoded hard match modelling, best-of group, cold-start/unknown players, source limitations and retrospective cohorts. Primary prediction displays are actual valid board cards only; no fake match placeholders or marketing edge/value/post labels.
- [ ] Remove all inline operational code, POST fetches, tracker/learn/rollback/settle buttons, tweet sharing and sizing controls. Do not merely hide controls while handlers remain. If historical paper-profit literals stay in index to preserve current tests, place them in a clearly labeled historical simulation disclosure with explicit not-real-profit text; no primary profit headline.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/test_tennis_site_api.py tests/test_analytics.py tests/core/test_site.py tests/evidence/test_frozen_evidence.py -q` plus all Node tests. No hash guard deletion or rebaseline; explain any presentation assertion migration in review.
- [ ] Repeat visual/keyboard/zoom/contrast checks at NBA viewport set for each tennis tab and live/mock/unknown/stale/error states. Gate milestone on real rendered evidence and sandboxed API integration, not static source substring checks.

### Task 6: Cross-sport regression and final verification

**Files:** Extend browser tests; no product data writes. Future verification report may be added under docs only after execution authorization.

- [ ] Pin cross-sport browser tests `all_network_get`, `invalid_adapter_blocks_requests`, `http200_error_not_empty`, `refresh_failure_preserves_timestamp`, `timezone_midnight_filter`, `null_numeric_not_zero`, `contrast_and_focus`, `all_views_table_chart_parity`, `static_modules_same_origin`. Test malicious API strings containing `<img onerror=...>` and ensure literal text/no injected elements.
- [ ] Run `node --test tests/frontend/transport.test.mjs tests/frontend/request-state.test.mjs tests/frontend/nba-contract.test.mjs tests/frontend/tennis-contract.test.mjs`; then configured browser test command. Explicitly report runner unavailable as a blocker, not passing/skipped success.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/core/test_api.py tests/core/test_site.py tests/sports/nba/test_site_api.py tests/test_tennis_site_api.py tests/test_analytics.py tests/evidence/test_frozen_evidence.py tests/evidence/test_nba_frozen_evidence.py -q` then `.venv/Scripts/python.exe -m pytest -q` in the existing sandbox. Require no writer escapes; inspect any failures instead of dismissing as unrelated.
- [ ] Compare all full baseline hashes and sizes, manifests themselves, protected filenames and model IDs. Verify inventories still 41 tennis/25 NBA. Run `git diff --check`, inspect `git diff --stat` and `git status --short`; confirm only approved implementation/UI/test files changed, no evidence/operational scripts.
- [ ] Hermes final review records actual command exit statuses, test counts from output, screenshot paths, keyboard/contrast results, known feed provenance limitations and exact remaining blockers. Do not say “live API verified” for provider-stubbed ASGI tests or “accessible” solely from HTML attributes.

## Known blockers and intentional non-goals

- Actual pre-tip A7 predictions cannot be produced from the documented free pipeline; no frontend can repair post-game-only availability. Historical A5/API reconstruction and A7 claim reads stay separately labeled.
- NBA board has no names/odds/observation freshness. Use honest IDs and provenance, not an invented NBA slate from scheduled games or games_today.
- Tennis group feed mode/minimum cache age do not establish event-level freshness; no actionable live claim is permitted. Additional backend metadata would be a separately approved scope.
- Root package.json/browser harness is absent. Browser and Node availability must be discovered; installation requires approval. No services were started, test suites run, or API calls made during planning.
- Current evidence test literals constrain tennis copy. Preserve historical disclosure or migrate only presentation assertions without touching bytes/re-derived truths.

## Self-review and handoff

Spec coverage: NBA contracts/provenance/teams denominators Tasks 1–3; tennis mock/time/ID/players contracts Tasks 4–5; all request/error/security/accessibility states Tasks 1–6; hash protection every checkpoint and Task 6. Review Focus items each have named regression cases. Interfaces retain separate schemas and exact injected routes; visual conventions are shared without new backend mounting.

These are proposed future tests; no implementation or green result is claimed. Written spec and plan review are the next gate. Keep the approved later DeepSeek coding/Hermes review method; do not launch delegation until the parent/user explicitly authorizes implementation.

// Zero-dependency browser harness for Court Evidence rendered checks.
//
// Uses an already-installed Chrome/Edge via the Chrome DevTools Protocol over
// Node's global WebSocket + fetch. No npm install is required. An isolated
// test-only HTTP server serves the REAL sports/nba/site static files and
// controlled GET API fixtures (explicitly labelled x-court-test-fixture).
//
// NOT a product service: it binds loopback only, checks the port is free first,
// and never calls a paid feed. Set COURT_NBA_URL to target an explicit base.

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const repoRoot = path.resolve(here, "..", "..");
const SITE = path.join(repoRoot, "sports", "nba", "site");
const TENNIS_SITE = path.join(repoRoot, "frontend");
const NBA_DATA = path.join(repoRoot, "sports", "nba", "data");
const TENNIS_DATA = path.join(repoRoot, "data");

export const NBA_TABS = ["Matches", "Teams", "Model", "Experiments", "Methodology"];
export const TENNIS_TABS = ["Matches", "Players", "Model", "Experiments", "Methodology"];
export const TENNIS_GROUPS = ["usopen", "tennis"];

// Read a frozen JSON artifact if present. This is test-fixture plumbing only:
// it reads bytes already on disk and NEVER imports backend modules or calls a
// provider. The resulting body shape mirrors the real API responses so the
// renderer is exercised against the ACTUAL nested artifact schema.
function readJsonMaybe(file) {
  try { return JSON.parse(readFileSync(file, "utf8")); } catch (_e) { return null; }
}

// ---- actual NBA artifact-shaped responses (mirrors sports/nba/evidence.py) ---
export function nbaActualResponses() {
  const w = readJsonMaybe(path.join(NBA_DATA, "nba_walkforward_v1.json"));
  const r = readJsonMaybe(path.join(NBA_DATA, "formula_v1_report.json"));
  const avail = readJsonMaybe(path.join(NBA_DATA, "nba_availability_v1.json"));
  const cal = readJsonMaybe(path.join(NBA_DATA, "nba_calibration_v1.json"));
  const ab = readJsonMaybe(path.join(NBA_DATA, "nba_ablation_v1.json"));
  if (!w || !r || !avail || !cal || !ab) return null;
  const model = {
    formula: w.formula,
    tiers: w.tiers,
    pooled_all_test: w.pooled_all_test,
    pooled_with_close: w.pooled_with_close,
    pooled_with_open: w.pooled_with_open,
    n_test_games: w.n_test_games,
    notes: w.notes,
    by_season: r.by_season,
    paired_vs_close: r.pooled_test_paired_formula_vs_market,
    tuning_paired: r.tuning_season_paired_formula_vs_market,
  };
  const experiments = { availability: avail, calibration: cal, ablation: ab };
  const ARTIFACTS = {
    formula: "formula_v1.json",
    walkforward: "nba_walkforward_v1.json",
    walkforward_contaminated: "nba_walkforward_v1_contaminated.json",
    availability: "nba_availability_v1.json",
    calibration: "nba_calibration_v1.json",
    ablation: "nba_ablation_v1.json",
    formula_report: "formula_v1_report.json",
  };
  const artifacts = Object.entries(ARTIFACTS).map(([key, file]) => {
    const p = path.join(NBA_DATA, file);
    const entry = { key, file, bytes: existsSync(p) ? statSync(p).size : null };
    const j = readJsonMaybe(p);
    if (j && j.generated_by != null) entry.generated_by = j.generated_by;
    return entry;
  });
  const site_meta = {
    sport: "nba", tabs: NBA_TABS, artifacts,
    scope: "regular-season moneyline winner (incl. OT); 2005-06..2025-26",
    formula: "formula_v1.json (published coefficients)",
  };
  return { model, experiments, sim: w, site_meta };
}

// ---- actual tennis artifact-shaped responses (mirrors analytics.model_summary)
export function tennisActualModel() {
  const sim = readJsonMaybe(path.join(TENNIS_DATA, "sim_2024_2025.json"));
  const cal = readJsonMaybe(path.join(TENNIS_DATA, "cal_validation.json"));
  const tourney = readJsonMaybe(path.join(TENNIS_DATA, "tourney.json"));
  const ab = readJsonMaybe(path.join(TENNIS_DATA, "ablation.json"));
  const manifest = readJsonMaybe(path.join(TENNIS_DATA, "sim_frozen", "manifest.json"));
  if (!sim || !cal || !tourney || !ab) return null;
  const imp = (tourney.improvement_vs_A) || {};
  const folds = (tourney.results) || {};
  return {
    sim: {
      n: (((sim.arms || {}).adaptive) || {}).n,
      arms: sim.arms,
      per_surface: sim.per_surface,
      confidence: sim.confidence,
      paired_mean: sim.paired_mean,
      gbm_vs_adaptive: sim.gbm_vs_adaptive,
      gbm_vs_frozen: sim.gbm_vs_frozen,
      betting: sim.betting,
      policies: sim.policies,
      line_note: sim.line_note,
      use_market: sim.use_market,
    },
    calibration_validation: {
      n_fit: cal.n_fit, n_test: cal.n_test, methods: cal.methods,
      verdict: "raw GBM ships uncalibrated: none 0.2141/ECE 0.0136 beats temperature, logistic and isotonic (all gain<=0)",
    },
    column_tournament: {
      sets: tourney.sets, folds: tourney.folds, improvement_vs_A: imp,
      B_folds: folds.B,
      verdict: "ship B (shrunk serve states + missingness + age): +0.00082 mean, 4/4 folds positive, CI excludes zero. C/D extras killed (unproven over B). E dead (kstar=0 everywhere).",
    },
    ablation: ab,
    gbm_freeze: manifest || {},
  };
}

const NBA_CONFIG = {
  title: "Court Evidence — NBA",
  sport: "nba",
  tabs: ["Matches", "Teams", "Model", "Experiments", "Methodology"],
  api: {
    site_meta: "/api/site_meta", board: "/api/board", predict: "/api/predict",
    model: "/api/model", experiments: "/api/experiments", teams: "/api/teams",
    reliability: "/api/reliability", sim: "/api/sim", stats: "/api/stats",
    live_log: "/api/live/log",
  },
};

// TEST-ONLY race fixture page. It mounts the genuine sports/nba/site/nba-app.js
// (and its real transport/contract modules) with a controlled read client so a
// superseded historical lookup can be resolved after a newer one. This is a
// browser fixture, never an ASGI integration and never product code.
const RACE_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Court Evidence — race fixture (test-only)</title>
<link rel="stylesheet" href="/static/court-evidence.css">
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
<div id="app" data-sport="nba"><main id="main"></main></div>
<script>
window.__RACE_PENDING__ = {};
window.__RACE_CLIENT__ = {
  getJson(path) {
    if (String(path).includes("/api/predict")) {
      return new Promise((resolve) => { window.__RACE_PENDING__[String(path)] = resolve; });
    }
    return fetch(path).then((r) => r.json()).then((payload) => ({ kind: "ok", payload, fetchedAt: Date.now() }));
  },
};
</script>
<script type="module">
import { mountNbaApp } from "/static/nba-app.js";
window.__COURT_APP__ = mountNbaApp({
  root: document.getElementById("app"),
  adapter: window.__ADAPTER__ || null,
  client: window.__RACE_CLIENT__,
});
</script>
</body>
</html>`;

// TEST-ONLY tennis race page. Mounts the genuine frontend/tennis-app.js with a
// controlled read client that holds selected paths until the test resolves them
// and deliberately IGNORES AbortSignal, so the app's own generation guard is
// what protects the newest selection (not fetch cancellation).
const TENNIS_RACE_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Court Evidence — tennis race fixture (test-only)</title>
<link rel="stylesheet" href="/static/court-evidence.css">
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
<div id="app" data-sport="tennis"><main id="main"></main></div>
<template id="court-disclosure"><div class="disclosure"><h3>Historical paper simulation — retrospective only</h3><p>Fixture disclosure.</p></div></template>
<script>
window.__RACE_HOLD__ = [];
window.__RACE_PENDING__ = {};
window.__RACE_CLIENT__ = {
  getJson(path) {
    const hold = window.__RACE_HOLD__ || [];
    if (hold.some((p) => String(path).includes(p))) {
      return new Promise((resolve) => {
        const k = String(path);
        (window.__RACE_PENDING__[k] = window.__RACE_PENDING__[k] || []).push(resolve);
      });
    }
    return fetch(path).then((r) => r.json()).then((payload) => ({ kind: "ok", payload, fetchedAt: Date.now() }));
  },
};
</script>
<script type="module">
import { mountTennisApp } from "/static/tennis-app.js";
window.__COURT_APP__ = mountTennisApp({
  root: document.getElementById("app"),
  adapter: window.__ADAPTER__ || null,
  client: window.__RACE_CLIENT__,
});
</script>
</body>
</html>`;

export function fixtureData() {
  // Model / experiments / sim / site_meta come from the ACTUAL frozen disk
  // artifacts so the renderer is stressed with the real nested schema, long
  // tier identifiers, seven coefficients, real filenames and generated_by
  // commands. Board / teams / stats / reliability remain explicit test-only
  // fixtures (they are NOT to be reported as freshly verified backend numbers).
  const actual = nbaActualResponses();
  const base = {
    board: { board: [], n: 0 },
    teams: { home_win_rate: 0.58, n_games: 3, teams: [{ team_id: 1, team: "Alpha", wins: 2, games: 3, win_rate: 0.667 }] },
    stats: { db_counts: { games: 3, games_completed: 2, games_scheduled: 1, odds_snapshots: 0, features: 0, game_inactives: 0, game_traditional: 0, game_officials: 0 }, seasons: [{ season: "2026-27", n: 1 }], artifacts: [] },
    reliability: { n: 4, bins: [{ lo: 0, hi: 0.5, n: 3, mean_p: 0.3, rate: 0.33 }, { lo: 0.5, hi: 1, n: 0, mean_p: null, rate: null }], ece: 0.01, note: "fixture reliability" },
    predict: null, // per-request
  };
  if (!actual) {
    return {
      ...base,
      model: {
        formula: { features: ["elo_diff", "rest_diff"], coef_raw: { elo_diff: 0.9, rest_diff: 0.1 }, intercept_raw: 0.0, n_train: 20000, train_end: "2020-21", tuned_on: "2021-22", file: "formula_v1.json" },
        tiers: { T1: { passes: true, claim: "beats naive baselines" }, T2: { passes: false, claim: "beats the opening line" } },
        pooled_all_test: { formula: { brier: 0.208, acc: 0.62, ece: 0.01 }, market_close: { brier: 0.2 } },
        n_test_games: { all: 4888 }, by_season: {}, paired_vs_close: {}, tuning_paired: {}, notes: "fixture",
      },
      experiments: { availability: { verdict: "enter", brier_gap_to_open: { A5_minus_open: 0.007, A6_minus_open: 0.004 } }, calibration: { decision: "raw" }, ablation: { A5: { full_test_brier: 0.208 }, A6: {} } },
      sim: { tiers: { T1: { passes: true } }, pooled_all_test: { formula: { brier: 0.208 } }, n_test_games: { all: 4888 } },
      site_meta: { sport: "nba", tabs: NBA_CONFIG.tabs, artifacts: [{ key: "formula", file: "formula_v1.json", bytes: 1040 }], scope: "regular-season moneyline winner", formula: "formula_v1.json (published coefficients)" },
    };
  }
  return { ...base, model: actual.model, experiments: actual.experiments, sim: actual.sim, site_meta: actual.site_meta };
}

// ---- isolated fixture HTTP server -----------------------------------------

export async function startFixtureServer({ port = 8010 } = {}) {
  const state = fixtureData();
  const requests = [];
  state.failResource = new Set();
  state.predictResponse = (gameId) => ({ error: "unknown game_id", game_id: gameId });

  const indexHtml = readFileSync(path.join(SITE, "index.html"), "utf8");
  const inject = (html, cfg = NBA_CONFIG) => {
    const tag = `<script>window.__ADAPTER__ = ${JSON.stringify(cfg)};</script>`;
    if (/<script>window\.__ADAPTER__ = .*?;<\/script>/s.test(html)) {
      return html.replace(/<script>window\.__ADAPTER__ = .*?;<\/script>/s, () => tag);
    }
    return html.replace("</head>", tag + "\n</head>");
  };

  const contentTypes = { ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8" };

  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    requests.push({ method: req.method, path: url.pathname, query: url.search });
    const label = () => res.setHeader("x-court-test-fixture", "1");
    const json = (body, status = 200) => { label(); res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };

    if (req.method !== "GET") return json({ detail: "read-only test fixture" }, 405);

    const p = url.pathname;
    if (p === "/") {
      const cfg = url.searchParams.get("adapter") === "invalid" ? { sport: "bogus", api: {} } : NBA_CONFIG;
      label(); res.writeHead(200, { "content-type": contentTypes[".html"] }); return res.end(inject(indexHtml, cfg));
    }
    if (p === "/race") {
      // TEST-ONLY page: mounts the REAL nba-app.js with a controlled client that
      // holds /api/predict responses until the test resolves them. It ignores
      // AbortSignal on purpose so the app's own generation guard is exercised.
      label(); res.writeHead(200, { "content-type": contentTypes[".html"] });
      return res.end(inject(RACE_HTML, NBA_CONFIG));
    }
    if (p.startsWith("/static/")) {
      const file = path.join(SITE, p.slice("/static/".length));
      if (!file.startsWith(SITE) || !existsSync(file)) return json({ detail: "not found" }, 404);
      label(); res.writeHead(200, { "content-type": contentTypes[path.extname(file)] || "application/octet-stream" });
      return res.end(readFileSync(file));
    }
    if (p.startsWith("/api/")) {
      const key = p.slice("/api/".length);
      if (key === "predict") return json(state.predictResponse(url.searchParams.get("game_id")));
      if (state.failResource.has(key)) return json({ detail: "fixture failure" }, 500);
      if (key in state) return json(state[key]);
      return json({ detail: "no fixture" }, 404);
    }
    return json({ detail: "not found" }, 404);
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  return {
    state,
    requests,
    origin: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((r) => server.close(() => r())),
  };
}

// ---- isolated tennis fixture HTTP server ----------------------------------

const TENNIS_CONFIG = {
  title: "Court Evidence — ATP tennis",
  sport: "tennis",
  surfaces: ["hard", "clay", "grass"],
  groups: TENNIS_GROUPS,
  tabs: TENNIS_TABS,
  api: {
    health: "/api/health", board: "/api/board", predict: "/api/predict",
    players: "/api/players", player_detail: "/api/players/{name}",
    model: "/api/model", stats: "/api/stats", reliability: "/api/reliability",
  },
};

export function tennisCard(over = {}) {
  return {
    id: "ev1",
    label: "Player A vs Player B",
    commence_time: "2030-01-01T18:00:00Z",
    n_books: 5,
    pick: "Player A",
    conf: 0.62,
    model_prob_a: 0.62,
    fair_source: "pinnacle",
    warning: null,
    unknown_players: [],
    outcomes: {
      "Player A": { model_prob: 0.62, model_prob_free: 0.62, fair_prob: 0.58, best_odds: 1.8, best_book: "Pinnacle", edge: 0.041, kelly_half: 0.03, field_median: 1.78, n_quotes: 5 },
      "Player B": { model_prob: 0.38, model_prob_free: 0.38, fair_prob: 0.42, best_odds: 2.1, best_book: "Pinnacle", edge: null, kelly_half: 0, field_median: 2.05, n_quotes: 5 },
    },
    signals: { elo: 0.6, gbm: 0.62 },
    weights_used: { gbm: 1.0 },
    reasons: ["Elo says Player A", "H2H 4-2", "ML model -> Player A 62%"],
    policy: "pass",
    policy_bandit: "pass",
    status: { started: false, thin: false, hours_old: -24, bettable: false },
    cold_start: { guarded: false, players: [] },
    flags: { disagree: false, gap: 0.04, value: false, edge_raw: false, big_gap: false },
    ...over,
  };
}

export function tennisFixtureData() {
  return {
    feed: "live",
    cache_age_s: 10,
    health: { ok: true, has_key: true, auth: false, provider: { mode: "live" }, quota: { remaining: 100 }, groups: TENNIS_GROUPS },
    boardDelay: {},
    boardOverrides: {},
    boardMatches: null,
    failResource: new Set(),
    playersDelay: 0,
    playersResponder: null,
    playerDetailResponder: null,
    predictResponder: null,
    // Actual frozen tennis artifacts (data/sim_2024_2025.json, cal_validation,
    // tourney, ablation) shaped exactly as analytics.model_summary returns them,
    // so the Model/Experiments tabs are stressed with the real nested schema
    // (fold triplets like 10051/3139/2839, per-surface maps, paired CIs).
    model: tennisActualModel() || {
      sim: {
        n: 5978,
        arms: { adaptive: { n: 5978, acc: 0.68, brier: 0.2068 }, frozen: { n: 5978, acc: 0.67, brier: 0.21 }, gbm: { n: 5978, acc: 0.69, brier: 0.2056 }, elo: { n: 5978, acc: 0.66, brier: 0.22 } },
        per_surface: { adaptive: { hard: { n: 3000, brier: 0.206 }, clay: { n: 1800, brier: 0.207 }, grass: { n: 1178, brier: 0.208 } }, gbm: { hard: { n: 3000, brier: 0.205 }, clay: { n: 1800, brier: 0.206 }, grass: { n: 1178, brier: 0.207 } } },
        confidence: { adaptive: { ">=0.6": { n: 3364, acc: 0.7286, brier: 0.1914 }, ">=0.65": { n: 2318, acc: 0.7692, brier: 0.1736 } } },
        paired_mean: { blocks: 14, mean_diff: -0.001, ci95: [-0.002, 0.0], verdict: "adaptive wins" },
        gbm_vs_adaptive: { mean_diff: -0.0012, ci95: [-0.002, 0.0], verdict: "gbm wins" },
        gbm_vs_frozen: { mean_diff: -0.004, ci95: [-0.006, -0.001], verdict: "gbm wins" },
        betting: { n_posts: 1522, win_rate: 0.61, profit_u: 21.12, roi_on_staked_pct: 17.6, max_drawdown_u: 4.2, by_edge: { "3-5%": { n: 10, expected_u: 1.0, realized_u: 1.2 } }, passed_n: 100, passed_profit_u: 5.0 },
        policies: { bandit: { n_posts: 100, profit_u: 11.04 }, post_all: { n_posts: 200, profit_u: 21.12 }, ev003: { n_posts: 150, profit_u: 20.57 }, ev003_conf60: { n_posts: 120, profit_u: 18.0 } },
        line_note: "Skill vs line (half-Kelly), no vig.",
        use_market: 0.19,
      },
      calibration_validation: { n_fit: 1000, n_test: 2000, methods: { none: { brier: 0.2141, ece: 0.0136, adopt: null }, temp: { brier: 0.2143, ece: 0.0130, adopt: false }, logistic: { brier: 0.2142, ece: 0.0131, adopt: false }, isotonic: { brier: 0.2145, ece: 0.0129, adopt: false } }, verdict: "raw GBM ships uncalibrated" },
      column_tournament: { sets: ["A", "B"], folds: 4, improvement_vs_A: { B: { mean: 0.00082, positive_folds: 4, per_fold: [1, 2, 3, 4] } }, B_folds: { "1": { n: [100, 50, 50], acc: 0.68, brier: 0.2, ece: 0.02, best_iter: 50 } }, verdict: "ship B" },
      ablation: { full_brier: 0.206, rows: [{ variant: "no_gbm", brier: 0.208, delta_brier: 0.0024, acc: 0.68, n: 5978 }] },
      gbm_freeze: {},
    },
    reliability: { n: 12000, bins: [{ p_mean: 0.15, frac_pos: 0.14, n: 1200 }, { p_mean: 0.55, frac_pos: 0.56, n: 1200 }, { p_mean: 0.85, frac_pos: 0.86, n: 1200 }], ece_oof: 0.012, note: "GBM out-of-fold, equal-count bins." },
    stats: { stats: { n_picks: 100, n_settled: 80 }, calibration: {}, tuning: {}, provider: { mode: "live" }, quota: { remaining: 100 } },
    players: {
      "": [
        { name: "Carlos Alcaraz", matches: 200, overall: 2150, hard: 2100, clay: 2200, grass: 2000, blended: 2150, cold_start: false },
        { name: "Jannik Sinner", matches: 180, overall: 2120, hard: 2140, clay: 2080, grass: 2020, blended: 2140, cold_start: false },
        { name: "Rookie Player", matches: 6, overall: 1600, hard: 1600, clay: 1600, grass: 1600, blended: 1600, cold_start: true },
      ],
    },
  };
}

export async function startTennisFixtureServer({ port = 8000 } = {}) {
  const state = tennisFixtureData();
  const requests = [];

  const indexHtml = readFileSync(path.join(TENNIS_SITE, "index.html"), "utf8");
  const inject = (html, cfg = TENNIS_CONFIG) => {
    const tag = `<script>window.__ADAPTER__ = ${JSON.stringify(cfg)};</script>`;
    if (/<script>window\.__ADAPTER__ = .*?;<\/script>/s.test(html)) {
      return html.replace(/<script>window\.__ADAPTER__ = .*?;<\/script>/s, () => tag);
    }
    return html.replace("</head>", tag + "\n</head>");
  };

  const contentTypes = { ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8" };

  const boardBody = (group) => {
    const matches = state.boardMatches
      ? state.boardMatches
      : [tennisCard({ label: `Board ${group}`, ...(state.boardOverrides[group] || {}) })];
    return { group, feed: state.feed, cache_age_s: state.cache_age_s, matches, summary: { n: matches.length, value: 0, disagree: 0 } };
  };

  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    requests.push({ method: req.method, path: url.pathname, query: url.search });
    const label = () => res.setHeader("x-court-test-fixture", "1");
    const json = (body, status = 200) => { label(); res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(body)); };

    if (req.method !== "GET") return json({ detail: "read-only test fixture" }, 405);

    const p = url.pathname;
    if (p === "/") {
      const cfg = url.searchParams.get("adapter") === "invalid" ? { sport: "bogus", api: {} } : TENNIS_CONFIG;
      label(); res.writeHead(200, { "content-type": contentTypes[".html"] }); return res.end(inject(indexHtml, cfg));
    }
    if (p === "/race") {
      // TEST-ONLY page: mounts the REAL tennis-app.js with a controlled client
      // that holds selected paths and ignores AbortSignal on purpose.
      label(); res.writeHead(200, { "content-type": contentTypes[".html"] });
      return res.end(inject(TENNIS_RACE_HTML, TENNIS_CONFIG));
    }
    if (p.startsWith("/static/")) {
      const file = path.join(TENNIS_SITE, p.slice("/static/".length));
      if (!file.startsWith(TENNIS_SITE) || !existsSync(file)) return json({ detail: "not found" }, 404);
      label(); res.writeHead(200, { "content-type": contentTypes[path.extname(file)] || "application/octet-stream" });
      return res.end(readFileSync(file));
    }
    if (p.startsWith("/api/")) {
      const key = p.slice("/api/".length);
      if (state.failResource.has(key)) return json({ detail: "fixture failure" }, 500);
      if (key === "health") return json(state.health);
      if (key === "board") {
        const group = url.searchParams.get("group") || "usopen";
        const delay = state.boardDelay[group] || 0;
        return setTimeout(() => json(boardBody(group)), delay);
      }
      if (key === "predict") {
        const id = url.searchParams.get("event_id");
        if (state.predictResponder) return json(state.predictResponder(id));
        return json({ error: "no events" });
      }
      if (key === "players") {
        const q = (url.searchParams.get("q") || "").toLowerCase();
        const surface = url.searchParams.get("surface") || "hard";
        const body = state.playersResponder
          ? state.playersResponder(q, surface)
          : { surface, players: state.players[""].filter((pl) => !q || pl.name.toLowerCase().includes(q)) };
        return setTimeout(() => json(body), state.playersDelay);
      }
      if (key.startsWith("players/")) {
        const name = decodeURIComponent(key.slice("players/".length));
        if (state.playerDetailResponder) return json(state.playerDetailResponder(name));
        return json({ name, unknown: true, note: "no rating on record" });
      }
      if (key in state && typeof state[key] !== "function") return json(state[key]);
      return json({ detail: "no fixture" }, 404);
    }
    return json({ detail: "not found" }, 404);
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve());
  });
  const address = server.address();
  return {
    state,
    requests,
    origin: `http://127.0.0.1:${address.port}`,
    reset: () => {
      const fresh = tennisFixtureData();
      for (const k of Object.keys(state)) delete state[k];
      Object.assign(state, fresh);
    },
    close: () => new Promise((r) => server.close(() => r())),
  };
}

// ---- browser discovery -----------------------------------------------------

export function findBrowser() {
  const candidates = [
    process.env.COURT_CHROME,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  ].filter(Boolean);
  return candidates.find((c) => existsSync(c)) || null;
}

// ---- CDP client ------------------------------------------------------------

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id != null && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} (${JSON.stringify(msg.error)})`));
        else resolve(msg.result);
      } else if (msg.method) {
        for (const l of this.listeners) l(msg);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(fn) { this.listeners.push(fn); }
}

async function waitForDevtoolsPort(stderr, proc) {
  return new Promise((resolve, reject) => {
    let buf = "";
    const timer = setTimeout(() => reject(new Error("timed out waiting for Chrome DevTools port")), 20000);
    const onData = (d) => {
      buf += d.toString();
      const m = /DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//.exec(buf);
      if (m) { clearTimeout(timer); proc.stderr.off("data", onData); resolve(Number(m[1])); }
    };
    proc.stderr.on("data", onData);
    proc.once("exit", (code) => { clearTimeout(timer); reject(new Error(`browser exited early (code ${code})`)); });
  });
}

export async function launchBrowser() {
  const exe = findBrowser();
  if (!exe) throw new Error("No Chrome/Edge found (set COURT_CHROME); browser evidence is a blocker, not a pass.");
  const userDataDir = mkdtempSync(path.join(tmpdir(), "court-chrome-"));
  const proc = spawn(exe, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-background-networking", "--mute-audio",
    "--remote-debugging-port=0", `--user-data-dir=${userDataDir}`,
    "--window-size=1280,900", "about:blank",
  ], { stdio: ["ignore", "pipe", "pipe"] });

  const port = await waitForDevtoolsPort(proc, proc);
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = list.find((t) => t.type === "page");
  if (!page) throw new Error("no page target from Chrome");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  const cdp = new CDP(ws);
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Network.enable");
  return {
    cdp,
    close: async () => {
      try { ws.close(); } catch (_e) { /* ignore */ }
      proc.kill();
    },
  };
}

export async function evalInPage(cdp, expression) {
  const r = await cdp.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(`page error: ${r.exceptionDetails.text} ${JSON.stringify(r.exceptionDetails.exception || {})}`);
  return r.result.value;
}

export async function openApp(cdp, origin, query = "") {
  await cdp.send("Page.navigate", { url: origin + "/" + query });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const ready = await evalInPage(cdp, "!!(document.getElementById('app') && document.getElementById('app').dataset && document.getElementById('app').dataset.courtReady === '1')");
      if (ready) return;
    } catch (_e) { /* page still loading */ }
    await new Promise((r) => setTimeout(r, 60));
  }
  throw new Error("app did not reach courtReady within 15s");
}

// Resolve a pending /api/predict request on the race fixture page (test-only).
export async function resolveRace(cdp, gameId, result) {
  return evalInPage(cdp, `(() => {
    const key = Object.keys(window.__RACE_PENDING__).find((k) => k.includes('game_id=${gameId}'));
    if (!key) throw new Error('no pending predict for ${gameId}');
    const resolve = window.__RACE_PENDING__[key];
    delete window.__RACE_PENDING__[key];
    resolve(${JSON.stringify(result)});
    return true;
  })()`);
}

// Tennis race page: hold every path containing any of these fragments. The
// injected client never observes AbortSignal, so a held response can be
// delivered late on purpose.
export async function holdPatterns(cdp, patterns) {
  return evalInPage(cdp, `(() => { window.__RACE_HOLD__ = ${JSON.stringify(patterns)}; return true; })()`);
}

export async function racePendingPaths(cdp) {
  return evalInPage(cdp, "Object.keys(window.__RACE_PENDING__ || {})");
}

// Resolve the OLDEST pending held request whose path contains `fragment` with a
// full transport-shaped result (e.g. {kind:'ok',payload,fetchedAt}, {kind:'http'},
// {kind:'abort'}, {kind:'timeout'}).
export async function resolveRacePath(cdp, fragment, result) {
  return evalInPage(cdp, `(() => {
    const keys = Object.keys(window.__RACE_PENDING__ || {}).filter((k) => k.includes(${JSON.stringify(fragment)}));
    if (!keys.length) throw new Error('no pending request for ' + ${JSON.stringify(fragment)});
    const key = keys[0];
    const q = window.__RACE_PENDING__[key];
    const resolve = q.shift();
    if (!q.length) delete window.__RACE_PENDING__[key];
    resolve(${JSON.stringify(result)});
    return key;
  })()`);
}

export async function clickTab(cdp, name) {
  await evalInPage(cdp, `(() => { const b=[...document.querySelectorAll('.sport-nav button')].find(x=>x.textContent==='${name}'); if(!b) throw new Error('no tab ${name}'); b.click(); return true; })()`);
}

export async function screenshot(cdp, dir, name) {
  mkdirSync(dir, { recursive: true });
  const r = await cdp.send("Page.captureScreenshot", { format: "png" });
  const file = path.join(dir, name);
  writeFileSync(file, Buffer.from(r.data, "base64"));
  return file;
}

// Page-wide geometry: the ONLY sanctioned overflow gate is the document and
// body scroll width vs the viewport. Legitimate internal `.table-scroll`
// content may extend past the viewport INSIDE its constrained region.
export async function pageMetrics(cdp) {
  return evalInPage(cdp, `(() => ({
    innerWidth: window.innerWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body ? document.body.scrollWidth : 0,
  }))()`);
}

// Wait until a page expression is truthy.
export async function waitForExpression(cdp, expression, timeout = 6000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await evalInPage(cdp, `!!(${expression})`)) return;
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`timeout waiting for ${expression}`);
}

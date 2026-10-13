// Court Evidence — rendered milestone gate (Tasks 3, 5, 6).
//
// Real static delivery (sports/nba/site and frontend/) + controlled GET API
// fixtures driven through headless Chrome/Edge over CDP. Fixture responses are
// test-only and labelled x-court-test-fixture; fixture data never enters the
// product. Two isolated loopback servers are used (port 8010 NBA, 8000 tennis);
// each fails loudly if its port is already in use.

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import {
  startFixtureServer, startTennisFixtureServer, launchBrowser, openApp, clickTab,
  evalInPage, screenshot, repoRoot, NBA_TABS, TENNIS_TABS, TENNIS_GROUPS, resolveRace,
  pageMetrics, waitForExpression, holdPatterns, resolveRacePath, tennisCard,
  nbaActualResponses,
} from "./_browser-harness.mjs";
const NBA_ACTUAL = nbaActualResponses();

const TABS = NBA_TABS;
const R2_DIR = path.join(repoRoot, ".hermes", "court-repair-r2");
const R2_SHOTS = path.join(R2_DIR, "screenshots");
const SHOTS = R2_SHOTS;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let fixture;
let tennisFixture;
let browser;
let cdp;
let origin;
let tennisOrigin;
let netLog = [];

function row(over = {}) {
  return {
    type: "predict",
    game_id: "G2026",
    tipoff_ts: "2030-01-01T00:00:00Z",
    logged_at: "2029-12-31T00:00:00Z",
    prob_home: 0.62,
    pick: "home",
    source: "formula_v1",
    ...over,
  };
}

async function waitFor(expression, timeout = 6000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await evalInPage(cdp, `!!(${expression})`)) return;
    await sleep(50);
  }
  throw new Error(`timeout waiting for ${expression}`);
}

const count = (sel) => evalInPage(cdp, `document.querySelectorAll(${JSON.stringify(sel)}).length`);
const mainText = () => evalInPage(cdp, "document.querySelector('main').textContent");

before(async () => {
  if (process.env.COURT_NBA_URL) {
    origin = process.env.COURT_NBA_URL.replace(/\/$/, "");
  } else {
    fixture = await startFixtureServer({ port: 8010 }); // loopback only; fails if 8010 busy
    origin = fixture.origin;
  }
  if (process.env.COURT_TENNIS_URL) {
    tennisOrigin = process.env.COURT_TENNIS_URL.replace(/\/$/, "");
  } else {
    tennisFixture = await startTennisFixtureServer({ port: 8000 }); // loopback only; fails if 8000 busy
    tennisOrigin = tennisFixture.origin;
  }
  browser = await launchBrowser();
  cdp = browser.cdp;
  cdp.on((msg) => {
    if (msg.method === "Network.requestWillBeSent") {
      netLog.push({ url: msg.params.request.url, method: msg.params.request.method });
    }
  });
});

after(async () => {
  if (browser) await browser.close();
  if (fixture) await fixture.close();
  if (tennisFixture) await tennisFixture.close();
});

// ===========================================================================
// NBA
// ===========================================================================

test("nba_all_tabs_render", async () => {
  await openApp(cdp, origin, "?case=all_tabs");
  assert.equal(await count(".sport-nav button"), 5);
  for (const name of TABS) {
    await clickTab(cdp, name);
    const heading = await evalInPage(cdp, "document.querySelector('main h2') && document.querySelector('main h2').textContent");
    assert.ok(heading && heading.length > 0, `tab ${name} rendered a heading`);
    const current = await evalInPage(cdp, "document.querySelector('.sport-nav button[aria-current=\"page\"]').textContent");
    assert.equal(current, name, `aria-current follows the active tab ${name}`);
    await screenshot(cdp, SHOTS, `nba-tab-${name.toLowerCase()}-1280.png`);
  }
});

test("nba_empty_without_fake_cards", async () => {
  if (!fixture) return;
  fixture.state.board = { board: [], n: 0 };
  await openApp(cdp, origin, "?case=empty");
  await clickTab(cdp, "Matches");
  assert.equal(await count(".row-card"), 0);
  const text = await mainText();
  assert.match(text, /No logged predictions yet/);
  assert.doesNotMatch(text, /Tonight/);
  await screenshot(cdp, SHOTS, "nba-matches-empty-1280.png");
});

test("nba_refresh_failure_keeps_last_success", async () => {
  if (!fixture) return;
  fixture.state.failResource = new Set();
  fixture.state.board = { board: [row(), row({ game_id: "G2", prob_home: 0.4, pick: "away" })], n: 2 };
  await openApp(cdp, origin, "?case=refresh");
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('.row-card').length === 2");
  const stampBefore = await evalInPage(cdp, "(() => { const e=[...document.querySelectorAll('.note')].find(x=>x.textContent.startsWith('Last successful response')); return e ? e.textContent : null; })()");
  assert.ok(stampBefore, "a last-successful timestamp is shown");

  fixture.state.failResource = new Set(["board"]);
  await evalInPage(cdp, "(() => { const b=document.querySelector('[data-focus-key=\"refresh-board\"]'); b.click(); return true; })()");
  await waitFor("document.querySelector('main').textContent.includes('Refresh failed')");
  assert.equal(await count(".row-card"), 2, "old rows preserved on failure");
  const stampAfter = await evalInPage(cdp, "(() => { const e=[...document.querySelectorAll('.note')].find(x=>x.textContent.startsWith('Last successful response')); return e ? e.textContent : null; })()");
  assert.equal(stampAfter, stampBefore, "the fetched-at timestamp is not updated by a failed refresh");
  await screenshot(cdp, SHOTS, "nba-matches-refresh-failed-1280.png");
  fixture.state.failResource = new Set();
});

test("nba_historical_is_not_prospective", async () => {
  if (!fixture) return;
  fixture.state.board = { board: [], n: 0 };
  fixture.state.predictResponse = (id) => ({
    game_id: id, date: "2024-01-05", home_team_id: "LAL", away_team_id: "BOS",
    result_home_win: 1, prob_home: 0.62, pick: "home", formula: "formula_v1.json", note: "re-derived from published coefficients",
  });
  await openApp(cdp, origin, "?case=historical");
  await clickTab(cdp, "Matches");
  await evalInPage(cdp, "(() => { const i=document.querySelector('#hist-input'); i.value='202401050LAL'; i.form.requestSubmit(); return true; })()");
  await waitFor("document.querySelector('main').textContent.includes('re-derived from published coefficients')");
  const text = await mainText();
  assert.match(text, /Historical/);
  assert.match(text, /does not establish prospective A7/);
  assert.equal(await count(".row-card"), 0, "historical lookup never enters the live board");
});

test("nba_all_requests_get", async () => {
  if (!fixture) return;
  netLog = [];
  await openApp(cdp, origin, "?case=network");
  await clickTab(cdp, "Model");
  await clickTab(cdp, "Matches");
  const ours = netLog.filter((r) => r.url.startsWith(origin));
  assert.ok(ours.length >= 8, `expected the app to issue several reads, saw ${ours.length}`);
  assert.deepEqual(ours.filter((r) => r.method !== "GET"), [], "all browser evidence traffic must be GET");
});

test("nba_keyboard_navigation", async () => {
  await openApp(cdp, origin, "?case=keyboard");
  await evalInPage(cdp, "(() => { document.querySelectorAll('.sport-nav button')[2].focus(); return true; })()");
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: "\r", unmodifiedText: "\r" });
  await cdp.send("Input.dispatchKeyEvent", { type: "char", key: "Enter", code: "Enter", text: "\r", unmodifiedText: "\r", windowsVirtualKeyCode: 13 });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await waitFor("document.querySelector('.sport-nav button[aria-current=\"page\"]').textContent === 'Model'");
  const active = await evalInPage(cdp, "document.activeElement && document.activeElement.textContent");
  assert.equal(active, "Model", "focus remains on the activated control");
});

test("nba_responsive_layout", async () => {
  await openApp(cdp, origin, "?case=responsive");
  for (const [w, h] of [[390, 844], [768, 1024], [1280, 900]]) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await sleep(120);
    const noOverflow = await evalInPage(cdp, "document.documentElement.scrollWidth <= window.innerWidth + 2");
    assert.ok(noOverflow, `no page-wide horizontal scrolling at ${w}x${h}`);
    assert.equal(await count(".sport-nav button"), 5);
    await screenshot(cdp, SHOTS, `nba-responsive-${w}x${h}.png`);
  }
  await cdp.send("Emulation.clearDeviceMetricsOverride");
});

test("nba_hostile_text_is_literal", async () => {
  if (!fixture) return;
  const hostile = '"><img src=x onerror="window.__pwned=1">';
  fixture.state.board = { board: [row({ source: hostile })], n: 1 };
  await openApp(cdp, origin, "?case=hostile");
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('.row-card').length === 1");
  assert.equal(await evalInPage(cdp, "typeof window.__pwned"), "undefined", "no injected handler fired");
  assert.equal(await count("main img"), 0, "hostile markup is not parsed as elements");
  assert.ok((await mainText()).includes(hostile), "hostile string is rendered literally");
});

test("nba_timezone_midnight_filter", async () => {
  if (!fixture) return;
  fixture.state.board = {
    board: [row({ game_id: "TZ-FUTURE", tipoff_ts: "2030-01-01T00:00:00+14:00", logged_at: "2029-12-31T00:00:00+14:00" })],
    n: 1,
  };
  await openApp(cdp, origin, "?case=tz");
  await clickTab(cdp, "Matches");
  assert.match(await mainText(), /Upcoming \(1\)/, "timezone-bearing future tipoff is Upcoming");
});

// ===========================================================================
// Tennis (Task 5)
// ===========================================================================

test("tennis_all_tabs_render", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_tabs");
  assert.equal(await count(".sport-nav button"), 5);
  for (const name of TENNIS_TABS) {
    await clickTab(cdp, name);
    const heading = await evalInPage(cdp, "document.querySelector('main h2') && document.querySelector('main h2').textContent");
    assert.ok(heading && heading.length > 0, `tab ${name} rendered a heading`);
    assert.equal(await evalInPage(cdp, "document.querySelector('.sport-nav button[aria-current=\"page\"]').textContent"), name);
    await screenshot(cdp, SHOTS, `tennis-tab-${name.toLowerCase()}-1280.png`);
  }
});

test("tennis_mock_suppressed", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "mock";
  await openApp(cdp, tennisOrigin, "?case=tennis_mock");
  await clickTab(cdp, "Matches");
  const text = await mainText();
  assert.match(text, /Mock mode — live predictions suppressed/);
  assert.equal(await count("main .row-card"), 0, "mock never presents prediction cards");
  assert.equal(await count("main .series"), 0, "no probability series in mock mode");
  await screenshot(cdp, SHOTS, "tennis-matches-mock-1280.png");
});

test("tennis_unverified_probabilities_withheld", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.health.has_key = false;
  await openApp(cdp, tennisOrigin, "?case=tennis_unverified");
  await clickTab(cdp, "Matches");
  const text = await mainText();
  assert.match(text, /Unverified feed — probabilities withheld/);
  assert.equal(await count("main .row-card"), 1, "the row is listed…");
  assert.equal(await count("main .series"), 0, "…but no probabilities are shown");
});

test("tennis_live_group_feed_neutral_and_spaced", async () => {
  // Regression: a live group feed mode must read as a provider-wide REPORT, not
  // a green per-event freshness claim, and adjacent meta text must not concatenate.
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.cache_age_s = 10;
  await openApp(cdp, tennisOrigin, "?case=tennis_live_badge");
  await clickTab(cdp, "Matches");

  const badge = await evalInPage(cdp, `(() => {
    const b = [...document.querySelectorAll('main .badge')].find((x) => x.textContent === 'Reported group feed: live');
    return b ? { pass: b.classList.contains('pass'), cls: b.className } : null;
  })()`);
  assert.ok(badge, "the live feed is labelled 'Reported group feed: live'");
  assert.equal(badge.pass, false, "the live feed badge is never the green pass style");
  assert.equal(await count("main .badge.pass"), 0, "no green pass badge is rendered for a group feed");

  const text = await mainText();
  assert.match(text, /Group cache age: 10s/, "server-reported group cache age remains as provenance");
  assert.doesNotMatch(text, /Live feed/, "the old per-event-sounding 'Live feed' label is gone");

  const rawTextNodes = await evalInPage(cdp, `(() => {
    const metas = [...document.querySelectorAll('main .meta')];
    return metas.filter((m) => [...m.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim() !== '')).length;
  })()`);
  assert.equal(rawTextNodes, 0, "every .meta row uses element children so flex gap separates adjacent text");
});

test("tennis_stale_cache_label", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "cache";
  tennisFixture.state.cache_age_s = 120;
  await openApp(cdp, tennisOrigin, "?case=tennis_stale");
  await clickTab(cdp, "Matches");
  assert.match(await mainText(), /Stale group cache/);
  await screenshot(cdp, SHOTS, "tennis-matches-stale-1280.png");
});

test("tennis_offline_retry", async () => {
  tennisFixture.reset();
  tennisFixture.state.failResource = new Set(["board"]);
  await openApp(cdp, tennisOrigin, "?case=tennis_offline");
  await clickTab(cdp, "Matches");
  assert.match(await mainText(), /Server returned an error/);
  assert.equal(await count("[data-focus-key=\"retry-board\"]"), 1, "a visible retry is offered");
  tennisFixture.state.failResource = new Set();
  await evalInPage(cdp, "document.querySelector('[data-focus-key=\"retry-board\"]').click()");
  await waitFor("document.querySelector('main').textContent.includes('Board usopen')");
  await screenshot(cdp, SHOTS, "tennis-matches-offline-retry-1280.png");
});

test("tennis_malformed_board_quarantined", async () => {
  tennisFixture.reset();
  const bad = { id: "bad", label: "Bad", commence_time: "2030-01-01T00:00:00Z", n_books: 3, pick: "X", outcomes: { X: { model_prob: 0.5, model_prob_free: 0.5, fair_prob: 0.5 } } };
  tennisFixture.state.boardMatches = [
    { id: "ok", label: "Good", commence_time: "2030-01-01T18:00:00Z", n_books: 5, pick: "Player A", conf: 0.6, outcomes: { "Player A": { model_prob: 0.6, model_prob_free: 0.6, fair_prob: 0.58 }, "Player B": { model_prob: 0.4, model_prob_free: 0.4, fair_prob: 0.42 } }, signals: {}, reasons: [], unknown_players: [], flags: {}, status: { started: false, thin: false, bettable: true } },
    bad,
  ];
  await openApp(cdp, tennisOrigin, "?case=tennis_malformed");
  await clickTab(cdp, "Matches");
  assert.match(await mainText(), /1 quarantined row/);
  assert.equal(await count("main .row-card"), 1, "only the valid row is shown as a prediction");
});

test("tennis_demo_opt_in_only", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_demo");
  await clickTab(cdp, "Matches");
  const beforeText = await mainText();
  assert.doesNotMatch(beforeText, /DEMO — not live/, "demo is not shown until opted in");
  await evalInPage(cdp, "document.querySelector('[data-focus-key=\"demo-toggle\"]').click()");
  await waitFor("document.querySelector('main').textContent.includes('DEMO — not live')");
  assert.ok((await mainText()).includes("Demo Player A"), "demo rows render only after opt-in");
});

test("tennis_no_write_controls", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_nowrite");
  for (const name of ["Matches", "Players", "Model", "Experiments", "Methodology"]) await clickTab(cdp, name);
  const opButtons = await evalInPage(cdp, "document.querySelectorAll('button').length ? [...document.querySelectorAll('button')].map(b=>b.textContent.toLowerCase()).filter(t=>/learn|log pick|settle|rollback|copy tweet|run learner|track/.test(t)).length : 0");
  assert.equal(opButtons, 0, "no operational buttons remain");
  const html = await evalInPage(cdp, "document.documentElement.outerHTML");
  assert.doesNotMatch(html, /\/api\/(track|settle|learn|rollback)/, "no write endpoints are referenced");
});

test("tennis_group_race", async () => {
  tennisFixture.reset();
  tennisFixture.state.boardDelay = { usopen: 250, tennis: 8 };
  tennisFixture.state.boardOverrides = { usopen: { label: "Board usopen" }, tennis: { label: "Board tennis" } };
  await openApp(cdp, tennisOrigin, "?case=tennis_group_race");
  await clickTab(cdp, "Matches");
  // select ATP (fast), then US Open (slow), then ATP again (fast, supersedes slow)
  await evalInPage(cdp, "(() => { const b=[...document.querySelectorAll('.filters button')].find(x=>x.textContent.includes('ATP Tour')); b.click(); return true; })()");
  await waitFor("document.querySelector('main').textContent.includes('Board tennis')");
  await evalInPage(cdp, "(() => { const b=[...document.querySelectorAll('.filters button')].find(x=>x.textContent.includes('US Open')); b.click(); return true; })()");
  await evalInPage(cdp, "(() => { const b=[...document.querySelectorAll('.filters button')].find(x=>x.textContent.includes('ATP Tour')); b.click(); return true; })()");
  await sleep(450); // the superseded slow US Open response arrives late
  const text = await mainText();
  assert.ok(text.includes("Board tennis"), "the newest group response wins");
  assert.ok(!text.includes("Board usopen"), "a superseded group response never overwrites results");
});

test("tennis_player_search_debounce_and_race", async () => {
  tennisFixture.reset();
  tennisFixture.state.playersResponder = (q, surface) => ({
    surface,
    players: [{ name: `Player ${q || "ALL"}-${surface}`, matches: 10, overall: 1500, hard: 1500, clay: 1500, grass: 1500, blended: 1500, cold_start: false }],
  });
  await openApp(cdp, tennisOrigin, "?case=tennis_search");
  await clickTab(cdp, "Players");
  netLog = [];
  await evalInPage(cdp, "(() => { const i=document.querySelector('#player-query'); for (const v of ['c','ca','car','carl']) { i.value=v; i.dispatchEvent(new Event('input',{bubbles:true})); } return true; })()");
  await sleep(500);
  const playerReqs = netLog.filter((r) => r.url.includes("/api/players?"));
  assert.equal(playerReqs.length, 1, `debounce must collapse the keystrokes into one request (saw ${playerReqs.length})`);

  // race: a slow surface load superseded by a fast one must not overwrite
  tennisFixture.state.playersDelay = 200;
  await evalInPage(cdp, "(() => { const s=document.querySelector('#player-surface'); s.value='clay'; s.dispatchEvent(new Event('change',{bubbles:true})); return true; })()");
  await evalInPage(cdp, "(() => { const s=document.querySelector('#player-surface'); s.value='grass'; s.dispatchEvent(new Event('change',{bubbles:true})); return true; })()");
  await sleep(450);
  assert.match(await mainText(), /Player carl-grass/, "only the newest search response renders");
  assert.doesNotMatch(await mainText(), /Player carl-clay/, "a superseded search response never overwrites");
});

test("tennis_surface_only_affects_ratings", async () => {
  tennisFixture.reset();
  netLog = [];
  await openApp(cdp, tennisOrigin, "?case=tennis_surface");
  await clickTab(cdp, "Players");
  assert.equal(await count("#player-surface"), 1, "surface control exists in the ratings view");
  await clickTab(cdp, "Matches");
  assert.equal(await count("#player-surface"), 0, "no surface control on the match board");
  const boardReqs = netLog.filter((r) => r.url.includes("/api/board"));
  assert.ok(boardReqs.length >= 1);
  assert.ok(boardReqs.every((r) => !r.url.includes("surface")), "the match board is never asked for a surface");
});

test("tennis_probability_series_named", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_series");
  await clickTab(cdp, "Matches");
  const text = await mainText();
  assert.match(text, /Model \(full\)/);
  assert.match(text, /Model \(market-free\)/);
  assert.match(text, /Market/);
});

test("tennis_unknown_player", async () => {
  tennisFixture.reset();
  tennisFixture.state.playerDetailResponder = (name) => ({ name, unknown: true, note: "fewer than 5 tour-level matches on record" });
  await openApp(cdp, tennisOrigin, "?case=tennis_unknown");
  await clickTab(cdp, "Players");
  await waitFor("document.querySelector('.linklike')");
  await evalInPage(cdp, "document.querySelector('.linklike').click()");
  await waitFor("document.querySelector('main').textContent.includes('fewer than 5 tour-level matches')");
});

test("tennis_historical_policy_disclaimer", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_disclaimer");
  await clickTab(cdp, "Experiments");
  const text = await mainText();
  assert.match(text, /Historical paper simulation/);
  assert.match(text, /not actual profit/);
  assert.ok(text.includes("+20.57u"), "the frozen retrospective literal is retained as a disclaimer");
});

test("tennis_missing_model_artifact", async () => {
  tennisFixture.reset();
  tennisFixture.state.model = { sim: null, calibration_validation: null, column_tournament: null, ablation: { error: "not built" }, gbm_freeze: null };
  await openApp(cdp, tennisOrigin, "?case=tennis_missing_model");
  await clickTab(cdp, "Model");
  const text = await mainText();
  assert.match(text, /artifact missing/);
  assert.ok(text.includes("—"), "missing artifacts render as — not zero");
});

test("tennis_hostile_text_is_literal", async () => {
  tennisFixture.reset();
  const hostile = '"><img src=x onerror="window.__pwned=1">';
  tennisFixture.state.boardOverrides = { usopen: { label: hostile, reasons: [hostile] } };
  await openApp(cdp, tennisOrigin, "?case=tennis_hostile");
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('main .row-card').length === 1");
  assert.equal(await evalInPage(cdp, "typeof window.__pwned"), "undefined", "no injected handler fired");
  assert.equal(await count("main img"), 0, "hostile markup is not parsed as elements");
  assert.ok((await mainText()).includes(hostile), "hostile string is rendered literally");
});

test("tennis_responsive_layout", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_responsive");
  for (const [w, h] of [[390, 844], [768, 1024], [1280, 900]]) {
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
    await sleep(120);
    assert.ok(await evalInPage(cdp, "document.documentElement.scrollWidth <= window.innerWidth + 2"), `no page scroll at ${w}x${h}`);
    await clickTab(cdp, "Matches");
    await screenshot(cdp, SHOTS, `tennis-responsive-${w}x${h}.png`);
  }
  await cdp.send("Emulation.clearDeviceMetricsOverride");
});

test("tennis_refresh_failure_preserves_timestamp", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_refresh");
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('main .row-card').length === 1");
  const before = await evalInPage(cdp, "(() => { const e=[...document.querySelectorAll('.note')].find(x=>x.textContent.startsWith('Last successful response')); return e?e.textContent:null; })()");
  assert.ok(before, "a last-successful timestamp is shown");
  tennisFixture.state.failResource = new Set(["board"]);
  await evalInPage(cdp, "document.querySelector('[data-focus-key=\"refresh-board\"]').click()");
  await waitFor("document.querySelector('main').textContent.includes('Refresh failed')");
  assert.equal(await count("main .row-card"), 1, "old data preserved");
  const after = await evalInPage(cdp, "(() => { const e=[...document.querySelectorAll('.note')].find(x=>x.textContent.startsWith('Last successful response')); return e?e.textContent:null; })()");
  assert.equal(after, before, "failed refresh does not move the fetched-at timestamp");
  tennisFixture.state.failResource = new Set();
});

test("tennis_keyboard_navigation", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_keyboard");
  await evalInPage(cdp, "(() => { document.querySelectorAll('.sport-nav button')[1].focus(); return true; })()");
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: "\r", unmodifiedText: "\r" });
  await cdp.send("Input.dispatchKeyEvent", { type: "char", key: "Enter", code: "Enter", text: "\r", unmodifiedText: "\r", windowsVirtualKeyCode: 13 });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await waitFor("document.querySelector('.sport-nav button[aria-current=\"page\"]').textContent === 'Players'");
  assert.equal(await evalInPage(cdp, "document.activeElement && document.activeElement.textContent"), "Players", "focus remains on the activated control");
});

test("tennis_zoom_proxy_200", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_zoom");
  // 200% zoom proxy: half-viewport CSS width at 2x scale.
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 640, height: 450, deviceScaleFactor: 2, mobile: false });
  await sleep(150);
  await clickTab(cdp, "Matches");
  assert.ok(await evalInPage(cdp, "document.documentElement.scrollWidth <= window.innerWidth + 2"), "no page-wide horizontal scrolling at 200% zoom proxy");
  assert.equal(await count(".sport-nav button"), 5, "all tabs remain reachable at 200% zoom proxy");
  await screenshot(cdp, SHOTS, "tennis-zoom-200.png");
  await cdp.send("Emulation.clearDeviceMetricsOverride");
});

// ===========================================================================
// Cross-sport regression (Task 6)
// ===========================================================================

test("all_network_get", async () => {
  netLog = [];
  await openApp(cdp, origin, "?case=x_nba");
  for (const name of TABS) await clickTab(cdp, name);
  await openApp(cdp, tennisOrigin, "?case=x_tennis");
  for (const name of TENNIS_TABS) await clickTab(cdp, name);
  const ours = netLog.filter((r) => r.url.startsWith(origin) || r.url.startsWith(tennisOrigin));
  assert.ok(ours.length >= 12, `expected reads from both sports, saw ${ours.length}`);
  assert.deepEqual(ours.filter((r) => r.method !== "GET"), [], "cross-sport browser traffic is GET only");
});

test("invalid_adapter_blocks_requests", async () => {
  for (const base of [origin, tennisOrigin]) {
    netLog = [];
    await openApp(cdp, base, "?adapter=invalid");
    assert.match(await mainText(), /Configuration unavailable/);
    const apiCalls = netLog.filter((r) => r.url.startsWith(base) && r.url.includes("/api/"));
    assert.deepEqual(apiCalls, [], `an invalid adapter must not issue API requests (${base})`);
  }
});

test("http200_error_not_empty", async () => {
  if (!fixture) return;
  fixture.state.board = { error: "boom" }; // HTTP 200 body with an error, not an empty slate
  await openApp(cdp, origin, "?case=http200");
  await clickTab(cdp, "Matches");
  const text = await mainText();
  assert.match(text, /Invalid response/);
  assert.doesNotMatch(text, /No logged predictions yet/);
  fixture.state.board = { board: [], n: 0 };
});

test("null_numeric_not_zero", async () => {
  if (!fixture) return;
  fixture.state.reliability = { n: 4, bins: [{ lo: 0, hi: 0.5, n: 3, mean_p: 0.3, rate: 0.33 }, { lo: 0.5, hi: 1, n: 0, mean_p: null, rate: null }], ece: null, note: "fixture" };
  await openApp(cdp, origin, "?case=nullnum");
  await clickTab(cdp, "Methodology");
  assert.match(await mainText(), /— \(empty bin\)/, "empty bins render — (empty bin), never 0");
});

test("contrast_and_focus", async () => {
  await openApp(cdp, tennisOrigin, "?case=contrast");
  const ratio = await evalInPage(cdp, `(() => {
    function lum(c) {
      const p = c.match(/\\d+(?:\\.\\d+)?/g).map(Number);
      const [r, g, b] = p.slice(0, 3).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    }
    const cs = getComputedStyle(document.body);
    const L1 = lum(cs.color), L2 = lum(cs.backgroundColor);
    const hi = Math.max(L1, L2), lo = Math.min(L1, L2);
    return (hi + 0.05) / (lo + 0.05);
  })()`);
  assert.ok(ratio >= 4.5, `body text contrast ${ratio.toFixed(2)} must be >= 4.5`);
  await evalInPage(cdp, "document.body.focus()");
  await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 });
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 });
  const tag = await evalInPage(cdp, "document.activeElement && document.activeElement.tagName");
  assert.ok(["A", "BUTTON", "INPUT", "SELECT"].includes(tag), `Tab reaches an interactive control (got ${tag})`);
});

test("all_views_table_chart_parity", async () => {
  for (const base of [origin, tennisOrigin]) {
    await openApp(cdp, base, "?case=parity");
    const tabs = base === origin ? TABS : TENNIS_TABS;
    for (const name of tabs) {
      await clickTab(cdp, name);
      const structured = await evalInPage(cdp, "document.querySelectorAll('main table, main dl, main .series, main .row-card, main svg, main form, main .filters, main .card').length");
      const len = (await mainText()).length;
      assert.ok(structured >= 1, `${name} on ${base} has structured evidence, not only raw JSON`);
      assert.ok(len > 40, `${name} on ${base} has real content`);
    }
  }
});

test("static_modules_same_origin", async () => {
  netLog = [];
  await openApp(cdp, tennisOrigin, "?case=static");
  const staticReqs = netLog.filter((r) => r.url.includes("/static/"));
  assert.ok(staticReqs.length >= 2, "the page loads its local modules");
  assert.ok(staticReqs.every((r) => r.url.startsWith(tennisOrigin)), "every module is same-origin");
});

// ===========================================================================
// Repair R1 regression cases (findings 1, 2, 5).
// Browser fixtures only (x-court-test-fixture / the test-only /race page);
// these are NOT ASGI integration tests and never represent product data.
// ===========================================================================

const histPayload = (id, note) => ({
  game_id: id, date: "2024-01-05", home_team_id: "LAL", away_team_id: "BOS",
  result_home_win: 1, prob_home: 0.62, pick: "home",
  formula: "formula_v1.json", note,
});

async function submitLookup(id) {
  await evalInPage(cdp, `(() => { const i=document.querySelector('#hist-input'); i.value=${JSON.stringify(id)}; i.form.requestSubmit(); return true; })()`);
}

test("nba_historical_race_newest_wins", async () => {
  if (!fixture) return;
  await openApp(cdp, origin, "race");
  await clickTab(cdp, "Matches");
  await submitLookup("A");
  await sleep(40);
  await submitLookup("B");
  await sleep(40);
  await resolveRace(cdp, "B", { kind: "ok", payload: histPayload("B", "re-derived B"), fetchedAt: 123 });
  await waitFor("document.querySelector('main').textContent.includes('re-derived B')");
  await resolveRace(cdp, "A", { kind: "ok", payload: histPayload("A", "re-derived A"), fetchedAt: 456 });
  await sleep(200); // the superseded slow A response lands after B
  const text = await mainText();
  assert.match(text, /Game B/);
  assert.doesNotMatch(text, /re-derived A/, "a superseded historical success never overwrites the newest");
  await screenshot(cdp, SHOTS, "nba-historical-race-1280.png");
});

test("nba_historical_slow_error_cannot_overwrite", async () => {
  if (!fixture) return;
  await openApp(cdp, origin, "race");
  await clickTab(cdp, "Matches");
  await submitLookup("A");
  await sleep(40);
  await submitLookup("B");
  await sleep(40);
  await resolveRace(cdp, "B", { kind: "ok", payload: histPayload("B", "re-derived B"), fetchedAt: 123 });
  await waitFor("document.querySelector('main').textContent.includes('re-derived B')");
  await resolveRace(cdp, "A", { kind: "ok", payload: { error: "unknown game_id", game_id: "A" }, fetchedAt: 456 });
  await sleep(200);
  const text = await mainText();
  assert.match(text, /re-derived B/);
  assert.doesNotMatch(text, /Not a valid historical record/, "a slow superseded error must not overwrite a newer success");
  await screenshot(cdp, SHOTS, "nba-historical-slow-error-1280.png");
});

test("nba_historical_empty_supersession_holds", async () => {
  if (!fixture) return;
  await openApp(cdp, origin, "race");
  await clickTab(cdp, "Matches");
  await submitLookup("A");
  await sleep(40);
  await submitLookup("B");
  await sleep(40);
  await resolveRace(cdp, "B", { kind: "ok", payload: { error: "unknown game_id", game_id: "B" }, fetchedAt: 123 });
  await waitFor("document.querySelector('main').textContent.includes('Not a valid historical record')");
  await resolveRace(cdp, "A", { kind: "ok", payload: histPayload("A", "re-derived A"), fetchedAt: 456 });
  await sleep(200);
  const text = await mainText();
  assert.doesNotMatch(text, /re-derived A/, "a late success must not resurrect a superseded lookup");
  assert.doesNotMatch(text, /Game A/);
});

test("nba_source_timestamps_exact_and_fetched_separate", async () => {
  if (!fixture) return;
  fixture.state.board = { board: [row({ tipoff_ts: "2031-06-15T23:30:00Z", logged_at: "2031-06-15T20:00:00Z" })], n: 1 };
  await openApp(cdp, origin, "?case=src_ts");
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('.row-card').length === 1");
  const text = await mainText();
  assert.ok(text.includes("2031-06-15T23:30:00Z"), "exact ISO tipoff source timestamp is rendered");
  assert.ok(text.includes("2031-06-15T20:00:00Z"), "exact ISO logged source timestamp is rendered");
  const fetched = await evalInPage(cdp, "(() => { const e=[...document.querySelectorAll('main .provenance')].find(x=>x.textContent.startsWith('Fetched at ')); return e?e.textContent:null; })()");
  assert.ok(fetched, "fetched provenance is rendered separately");
  assert.doesNotMatch(fetched, /2031-06-15T23:30:00Z/, "fetched provenance is not the source tipoff timestamp");
  await screenshot(cdp, SHOTS, "nba-source-timestamps-1280.png");
  fixture.state.board = { board: [], n: 0 };
});

test("tennis_health_provider_mock_suppresses_live", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.health = { ok: true, has_key: true, auth: false, provider: { mode: "mock" }, quota: { remaining: 100 }, groups: TENNIS_GROUPS };
  await openApp(cdp, tennisOrigin, "?case=tennis_provider_mock");
  await clickTab(cdp, "Matches");
  const text = await mainText();
  assert.match(text, /Unverified feed — probabilities withheld/);
  assert.equal(await count("main .series"), 0, "a contradictory provider mode withholds probabilities");
  await screenshot(cdp, SHOTS, "tennis-provider-mock-1280.png");
});

test("tennis_missing_health_ok_suppresses_live", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.health = { has_key: true, provider: { mode: "live" }, groups: TENNIS_GROUPS };
  await openApp(cdp, tennisOrigin, "?case=tennis_no_ok");
  await clickTab(cdp, "Matches");
  assert.match(await mainText(), /Unverified feed — probabilities withheld/);
  assert.equal(await count("main .series"), 0, "a missing health.ok fails closed");
});

test("tennis_failed_health_refresh_revokes_live", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "?case=tennis_health_revoke");
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('main .series').length > 0");
  tennisFixture.state.failResource = new Set(["health"]);
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await waitFor("document.querySelector('main').textContent.includes('Unverified feed — probabilities withheld')");
  assert.equal(await count("main .series"), 0, "a failed health refresh revokes old live readiness");
  await screenshot(cdp, SHOTS, "tennis-health-revoked-1280.png");
  tennisFixture.state.failResource = new Set();
});

// ===========================================================================
// Repair R1 followup — finding 6 real tennis-app prediction-detail cases.
//
// These drive the GENUINE mountTennisApp (its own on-demand /api/predict detail
// load + tennis-contract.js parsePrediction). The payloads come from the
// test-only fixture server (labelled x-court-test-fixture); this is NEVER an
// ASGI integration and never product data.
// ===========================================================================

function predictDetailPayload(id, over = {}) {
  return {
    event: { id, label: "Player A vs Player B", commence_time: "2030-01-01T18:00:00Z" },
    verdict: { pick: "Player A", conf: 0.62, model_prob_a: 0.62, model_prob_cal_a: 0.62, model_prob_free_a: 0.62, model_prob_pick: 0.62 },
    market: {
      "Player A": { model_prob: 0.62, model_prob_free: 0.62, best_odds: 1.8, best_book: "Pinnacle" },
      "Player B": { model_prob: 0.38, model_prob_free: 0.38, best_odds: 2.1, best_book: "Pinnacle" },
    },
    fair_source: "pinnacle",
    warning: null,
    unknown_players: [],
    feed: "live",
    policy: { action: "pass" },
    tweet: "[USO Model] Player A — do not render this",
    ...over,
  };
}

async function loadPredictDetail(id) {
  await evalInPage(cdp, `(() => {
    const i = document.querySelector('#predict-input');
    if (!i) throw new Error('no prediction detail input');
    i.value = ${JSON.stringify(id)};
    i.form.requestSubmit();
    return true;
  })()`);
}

test("tennis_predict_detail_same_id_malformed_fails_closed", async () => {
  tennisFixture.reset();
  tennisFixture.state.predictResponder = (id) => predictDetailPayload(id, {
    market: {
      "Player A": { model_prob: 0.62, model_prob_free: 0.62, fair_prob: 42 },
      "Player B": { model_prob: 0.38, model_prob_free: 0.38, fair_prob: 0.42 },
    },
  });
  await openApp(cdp, tennisOrigin, "?case=tennis_predict_malformed");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelector('main').textContent.includes('Not a valid prediction')");
  assert.equal(await count("main .predict-detail-ready"), 0, "a same-ID malformed prediction never renders as ready");
  await screenshot(cdp, SHOTS, "tennis-predict-malformed-1280.png");
});

test("tennis_predict_detail_mock_fails_closed", async () => {
  tennisFixture.reset();
  tennisFixture.state.predictResponder = (id) => predictDetailPayload(id, { feed: "mock" });
  await openApp(cdp, tennisOrigin, "?case=tennis_predict_mock");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelector('main').textContent.includes('Not a valid prediction')");
  assert.equal(await count("main .predict-detail-ready"), 0, "a contradictory mock feed cannot become a ready prediction");
  await screenshot(cdp, SHOTS, "tennis-predict-mock-1280.png");
});

test("tennis_predict_detail_returned_id_mismatch_fails_closed", async () => {
  tennisFixture.reset();
  tennisFixture.state.predictResponder = () => predictDetailPayload("some-other-event");
  await openApp(cdp, tennisOrigin, "?case=tennis_predict_otherid");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelector('main').textContent.includes('Not a valid prediction')");
  assert.equal(await count("main .predict-detail-ready"), 0, "a returned-ID mismatch (backend first-event fallback) never renders");
});

test("tennis_predict_detail_ready_renders_real_contract", async () => {
  tennisFixture.reset();
  tennisFixture.state.predictResponder = (id) => predictDetailPayload(id);
  await openApp(cdp, tennisOrigin, "?case=tennis_predict_ready");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelectorAll('main .predict-detail-ready').length === 1");
  const text = await mainText();
  assert.match(text, /Player A vs Player B/);
  assert.doesNotMatch(text, /do not render this/, "the endpoint tweet/policy recommendation is never rendered");
  await screenshot(cdp, SHOTS, "tennis-predict-ready-1280.png");
});

// ===========================================================================
// Court Evidence repair r2 — independent review findings 1..5.
//
// All fixtures here are test-only (x-court-test-fixture / the test-only /race
// pages). Model/Experiments/sim/site_meta bodies are built from the ACTUAL
// frozen disk artifacts so the real nested schema is exercised; board/teams/
// stats/reliability stay explicit fixtures and are never reported as verified
// backend numbers. No backend imports, no provider calls, no writes.
// ===========================================================================

const NBA_SIGNATURE = {
  Matches: "Logged predictions",
  Teams: "as-ingested",
  Model: "Published A5 coefficients",
  Experiments: "Drop-one-signal ablation",
  Methodology: "Artifact inventory",
};
const TENNIS_SIGNATURE = {
  Matches: "Board usopen",
  Players: "Blended surface Elo",
  Model: "Replay arms",
  Experiments: "Per surface",
  Methodology: "Stats",
};

async function assertNoPageOverflow(where) {
  const m = await pageMetrics(cdp);
  assert.ok(m.docScrollWidth <= m.innerWidth + 1, `documentElement overflow at ${where}: ${m.docScrollWidth} > ${m.innerWidth}`);
  assert.ok(m.bodyScrollWidth <= m.innerWidth + 1, `body overflow at ${where}: ${m.bodyScrollWidth} > ${m.innerWidth}`);
}

// Finding 4: the responsive gate must sweep EVERY real tab at EVERY width and
// assert the tab-specific content, not merely "some heading exists".
for (const [sport, base, tabs, sig] of [
  ["nba", () => origin, TABS, NBA_SIGNATURE],
  ["tennis", () => tennisOrigin, TENNIS_TABS, TENNIS_SIGNATURE],
]) {
  test(`r2_${sport}_all_tab_layout_matrix`, async () => {
    await openApp(cdp, base(), sport === "nba" ? "?case=r2_layout" : "?case=r2_layout");
    for (const w of [320, 390, 768, 1280]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: false });
      await sleep(60);
      for (const name of tabs) {
        await clickTab(cdp, name);
        await sleep(50);
        await assertNoPageOverflow(`${sport}/${name}@${w}`);
        assert.equal(
          await evalInPage(cdp, "document.querySelector('.sport-nav button[aria-current=\"page\"]').textContent"),
          name, `${sport} aria-current follows ${name} at ${w}`);
        const text = await mainText();
        assert.ok(text.includes(sig[name]), `${sport}/${name}@${w} shows its tab-specific content (${sig[name]})`);
        await screenshot(cdp, R2_SHOTS, `r2-${sport}-${name.toLowerCase()}-${w}.png`);
      }
    }
    await cdp.send("Emulation.clearDeviceMetricsOverride");
  });
}

// Finding 4: 200%-zoom CSS proxy must cover every tab, not just the default.
for (const [sport, base, tabs] of [
  ["nba", () => origin, TABS],
  ["tennis", () => tennisOrigin, TENNIS_TABS],
]) {
  test(`r2_${sport}_zoom_proxy_all_tabs`, async () => {
    await openApp(cdp, base(), "?case=r2_zoom");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 640, height: 720, deviceScaleFactor: 2, mobile: false });
    await sleep(80);
    for (const name of tabs) {
      await clickTab(cdp, name);
      await sleep(50);
      await assertNoPageOverflow(`${sport}/${name}@200%`);
      assert.equal(await count(".sport-nav button"), 5, `${sport} nav reachable at 200% on ${name}`);
    }
    await screenshot(cdp, R2_SHOTS, `r2-${sport}-zoom-200-alltabs.png`);
    await cdp.send("Emulation.clearDeviceMetricsOverride");
  });
}

// Finding 1: scroller containment. Tables may exceed the viewport INSIDE the
// constrained region, but the region itself must sit inside its parent.
for (const [sport, base, tab] of [["nba", () => origin, "Model"], ["tennis", () => tennisOrigin, "Model"]]) {
  test(`r2_${sport}_table_scroll_containment`, async () => {
    await openApp(cdp, base(), "?case=r2_scroll");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 900, deviceScaleFactor: 1, mobile: false });
    await sleep(60);
    await clickTab(cdp, tab);
    await sleep(60);
    const regions = await evalInPage(cdp, `(() => Array.from(document.querySelectorAll('main .table-scroll')).map((el) => {
      const r = el.getBoundingClientRect();
      const p = el.parentElement.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return { role: el.getAttribute('role'), label: el.getAttribute('aria-label') || '',
        tabindex: el.getAttribute('tabindex'), overflowX: cs.overflowX,
        right: r.right, left: r.left, width: r.width,
        parentRight: p.right, parentLeft: p.left, parentWidth: el.parentElement.clientWidth,
        scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, scrollX: window.scrollX };
    }))()`);
    assert.ok(regions.length >= 1, `${sport} ${tab} has at least one table region`);
    for (const r of regions) {
      assert.equal(r.role, "region", "every .table-scroll is a region");
      assert.ok(r.label.length > 0, "every .table-scroll has a nonempty aria-label");
      assert.equal(r.tabindex, "0", "every .table-scroll is focusable");
      assert.ok(["auto", "scroll"].includes(r.overflowX), `overflowX=${r.overflowX}`);
      assert.ok(r.right <= r.parentRight + 1, `table region right ${r.right} within parent ${r.parentRight}`);
      assert.ok(r.left >= r.parentLeft - 1, "table region left within parent");
      assert.ok(r.width <= r.parentWidth + 1, `table region width ${r.width} <= parent content ${r.parentWidth}`);
      assert.ok(r.scrollX === 0, "measuring table regions must not scroll the page");
    }
    // A populated wide table must actually be scrollable internally.
    const wide = await evalInPage(cdp, `(() => {
      const el = Array.from(document.querySelectorAll('main .table-scroll')).find((x) => x.scrollWidth > x.clientWidth + 1);
      return el ? { index: Array.from(document.querySelectorAll('main .table-scroll')).indexOf(el) } : null;
    })()`);
    assert.ok(wide, `${sport} ${tab} has a populated wide table that scrolls internally`);
    const scrollResult = await evalInPage(cdp, `(() => {
      const el = document.querySelectorAll('main .table-scroll')[${wide.index}];
      el.focus();
      const before = el.scrollLeft;
      return { before, focused: document.activeElement === el, pageX: window.scrollX };
    })()`);
    assert.ok(scrollResult.focused, "the table region accepts focus");
    for (let i = 0; i < 5; i++) {
      await cdp.send("Input.dispatchKeyEvent", { type: "rawKeyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39, nativeVirtualKeyCode: 39 });
      await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39, nativeVirtualKeyCode: 39 });
    }
    await sleep(60);
    const after = await evalInPage(cdp, `(() => {
      const el = document.querySelectorAll('main .table-scroll')[${wide.index}];
      el.scrollLeft = el.scrollWidth; const max = el.scrollLeft;
      return { left: el.scrollLeft, max, pageX: window.scrollX, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth };
    })()`);
    assert.ok(after.max > 0, "the rightmost table content is reachable inside the region");
    assert.ok(after.left + after.clientWidth >= after.scrollWidth - 1, "rightmost value can be brought into view");
    assert.equal(after.pageX, scrollResult.pageX, "keyboard table scrolling never moves the page");
    await cdp.send("Emulation.clearDeviceMetricsOverride");
  });
}

// Findings 2/3: real nested numeric NBA evidence must be PRIMARY (visible
// without opening Raw JSON), and must never be invented as zero.
const mainInnerText = () => evalInPage(cdp, "document.querySelector('main').innerText");

test("r2_nba_experiments_nested_evidence_primary", async () => {
  await openApp(cdp, origin, "?case=r2_experiments");
  await clickTab(cdp, "Experiments");
  await sleep(80);
  // innerText EXCLUDES collapsed <details>, so this proves the numbers are
  // primary content rather than hidden behind Raw JSON.
  const text = await mainInnerText();
  // Expected values are read from the ACTUAL fixture response, not duplicated.
  assert.ok(NBA_ACTUAL, "actual frozen NBA artifacts are available to the fixture");
  const avail = NBA_ACTUAL.experiments.availability;
  const cal = NBA_ACTUAL.experiments.calibration;
  const ab = NBA_ACTUAL.experiments.ablation;
  for (const [label, v] of [
    ["availability A6 pooled Brier", avail.pooled_test_A5_vs_A6.A6.brier],
    ["availability A5 pooled Brier", avail.pooled_test_A5_vs_A6.A5.brier],
    ["availability eligible-test cohort", avail.row_counts.eligible_test],
    ["calibration tune shipped Brier", cal.bakeoff_tune.A5.isotonic.brier],
    ["calibration test shipped Brier", cal.test_evaluation.A5.shipped_metrics.brier],
    ["ablation elo_diff delta", ab.A5.by_feature.elo_diff.delta_pooled],
    ["ablation elo_diff dropped Brier", ab.A5.by_feature.elo_diff.dropped_test_brier_pooled],
    ["ablation A6 train count", ab.A6.n_train],
  ]) {
    assert.ok(text.includes(String(v)), `${label} (${v}) is primary (outside Raw JSON)`);
  }
  assert.ok(text.includes(cal.decision.A5.shipped), "calibration shipped calibrator name is primary");
  assert.ok(text.includes(cal.decision.A6.shipped), "calibration A6 shipped calibrator name is primary");
  assert.ok(text.includes("elo_diff"), "ablation feature rows are primary");
  const openDetails = await count("main details[open]");
  assert.equal(openDetails, 0, "Raw JSON stays secondary and is never required to reach numeric evidence");
  await screenshot(cdp, R2_SHOTS, "r2-nba-experiments-primary-evidence.png");
});

test("r2_nba_model_windows_cohorts_paired", async () => {
  await openApp(cdp, origin, "?case=r2_model");
  await clickTab(cdp, "Model");
  await sleep(80);
  const text = await mainText();
  assert.ok(NBA_ACTUAL, "actual frozen NBA artifacts are available to the fixture");
  const model = NBA_ACTUAL.model;
  // exact test seasons + train/tune windows
  for (const s of model.formula.test_seasons) assert.ok(text.includes(s), `test season ${s} rendered`);
  assert.ok(text.includes(String(model.formula.train_end)), "train_end window rendered");
  assert.ok(text.includes(String(model.formula.tuned_on)), "tuned_on window rendered");
  // distinct availability cohorts (read from the response)
  for (const key of ["all", "with_close", "with_open"]) {
    assert.ok(text.includes(String(model.n_test_games[key])), `cohort ${key} count rendered`);
  }
  // paired intervals (test vs close, and tuning vs market) — read from the response
  for (const pair of [model.paired_vs_close, model.tuning_paired]) {
    for (const x of pair.ci95_blocked) assert.ok(text.includes(String(x)), `paired CI bound ${x} rendered`);
  }
  // per-season + long tier identifiers
  const someSeason = Object.keys(model.by_season)[0];
  assert.ok(text.includes(someSeason), `per-season evidence (${someSeason}) rendered`);
  const tierKey = Object.keys(model.tiers)[0];
  assert.ok(text.includes(tierKey), `long tier identifier ${tierKey} preserved`);
  assert.ok(text.includes(String(model.pooled_all_test.formula.brier)), "formula pooled Brier rendered");
  await screenshot(cdp, R2_SHOTS, "r2-nba-model-windows-cohorts.png");
});

test("r2_nba_model_missing_numeric_is_dash", async () => {
  const backup = fixture.state.model;
  fixture.state.model = {
    formula: { features: ["elo_diff"], coef_raw: { elo_diff: 0.004333 }, intercept_raw: -0.684436, n_train: null, test_seasons: ["2022-23"], train_end: null, tuned_on: null },
    tiers: {},
    pooled_all_test: { formula: { n: null, brier: null, acc: null, ece: null } },
    pooled_with_close: {}, pooled_with_open: {},
    n_test_games: { all: null, with_close: null, with_open: null },
    by_season: {}, paired_vs_close: null, tuning_paired: null, notes: [],
  };
  try {
    await openApp(cdp, origin, "?case=r2_missing");
    await clickTab(cdp, "Model");
    await sleep(80);
    const text = await mainText();
    assert.ok(text.includes("—"), "missing nested numerics render as —");
    assert.doesNotMatch(text, /0\.0000/, "missing brier/accuracy are never invented as zero");
  } finally {
    fixture.state.model = backup;
  }
});

// Repair R2 followup: malformed nested model schema must degrade to explicit
// —/Unknown in the rendered Model view, never to an invented value, a
// "[object Object]" join, or a crash — while valid rows stay exact.
test("r2_nba_model_malformed_schema_renders_unknown", async () => {
  const backup = fixture.state.model;
  fixture.state.model = {
    formula: {
      features: ["elo_diff"], coef_raw: { elo_diff: 0.004333 },
      test_seasons: ["2022-23", { bad: 1 }, 42, "", "2025-26"],
      train_end: null, tuned_on: null,
    },
    tiers: {
      T1_beats_naive_baselines: { passes: true },
      T2: { passes: false, claim: "" },
    },
    pooled_all_test: {
      formula: { n: "100", brier: "0.21", acc: Infinity, ece: -1 },
      good_arm: { n: 10, brier: 0.2, acc: 0.6, ece: 0.01 },
    },
    pooled_with_close: "nope",
    pooled_with_open: [],
    n_test_games: { all: "4910", with_close: -5, with_open: NaN },
    by_season: [1, 2, 3],
    paired_vs_close: { n: "5", mean_diff: Infinity, sigma_d: "x", ci95_blocked: [1, "y", 2], blocks: -1 },
    tuning_paired: "junk",
    notes: [],
  };
  try {
    await openApp(cdp, origin, "?case=r2_schema");
    await clickTab(cdp, "Model");
    await waitFor("document.querySelector('main').textContent.includes('Claim ladder')");
    const text = await mainText();
    assert.doesNotMatch(text, /\[object Object\]/, "mixed test seasons never join to [object Object]");
    assert.match(text, /2022-23/, "valid test season preserved");
    assert.match(text, /2025-26/, "valid test season preserved");
    assert.doesNotMatch(text, /4910/, "string cohort count is not rendered as a number");
    assert.doesNotMatch(text, /NaN|Infinity/, "non-finite values never surface as text");
    assert.doesNotMatch(text, /-5\b/, "negative cohort count is not rendered");
    assert.doesNotMatch(text, /Invalid response/, "malformed nested schema degrades gracefully, not a hard failure");
    assert.match(text, /0\.20000/, "valid pooled arm brier preserved");
    assert.match(text, /\(no paired record\)/, "non-object tuning pair renders an explicit gap");
    assert.match(text, /—/, "missing/unknown evidence renders an explicit —");
    const openDetails = await count("main details[open]");
    assert.equal(openDetails, 0, "Raw JSON stays secondary");
    await screenshot(cdp, R2_SHOTS, "r2-nba-model-malformed-schema.png");
  } finally {
    fixture.state.model = backup;
  }
});

// Repair R2 final: a blocked CI is fail-closed in the rendered Model view. A
// malformed interval (reversed, extra, non-numeric) is rejected WHOLE and shows
// an explicit —, never a filtered subset of endpoints and never a fabricated
// significance claim. Valid zero / negative endpoints survive exactly. The same
// rule covers the claim-ladder tier CI, which is an analogous interval renderer.
test("r2_nba_paired_interval_fail_closed_and_zero_negative_survive", async () => {
  const backup = fixture.state.model;
  const pairedCi = `(() => {
    const out = [];
    for (const dl of document.querySelectorAll('main dl.provenance')) {
      const dts = [...dl.querySelectorAll('dt')];
      const dds = [...dl.querySelectorAll('dd')];
      dts.forEach((dt, i) => { if (dt.textContent === '95% CI (blocked)') out.push(dds[i] ? dds[i].textContent : null); });
    }
    return out;
  })()`;
  const tierCi = `(() => {
    const t = [...document.querySelectorAll('main table')].find((x) => [...x.querySelectorAll('thead th')].some((th) => th.textContent === 'tier'));
    if (!t) return null;
    return Object.fromEntries([...t.querySelectorAll('tbody tr')].map((tr) => {
      const td = [...tr.querySelectorAll('td')];
      return [td[0] ? td[0].textContent : '', td[td.length - 1] ? td[td.length - 1].textContent : null];
    }));
  })()`;
  fixture.state.model = {
    ...backup,
    // reversed endpoints — malformed, must render — and never [0.02, -0.01]
    paired_vs_close: { n: 4890, mean_diff: -0.01077, sigma_d: 0.09044, ci95_blocked: [0.02, -0.01], blocks: 489 },
    // valid interval containing an exact zero and a negative endpoint
    tuning_paired: { n: 1227, mean_diff: -0.01171, sigma_d: 0.08522, ci95_blocked: [-0.002, 0.0], blocks: 123 },
    tiers: {
      T2_beats_opening_line: { n_games: 4888, formula_vs: { n: 4888, mean_diff: -0.00681, ci95_blocked: [1, "y", 2] }, passes: false },
      T3_beats_closing_line: { n_games: 4890, formula_vs: { n: 4890, mean_diff: -0.01077, ci95_blocked: [-0.01324, -0.00823] }, passes: false },
    },
  };
  try {
    await openApp(cdp, origin, "?case=r2_paired_ci");
    await clickTab(cdp, "Model");
    await waitFor("document.querySelector('main').textContent.includes('Paired — formula vs closing line')");
    const ci = await evalInPage(cdp, pairedCi);
    assert.equal(ci.length, 2, "both paired records render a blocked-CI value");
    assert.equal(ci[0], "—", "reversed paired CI is rejected whole and shown as —");
    assert.equal(ci[1], "[-0.00200, 0.00000]", "valid zero/negative paired CI survives exactly");
    const text = await mainText();
    assert.doesNotMatch(text, /\[0\.02000, -0\.01000\]/, "reversed interval is never rendered as a plausible pair");
    const tiers = await evalInPage(cdp, tierCi);
    assert.equal(tiers.T2_beats_opening_line, "—", "malformed tier CI (non-numeric member) is rejected whole, never filtered");
    assert.equal(tiers.T3_beats_closing_line, "[-0.01324, -0.00823]", "valid negative tier CI survives exactly");
    assert.doesNotMatch(text, /significan/i, "no significance claim is derived from a malformed interval");
    await screenshot(cdp, R2_SHOTS, "r2-nba-paired-interval-fail-closed.png");

    // Unknown (null) and missing-endpoint intervals are explicit gaps, while a
    // valid negative interval still survives.
    fixture.state.model = {
      ...backup,
      paired_vs_close: { n: 4890, mean_diff: -0.01077, sigma_d: 0.09044, ci95_blocked: null, blocks: 489 },
      tuning_paired: { n: 1227, mean_diff: -0.01171, sigma_d: 0.08522, ci95_blocked: [0.1], blocks: 123 },
    };
    await openApp(cdp, origin, "?case=r2_paired_unknown");
    await clickTab(cdp, "Model");
    await waitFor("document.querySelector('main').textContent.includes('Paired — formula vs closing line')");
    const unknownCi = await evalInPage(cdp, pairedCi);
    assert.equal(unknownCi.length, 2, "both paired records still render");
    assert.equal(unknownCi[0], "—", "null interval renders an explicit — (unknown)");
    assert.equal(unknownCi[1], "—", "missing-endpoint interval is rejected whole as —");
  } finally {
    fixture.state.model = backup;
  }
});

// Repair R2 final: the generic Experiments grouping must not force semantically
// unrelated sibling records (windows vs hyperparameters vs coefficients vs row
// counts vs paired stats) into one sparse wide table. Only records that share an
// identical primitive leaf schema (arms, seasons, calibrators) are merged; every
// other section keeps its own labelled block. The primary numbers must remain
// reachable without opening Raw JSON.
test("r2_nba_experiments_grouping_no_heterogeneous_wide_table", async () => {
  await openApp(cdp, origin, "?case=r2_exp_group");
  await clickTab(cdp, "Experiments");
  await waitFor("document.querySelector('main').textContent.includes('Drop-one-signal ablation')");
  const headers = await evalInPage(cdp, `(() => Array.from(document.querySelectorAll('main .table-scroll table')).map((t) => [...t.querySelectorAll('thead th')].map((th) => th.textContent)))()`);
  const has = (cols, ...names) => names.every((n) => cols.includes(n));
  for (const cols of headers) {
    assert.ok(!has(cols, "fit", "eligible_train"), `no table mixes window keys with row-count keys: ${cols.join(",")}`);
    assert.ok(!has(cols, "fit", "n_train"), `no table mixes window keys with coefficient keys: ${cols.join(",")}`);
    assert.ok(!has(cols, "procedure", "brier"), `no table mixes hyperparameter keys with metric keys: ${cols.join(",")}`);
    assert.ok(!has(cols, "kind", "acc_wilson95"), `no table mixes curve-point keys with arm metric keys: ${cols.join(",")}`);
  }
  // Homogeneous arm/season records still merge into readable tables.
  assert.ok(headers.some((cols) => has(cols, "n", "brier", "ece", "acc")), "arm metric tables still exist");
  const text = await mainInnerText();
  const avail = NBA_ACTUAL.experiments.availability;
  assert.ok(text.includes(String(avail.row_counts.eligible_test)), "availability row-count evidence is primary");
  assert.ok(text.includes(String(avail.pooled_test_A5_vs_A6.A6.brier)), "availability pooled Brier is primary");
  await screenshot(cdp, R2_SHOTS, "r2-nba-experiments-grouping.png");
});

// Finding: long real identifiers + sub-second timestamps must stay exact and
// must not push prose/provenance past the available width.
test("r2_nba_timestamp_stress_long_ids", async () => {
  const longGame = "GAME-20260615-LONGIDENTIFIER-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789";
  const longSource = "provider:long-source-identifier/v1/feed/with/a/very/long/path/formula_v1";
  const tip = "2031-06-15T23:30:00.123456789+05:30";
  const logged = "2031-06-15T20:00:00.000000001+05:30";
  if (fixture) {
    fixture.state.board = { board: [row({ game_id: longGame, tipoff_ts: tip, logged_at: logged, source: longSource })], n: 1 };
  }
  await openApp(cdp, origin, "?case=r2_ts");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(60);
  await clickTab(cdp, "Matches");
  await waitFor("document.querySelectorAll('.row-card').length === 1");
  const text = await mainText();
  assert.ok(text.includes(tip), "exact sub-second tipoff source timestamp rendered");
  assert.ok(text.includes(logged), "exact sub-second logged source timestamp rendered");
  assert.ok(text.includes(longGame), "long game identifier preserved verbatim");
  assert.ok(text.includes(longSource), "long source identifier preserved verbatim");
  await assertNoPageOverflow("nba timestamp stress @390");
  const overflowing = await evalInPage(cdp, `(() => {
    const els = Array.from(document.querySelectorAll('main .note, main .provenance, main .provenance dd, main .meta'));
    return els.filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.className || e.tagName);
  })()`);
  assert.deepEqual(overflowing, [], "no prose/provenance/meta element overflows its own box");
  const wraps = await evalInPage(cdp, `Array.from(document.querySelectorAll('.row-card .meta')).map((m) => getComputedStyle(m).flexWrap)`);
  assert.ok(wraps.length > 0 && wraps.every((w) => w === "wrap"), "row-card meta rows wrap");
  await screenshot(cdp, R2_SHOTS, "r2-nba-timestamp-stress-390.png");
  await cdp.send("Emulation.clearDeviceMetricsOverride");
  if (fixture) fixture.state.board = { board: [], n: 0 };
});

// Finding 5: the general (non-row-card) refresh provenance row must be a
// wrapping flex row with element children and a real gap, and it must stay
// fully visible at 390px while preserving the last-successful timestamp.
test("r2_nba_refresh_meta_flex_wrap", async () => {
  if (!fixture) return;
  await openApp(cdp, origin, "?case=r2_refresh");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(60);
  await clickTab(cdp, "Model");
  await sleep(80);
  const info = await evalInPage(cdp, `(() => {
    const btn = document.querySelector('[data-focus-key="refresh-model"]');
    if (!btn) return null;
    const row = btn.closest('.meta');
    const cs = getComputedStyle(row);
    const stamp = row.querySelector('span');
    const br = btn.getBoundingClientRect();
    const sr = stamp ? stamp.getBoundingClientRect() : null;
    return { display: cs.display, flexWrap: cs.flexWrap, gap: cs.gap,
      childTypes: Array.from(row.childNodes).map((n) => n.nodeType),
      btnRight: br.right, btnLeft: br.left, stampText: stamp ? stamp.textContent : null,
      stampRight: sr ? sr.right : null, stampLeft: sr ? sr.left : null, innerWidth: window.innerWidth };
  })()`);
  assert.ok(info, "model refresh row exists");
  assert.equal(info.display, "flex", "general .meta refresh row is flex");
  assert.equal(info.flexWrap, "wrap", "general .meta refresh row wraps");
  assert.ok(info.gap && info.gap !== "normal" && info.gap !== "0px", `general .meta has a real gap (${info.gap})`);
  assert.ok(info.childTypes.every((t) => t === 1), "refresh row uses element children, never bare text nodes");
  assert.ok(info.stampText && info.stampText.startsWith("Last successful response"), "last-successful timestamp present");
  assert.ok(info.btnLeft >= 0 && info.btnRight <= info.innerWidth + 1, "Refresh remains fully visible at 390px");
  assert.ok(info.stampLeft >= 0 && info.stampRight <= info.innerWidth + 1, "timestamp remains fully visible at 390px");

  const before = info.stampText;
  const backupFail = fixture.state.failResource;
  fixture.state.failResource = new Set(["model"]);
  await evalInPage(cdp, "document.querySelector('[data-focus-key=\"refresh-model\"]').click()");
  await waitFor("document.querySelector('main').textContent.includes('Refresh failed')");
  const afterText = await evalInPage(cdp, "(() => { const e=[...document.querySelectorAll('.note')].find(x=>x.textContent.startsWith('Last successful response')); return e?e.textContent:null; })()");
  assert.equal(afterText, before, "a failed refresh preserves the last-successful timestamp");
  fixture.state.failResource = backupFail;
  await screenshot(cdp, R2_SHOTS, "r2-nba-refresh-meta-390.png");
  await cdp.send("Emulation.clearDeviceMetricsOverride");
});

// ===========================================================================
// Repair r2 finding 7 — abort-IGNORING race immunity for every tennis surface.
// The injected client never observes AbortSignal, so the app's own generation
// guard must protect the newest selection when an older response lands late.
// ===========================================================================

test("r2_tennis_group_race_abort_ignoring", async () => {
  tennisFixture.reset();
  tennisFixture.state.boardOverrides = { usopen: { label: "Board usopen" }, tennis: { label: "Board tennis" } };
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Matches");
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Board usopen')");
  await holdPatterns(cdp, ["/api/board"]);
  const click = (frag) => evalInPage(cdp, `(() => { const b=[...document.querySelectorAll('.filters button')].find(x=>x.textContent.includes(${JSON.stringify(frag)})); b.click(); return true; })()`);
  await click("ATP Tour");   // A (superseded)
  await sleep(30);
  await click("US Open");    // B (newest)
  await sleep(30);
  const board = (label, over = {}) => ({ group: label === "Board usopen" ? "usopen" : "tennis", feed: "live", cache_age_s: 5, matches: [tennisCard({ label, ...over })], summary: { n: 1, value: 0, disagree: 0 } });
  // Resolve B first, then the superseded A late.
  await resolveRacePath(cdp, "group=usopen", { kind: "ok", payload: board("Board usopen"), fetchedAt: 111 });
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Board usopen')");
  await resolveRacePath(cdp, "group=tennis", { kind: "ok", payload: board("Board tennis"), fetchedAt: 222 });
  await sleep(200);
  const text = await mainText();
  assert.ok(text.includes("Board usopen"), "newest group response wins");
  assert.doesNotMatch(text, /Board tennis/, "superseded group response never overwrites");
  await screenshot(cdp, R2_SHOTS, "r2-tennis-group-race.png");
});

test("r2_tennis_detail_race_abort_ignoring", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Players");
  await waitForExpression(cdp, "document.querySelectorAll('.linklike').length > 0");
  await holdPatterns(cdp, ["/api/players/"]);
  await evalInPage(cdp, "document.querySelectorAll('.linklike')[0].click()"); // A
  await sleep(30);
  await evalInPage(cdp, "document.querySelectorAll('.linklike')[1].click()"); // B
  await sleep(30);
  const detail = (name) => ({ name, unknown: false, matches: 200, overall: 2100, cold_start: false, surfaces: { hard: { rating: 2000, n: 100 }, clay: { rating: 1900, n: 50 }, grass: { rating: 1800, n: 20 } }, form_last10: { n: 10, win_rate: 0.7 }, surface_splits: {}, recent: [], form_trend: [] });
  const detailName = () => evalInPage(cdp, "Array.from(document.querySelectorAll('main .p')).map((e) => e.textContent).join('|')");
  await resolveRacePath(cdp, "Jannik", { kind: "ok", payload: detail("Jannik Sinner"), fetchedAt: 111 });
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Jannik Sinner')");
  await resolveRacePath(cdp, "Carlos", { kind: "ok", payload: detail("Carlos Alcaraz"), fetchedAt: 222 });
  await sleep(200);
  const shown = await detailName();
  assert.ok(shown.includes("Jannik Sinner"), "newest player detail wins");
  assert.ok(!shown.includes("Carlos Alcaraz"), "superseded player detail never overwrites the detail view");
});

test("r2_tennis_search_race_abort_ignoring", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Players");
  await waitForExpression(cdp, "document.querySelector('#player-query') !== null");
  await holdPatterns(cdp, ["/api/players?"]);
  await evalInPage(cdp, "(() => { const i=document.querySelector('#player-query'); i.value='carl'; i.dispatchEvent(new Event('input',{bubbles:true})); return true; })()");
  await sleep(330); // debounce fires query A on the default (hard) surface
  await evalInPage(cdp, "(() => { const s=document.querySelector('#player-surface'); s.value='clay'; s.dispatchEvent(new Event('change',{bubbles:true})); return true; })()");
  await sleep(40);  // query B on clay becomes the newest request
  const players = (name, surface) => ({ surface, players: [{ name, matches: 50, overall: 1500, hard: 1500, clay: 1500, grass: 1500, blended: 1500, cold_start: false }] });
  await resolveRacePath(cdp, "surface=clay", { kind: "ok", payload: players("Player B-clay", "clay"), fetchedAt: 111 });
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Player B-clay')");
  await resolveRacePath(cdp, "surface=hard", { kind: "ok", payload: players("Player A-hard", "hard"), fetchedAt: 222 });
  await sleep(200);
  const text = await mainText();
  assert.ok(text.includes("Player B-clay"), "newest search response wins");
  assert.ok(!text.includes("Player A-hard"), "a superseded search response never overwrites");
  assert.doesNotMatch(text, /Refreshing/, "no stale in-flight indicator is left behind");
  await screenshot(cdp, R2_SHOTS, "r2-tennis-search-race.png");
});

test("r2_tennis_prediction_race_abort_ignoring", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Matches");
  await waitForExpression(cdp, "document.querySelector('#predict-input') !== null");
  await holdPatterns(cdp, ["/api/predict"]);
  await loadPredictDetail("evA");
  await sleep(30);
  await loadPredictDetail("evB");
  await sleep(30);
  await resolveRacePath(cdp, "event_id=evB", { kind: "ok", payload: predictDetailPayload("evB"), fetchedAt: 111 });
  await waitForExpression(cdp, "document.querySelectorAll('main .predict-detail-ready').length === 1");
  await resolveRacePath(cdp, "event_id=evA", { kind: "ok", payload: predictDetailPayload("evA", { event: { id: "evA", label: "Superseded A vs B", commence_time: "2030-01-01T18:00:00Z" } }), fetchedAt: 222 });
  await sleep(200);
  const text = await mainText();
  assert.match(text, /Player A vs Player B/, "newest prediction detail wins");
  assert.doesNotMatch(text, /Superseded A vs B/, "superseded prediction detail never overwrites");
});

test("r2_tennis_prediction_late_error_cannot_overwrite", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Matches");
  await waitForExpression(cdp, "document.querySelector('#predict-input') !== null");
  await holdPatterns(cdp, ["/api/predict"]);
  await loadPredictDetail("evA");
  await sleep(30);
  await loadPredictDetail("evB");
  await sleep(30);
  await resolveRacePath(cdp, "event_id=evB", { kind: "ok", payload: predictDetailPayload("evB"), fetchedAt: 111 });
  await waitForExpression(cdp, "document.querySelectorAll('main .predict-detail-ready').length === 1");
  await resolveRacePath(cdp, "event_id=evA", { kind: "http", message: "late failure" });
  await sleep(200);
  const text = await mainText();
  assert.match(text, /Player A vs Player B/, "newest success survives a late superseded HTTP error");
  assert.doesNotMatch(text, /Server returned an error/, "a late superseded error cannot overwrite");
});

test("r2_tennis_prediction_empty_then_late_success_holds", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Matches");
  await waitForExpression(cdp, "document.querySelector('#predict-input') !== null");
  await holdPatterns(cdp, ["/api/predict"]);
  await loadPredictDetail("evA");
  await sleep(30);
  await loadPredictDetail("evB");
  await sleep(30);
  await resolveRacePath(cdp, "event_id=evB", { kind: "ok", payload: { error: "unknown event", event: { id: "evB" } }, fetchedAt: 111 });
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Not a valid prediction')");
  await resolveRacePath(cdp, "event_id=evA", { kind: "ok", payload: predictDetailPayload("evA"), fetchedAt: 222 });
  await sleep(200);
  const text = await mainText();
  assert.match(text, /Not a valid prediction/, "the newest (invalid) outcome stands");
  assert.equal(await count("main .predict-detail-ready"), 0, "a late success cannot resurrect a superseded request");
});

// ===========================================================================
// Court Evidence repair r3 — independent review findings 1 and 2.
//
// Finding 1: the on-demand tennis prediction detail must obey the SAME health
// containment policy as the board (via tennis-contract classifyFeed). A
// contradictory provider mode withholds it, and a previously rendered detail is
// revoked when a health refresh fails.
// Finding 2: a genuinely blank (or whitespace-only) event submission must
// supersede the in-flight request BEFORE the validation early-return, so a late
// success AND a late error can never resurrect it.
// ===========================================================================

const DETAIL_LABEL = "DETAIL-ONLY EVENT LABEL";

function detailResponder(id) {
  return predictDetailPayload(id, { event: { id, label: DETAIL_LABEL, commence_time: "2030-01-01T18:00:00Z" } });
}

test("r3_tennis_predict_detail_health_containment_and_revoke", async () => {
  // (a) a contradictory provider mode contains the detail from the very start
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.health = { ok: true, has_key: true, auth: false, provider: { mode: "mock" }, quota: { remaining: 100 }, groups: TENNIS_GROUPS };
  tennisFixture.state.predictResponder = detailResponder;
  await openApp(cdp, tennisOrigin, "?case=r3_predict_health_mock");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelector('main').textContent.includes('Unverified feed — probabilities withheld')");
  assert.equal(await count("main .predict-detail-ready"), 0, "a contradictory provider mode withholds the prediction detail");
  assert.doesNotMatch(await mainText(), new RegExp(DETAIL_LABEL), "the withheld detail never renders its event");

  // (b) a detail rendered while healthy is revoked when a health refresh fails
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.predictResponder = detailResponder;
  await openApp(cdp, tennisOrigin, "?case=r3_predict_health_revoke");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelectorAll('main .predict-detail-ready').length === 1");
  assert.ok((await mainText()).includes(DETAIL_LABEL), "a healthy feed renders the detail");
  tennisFixture.state.failResource = new Set(["health"]);
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await waitFor("document.querySelector('main').textContent.includes('Unverified feed — probabilities withheld')");
  assert.equal(await count("main .predict-detail-ready"), 0, "a failed health refresh revokes the previously rendered detail");
  assert.doesNotMatch(await mainText(), new RegExp(DETAIL_LABEL), "the revoked detail is no longer rendered");
  tennisFixture.state.failResource = new Set();
});

test("r3_tennis_prediction_blank_and_whitespace_supersede_late_outcomes", async () => {
  tennisFixture.reset();
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Matches");
  await waitForExpression(cdp, "document.querySelector('#predict-input') !== null");
  await holdPatterns(cdp, ["/api/predict"]);

  // A genuinely blank submission supersedes the in-flight request; a late
  // success must not resurrect it.
  await loadPredictDetail("evA");
  await sleep(30);
  await loadPredictDetail("");
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Enter an event ID')");
  assert.equal(await count("main .predict-detail-ready"), 0, "a blank submission shows no detail");
  await resolveRacePath(cdp, "event_id=evA", { kind: "ok", payload: predictDetailPayload("evA", { event: { id: "evA", label: "RESURRECTED-A", commence_time: "2030-01-01T18:00:00Z" } }), fetchedAt: 222 });
  await sleep(200);
  let text = await mainText();
  assert.match(text, /Enter an event ID/, "the blank error stands after a late success");
  assert.doesNotMatch(text, /RESURRECTED-A/, "a late success cannot resurrect a blank submission");
  assert.equal(await count("main .predict-detail-ready"), 0, "no detail appears after a late success");

  // A whitespace-only submission (trimmed to blank) supersedes too; a late
  // error must not resurrect it.
  await loadPredictDetail("evB");
  await sleep(30);
  await loadPredictDetail("   ");
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Enter an event ID')");
  await resolveRacePath(cdp, "event_id=evB", { kind: "http", message: "late failure" });
  await sleep(200);
  text = await mainText();
  assert.match(text, /Enter an event ID/, "the whitespace-blank error stands after a late error");
  assert.doesNotMatch(text, /Server returned an error/, "a late error cannot resurrect a blank submission");
  assert.equal(await count("main .predict-detail-ready"), 0, "no detail appears after a late error");
});

// ===========================================================================
// Court Evidence repair r5 — independent review findings.
//
// Finding 3: contradictory/missing/mock tennis health must PERMANENTLY retire a
// retained detail AND supersede any pending detail lookup via request-state
// cancellation, so a later healthy refresh cannot resurrect an old lookup. Only
// an explicit new lookup may establish fresh detail. Valid real healthy controls
// must still render.
//
// Finding 5: an invalid calendar date in player detail must render an explicit
// missing marker (—), never the literal string "null".
//
// All fixtures are test-only (x-court-test-fixture / the test-only /race page);
// never ASGI integration and never product data.
// ===========================================================================

test("r5_tennis_health_retirement_is_permanent_until_new_lookup", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  tennisFixture.state.predictResponder = detailResponder;
  await openApp(cdp, tennisOrigin, "?case=r5_health_retire");
  await clickTab(cdp, "Matches");
  await loadPredictDetail("ev1");
  await waitFor("document.querySelectorAll('main .predict-detail-ready').length === 1");
  assert.ok((await mainText()).includes(DETAIL_LABEL), "a healthy feed renders the detail");

  // Health turns contradictory (ok:false): the retained detail is retired at once.
  tennisFixture.state.health = { ok: false, has_key: true, provider: { mode: "live" } };
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await waitFor("document.querySelector('main').textContent.includes('Unverified feed — probabilities withheld')");
  assert.equal(await count("main .predict-detail-ready"), 0, "contradictory health retires the retained detail");

  // Health recovers: the old detail must NOT resurrect without a new lookup.
  tennisFixture.state.health = { ok: true, has_key: true, provider: { mode: "live" } };
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await sleep(200);
  assert.equal(await count("main .predict-detail-ready"), 0, "a later healthy refresh cannot resurrect a retired detail");
  assert.doesNotMatch(await mainText(), new RegExp(DETAIL_LABEL), "the retired event label stays gone");

  // A provider-mock health also retires, and a mock health keeps a new lookup contained.
  tennisFixture.state.health = { ok: true, has_key: true, provider: { mode: "mock" } };
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await sleep(150);
  assert.equal(await count("main .predict-detail-ready"), 0, "mock health keeps the detail retired");

  // Only an explicit new lookup may re-establish detail, and only while healthy.
  tennisFixture.state.health = { ok: true, has_key: true, provider: { mode: "live" } };
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await sleep(150);
  await loadPredictDetail("ev1");
  await waitFor("document.querySelectorAll('main .predict-detail-ready').length === 1");
  assert.ok((await mainText()).includes(DETAIL_LABEL), "an explicit new lookup re-establishes detail while healthy");
});

test("r5_tennis_health_retirement_supersedes_pending_lookup", async () => {
  tennisFixture.reset();
  tennisFixture.state.feed = "live";
  await openApp(cdp, tennisOrigin, "race");
  await clickTab(cdp, "Matches");
  await waitForExpression(cdp, "document.querySelector('#predict-input') !== null");
  await holdPatterns(cdp, ["/api/predict"]);
  await loadPredictDetail("evP");
  await sleep(30);

  // A contradictory health refresh arrives while the detail is still in flight.
  tennisFixture.state.health = { ok: false, has_key: true, provider: { mode: "live" } };
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await waitForExpression(cdp, "document.querySelector('main').textContent.includes('Unverified feed — probabilities withheld')");

  // Health recovers BEFORE the held lookup resolves. If the pending lookup were
  // not superseded, the late success would now pass containment and render.
  tennisFixture.state.health = { ok: true, has_key: true, provider: { mode: "live" } };
  await evalInPage(cdp, "window.__COURT_APP__.refresh()");
  await sleep(150);

  // The held detail resolves successfully, but its generation was superseded.
  await resolveRacePath(cdp, "event_id=evP", {
    kind: "ok",
    payload: predictDetailPayload("evP", { event: { id: "evP", label: "PENDING-RESURRECTION", commence_time: "2030-01-01T18:00:00Z" } }),
    fetchedAt: 333,
  });
  await sleep(250);
  assert.equal(await count("main .predict-detail-ready"), 0, "a superseded pending lookup cannot render after health retirement");
  assert.doesNotMatch(await mainText(), /PENDING-RESURRECTION/, "the late held success cannot resurrect the retired lookup");
});

test("r5_tennis_invalid_calendar_date_renders_dash_not_null", async () => {
  tennisFixture.reset();
  tennisFixture.state.playerDetailResponder = (name) => ({
    name, unknown: false, matches: 100, overall: 2000, cold_start: false,
    surfaces: { hard: { rating: 2000, n: 50 }, clay: { rating: 1900, n: 30 }, grass: { rating: 1800, n: 20 } },
    form_last10: { n: 10, win_rate: 0.7 },
    surface_splits: {},
    recent: [
      { date: 20260230, surface: "hard", round: "R16", opponent: "X", result: "W" },
      { date: 20240229, surface: "clay", round: "QF", opponent: "Y", result: "L" },
    ],
    form_trend: [],
  });
  await openApp(cdp, tennisOrigin, "?case=r5_date");
  await clickTab(cdp, "Players");
  await waitFor("document.querySelector('.linklike')");
  await evalInPage(cdp, "document.querySelector('.linklike').click()");
  await waitFor("document.querySelector('main').textContent.includes('Recent matches')");
  const firstDate = await evalInPage(cdp, `(() => {
    const t = [...document.querySelectorAll('main table')].find((x) => [...x.querySelectorAll('thead th')].some((th) => th.textContent === 'date'));
    return t ? t.querySelector('tbody tr td').textContent : null;
  })()`);
  assert.equal(firstDate, "—", "an invalid calendar date renders an explicit missing marker");
  assert.ok((await mainText()).includes("20240229"), "a valid calendar date is preserved exactly");
  assert.doesNotMatch(await mainText(), /\bnull\b/, "no invalid date ever renders as the literal 'null'");
});

// Court Evidence — tennis application (ES module, browser only).
//
// mountTennisApp({root, adapter, client, now}) -> { dispose(), refresh(), whenIdle }
//
// Owns every resource's independent request slot. It never writes to
// localStorage, never POSTs, and never presents a mock/unverified feed as a
// live prediction. Group changes abort the prior board request; player search
// is debounced and generation-checked. Surface selection belongs to the player
// ratings view only: match modelling is hard-court only in the backend.

import { createReadClient } from "./transport.js";
import { createRequestSlot } from "./request-state.js";
import * as TC from "./tennis-contract.js";

const TABS = ["Matches", "Players", "Model", "Experiments", "Methodology"];
const GROUPS = [
  { key: "usopen", label: "US Open · best-of-5" },
  { key: "tennis", label: "ATP Tour · best-of-3" },
];
const SURFACES = ["hard", "clay", "grass"];
const REQUIRED_API = ["health", "board", "model", "stats", "players", "predict", "reliability", "player_detail"];
const RESOURCE_KEYS = ["health", "board", "model", "stats", "players", "reliability"];
const SEARCH_DEBOUNCE_MS = 250;
const CONTRADICTORY_MODES = ["mock", "init", "error", "unknown"];

export function mountTennisApp({ root, adapter, client, now = () => Date.now() } = {}) {
  const readClient = client || createReadClient({ now });
  const config = validateAdapter(adapter);
  const state = {
    tab: config.ok ? (config.tabs[0] || "Matches") : "Matches",
    group: "usopen",
    demo: false,
    matchesFilter: "all",
    playerQuery: "",
    playerSurface: "hard",
    playerStatus: "idle",
    playerError: null,
    playerDetail: null,
    detailName: null,
    prediction: { status: "idle", eventId: null, result: null, error: null },
  };
  const resources = {};
  for (const key of RESOURCE_KEYS) resources[key] = mkResource();
  const detailSlot = createRequestSlot();
  // The prediction detail is an explicit, opt-in lookup: it owns its own slot so
  // a slow earlier event can never overwrite a newer one, and it never replaces
  // the board (board cards stay the primary prediction view).
  const predictionSlot = createRequestSlot();
  // Live readiness is only as good as the LAST health read. A failed health
  // refresh revokes it so an old has_key=true can never keep licensing live.
  let healthTrusted = false;
  let searchTimer = null;
  let disposed = false;
  let idleResolve;
  const idle = new Promise((res) => { idleResolve = res; });
  let mainEl;
  let railEl;
  let statusEl;

  const VIEWS = {
    Matches: matchesView,
    Players: playersView,
    Model: modelView,
    Experiments: experimentsView,
    Methodology: methodologyView,
  };

  if (!config.ok) {
    renderConfigurationUnavailable(config.reason);
    idleResolve();
    return { dispose, refresh: () => {}, whenIdle: () => idle };
  }

  const PARSE = {
    health: (p) => (isObj(p) ? { ...p, kind: "ready" } : { kind: "invalid", reason: "health payload missing" }),
    model: (p) => TC.parseModel(p),
    stats: (p) => (isObj(p) ? { ...p, kind: "ready" } : { kind: "invalid", reason: "stats payload missing" }),
    reliability: (p) => TC.parseReliability(p),
    players: (p) => TC.parsePlayers(p),
    board: (p, fetchedAt) => TC.parseBoard(p, { fetchedAt, health: healthTrusted ? resources.health.data : null, now: now() }),
  };

  buildShell();
  queueMicrotask(() => { void loadAll(); });
  return { dispose, refresh: loadAll, whenIdle: () => idle };

  // ---- lifecycle ----------------------------------------------------------

  async function loadAll() {
    // Health first: the board's feed containment depends on the credential
    // report, so the board must not be classified before health resolves.
    await load("health");
    await Promise.all(RESOURCE_KEYS.filter((k) => k !== "health").map((k) => load(k)));
    if (root && root.dataset) root.dataset.courtReady = "1";
    idleResolve();
  }

  function apiPath(key, extra = "") {
    return config.api[key] + (extra || "");
  }

  async function load(key) {
    const r = resources[key];
    if (!r || disposed) return;
    const { generation, signal } = r.slot.begin();
    const had = r.status === "ready" || r.status === "empty";
    r.refreshing = had;
    r.refreshFailed = false;
    if (!had) r.status = "loading";
    render();

    const path = key === "board"
      ? apiPath("board", `?group=${encodeURIComponent(state.group)}`)
      : key === "players"
        ? `${config.api.players}?q=${encodeURIComponent(state.playerQuery)}&n=30&surface=${encodeURIComponent(state.playerSurface)}`
        : config.api[key];
    const res = await readClient.getJson(path, { signal });
    if (disposed || !r.slot.isCurrent(generation)) return; // superseded: touch nothing

    if (res.kind === "ok") {
      const parsed = PARSE[key](res.payload, res.fetchedAt);
      if (parsed.kind === "invalid") {
        if (key === "health") setHealthState(null);
        if (had) { r.refreshFailed = true; r.refreshError = parsed.reason || "invalid response"; }
        else { r.status = "invalid"; r.error = parsed.reason || "invalid response"; announce(`Invalid response for ${key}`); }
      } else {
        r.status = parsed.kind === "empty" ? "empty" : "ready";
        r.data = parsed;
        r.fetchedAt = res.fetchedAt;
        r.error = null;
        r.refreshFailed = false;
        if (key === "health") setHealthState(parsed);
      }
    } else if (res.kind === "abort") {
      r.refreshing = false;
      render();
      return;
    } else {
      if (key === "health") setHealthState(null);
      if (had) { r.refreshFailed = true; r.refreshError = res.message; }
      else { r.status = res.kind; r.error = res.message; }
      announce(`Could not load ${key}: ${res.message}`);
    }
    r.refreshing = false;
    render();
  }

  function dispose() {
    disposed = true;
    if (searchTimer) clearTimeout(searchTimer);
    for (const r of Object.values(resources)) r.slot.cancel();
    detailSlot.cancel();
    predictionSlot.cancel();
    if (root) root.textContent = "";
    idleResolve();
  }

  // ---- shell --------------------------------------------------------------

  function buildShell() {
    root.textContent = "";
    const header = el("header", { cls: "site-header" });
    const brand = el("div", { cls: "brand" });
    brand.appendChild(text("Court Evidence"));
    brand.appendChild(el("span", { cls: "sport", text: " — ATP tennis" }));
    header.appendChild(brand);

    const nav = el("nav", { cls: "sport-nav", attrs: { "aria-label": "Evidence sections" } });
    for (const tab of config.tabs) {
      const b = el("button", { text: tab, attrs: { type: "button", "aria-current": "false" } });
      b.addEventListener("click", () => { state.tab = tab; render(); });
      nav.appendChild(b);
    }
    header.appendChild(nav);
    root.appendChild(header);

    const layout = el("div", { cls: "layout" });
    mainEl = el("main", { attrs: { id: "main", tabindex: "-1" } });
    railEl = el("aside", { cls: "rail", attrs: { "aria-label": "Provenance" } });
    layout.appendChild(mainEl);
    layout.appendChild(railEl);
    root.appendChild(layout);

    statusEl = el("div", { cls: "visually-hidden", attrs: { role: "status", "aria-live": "polite" } });
    root.appendChild(statusEl);
  }

  function announce(msg) {
    if (statusEl) statusEl.textContent = msg;
  }

  // ---- render -------------------------------------------------------------

  function render() {
    if (disposed || !mainEl) return;
    const activeEl = document.activeElement;
    const focusKey = activeEl && activeEl.getAttribute ? activeEl.getAttribute("data-focus-key") : null;

    const nav = root.querySelector(".sport-nav");
    if (nav) {
      [...nav.children].forEach((b) => {
        const on = b.textContent === state.tab;
        b.setAttribute("aria-current", on ? "page" : "false");
      });
    }

    mainEl.textContent = "";
    const view = VIEWS[state.tab] || VIEWS.Matches;
    mainEl.appendChild(view());

    railEl.textContent = "";
    railEl.appendChild(railCard());

    if (focusKey) {
      const next = mainEl.querySelector(`[data-focus-key="${focusKey}"]`);
      if (next) next.focus();
    }
  }

  // ---- helper for the injected static disclosure --------------------------

  function disclosureNode() {
    const tpl = document.getElementById("court-disclosure");
    if (tpl && tpl.content) {
      const frag = document.createDocumentFragment();
      frag.appendChild(tpl.content.cloneNode(true));
      return frag;
    }
    return el("p", { cls: "note", text: "Historical paper-simulation disclosure unavailable." });
  }

  // ---- Matches ------------------------------------------------------------

  function matchesView() {
    const frag = document.createDocumentFragment();
    frag.appendChild(boardCard());
    frag.appendChild(predictionCard());
    frag.appendChild(demoCard());
    return frag;
  }

  function boardCard() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "board-h" } });
    card.appendChild(el("h2", { text: "Matches", attrs: { id: "board-h" } }));
    card.appendChild(el("p", { cls: "note", text:
      "Model vs market for upcoming matches. Match modelling is hard-court only in the backend " +
      "(surface selection applies to player ratings, not to this board). Group feed modes and cache age " +
      "do NOT establish per-event freshness." }));

    // group selector (match group, not surface)
    const gbar = el("div", { cls: "filters", attrs: { role: "group", "aria-label": "Match group" } });
    for (const g of GROUPS) {
      const b = el("button", { text: g.label, attrs: { type: "button", "aria-pressed": String(state.group === g.key) } });
      b.addEventListener("click", () => { if (state.group !== g.key) { state.group = g.key; void load("board"); } });
      gbar.appendChild(b);
    }
    card.appendChild(gbar);

    const r = resources.board;
    const health = resources.health.data;

    if (r.status === "loading") { card.appendChild(el("p", { cls: "muted", text: "Loading matches…" })); return card; }
    if (r.status === "invalid") {
      card.appendChild(statusBanner("Invalid response", r.error, "error"));
      card.appendChild(retryButton("board"));
      return card;
    }
    if (r.status !== "ready" && r.status !== "empty") {
      card.appendChild(statusBanner(statusTitle(r.status), r.error, "error"));
      card.appendChild(retryButton("board"));
      return card;
    }

    if (r.refreshFailed) card.appendChild(statusBanner("Refresh failed — showing last successful response", r.refreshError, "warn"));
    card.appendChild(feedRow(r));
    card.appendChild(refreshRow("board", r));

    const board = r.data;
    if (!board) { card.appendChild(el("p", { cls: "muted", text: "No board data." })); return card; }

    // feed containment banners
    if (board.kind === "mock") {
      card.appendChild(statusBanner("Mock mode — live predictions suppressed", "The provider returned demo fixtures (or no credential). These are not real predictions and are not shown.", "error"));
    } else if (board.kind === "unverified") {
      card.appendChild(statusBanner("Unverified feed — probabilities withheld", "The feed provenance is unknown or inconsistent with credentials. Rows are listed without model probabilities.", "warn"));
    }
    if (board.stale) {
      card.appendChild(statusBanner("Stale group cache", `Server-reported group cache age ${board.cacheAgeS}s (> 60s). Not actionable.`, "warn"));
    }
    if (board.provenance.cached) {
      card.appendChild(el("p", { cls: "note", text: "Cached evidence — event freshness is unknown, never an actionable view." }));
    }
    if (board.rejected > 0) {
      const wrap = el("p", { cls: "note" });
      wrap.appendChild(el("span", { cls: "badge quarantine", text: `${board.rejected} quarantined row${board.rejected === 1 ? "" : "s"}` }));
      wrap.appendChild(text(" — malformed cards are shown here, never normalised into predictions."));
      card.appendChild(wrap);
    }

    // Mock is blocked from live presentation entirely: no rows, no probabilities.
    if (board.kind === "mock") {
      card.appendChild(el("p", { cls: "muted", text: "Live board suppressed in mock mode. Use the demo fixtures toggle below for layout review only." }));
      card.appendChild(el("p", { cls: "provenance", text: `Fetched at ${fmtTs(r.fetchedAt)}` }));
      return card;
    }

    const suppress = board.kind !== "ready";
    const filter = filterRow(board.rows);
    card.appendChild(filter.bar);
    const shown = board.rows.filter((row) => matchesFilter(row, state.matchesFilter));
    if (shown.length === 0) {
      card.appendChild(el("p", { cls: "muted", text: `No ${filterLabel(state.matchesFilter)} matches. An empty board is never a reason to invent fixtures.` }));
      return card;
    }
    card.appendChild(matchList(shown, suppress));
    card.appendChild(el("p", { cls: "provenance", text:
      `Fetched at ${fmtTs(r.fetchedAt)} · ${board.rows.length} validated · ${board.errors.length} server error card(s)` }));
    return card;
  }

  function feedRow(r) {
    const board = r.data;
    const row = el("div", { cls: "meta" });
    if (board) {
      const mode = board.provenance.feedMode;
      // A group feed mode is a provider-wide report, never per-event freshness:
      // label it honestly and never colour it as a green "pass".
      const label = mode === "live" ? "Reported group feed: live" : mode === "cached" ? "Cached feed" : mode === "mock" ? "Mock — suppressed" : "Unverified feed";
      row.appendChild(el("span", { cls: `badge ${mode === "mock" ? "fail" : "unknown"}`, text: label }));
      if (board.cacheAgeS == null) row.appendChild(el("span", { cls: "note", text: "Group cache age: unknown" }));
      else row.appendChild(el("span", { cls: `note`, text: `Group cache age: ${board.cacheAgeS}s${board.stale ? " (stale)" : ""}` }));
      if (board.provenance.hasKey === false) row.appendChild(el("span", { cls: "note", text: "No provider credential reported" }));
    }
    return row;
  }

  function filterRow(rows) {
    const bar = el("div", { cls: "filters", attrs: { role: "group", "aria-label": "Filter matches" } });
    const opts = [["all", "All"], ["upcoming", "Upcoming"], ["started", "Started"], ["thin", "Thin market"]];
    for (const [value, label] of opts) {
      const count = rows.filter((row) => matchesFilter(row, value)).length;
      const b = el("button", { text: `${label} (${count})`, attrs: { type: "button", "aria-pressed": String(state.matchesFilter === value) } });
      b.addEventListener("click", () => { state.matchesFilter = value; render(); });
      bar.appendChild(b);
    }
    return { bar };
  }

  function matchList(rows, suppress) {
    const ul = el("ul", { cls: "row-cards" });
    for (const row of rows) ul.appendChild(matchCard(row, suppress));
    return ul;
  }

  function matchCard(row, suppress) {
    const li = el("li", { cls: "row-card" });
    const top = el("div", { cls: "meta" });
    top.appendChild(el("span", { cls: "badge", text: `Pick ${row.pick}` }));
    top.appendChild(el("span", { text: row.label }));
    top.appendChild(el("span", { text: fmtCommence(row.commence_time) }));
    li.appendChild(top);

    const outcomes = Object.keys(row.outcomes);
    const other = outcomes.find((o) => o !== row.pick);
    const x = row.outcomes[row.pick] || {};
    if (suppress) {
      const withheld = el("div", { cls: "meta" });
      withheld.appendChild(el("span", { text: "Probabilities withheld — feed not verified as live." }));
      li.appendChild(withheld);
      return li;
    }
    li.appendChild(el("div", { cls: "p", text: `${fmt(row.conf, 3)} on ${row.pick}` }));
    const series = el("div", { cls: "series" });
    series.appendChild(seriesRow("Model (full)", x.model_prob));
    series.appendChild(seriesRow("Model (market-free)", x.model_prob_free));
    series.appendChild(seriesRow("Market", x.fair_prob));
    li.appendChild(series);
    const meta = el("div", { cls: "meta" });
    meta.appendChild(el("span", { text: `${row.n_books} books` }));
    if (x.best_odds != null) meta.appendChild(el("span", { text: `best ${x.best_odds} ${x.best_book || ""}` }));
    if (row.fairSource) meta.appendChild(el("span", { text: `fair: ${row.fairSource}` }));
    li.appendChild(meta);
    if (row.unknownPlayers.length) {
      li.appendChild(el("div", { cls: "note", text: `Low history: ${row.unknownPlayers.join(", ")} — treat with caution` }));
    }
    if (row.warning) li.appendChild(el("div", { cls: "note", text: row.warning }));
    if (row.reasons.length) {
      const det = el("details");
      det.appendChild(el("summary", { text: "Reasons" }));
      const list = el("ul", { cls: "note" });
      for (const reason of row.reasons) list.appendChild(el("li", { text: reason }));
      det.appendChild(list);
      li.appendChild(det);
    }
    if (other) {
      const opp = el("div", { cls: "meta" });
      opp.appendChild(el("span", { text: `Opponent ${other}` }));
      li.appendChild(opp);
    }
    return li;
  }

  function seriesRow(label, value) {
    const r = el("div", { cls: "series-row" });
    r.appendChild(el("span", { cls: "series-label", text: label }));
    r.appendChild(el("span", { cls: "series-value", text: value == null ? "—" : fmt(value, 3) }));
    return r;
  }

  function demoCard() {
    const card = el("section", { cls: "card" });
    card.appendChild(el("h2", { text: "Demo fixtures (opt-in)" }));
    card.appendChild(el("p", { cls: "note", text:
      "Separately labelled demo rows for layout review only. They are never counted as live, never enter evidence totals, and never appear in the board above." }));
    const btn = el("button", { cls: "action", text: state.demo ? "Hide demo fixtures" : "Show demo fixtures", attrs: { type: "button", "aria-pressed": String(state.demo), "data-focus-key": "demo-toggle" } });
    btn.addEventListener("click", () => { state.demo = !state.demo; render(); });
    card.appendChild(btn);
    if (state.demo) {
      const demo = TC.buildDemoBoard();
      card.appendChild(el("span", { cls: "badge unknown", text: "DEMO — not live" }));
      card.appendChild(matchList(demo.rows, false));
    }
    return card;
  }

  // ---- Prediction detail (opt-in, never primary) --------------------------
  //
  // The board cards above are the primary prediction view; this looks up a
  // single event's full /api/predict verdict on explicit user request. The
  // returned event ID must match the request (the backend falls back to the
  // first event), and every present probability/confidence field is validated
  // by parsePrediction — a malformed same-ID or contradictory mock/unverified
  // feed can never render as a ready prediction.

  function predictionCard() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "predict-h" } });
    card.appendChild(el("h2", { text: "Event prediction detail", attrs: { id: "predict-h" } }));
    card.appendChild(el("p", { cls: "note", text:
      "Board cards remain the primary prediction view. Load one event's full verdict on demand; " +
      "a returned ID must match the request, and no model or market probability is invented or clamped. " +
      "The endpoint policy/tweet recommendation is never shown." }));

    const form = el("form", { cls: "form-inline" });
    const label = el("label", { text: "Event ID" });
    const input = el("input", { attrs: { type: "text", name: "event_id", id: "predict-input", autocomplete: "off", placeholder: "e.g. ev1" } });
    input.dataset.focusKey = "predict-input";
    input.value = state.prediction.eventId || "";
    label.appendChild(input);
    const btn = el("button", { cls: "action", text: "Load detail", attrs: { type: "submit", "data-focus-key": "predict-submit" } });
    form.appendChild(label);
    form.appendChild(btn);
    form.addEventListener("submit", (e) => { e.preventDefault(); void loadPrediction(input.value.trim()); });
    card.appendChild(form);

    const p = state.prediction;
    if (p.status === "loading") { card.appendChild(el("p", { cls: "muted", text: "Loading prediction…" })); return card; }
    if (p.status === "invalid") { card.appendChild(statusBanner("Not a valid prediction", p.error, "error")); return card; }
    if (p.status === "withheld") { card.appendChild(withheldDetailBanner()); return card; }
    if (p.status === "ready" && p.result) {
      // Render-time containment: if health has since been revoked (e.g. a failed
      // refresh), the previously rendered detail is withheld rather than shown.
      if (predictionFeedContained(p.result.feed)) { card.appendChild(withheldDetailBanner()); return card; }
      card.appendChild(predictionResult(p.result));
      return card;
    }
    if (p.status === "offline" || p.status === "timeout" || p.status === "http") {
      card.appendChild(statusBanner(statusTitle(p.status), p.error, "error"));
      return card;
    }
    card.appendChild(el("p", { cls: "note", text: "No event detail requested." }));
    return card;
  }

  function withheldDetailBanner() {
    return statusBanner("Unverified feed — probabilities withheld", "The feed provenance is unknown or inconsistent with credentials. The prediction detail is withheld.", "warn");
  }

  function predictionResult(result) {
    const box = el("div", { cls: "predict-detail-ready" });
    box.appendChild(el("p", { text: result.event.label }));
    const dl = el("dl", { cls: "provenance" });
    addPair(dl, "Event ID", result.event.id);
    addPair(dl, "Commence (source ISO)", result.event.commence_time);
    addPair(dl, "Pick", result.verdict.pick);
    addPair(dl, "Model (pick)", fmt(result.verdict.model_prob_pick, 4));
    addPair(dl, "Confidence", result.verdict.conf == null ? "—" : fmt(result.verdict.conf, 4));
    addPair(dl, "Feed", result.feed);
    if (result.fairSource) addPair(dl, "Fair source", result.fairSource);
    box.appendChild(dl);
    box.appendChild(tableFrom(
      ["outcome", "model (full)", "model (market-free)", "market"],
      Object.keys(result.market).map((k) => {
        const o = result.market[k];
        return [k, fmt(o.model_prob, 4), fmt(o.model_prob_free, 4), o.fair_prob == null ? "—" : fmt(o.fair_prob, 4)];
      }),
      "Single-event verdict. Market fairness reads — when the endpoint omits fair_prob; nothing is inferred.",
    ));
    if (result.warning) box.appendChild(el("p", { cls: "note", text: result.warning }));
    box.appendChild(el("p", { cls: "note", text: "On-demand lookup only; the board above is unchanged." }));
    return box;
  }

  // The on-demand detail obeys the SAME health containment policy as the board
  // (tennis-contract classifyFeed): only a verified live/cache feed with a
  // consistent health report may render.
  function predictionFeedContained(feed) {
    const health = healthTrusted ? resources.health.data : null;
    const feedState = TC.classifyFeed(feed, health);
    return !(feedState.live || feedState.cached);
  }

  // Health that is missing, lacks an explicit ok===true, or reports a
  // contradictory provider mode cannot license a live detail. When such a report
  // arrives, permanently retire the retained detail AND supersede any in-flight
  // lookup via request-state cancellation, so a later healthy refresh cannot
  // resurrect an old lookup; only an explicit new lookup may establish detail.
  function setHealthState(health) {
    healthTrusted = isObj(health);
    if (healthRetires(health)) retractPredictionDetail();
  }

  function healthRetires(health) {
    if (!isObj(health)) return true;
    if (health.ok !== true) return true;
    const mode = isObj(health.provider) ? health.provider.mode : undefined;
    return typeof mode === "string" && CONTRADICTORY_MODES.includes(mode);
  }

  function retractPredictionDetail() {
    predictionSlot.cancel();
    const p = state.prediction;
    if (p.status === "ready" || p.status === "withheld" || p.status === "loading") {
      p.status = "withheld";
      p.result = null;
      p.error = null;
    }
  }

  async function loadPrediction(id) {
    const p = state.prediction;
    const slot = predictionSlot;
    // Supersede any in-flight detail request BEFORE validating the input: a
    // blank or whitespace-only submission genuinely retires the prior
    // generation, so a late success AND a late error can never resurrect it.
    const { generation, signal } = slot.begin();
    const requested = typeof id === "string" ? id.trim() : "";
    if (!requested) {
      p.status = "invalid";
      p.error = "Enter an event ID to load its prediction.";
      p.eventId = requested;
      p.result = null;
      render();
      return;
    }
    p.status = "loading";
    p.eventId = requested;
    p.result = null;
    p.error = null;
    render();

    const path = `${config.api.predict}?event_id=${encodeURIComponent(requested)}&group=${encodeURIComponent(state.group)}`;
    const res = await readClient.getJson(path, { signal });
    if (disposed || !slot.isCurrent(generation)) return;

    if (res.kind === "ok") {
      const parsed = TC.parsePrediction(res.payload, requested);
      if (parsed.kind === "ready" && predictionFeedContained(parsed.feed)) {
        p.status = "withheld";
        p.result = null;
        p.error = null;
      } else if (parsed.kind === "ready") {
        p.status = "ready";
        p.result = parsed;
      } else {
        p.status = "invalid";
        p.error = parsed.message;
      }
    } else if (res.kind === "abort") {
      return;
    } else {
      p.status = res.kind;
      p.error = res.message;
    }
    render();
  }

  // ---- Players ------------------------------------------------------------

  function playersView() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "players-h" } });
    card.appendChild(el("h2", { text: "Players", attrs: { id: "players-h" } }));
    card.appendChild(el("p", { cls: "note", text:
      "Surface selection applies to player ratings only. Match modelling is hard-court only, so surface does not change the Matches board." }));

    const row = el("div", { cls: "form-inline" });
    const label = el("label", { text: "Search player" });
    const input = el("input", { attrs: { type: "text", id: "player-query", autocomplete: "off", placeholder: "e.g. Alcaraz" } });
    input.value = state.playerQuery;
    input.dataset.focusKey = "player-query";
    input.addEventListener("input", () => {
      state.playerQuery = input.value;
      scheduleSearch();
    });
    label.appendChild(input);
    row.appendChild(label);

    const slabel = el("label", { text: "Rating surface" });
    const sel = el("select", { attrs: { id: "player-surface" } });
    for (const s of SURFACES) {
      const o = el("option", { text: s[0].toUpperCase() + s.slice(1), attrs: { value: s } });
      if (s === state.playerSurface) o.setAttribute("selected", "selected");
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => { state.playerSurface = sel.value; void load("players"); });
    slabel.appendChild(sel);
    row.appendChild(slabel);
    card.appendChild(row);

    const r = resources.players;
    if (r.status === "loading") card.appendChild(el("p", { cls: "muted", text: "Loading players…" }));
    else if (r.status === "invalid") { card.appendChild(statusBanner("Invalid response", r.error, "error")); card.appendChild(retryButton("players")); }
    else if (r.status === "empty") { card.appendChild(el("p", { text: "No players matched." })); }
    else if (r.status === "ready") {
      if (r.refreshFailed) card.appendChild(statusBanner("Refresh failed — showing last successful response", r.refreshError, "warn"));
      const list = r.data.players;
      if (list.length === 0) card.appendChild(el("p", { text: "No players matched." }));
      else {
        card.appendChild(tableFrom(
          ["Player", "Matches", "Overall", "Hard", "Clay", "Grass"],
          list.map((p) => [String(p.name || "—"), fmtInt(p.matches), fmt1(p.overall), fmt1(p.hard), fmt1(p.clay), fmt1(p.grass)]),
          "Blended surface Elo ratings. Surface column selection changes the ranking surface only.",
        ));
        const ul = el("ul", { cls: "row-cards" });
        for (const p of list.slice(0, 30)) {
          const li = el("li", { cls: "row-card" });
          const b = el("button", { cls: "linklike", text: `Show detail: ${p.name || "—"}`, attrs: { type: "button", "data-focus-key": `player-${p.name}` } });
          b.addEventListener("click", () => { state.detailName = p.name; void loadDetail(p.name); });
          li.appendChild(b);
          if (p.coldStart) li.appendChild(el("span", { cls: "badge unknown", text: "Cold-start" }));
          ul.appendChild(li);
        }
        card.appendChild(ul);
      }
      card.appendChild(el("p", { cls: "provenance", text: `Fetched at ${fmtTs(r.fetchedAt)}` }));
    } else {
      card.appendChild(statusBanner(statusTitle(r.status), r.error, "error"));
      card.appendChild(retryButton("players"));
    }

    card.appendChild(playerDetailBody());
    return card;
  }

  function playerDetailBody() {
    const box = el("div");
    box.appendChild(el("h3", { text: "Player detail" }));
    if (state.playerStatus === "idle") { box.appendChild(el("p", { cls: "note", text: "Select a player to view their canonical detail." })); return box; }
    if (state.playerStatus === "loading") { box.appendChild(el("p", { cls: "muted", text: "Loading player detail…" })); return box; }
    if (state.playerStatus === "invalid") { box.appendChild(statusBanner("Not a valid player record", state.playerError, "error")); return box; }
    const d = state.playerDetail;
    if (!d) return box;
    if (d.kind === "unknown") {
      box.appendChild(el("p", { text: `${d.name} — ${d.note}` }));
      return box;
    }
    box.appendChild(el("p", { cls: "p", text: d.name }));
    const dl = el("dl", { cls: "provenance" });
    addPair(dl, "Canonical name", d.name);
    addPair(dl, "Tour matches", fmtInt(d.matches));
    addPair(dl, "Overall Elo", fmt1(d.overall));
    addPair(dl, "Last-10 win rate", d.formLast10 && d.formLast10.winRate != null ? fmt(d.formLast10.winRate, 3) : "—");
    box.appendChild(dl);
    const bars = el("div", { cls: "series" });
    for (const s of SURFACES) {
      const v = d.surfaces[s];
      bars.appendChild(seriesRow(s, v && v.rating != null ? v.rating : null));
    }
    box.appendChild(bars);
    if (d.recent.length) {
      box.appendChild(tableFrom(
        ["date", "surface", "round", "opponent", "result"],
        d.recent.slice(0, 15).map((m) => [m.date == null ? "—" : String(m.date), String(m.surface || "—"), String(m.round || "—"), String(m.opponent || "—"), String(m.result || "—")]),
        "Recent matches. Dates are integer YYYYMMDD, not ISO timestamps.",
      ));
    }
    return box;
  }

  // ---- Model --------------------------------------------------------------

  function modelView() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "model-h" } });
    card.appendChild(el("h2", { text: "Model", attrs: { id: "model-h" } }));
    card.appendChild(el("p", { cls: "note", text: "Frozen evaluation evidence read from disk. Missing artifacts render as —, never as zero or an inferred success." }));
    const r = resources.model;
    card.appendChild(resourceBody(r, "model", (m) => {
      const wrap = el("div");
      const sim = m.sim;
      if (sim && isObj(sim.arms)) {
        wrap.appendChild(el("h3", { text: "Replay arms (2024–25 sim)" }));
        wrap.appendChild(tableFrom(
          ["arm", "n", "accuracy", "brier"],
          Object.entries(sim.arms).map(([k, a]) => [k, fmtInt(a && a.n), fmt(a && a.acc, 4), fmt(a && a.brier, 4)]),
          "Lower Brier is better; historical replay, not a market claim.",
        ));
      } else {
        wrap.appendChild(el("p", { cls: "muted", text: "Sim ledger: — (artifact missing)" }));
      }
      if (sim && isObj(sim.confidence)) {
        const adaptive = sim.confidence.adaptive || {};
        wrap.appendChild(el("h3", { text: "Confidence slices (adaptive arm)" }));
        wrap.appendChild(tableFrom(
          ["slice", "n", "accuracy", "brier"],
          Object.entries(adaptive).map(([k, v]) => [k, fmtInt(v && v.n), fmt(v && v.acc, 4), fmt(v && v.brier, 4)]),
          "Historical hit rates at confidence thresholds.",
        ));
      }
      const cal = m.calibration;
      wrap.appendChild(el("h3", { text: "Calibration validation" }));
      if (cal && isObj(cal.methods)) {
        wrap.appendChild(tableFrom(
          ["method", "brier", "ece", "ships"],
          Object.entries(cal.methods).map(([k, v]) => [k, fmt(v && v.brier, 4), fmt(v && v.ece, 4), v && v.adopt === false ? "killed" : k === "none" ? "yes — raw" : "—"]),
          cal.verdict || "",
        ));
      } else wrap.appendChild(el("p", { cls: "muted", text: "Calibration artifact: — (missing)" }));

      const tour = m.tournament;
      wrap.appendChild(el("h3", { text: "Column-set tournament" }));
      if (tour && isObj(tour.B_folds)) {
        wrap.appendChild(tableFrom(
          ["fold", "n (tr/va/te)", "accuracy", "brier", "ece"],
          Object.entries(tour.B_folds).map(([f, v]) => [f, Array.isArray(v && v.n) ? v.n.join("/") : "—", fmt(v && v.acc, 4), fmt(v && v.brier, 4), fmt(v && v.ece, 4)]),
          tour.verdict || "",
        ));
      } else wrap.appendChild(el("p", { cls: "muted", text: "Tournament artifact: — (missing)" }));

      wrap.appendChild(el("h3", { text: "Signal ablation" }));
      const ab = m.ablation;
      if (ab && Array.isArray(ab.rows)) {
        wrap.appendChild(tableFrom(
          ["dropped", "brier", "delta", "accuracy"],
          ab.rows.map((x) => [x.variant, fmt(x.brier, 4), fmt(x.delta_brier, 5), fmt(x.acc, 4)]),
          "Positive delta means dropping the signal hurt.",
        ));
      } else if (ab && ab.error) wrap.appendChild(el("p", { cls: "muted", text: `Ablation artifact: ${ab.error}` }));
      else wrap.appendChild(el("p", { cls: "muted", text: "Ablation artifact: — (missing)" }));
      return wrap;
    }));

    const relCard = el("section", { cls: "card" });
    relCard.appendChild(el("h2", { text: "Reliability (out-of-fold)" }));
    relCard.appendChild(el("p", { cls: "note", text: "Equal-count bins. Perfect calibration is y = x. Empty numeric fields stay —." }));
    relCard.appendChild(resourceBody(resources.reliability, "reliability", (p) => {
      const wrap = el("div");
      wrap.appendChild(tableFrom(
        ["bin", "predicted mean", "actual frac +", "n"],
        p.bins.map((b) => [String(b.index + 1), fmt(b.pMean, 4), fmt(b.fracPos, 4), fmtInt(b.n)]),
        "Reliability by equal-count bin (p_mean vs frac_pos); no fixed-width lo/hi boundaries.",
      ));
      const dl = el("dl", { cls: "provenance" });
      addPair(dl, "OOF ECE", fmt(p.eceOof, 4));
      addPair(dl, "n", fmtInt(p.n));
      wrap.appendChild(dl);
      if (p.note) wrap.appendChild(el("p", { cls: "note", text: p.note }));
      return wrap;
    }));
    return fragOf([card, relCard]);
  }

  // ---- Experiments --------------------------------------------------------

  function experimentsView() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "exp-h" } });
    card.appendChild(el("h2", { text: "Experiments", attrs: { id: "exp-h" } }));
    card.appendChild(el("p", { cls: "note", text: "Frozen walk-forward experiments. Arms keep their own identifiers and cohorts; unlike samples are never pooled." }));
    card.appendChild(resourceBody(resources.model, "model", (m) => {
      const wrap = el("div");
      const sim = m.sim;
      if (sim) {
        const dl = el("dl", { cls: "provenance" });
        addPair(dl, "Matches (adaptive arm)", fmtInt(sim.n));
        addPair(dl, "Paired blocks", sim.paired_mean ? fmtInt(sim.paired_mean.blocks) : "—");
        addPair(dl, "Adaptive − frozen mean Brier", sim.paired_mean ? fmt(sim.paired_mean.mean_diff, 5) : "—");
        addPair(dl, "Market usage", sim.use_market == null ? "—" : String(sim.use_market));
        wrap.appendChild(dl);
        const pcs = [["adaptive vs frozen", sim.paired_mean], ["gbm vs adaptive", sim.gbm_vs_adaptive], ["gbm vs frozen", sim.gbm_vs_frozen]];
        wrap.appendChild(tableFrom(
          ["comparison", "mean diff", "95% CI", "verdict"],
          pcs.map(([t, v]) => [t, fmt(v && v.mean_diff, 5), v && Array.isArray(v.ci95) ? `[${v.ci95.map((x) => fmt(x, 5)).join(", ")}]` : "—", v && v.verdict ? String(v.verdict) : "—"]),
          "Negative favours the first arm.",
        ));
      } else {
        wrap.appendChild(el("p", { cls: "muted", text: "Sim ledger: — (artifact missing). Not shown as zero." }));
      }
      if (sim && isObj(sim.per_surface)) {
        wrap.appendChild(el("h3", { text: "Per surface" }));
        wrap.appendChild(tableFrom(
          ["arm", "surface", "n", "brier"],
          Object.entries(sim.per_surface).flatMap(([arm, byS]) => isObj(byS) ? Object.entries(byS).map(([s, v]) => [arm, s, fmtInt(v && v.n), fmt(v && v.brier, 4)]) : []),
          "Per-surface replay evidence.",
        ));
      }
      return wrap;
    }));
    const disc = el("section", { cls: "card" });
    disc.appendChild(el("h2", { text: "Historical paper simulation" }));
    disc.appendChild(disclosureNode());
    return fragOf([card, disc]);
  }

  // ---- Methodology --------------------------------------------------------

  function methodologyView() {
    const frag = document.createDocumentFragment();
    const scope = el("section", { cls: "card" });
    scope.appendChild(el("h2", { text: "Method and scope" }));
    const h = resources.health.data;
    const dl = el("dl", { cls: "provenance" });
    addPair(dl, "Sport", "ATP tennis (men's singles)");
    addPair(dl, "Hard match limitation", "Match prediction is hard-court only (the backend _build_card hardcodes hard); no fictitious surface control is offered.");
    addPair(dl, "Groups", "US Open (best-of-5) and ATP Tour (best-of-3).");
    addPair(dl, "Surface", "Surface selection applies to player ratings only, never to the match board.");
    addPair(dl, "Provider credential", h ? (h.has_key ? "reported present" : "not reported") : "—");
    addPair(dl, "Auth for writes", h ? String(h.auth) : "—");
    scope.appendChild(dl);
    scope.appendChild(el("p", { cls: "note", text:
      "has_key is credential availability, not proof of live data. Provider status is process-global, not an event-level attestation." }));
    frag.appendChild(scope);

    const stats = el("section", { cls: "card" });
    stats.appendChild(el("h2", { text: "Stats" }));
    stats.appendChild(resourceBody(resources.stats, "stats", (s) => {
      const wrap = el("div");
      const dl2 = el("dl", { cls: "provenance" });
      if (isObj(s.stats)) for (const [k, v] of Object.entries(s.stats)) {
        if (v !== null && typeof v === "object") continue;
        addPair(dl2, k, v == null ? "—" : String(v));
      }
      wrap.appendChild(dl2);
      wrap.appendChild(structuredPanel("Provider", s.provider));
      wrap.appendChild(structuredPanel("Quota", s.quota));
      return wrap;
    }));
    frag.appendChild(stats);

    const limits = el("section", { cls: "card" });
    limits.appendChild(el("h2", { text: "Limitations" }));
    const ul = el("ul");
    for (const t of [
      "Tennis group-feed modes and minimum cache age cannot prove every event is fresh or even homogeneous.",
      "Mock and unknown feeds are always contained; no actionable live claim is permitted.",
      "Historical A5 evaluation does not establish prospective A7 prediction performance.",
      "The match board has no event-level source timestamp; group cache age is not event freshness.",
    ]) ul.appendChild(el("li", { text: t }));
    limits.appendChild(ul);
    frag.appendChild(limits);
    return frag;
  }

  // ---- shared resource rendering -----------------------------------------

  function resourceBody(r, key, renderData) {
    const box = el("div");
    if (r.status === "loading") { box.appendChild(el("p", { cls: "muted", text: "Loading…" })); return box; }
    if (r.status === "invalid") { box.appendChild(statusBanner("Invalid response", r.error, "error")); box.appendChild(retryButton(key)); return box; }
    if (r.status === "empty") { box.appendChild(el("p", { text: "No data returned." })); box.appendChild(refreshRow(key, r)); return box; }
    if (r.status !== "ready") { box.appendChild(statusBanner(statusTitle(r.status), r.error, "error")); box.appendChild(retryButton(key)); return box; }
    if (r.refreshFailed) box.appendChild(statusBanner("Refresh failed — showing last successful response", r.refreshError, "warn"));
    box.appendChild(refreshRow(key, r));
    box.appendChild(renderData(r.data));
    box.appendChild(el("p", { cls: "provenance", text: `Fetched at ${fmtTs(r.fetchedAt)}` }));
    return box;
  }

  function retryButton(key) {
    const b = el("button", { cls: "action", text: "Retry", attrs: { type: "button", "data-focus-key": `retry-${key}` } });
    b.addEventListener("click", () => void load(key));
    return b;
  }

  function refreshRow(key, r) {
    const row = el("div", { cls: "meta" });
    const b = el("button", { cls: "action", text: r.refreshing ? "Refreshing…" : "Refresh", attrs: { type: "button", "data-focus-key": `refresh-${key}` } });
    b.disabled = !!r.refreshing;
    b.addEventListener("click", () => void load(key));
    row.appendChild(b);
    if (r.fetchedAt != null) row.appendChild(el("span", { cls: "note", text: `Last successful response ${fmtTs(r.fetchedAt)}` }));
    return row;
  }

  function statusBanner(title, detail, kind) {
    const b = el("div", { cls: `banner ${kind}` });
    b.appendChild(el("strong", { text: title }));
    if (detail) b.appendChild(el("span", { text: ` — ${detail}` }));
    return b;
  }

  function structuredPanel(title, obj) {
    const panel = el("div", { cls: "card" });
    panel.appendChild(el("h3", { text: title }));
    if (!isObj(obj)) { panel.appendChild(el("p", { cls: "muted", text: "—" })); return panel; }
    const dl = el("dl", { cls: "provenance" });
    for (const [k, v] of Object.entries(obj)) {
      if (v !== null && typeof v === "object") continue;
      addPair(dl, k, v === null ? "—" : String(v));
    }
    panel.appendChild(dl);
    const det = el("details");
    det.appendChild(el("summary", { text: "Raw JSON" }));
    det.appendChild(el("pre", { text: safeJson(obj) }));
    panel.appendChild(det);
    return panel;
  }

  function tableFrom(headers, rows, caption) {
    const scroll = el("div", { cls: "table-scroll", attrs: { role: "region", tabindex: "0", "aria-label": caption || "Evidence table" } });
    const t = el("table");
    if (caption) t.appendChild(el("caption", { text: caption }));
    const thead = el("thead");
    const hr = el("tr");
    for (const hh of headers) hr.appendChild(el("th", { text: hh, attrs: { scope: "col" } }));
    thead.appendChild(hr);
    t.appendChild(thead);
    const tbody = el("tbody");
    for (const row of rows) {
      const tr = el("tr");
      for (const cell of row) tr.appendChild(el("td", { text: String(cell) }));
      tbody.appendChild(tr);
    }
    t.appendChild(tbody);
    scroll.appendChild(t);
    return scroll;
  }

  function railCard() {
    const card = el("section", { cls: "card" });
    card.appendChild(el("h2", { text: "Provenance" }));
    const dl = el("dl", { cls: "provenance" });
    addPair(dl, "Sport", "ATP tennis (men's singles)");
    addPair(dl, "Match surface", "hard only (backend limitation)");
    addPair(dl, "Browser traffic", "GET only");
    addPair(dl, "Current view", state.tab || "—");
    card.appendChild(dl);
    card.appendChild(el("p", { cls: "note", text:
      "Mock/unknown feeds are contained; cached evidence is never actionable. Artifact paths and model IDs are unchanged from vibe-odds." }));
    return card;
  }

  function renderConfigurationUnavailable(reason) {
    root.textContent = "";
    const main = el("main", { attrs: { id: "main" } });
    const card = el("section", { cls: "card" });
    card.appendChild(el("h1", { text: "Configuration unavailable" }));
    card.appendChild(el("p", { text: String(reason || "The tennis adapter configuration is missing or invalid.") }));
    card.appendChild(el("p", { cls: "note", text: "No API requests were made. Serve this page through the tennis app so the adapter is injected." }));
    main.appendChild(card);
    root.appendChild(main);
    if (root && root.dataset) root.dataset.courtReady = "1";
  }

  // ---- player search / detail --------------------------------------------

  function scheduleSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { void load("players"); }, SEARCH_DEBOUNCE_MS);
  }

  async function loadDetail(name) {
    if (!name) return;
    const { generation, signal } = detailSlot.begin();
    state.playerStatus = "loading";
    state.playerError = null;
    render();
    const path = config.api.player_detail.replace("{name}", encodeURIComponent(name));
    const res = await readClient.getJson(path, { signal });
    if (disposed || !detailSlot.isCurrent(generation)) return;
    if (res.kind === "ok") {
      const parsed = TC.parsePlayerDetail(res.payload);
      if (parsed.kind === "invalid") { state.playerStatus = "invalid"; state.playerError = parsed.reason; }
      else { state.playerStatus = "ready"; state.playerDetail = parsed; state.detailName = parsed.name; }
    } else if (res.kind === "abort") {
      return;
    } else {
      state.playerStatus = res.kind;
      state.playerError = res.message;
    }
    render();
  }
}

// ---- adapter validation ---------------------------------------------------

function validateAdapter(adapter) {
  if (!isObj(adapter)) return { ok: false, reason: "No adapter configuration was injected." };
  if (adapter.sport !== "tennis") return { ok: false, reason: `Unsupported sport: ${String(adapter.sport)}.` };
  const tabs = Array.isArray(adapter.tabs) ? adapter.tabs.filter((t) => typeof t === "string") : TABS.slice();
  const api = adapter.api;
  if (!isObj(api)) return { ok: false, reason: "No API routes configured." };
  for (const [k, v] of Object.entries(api)) {
    if (typeof v !== "string" || !v.startsWith("/api/") || v.includes("://")) {
      return { ok: false, reason: `Invalid route for ${k}.` };
    }
  }
  for (const req of REQUIRED_API) {
    if (typeof api[req] !== "string" || !api[req].startsWith("/api/")) {
      return { ok: false, reason: `Missing required route: ${req}.` };
    }
  }
  return { ok: true, tabs, api, title: adapter.title || "Court Evidence — ATP tennis" };
}

// ---- small helpers --------------------------------------------------------

function mkResource() {
  return { slot: createRequestSlot(), status: "idle", data: null, fetchedAt: null, error: null, refreshing: false, refreshFailed: false, refreshError: null };
}

function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }

function el(tag, { cls, text: t, attrs } = {}) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (t != null) node.textContent = String(t);
  if (attrs) for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function text(t) { return document.createTextNode(String(t)); }

function addPair(dl, term, value) {
  dl.appendChild(el("dt", { text: term }));
  dl.appendChild(el("dd", { text: value == null ? "—" : String(value) }));
}

function num(v) { return typeof v === "number" && Number.isFinite(v); }
function fmt(v, d = 4) { return num(v) ? v.toFixed(d) : "—"; }
function fmt1(v) { return num(v) ? v.toFixed(1) : "—"; }
function fmtInt(v) { return num(v) ? String(Math.trunc(v)) : "—"; }
function fmtTs(msEpoch) {
  if (!num(msEpoch)) return "—";
  try { return new Date(msEpoch).toISOString(); } catch (_e) { return "—"; }
}
function fmtCommence(iso) {
  if (typeof iso !== "string" || !Number.isFinite(Date.parse(iso))) return "—";
  try { return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch (_e) { return iso; }
}
function safeJson(o) {
  try { return JSON.stringify(o, null, 2); } catch (_e) { return "(unserialisable)"; }
}

function statusTitle(kind) {
  if (kind === "offline") return "Offline — provider unavailable";
  if (kind === "timeout") return "Request timed out";
  if (kind === "http") return "Server returned an error";
  if (kind === "invalid") return "Invalid response";
  return "Unavailable";
}

function classify(row) {
  if (row.status && row.status.started) return "started";
  if (row.status && row.status.thin) return "thin";
  return "upcoming";
}

function matchesFilter(row, filter) {
  if (filter === "all") return true;
  return classify(row) === filter;
}

function filterLabel(filter) {
  return filter === "upcoming" ? "upcoming" : filter === "started" ? "started" : filter === "thin" ? "thin-market" : "";
}

function fragOf(nodes) {
  const f = document.createDocumentFragment();
  for (const n of nodes) f.appendChild(n);
  return f;
}

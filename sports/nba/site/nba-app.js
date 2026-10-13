// Court Evidence — NBA application (ES module, browser only).
//
// mountNbaApp({root, adapter, client, now}) -> { dispose(), refresh(), whenIdle }
//
// Owns every resource's independent request slot and renders text nodes only.
// It never writes to localStorage, never POSTs, and never fabricates a
// prediction: a genuine empty log stays empty, malformed rows are quarantined,
// and a failed refresh preserves the last successful timestamp.

import { createReadClient } from "./transport.js";
import { createRequestSlot } from "./request-state.js";
import * as CU from "./nba-contract.js";

const REQUIRED_API = ["site_meta", "board", "model", "experiments", "teams",
  "reliability", "sim", "stats", "predict"];

const RESOURCE_KEYS = ["board", "model", "teams", "experiments", "sim",
  "site_meta", "stats", "reliability"];

const A7_NOTE = "Historical A5 evaluation does not establish prospective A7 prediction performance.";

export function mountNbaApp({ root, adapter, client, now = () => Date.now() } = {}) {
  const readClient = client || createReadClient({ now });
  const config = validateAdapter(adapter);
  const state = { tab: null, matchesFilter: "all", hist: mkHist() };
  const resources = {};
  for (const key of RESOURCE_KEYS) resources[key] = mkResource();
  let disposed = false;
  let idleResolve;
  const idle = new Promise((res) => { idleResolve = res; });
  let mainEl;
  let railEl;
  let statusEl;

  const VIEWS = {
    Matches: matchesView,
    Teams: teamsView,
    Model: modelView,
    Experiments: experimentsView,
    Methodology: methodologyView,
  };

  if (!config.ok) {
    renderConfigurationUnavailable(config.reason);
    idleResolve();
    return { dispose, refresh: () => {}, whenIdle: () => idle };
  }

  state.tab = config.tabs[0] || "Matches";
  buildShell();
  // Defer so the function-scoped view table finishes initialising first.
  queueMicrotask(() => { void loadAll(); });

  return { dispose, refresh: loadAll, whenIdle: () => idle };

  // ---- lifecycle ----------------------------------------------------------

  async function loadAll() {
    await Promise.all(RESOURCE_KEYS.map((k) => load(k)));
    if (root && root.dataset) root.dataset.courtReady = "1";
    idleResolve();
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

    const res = await readClient.getJson(config.api[key], { signal });
    if (disposed || !r.slot.isCurrent(generation)) return; // superseded: touch nothing

    if (res.kind === "ok") {
      const parsed = PARSE[key](res.payload, res.fetchedAt);
      if (parsed.kind === "invalid") {
        if (had) { r.refreshFailed = true; r.refreshError = parsed.reason || "invalid response"; }
        else { r.status = "invalid"; r.error = parsed.reason || "invalid response"; announce(`Invalid response for ${key}`); }
      } else {
        r.status = parsed.kind === "empty" ? "empty" : "ready";
        r.data = parsed.data;
        r.fetchedAt = res.fetchedAt;
        r.error = null;
        r.refreshFailed = false;
      }
    } else if (res.kind === "abort") {
      r.refreshing = false;
      render();
      return;
    } else {
      if (had) { r.refreshFailed = true; r.refreshError = res.message; }
      else { r.status = res.kind; r.error = res.message; }
      announce(`Could not load ${key}: ${res.message}`);
    }
    r.refreshing = false;
    render();
  }

  function dispose() {
    disposed = true;
    for (const r of Object.values(resources)) r.slot.cancel();
    if (state.hist.slot) state.hist.slot.cancel();
    if (root) root.textContent = "";
    idleResolve();
  }

  // ---- shell --------------------------------------------------------------

  function buildShell() {
    root.textContent = "";
    const header = el("header", { cls: "site-header" });
    const brand = el("div", { cls: "brand" });
    brand.appendChild(text("Court Evidence"));
    brand.appendChild(el("span", { cls: "sport", text: " — NBA" }));
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

  // ---- views --------------------------------------------------------------

  function matchesView() {
    const frag = document.createDocumentFragment();
    frag.appendChild(historicalCard());
    frag.appendChild(boardCard());
    return frag;
  }

  function boardCard() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "board-h" } });
    const h = el("h2", { text: "Logged predictions", attrs: { id: "board-h" } });
    card.appendChild(h);
    card.appendChild(el("p", { cls: "note", text:
      "The pre-tip live log is the only forward-looking record: every row was logged strictly before its tipoff." }));

    const r = resources.board;

    if (r.status === "loading") {
      card.appendChild(el("p", { cls: "muted", text: "Loading logged predictions…" }));
      return card;
    }
    if (r.status === "invalid") {
      card.appendChild(statusBanner("Invalid response", r.error, "error"));
      card.appendChild(retryButton("board"));
      return card;
    }
    if (r.status === "empty") {
      card.appendChild(el("p", { text: "No logged predictions yet." }));
      card.appendChild(el("p", { cls: "note", text:
        "An empty log is not a slate and never a reason to invent games." }));
      card.appendChild(refreshRow("board", r));
      return card;
    }
    if (r.status !== "ready") {
      card.appendChild(statusBanner(statusTitle(r.status), r.error, "error"));
      card.appendChild(retryButton("board"));
      return card;
    }

    if (r.refreshFailed) {
      card.appendChild(statusBanner("Refresh failed — showing last successful response", r.refreshError, "warn"));
    }
    card.appendChild(refreshRow("board", r));

    const board = r.data; // parsed board result
    if (board.rejected > 0) {
      const pill = el("span", { cls: "badge quarantine", text: `${board.rejected} quarantined row${board.rejected === 1 ? "" : "s"}` });
      const wrap = el("p", { cls: "note" });
      wrap.appendChild(pill);
      wrap.appendChild(text(" — malformed rows are shown here, never normalised into predictions."));
      card.appendChild(wrap);
    }

    const filter = filterRow(board.rows);
    card.appendChild(filter.bar);
    const shown = board.rows.filter((row) => matchesFilter(row, state.matchesFilter, now()));
    if (shown.length === 0) {
      card.appendChild(el("p", { cls: "muted", text: `No ${filterLabel(state.matchesFilter)} predictions in the log.` }));
      return card;
    }
    card.appendChild(cardList(shown));
    card.appendChild(el("p", { cls: "provenance", text:
      `Fetched at ${fmtTs(r.fetchedAt)} · ${board.rows.length} valid · ${board.rejected} quarantined` }));
    return card;
  }

  function filterRow(rows) {
    const bar = el("div", { cls: "filters", attrs: { role: "group", "aria-label": "Filter logged predictions" } });
    const opts = [["all", "All"], ["upcoming", "Upcoming"], ["awaiting", "Awaiting settlement"], ["settled", "Settled"]];
    for (const [value, label] of opts) {
      const count = rows.filter((row) => matchesFilter(row, value, now())).length;
      const b = el("button", { text: `${label} (${count})`, attrs: { type: "button", "aria-pressed": String(state.matchesFilter === value) } });
      b.addEventListener("click", () => { state.matchesFilter = value; render(); });
      bar.appendChild(b);
    }
    return { bar };
  }

  function cardList(rows) {
    const ul = el("ul", { cls: "row-cards" });
    for (const row of rows) {
      const li = el("li", { cls: "row-card" });
      const top = el("div", { cls: "meta" });
      top.appendChild(pill(row.pick === "home" ? "Home" : "Away", "badge"));
      top.appendChild(text(`Game ${row.game_id}`));
      top.appendChild(text(`P(home) ${fmt(row.prob_home, 3)}`));
      li.appendChild(top);
      const meta = el("div", { cls: "meta" });
      // Source timestamps are the exact ISO instants the log recorded; the
      // browser "Fetched at" provenance is a separate, epoch-derived value.
      meta.appendChild(text(`Tipoff ${fmtSourceTs(row.tipoff_ts)}`));
      meta.appendChild(text(`Logged ${fmtSourceTs(row.logged_at)}`));
      meta.appendChild(text(`Source ${row.source}`));
      li.appendChild(meta);
      const state_ = classify(row, now());
      if (state_ === "settled" && row.settled) {
        li.appendChild(el("div", { cls: "meta", text:
          `Settled ${row.settled.home_score}–${row.settled.away_score}` +
          (row.correct === true ? " · correct" : row.correct === false ? " · incorrect" : "") }));
      } else if (state_ === "awaiting") {
        li.appendChild(el("div", { cls: "meta", text: "Awaiting settlement — past tipoff, no score logged. Not a loss." }));
      } else {
        li.appendChild(el("div", { cls: "meta", text: "Upcoming — tipoff is in the future." }));
      }
      // An invalid settlement is dropped by the contract; the row survives as an
      // explicit pending row and states why it is not settled.
      if (row.warning) li.appendChild(el("div", { cls: "note", text: row.warning }));
      ul.appendChild(li);
    }
    return ul;
  }

  function historicalCard() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "hist-h" } });
    const head = el("div", { cls: "meta" });
    head.appendChild(el("h2", { text: "Historical reconstruction", attrs: { id: "hist-h" } }));
    head.appendChild(el("span", { cls: "badge historical", text: "Historical — not prospective" }));
    card.appendChild(head);
    card.appendChild(el("p", { cls: "note", text:
      "Look up a completed game by ID. The value is re-derived from published coefficients (A5), " +
      "not a timestamped pre-tip prediction. " + A7_NOTE }));

    const form = el("form", { cls: "form-inline" });
    const label = el("label", { text: "Game ID" });
    const input = el("input", { attrs: { type: "text", name: "game_id", id: "hist-input", autocomplete: "off", placeholder: "e.g. 202401050LAL" } });
    input.dataset.focusKey = "hist-input";
    label.appendChild(input);
    const btn = el("button", { cls: "action", text: "Reconstruct", attrs: { type: "submit", "data-focus-key": "hist-submit" } });
    form.appendChild(label);
    form.appendChild(btn);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      void lookupHistorical(input.value.trim());
    });
    card.appendChild(form);

    const h = state.hist;
    if (h.status === "loading") card.appendChild(el("p", { cls: "muted", text: "Reconstructing…" }));
    else if (h.status === "invalid") card.appendChild(statusBanner("Not a valid historical record", h.error, "error"));
    else if (h.status === "ready") card.appendChild(historicalResult(h.result, h.requestedId));
    else card.appendChild(el("p", { cls: "note", text: "No lookup yet." }));
    return card;
  }

  function historicalResult(result, requestedId) {
    const box = el("div");
    const row = result.row;
    box.appendChild(el("p", { text: `Game ${row.game_id} · ${fmtDate(row.date)}` }));
    const dl = el("dl", { cls: "provenance" });
    addPair(dl, "Home team ID", row.home_team_id);
    addPair(dl, "Away team ID", row.away_team_id);
    addPair(dl, "P(home) re-derived", fmt(row.prob_home, 4));
    addPair(dl, "Pick", row.pick === "home" ? "Home" : "Away");
    addPair(dl, "Recorded result", row.result_home_win === null ? "— (unscored)" : row.result_home_win === 1 ? "Home won" : "Away won");
    addPair(dl, "Formula", result.provenance.formula || "—");
    addPair(dl, "Requested ID", requestedId);
    box.appendChild(dl);
    box.appendChild(el("p", { cls: "note", text: result.provenance.note || "" }));
    box.appendChild(el("p", { cls: "note", text: A7_NOTE }));
    return box;
  }

  async function lookupHistorical(id) {
    const h = state.hist;
    if (!id) {
      h.status = "invalid";
      h.error = "Enter a game ID to reconstruct.";
      h.requestedId = id;
      render();
      return;
    }
    // The slot is persistent (minted once in mkHist) and captured locally: a
    // new lookup supersedes by generation, never by replacing the slot. This
    // keeps a slow A success/error from matching B's generation and winning.
    const slot = h.slot;
    const { generation, signal } = slot.begin();
    h.status = "loading";
    h.requestedId = id;
    h.result = null;
    h.error = null;
    render();

    const res = await readClient.getJson(`${config.api.predict}?game_id=${encodeURIComponent(id)}`, { signal });
    if (disposed || !slot.isCurrent(generation)) return;

    if (res.kind === "ok") {
      const parsed = CU.parseHistoricalPrediction(res.payload, id);
      if (parsed.kind === "ready") { h.status = "ready"; h.result = parsed; }
      else { h.status = "invalid"; h.error = parsed.message; }
    } else if (res.kind === "abort") {
      return;
    } else {
      h.status = res.kind;
      h.error = res.message;
    }
    render();
  }

  function teamsView() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "teams-h" } });
    card.appendChild(el("h2", { text: "Teams", attrs: { id: "teams-h" } }));
    card.appendChild(el("p", { cls: "note", text:
      "Names, wins and as-ingested row counts only. Win-rate and home-win-rate headlines are suppressed " +
      "because their denominators include scheduled, unscored rows; they are not completed-game rates." }));
    const r = resources.teams;
    card.appendChild(resourceBody(r, "teams", (parsed) => {
      const wrap = el("div");
      wrap.appendChild(el("p", { cls: "note", text: `${parsed.nGames == null ? "—" : parsed.nGames} regular-season rows as-ingested.` }));
      wrap.appendChild(tableFrom(
        ["team", "wins", "games"],
        parsed.teams.map((t) => [String(t.team == null ? t.team_id : t.team), fmtInt(t.wins), fmtInt(t.games)]),
        "Teams by as-ingested regular-season rows. No rate column by design.",
      ));
      return wrap;
    }));
    return card;
  }

  function modelView() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "model-h" } });
    card.appendChild(el("h2", { text: "Model", attrs: { id: "model-h" } }));
    const r = resources.model;
    card.appendChild(resourceBody(r, "model", (m) => {
      const wrap = el("div");
      wrap.appendChild(el("p", { text: `Formula source: ${m.formula.file || "formula_v1.json"} (published coefficients).` }));
      const f = m.formula;
      wrap.appendChild(tableFrom(
        ["feature", "coefficient"],
        (f.features || []).map((k) => [String(k), fmt((f.coef_raw || {})[k], 4)]),
        "Published A5 coefficients (raw logistic).",
      ));
      const dl = el("dl", { cls: "provenance" });
      addPair(dl, "Intercept (raw)", fmt(f.intercept_raw, 4));
      addPair(dl, "Fit games", fmtInt(f.n_train));
      addPair(dl, "Train end", f.train_end == null ? "—" : String(f.train_end));
      addPair(dl, "Tuned on", f.tuned_on == null ? "—" : String(f.tuned_on));
      addPair(dl, "Test seasons", Array.isArray(f.test_seasons) && f.test_seasons.length ? f.test_seasons.join(", ") : "—");
      wrap.appendChild(dl);

      // Distinct availability cohorts — unlike samples are never pooled.
      const ntg = m.nTestGames || {};
      wrap.appendChild(el("h3", { text: "Test windows and cohorts" }));
      wrap.appendChild(tableFrom(
        ["cohort", "n games"],
        [
          ["all pre-registered test games", fmtInt(ntg.all)],
          ["cohort with closing line", fmtInt(ntg.with_close)],
          ["cohort with opening line", fmtInt(ntg.with_open)],
        ],
        "Cohorts differ by which market prices exist; they are never pooled.",
      ));

      for (const [label, pooled] of [
        ["Pooled Brier — all test games", m.pooledAllTest],
        ["Pooled Brier — cohort with closing line", m.pooledWithClose],
        ["Pooled Brier — cohort with opening line", m.pooledWithOpen],
      ]) {
        const arms = pooled && isObj(pooled) ? Object.keys(pooled) : [];
        if (!arms.length) continue;
        wrap.appendChild(el("h3", { text: label }));
        wrap.appendChild(tableFrom(
          ["arm", "n", "brier", "accuracy", "ece"],
          arms.map((k) => {
            const a = pooled[k] || {};
            return [k, fmtInt(a.n), fmt(a.brier, 5), fmt(a.acc, 5), fmt(a.ece, 5)];
          }),
          "Lower Brier is better; historical evaluation, not a market claim.",
        ));
      }

      const seasons = m.bySeason && isObj(m.bySeason) ? Object.keys(m.bySeason) : [];
      if (seasons.length) {
        wrap.appendChild(el("h3", { text: "Per-season formula vs baselines" }));
        wrap.appendChild(tableFrom(
          ["season", "n", "formula brier", "formula ece", "formula acc", "elo-only brier", "climatological brier", "market close brier"],
          seasons.map((s) => {
            const row = m.bySeason[s] || {};
            const f0 = row.formula || {};
            return [s, fmtInt(f0.n), fmt(f0.brier, 5), fmt(f0.ece, 5), fmt(f0.acc, 5),
              fmt((row.elo_only || {}).brier, 5), fmt((row.climatological || {}).brier, 5),
              fmt((row["market_close (market)"] || {}).brier, 5)];
          }),
          "Descriptive per-season metrics; unlike samples are never pooled.",
        ));
      }

      addPairedRow(wrap, "Paired — formula vs closing line (frozen test block)", m.pairedVsClose);
      addPairedRow(wrap, "Paired — formula vs market (tuning season)", m.tuningPaired);

      wrap.appendChild(el("h3", { text: "Claim ladder (pre-registered)" }));
      wrap.appendChild(tableFrom(
        ["tier", "claim", "verdict", "n games", "mean diff", "95% CI"],
        (m.tierRows || []).map((t) => {
          const tier = (m.tiers || {})[t.key] || {};
          const fv = tier.formula_vs || {};
          const ci = intervalText(fv.ci95_blocked);
          return [t.key, t.claim, verdictBadge(t.verdict), fmtInt(fv.n != null ? fv.n : tier.n_games), fmt(fv.mean_diff, 5), ci];
        }),
        "A verdict of Unknown means the frozen record carries no verdict; success is never inferred.",
      ));
      return wrap;
    }));
    return card;
  }

  function experimentsView() {
    const card = el("section", { cls: "card", attrs: { "aria-labelledby": "exp-h" } });
    card.appendChild(el("h2", { text: "Experiments", attrs: { id: "exp-h" } }));
    card.appendChild(el("p", { cls: "note", text:
      "Exploratory and development evidence. Arms keep their own identifiers and cohorts; unlike samples are never pooled." }));
    const r = resources.experiments;
    card.appendChild(resourceBody(r, "experiments", (exp) => {
      const wrap = el("div");
      wrap.appendChild(structuredPanel("A6 — availability arm", exp.availability));
      wrap.appendChild(structuredPanel("Calibration bake-off", exp.calibration));
      wrap.appendChild(structuredPanel("Drop-one-signal ablation", exp.ablation));
      return wrap;
    }));
    const sim = resources.sim;
    const simCard = el("section", { cls: "card" });
    simCard.appendChild(el("h2", { text: "Walk-forward ledger" }));
    simCard.appendChild(resourceBody(sim, "sim", (s) => structuredPanel("Frozen walk-forward", s)));
    return fragOf([card, simCard]);
  }

  function methodologyView() {
    const frag = document.createDocumentFragment();

    const scope = el("section", { cls: "card" });
    scope.appendChild(el("h2", { text: "Method and scope" }));
    const meta = resources.site_meta;
    scope.appendChild(resourceBody(meta, "site_meta", (m) => {
      const wrap = el("div");
      const dl = el("dl", { cls: "provenance" });
      addPair(dl, "Scope", m.scope || "—");
      addPair(dl, "Formula", m.formula || "—");
      addPair(dl, "As-of discipline", "Every value is derived from the frozen ledgers or the as-ingested DB; nothing is hand-typed.");
      wrap.appendChild(dl);
      wrap.appendChild(el("h3", { text: "Artifact inventory" }));
      wrap.appendChild(tableFrom(
        ["key", "file", "bytes", "generated by"],
        (m.artifacts || []).map((a) => [String(a.key), String(a.file), fmtInt(a.bytes), a.generated_by == null ? "—" : String(a.generated_by)]),
        "Byte sizes are inventories, not source hashes or freshness timestamps.",
      ));
      return wrap;
    }));
    frag.appendChild(scope);

    const stats = el("section", { cls: "card" });
    stats.appendChild(el("h2", { text: "Database counts" }));
    stats.appendChild(resourceBody(resources.stats, "stats", (s) => {
      const wrap = el("div");
      const dl = el("dl", { cls: "provenance" });
      addPair(dl, "Rows total", fmtInt(s.raw));
      addPair(dl, "Completed (scored)", fmtInt(s.completed));
      addPair(dl, "Scheduled (unscored)", fmtInt(s.scheduled));
      wrap.appendChild(dl);
      wrap.appendChild(el("p", { cls: "note", text:
        "Scheduled rows are split out so unplayed games cannot masquerade as a larger played corpus." }));
      if (s.seasons.length) {
        wrap.appendChild(tableFrom(["season", "rows"], s.seasons.map((x) => [String(x.season), fmtInt(x.n)]),
          "Raw row counts per season — completed counts are not inferred per season."));
      }
      return wrap;
    }));
    frag.appendChild(stats);

    const rel = el("section", { cls: "card" });
    rel.appendChild(el("h2", { text: "Reliability (test block, re-derived)" }));
    rel.appendChild(resourceBody(resources.reliability, "reliability", (p) => {
      const wrap = el("div");
      wrap.appendChild(el("p", { cls: "note", text: p.note || "" }));
      wrap.appendChild(tableFrom(
        ["bin", "n", "mean P", "win rate"],
        p.bins.map((b) => [
          b.lo == null || b.hi == null ? "—" : `${fmt(b.lo, 3)}–${fmt(b.hi, 3)}`,
          fmtInt(b.n),
          b.n === 0 ? "— (empty bin)" : fmt(b.meanP, 4),
          b.n === 0 ? "— (empty bin)" : fmt(b.rate, 4),
        ]),
        "Empty bins remain gaps; a missing mean or rate is never shown as zero.",
      ));
      const dl = el("dl", { cls: "provenance" });
      addPair(dl, "ECE", fmt(p.ece, 4));
      wrap.appendChild(dl);
      return wrap;
    }));
    frag.appendChild(rel);

    const limits = el("section", { cls: "card" });
    limits.appendChild(el("h2", { text: "Limitations" }));
    const ul = el("ul");
    for (const t of [
      A7_NOTE,
      "NBA predictions cannot be produced pre-tip from the documented free pipeline; the live board only shows what was actually logged.",
      "The board has no team names, odds, or source-feed timestamp; game ID and home/away orientation are shown instead.",
      "Reliability and calibration are historical re-derivations on the frozen test block, not live calibration.",
      "Tennis group-feed modes and cache age do not establish event-level freshness; that is a separate milestone.",
    ]) ul.appendChild(el("li", { text: t }));
    limits.appendChild(ul);
    frag.appendChild(limits);
    return frag;
  }

  // ---- resource rendering helpers ----------------------------------------

  function resourceBody(r, key, renderData) {
    const box = el("div");
    if (r.status === "loading") { box.appendChild(el("p", { cls: "muted", text: "Loading…" })); return box; }
    if (r.status === "invalid") {
      box.appendChild(statusBanner("Invalid response", r.error, "error"));
      box.appendChild(retryButton(key));
      return box;
    }
    if (r.status === "empty") {
      box.appendChild(el("p", { text: "No data returned." }));
      box.appendChild(refreshRow(key, r));
      return box;
    }
    if (r.status !== "ready") {
      box.appendChild(statusBanner(statusTitle(r.status), r.error, "error"));
      box.appendChild(retryButton(key));
      return box;
    }
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

  // Structured artifact panels. The primary UX renders nested numeric evidence
  // (arms, cohorts, calibrators, per-season metrics, ablation deltas, paired
  // intervals) as visible tables/key-values. Raw JSON is a secondary aid and is
  // never required to reach a number. A missing value renders "—", never 0.
  function structuredPanel(title, obj) {
    const panel = el("div", { cls: "card structured" });
    panel.appendChild(el("h3", { text: title }));
    if (obj == null || (!isObj(obj) && !Array.isArray(obj))) {
      panel.appendChild(el("p", { cls: "muted", text: "—" }));
    } else {
      panel.appendChild(renderStructuredNode(obj));
    }
    const det = el("details");
    det.appendChild(el("summary", { text: "Raw JSON" }));
    det.appendChild(el("pre", { text: safeJson(obj) }));
    panel.appendChild(det);
    return panel;
  }

  function isPrim(v) { return v === null || typeof v === "number" || typeof v === "boolean" || typeof v === "string"; }
  function isPrimArray(v) { return Array.isArray(v) && v.every(isPrim); }
  function fmtScalar(v) {
    if (v == null) return "—";
    if (typeof v === "number") return Number.isFinite(v) ? String(v) : "—";
    if (Array.isArray(v)) return `[${v.map(fmtScalar).join(", ")}]`;
    return String(v);
  }
  function unionLeafKeys(objs) {
    const cols = [];
    for (const o of objs) for (const [k, v] of Object.entries(o)) {
      if ((isPrim(v) || isPrimArray(v)) && !cols.includes(k)) cols.push(k);
    }
    return cols;
  }
  // Sibling records are merged into one table ONLY when they share an identical
  // set of primitive leaf keys (A5/A6 arms, seasons, calibrators). Semantically
  // unrelated sections (windows, hyperparameters, coefficients, row counts,
  // paired stats) are never forced into a single sparse wide table; each keeps
  // its own labelled block so the columns mean one thing.
  function leafSchema(o) {
    return Object.entries(o)
      .filter(([, v]) => isPrim(v) || isPrimArray(v))
      .map(([k]) => k)
      .sort()
      .join("\u0000");
  }
  function schemaGroups(branches) {
    const groups = [];
    const bySchema = new Map();
    for (const entry of branches) {
      const schema = leafSchema(entry[1]);
      if (schema === "") { groups.push({ schema: null, entries: [entry] }); continue; }
      if (bySchema.has(schema)) bySchema.get(schema).entries.push(entry);
      else { const g = { schema, entries: [entry] }; bySchema.set(schema, g); groups.push(g); }
    }
    return groups;
  }
  function nestedEntries(o) {
    return Object.entries(o).filter(([, v]) => v !== null && typeof v === "object" && !isPrimArray(v));
  }
  function recordTable(objs, cols, rowLabels) {
    const scroll = el("div", { cls: "table-scroll", attrs: { role: "region", tabindex: "0", "aria-label": "Structured evidence" } });
    const t = el("table");
    const thead = el("thead");
    const hr = el("tr");
    hr.appendChild(el("th", { text: "", attrs: { scope: "col" } }));
    for (const c of cols) hr.appendChild(el("th", { text: c, attrs: { scope: "col" } }));
    thead.appendChild(hr);
    t.appendChild(thead);
    const tbody = el("tbody");
    objs.forEach((o, i) => {
      const tr = el("tr");
      tr.appendChild(el("td", { text: rowLabels ? fmtScalar(rowLabels[i]) : String(i + 1) }));
      for (const c of cols) tr.appendChild(el("td", { text: fmtScalar(o[c]) }));
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    scroll.appendChild(t);
    return scroll;
  }
  function structuredSubBlock(label, value, depth) {
    const b = el("div", { cls: "structured" });
    b.appendChild(el(depth <= 1 ? "h4" : "h5", { text: label }));
    b.appendChild(renderStructuredNode(value, depth));
    return b;
  }
  function renderStructuredNode(node, depth = 0) {
    const box = el("div", { cls: "structured" });
    if (isPrim(node)) { box.appendChild(el("p", { text: fmtScalar(node) })); return box; }
    if (Array.isArray(node)) {
      if (node.length === 0) { box.appendChild(el("p", { cls: "muted", text: "—" })); return box; }
      if (isPrimArray(node)) {
        const ul = el("ul", { cls: "note" });
        for (const it of node) ul.appendChild(el("li", { text: fmtScalar(it) }));
        box.appendChild(ul);
        return box;
      }
      const objs = node.filter(isObj);
      const cols = unionLeafKeys(objs);
      if (cols.length) box.appendChild(recordTable(objs, cols, null));
      objs.forEach((o, i) => {
        const nested = nestedEntries(o);
        if (nested.length) box.appendChild(structuredSubBlock(`item ${i + 1}`, Object.fromEntries(nested), depth + 1));
      });
      return box;
    }
    const leaves = Object.entries(node).filter(([, v]) => isPrim(v) || isPrimArray(v));
    const branches = nestedEntries(node);
    if (leaves.length) {
      const dl = el("dl", { cls: "provenance" });
      for (const [k, v] of leaves) addPair(dl, k, fmtScalar(v));
      box.appendChild(dl);
    }
    if (branches.length && branches.every(([, v]) => isObj(v))) {
      for (const group of schemaGroups(branches)) {
        const merge = group.schema != null && group.entries.length > 1;
        if (merge) {
          const objs = group.entries.map(([, v]) => v);
          const cols = unionLeafKeys(objs);
          if (cols.length) box.appendChild(recordTable(objs, cols, group.entries.map(([k]) => k)));
          for (const [k, v] of group.entries) {
            const nested = nestedEntries(v);
            if (nested.length) box.appendChild(structuredSubBlock(k, Object.fromEntries(nested), depth + 1));
          }
        } else {
          for (const [k, v] of group.entries) box.appendChild(structuredSubBlock(k, v, depth + 1));
        }
      }
    } else {
      for (const [k, v] of branches) box.appendChild(structuredSubBlock(k, v, depth + 1));
    }
    if (!leaves.length && !branches.length) box.appendChild(el("p", { cls: "muted", text: "—" }));
    return box;
  }

  function tableFrom(headers, rows, caption) {
    const scroll = el("div", { cls: "table-scroll", attrs: { role: "region", tabindex: "0", "aria-label": caption || "Evidence table" } });
    const t = el("table");
    if (caption) { const c = el("caption", { text: caption }); t.appendChild(c); }
    const thead = el("thead");
    const hr = el("tr");
    for (const hh of headers) hr.appendChild(el("th", { text: hh, attrs: { scope: "col" } }));
    thead.appendChild(hr);
    t.appendChild(thead);
    const tbody = el("tbody");
    for (const row of rows) {
      const tr = el("tr");
      for (const cell of row) {
        if (cell instanceof Node) tr.appendChild(el("td")).appendChild(cell);
        else tr.appendChild(el("td", { text: String(cell) }));
      }
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
    addPair(dl, "Sport", "NBA (A5 formula, market-free)");
    addPair(dl, "Board source", "Pre-tip live log (logged before tipoff)");
    addPair(dl, "Browser traffic", "GET only");
    addPair(dl, "Current view", state.tab || "—");
    card.appendChild(dl);
    card.appendChild(el("p", { cls: "note", text: A7_NOTE }));
    card.appendChild(el("p", { cls: "note", text:
      "Coefficients, artifact paths and model IDs are unchanged from vibe-odds; only presentation is renamed." }));
    return card;
  }

  function renderConfigurationUnavailable(reason) {
    root.textContent = "";
    const main = el("main", { attrs: { id: "main" } });
    const card = el("section", { cls: "card" });
    card.appendChild(el("h1", { text: "Configuration unavailable" }));
    card.appendChild(el("p", { text: String(reason || "The NBA adapter configuration is missing or invalid.") }));
    card.appendChild(el("p", { cls: "note", text: "No API requests were made. Serve this page through the NBA app so the adapter is injected." }));
    main.appendChild(card);
    root.appendChild(main);
    if (root && root.dataset) root.dataset.courtReady = "1";
  }
}

// ---- adapter validation ---------------------------------------------------

function validateAdapter(adapter) {
  if (!isObj(adapter)) return { ok: false, reason: "No adapter configuration was injected." };
  if (adapter.sport !== "nba") return { ok: false, reason: `Unsupported sport: ${String(adapter.sport)}.` };
  const tabs = Array.isArray(adapter.tabs) ? adapter.tabs.filter((t) => typeof t === "string") : [];
  if (tabs.length === 0) return { ok: false, reason: "No tabs configured." };
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
  return { ok: true, tabs, api, title: adapter.title || "Court Evidence — NBA" };
}

// ---- parsers --------------------------------------------------------------

const RAW = (p) => (isObj(p) ? { kind: "ready", data: p } : { kind: "invalid", reason: "payload is not an object" });

const PARSE = {
  site_meta: RAW,
  sim: RAW,
  experiments: (p) => (isObj(p) ? { kind: "ready", data: p } : { kind: "invalid", reason: "experiments payload missing" }),
  board: (p, fetchedAt) => {
    const r = CU.parseBoard(p, { fetchedAt });
    if (r.kind === "invalid") return { kind: "invalid", reason: firstReason(r) };
    return { kind: r.kind, data: r };
  },
  model: (p) => { const r = CU.parseModel(p); return r.kind === "ready" ? { kind: "ready", data: r } : { kind: "invalid", reason: r.reason }; },
  teams: (p) => { const r = CU.parseTeams(p); return r.kind === "ready" ? { kind: "ready", data: r } : { kind: "invalid", reason: r.reason }; },
  stats: (p) => { const r = CU.parseStats(p); return r.kind === "ready" ? { kind: "ready", data: r } : { kind: "invalid", reason: r.reason }; },
  reliability: (p) => { const r = CU.parseReliability(p); return r.kind === "ready" ? { kind: "ready", data: r } : { kind: "invalid", reason: r.reason }; },
};

function firstReason(r) {
  return (r.reasons && r.reasons[0] && r.reasons[0].reason) || "invalid payload";
}

// ---- small helpers --------------------------------------------------------

function mkResource() {
  return { slot: createRequestSlot(), status: "idle", data: null, fetchedAt: null, error: null, refreshing: false, refreshFailed: false, refreshError: null };
}

function mkHist() {
  return { slot: createRequestSlot(), status: "idle", requestedId: null, result: null, error: null };
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

// Paired interval evidence (paired_vs_close / tuning_paired). A missing pair is
// rendered as a gap, never as a zero-effect or an invented success.
function addPairedRow(wrap, label, pair) {
  wrap.appendChild(el("h3", { text: label }));
  const p = isObj(pair) ? pair : null;
  if (!p) { wrap.appendChild(el("p", { cls: "muted", text: "— (no paired record)" })); return; }
  const ci = intervalText(p.ci95_blocked);
  const dl = el("dl", { cls: "provenance" });
  addPair(dl, "n games", fmtInt(p.n));
  addPair(dl, "Mean Brier diff", fmt(p.mean_diff, 5));
  addPair(dl, "sigma_d", fmt(p.sigma_d, 5));
  addPair(dl, "95% CI (blocked)", ci);
  addPair(dl, "Blocks", fmtInt(p.blocks));
  wrap.appendChild(dl);
}

function num(v) { return typeof v === "number" && Number.isFinite(v); }
function fmt(v, d = 4) { return num(v) ? v.toFixed(d) : "—"; }
// Fail-closed interval rendering: a CI is shown only when it is exactly two
// finite ordered endpoints. Any malformed interval renders "—" whole rather
// than a filtered or reordered pair (see CU.sanitizeInterval).
function intervalText(v) {
  const iv = CU.sanitizeInterval(v);
  return iv ? `[${iv.map((x) => fmt(x, 5)).join(", ")}]` : "—";
}
function fmtInt(v) { return num(v) ? String(Math.trunc(v)) : "—"; }
function fmtTs(msEpoch) {
  if (!num(msEpoch)) return "—";
  try { return new Date(msEpoch).toISOString(); } catch (_e) { return "—"; }
}
// Source timestamps are ISO strings, never epoch numbers. Render the exact
// recorded instant; a non-strict value stays "—" rather than being guessed.
function fmtSourceTs(iso) {
  return CU.hasTimezone(iso) ? iso : "—";
}
function fmtDate(s) {
  if (typeof s !== "string" || s.length === 0) return "—";
  return s; // game date, not a generation timestamp
}
function safeJson(o) {
  try { return JSON.stringify(o, null, 2); } catch (_e) { return "(unserialisable)"; }
}
function verdictBadge(v) {
  const cls = v === "PASS" ? "pass" : v === "FAIL" ? "fail" : "unknown";
  return el("span", { cls: `badge ${cls}`, text: v });
}
function pill(label, cls) { return el("span", { cls, text: label }); }

function statusTitle(kind) {
  if (kind === "offline") return "Offline — provider unavailable";
  if (kind === "timeout") return "Request timed out";
  if (kind === "http") return "Server returned an error";
  if (kind === "invalid") return "Invalid response";
  return "Unavailable";
}

function classify(row, nowMs) {
  if (row.settled) return "settled";
  const tip = Date.parse(row.tipoff_ts);
  if (Number.isFinite(tip) && tip > nowMs) return "upcoming";
  return "awaiting";
}

function matchesFilter(row, filter, nowMs) {
  if (filter === "all") return true;
  return classify(row, nowMs) === filter;
}

function filterLabel(filter) {
  return filter === "upcoming" ? "upcoming" : filter === "awaiting" ? "awaiting-settlement" : filter === "settled" ? "settled" : "";
}

function fragOf(nodes) {
  const f = document.createDocumentFragment();
  for (const n of nodes) f.appendChild(n);
  return f;
}

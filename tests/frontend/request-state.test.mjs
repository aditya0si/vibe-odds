import { test } from "node:test";
import assert from "node:assert/strict";
import { loadModule } from "./load-module.mjs";

const { createRequestSlot } = await loadModule("sports/nba/site/request-state.js");
const tennis = await loadModule("frontend/request-state.js");

test("newest_generation_wins", async () => {
  const slot = createRequestSlot();
  const a = slot.begin();
  assert.equal(slot.isCurrent(a.generation), true);

  const b = slot.begin();
  assert.equal(a.signal.aborted, true, "begin() must abort the superseded request");
  assert.equal(slot.isCurrent(a.generation), false);
  assert.equal(slot.isCurrent(b.generation), true);

  // A late response for A resolves AFTER B; only B may commit.
  let data = null;
  let fetchedAt = null;
  const apply = (handle, value, ts) => {
    if (!slot.isCurrent(handle.generation)) return;
    data = value;
    fetchedAt = ts;
  };
  apply(b, "B", 200);
  apply(a, "A", 100);
  assert.equal(data, "B");
  assert.equal(fetchedAt, 200);
});

test("stale_error_cannot_clear_new_spinner", async () => {
  const slot = createRequestSlot();
  const a = slot.begin();
  let spinner = true;
  slot.begin(); // B supersedes A

  const applyStaleError = () => {
    if (!slot.isCurrent(a.generation)) return;
    spinner = false; // would be the bug: a stale failure clears the live spinner
  };
  applyStaleError();
  assert.equal(spinner, true);

  const c = slot.begin();
  slot.cancel();
  assert.equal(c.signal.aborted, true);
  assert.equal(slot.isCurrent(c.generation), false, "cancel() retires the active generation");
});

test("tennis_request_slot_copy_isolates_generations", () => {
  const slot = tennis.createRequestSlot();
  const a = slot.begin();
  const b = slot.begin();
  assert.equal(a.signal.aborted, true, "the tennis copy aborts superseded requests");
  assert.equal(slot.isCurrent(a.generation), false);
  assert.equal(slot.isCurrent(b.generation), true);
  slot.cancel();
  assert.equal(slot.isCurrent(b.generation), false);
});

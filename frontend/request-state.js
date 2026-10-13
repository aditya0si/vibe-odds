// Court Evidence — per-resource request lifecycle (dependency-free ES module).
//
// Local sport copy of the shared request-slot contract (see sports/nba/site/
// request-state.js). Kept local so each sport owns its static root; behaviour
// is identical and covered by the same Node tests.
//
// createRequestSlot() -> { begin(), isCurrent(gen), cancel() }
//
// Each independent resource (board, model, players, player detail, a group
// change, a player search, ...) owns its own slot. begin() supersedes the
// previous request for THAT slot only: it aborts the old one and mints a new
// generation. A superseded response or error must never change current data,
// the spinner, the timestamp, or the selection.

export function createRequestSlot() {
  let generation = 0;
  let controller = null;

  function begin() {
    if (controller) controller.abort();
    controller = new AbortController();
    generation += 1;
    return { generation, signal: controller.signal };
  }

  function isCurrent(gen) {
    return gen === generation;
  }

  function cancel() {
    if (controller) controller.abort();
    controller = null;
    generation += 1;
    return generation;
  }

  return { begin, isCurrent, cancel };
}

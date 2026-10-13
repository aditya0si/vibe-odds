// Court Evidence — per-resource request lifecycle (dependency-free ES module).
//
// createRequestSlot() -> { begin(), isCurrent(gen), cancel() }
//
// Each independent resource (board, model, teams, stats, reliability, a
// historical lookup, ...) owns its own slot. begin() supersedes the previous
// request for THAT slot only: it aborts the old one and mints a new
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

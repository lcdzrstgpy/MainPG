import assert from "node:assert/strict";
import test from "node:test";
import { createPrecheckFinalizeRefresh } from "./precheckFinalizeRefresh.ts";

test("an older response cannot replace a newer finalize run", async () => {
  const resolvers: Array<(value: { id: string; status: string }) => void> = [];
  const seen: string[] = [];
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: () => new Promise((resolve) => resolvers.push(resolve)),
    onRun: (run) => seen.push(run.id),
    onError: () => undefined,
    intervalMs: 20,
  });
  refresh.watch("old");
  refresh.watch("new");
  resolvers[0]({ id: "old", status: "completed" });
  await Promise.resolve();
  assert.deepEqual(seen, []);
  refresh.stop();
});

test("stop clears timers and prevents later writes", async () => {
  let writes = 0;
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: async () => ({ id: "run", status: "publishing" }),
    onRun: () => { writes += 1; },
    onError: () => undefined,
    intervalMs: 10,
  });
  refresh.watch("run");
  refresh.stop();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(writes, 0);
});

test("only one request is in flight and newest watch refreshes next", async () => {
  const resolvers: Array<(value: { id: string; status: string }) => void> = [];
  const requested: string[] = [];
  const seen: string[] = [];
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: (runId) => {
      requested.push(runId);
      return new Promise((resolve) => resolvers.push(resolve));
    },
    onRun: (run) => seen.push(run.id),
    onError: () => undefined,
  });

  refresh.watch("old");
  refresh.watch("new");
  assert.deepEqual(requested, ["old"]);
  resolvers[0]({ id: "old", status: "publishing" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(requested, ["old", "new"]);
  assert.deepEqual(seen, []);
  resolvers[1]({ id: "new", status: "completed" });
  await Promise.resolve();
  assert.deepEqual(seen, ["new"]);
  refresh.stop();
});

test("a newer failure cannot let an older success write back", async () => {
  const resolvers: Array<(value: { id: string; status: string }) => void> = [];
  const rejecters: Array<(error: Error) => void> = [];
  const seen: string[] = [];
  const errors: string[] = [];
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: (runId) => new Promise((resolve, reject) => {
      resolvers.push(resolve);
      rejecters.push(reject);
    }),
    onRun: (run) => seen.push(run.id),
    onError: (error) => errors.push(String(error)),
  });

  refresh.watch("old");
  refresh.watch("new");
  resolvers[0]({ id: "old", status: "completed" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(seen, []);
  rejecters[1](new Error("new request failed"));
  await Promise.resolve();
  assert.deepEqual(seen, []);
  assert.deepEqual(errors, ["Error: new request failed"]);
  refresh.stop();
});

test("visibility pauses polling and becoming visible refreshes immediately", async () => {
  let requests = 0;
  const refresh = createPrecheckFinalizeRefresh({
    fetchRun: async () => {
      requests += 1;
      return { id: "run", status: "publishing" };
    },
    onRun: () => undefined,
    onError: () => undefined,
    intervalMs: 10,
  });

  refresh.setVisible(false);
  refresh.watch("run");
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(requests, 0);
  refresh.setVisible(true);
  await Promise.resolve();
  assert.equal(requests, 1);
  refresh.stop();
});

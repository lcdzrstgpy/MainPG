import assert from "node:assert/strict";
import test from "node:test";

import { createLatestRefreshRunner, isSourcingFullyResolved, sourceSearchResultNotice } from "./refreshCoordinator.ts";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("refresh is single-flight and only the latest generation commits", async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const pending = [first, second];
  const commits: string[] = [];
  let loads = 0;
  const runner = createLatestRefreshRunner(
    async ({ label }: { label: string }) => {
      const current = pending[loads];
      loads += 1;
      return `${label}:${await current.promise}`;
    },
    (value) => commits.push(value),
    (error) => assert.fail(String(error)),
  );

  const oldRun = runner.run({ label: "old" });
  const latestRun = runner.run({ label: "latest" });
  assert.equal(loads, 1);
  first.resolve("response");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(loads, 2);
  assert.deepEqual(commits, []);
  second.resolve("response");
  await Promise.all([oldRun, latestRun]);
  assert.deepEqual(commits, ["latest:response"]);
});

test("a latest refresh failure preserves previously committed data", async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const pending = [first, second];
  let committed = "existing";
  const failures: string[] = [];
  let loads = 0;
  const runner = createLatestRefreshRunner(
    async () => pending[loads++].promise,
    (value) => { committed = value; },
    (error) => failures.push(String(error)),
  );

  const oldRun = runner.run({});
  const latestRun = runner.run({});
  first.resolve("stale");
  await new Promise((resolve) => setImmediate(resolve));
  second.reject(new Error("temporary outage"));
  await Promise.all([oldRun, latestRun]);
  assert.equal(committed, "existing");
  assert.deepEqual(failures, ["Error: temporary outage"]);
});

test("failed quotes never produce an all-success message", () => {
  const notice = sourceSearchResultNotice({ items: [], counts: { candidate_count: 8, failed_quotes: 2 } });
  assert.match(notice, /2 个 SKC 失败/);
  assert.doesNotMatch(notice, /图搜完成/);
});

test("completion requires no unresolved, selected, or preview state", () => {
  const base = { selected_skc_ids: ["SKC-1"], unresolved_skc_ids: [], matched_products: [], preview: null, selected_candidates: [] };
  assert.equal(isSourcingFullyResolved(base), true);
  assert.equal(isSourcingFullyResolved({ ...base, unresolved_skc_ids: ["SKC-1"] }), false);
  assert.equal(isSourcingFullyResolved({ ...base, selected_candidates: [{ skc_id: "SKC-1", offer_id: "1688", source_url: "https://detail.1688.com/offer/1688.html" }] }), false);
  assert.equal(isSourcingFullyResolved({ ...base, preview: { items: [], counts: {} } }), false);
  assert.equal(isSourcingFullyResolved(base, false), false);
});

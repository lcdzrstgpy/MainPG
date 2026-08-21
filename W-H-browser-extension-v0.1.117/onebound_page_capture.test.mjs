import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import PageCapture from "./onebound_page_capture.js";

test("background uses the product-capture OneBound batch endpoints", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  assert.match(background, /\/plugin\/product-capture\/onebound-batches\/prepare/);
  assert.match(background, /\/plugin\/product-capture\/onebound-batches\/start/);
  assert.match(background, /\/plugin\/product-capture\/onebound-batches\/item/);
  assert.match(background, /\/plugin\/product-capture\/onebound-batches\/finish/);
  assert.doesNotMatch(background, /\/plugin\/onebound-page-capture/);
  assert.match(
    background,
    /stage === "prepare"[\s\S]{0,240}批次登记失败/,
    "prepare HTTP errors must not be described as ingestion failures before OneBound starts"
  );
});

test("background persists recoverable jobs, guards START, and keeps 1688 out of detail-tab capture", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  assert.match(background, /createSessionJobStore\(chrome\.storage\.session\)/);
  assert.match(background, /oneboundPageCaptureSessionStore\.load\(batchToken\)/);
  assert.match(background, /connectionIdentityMatches\(job\.connection_identity, connection\)/);
  assert.match(background, /oneboundPageCaptureStartGate\.begin\(batchToken\)/);
  assert.match(background, /if \(result\.finish_error\)/);
  assert.match(background, /await oneboundPageCaptureSessionStore\.save\(job\)/);
  assert.match(background, /status: 409/);
  assert.match(background, /const timeoutMs = stage === "item" \? 60000 : 30000/);
  assert.match(
    background,
    /if \(result\.finish_error\)[\s\S]*?oneboundPageCaptureSessionStore\.save\(job\)[\s\S]*?preservePreparedJob = true;/,
    "a rejected start whose cleanup also fails must keep a cancellable session job"
  );
  const routed1688Branch = background.slice(background.indexOf("if (is1688Tab(tab)"), background.indexOf("const job = createProductBatchCaptureJob(tab)"));
  assert.match(routed1688Branch, /onebound_page_capture_requires_page_confirmation/);
  assert.doesNotMatch(routed1688Branch, /start1688OneboundPageCapture/);
  assert.doesNotMatch(routed1688Branch, /captureBatchProductDetail|createProductBatchCaptureWindow/);
});

test("background forwards finish diagnostics and clears a same-tab prepared batch before re-prepare", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  assert.match(background, /finish_error: result\.finish_error/);
  assert.match(background, /clearPreparedOneboundPageCaptureJobsForTab\(sourceTab, connection\)/);
  assert.match(background, /onebound_page_capture_previous_finish_failed/);
});

test("1688 whole-page contract only scans canonical offer links and never enters detail capture", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  const prepareStart = background.indexOf("async function prepare1688OneboundPageCapture");
  const startStart = background.indexOf("async function start1688OneboundPageCapture");
  const legacyDetailStart = background.indexOf("async function createProductBatchCaptureWindow");
  const prepareBranch = background.slice(prepareStart, startStart);
  const startBranch = background.slice(startStart, legacyDetailStart);

  assert.match(prepareBranch, /OneboundPageCapture\.scanPage/);
  assert.match(prepareBranch, /canonicalizeOfferUrls/);
  assert.match(prepareBranch, /source_urls: sourceUrls/);
  assert.doesNotMatch(prepareBranch, /oneboundPageCaptureSessionStore\.save\(job\)/, "manual workbench batches must be handed off instead of retained as page-owned jobs");
  assert.doesNotMatch(prepareBranch, /oneboundPageCaptureJobs\.set\(job\.id, job\)/, "navigation must not cancel a batch handed off to the workbench");
  assert.match(startBranch, /OneboundPageCapture\.runCapture/);
  assert.doesNotMatch(`${prepareBranch}\n${startBranch}`, /captureBatchProductDetail|createProductBatchCaptureWindow|chrome\.tabs\.(?:create|update)|chrome\.windows\.(?:create|remove)|detail_worker|waitFor.*Detail|extract.*Detail/i);

  const visibleStart = background.indexOf("async function captureVisibleProductsToWorkbench");
  const visibleBranch = background.slice(visibleStart, background.indexOf("async function captureVisibleProductsToWorkbenchJob", visibleStart));
  assert.match(visibleBranch, /onebound_page_capture_requires_page_confirmation/);
  assert.match(visibleBranch, /createProductBatchCaptureJob/);
  assert.doesNotMatch(visibleBranch.slice(0, visibleBranch.indexOf("const job = createProductBatchCaptureJob")), /captureBatchProductDetail|start1688OneboundPageCapture/);
});

test("legacy TEMU/Alibaba and single-product message protocols remain registered", async () => {
  const background = await readFile(new URL("./background.js", import.meta.url), "utf8");
  assert.match(background, /message\?\.type === "CAPTURE_PRODUCT_TO_WORKBENCH"/);
  assert.match(background, /message\?\.type === "CAPTURE_VISIBLE_PRODUCTS_TO_WORKBENCH"/);
  assert.match(background, /message\?\.type === "CAPTURE_TEMU_PRICE_QUOTE_PAGE"/);
  assert.match(background, /captureProductToWorkbench\(sender\.tab\)/);
  assert.match(background, /captureVisibleProductsToWorkbench\(sender\.tab\)/);
});

test("canonicalizeOfferUrls keeps only numeric 1688 offer ids and deduplicates them", () => {
  assert.deepEqual(PageCapture.canonicalizeOfferUrls([
    "https://detail.1688.com/offer/12345.html?foo=bar",
    "//detail.1688.com/offer/12345.html",
    "https://detail.1688.com/offer/not-a-number.html",
    "https://detail.1688.com/offer/67890.htm",
    "https://example.test/offer/111.html",
    "not a url"
  ]), [
    "https://detail.1688.com/offer/12345.html",
    "https://detail.1688.com/offer/67890.html"
  ]);
});

test("scanPage auto-scrolls, stops after three unchanged passes, and restores the scroll position", async () => {
  let scrollY = 73;
  let pass = 0;
  const page = {
    location: { href: "https://s.1688.com/selloffer/offer_search.htm" },
    get scrollY() { return scrollY; },
    document: {
      documentElement: { scrollHeight: 3000 },
      body: { scrollHeight: 3000 },
      querySelectorAll() {
        return pass < 1
          ? [{ href: "https://detail.1688.com/offer/1.html" }]
          : [
            { href: "https://detail.1688.com/offer/1.html" },
            { href: "https://detail.1688.com/offer/2.html" }
          ];
      }
    },
    scrollTo(_x, y) {
      scrollY = y;
      if (y !== 73) pass += 1;
    }
  };

  const result = await PageCapture.scanPage({ page, sleep: async () => {}, maxScrollPasses: 12, scrollWaitMs: 0 });

  assert.deepEqual(result.source_urls, [
    "https://detail.1688.com/offer/1.html",
    "https://detail.1688.com/offer/2.html"
  ]);
  assert.equal(result.stop_reason, "no_new_urls");
  assert.equal(result.scroll_scan_passes, 4);
  assert.equal(scrollY, 73);
});

test("scanPage recognizes current 1688 offerIds card links without accepting unrelated numeric params", async () => {
  const page = {
    location: { href: "https://s.1688.com/selloffer/offer_search.htm?keywords=basket" },
    scrollY: 0,
    document: {
      documentElement: { scrollHeight: 1000 },
      body: { scrollHeight: 1000 },
      querySelectorAll: () => [
        {
          href: "https://s.1688.com/selloffer/similar_search.htm?offerIds=1040702417600&imageAddress=https%3A%2F%2Fexample.test%2Fimage.jpg"
        },
        {
          href: "https://s.1688.com/selloffer/offer_search.htm?pageId=1040702417601&spm=a260k.search"
        },
        {
          href: "https://example.test/similar_search.htm?offerIds=1040702417602"
        },
        {
          href: "https://air.1688.com/chat?offerId=1040702417603"
        },
        {
          href: "https://s.1688.com/selloffer/similar_search.htm?offerIds=not-1040702417604"
        }
      ]
    },
    scrollTo() {}
  };

  const result = await PageCapture.scanPage({ page, sleep: async () => {}, maxScrollPasses: 3, scrollWaitMs: 0 });

  assert.deepEqual(result.source_urls, [
    "https://detail.1688.com/offer/1040702417600.html"
  ]);
});

test("scanPage reads the current product-card outer anchor and deduplicates its inner similar link", async () => {
  const page = {
    location: { href: "https://s.1688.com/selloffer/offer_search.htm?keywords=basket" },
    scrollY: 0,
    document: {
      documentElement: { scrollHeight: 1000 },
      body: { scrollHeight: 1000 },
      querySelectorAll: () => [
        {
          href: "http://detail.m.1688.com/page/index.html?offerId=1056665846829&sortType=&pageId=&abBizDataType=cbuOffer"
        },
        {
          href: "https://s.1688.com/selloffer/similar_search.html?offerIds=1056665846829&scene=similar_search"
        },
        {
          href: "https://air.1688.com/app/chat/index.html?offerId=1056665846829"
        }
      ]
    },
    scrollTo() {}
  };

  const result = await PageCapture.scanPage({ page, sleep: async () => {}, maxScrollPasses: 3, scrollWaitMs: 0 });

  assert.deepEqual(result.source_urls, [
    "https://detail.1688.com/offer/1056665846829.html"
  ]);
});

test("scanPage caps a 1688 page at 80 canonical offer URLs", async () => {
  const page = {
    location: { href: "https://s.1688.com/selloffer/offer_search.htm" },
    scrollY: 0,
    document: {
      documentElement: { scrollHeight: 1000 }, body: { scrollHeight: 1000 },
      querySelectorAll: () => Array.from({ length: 81 }, (_value, index) => ({ href: `https://detail.1688.com/offer/${index + 1}.html` }))
    },
    scrollTo(_x, y) { if (y !== 0) throw new Error("80-item limit should stop before scrolling"); }
  };
  const result = await PageCapture.scanPage({ page, sleep: async () => {}, maxScrollPasses: 12, scrollWaitMs: 650, maxItems: 80 });
  assert.equal(result.source_urls.length, 80);
  assert.equal(result.scroll_scan_passes, 0);
  assert.equal(result.stop_reason, "max_items");
});

test("runCapture calls prepare/start/item/finish with session token, bounds concurrency, and reports failed URLs", async () => {
  const calls = [];
  const progress = [];
  let inFlight = 0;
  let peak = 0;
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: [
      "https://detail.1688.com/offer/1.html",
      "https://detail.1688.com/offer/2.html",
      "https://detail.1688.com/offer/3.html",
      "https://detail.1688.com/offer/4.html"
    ],
    sessionToken: "session-1",
    request: async (stage, body) => {
      calls.push({ stage, body });
      if (stage === "prepare") return { batch_token: "batch-1", source_urls: body.source_urls };
      if (stage === "start") return { batch_token: "batch-1" };
      if (stage === "item") {
        inFlight += 1;
        peak = Math.max(peak, inFlight);
        await Promise.resolve();
        inFlight -= 1;
        if (body.source_url.endsWith("/3.html")) throw new Error("upstream unavailable");
        return { ok: true };
      }
      if (stage === "finish") return { ok: true };
      throw new Error(`unexpected stage: ${stage}`);
    },
    onProgress: (value) => progress.push(value)
  });

  assert.equal(peak, 3);
  assert.deepEqual(calls.map((call) => call.stage), ["prepare", "start", "item", "item", "item", "item", "finish"]);
  assert.ok(calls.every((call) => call.body.session_token === "session-1"));
  assert.deepEqual(result.failed, [{ source_url: "https://detail.1688.com/offer/3.html", error: "upstream unavailable" }]);
  assert.equal(result.created_count, 0);
  assert.equal(calls.at(-1).body.cancelled, false);
  assert.equal(progress.at(-1).completed, 4);
});

test("runCapture keeps created_count distinct from refreshed_count", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html", "https://detail.1688.com/offer/2.html"],
    sessionToken: "session-1",
    request: async (stage, body) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item" && body.source_url.endsWith("/1.html")) return { ok: true, created_count: 1 };
      if (stage === "item") return { ok: true, refreshed_count: 1 };
      if (stage === "finish") return { ok: true };
    }
  });
  assert.equal(result.created_count, 1);
  assert.equal(result.refreshed_count, 1);
  assert.equal(result.captured_count, 2);
});

test("runCapture closes a backend batch when start rejects and retains both diagnostics", async () => {
  const calls = [];
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html"],
    sessionToken: "session-1",
    request: async (stage, body) => {
      calls.push({ stage, body });
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: false, error: "batch expired", statusText: "批次已过期" };
      if (stage === "finish") throw new Error("finish unavailable");
    }
  });
  assert.deepEqual(calls.map((call) => call.stage), ["prepare", "start", "finish"]);
  assert.equal(calls.at(-1).body.cancelled, true);
  assert.equal(result.error, "batch expired");
  assert.match(result.finish_error, /finish unavailable/);
});

test("runCapture treats a failed finish response after rejected start as cleanup failure", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html"],
    sessionToken: "session-1",
    request: async (stage) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: false, error: "start rejected" };
      if (stage === "finish") return { ok: false, error: "finish rejected", statusText: "无法收口" };
    }
  });
  assert.equal(result.error, "start rejected");
  assert.match(result.finish_error, /finish rejected/);
  assert.match(result.help, /无法收口|finish rejected/);
});

test("runCapture decodes backend item outcome values into live counters", async () => {
  const progress = [];
  const outcomes = ["created", "refreshed", "skipped"];
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: outcomes.map((_, index) => `https://detail.1688.com/offer/${index + 1}.html`),
    sessionToken: "session-1",
    concurrency: 1,
    request: async (stage) => {
      if (stage === "prepare") return {
        ok: true,
        batch_token: "batch-outcomes",
        pending_count: 3,
        pending_urls: outcomes.map((_, index) => `https://detail.1688.com/offer/${index + 1}.html`)
      };
      if (stage === "start") return { ok: true };
      if (stage === "item") return { ok: true, outcome: outcomes.shift() };
      if (stage === "finish") return { ok: true };
      throw new Error(`unexpected stage: ${stage}`);
    },
    onProgress: (value) => progress.push(value)
  });

  assert.equal(result.captured_count, 2);
  assert.equal(result.refreshed_count, 1);
  assert.equal(result.skipped_count, 1);
  assert.deepEqual(
    progress.filter((value) => value.phase === "capturing").map((value) => [value.created_count, value.refreshed_count, value.skipped_count]),
    [[1, 0, 0], [1, 1, 0], [1, 1, 1]]
  );
});

test("runCapture stops dispatching after cancellation but waits for in-flight items before finish", async () => {
  const control = { cancelled: false };
  const calls = [];
  let releaseFirstItem;
  let startedItems = 0;
  const firstItem = new Promise((resolve) => { releaseFirstItem = resolve; });
  const pending = PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: [
      "https://detail.1688.com/offer/1.html",
      "https://detail.1688.com/offer/2.html",
      "https://detail.1688.com/offer/3.html",
      "https://detail.1688.com/offer/4.html"
    ],
    sessionToken: "session-1",
    control,
    request: async (stage, body) => {
      calls.push({ stage, body });
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { batch_token: "batch-1" };
      if (stage === "item") {
        startedItems += 1;
        if (startedItems === 3) control.cancelled = true;
        await firstItem;
        return { ok: true };
      }
      if (stage === "finish") return { ok: true };
      throw new Error(`unexpected stage: ${stage}`);
    }
  });

  await Promise.resolve();
  releaseFirstItem();
  const result = await pending;

  assert.equal(calls.filter((call) => call.stage === "item").length, 3);
  assert.equal(calls.at(-1).stage, "finish");
  assert.equal(calls.at(-1).body.cancelled, true);
  assert.equal(result.cancelled, true);
});

test("session job store restores only the recoverable prepared job fields", async () => {
  const values = new Map();
  const storage = {
    async get(key) { return { [key]: values.get(key) }; },
    async set(value) { Object.entries(value).forEach(([key, item]) => values.set(key, item)); },
    async remove(key) { values.delete(key); }
  };
  const store = PageCapture.createSessionJobStore(storage);
  await store.save({
    batch_token: "batch-1",
    source_tab_id: 42,
    page_url: "https://s.1688.com/selloffer/offer_search.htm",
    source_urls: ["https://detail.1688.com/offer/123.html"],
    plan: { batch_token: "batch-1", pending_urls: ["https://detail.1688.com/offer/123.html"] },
    connection_identity: { http_base: "http://127.0.0.1:8010", session_token: "session-1" },
    api_key: "must-not-persist"
  });

  const restored = await store.load("batch-1");
  assert.equal(restored.batch_token, "batch-1");
  assert.equal(restored.source_tab_id, 42);
  assert.deepEqual(restored.source_urls, ["https://detail.1688.com/offer/123.html"]);
  assert.deepEqual(restored.plan.pending_urls, ["https://detail.1688.com/offer/123.html"]);
  assert.deepEqual(restored.connection_identity, { http_base: "http://127.0.0.1:8010", session_token: "session-1" });
  assert.equal(restored.state, "prepared");
  assert.equal(restored.api_key, undefined);
  await store.remove("batch-1");
  assert.equal(await store.load("batch-1"), null);
});

test("session job store serializes concurrent saves and removes without dropping another tab job", async () => {
  const values = new Map();
  const storage = {
    async get(key) { return { [key]: values.get(key) }; },
    async set(value) { Object.entries(value).forEach(([key, item]) => values.set(key, item)); },
    async remove(key) { values.delete(key); }
  };
  const store = PageCapture.createSessionJobStore(storage);
  const identity = { http_base: "http://127.0.0.1:8010", session_token: "session-1" };
  const job = (batchToken, sourceTabId) => ({ batch_token: batchToken, source_tab_id: sourceTabId, page_url: "https://s.1688.com/", source_urls: [`https://detail.1688.com/offer/${sourceTabId}.html`], plan: {}, connection_identity: identity });
  await Promise.all([store.save(job("batch-a", 1)), store.save(job("batch-b", 2))]);
  assert.equal((await store.load("batch-a")).source_tab_id, 1);
  assert.equal((await store.load("batch-b")).source_tab_id, 2);
  await Promise.all([store.remove("batch-a"), store.save(job("batch-c", 3))]);
  assert.equal(await store.load("batch-a"), null);
  assert.equal((await store.load("batch-b")).source_tab_id, 2);
  assert.equal((await store.load("batch-c")).source_tab_id, 3);
});

test("prepared jobs selected for replacement are limited to the requesting tab", () => {
  const jobs = [
    { source_tab_id: 1, state: "prepared", finished: false },
    { source_tab_id: 1, state: "running", finished: false },
    { source_tab_id: 2, state: "prepared", finished: false },
    { source_tab_id: 1, state: "prepared", finished: true }
  ];
  assert.deepEqual(PageCapture.preparedJobsForReplacement(jobs, 1), [jobs[0]]);
});

test("restored cancellation job requires the original tab and connection identity", async () => {
  const values = new Map();
  const storage = {
    async get(key) { return { [key]: values.get(key) }; },
    async set(value) { Object.entries(value).forEach(([key, item]) => values.set(key, item)); },
    async remove(key) { values.delete(key); }
  };
  const store = PageCapture.createSessionJobStore(storage);
  const identity = { http_base: "http://127.0.0.1:8010", session_token: "session-1" };
  await store.save({ batch_token: "batch-1", source_tab_id: 4, page_url: "https://s.1688.com/", source_urls: ["https://detail.1688.com/offer/1.html"], plan: {}, connection_identity: identity });
  assert.equal((await PageCapture.restoreCancellableJob({ store, batchToken: "batch-1", sourceTabId: 5, connection: identity })).job, null);
  assert.equal((await PageCapture.restoreCancellableJob({ store, batchToken: "batch-1", sourceTabId: 4, connection: { ...identity, session_token: "changed" } })).job, null);
  assert.equal((await PageCapture.restoreCancellableJob({ store, batchToken: "batch-1", sourceTabId: 4, connection: identity })).job.batch_token, "batch-1");
});

test("runCapture unions canonical backend and local failed URLs", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html", "https://detail.1688.com/offer/2.html"],
    sessionToken: "session-1",
    request: async (stage, body) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item" && body.source_url.endsWith("/1.html")) throw new Error("item failed");
      if (stage === "item") return { ok: true, created_count: 1 };
      if (stage === "finish") return { ok: true, failed_urls: ["https://detail.1688.com/offer/1.html?retry=1", "https://detail.1688.com/offer/3.html"] };
    }
  });

  assert.deepEqual(result.failed_urls, [
    "https://detail.1688.com/offer/1.html",
    "https://detail.1688.com/offer/3.html"
  ]);
});

test("runCapture preserves local counters and failures when finish rejects", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html", "https://detail.1688.com/offer/2.html"],
    sessionToken: "session-1",
    request: async (stage, body) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item" && body.source_url.endsWith("/1.html")) return { ok: true, created_count: 1 };
      if (stage === "item") throw new Error("item failed");
      if (stage === "finish") throw new Error("finish timeout");
    }
  });

  assert.equal(result.ok, false);
  assert.equal(result.captured_count, 1);
  assert.deepEqual(result.failed_urls, ["https://detail.1688.com/offer/2.html"]);
  assert.match(result.help, /finish timeout/);
});

test("runCapture marks finish_error while retaining completed counters when normal finish throws", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html"],
    sessionToken: "session-1",
    request: async (stage) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item") return { ok: true, created_count: 1 };
      if (stage === "finish") throw new Error("finish network error");
    }
  });
  assert.equal(result.created_count, 1);
  assert.equal(result.captured_count, 1);
  assert.match(result.finish_error, /finish network error/);
});

test("runCapture marks finish_error when normal finish returns a retryable HTTP failure", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html"],
    sessionToken: "session-1",
    request: async (stage) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item") return { ok: true, created_count: 1 };
      if (stage === "finish") return { ok: false, status: 503, error: "finish unavailable" };
    }
  });
  assert.equal(result.created_count, 1);
  assert.match(result.finish_error, /finish unavailable/);
});

test("runCapture treats finish 404 as an already-gone batch that does not need recovery", async () => {
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: ["https://detail.1688.com/offer/1.html"],
    sessionToken: "session-1",
    request: async (stage) => {
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item") return { ok: true, created_count: 1 };
      if (stage === "finish") return { ok: false, status: 404, error: "batch not found" };
    }
  });
  assert.equal(result.created_count, 1);
  assert.equal(result.finish_error, undefined);
  assert.equal(result.finish_not_found, true);
});

test("fatal item response stops further dispatch while still finishing the batch", async () => {
  const itemCalls = [];
  const calls = [];
  const result = await PageCapture.runCapture({
    pageUrl: "https://s.1688.com/selloffer/offer_search.htm",
    sourceUrls: [1, 2, 3, 4, 5].map((id) => `https://detail.1688.com/offer/${id}.html`),
    sessionToken: "session-1",
    request: async (stage, body) => {
      calls.push(stage);
      if (stage === "prepare") return { batch_token: "batch-1" };
      if (stage === "start") return { ok: true };
      if (stage === "item") {
        itemCalls.push(body.source_url);
        if (body.source_url.endsWith("/1.html")) return { ok: false, status: 429, error: "quota exhausted" };
        return { ok: true };
      }
      if (stage === "finish") return { ok: true };
    }
  });

  assert.ok(itemCalls.length <= 3);
  assert.equal(calls.at(-1), "finish");
  assert.equal(result.unprocessed_count, 5 - itemCalls.length);
  assert.equal(result.failed_count, 1);
});

test("start gate rejects duplicate starts atomically and releases after finish", () => {
  const gate = PageCapture.createStartGate();
  assert.equal(gate.begin("batch-1"), true);
  assert.equal(gate.begin("batch-1"), false);
  gate.release("batch-1");
  assert.equal(gate.begin("batch-1"), true);
});

test("tab-scoped cancellation never falls back to jobs in another tab", () => {
  const jobs = [{ source_tab_id: 1 }, { source_tab_id: 2 }];
  assert.deepEqual(PageCapture.jobsForCancellation(jobs, 1), [jobs[0]]);
  assert.deepEqual(PageCapture.jobsForCancellation(jobs, 9), []);
  assert.deepEqual(PageCapture.jobsForCancellation(jobs, null), jobs);
});

test("connection identity requires the same workbench base and session token", () => {
  const identity = { http_base: "http://127.0.0.1:8010", session_token: "session-1" };
  assert.equal(PageCapture.connectionIdentityMatches(identity, identity), true);
  assert.equal(PageCapture.connectionIdentityMatches(identity, { ...identity, session_token: "session-2" }), false);
  assert.equal(PageCapture.connectionIdentityMatches(identity, { ...identity, http_base: "http://127.0.0.1:8011" }), false);
});

test("singleflight shares one in-flight page repair per tab and releases after completion", async () => {
  const gate = PageCapture.createSingleflight();
  let releaseFirst;
  let calls = 0;
  const first = gate.run(1688, () => {
    calls += 1;
    return new Promise((resolve) => { releaseFirst = resolve; });
  });
  const duplicate = gate.run(1688, () => {
    calls += 1;
    return Promise.resolve("duplicate");
  });

  assert.equal(calls, 1);
  assert.equal(first, duplicate);
  releaseFirst("repaired");
  assert.equal(await duplicate, "repaired");

  assert.equal(await gate.run(1688, async () => {
    calls += 1;
    return "next";
  }), "next");
  assert.equal(calls, 2);
});

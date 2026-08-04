import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";

const require = createRequire(import.meta.url);
const extensionRoot = path.resolve(import.meta.dirname, "../../../wh_local/price_verification/plugin/extension");
const {
  isAllowedQuoteResponse,
  isLocalBridgeUrl,
  collectAllowedQuoteRecords,
  sanitizeQuoteRecord,
  sanitizeResult,
} = require(path.join(extensionRoot, "network_probe_utils.js"));
const { extractTemuQuoteRows } = require(path.join(extensionRoot, "page_probe.js"));

test("quote filter excludes write endpoint", () => {
  assert.equal(
    isAllowedQuoteResponse({ url: "https://seller.temu.com/api/price/accept" }),
    false,
  );
  assert.equal(
    isAllowedQuoteResponse({ url: "https://seller.temu.com/api/bargain-no-bom/batch/info/query" }),
    true,
  );
  assert.equal(
    isAllowedQuoteResponse({ url: "https://evil.example/api/batch/info/query" }),
    false,
  );
  for (const path of ["/api/price/reject", "/api/price/cancel", "/api/price/modify", "/api/cart/query", "/api/set-price"]) {
    assert.equal(isAllowedQuoteResponse({ url: `https://seller.temu.com${path}` }), false, path);
  }
});

test("quote evidence retains read-only JSON while redacting sensitive values", () => {
  const record = sanitizeQuoteRecord({
    url: "https://seller.temu.com/api/bargain-no-bom/batch/info/query?access_token=secret&keep=1",
    status: 200,
    capturedAt: "2026-08-04T09:30:00Z",
    responseJson: {
      data: { priceReviewItemList: [{ skcId: "SKC-1", token: "secret" }] },
      authorization: "Bearer secret",
      api_key: "secret",
    },
  });

  assert.equal(record.url, "https://seller.temu.com/api/bargain-no-bom/batch/info/query?keep=1");
  assert.equal(record.responseJson.data.priceReviewItemList[0].token, "***REDACTED***");
  assert.equal(record.responseJson.authorization, "***REDACTED***");
  assert.equal(record.responseJson.api_key, "***REDACTED***");
});

test("bridge targets are limited to local loopback HTTP origins", () => {
  assert.equal(isLocalBridgeUrl("https://127.0.0.1:8000"), true);
  assert.equal(isLocalBridgeUrl("https://localhost:8000"), true);
  assert.equal(isLocalBridgeUrl("http://127.0.0.1:8000"), false);
  assert.equal(isLocalBridgeUrl("https://bridge.example"), false);
  assert.equal(isLocalBridgeUrl("http://127.0.0.2:8000"), false);
});

test("response capture filters platform writes and redacts credential text before delivery", () => {
  assert.deepEqual(
    collectAllowedQuoteRecords([
      {
        url: "https://seller.temu.com/api/bargain-no-bom/batch/info/query?sid=private",
        method: "POST",
        status: 200,
        responseJson: { note: "Bearer secret-token", data: { priceReviewItemList: [] } },
      },
      {
        url: "https://seller.temu.com/api/price/reject",
        method: "POST",
        status: 200,
        responseJson: { data: { priceReviewItemList: [] } },
      },
    ]),
    [{
      url: "https://seller.temu.com/api/bargain-no-bom/batch/info/query",
      status: 200,
      capturedAt: "",
      responseJson: { note: "Bearer [REDACTED]", data: { priceReviewItemList: [] } },
    }],
  );
});

test("sanitization removes credential text from every retained string", () => {
  const result = sanitizeResult({
    message: "authorization=Bearer private-token",
    records: [{
      url: "https://seller.temu.com/api/price/query",
      status: 200,
      responseJson: { note: "api_key=private-key", title: "Safe title" },
    }],
    items: [{
      task_key: "task-1",
      candidates: [{ source_url: "https://detail.1688.com/offer/1.html?note=api_key=private-key" }],
    }],
  });
  assert.equal(result.message, "authorization=[REDACTED]");
  assert.equal(result.records[0].responseJson.note, "api_key=[REDACTED]");
  assert.equal(result.records[0].responseJson.title, "Safe title");
  assert.equal(result.items[0].candidates[0].source_url, "https://detail.1688.com/offer/1.html?note=api_key=[REDACTED]");
});

test("result sanitization rejects unsupported fields and redacts nested credentials", () => {
  assert.deepEqual(
    sanitizeResult({
      items: [{ quote_key: "SKC-1:SKU-1", cookies: "private", candidates: [] }],
      debug: "discard me",
    }),
    { items: [{ quote_key: "SKC-1:SKU-1", cookies: "***REDACTED***", candidates: [] }] },
  );
});

test("quote result sanitization preserves only normalizer-compatible DOM evidence", () => {
  assert.deepEqual(
    sanitizeResult({
      dom: {
        dialog_present: true,
        rows: [{
          source: "batch_price_popup",
          cellsByHeader: { "SKU ID": "SKU-1", password: "private" },
          html: "discard me",
        }],
      },
      cookies: "discard me",
    }),
    {
      dom: {
        dialog_present: true,
        rows: [{
          source: "batch_price_popup",
          cellsByHeader: { "SKU ID": "SKU-1", password: "***REDACTED***" },
        }],
      },
    },
  );
});

test("confirmed Temu popup rows produce stable read-only DOM evidence", () => {
  const root = {
    querySelectorAll: () => [
      {
        dataset: { source: "batch_price_popup" },
        innerText: "SKC ID: SKC-1001\nSKU ID: SKU-2001\n原申报价格(CNY): ¥20.00\n调整后申报价格(CNY): ¥18.90",
      },
    ],
  };

  assert.deepEqual(extractTemuQuoteRows(root, { popupConfirmed: true }), [{
    source: "batch_price_popup",
    cellsByHeader: {
      "SKC ID": "SKC-1001",
      "SKU ID": "SKU-2001",
      "原申报价格(CNY)": "20.00",
      "调整后申报价格(CNY)": "18.90",
    },
  }]);
});

test("worker injects only the page probe helpers into inspected tabs", () => {
  const worker = fs.readFileSync(path.join(extensionRoot, "background.js"), "utf8");
  assert.match(worker, /files:\s*\["network_probe_utils\.js", "page_probe\.js"\]/);
  assert.match(worker, /collectAllowedQuoteRecords\(captured\)/);
  assert.match(worker, /world:\s*"MAIN"/);
});

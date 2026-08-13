import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const backgroundPath = new URL("./background.js", import.meta.url);
const contentPath = new URL("./content.js", import.meta.url);

test("核价采集 busy 锁不被 1.8 秒刷新提前释放", async () => {
  const source = await readFile(contentPath, "utf8");
  assert.match(source, /let priceQuoteCaptureBusy = false;/);
  assert.match(source, /function refreshPriceQuoteCaptureButton\(\) \{[\s\S]*?if \(priceQuoteCaptureBusy\) return;[\s\S]*?safeStorageGet\(\["connectionContext"\], \(settings\) => \{\s*if \(priceQuoteCaptureBusy\) return;/);
  assert.match(source, /async function capturePriceQuotePageToWorkbench\(\) \{\s*if \(priceQuoteCaptureBusy\) return;/);
  assert.match(source, /priceQuoteCaptureBusy = Boolean\(busy\);/);
  assert.match(source, /async function captureProductListToWorkbench\(\) \{\s*if \(productListCaptureBusy\) return;/);
});

test("核价 DOM 保留当前页行并在 500 行上限明确失败", async () => {
  const source = await readFile(backgroundPath, "utf8");
  assert.match(source, /const PRICE_QUOTE_DOM_ROW_LIMIT = 500;/);
  assert.match(source, /row_truncated: rowTruncated/);
  assert.match(source, /超过安全上限/);
  assert.doesNotMatch(source, /extractedDom\.rows\.slice\(0, 50\)/);
  assert.doesNotMatch(source, /rows: rows\.slice\(0, 120\)/);
});

test("货源命令不会在 runCommand 前上报 sent", async () => {
  const source = await readFile(backgroundPath, "utf8");
  const executeStart = source.indexOf("async function executeCommand");
  const runStart = source.indexOf("async function runCommand", executeStart);
  assert.ok(executeStart >= 0 && runStart > executeStart);
  const executeSource = source.slice(executeStart, runStart);
  assert.doesNotMatch(executeSource, /postResult\([^\n]+"sent"/);
  assert.match(executeSource, /runCommand\(baseUrl, sessionToken, command\)/);
});

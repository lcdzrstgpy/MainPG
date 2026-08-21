import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const content = readFileSync(new URL("./content.js", import.meta.url), "utf8");
const manifest = JSON.parse(readFileSync(new URL("./manifest.json", import.meta.url), "utf8"));
const installNotes = readFileSync(new URL("./安装说明.txt", import.meta.url), "utf8");

function stateHelpersFromContent(source) {
  const start = source.indexOf("// ONEBOUND_STATE_HELPERS_START");
  const end = source.indexOf("// ONEBOUND_STATE_HELPERS_END");
  assert.ok(start >= 0 && end > start, "content must expose a bounded, executable Onebound state-helper section");
  const helperSource = source.slice(start, end);
  const context = {};
  vm.runInNewContext(`${helperSource}\nglobalThis.helpers = { createOneboundPageCaptureState, canUseOneboundPageCaptureState, applyOneboundPageCapturePrepareState, applyOneboundPageCaptureProgressState, applyOneboundPageCaptureFinalState, oneboundFinalHasUsableSummary, oneboundPreparedCancelRequest, oneboundRetryPrepareRequest };`, context);
  return context.helpers;
}

const helpers = stateHelpersFromContent(content);
const state = helpers.createOneboundPageCaptureState("https://s.1688.com/offer/list", 7);
helpers.applyOneboundPageCapturePrepareState(state, {
  ok: false, total_count: 60, existing_count: 0, pending_count: 0,
  statusText: "批次登记失败：数据库升级未完成"
});
assert.equal(state.scanTotal, 60, "a prepare failure must retain the number of links already scanned");
assert.equal(state.statusText, "批次登记失败：数据库升级未完成");
state.batchToken = "batch-7";
state.scanTotal = 18;
state.workTotal = 4;
state.pendingTotal = 4;
helpers.applyOneboundPageCaptureProgressState(state, {
  total: 4, completed: 3, created_count: 1, refreshed_count: 2,
  skipped_count: 1, failed_count: 0, unprocessed_count: 1
});
assert.equal(state.scanTotal, 18, "progress work total must not overwrite the prepared scan total");
assert.equal(state.workTotal, 4, "progress total is the capture work total");
assert.equal(state.pendingTotal, 4, "progress must not overwrite the prepared pending total");
assert.equal(state.createdCount, 1, "created count must be retained independently");
assert.equal(state.refreshedCount, 2, "refreshed count must be retained independently");
assert.equal(state.capturedCount, 3, "success total includes created plus refreshed when captured_count is absent");
assert.equal(
  helpers.canUseOneboundPageCaptureState(state, "https://s.1688.com/offer/list", 6, "batch-7"),
  false,
  "a response from an old navigation generation must be ignored"
);
assert.equal(
  helpers.canUseOneboundPageCaptureState(state, "https://s.1688.com/other", 7, "batch-7"),
  false,
  "a response for a previous URL must be ignored"
);
helpers.applyOneboundPageCaptureFinalState(state, {
  captured_count: 5, created_count: 2, refreshed_count: 3, skipped_count: 1,
  failed_count: 2, unprocessed_count: 1, failed_urls: ["https://detail.1688.com/offer/1.html", "https://detail.1688.com/offer/1.html"]
});
assert.equal(state.createdCount, 2);
assert.equal(state.refreshedCount, 3);
assert.equal(JSON.stringify(state.failedUrls), JSON.stringify(["https://detail.1688.com/offer/1.html"]));
assert.equal(
  helpers.oneboundFinalHasUsableSummary({
    ok: false, error: "onebound_page_capture_start_failed", captured_count: 0,
    created_count: 0, refreshed_count: 0, skipped_count: 0, failed_count: 0, unprocessed_count: 3
  }),
  false,
  "a start failure with placeholder counters must render as an error"
);
assert.equal(
  helpers.oneboundFinalHasUsableSummary({ ok: false, error: "onebound_page_capture_finish_failed", created_count: 1 }),
  true,
  "a finish failure after item processing must retain its partial summary"
);
assert.equal(
  JSON.stringify(helpers.oneboundRetryPrepareRequest(state.failedUrls)),
  JSON.stringify({ type: "PREPARE_1688_ONEBOUND_PAGE_CAPTURE", source_urls: ["https://detail.1688.com/offer/1.html"] }),
  "retry preparation must send only failed URLs"
);
assert.equal(
  JSON.stringify(helpers.oneboundPreparedCancelRequest("batch-7")),
  JSON.stringify({ type: "CANCEL_PRODUCT_BATCH_CAPTURE", batch_token: "batch-7" }),
  "prepared confirmation cancellation must identify the recoverable backend batch"
);
assert.equal(manifest.version, "0.1.124", "manifest version must be 0.1.124");
assert.match(installNotes, /v0\.1\.124/);
assert.match(installNotes, /整页采集/);

assert.match(
  content,
  /function is1688ProductListPage\(\)/,
  "1688 list pages need an explicit branch from the existing Temu/Alibaba batch path"
);
assert.match(
  content,
  /"PREPARE_1688_ONEBOUND_PAGE_CAPTURE"/,
  "the first 1688 click must prepare a page-capture batch"
);
assert.match(
  content,
  /"START_1688_ONEBOUND_PAGE_CAPTURE"[\s\S]{0,240}batch_token/,
  "automatic capture must start the prepared batch with its token"
);
const prepareFlow = content.slice(
  content.indexOf("async function prepare1688OneboundPageCapture"),
  content.indexOf("async function cancelPrepared1688OneboundPageCapture")
);
assert.doesNotMatch(
  prepareFlow,
  /await start1688OneboundPageCapture\(\);/,
  "link registration must wait for the workbench OneBound start button"
);
assert.match(prepareFlow, /请到工作台点击“启动万邦采集”/, "the plugin must direct users to the workbench start control");
assert.doesNotMatch(content, /确认采集/, "the 1688 page itself must not show a second confirmation button");
assert.match(
  content,
  /"ONEBOUND_PAGE_CAPTURE_PROGRESS"/,
  "content UI must listen for live batch progress"
);
assert.match(
  content,
  /failed_urls/,
  "final state must retain failed URLs for retry-only preparation"
);
assert.match(
  content,
  /整页采集/,
  "1688 list button label must be 整页采集"
);
assert.match(
  content,
  /temu-workbench-page-capture-status/,
  "non-blocking in-page capture status card must be rendered"
);
assert.match(content, /已存在 \$\{oneboundPageCapture\.existingCount\}/, "prepare summary must describe duplicate drafts as existing, not already ingested");
assert.doesNotMatch(content, /扫描\/识别总数[^`\n]*已入池/, "prepare summary must not claim links were ingested before OneBound runs");
assert.match(
  content,
  /cancelPrepared1688OneboundPageCapture[\s\S]*?if \(!response\?\.ok\)/,
  "prepared cancellation must keep the UI recoverable when the backend does not accept cancellation"
);
assert.match(content, /let priceQuoteCaptureBusy = false;/, "Temu price capture busy guard must remain intact");
assert.match(
  content,
  /function refreshPriceQuoteCaptureButton\(\)[\s\S]*?if \(priceQuoteCaptureBusy\) return;/,
  "periodic refresh must not re-enable Temu price capture while a request is running"
);
assert.match(
  content,
  /function invalidateOneboundPageCaptureForNavigation\(\)[\s\S]*?oneboundPreparedCancelRequest\(stale\.batchToken\)/,
  "SPA navigation must best-effort close a prepared backend batch"
);

console.log("content page-capture contract: PASS");

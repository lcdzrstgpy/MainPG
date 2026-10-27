import assert from "node:assert/strict";
import test from "node:test";

import {
  applyReplicaAnalysis,
  buildReplicaBatchRequest,
  buildReplicaTargetRequest,
  copyReplicaListingInfo,
  createEmptyReplicaTarget,
  deepCopyReplicaTarget,
  firstInvalidReplicaTargetIndex,
  isReplicaAnalysisReady,
  isReplicaTargetComplete,
  markReplicaAnalysisAnalyzing,
  markReplicaAnalysisFailed,
  nextReplicaClientId,
  replicaAnalysesReady,
  replicaAnalysisBlockReason,
  replicaFeeEstimate,
  replicaFeeEstimateText,
  removeReplicaTarget,
  validateReplicaTargetDraft,
  type ReplicaTargetDraft,
} from "./podReplicaModel.ts";
import type { ReplicaAnalyzeResponse } from "../types.ts";

/** 构造一份可通过校验的目标产品：白底图 + 名称 + 类目 + 售价 + 合法 SKU + 已填尺寸 + 已就绪方案。 */
function validTarget(overrides: Partial<ReplicaTargetDraft> = {}): ReplicaTargetDraft {
  const base = createEmptyReplicaTarget("client-1");
  return {
    ...base,
    assetId: "asset_target",
    filename: "cushion.png",
    width: 800,
    height: 800,
    productName: "抱枕",
    listingFields: {
      title_mode: "long",
      category_name: "抱枕",
      skus: [{ name: "40cm", declared_price: "6", weight_g: "300" }],
    },
    specCard: {
      enabled: true,
      style: "light",
      corner: "bottom-right",
      display_unit: "cm",
      cells: [
        ["SKU", "Length", "Width", "Height"],
        ["40cm", "40", "40", "10"],
      ],
    },
    // 提交门禁：该款主图方案必须已就绪。
    analysisId: "plan-1",
    analysisStatus: "ready",
    ...overrides,
  };
}

function analysisResponse(overrides: Partial<ReplicaAnalyzeResponse> = {}): ReplicaAnalyzeResponse {
  return {
    analysis_id: "plan-1",
    status: "succeeded",
    source_asset_id: "asset_source",
    target_asset_id: "asset_target",
    prompt_version: "pod-replica-main-prompt-v1",
    model: "m",
    cached: false,
    ...overrides,
  };
}

test("empty target starts blank with one SKU and a fresh stable client id", () => {
  const target = createEmptyReplicaTarget();
  assert.ok(target.clientId.startsWith("pod-replica-"));
  assert.equal(target.assetId, "");
  assert.equal(target.productName, "");
  assert.equal(target.analysisId, "");
  assert.equal(target.analysisStatus, "pending");
  assert.equal(target.listingFields.skus.length, 1);
  assert.equal(target.listingFields.skus[0].name, "");
  assert.notEqual(nextReplicaClientId(), nextReplicaClientId());
});

test("field validation requires name, asset, ready plan, listing fields and a configured spec card", () => {
  assert.equal(validateReplicaTargetDraft(validTarget()).ok, true);

  const missingName = validTarget({ productName: "  " });
  assert.deepEqual(validateReplicaTargetDraft(missingName), { ok: false, error: "请填写产品名称。" });

  const missingAsset = validTarget({ assetId: "" });
  assert.deepEqual(validateReplicaTargetDraft(missingAsset), { ok: false, error: "请先上传该产品的白底图。" });

  const pendingPlan = validTarget({ analysisId: "", analysisStatus: "pending" });
  assert.deepEqual(validateReplicaTargetDraft(pendingPlan), { ok: false, error: "正在等待主图方案生成。" });

  // 类目必填复用全定制 listingFieldsForApi 的既有口径。
  const missingCategory = validTarget({
    listingFields: { title_mode: "long", category_name: "", skus: [{ name: "40cm", declared_price: "6", weight_g: "300" }] },
  });
  assert.deepEqual(validateReplicaTargetDraft(missingCategory), { ok: false, error: "请填写店小秘类目。" });

  const badPrice = validTarget({
    listingFields: { title_mode: "long", category_name: "抱枕", skus: [{ name: "40cm", declared_price: "0", weight_g: "300" }] },
  });
  assert.equal(validateReplicaTargetDraft(badPrice).ok, false);

  const badSpecCard = validTarget({
    specCard: { enabled: true, style: "light", corner: "bottom-right", display_unit: "cm", cells: [["SKU", "Length", "Width", "Height"], ["40cm", "", "", ""]] },
  });
  assert.equal(validateReplicaTargetDraft(badSpecCard).ok, false);

  assert.equal(isReplicaTargetComplete(validTarget()), true);
  assert.equal(firstInvalidReplicaTargetIndex([validTarget(), missingName]), 1);
});

test("buildReplicaBatchRequest keeps target order, snake_case shape and only new-contract fields", () => {
  const first = validTarget({ clientId: "a", assetId: "asset_a", productName: "抱枕" });
  const second = validTarget({ clientId: "b", assetId: "asset_b", productName: "收纳篮" });
  const request = buildReplicaBatchRequest({
    client_request_id: "req-1",
    source_asset_id: "asset_source",
    title: "包图案迁移",
    composition_id: "comp-1",
    targets: [first, second],
  });

  assert.deepEqual(Object.keys(request), [
    "client_request_id", "source_asset_id", "title", "creative_prompt", "composition_id", "targets",
  ]);
  assert.equal(request.creative_prompt, "");
  assert.equal(request.composition_id, "comp-1");
  // 旧契约字段（source_confirmed / generation_strategy）不再上报。
  assert.equal(Object.prototype.hasOwnProperty.call(request, "source_confirmed"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(request, "generation_strategy"), false);

  assert.deepEqual(request.targets.map((target) => target.target_asset_id), ["asset_a", "asset_b"]);
  assert.deepEqual(request.targets.map((target) => target.product_name), ["抱枕", "收纳篮"]);
  assert.deepEqual(request.targets.map((target) => target.analysis_id), ["plan-1", "plan-1"]);
  assert.deepEqual(Object.keys(request.targets[0]), ["target_asset_id", "product_name", "listing_fields", "analysis_id"]);
  assert.equal(request.targets[0].listing_fields.skus[0].declared_price, 6);
  assert.equal(request.targets[0].listing_fields.spec_card?.cells[1][1], "40");
});

test("target request always carries analysis_id and refuses a missing plan", () => {
  const listing = validateReplicaTargetDraft(validTarget());
  assert.equal(listing.ok, true);
  if (!listing.ok) return;
  assert.equal(buildReplicaTargetRequest(validTarget(), listing.listingFields).analysis_id, "plan-1");
  assert.throws(
    () => buildReplicaTargetRequest(validTarget({ analysisId: "", analysisStatus: "pending" }), listing.listingFields),
    /主图方案缺失/,
  );
});

test("buildReplicaBatchRequest refuses an invalid target and names its position", () => {
  assert.throws(
    () => buildReplicaBatchRequest({ client_request_id: "req-1", source_asset_id: "asset_source", targets: [] }),
    /至少添加一个目标产品/,
  );
  assert.throws(
    () => buildReplicaBatchRequest({ client_request_id: "req-1", source_asset_id: "asset_source", targets: [validTarget(), validTarget({ productName: "" })] }),
    /第 2 个产品有误：请填写产品名称。/,
  );
});

test("copying another product's listing info deep-copies SKUs and spec card while keeping identity", () => {
  const source = validTarget({ clientId: "source", assetId: "asset_source", productName: "来源产品" });
  const target = validTarget({ clientId: "target", assetId: "asset_target", productName: "目标产品", analysisId: "plan-target" });
  const copied = copyReplicaListingInfo(source, target);

  // 保留当前产品的图片、名称、类目、方案与稳定 client ID。
  assert.equal(copied.clientId, "target");
  assert.equal(copied.assetId, "asset_target");
  assert.equal(copied.productName, "目标产品");
  assert.equal(copied.analysisId, "plan-target");
  assert.equal(copied.listingFields.category_name, "抱枕");

  // 上架信息 / SKU / 规格卡来自来源且为深拷贝。
  assert.notEqual(copied.listingFields.skus, source.listingFields.skus);
  copied.listingFields.skus[0].name = "改动";
  copied.specCard.cells[1][1] = "999";
  assert.equal(source.listingFields.skus[0].name, "40cm");
  assert.equal(source.specCard.cells[1][1], "40");
});

test("deepCopyReplicaTarget and removeReplicaTarget do not mutate the original list", () => {
  const original = [validTarget({ clientId: "a" }), validTarget({ clientId: "b" })];
  const [clone] = [deepCopyReplicaTarget(original[0])];
  assert.notEqual(clone, original[0]);
  assert.notEqual(clone.specCard.cells, original[0].specCard.cells);
  assert.equal(clone.analysisId, "plan-1");
  assert.deepEqual(removeReplicaTarget(original, "a").map((target) => target.clientId), ["b"]);
  assert.equal(original.length, 2);
});

test("applying a plan response moves the target to ready or failed without exposing facts", () => {
  const base = createEmptyReplicaTarget("c1");

  const ready = applyReplicaAnalysis(base, analysisResponse());
  assert.equal(ready.analysisStatus, "ready");
  assert.equal(ready.analysisId, "plan-1");
  assert.equal(ready.analysisError, "");
  assert.equal(isReplicaAnalysisReady(ready), true);
  assert.deepEqual(Object.keys(ready).filter((key) => key.startsWith("source") || key.startsWith("target")), []);
  assert.equal(Object.prototype.hasOwnProperty.call(ready, "candidateCapabilities"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(ready, "presentationPreferences"), false);

  const failed = applyReplicaAnalysis(base, analysisResponse({ status: "failed", analysis_id: "" }));
  assert.equal(failed.analysisStatus, "failed");
  assert.ok(failed.analysisError);

  // 生成中不覆盖上一份可用方案 id，失败重试后仍能回退到可用方案。
  const analyzing = markReplicaAnalysisAnalyzing({ ...base, analysisId: "plan-1", analysisStatus: "ready" });
  assert.equal(analyzing.analysisStatus, "analyzing");
  assert.equal(analyzing.analysisId, "plan-1");
  assert.equal(markReplicaAnalysisFailed(analyzing, "boom").analysisStatus, "failed");
});

test("submit gate requires every target to have a ready main-image plan", () => {
  const pending = validTarget({ analysisStatus: "pending", analysisId: "" });
  const analyzing = validTarget({ analysisStatus: "analyzing" });
  const failed = validTarget({ analysisStatus: "failed" });

  assert.equal(replicaAnalysesReady([validTarget()]), true);
  assert.equal(replicaAnalysesReady([validTarget(), pending]), false);
  assert.equal(replicaAnalysesReady([]), false);

  assert.equal(replicaAnalysisBlockReason(pending), "正在等待主图方案生成。");
  assert.equal(replicaAnalysisBlockReason(analyzing), "正在生成主图方案，请稍候。");
  assert.equal(replicaAnalysisBlockReason(failed), "主图方案生成失败，请重试。");
  assert.equal(replicaAnalysisBlockReason(validTarget()), "");
  assert.equal(validateReplicaTargetDraft(pending).ok, false);
});

test("fee estimate follows the unified 100-points-per-style replica price", () => {
  assert.deepEqual(replicaFeeEstimate(0), { min: 0, max: 0 });
  assert.deepEqual(replicaFeeEstimate(2), { min: 200, max: 200 });
  assert.equal(replicaFeeEstimateText(3), "约 300 积分");
});

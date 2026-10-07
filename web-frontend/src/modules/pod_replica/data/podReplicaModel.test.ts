import assert from "node:assert/strict";
import test from "node:test";

import {
  buildReplicaBatchRequest,
  copyReplicaListingInfo,
  createEmptyReplicaTarget,
  deepCopyReplicaTarget,
  firstInvalidReplicaTargetIndex,
  isReplicaTargetComplete,
  nextReplicaClientId,
  replicaFeeEstimate,
  replicaFeeEstimateText,
  removeReplicaTarget,
  validateReplicaTargetDraft,
  type ReplicaTargetDraft,
} from "./podReplicaModel.ts";

/** 构造一份可通过校验的目标产品：白底图 + 名称 + 类目 + 售价 + 一个合法 SKU + 已填尺寸。 */
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
      suggested_price_usd: "19.99",
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
    ...overrides,
  };
}

test("empty target starts blank with one SKU and a fresh stable client id", () => {
  const target = createEmptyReplicaTarget();
  assert.ok(target.clientId.startsWith("pod-replica-"));
  assert.equal(target.assetId, "");
  assert.equal(target.productName, "");
  assert.equal(target.listingFields.skus.length, 1);
  assert.equal(target.listingFields.skus[0].name, "");
  assert.notEqual(nextReplicaClientId(), nextReplicaClientId());
});

test("field validation requires product name, category, SKU numbers and a configured spec card", () => {
  assert.equal(validateReplicaTargetDraft(validTarget()).ok, true);

  const missingName = validTarget({ productName: "  " });
  assert.deepEqual(validateReplicaTargetDraft(missingName), { ok: false, error: "请填写产品名称。" });

  const missingAsset = validTarget({ assetId: "" });
  assert.deepEqual(validateReplicaTargetDraft(missingAsset), { ok: false, error: "请先上传该产品的白底图。" });

  // 类目必填复用全定制 listingFieldsForApi 的既有口径。
  const missingCategory = validTarget({
    listingFields: { title_mode: "long", suggested_price_usd: "19.99", category_name: "", skus: [{ name: "40cm", declared_price: "6", weight_g: "300" }] },
  });
  assert.deepEqual(validateReplicaTargetDraft(missingCategory), { ok: false, error: "请填写店小秘类目。" });

  // SKU 申报价 / 重量约束同样复用 helper。
  const badPrice = validTarget({
    listingFields: { title_mode: "long", suggested_price_usd: "19.99", category_name: "抱枕", skus: [{ name: "40cm", declared_price: "0", weight_g: "300" }] },
  });
  assert.equal(validateReplicaTargetDraft(badPrice).ok, false);

  const badSpecCard = validTarget({
    specCard: { enabled: true, style: "light", corner: "bottom-right", display_unit: "cm", cells: [["SKU", "Length", "Width", "Height"], ["40cm", "", "", ""]] },
  });
  const specResult = validateReplicaTargetDraft(badSpecCard);
  assert.equal(specResult.ok, false);

  assert.equal(isReplicaTargetComplete(validTarget()), true);
  assert.equal(firstInvalidReplicaTargetIndex([validTarget(), missingName]), 1);
});

test("buildReplicaBatchRequest keeps target order, snake_case shape and an empty creative prompt", () => {
  const first = validTarget({ clientId: "a", assetId: "asset_a", productName: "抱枕" });
  const second = validTarget({ clientId: "b", assetId: "asset_b", productName: "收纳篮" });
  const request = buildReplicaBatchRequest({
    client_request_id: "req-1",
    source_asset_id: "asset_source",
    title: "包图案迁移",
    targets: [first, second],
  });

  assert.equal(request.client_request_id, "req-1");
  assert.equal(request.source_asset_id, "asset_source");
  assert.equal(request.title, "包图案迁移");
  assert.equal(request.creative_prompt, "");
  assert.deepEqual(request.targets.map((target) => target.target_asset_id), ["asset_a", "asset_b"]);
  assert.deepEqual(request.targets.map((target) => target.product_name), ["抱枕", "收纳篮"]);
  assert.equal(request.targets[0].listing_fields.suggested_price_usd, 19.99);
  assert.equal(request.targets[0].listing_fields.skus[0].declared_price, 6);
  assert.equal(request.targets[0].listing_fields.spec_card?.cells[1][1], "40");
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
  const target = validTarget({ clientId: "target", assetId: "asset_target", productName: "目标产品" });
  const copied = copyReplicaListingInfo(source, target);

  // 保留当前产品的图片、名称、类目和稳定 client ID。
  assert.equal(copied.clientId, "target");
  assert.equal(copied.assetId, "asset_target");
  assert.equal(copied.productName, "目标产品");
  assert.equal(copied.listingFields.category_name, "抱枕");

  // 上架信息 / SKU / 规格卡来自来源且为深拷贝。
  assert.equal(copied.listingFields.suggested_price_usd, source.listingFields.suggested_price_usd);
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
  assert.deepEqual(removeReplicaTarget(original, "a").map((target) => target.clientId), ["b"]);
  assert.equal(original.length, 2);
});

test("fee estimate follows the full-custom per-product billing range", () => {
  assert.deepEqual(replicaFeeEstimate(0), { min: 0, max: 0 });
  assert.deepEqual(replicaFeeEstimate(2), { min: 80, max: 100 });
  assert.equal(replicaFeeEstimateText(3), "约 120–150 积分");
});

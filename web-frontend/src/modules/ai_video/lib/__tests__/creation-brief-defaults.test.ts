import assert from "node:assert/strict";
import test from "node:test";

import {
  coerceStyleTypeForInputMode,
  defaultAudioStrategyFor,
  resolveStyleSource,
  validateCreationBriefForm,
} from "../creation-brief-defaults.ts";

test("upload 来源必须同时有商品名与至少一张图", () => {
  const missing = validateCreationBriefForm({ productName: "", images: [], inputMode: "upload", strategyChosen: true });
  assert.equal(missing.valid, false);
  assert.ok(missing.errors.productName);
  assert.ok(missing.errors.images);

  const ok = validateCreationBriefForm({ productName: "桂花乌龙", images: ["/api/files/a.png"], inputMode: "upload", strategyChosen: true });
  assert.deepEqual(ok, { valid: true, errors: {} });
});

test("链接已导入时不再强制本地图片，未导入时仍然要求", () => {
  assert.equal(
    validateCreationBriefForm({ productName: "茶", images: [], inputMode: "link", linkImported: true, strategyChosen: true }).valid,
    true,
  );
  assert.ok(
    validateCreationBriefForm({ productName: "茶", images: [], inputMode: "link", linkImported: false, strategyChosen: true })
      .errors.images,
  );
});

test("一句话主题只校验主题文本", () => {
  assert.ok(validateCreationBriefForm({ productName: "", images: [], inputMode: "topic", topic: "  ", strategyChosen: true }).errors.topic);
  assert.equal(
    validateCreationBriefForm({ productName: "", images: [], inputMode: "topic", topic: "手冲咖啡", strategyChosen: true }).valid,
    true,
  );
});

test("未点过出片策略卡时不放行", () => {
  const result = validateCreationBriefForm({
    productName: "茶",
    images: ["/api/files/a.png"],
    inputMode: "upload",
    strategyChosen: false,
  });
  assert.equal(result.valid, false);
  assert.ok(result.errors.outputStrategy);
});

test("切到主题来源时把带货风格收敛到旁白词表", () => {
  assert.equal(coerceStyleTypeForInputMode("drama", "topic"), "knowledge");
  assert.equal(coerceStyleTypeForInputMode("travel", "topic"), "travel");
  assert.equal(coerceStyleTypeForInputMode("drama", "upload"), "drama");
});

test("策略决定默认音源，风格来源区分 auto 与显式选择", () => {
  assert.equal(defaultAudioStrategyFor("native-film"), "native-audio");
  assert.equal(defaultAudioStrategyFor("draft"), "volcengine-tts");
  assert.equal(defaultAudioStrategyFor("controlled-motion"), "volcengine-tts");

  assert.equal(resolveStyleSource({ styleType: "auto" }), "performance-recommendation");
  assert.equal(resolveStyleSource({ styleType: "drama" }), "explicit");
  assert.equal(resolveStyleSource({ styleType: "drama", templateId: "t1" }), "template");
});

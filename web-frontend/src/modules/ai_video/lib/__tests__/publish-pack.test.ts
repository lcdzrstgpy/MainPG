import assert from "node:assert/strict";
import test from "node:test";

import { buildAiDeclaration, buildPublishPack } from "../publish-pack.ts";

test("模板发布文案：同输入稳定输出，标题 3 条、话题不超过 10 个", () => {
  const input = {
    productName: "云朵宠物窝",
    category: "home",
    sellingPoints: "高围边包裹感，长毛绒可拆洗，猫咪整只蜷进去",
    platform: "douyin",
    locale: "zh" as const,
  };
  const first = buildPublishPack(input);
  const second = buildPublishPack(input);

  assert.deepEqual(first.titles, second.titles);
  assert.equal(first.titles.length, 3);
  assert.ok(first.hashtags.length >= 6 && first.hashtags.length <= 10);
  assert.equal(first.hashtags[0], "#云朵宠物窝");
  assert.ok(first.hashtags.includes("#家居好物"));
  assert.ok(first.hashtags.includes("#抖音好物"));
  assert.match(first.caption, /点下方小黄车/);
});

test("模板发布文案：无卖点时仍生成标题，评论区带合规提示", () => {
  const pack = buildPublishPack({ productName: "桌面收纳盒", category: "other", locale: "zh" });
  assert.equal(pack.titles.length, 3);
  assert.equal(pack.commentKit.objections.length, 3);
  assert.match(pack.commentKit.notice, /伪造顾客证言/);
  assert.match(pack.aiDeclaration.line, /AI 生成/);
});

test("AIGC 声明跟随语言", () => {
  assert.match(buildAiDeclaration("en").line, /AI-generated/);
  assert.match(buildAiDeclaration("zh").notice, /内容由 AI 生成/);
});

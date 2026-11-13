import assert from "node:assert/strict";
import test from "node:test";

import { explainPublishError, shortReasonLabel } from "./errorExplain.ts";

test("失败原因翻译：真实台账里的 8 类都要说人话 + 修法", () => {
  const cases: Array<[string, RegExp, RegExp]> = [
    [
      "bg.glo.goods.add: 货品类目属性更新，请刷新页面后重新创建货品",
      /属性和当前类目对不上/,
      /产品属性.*重选/,
    ],
    [
      "bg.glo.goods.add: 重量不符合类目2~1000000g限制，无法售卖",
      /重量超出/,
      /kg 填进了 g/,
    ],
    [
      "bg.glo.goods.add: Preview image cannot be empty;",
      /主图.*空的/,
      /至少要有一张图/,
    ],
    ["bg.glo.goods.add: 所选类目不合法", /类目选错/, /叶子类目/],
    [
      "bg.glo.goods.add: 属性[颜色]校验错误:关键词属性 默认 不是有效值。，请刷新页面后重新创建货品",
      /「颜色」/,
      /预设值/,
    ],
    ["bg.glo.goods.add: 非服饰类目spu轮播图最少为3张", /不足 3 张/, /补到至少 3 张/],
    ["bg.glo.goods.add: 当前承诺发货时效暂不支持", /发货时效/, /换一档/],
    [
      "bg.glo.goods.add: 体积内容必须遵循最长边 ≥ 次长边 ≥ 最短边原则",
      /长宽高顺序/,
      /自动按长短排序/,
    ],
  ];
  for (const [raw, humanPattern, fixPattern] of cases) {
    const explained = explainPublishError(raw);
    assert.ok(explained, `没翻译出来：${raw}`);
    assert.match(explained.human, humanPattern, `人话不对：${raw}`);
    assert.match(explained.fix, fixPattern, `修法不对：${raw}`);
  }
});

test("属性名要带进人话，不是笼统说「某个属性」", () => {
  const explained = explainPublishError(
    "bg.glo.goods.add: 属性[尺码]校验错误:关键词属性 均码 不是有效值。",
  );
  assert.match(explained!.human, /「尺码」/);
  assert.match(explained!.fix, /「尺码」/);
});

test("不认识的错误照原文，不瞎猜（返回 null）", () => {
  assert.equal(explainPublishError("bg.glo.goods.add: 神秘的新错误，以前没见过"), null);
  assert.equal(explainPublishError(""), null);
});

test("归堆 chips 用短标签：剥掉 bg.glo 前缀，能翻译就用人话", () => {
  assert.equal(
    shortReasonLabel("bg.glo.goods.add: 货品类目属性更新，请刷新页面后重新创建货品"),
    "产品属性和当前类目对不上（类目可能换了，或属性值已失效）",
  );
  assert.equal(
    shortReasonLabel("bg.glo.goods.add: 神秘的新错误"),
    "神秘的新错误",
  );
});

test("货号类错误也要说人话（2026-11-12 实测踩到 1000002）", () => {
  const bad = explainPublishError(
    "bg.glo.goods.add: 货号不规范，请使用字母、数字和标点符号维护货号！",
  );
  assert.ok(bad, "货号不规范必须能翻译");
  assert.match(bad!.human, /货号/);
  assert.match(bad!.fix, /字母\/数字/);

  // 只带错误码也要认
  assert.ok(explainPublishError("提交失败（错误码 1000002）"));

  const dup = explainPublishError("SKU ext_code 重复：PP-000199-C1");
  assert.ok(dup);
  assert.match(dup!.fix, /SKU-001/);
});

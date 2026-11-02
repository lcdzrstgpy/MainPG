import assert from "node:assert/strict";
import test from "node:test";

import {
  autoFillAttributePicks,
  autoFillSourceTexts,
  describeAutoFill,
} from "./attrAutofill.ts";
import type { AttrPickMap } from "./attributeModel.ts";
import type { TemuAttributes, TemuProperty } from "../types.ts";

function prop(input: Partial<TemuProperty> & { pid: number }): TemuProperty {
  return {
    required: true,
    values: [],
    ...input,
  } as TemuProperty;
}

function attrs(properties: TemuProperty[]): TemuAttributes {
  return { properties } as TemuAttributes;
}

const SHAPE = prop({
  pid: 101,
  name: "形状",
  values: [
    { vid: 1, value: "矩形" },
    { vid: 2, value: "圆形" },
  ],
});

const MATERIAL = prop({
  pid: 102,
  name: "材质类型",
  values: [
    { vid: 11, value: "棉" },
    { vid: 12, value: "涤纶" },
    { vid: 13, value: "亚麻" },
    { vid: 14, value: "其他" },
  ],
});

const THICKNESS = prop({
  pid: 103,
  name: "厚度",
  values: [],
  valueUnit: ["mm"],
});

const COLOR = prop({
  pid: 104,
  name: "颜色",
  isSale: true,
  values: [{ vid: 21, value: "米白" }],
});

const EMPTY: AttrPickMap = {};

test("按标题点名的属性值自动填，只填空位", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([SHAPE, MATERIAL]),
    picks: EMPTY,
    // 「棉」和「亚麻」都在文本里 → 材质判断不了；「矩形」唯一 → 形状可以填
    texts: ["棉与亚麻混纺窗帘 矩形款"],
  });

  assert.equal(result.picks[101]?.vid, 1, "矩形应命中形状");
  assert.equal(result.picks[102], undefined, "棉和亚麻同时命中，判断不了，不能乱选");
  assert.deepEqual(result.ambiguous, ["材质类型"], "多命中要报出来，让用户自己选");
  assert.equal(result.hits.filter((hit) => hit.source === "text").length, 1);
});

test("标题只点名一个值时才填，命中两个就留空", () => {
  const single = autoFillAttributePicks({
    attributes: attrs([MATERIAL]),
    picks: EMPTY,
    texts: ["纯棉窗帘"],
  });
  assert.equal(single.picks[102]?.vid, 11);

  const multiple = autoFillAttributePicks({
    attributes: attrs([MATERIAL]),
    picks: EMPTY,
    texts: ["纯棉和涤纶混纺"],
  });
  assert.equal(multiple.picks[102], undefined);
  assert.deepEqual(multiple.ambiguous, ["材质类型"]);
});

test("「其他/无」这类兜底值不会被文本命中", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([MATERIAL]),
    picks: EMPTY,
    // 「其他」出现在文本里，也不能因为它而选中「其他」
    texts: ["其他材质说明见详情"],
  });
  assert.equal(result.picks[102], undefined);
});

test("已有值不覆盖：用户填的、存档恢复的都保持原样", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([SHAPE]),
    picks: { 101: { vid: 2, text: "" } },
    texts: ["矩形窗帘"],
  });
  assert.equal(result.picks[101]?.vid, 2);
  assert.equal(result.hits.length, 0);
});

test("销售属性不参与自动填（由销售规格承载）", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([COLOR]),
    picks: EMPTY,
    texts: ["米白色窗帘"],
  });
  assert.equal(result.picks[104], undefined);
});

test("纯数值属性不编造：没有可选值时一律跳过", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([THICKNESS]),
    picks: EMPTY,
    texts: ["厚度 5mm 窗帘"],
  });
  assert.equal(result.picks[103], undefined);
});

test("同类目上次填过的优先复用，且要 vid 当前仍合法", () => {
  const reused = autoFillAttributePicks({
    attributes: attrs([SHAPE, MATERIAL]),
    picks: EMPTY,
    texts: [],
    history: { 101: { vid: 2, text: "" }, 102: { vid: 11, text: "" } },
  });
  assert.equal(reused.picks[101]?.vid, 2);
  assert.equal(reused.picks[102]?.vid, 11);
  assert.equal(reused.hits.every((hit) => hit.source === "history"), true);

  // Temu 改了可选值：旧的 vid 不在列表里就不能硬塞
  const stale = autoFillAttributePicks({
    attributes: attrs([SHAPE]),
    picks: EMPTY,
    texts: [],
    history: { 101: { vid: 99, text: "" } },
  });
  assert.equal(stale.picks[101], undefined);
});

test("历史优先于文本：两者都指向不同值时不摇摆", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([SHAPE]),
    picks: EMPTY,
    texts: ["矩形窗帘"],
    history: { 101: { vid: 2, text: "" } },
  });
  assert.equal(result.picks[101]?.vid, 2, "历史值优先，文本不能把它改掉");
});

test("纯 ASCII 值按词边界匹配：PU 不该命中 input", () => {
  const pu = prop({
    pid: 105,
    name: "材质",
    values: [
      { vid: 31, value: "PU" },
      { vid: 32, value: "Canvas" },
    ],
  });
  const wrong = autoFillAttributePicks({
    attributes: attrs([pu]),
    picks: EMPTY,
    texts: ["input support only"],
  });
  assert.equal(wrong.picks[105], undefined);

  const right = autoFillAttributePicks({
    attributes: attrs([pu]),
    picks: EMPTY,
    texts: ["PU leather tote bag"],
  });
  assert.equal(right.picks[105]?.vid, 31);
});

test("摘要文案区分「沿用上次」和「按标题匹配」", () => {
  const result = autoFillAttributePicks({
    attributes: attrs([SHAPE, MATERIAL]),
    picks: EMPTY,
    texts: ["矩形窗帘"],
    history: { 102: { vid: 12, text: "" } },
  });
  const summary = describeAutoFill(result.hits);
  assert.match(summary, /1 项沿用上次同类目的填法/);
  assert.match(summary, /1 项按标题\/描述匹配/);
});

test("文本来源去空去重", () => {
  assert.deepEqual(
    autoFillSourceTexts({ title: " 窗帘 ", titleEn: "", description: "窗帘", categoryNames: ["", "家纺"] }),
    ["窗帘", "家纺"],
  );
});

import assert from "node:assert/strict";
import test from "node:test";

import { defaultNumberValue, estimateAttributePicks } from "./attrEstimate.ts";
import type { TemuAttributes, TemuProperty } from "../types.ts";

function prop(input: Partial<TemuProperty> & { pid: number }): TemuProperty {
  return { required: true, values: [], ...input } as TemuProperty;
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
  name: "材料",
  values: [
    { vid: 11, value: "其他" },
    { vid: 12, value: "棉" },
    { vid: 13, value: "涤纶" },
  ],
});

const THICKNESS = prop({ pid: 103, name: "厚度", values: [], valueUnit: ["mm"] });
const GSM = prop({ pid: 104, name: "平方克重", values: [], valueUnit: ["g/㎡"] });
const COLOR = prop({
  pid: 105,
  name: "颜色",
  isSale: true,
  values: [{ vid: 21, value: "米白" }],
});

test("每个必填项都填上值：没有依据的取第一个可用选项并标成「猜」", () => {
  const result = estimateAttributePicks({
    attributes: attrs([SHAPE, MATERIAL]),
    texts: [],
  });

  assert.equal(result.picks[101]?.vid, 1, "形状取第一个（矩形）");
  assert.equal(result.picks[102]?.vid, 12, "「其他」是兜底值，不能当第一个可用选项");
  assert.equal(result.guessed, 2);
  assert.equal(result.items.every((item) => item.source === "guess"), true);
});

test("优先级：同类目历史 > 标题点名 > 猜", () => {
  const result = estimateAttributePicks({
    attributes: attrs([SHAPE, MATERIAL]),
    texts: ["圆形棉质桌布"],
    history: { 102: { vid: 13, text: "" } },
  });

  assert.equal(result.picks[101]?.vid, 2, "形状：标题里点了「圆形」");
  assert.equal(result.picks[102]?.vid, 13, "材料：历史优先于标题里的「棉」");
  assert.equal(result.items.find((item) => item.pid === 101)?.source, "text");
  assert.equal(result.items.find((item) => item.pid === 102)?.source, "history");
  assert.equal(result.guessed, 0);
});

test("纯数值属性按属性名给常见默认值，且标 number-guess", () => {
  const result = estimateAttributePicks({
    attributes: attrs([THICKNESS, GSM]),
    texts: [],
  });

  assert.equal(result.picks[103]?.text, "1", "厚度默认 1mm");
  assert.equal(result.picks[104]?.text, "200", "平方克重默认 200");
  assert.equal(result.items.every((item) => item.source === "number-guess"), true);
  assert.equal(result.guessed, 2);
});

test("带单位的属性除了下拉还要给数值，否则必填校验照样拦", () => {
  const ratio = prop({
    pid: 106,
    name: "成分含量",
    values: [{ vid: 31, value: "棉" }],
    valueUnit: ["%"],
  });
  const result = estimateAttributePicks({ attributes: attrs([ratio]), texts: [] });

  assert.equal(result.picks[106]?.vid, 31);
  assert.equal(result.picks[106]?.text, "100", "成分默认 100%");
});

test("销售属性不填（由销售规格承载）", () => {
  const result = estimateAttributePicks({ attributes: attrs([COLOR]), texts: ["米白"] });
  assert.equal(result.picks[105], undefined);
});

test("已有值不覆盖：用户填的 / 带入的保持原样", () => {
  const result = estimateAttributePicks({
    attributes: attrs([SHAPE]),
    texts: [],
    keep: { 101: { vid: 2, text: "" } },
  });

  assert.equal(result.picks[101]?.vid, 2);
  assert.equal(result.items.length, 0);
});

test("联动子属性也能填上：父项定了值之后子项才出现，第二轮补", () => {
  const parent = prop({
    pid: 201,
    name: "材质",
    templatePid: 900,
    values: [
      { vid: 1, value: "布料" },
      { vid: 2, value: "塑料" },
    ],
  });
  const child = prop({
    pid: 202,
    name: "厚度",
    templatePid: 901,
    parentTemplatePid: 900,
    values: [],
    valueUnit: ["mm"],
    templatePropertyValueParentList: [{ parentVidList: [1], vidList: [] }],
  });

  const result = estimateAttributePicks({ attributes: attrs([parent, child]), texts: [] });

  assert.equal(result.picks[201]?.vid, 1);
  assert.equal(result.picks[202]?.text, "1", "父项选了「布料」后，厚度才出现并被估上");
});

test("既没可选值也没单位（自由文本型）估不出来，列进 manual", () => {
  const free = prop({ pid: 301, name: "型号" });
  const result = estimateAttributePicks({ attributes: attrs([free]), texts: [] });

  assert.equal(result.picks[301], undefined);
  assert.deepEqual(result.manual, ["型号"]);
});

test("属性名默认值规则：对不上时给 1，别给 0（0 会被当成没填）", () => {
  assert.equal(defaultNumberValue("厚度"), 1);
  assert.equal(defaultNumberValue("平方克重"), 200);
  assert.equal(defaultNumberValue("成分含量"), 100);
  assert.equal(defaultNumberValue("层数"), 1);
  assert.equal(defaultNumberValue("莫名字段"), 1);
});

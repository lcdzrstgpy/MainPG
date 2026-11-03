import assert from "node:assert/strict";
import test from "node:test";

import {
  attributeDiffText,
  buildAttributeGroups,
  describeAttributeDiff,
  missingRequiredNames,
  pruneStaleAttrPicks,
  remapAttrPicks,
  visibleProperties,
} from "./attributeModel.ts";
import type { TemuAttributes, TemuProperty } from "../types.ts";

/**
 * 这些是**行为测试**（直接调函数），不是源码正则断言。
 *
 * 可见性/分组规则原来是发布页里的一坨 useMemo，只靠文本断言守着；
 * 现在抽成纯函数，两边共用，正好可以喂数据验行为。
 */

function prop(patch: Partial<TemuProperty> & { pid: number }): TemuProperty {
  return {
    required: false,
    values: [],
    ...patch,
  } as TemuProperty;
}

/** 父属性「是否带电」+ 子属性「电池类型」，父选「不带电」时子属性不该出现。 */
function catalog(): TemuAttributes {
  return {
    properties: [
      prop({
        pid: 1,
        name: "是否带电池",
        templatePid: 100,
        required: true,
        values: [
          { vid: 10, value: "不带电" },
          { vid: 11, value: "带电" },
        ],
      }),
      prop({
        pid: 2,
        name: "电池类型",
        templatePid: 200,
        parentTemplatePid: 100,
        required: true,
        values: [
          { vid: 21, value: "锂电" },
          { vid: 22, value: "干电池" },
        ],
        templatePropertyValueParentList: [{ parentVidList: [11], vidList: [21] }],
      }),
    ],
  } as TemuAttributes;
}

test("联动：父属性没选，子属性完全不可见（即使 required）", () => {
  const visible = visibleProperties(catalog(), {});
  assert.deepEqual(
    visible.map((item) => item.pid),
    [1],
  );
});

test("联动：父值不匹配时子属性仍然不出现", () => {
  const visible = visibleProperties(catalog(), { 1: { vid: 10, text: "" } });
  assert.deepEqual(
    visible.map((item) => item.pid),
    [1],
  );
});

test("联动：父值命中后子属性出现，且可选值被 vidList 收窄", () => {
  const visible = visibleProperties(catalog(), { 1: { vid: 11, text: "" } });
  const child = visible.find((item) => item.pid === 2);
  assert.ok(child, "父值命中后子属性必须出现");
  assert.deepEqual(
    child!.values.map((value) => value.vid),
    [21],
  );
});

test("分组：子属性挂到父属性下，不进必填/选填根列表", () => {
  const attrs = catalog();
  const visible = visibleProperties(attrs, { 1: { vid: 11, text: "" } });
  const groups = buildAttributeGroups(attrs, visible);

  assert.deepEqual(
    groups.required.map((item) => item.pid),
    [1],
    "必填根项只留没有父属性的那些",
  );
  assert.deepEqual(
    (groups.children.get(1) ?? []).map((item) => item.pid),
    [2],
  );
});

test("必填判定：带单位的属性只选下拉不算填好", () => {
  const attrs = {
    properties: [
      prop({
        pid: 9,
        name: "成分比例",
        required: true,
        valueUnit: ["%"],
        numberInputTitle: "请输入数值",
        values: [{ vid: 91, value: "棉" }],
      }),
    ],
  } as TemuAttributes;

  assert.deepEqual(missingRequiredNames(visibleProperties(attrs, {}), {}), ["成分比例"]);
  // 只选了下拉、没填数值 → 仍然算缺
  assert.deepEqual(missingRequiredNames(visibleProperties(attrs, {}), { 9: { vid: 91, text: "" } }), [
    "成分比例",
  ]);
  // 数值也填了才算完整
  assert.deepEqual(missingRequiredNames(visibleProperties(attrs, {}), { 9: { vid: 91, text: "60" } }), []);
});

test("必填判定：valueUnit 是空串数组时按「无单位」处理，只选下拉就算填好", () => {
  // Temu 对无单位属性返回 valueUnit: [""] —— 长度是 1，但那是空串。
  // 用 valueUnit.length 判断会把它当成「必须填数值」，而界面按 valueUnit[0]
  // 根本不给数值框，结果就是这 7 个纯下拉属性永远报「缺少必填属性」。
  const attrs = {
    properties: [
      prop({ pid: 11, name: "形状", required: true, valueUnit: [""], values: [{ vid: 110, value: "方形" }] }),
      prop({ pid: 12, name: "编织类型", required: true, values: [{ vid: 120, value: "机器制作" }] }),
      prop({ pid: 13, name: "平方克重", required: true, valueUnit: [], values: [{ vid: 130, value: "70-80g" }] }),
    ],
  } as TemuAttributes;

  // 三个都只选下拉、不填数值 —— 一个都不该算缺
  assert.deepEqual(
    missingRequiredNames(visibleProperties(attrs, {}), {
      11: { vid: 110, text: "" },
      12: { vid: 120, text: "" },
      13: { vid: 130, text: "" },
    }),
    [],
  );

  // 真带单位的仍然要求数值（不能被这次修复放宽）
  const withUnit = {
    properties: [
      prop({ pid: 14, name: "厚度", required: true, valueUnit: ["mm"], values: [{ vid: 140, value: "10" }] }),
      prop({ pid: 15, name: "成分比例", required: true, valueUnit: ["%"], values: [{ vid: 150, value: "棉" }] }),
    ],
  } as TemuAttributes;
  assert.deepEqual(
    missingRequiredNames(visibleProperties(withUnit, {}), {
      14: { vid: 140, text: "" },
      15: { vid: 150, text: "" },
    }),
    ["厚度", "成分比例"],
  );
});

test("pruneStaleAttrPicks：草稿存的旧 vid 在当前属性表里查不到就清掉", () => {
  const attrs = {
    properties: [
      prop({
        pid: 21,
        name: "材料",
        required: true,
        values: [
          { vid: 210, value: "棉" },
          { vid: 211, value: "涤纶" },
        ],
      }),
      prop({ pid: 22, name: "颜色", required: true, values: [{ vid: 220, value: "粉" }] }),
      // 纯输入型属性：没有选项表，只能靠 text
      prop({ pid: 23, name: "护理说明", required: true, values: [] }),
    ],
  } as TemuAttributes;

  const { picks, dropped } = pruneStaleAttrPicks(
    {
      21: { vid: 210, text: "" }, // 还在 → 留
      22: { vid: 999, text: "" }, // vid 已下线 → 丢（典型：下拉框空白却算已填）
      23: { vid: 0, text: "手洗" }, // 文本型 → 留
      24: { vid: 240, text: "" }, // 当前类目根本没这个 pid → 丢
    },
    attrs,
  );

  assert.deepEqual(Object.keys(picks).sort(), ["21", "23"]);
  assert.deepEqual(dropped.sort(), ["pid=24", "颜色"].sort());
  assert.ok(dropped.includes("颜色"));
  assert.ok(dropped.includes("pid=24"));
});

test("pruneStaleAttrPicks：空值/空文本也算失效，别当成「填了」", () => {
  const attrs = {
    properties: [prop({ pid: 31, name: "材料", required: true, values: [{ vid: 310, value: "棉" }] })],
  } as TemuAttributes;
  const { picks, dropped } = pruneStaleAttrPicks(
    { 31: { vid: 0, text: "   " }, 32: { vid: 0, text: "" } },
    attrs,
  );
  assert.deepEqual(picks, {});
  assert.equal(dropped.length, 2);
});

test("pruneStaleAttrPicks：属性表还没拿到时不误杀（返回空但不算丢）", () => {
  const { picks, dropped } = pruneStaleAttrPicks({ 41: { vid: 410, text: "" } }, null);
  assert.deepEqual(picks, {});
  // null 属性表下无法判断有效性 → 记为丢弃，由调用方决定不展示提示
  assert.deepEqual(dropped, ["pid=41"]);
});

test("describeAttributeDiff：只报真正变了的项，没变就说没变", () => {
  // Temu 回 6000002 时，光说「重新选一遍」是废话 —— 用户打开属性区看跟原来一样，
  // 只能盲选再被拒一次。这里锁定「点名变更」的行为。
  const base = {
    properties: [
      prop({ pid: 1, name: "材料", required: true, values: [{ vid: 10, value: "棉" }] }),
      prop({ pid: 2, name: "颜色", required: true, values: [{ vid: 20, value: "粉" }] }),
    ],
  } as TemuAttributes;
  const next = {
    properties: [
      // 材料：原来的选值 10 没了，换成 11
      prop({ pid: 1, name: "材料", required: true, values: [{ vid: 11, value: "涤纶" }] }),
      // 颜色：还在，vid 没变
      prop({ pid: 2, name: "颜色", required: true, values: [{ vid: 20, value: "粉" }] }),
      // 新增必填
      prop({ pid: 3, name: "护理说明", required: true, values: [] }),
      // 新增选填：不算「新增必填」
      prop({ pid: 4, name: "产地", required: false, values: [] }),
    ],
  } as TemuAttributes;

  const diff = describeAttributeDiff(base, next, {
    1: { vid: 10, text: "" },
    2: { vid: 20, text: "" },
  });
  assert.deepEqual(diff.addedRequired, ["护理说明"]);
  assert.deepEqual(diff.staleChoices, ["材料"]);
  assert.deepEqual(diff.removed, []);

  const text = attributeDiffText(diff);
  assert.ok(text.includes("新增必填「护理说明」"), text);
  assert.ok(text.includes("「材料」原来的选择已失效"), text);

  // 完全没变化 → 空串（调用方据此改说「重选也没用」）
  assert.equal(attributeDiffText(describeAttributeDiff(next, next, {})), "");

  // 旧表有、新表删掉的属性也要说
  const removedDiff = describeAttributeDiff(base, { properties: [next.properties[1]] } as TemuAttributes, {});
  assert.ok(removedDiff.removed.includes("材料"));
  assert.ok(attributeDiffText(removedDiff).includes("Temu 已经删掉了"));
});

test("remapAttrPicks：只是 vid 被重建、选值文字没变 → 自动对上，不用用户重选", () => {
  // Temu 改类目属性最常见的其实就是重建 vid（选项本身没动）。
  // 之前一律丢掉 → 让用户「从头重选一遍」，而大部分值根本没变。
  const before = {
    properties: [prop({ pid: 1, name: "材料", required: true, values: [{ vid: 10, value: "棉" }] })],
  } as TemuAttributes;
  const after = {
    properties: [prop({ pid: 1, name: "材料", required: true, values: [{ vid: 999, value: "棉" }] })],
  } as TemuAttributes;

  const out = remapAttrPicks(before, after, { 1: { vid: 10, text: "" } });
  assert.deepEqual(out.picks[1], { vid: 999, text: "" });
  assert.deepEqual(out.remapped, ["材料"]);
  assert.deepEqual(out.dropped, []);

  // 选项真的被改名/删掉 → 对不上，只能让用户重选
  const renamed = {
    properties: [prop({ pid: 1, name: "材料", required: true, values: [{ vid: 888, value: "亚麻" }] })],
  } as TemuAttributes;
  const miss = remapAttrPicks(before, renamed, { 1: { vid: 10, text: "" } });
  assert.deepEqual(miss.dropped, ["材料"]);
  assert.deepEqual(miss.remapped, []);
  assert.deepEqual(miss.picks, {});
});

test("showCondition：父属性取值不满足时，子属性既不显示也不该发", () => {
  // 实测 2026-11-03（桌布类目 10498）：
  // 「厚度（mm）」带 showCondition = [{parentRefPid: 121, parentVids: [2512]}]，
  // 而 refPid=121 是「材料」。材料选了塑料(2148) 就不该出现「厚度」——
  // 以前只认 templatePropertyValueParentList，这条被完全忽略，
  // 于是界面照显示、payload 照发，Temu 回一句「货品类目属性更新」（6000002）。
  const attrs = {
    properties: [
      prop({
        pid: 89,
        refPid: 121,
        name: "材料",
        required: true,
        values: [
          { vid: 2148, value: "塑料" },
          { vid: 2512, value: "橡胶" },
        ],
      }),
      prop({
        pid: 2056,
        refPid: 6933,
        name: "厚度（mm）",
        required: true,
        valueUnit: ["mm"],
        values: [],
        showCondition: [{ parentRefPid: 121, parentVids: [2512] }],
      }),
    ],
  } as TemuAttributes;

  const names = (picks: Record<number, { vid: number; text: string }>) =>
    visibleProperties(attrs, picks).map((item) => item.name);

  // 材料=塑料 → 厚度不出现
  assert.ok(!names({ 89: { vid: 2148, text: "" } }).includes("厚度（mm）"));
  // 材料=橡胶(2512) → 厚度出现
  assert.ok(names({ 89: { vid: 2512, text: "" } }).includes("厚度（mm）"));
  // 父属性没选 → 也不出现（不能靠猜）
  assert.ok(!names({}).includes("厚度（mm）"));
});

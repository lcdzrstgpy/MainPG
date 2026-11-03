import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAttributeGroups,
  missingRequiredNames,
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

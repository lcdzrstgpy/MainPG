import type { TemuAttributes } from "../types";
import { PLACEHOLDER_VALUE, containsText, hasMatchableText } from "./attrAutofill.ts";
import { visibleProperties, type AttrPickMap } from "./attributeModel.ts";

/**
 * 产品属性的「估填」：在入草稿库时把**每个**必填属性都给一个值。
 *
 * 和发布页的 {@link ../data/attrAutofill} 不同：那边「判断不了就留空」，
 * 这边是用户明确要求「全都要填上，给估计的」，所以增加了兜底：
 *
 * ```
 * 同类目上次填的 → 标题/描述里点名的 → **第一个可用选项**（这一档就是猜的）
 * ```
 *
 * 猜的必须能被认出来：每一项都带 `source`，界面按来源显示不同标记，
 * 用户看了一眼就知道哪些要自己核对。
 *
 * 纯数值属性（厚度 mm、平方克重 g/㎡）没有可选值，只能按属性名给常见默认值
 * （见 {@link NUMBER_DEFAULT_RULES}），全部标 `number-guess`。
 */

export type EstimateSource = "history" | "text" | "guess" | "number-guess";

export type EstimateItem = {
  pid: number;
  name: string;
  vid: number;
  /** 带单位属性的数值；下拉属性为空 */
  text: string;
  /** 展示用：选中的值（数值属性带单位） */
  value: string;
  source: EstimateSource;
};

export type EstimateResult = {
  picks: AttrPickMap;
  items: EstimateItem[];
  /** 纯猜的项数（没有历史、标题里也没有点名） */
  guessed: number;
  /** 既没有可选值也没有单位、估不出来的属性名（人工必须补） */
  manual: string[];
};

/**
 * 纯数值属性的常见默认值。
 *
 * 这些值是「行业常识档位」，不是这家货的真实参数 —— 只为了让必填校验能过，
 * 用户在发布页核对时必须改。顺序敏感：先匹配到的赢。
 */
const NUMBER_DEFAULT_RULES: Array<[RegExp, number]> = [
  [/厚度|thickness/i, 1],
  [/克重|平方克|克\/平方|gsm/i, 200],
  [/成分|含量|比例|percent|content/i, 100],
  [/层数|数量|个数|件数|只数|pcs|count|qty/i, 1],
  [/长度|高度|宽度|尺寸|length|height|width|size/i, 1],
];

/** 属性名对不上任何规则时给 1：至少能过必填，用户看到「1」也会知道要改。 */
export function defaultNumberValue(name: string): number {
  for (const [pattern, value] of NUMBER_DEFAULT_RULES) {
    if (pattern.test(name)) return value;
  }
  return 1;
}

/**
 * 把每个可见必填属性都填上值（含联动子属性）。
 *
 * 联动属性依赖父属性的取值，所以要多跑几轮：第一轮填父项，第二轮父值生效后
 * 子属性才出现、可选值也才被 `vidList` 收窄。轮数上限避免属性表异常时死循环。
 */
export function estimateAttributePicks(options: {
  attributes: TemuAttributes | null;
  texts: string[];
  /** 同类目上次在发布页填的（最可信） */
  history?: AttrPickMap;
  /** 已经填好的（用户手填 / 带入的）：绝不覆盖 */
  keep?: AttrPickMap;
  rounds?: number;
}): EstimateResult {
  const picks: AttrPickMap = { ...(options.keep ?? {}) };
  const items: EstimateItem[] = [];
  const manual: string[] = [];
  const haystack = options.texts.filter(Boolean).join("\n").toLowerCase();
  const rounds = Math.max(1, options.rounds ?? 4);

  const push = (item: EstimateItem) => {
    items.push(item);
    picks[item.pid] = { vid: item.vid, text: item.text };
  };

  for (let round = 0; round < rounds; round += 1) {
    let changed = false;
    for (const prop of visibleProperties(options.attributes, picks)) {
      const pid = Number(prop.pid ?? 0);
      if (!pid) continue;
      // 销售属性（颜色/尺码）由「销售规格」承载，属性区填了反而被判不合法
      if (prop.isSale) continue;
      const current = picks[pid];
      if (current && (current.vid > 0 || current.text.trim())) continue;

      const name = prop.name || prop.propertyName || `pid=${pid}`;
      const units = prop.valueUnit ?? [];
      // 空串等于没有单位：Temu 会给无单位属性返回 [""]
      const unit = units.length ? String(units[0] ?? "").trim() : "";
      const usable = (prop.values ?? []).filter((value) => {
        const text = String(value.value ?? "").trim();
        return !!text && !PLACEHOLDER_VALUE.test(text) && hasMatchableText(text);
      });

      // ① 有可选值：历史 → 文本唯一命中 → 第一个（猜）
      if (usable.length) {
        const historyVid = Number(options.history?.[pid]?.vid ?? 0);
        const kept =
          historyVid > 0 ? usable.find((value) => Number(value.vid) === historyVid) : undefined;
        const matched = haystack
          ? usable.filter((value) => containsText(haystack, String(value.value ?? "")))
          : [];
        const chosen = kept ?? (matched.length === 1 ? matched[0] : usable[0]);
        const source: EstimateSource = kept ? "history" : matched.length === 1 ? "text" : "guess";
        // 带单位的属性（成分比例 %）除了下拉还要一个数值，否则必填校验照样拦
        const text = unit ? String(defaultNumberValue(name)) : "";
        push({
          pid,
          name,
          vid: Number(chosen.vid),
          text,
          value: `${String(chosen.value ?? "")}${text ? ` ${text}${unit}` : ""}`,
          source,
        });
        changed = true;
        continue;
      }

      // ② 没有可选值但有单位：纯数值，按属性名给常见默认值
      if (unit) {
        const value = defaultNumberValue(name);
        push({
          pid,
          name,
          vid: 0,
          text: String(value),
          value: `${value}${unit}`,
          source: "number-guess",
        });
        changed = true;
        continue;
      }

      // ③ 既没有可选值也没有单位（自由文本型）：估不出来，必须人工
      if (!manual.includes(name)) manual.push(name);
    }
    if (!changed) break;
  }

  const guessed = items.filter(
    (item) => item.source === "guess" || item.source === "number-guess",
  ).length;
  return { picks, items, guessed, manual };
}

import type { TemuAttributes } from "../types";
import {
  PLACEHOLDER_VALUE,
  attrNameToken,
  hasMatchableText,
  valueMatchScore,
} from "./attrAutofill.ts";
import { visibleProperties, type AttrPickMap } from "./attributeModel.ts";

/**
 * 产品属性的「估填」：在入成品库时把**每个**必填属性都给一个值（全自动）。
 *
 * 关键是「猜」的质量：瞎填第一个选项被 Temu 拒过（「默认」不是有效值），
 * 也把商品数据弄脏。所以证据链按强度排，能命中就不用猜：
 *
 * ```
 * 同类目历史 → 采集属性名对齐（材质=PU→材质）→ 变种规格值 → 文本同义打分
 *   → 跨类目同属性历史 → 第一个非占位选项（兜底，标 guess）
 * ```
 *
 * 猜的必须能被认出来：每一项都带 `source`，界面按来源显示不同标记。
 *
 * 纯数值属性（厚度 mm、平方克重 g/㎡）优先从采集属性里取真实数
 * （`attr`），取不到才按属性名给常见默认值（{@link NUMBER_DEFAULT_RULES}，标
 * `number-guess`）。
 */

export type EstimateSource = "history" | "attr" | "spec" | "text" | "guess" | "number-guess";

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

/** 从文本里抠第一个数字（「65%」→65、「0.5mm」→0.5）；抠不到返回 undefined */
export function numberFromText(text: string): number | undefined {
  const matched = String(text ?? "").match(/-?\d+(?:\.\d+)?/);
  if (!matched) return undefined;
  const value = Number(matched[0]);
  return Number.isFinite(value) ? value : undefined;
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
  /** 采集属性（1688 等来源的真实属性）：[{name, value}]，按属性名对齐当最强证据 */
  sourceAttrs?: Array<{ name: string; value: string }>;
  /** 变种规格值（颜色/尺码列表）：喂给对应属性当证据 */
  specTexts?: string[];
  /** 同类目上次在发布页填的（最可信） */
  history?: AttrPickMap;
  /** 跨类目同 pid 的最近一次真实选择（同类目历史没命中时的下一级证据） */
  historyByPid?: AttrPickMap;
  /** 失败教训黑名单：被 Temu 拒过的 (属性名, 值) 不再给 */
  badValue?: (name: string, value: string) => boolean;
  /** 已经填好的（用户手填 / 带入的）：绝不覆盖 */
  keep?: AttrPickMap;
  rounds?: number;
}): EstimateResult {
  const picks: AttrPickMap = { ...(options.keep ?? {}) };
  const items: EstimateItem[] = [];
  const manual: string[] = [];
  const sourceAttrs = (options.sourceAttrs ?? []).filter(
    (attr) => attr?.name && attr?.value,
  );
  const specHay = (options.specTexts ?? []).filter(Boolean).join("\n").toLowerCase();
  // 通用文本：标题/描述/类目名 + 采集属性值 + 变种值（全部证据都进池子）
  const haystack = [
    ...options.texts,
    ...sourceAttrs.map((attr) => attr.value),
    ...(options.specTexts ?? []),
  ]
    .filter(Boolean)
    .join("\n")
    .toLowerCase();
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
      const unit = units.length ? String(units[0] ?? "") : "";
      // 选填属性只在有证据时填：空着合法，瞎填只会弄脏数据（给 PU 包编「木材类型」这种）
      const evidenceOnly = !prop.required;
      const usable = (prop.values ?? []).filter((value) => {
        const text = String(value.value ?? "").trim();
        if (!text || PLACEHOLDER_VALUE.test(text) || !hasMatchableText(text)) return false;
        // 被 Temu 拒过的值不再给（失败教训黑名单）
        return !(options.badValue && options.badValue(name, text));
      });

      // ① 有可选值：证据链按强度排，能命中就不猜
      if (usable.length) {
        // 可选值里的「最合适者」：分高者赢、同分取更长（更具体的值）
        const bestOf = (hay: string) => {
          let best: { option: (typeof usable)[number]; score: number } | undefined;
          for (const option of usable) {
            const score = hay ? valueMatchScore(hay, String(option.value ?? "")) : 0;
            if (!score) continue;
            const len = String(option.value ?? "").length;
            const bestLen = String(best?.option.value ?? "").length;
            if (!best || score > best.score || (score === best.score && len > bestLen)) {
              best = { option, score };
            }
          }
          return best;
        };
        const historyVid = Number(options.history?.[pid]?.vid ?? 0);
        const kept =
          historyVid > 0 ? usable.find((value) => Number(value.vid) === historyVid) : undefined;
        // 采集属性名对齐（材质=PU → 属性「材质」）：最硬的外部证据
        const nameToken = attrNameToken(name);
        const alignedHay = nameToken
          ? sourceAttrs
              .filter((attr) => attrNameToken(attr.name) === nameToken)
              .map((attr) => attr.value)
              .join("\n")
              .toLowerCase()
          : "";
        const byAttr = bestOf(alignedHay);
        const bySpec = bestOf(specHay);
        const byText = bestOf(haystack);
        // 跨类目同 pid 历史（换类目遇到同一个属性，上次怎么填的照样有用）
        const pidHistoryVid = Number(options.historyByPid?.[pid]?.vid ?? 0);
        const keptByPid =
          pidHistoryVid > 0
            ? usable.find((value) => Number(value.vid) === pidHistoryVid)
            : undefined;
        // 全自动兜底：第一个**非占位**选项（「默认/其他」当答案必被 Temu 拒）
        const fallback =
          usable.find(
            (value) => !PLACEHOLDER_VALUE.test(String(value.value ?? "").trim()),
          ) ?? usable[0];
        const [chosen, source]: [(typeof usable)[number], EstimateSource] = kept
          ? [kept, "history"]
          : byAttr
            ? [byAttr.option, "attr"]
            : bySpec
              ? [bySpec.option, "spec"]
              : byText
                ? [byText.option, "text"]
                : keptByPid
                  ? [keptByPid, "history"]
                  : [fallback, "guess"];
        // 选填属性没证据就留空，别猜（只有必填才用兜底）
        if (evidenceOnly && source === "guess") continue;
        // 带单位的属性（成分比例 %）除了下拉还要一个数值，否则必填校验照样拦：
        // 能从采集属性里抠到真实数就用真实数
        const numberFromAttr = nameToken
          ? sourceAttrs
              .filter((attr) => attrNameToken(attr.name) === nameToken)
              .map((attr) => numberFromText(attr.value))
              .find((value) => value !== undefined)
          : undefined;
        const text = unit ? String(numberFromAttr ?? defaultNumberValue(name)) : "";
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

      // ② 没有可选值但有单位：纯数值，优先采集属性里的真实数，否则按属性名给常见默认值
      if (unit) {
        const nameToken = attrNameToken(name);
        const numberFromAttr = nameToken
          ? sourceAttrs
              .filter((attr) => attrNameToken(attr.name) === nameToken)
              .map((attr) => numberFromText(attr.value))
              .find((value) => value !== undefined)
          : undefined;
        const value = numberFromAttr ?? defaultNumberValue(name);
        // 选填的数值属性没真实数就别编（厚度=1mm 这种是自找麻烦）
        if (numberFromAttr === undefined && evidenceOnly) continue;
        push({
          pid,
          name,
          vid: 0,
          text: String(value),
          value: `${value}${unit}`,
          source: numberFromAttr !== undefined ? "attr" : "number-guess",
        });
        changed = true;
        continue;
      }

      // ③ 既没有可选值也没有单位（自由文本型）：估不出来，必填才进人工清单
      if (prop.required && !manual.includes(name)) manual.push(name);
    }
    if (!changed) break;
  }

  const guessed = items.filter(
    (item) => item.source === "guess" || item.source === "number-guess",
  ).length;
  return { picks, items, guessed, manual };
}

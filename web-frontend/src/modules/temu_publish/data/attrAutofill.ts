import type { TemuAttributes, TemuProperty } from "../types";
import type { AttrPickMap } from "./attributeModel";

/**
 * 产品属性的「自动填」。
 *
 * 接口（`attrs.get`）给的是**选择题的选项**，不是答案：它告诉你这个类目的「材料」
 * 可以是棉/涤纶/亚麻，但不会说这批货是哪个。所以能自动推的只有两条依据：
 *
 * 1. **同一个叶子类目上次怎么填的** —— 连发同款货时最准，vid 还在当前可选值里就直接沿用；
 * 2. **标题/描述里点名了某个可选值** —— 「棉麻窗帘」里的「棉」「麻」；只接受
 *    **唯一命中**，多个值同时出现说明判断不了，宁可留空也不猜。
 *
 * 刻意不做的：
 * - 纯数值属性（厚度 mm、平方克重 g/㎡）不填 —— 这类是商品实测值，编一个数比空着更糟；
 * - 销售属性（isSale，颜色/尺码）不填 —— 由「销售规格」承载，属性区里多填会被判不合法；
 * - 不覆盖任何已有选择 —— 自动填只填空位。
 */

export type AutoFillSource = "history" | "text";

export type AutoFillHit = {
  pid: number;
  name: string;
  vid: number;
  value: string;
  source: AutoFillSource;
};

export type AutoFillResult = {
  picks: AttrPickMap;
  hits: AutoFillHit[];
  /** 文本命中了多个可选值、判断不了选哪个的属性名（只提示，不猜） */
  ambiguous: string[];
};

/** 「其他 / 无 / N/A」这类兜底值不能靠文本命中，否则每个属性都会选中它。 */
export const PLACEHOLDER_VALUE = /^(其他|其它|other|others|无|none|n\/a|na|不适用|not applicable|未知|unknown)$/i;

function propertyLabel(item: TemuProperty): string {
  return item.name || item.propertyName || `pid=${item.pid}`;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * `needle` 是否出现在 `haystack`（已小写）里。
 *
 * 纯 ASCII 的值按词边界匹配：「PU」不能命中「input」，「cot」不能命中「cotton」。
 * 中文没有词边界概念，直接子串。
 */
export function containsText(haystack: string, needle: string): boolean {
  const raw = needle.trim();
  if (!raw) return false;
  const value = raw.toLowerCase();
  if (!/^[\x20-\x7e]+$/.test(raw)) return haystack.includes(value);
  const pattern = new RegExp(`(^|[^a-z0-9])${escapeRegExp(value)}([^a-z0-9]|$)`, "i");
  return pattern.test(haystack);
}

/** 值名里有没有可用字符：纯符号/纯标点的值匹配了也没意义。 */
export function hasMatchableText(text: string): boolean {
  return /[a-z0-9\u4e00-\u9fff]/i.test(text);
}

/** 把标题/描述/类目名整理成可匹配的文本数组（去空、去重）。 */
export function autoFillSourceTexts(input: {
  title?: string;
  titleEn?: string;
  description?: string;
  categoryNames?: string[];
}): string[] {
  const list = [
    input.title,
    input.titleEn,
    input.description,
    ...(input.categoryNames ?? []),
  ]
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(list));
}

/**
 * 按「同类目历史 + 文本点名」补空属性。
 *
 * @returns 新的 picks（不改入参）、填了哪些、以及判断不了的属性名
 */
export function autoFillAttributePicks(options: {
  attributes: TemuAttributes | null;
  picks: AttrPickMap;
  texts: string[];
  history?: AttrPickMap;
}): AutoFillResult {
  const picks: AttrPickMap = { ...options.picks };
  const hits: AutoFillHit[] = [];
  const ambiguous: string[] = [];
  const haystack = options.texts.filter(Boolean).join("\n").toLowerCase();

  for (const item of options.attributes?.properties ?? []) {
    const pid = Number(item.pid ?? 0);
    if (!pid) continue;
    // 销售属性（颜色/尺码）由「销售规格」承载，属性区再填一遍会被 Temu 判不合法
    if (item.isSale) continue;
    // 已经有值（用户填的 / 存档恢复的）绝不覆盖
    const current = picks[pid];
    if (current && (current.vid > 0 || current.text.trim())) continue;
    const values = item.values ?? [];
    // 纯数值属性（厚度 mm、平方克重）：没有可选值，不给编造
    if (!values.length) continue;

    // ① 同类目上次怎么填，这次照样填 —— 但只认当前依然合法的 vid
    const historyVid = Number(options.history?.[pid]?.vid ?? 0);
    if (historyVid > 0) {
      const kept = values.find((value) => Number(value.vid) === historyVid);
      if (kept) {
        picks[pid] = { vid: kept.vid, text: "" };
        hits.push({
          pid,
          name: propertyLabel(item),
          vid: kept.vid,
          value: String(kept.value ?? ""),
          source: "history",
        });
        continue;
      }
    }

    // ② 标题/描述里点名了某个可选值；只接受唯一命中
    if (!haystack) continue;
    const matched = values.filter((value) => {
      const text = String(value.value ?? "").trim();
      if (!text || PLACEHOLDER_VALUE.test(text) || !hasMatchableText(text)) return false;
      return containsText(haystack, text);
    });
    if (matched.length === 1) {
      picks[pid] = { vid: matched[0].vid, text: "" };
      hits.push({
        pid,
        name: propertyLabel(item),
        vid: matched[0].vid,
        value: String(matched[0].value ?? ""),
        source: "text",
      });
    } else if (matched.length > 1) {
      ambiguous.push(propertyLabel(item));
    }
  }

  return { picks, hits, ambiguous };
}

/** 提示条上的一句话摘要，例：3 项沿用上次「窗帘」类目的填法、1 项按标题匹配。 */
export function describeAutoFill(hits: AutoFillHit[]): string {
  if (!hits.length) return "";
  const history = hits.filter((hit) => hit.source === "history").length;
  const byText = hits.filter((hit) => hit.source === "text").length;
  const parts: string[] = [];
  if (history) parts.push(`${history} 项沿用上次同类目的填法`);
  if (byText) parts.push(`${byText} 项按标题/描述匹配`);
  return parts.join("、");
}

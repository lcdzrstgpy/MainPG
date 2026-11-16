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

/**
 * 「其他 / 无 / N/A / 默认」这类兜底值不能靠文本命中，更不能当猜测值填出去。
 * 「默认」是实测踩过的坑：关键词属性填「默认」直接被 Temu 拒（不是有效值）。
 */
export const PLACEHOLDER_VALUE =
  /^(其他|其它|other|others|无|none|n\/a|na|不适用|not applicable|未知|unknown|默认|默认项|常规|通用|标准|standard|default|自定义|custom|待定|暂无|敬请期待)$/i;

/**
 * 同义词归一：「涤纶」和「聚酯纤维」是同一个东西，文本里出现哪个都该算命中。
 * 只收 ≥2 字的词组（单字「麻」会命中「芝麻」，宁可漏不可错）。
 */
const SYNONYM_GROUPS: string[][] = [
  ["涤纶", "聚酯纤维", "polyester"],
  ["锦纶", "尼龙", "nylon"],
  ["纯棉", "全棉", "cotton"],
  ["亚麻", "linen", "flax"],
  ["pu", "聚氨酯", "人造革"],
  ["真皮", "头层牛皮", "leather"],
  ["不锈钢", "stainless"],
  ["玻璃", "glass"],
  ["实木", "原木", "solid wood"],
  ["塑料", "plastic"],
  // 常见图案/面料的英中对照：标题是英文、选项是中文时也能命中
  ["印花", "printed", "print"],
  ["纯色", "solid"],
  ["碎花", "花卉", "floral"],
  ["条纹", "striped", "stripe"],
  ["格子", "格纹", "plaid", "check"],
  ["豹纹", "leopard"],
  ["迷彩", "camouflage"],
  ["牛仔", "denim"],
  ["丝绒", "天鹅绒", "velvet"],
  ["雪纺", "chiffon"],
  ["缎面", "satin"],
];

/** 一个值的同义词（不含自己）；不在表里返回空。 */
export function synonymsOf(value: string): string[] {
  const raw = value.trim().toLowerCase();
  for (const group of SYNONYM_GROUPS) {
    if (group.some((item) => item.toLowerCase() === raw)) {
      return group.filter((item) => item.toLowerCase() !== raw);
    }
  }
  return [];
}

/**
 * 值对文本的命中强度：0 = 没命中，2 = 同义词命中，3 = 值本身命中（最硬）。
 * 多个值都命中时调用方按「分高者赢、同分取更长（更具体）」裁决。
 */
export function valueMatchScore(haystack: string, value: string): 0 | 2 | 3 {
  const text = String(value ?? "").trim();
  if (!text || !hasMatchableText(text)) return 0;
  if (containsText(haystack, text)) return 3;
  if (synonymsOf(text).some((syn) => containsText(haystack, syn))) return 2;
  return 0;
}

/**
 * 属性名的归一化令牌：不同写法指同一个属性时令牌相同
 * （材质↔material、品牌↔brand…）。对不上返回空串。
 */
const NAME_ALIAS_RULES: Array<[RegExp, string]> = [
  [/材质|材料|面料|布料|主料|material/i, "material"],
  [/品牌|brand/i, "brand"],
  [/风格|style/i, "style"],
  [/颜色|色彩|color|colour/i, "color"],
  [/尺码|尺寸规格|size/i, "size"],
  [/厚度|thickness/i, "thickness"],
  [/克重|平方克|gsm/i, "gsm"],
  [/成分|含量|composition|content/i, "composition"],
  [/适用人群|人群|department|audience/i, "audience"],
  [/产地|原产地|origin/i, "origin"],
  [/款式|造型|style type|shape/i, "shape"],
  [/图案|花纹|pattern/i, "pattern"],
  [/木材|木种|木质|wood/i, "wood"],
];

export function attrNameToken(name: string): string {
  const raw = String(name ?? "").trim();
  for (const [pattern, token] of NAME_ALIAS_RULES) {
    if (pattern.test(raw)) return token;
  }
  return "";
}

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

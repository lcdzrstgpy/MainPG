/**
 * 规格值（颜色）的色系识别 + 色卡。
 *
 * Temu 的颜色预设是几十上百个中文/英文色名，纯文字列表里「浅绿色 / 浅绿 / 薄荷绿」
 * 挨在一起根本分不清。这里按关键词把色名归到一个色系，并给一个代表色值，
 * 用于：① 面板里每个值前面点一个小色块；② 按色系分组筛选。
 *
 * 只做「够用」的映射：认不出来就返回 null，界面退化成纯文字，不猜。
 */

export type SpecColorGroup = {
  key: string;
  label: string;
  /** 代表色：用于色块与分组标签 */
  swatch: string;
  /** 判断顺序敏感：长的、具体的写在前面（深棕 先于 棕） */
  keywords: string[];
  /** 多色 / 渐变 */
  gradient?: boolean;
};

/** 顺序即优先级：越具体的色系越靠前。 */
export const SPEC_COLOR_GROUPS: SpecColorGroup[] = [
  { key: "multi", label: "多色", swatch: "", gradient: true, keywords: ["多色", "混色", "彩色", "花色", "渐变", "multi", "colorful"] },
  { key: "black", label: "黑", swatch: "#1b1b1b", keywords: ["黑", "black"] },
  { key: "white", label: "白", swatch: "#f5f5f2", keywords: ["白", "white", "ivory", "米白", "象牙"] },
  { key: "gray", label: "灰", swatch: "#9aa0a6", keywords: ["灰", "gray", "grey", "silver", "银"] },
  { key: "red", label: "红", swatch: "#c0392b", keywords: ["红", "red", "酒", "wine", "burgundy", "枣"] },
  { key: "orange", label: "橙", swatch: "#e67e22", keywords: ["橙", "橘", "orange", "coral"] },
  { key: "yellow", label: "黄", swatch: "#e8c547", keywords: ["黄", "yellow", "金", "gold", "柠檬", "lemon"] },
  { key: "green", label: "绿", swatch: "#4c9a5f", keywords: ["绿", "green", "青绿", "橄榄", "olive", "抹茶", "mint", "薄荷"] },
  { key: "cyan", label: "青", swatch: "#3aa8a0", keywords: ["青", "cyan", "teal", "tiffany", "湖蓝", "孔雀"] },
  { key: "blue", label: "蓝", swatch: "#2f6fb5", keywords: ["蓝", "blue", "藏青", "navy", "牛仔", "denim"] },
  { key: "purple", label: "紫", swatch: "#7d5ba6", keywords: ["紫", "purple", "violet", "lavender", "薰衣草"] },
  { key: "pink", label: "粉", swatch: "#e38aa8", keywords: ["粉", "pink", "rose", "玫", "桃"] },
  { key: "brown", label: "棕咖", swatch: "#8a5a3b", keywords: ["棕", "咖", "褐", "brown", "coffee", "khaki", "卡其", "驼", "camel"] },
];

/** 认出色系；认不出来返回 null（界面按纯文字处理）。 */
export function specColorGroup(name: string): SpecColorGroup | null {
  const text = String(name ?? "").trim().toLowerCase();
  if (!text) return null;
  for (const group of SPEC_COLOR_GROUPS) {
    if (group.keywords.some((keyword) => text.includes(keyword.toLowerCase()))) return group;
  }
  return null;
}

/**
 * 面板里的分组顺序：按出现顺序保留，最后补一个「其它」（认不出的值）。
 */
export function groupSpecColors(options: string[]): Array<{
  group: SpecColorGroup | null;
  label: string;
  values: string[];
}> {
  const buckets = new Map<string, { group: SpecColorGroup | null; label: string; values: string[] }>();
  for (const option of options) {
    const group = specColorGroup(option);
    const key = group ? group.key : "other";
    const bucket = buckets.get(key) ?? {
      group,
      label: group ? group.label : "其它",
      values: [],
    };
    bucket.values.push(option);
    buckets.set(key, bucket);
  }
  // 按 SPEC_COLOR_GROUPS 的定义顺序输出，「其它」永远在最后
  const ordered = SPEC_COLOR_GROUPS.map((group) => buckets.get(group.key)).filter(Boolean) as Array<{
    group: SpecColorGroup | null;
    label: string;
    values: string[];
  }>;
  const other = buckets.get("other");
  return other ? [...ordered, other] : ordered;
}

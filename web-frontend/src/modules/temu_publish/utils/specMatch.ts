/**
 * 规格值对齐：把草稿/用户填的颜色或尺码，映射到 Temu 类目预设值。
 *
 * Temu 只认类目预设的规格值（颜色是预设色系），自己造值提交会被判
 * 「不合法的规格属性 Color_Black」。草稿里常是「安卓通用款-黑色」「迷雾蓝」
 * 这类描述，所以这里做三级匹配：完全相等 → 包含预设值（取最长）→ 色系关键词。
 */

/** 色系关键词 → Temu 预设值（长的优先，避免「咖啡」被「咖」抢先）。 */
export const SPEC_KEYWORDS: Array<[string, string]> = [
  ["咖啡色", "咖啡色"],
  ["银灰", "银灰色"],
  ["抹茶", "抹茶色"],
  ["卡其", "卡其色"],
  ["咖啡", "咖啡色"],
  ["透明", "透明"],
  ["黑", "黑色"],
  ["白", "白色"],
  ["蓝", "蓝色"],
  ["绿", "绿色"],
  ["红", "红色"],
  ["粉", "粉红色"],
  ["灰", "灰色"],
  ["棕", "深棕色"],
  ["褐", "褐色"],
  ["咖", "咖啡色"],
  ["金", "金色"],
  ["银", "银灰色"],
  ["黄", "黄色"],
  ["紫", "紫色"],
  ["橙", "橙色"],
  ["橘", "橙红色"],
  ["青", "青色"],
];

/**
 * 把 `raw` 对齐到 `options` 里的某个预设值。
 *
 * @returns 命中的预设值；都不匹配时返回 null（调用方应提示用户手动选）
 */
export function matchPresetValue(raw: string, options: string[]): string | null {
  const name = String(raw ?? "").trim();
  if (!name || !options || !options.length) return null;

  // ① 完全相等
  if (options.includes(name)) return name;

  // ② 预设值出现在输入里，取最长的那个（「安卓通用款-黑色」→ 黑色）
  const contained = options
    .filter((option) => option && name.includes(option))
    .sort((a, b) => b.length - a.length);
  if (contained.length) return contained[0];

  // ③ 色系关键词（「迷雾蓝」→ 蓝色、「抹茶绿色」→ 绿色）
  const keywords = [...SPEC_KEYWORDS].sort((a, b) => b[0].length - a[0].length);
  for (const [keyword, preset] of keywords) {
    if (!name.includes(keyword)) continue;
    const hit =
      options.find((option) => option === preset) ??
      options.find((option) => option.includes(preset));
    if (hit) return hit;
  }
  return null;
}

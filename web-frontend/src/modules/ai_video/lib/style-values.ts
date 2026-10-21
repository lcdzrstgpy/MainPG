/**
 * 抽取自 integrations/clipforge/src/lib/ad-templates.ts 的 `STYLE_VALUES`
 * （原文件第 5600–5603 行的 Set 字面量）。
 * 只读同步：白名单以 ClipForge 上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 *
 * 只抽取这一个常量，不引入 ad-templates.ts 的其余逻辑（该文件依赖过重，不在第一批复制范围）。
 */

export const STYLE_VALUES: ReadonlySet<string> = new Set([
  "drama", "reversal", "interview", "story", "unboxing", "product_pov",
  "comparison", "talking_head", "pain-point", "scenario", "auto",
]);

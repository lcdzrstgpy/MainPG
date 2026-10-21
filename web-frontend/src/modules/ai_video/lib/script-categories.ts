/**
 * 抽取自 integrations/clipforge/src/lib/script-engine/templates/index.ts
 * 的 `ProductCategory` 与 `categoryNameMap`（原文件第 19–29 行）。
 * 只读同步：逻辑与文案以 ClipForge 上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 *
 * 只抽取类型与中文分类名，不引入模板内容（templates/*.ts 属于被排除的 script-engine 目录）。
 */

/** Product category type */
export type ProductCategory = "beauty" | "food" | "home" | "fashion" | "tech";

/** Map of category keys to display names */
export const categoryNameMap: Record<ProductCategory, string> = {
  beauty: "美妆护肤",
  food: "食品零食",
  home: "家居日用",
  fashion: "服饰鞋包",
  tech: "数码3C",
};

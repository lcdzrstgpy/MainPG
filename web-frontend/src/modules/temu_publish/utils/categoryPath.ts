import type { TemuCategory, TemuCategoryPath } from "../types";

/** `categoryPathDTOS` 的 cat1DTO…cat10DTO 摊平成路径（顺序即层级）。 */
export function categoryPathFromDto(dto: TemuCategoryPath): TemuCategory[] {
  const result: TemuCategory[] = [];
  for (let index = 1; index <= 10; index += 1) {
    const key = `cat${index}DTO` as keyof TemuCategoryPath;
    const item = dto[key];
    if (item) result.push(item);
  }
  return result;
}

/** 类目展示名：没名字就退回 id，别渲染成 undefined。 */
export function categoryName(item: TemuCategory | undefined): string {
  if (!item) return "";
  return item.catName || String(item.catId);
}

/** `a > b > c` 形式的路径文案。 */
export function categoryPathLabel(path: TemuCategory[]): string {
  return path.map((item) => categoryName(item)).filter(Boolean).join(" > ");
}

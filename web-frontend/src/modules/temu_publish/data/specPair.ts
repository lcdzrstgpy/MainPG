import type { TemuParentSpec } from "../types";

/**
 * 「变种两列该绑哪两个父规格」的选择逻辑。
 *
 * 抽成纯函数而不是留在发布页里，是因为它必须能被穷举验证：不同类目这两列
 * 叫法完全不同（颜色/款式/花色/型号/容量/尺码/尺寸…），
 * 写死一个名字或者只靠下标，换个品类就错位，而错位的表现是「发布必失败」。
 */

/** 「第一列」类属性的常见叫法（决定 SKC 的那一列）。 */
export const FIRST_SPEC_WORDS = ["颜色", "色", "款式", "花色", "图案", "型号", "风格"];

/** 「第二列」类属性的常见叫法（同一 SKC 下的规格细分）。 */
export const SECOND_SPEC_WORDS = [
  "尺码",
  "尺寸",
  "规格",
  "容量",
  "长度",
  "大小",
  "重量",
  "数量",
  "样式",
  "型号", // 手机壳 / 手机配件：颜色 + 型号
];

/**
 * 在父规格列表里挑一个。
 *
 * **按关键字的优先级找，不是按列表顺序找**：
 * `FIRST_SPEC_WORDS` 里「颜色」排在「型号」前面，说明产品意图是「有颜色就优先当第一列」。
 * 原来写成「遍历列表、命中任一关键字就返回」，类目返回 [型号, 颜色] 时会先命中「型号」，
 * 第一列就绑错了 —— 顺序一变结果就变，很难查。
 */
export function findParentSpec(
  specs: TemuParentSpec[],
  keywords: string | string[],
  fallbackIndex: number,
): TemuParentSpec | null {
  const list = Array.isArray(keywords) ? keywords : [keywords];
  for (const word of list) {
    const hit = specs.find((item) => item.parentSpecName.includes(word));
    if (hit) return hit;
  }
  return specs[fallbackIndex] ?? specs[0] ?? null;
}

/**
 * 默认给两列挑哪两个父规格。
 *
 * 关键约束：**两列不能是同一个属性**。按名字找完还要排除掉已选的，
 * 否则类目顺序是 [型号, 颜色] 时，两列会同时落到「颜色」上 —— 生成变种直接崩。
 *
 * 只有一列销售属性时 `size` 返回 0：调用方据此隐藏第二列、也不再要求填第二列的值。
 */
export function pickSpecPair(specs: TemuParentSpec[]): { color: number; size: number } {
  const color = findParentSpec(specs, FIRST_SPEC_WORDS, 0)?.parentSpecId ?? 0;
  // 第二列也按关键字优先级挑：同时有「尺码」和「容量」时，「尺码」更常见
  let size = 0;
  for (const word of SECOND_SPEC_WORDS) {
    const hit = specs.find(
      (item) => item.parentSpecId !== color && item.parentSpecName.includes(word),
    );
    if (hit) {
      size = hit.parentSpecId;
      break;
    }
  }
  if (!size) {
    size = specs.find((item) => item.parentSpecId !== color)?.parentSpecId ?? 0;
  }
  return { color, size };
}

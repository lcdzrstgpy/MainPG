/**
 * 「生成变种」的合并规则。
 *
 * 语义必须是「按颜色 × 尺码补齐组合」，而不是「重建表格」：
 * 用户逐行改过价格/库存/尺寸之后再点一次生成（比如只是多加一个尺码），
 * 整表重建会把改过的值静默冲掉，而且没有任何提示。
 *
 * 所以这里按「颜色 + 尺码」认领旧行：同一格已经存在就原样留下，
 * 只有新组合才交给 `makeRow` 生成。抽成独立纯函数是为了能直接单测 ——
 * 这功能一旦退回重建，只有肉眼能发现。
 *
 * 第二层认领：**同颜色**的旧行也优先复用。
 * 真实场景 —— 草稿带入时只有「均码」一行，用户把尺码改成「可定制尺寸」再点生成：
 * 尺码不同 → 旧行认领不上 → 四行全部重建 → 每行手配的预览图被冲掉，
 * 全变成轮播第一张（用户截图反馈「原先的图片全都换了，有些图是对的」）。
 * 同色复用能保住图、价格、尺寸；尺码文字按新值改写。
 */
export function mergeGeneratedRows<T extends { colorName: string; sizeName: string }>(
  rows: T[],
  colors: string[],
  sizes: string[],
  makeRow: (color: string, size: string, index: number) => T,
): T[] {
  const existing = new Map<string, T>();
  /** 颜色 → 该颜色下还没被认领的旧行（按原顺序） */
  const byColor = new Map<string, T[]>();
  for (const row of rows) {
    existing.set(`${row.colorName}\u0000${row.sizeName}`, row);
    const list = byColor.get(row.colorName);
    if (list) list.push(row);
    else byColor.set(row.colorName, [row]);
  }
  const claimed = new Set<T>();
  const next: T[] = [];
  for (const color of colors) {
    for (const size of sizes) {
      const exact = existing.get(`${color}\u0000${size}`);
      if (exact && !claimed.has(exact)) {
        claimed.add(exact);
        next.push(exact);
        continue;
      }
      // 同颜色还有没被认领的旧行 → 沿用它（保住预览图/价格/尺寸），只改尺码文字
      const sameColor = (byColor.get(color) ?? []).find((row) => !claimed.has(row));
      if (sameColor) {
        claimed.add(sameColor);
        next.push({ ...sameColor, sizeName: size });
        continue;
      }
      next.push(makeRow(color, size, next.length));
    }
  }
  return next;
}


/**
 * 表格和「颜色 × 尺码」是否已经一一对应。
 *
 * 「生成变种」在草稿自动铺满之后大多数时候无事可做（点一下也不会变），
 * 按钮如果一直亮着，用户就会问「这玩意儿到底干什么用」。所以先算一下：
 * 组合全都在、也没有多余行 → 按钮变灰显示「已生成」；改了取值才亮起来。
 */
export function variantCoverage(
  rows: Array<{ colorName: string; sizeName: string }>,
  colors: string[],
  sizes: string[],
): { expected: number; missing: number; extra: number; inSync: boolean } {
  const key = (color: string, size: string) => `${color}\u0000${size}`;
  const wanted = new Set<string>();
  for (const color of colors) for (const size of sizes) wanted.add(key(color, size));
  const have = new Set(rows.map((row) => key(row.colorName, row.sizeName)));
  const missing = [...wanted].filter((item) => !have.has(item)).length;
  const extra = [...have].filter((item) => !wanted.has(item)).length;
  return {
    expected: wanted.size,
    missing,
    extra,
    inSync: wanted.size > 0 && missing === 0 && extra === 0,
  };
}

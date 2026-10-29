/**
 * 价格单位：界面上**一律是「元」**，Temu 接口**一律是「分」**。
 *
 * 换算只在这一个文件里做。以前界面直接让人填分（1299 = ¥12.99），每次都要反向心算，
 * 填错就是 100 倍价差 —— 而申报价是真正会扣钱的数，不该有任何心算环节。
 */

/**
 * "12.99"（元）→ 1299（分）。
 *
 * 最多两位小数：多填的位数当场报错，而不是悄悄四舍五入到分（定价会偏）。
 * 返回 0 或负数也算错：Temu 的 supplierPrice 字段要求 >= 1 分。
 */
export function yuanToCents(value: string, label: string): number {
  const text = String(value ?? "").trim();
  if (!text) throw new Error(`${label}必须填（单位：元）`);
  if (!/^\d+(\.\d{1,2})?$/.test(text)) {
    throw new Error(`${label}请按「元」填，最多两位小数（例如 12.99）`);
  }
  const cents = Math.round(Number(text) * 100);
  if (cents <= 0) throw new Error(`${label}必须大于 0`);
  return cents;
}

/**
 * 1299（分）→ "12.99"（元）。
 *
 * 只给「老数据迁移」用：本机存的变种行/变种模板以前是分，升级后要一次性换成元。
 * 空值或非数字原样返回，别把脏数据变成 "0.00"。
 */
export function centsToYuanText(value: unknown): string {
  const text = String(value ?? "").trim();
  const cents = Number(text);
  if (!text || !Number.isFinite(cents) || cents <= 0) return String(value ?? "");
  return (cents / 100).toFixed(2);
}

/** 元金额展示：150 → "¥150.00"；空值或 0 原样返回（由调用方决定显示 —）。 */
export function formatYuan(text?: string): string {
  const value = Number(text);
  if (!text || !Number.isFinite(value) || value <= 0) return text || "";
  return `¥${value.toFixed(2)}`;
}

export function numberText(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function money(value: number | null | undefined, currency = "CNY"): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const amount = Number(value);
  const body = Math.abs(amount).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (currency === "USD") return `${amount < 0 ? "-" : ""}$${body}`;
  return `${amount < 0 ? "-" : ""}¥${body}`;
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

export function amountTone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  if (Number(value) < 0) return "is-negative";
  if (Number(value) > 0) return "is-positive";
  return "";
}

export function shortDate(value: string | null | undefined): string {
  const text = String(value || "").trim();
  return text || "—";
}

/**
 * 把后端/插件写入的时间统一显示成北京时间。
 *
 * 采集批次里的时间是插件用 `toISOString()` 写的 UTC（结尾带 Z），
 * 直接展示会比北京时间少 8 小时，看起来像"昨晚采的"。
 */
export function beijingTime(value: unknown): string {
  const text = String(value || "").trim();
  if (!text) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text; // 纯日期不用转换
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(parsed);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value || "";
  return `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}:${pick("second")}`;
}

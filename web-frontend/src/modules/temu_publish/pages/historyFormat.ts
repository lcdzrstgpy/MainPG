/**
 * 发布历史列表用的几个纯格式化函数。
 *
 * 单独抽出来是因为「一分钱」和「十二块五」这类显示错误只有单测能拦住 ——
 * 列表里价格、时间、来源标签都是拼接字符串，写错了肉眼很难发现。
 */

/** 分 → 元（两位小数）。0 表示「没记价格」，显示空串而不是 ¥0.00。 */
export function formatPriceRange(min?: number, max?: number): string {
  const low = Math.max(0, Math.round(Number(min) || 0));
  const high = Math.max(0, Math.round(Number(max) || 0));
  if (!low && !high) return "";
  const lo = (low / 100).toFixed(2);
  const hi = (high / 100).toFixed(2);
  if (!high || low === high) return `¥${lo}`;
  if (!low) return `¥${hi}`;
  return `¥${lo}–${hi}`;
}

/** 秒级时间戳 → 「刚刚 / 12 分钟前 / 3 小时前 / 昨天 14:20 / 11-03 14:20」。 */
export function formatRelativeTime(createdTs?: number, now: number = Date.now()): string {
  const ts = Number(createdTs) || 0;
  if (!ts) return "";
  const seconds = Math.max(0, Math.floor(now / 1000 - ts));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  const days = Math.floor(seconds / 86400);
  if (days === 1) return `昨天 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (days < 7) return `${days} 天前`;
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 日期分隔标题：今天 / 昨天 / 11月3日。 */
export function dayLabelOf(createdTs?: number, now: number = Date.now()): string {
  const ts = Number(createdTs) || 0;
  if (!ts) return "";
  const target = new Date(ts * 1000);
  const today = new Date(now);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const startOfTarget = new Date(target.getFullYear(), target.getMonth(), target.getDate()).getTime();
  const diffDays = Math.round((startOfToday - startOfTarget) / 86400000);
  if (diffDays === 0) return "今天";
  if (diffDays === 1) return "昨天";
  return `${target.getMonth() + 1} 月 ${target.getDate()} 日`;
}

/** 来源标签：草稿 id 前缀决定这件商品是从哪条流水线来的。 */
export function sourceLabelOf(draftId?: string): string {
  const value = String(draftId || "");
  if (value.startsWith("ai:")) return "AI 处理";
  if (value.startsWith("pod:")) return "POD 定制";
  return "手动填写";
}

/**
 * 错误码：新记录后端直接给了，老记录只有一个长字符串。
 * 6000002 / 7000015 这类是 Temu 的 7 位业务码，从文本里捞出来做徽章。
 */
export function errorCodeOf(item: { error_code?: string; error?: string }): string {
  const explicit = String(item.error_code || "").trim();
  if (explicit) return explicit;
  const match = /\b(\d{7})\b/.exec(String(item.error || ""));
  return match ? match[1] : "";
}

/**
 * 去掉原因开头的错误码。
 *
 * Temu 的原始文案是「6000002 货品类目属性更新失败：…」，错误码已经单独做成徽章了，
 * 正文再来一遍就成了「错误码 6000002 / 6000002 货品…」的重复。
 */
export function stripErrorCode(error: string, code: string): string {
  const text = String(error || "").trim();
  const value = String(code || "").trim();
  if (!text) return "";
  if (!value) return text;
  const trimmed = text.startsWith(value) ? text.slice(value.length) : text;
  return trimmed.replace(/^[\s:：,，\-—]+/, "").trim() || text;
}

/** 复制到剪贴板：http 页面里 navigator.clipboard 不可用，退回 textarea + execCommand。 */
export async function copyText(text: string): Promise<boolean> {
  const value = String(text || "");
  if (!value) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // 落到下面的兼容路径
  }
  try {
    const area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

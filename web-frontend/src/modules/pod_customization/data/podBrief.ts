import type { PodBusinessFields, PodBusinessFieldsDraft, PodBriefFieldsDraft, PodBriefHistoryItem } from "../types";

export const POD_BRIEF_MAX_LENGTH = 500;
export const POD_BRIEF_HISTORY_LIMIT = 20;

/** 发请求前的输入归一：去首尾空白，连续空白/换行压缩为单个空格。 */
export function normalizeBriefInput(brief: string): string {
  return brief.trim().replace(/\s+/g, " ");
}

export function isBriefRequestValid(brief: string): boolean {
  const normalized = normalizeBriefInput(brief);
  return normalized.length >= 1 && normalized.length <= POD_BRIEF_MAX_LENGTH;
}

function joinBriefList(values: string[]): string {
  return values.map((value) => value.trim()).filter(Boolean).join("、");
}

/**
 * 把接口返回的数组型字段用「、」连接成草稿字符串，口径与
 * businessFieldsForApi 里的 splitBusinessField 对齐（可往返）。
 *
 * 刻意不含 `style_planning`：该项由用户二选一，AI 结果不得覆盖。
 */
export function briefFieldsToDraft(fields: PodBusinessFields): PodBriefFieldsDraft {
  return {
    product_name: fields.product_name,
    product_category: fields.product_category,
    target_market: fields.target_market,
    target_audience: fields.target_audience,
    core_selling_points: joinBriefList(fields.core_selling_points),
    design_theme: fields.design_theme,
    style_keywords: joinBriefList(fields.style_keywords),
    color_preferences: joinBriefList(fields.color_preferences),
    excluded_elements: joinBriefList(fields.excluded_elements),
  };
}

/** 生成结果直接覆盖同名字段，未涉及字段（含 style_planning）保持用户当前值。 */
export function mergeBusinessFields(
  current: PodBusinessFieldsDraft,
  incoming: Partial<PodBusinessFieldsDraft>,
): PodBusinessFieldsDraft {
  return { ...current, ...incoming };
}

export function createBriefHistoryItem(
  input: string,
  fields: PodBriefFieldsDraft,
  id: string = nextBriefHistoryId(),
  createdAt: string = new Date().toISOString(),
): PodBriefHistoryItem {
  return { id, input: normalizeBriefInput(input), fields: { ...fields }, created_at: createdAt };
}

/**
 * 记录一次生成：相同 input（trim 后）去重并保留最新，最新的排在最前，超出上限丢弃最旧。
 */
export function recordBriefHistory(
  history: readonly PodBriefHistoryItem[],
  item: PodBriefHistoryItem,
  limit: number = POD_BRIEF_HISTORY_LIMIT,
): PodBriefHistoryItem[] {
  const inputKey = normalizeBriefInput(item.input);
  const deduped = history.filter((entry) => normalizeBriefInput(entry.input) !== inputKey);
  return [item, ...deduped].slice(0, limit);
}

function nextBriefHistoryId(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `pod-brief-${crypto.randomUUID()}`
    : `pod-brief-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

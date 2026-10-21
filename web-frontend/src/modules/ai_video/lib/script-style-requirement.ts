/**
 * 复制自 integrations/clipforge/src/components/project-creation/script-style-requirement.ts。
 * 只读同步：判定逻辑以上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 */

import { SCRIPT_STYLE_VALUES } from "./script-style.ts";

/** 脚本接口在「风格为 auto/空且历史数据不足」时返回的稳定错误码。 */
export const NEEDS_EXPLICIT_STYLE_CODE = "needs_explicit_style";

export interface ScriptStyleRequirement {
  /** missing = 本地就能判定用户还没选风格；no-data = 服务端没有足够历史数据可推荐 */
  reason: "missing" | "no-data";
  candidates: string[];
}

const fallbackCandidates = (): string[] => [...SCRIPT_STYLE_VALUES];

export function missingStyleRequirement(): ScriptStyleRequirement {
  return { reason: "missing", candidates: fallbackCandidates() };
}

function candidatesOf(value: unknown): string[] {
  if (!Array.isArray(value)) return fallbackCandidates();
  const cleaned = value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  return cleaned.length ? [...new Set(cleaned)] : fallbackCandidates();
}

/** 只认 409 + needs_explicit_style；其它状态码/错误码必须按普通错误处理。 */
export function parseStyleRequirement(status: number, payload: unknown): ScriptStyleRequirement | null {
  if (status !== 409) return null;
  const data = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  if (data.code !== NEEDS_EXPLICIT_STYLE_CODE) return null;
  return { reason: "no-data", candidates: candidatesOf(data.candidates) };
}

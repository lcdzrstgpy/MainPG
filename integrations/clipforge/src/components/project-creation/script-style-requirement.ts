import { SCRIPT_STYLE_VALUES } from "@/lib/script-style";

/**
 * 脚本接口在「风格为 auto/空 且没有足够历史数据」时返回 HTTP 409：
 *   { error: string, code: "needs_explicit_style", candidates: string[] }
 *
 * 这不是致命错误，而是「请用户自己选一个风格」。两个创建入口都通过这里的纯函数把它翻译成
 * 可渲染的选项，再带用户选定的风格重试同一个请求。
 */
export const NEEDS_EXPLICIT_STYLE_CODE = "needs_explicit_style";

export interface ScriptStyleRequirement {
  /** missing = 本地就能判定用户还没选风格；no-data = 服务端没有足够历史数据可推荐 */
  reason: "missing" | "no-data";
  /** 可直接展示给用户点选的风格候选（永远是可选风格，不含 auto） */
  candidates: string[];
}

const fallbackCandidates = (): string[] => [...SCRIPT_STYLE_VALUES];

/** 用户还没显式选风格时的本地兜底：不带着空风格请求接口，先让用户选。 */
export function missingStyleRequirement(): ScriptStyleRequirement {
  return { reason: "missing", candidates: fallbackCandidates() };
}

function candidatesOf(value: unknown): string[] {
  if (!Array.isArray(value)) return fallbackCandidates();
  const cleaned = value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
  return cleaned.length ? [...new Set(cleaned)] : fallbackCandidates();
}

/**
 * 判断某个响应是否属于「需要用户显式选风格」分支。只认 409 + needs_explicit_style；
 * 其它状态码/code 返回 null，调用方必须按普通错误处理（不得吞掉、不得显示为生成失败）。
 */
export function parseStyleRequirement(status: number, payload: unknown): ScriptStyleRequirement | null {
  if (status !== 409) return null;
  const data = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  if (data.code !== NEEDS_EXPLICIT_STYLE_CODE) return null;
  return { reason: "no-data", candidates: candidatesOf(data.candidates) };
}

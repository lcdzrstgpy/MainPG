import { getDb } from "@/lib/db";
import { projects } from "@/lib/db/schema";
import { eq } from "drizzle-orm";

/**
 * 整片链（九宫格 → 原生整片）的服务端出片策略门禁（只保护 storyboard-film）。
 *
 * 正向白名单、失败关闭（fail-closed）：只有两种情况放行——
 *   1. 项目简报明确是 native-film；
 *   2. 项目确实存在且没有创作简报（creationBrief 为 null 的旧项目）。
 * 其余一律拒绝：draft / controlled-motion / 简报损坏或缺 outputStrategy / 未知未来策略 /
 * 项目不存在 / 数据库查询失败。查询异常绝不转换成字符串再进白名单判断。
 */

export type FilmChainGateResult =
  | { allowed: true }
  | { allowed: false; reason: "project-missing" | "db-error" | "strategy-denied"; strategy?: string };

/** 纯核心判定（不触数据库，可直接单测）：整片链放行白名单。 */
export function resolveFilmChainGate(input: {
  projectExists: boolean;
  creationBrief: { outputStrategy?: unknown } | null | undefined;
}): FilmChainGateResult {
  if (!input.projectExists) return { allowed: false, reason: "project-missing" };
  // 旧项目：项目存在但没有创作简报列，兼容原有整片行为
  if (input.creationBrief == null) return { allowed: true };
  if (input.creationBrief.outputStrategy === "native-film") return { allowed: true };
  // draft / controlled-motion / 简报损坏（无 outputStrategy）/ 未知未来策略：一律拒绝
  return {
    allowed: false,
    reason: "strategy-denied",
    strategy: String(input.creationBrief.outputStrategy ?? "unknown"),
  };
}

/** 读库 + 纯核心判定；数据库异常直接拒绝（fail-closed，不放行任何提交）。 */
export async function filmChainStrategyGuard(projectId: string): Promise<FilmChainGateResult> {
  const rows = await getDb()
    .select({ brief: projects.creationBrief })
    .from(projects)
    .where(eq(projects.id, projectId))
    .limit(1)
    .catch(() => null);
  if (rows === null) return { allowed: false, reason: "db-error" };
  return resolveFilmChainGate({ projectExists: rows.length > 0, creationBrief: rows[0]?.brief });
}

/** 把拒绝结果映射成 API 错误（文案与状态码），路由只负责转交。 */
export function filmChainGateError(
  gate: Extract<FilmChainGateResult, { allowed: false }>
): { zh: string; en: string; status: number } {
  switch (gate.reason) {
    case "project-missing":
      return { zh: "项目不存在", en: "Project not found", status: 404 };
    case "db-error":
      return { zh: "读取项目出片策略失败，请稍后重试", en: "Failed to read the project output strategy, please retry", status: 500 };
    default:
      return {
        zh: `出片策略「${gate.strategy ?? "未知"}」不允许整片生成`,
        en: `Output strategy "${gate.strategy ?? "unknown"}" does not allow native film generation`,
        status: 409,
      };
  }
}
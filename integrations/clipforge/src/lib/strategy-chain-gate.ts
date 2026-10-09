import { getDb } from "@/lib/db";
import { projects } from "@/lib/db/schema";
import { eq } from "drizzle-orm";
import type { OutputStrategy } from "@/lib/creation-brief";

/**
 * 三条出片链路各自的服务端策略门禁（正向白名单、失败关闭），与整片链的
 * `film-chain-gate` 同一口径、同一拒绝语义。
 *
 * 每条链只放行两种情况：
 *   1. 项目简报明确落在该链的策略白名单里；
 *   2. 项目确实存在且没有创作简报（creationBrief 为 null 的旧项目，保持迁移前行为）。
 * 其余一律拒绝：策略不匹配、简报损坏或缺 outputStrategy、未知未来策略、项目不存在、
 * 数据库查询失败。查询异常绝不转换成字符串再进白名单判断。
 *
 * 门禁只回答「这条链属不属于你这个策略」，不改变任何链路的业务逻辑、参数与计费。
 */

export type StrategyChain = "free" | "keyframe" | "motion";

const CHAIN_WHITELIST: Record<StrategyChain, readonly OutputStrategy[]> = {
  // 免费草稿链（judge → 免费素材 → FFmpeg 合成）：只有选了免费草稿的项目走它
  free: ["draft"],
  // 关键帧生图：逐镜动态与原生整片都要先有关键帧；免费草稿只用商品图/免费素材
  keyframe: ["controlled-motion", "native-film"],
  // 逐镜生视频（付费 I2V）：只有导演可控动态走逐镜；原生整片是一次模型调用，不该逐镜烧钱
  motion: ["controlled-motion"],
};

const CHAIN_LABEL: Record<StrategyChain, string> = {
  free: "免费草稿链",
  keyframe: "关键帧生图",
  motion: "逐镜动态链",
};

export type StrategyGateResult =
  | { allowed: true }
  | { allowed: false; reason: "project-missing" | "db-error" | "strategy-denied"; strategy?: string };

/** 纯核心判定（不触数据库，可直接单测）：某条链的策略白名单。 */
export function resolveStrategyGate(input: {
  chain: StrategyChain;
  projectExists: boolean;
  creationBrief: { outputStrategy?: unknown } | null | undefined;
}): StrategyGateResult {
  if (!input.projectExists) return { allowed: false, reason: "project-missing" };
  // 旧项目：项目存在但没有创作简报列，兼容迁移前的行为
  if (input.creationBrief == null) return { allowed: true };
  const strategy = input.creationBrief.outputStrategy;
  if (typeof strategy === "string" && (CHAIN_WHITELIST[input.chain] as readonly string[]).includes(strategy)) {
    return { allowed: true };
  }
  // 策略不匹配 / 简报损坏（无 outputStrategy）/ 未知未来策略：一律拒绝
  return { allowed: false, reason: "strategy-denied", strategy: String(strategy ?? "unknown") };
}

/** 读库 + 纯核心判定；数据库异常直接拒绝（fail-closed，不放行任何提交）。 */
export async function strategyChainGuard(projectId: string, chain: StrategyChain): Promise<StrategyGateResult> {
  const rows = await getDb()
    .select({ brief: projects.creationBrief })
    .from(projects)
    .where(eq(projects.id, projectId))
    .limit(1)
    .catch(() => null);
  if (rows === null) return { allowed: false, reason: "db-error" };
  return resolveStrategyGate({ chain, projectExists: rows.length > 0, creationBrief: rows[0]?.brief });
}

/** 把拒绝结果映射成 API 错误（文案与状态码），路由只负责转交。 */
export function strategyGateError(
  gate: Extract<StrategyGateResult, { allowed: false }>,
  chain: StrategyChain
): { zh: string; en: string; status: number } {
  switch (gate.reason) {
    case "project-missing":
      return { zh: "项目不存在", en: "Project not found", status: 404 };
    case "db-error":
      return { zh: "读取项目出片策略失败，请稍后重试", en: "Failed to read the project output strategy, please retry", status: 500 };
    default:
      return {
        zh: `出片策略「${gate.strategy ?? "未知"}」不允许走${CHAIN_LABEL[chain]}`,
        en: `Output strategy "${gate.strategy ?? "unknown"}" does not allow the ${chain} chain`,
        status: 409,
      };
  }
}

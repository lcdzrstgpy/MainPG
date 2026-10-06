import type { ClipForgeRuntimeState, ClipForgeStatus } from "../api/clipforgeApi.ts";

// 轮询节奏：起停阶段约 800ms 追状态，就绪后每 5s 复核一次（服务可能在任意时刻退出）。
// 返回 null 表示终态，停止轮询，避免对 unavailable/stopped/failed 无意义地打后端。
export function pollDelayForState(state: ClipForgeRuntimeState): number | null {
  if (state === "starting" || state === "stopping") return 800;
  if (state === "ready") return 5000;
  return null;
}

// 只有真正 ready、且有 url 与 instanceId 的实例才允许内嵌：
// 光看 HTTP 200 会把 starting/failed 也当成「已连接」。
export function canEmbedClipForge(status: Pick<ClipForgeStatus, "state" | "url" | "instanceId"> | null): boolean {
  return Boolean(status?.state === "ready" && status.url && status.instanceId);
}

// 实例身份键：instanceId 或 url 任一变化都代表换了 sidecar 进程，旧 iframe 文档必须作废重建。
export function clipForgeInstanceKey(status: Pick<ClipForgeStatus, "url" | "instanceId"> | null): string | null {
  return status?.url && status.instanceId ? `${status.instanceId}@${status.url}` : null;
}

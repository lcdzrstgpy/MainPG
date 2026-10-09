import { apiRequest } from "../../../shared/api/apiClient";

// 与后端 ClipForge sidecar 生命周期严格对齐的运行时状态。
export type ClipForgeRuntimeState = "unavailable" | "stopped" | "starting" | "ready" | "failed" | "stopping";
export type ClipForgeBuildState = "available" | "missing" | "invalid";
export type ClipForgeError = {
  code: string;
  message: string;
  retryable: boolean;
  exitCode: number | null;
  diagnosticId: string | null;
};
export type ClipForgeStatus = {
  state: ClipForgeRuntimeState;
  buildState: ClipForgeBuildState;
  available: boolean;
  url: string | null;
  instanceId: string | null;
  message: string;
  error: ClipForgeError | null;
};

// signal 用于卸载/新请求到达时取消在途轮询，避免慢的旧响应覆盖新状态。
export function getClipForgeStatus(signal?: AbortSignal) {
  return apiRequest<ClipForgeStatus>("/api/clipforge/status", { signal });
}

export function startClipForge(signal?: AbortSignal) {
  return apiRequest<ClipForgeStatus>("/api/clipforge/start", { method: "POST", signal });
}

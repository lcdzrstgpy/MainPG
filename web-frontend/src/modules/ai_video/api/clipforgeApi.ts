import { apiRequest } from "../../../shared/api/apiClient";

export type ClipForgeStatus = {
  available: boolean;
  state: "unavailable" | "stopped" | "ready" | "failed";
  url: string | null;
  message: string;
};

export function getClipForgeStatus() {
  return apiRequest<ClipForgeStatus>("/api/clipforge/status");
}

export function startClipForge() {
  return apiRequest<ClipForgeStatus>("/api/clipforge/start", { method: "POST" });
}

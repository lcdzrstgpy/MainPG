const API_BASE = "/api/pod-customization";

export function podStyleTitleRegenerateRequest(batchId: string, styleIndex: number) {
  return {
    path: `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/title/regenerate`,
    options: { method: "POST" as const, body: {} },
  };
}

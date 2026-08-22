const API_BASE = "/api/pod-customization";

export function podStyleTitleRegenerateRequest(batchId: string, styleIndex: number, ackPaidRetry = false) {
  return {
    path: `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/title/regenerate`,
    options: { method: "POST" as const, body: { ack_paid_retry: ackPaidRetry } },
  };
}

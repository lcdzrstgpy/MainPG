export function podBillingPendingRequest() {
  return { path: "/api/pod-customization/billing-runs/pending" } as const;
}

export function podBillingResumeRequest(runId: string) {
  return {
    path: `/api/pod-customization/billing-runs/${encodeURIComponent(runId)}/resume`,
    options: { method: "POST", body: {} },
  } as const;
}

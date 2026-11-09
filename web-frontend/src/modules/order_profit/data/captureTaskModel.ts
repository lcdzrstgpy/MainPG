/** 采集任务与同步策略的纯函数：不读写浏览器存储，便于单测。 */

export type CaptureTaskKind = "full" | "incremental" | "resource";

export type CaptureResultRow = {
  key: string;
  label: string;
  state: string;
  detail?: string;
};

export type CaptureResultSummary = {
  total: number;
  ok: number;
  skipped: number;
  failed: number;
  running: number;
  /** 有成功、但仍有失败或跳过：用于把「已完成」改成「部分完成」。 */
  partial: boolean;
};

export type CaptureStatusChip = {
  label: string;
  tone: "" | "is-ok" | "is-warn" | "is-error";
};

/** 每 7 天至少做一次全量，避免增量永远只补第一页。 */
export const FULL_SYNC_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

/** 可以单独补采的步骤（对应 /captures/resource 的资源名）。 */
export const RETRYABLE_CAPTURE_STEPS = new Set([
  "store",
  "traffic",
  "sales",
  "finance",
  "settlement",
  "orders",
]);

export function isActiveCaptureStatus(status: string): boolean {
  return status === "queued" || status === "sent" || status === "running";
}

/**
 * 单按钮同步策略：没有全量记录或距上次全量超过 7 天时做全量，否则只补增量。
 * 返回值与后端的 incremental 参数保持一致。
 */
export function resolveSyncKind(input: {
  now: number;
  lastFullAt: number;
  maxAgeMs?: number;
}): "full" | "incremental" {
  const maxAge = input.maxAgeMs ?? FULL_SYNC_MAX_AGE_MS;
  if (!Number.isFinite(input.lastFullAt) || input.lastFullAt <= 0) return "full";
  if (!Number.isFinite(input.now)) return "incremental";
  return input.now - input.lastFullAt > maxAge ? "full" : "incremental";
}

export function summarizeCaptureResults(results?: CaptureResultRow[] | null): CaptureResultSummary {
  const list = Array.isArray(results) ? results : [];
  let ok = 0;
  let skipped = 0;
  let failed = 0;
  let running = 0;
  for (const row of list) {
    if (row.state === "ok") ok += 1;
    else if (row.state === "skipped") skipped += 1;
    else if (row.state === "failed") failed += 1;
    else running += 1;
  }
  return {
    total: list.length,
    ok,
    skipped,
    failed,
    running,
    partial: list.length > 0 && ok > 0 && (skipped > 0 || failed > 0),
  };
}

/** 采集结果徽标：只要有一项没成功，就不能再显示成“已完成”。 */
export function captureStatusChip(
  status: string,
  ok: boolean,
  results?: CaptureResultRow[] | null,
): CaptureStatusChip {
  const summary = summarizeCaptureResults(results);
  if (status === "succeeded") {
    if (summary.total > 0 && summary.ok === 0) return { label: "未采到数据", tone: "is-error" };
    if (summary.partial) return { label: "部分完成", tone: "is-warn" };
    return { label: ok ? "已完成" : "未采到数据", tone: ok ? "is-ok" : "is-error" };
  }
  if (status === "failed") return { label: "失败", tone: "is-error" };
  if (status === "cancelled") return { label: "已终止", tone: "" };
  if (status === "queued") return { label: "排队中", tone: "" };
  if (status === "sent") return { label: "已下发", tone: "" };
  if (status === "running") return { label: "采集中", tone: "" };
  return { label: "未开始", tone: "" };
}

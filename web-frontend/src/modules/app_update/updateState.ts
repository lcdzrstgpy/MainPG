export type AppUpdatePhase = "idle" | "checking" | "available" | "downloading" | "verifying" | "installing" | "failed" | "unavailable";

export type AppUpdateRelease = {
  version: string;
  mandatory: boolean;
  release_notes: string;
  published_at: string;
};

export type AppUpdateProgress = {
  downloaded_bytes: number;
  total_bytes: number | null;
  percent: number | null;
};

export type AppUpdateStatus = {
  current_version: string;
  state: AppUpdatePhase;
  release: AppUpdateRelease | null;
  progress: AppUpdateProgress | null;
  error: string | null;
};

export type PatchFile = {
  path: string;
  action: "add" | "replace" | "delete";
  sha256: string;
  size: number;
};

export type PatchRelease = {
  from_version: string;
  to_version: string;
  published_at: string;
  file_base_url: string;
  files: PatchFile[];
};

export type PatchProgress = {
  downloaded_files: number;
  total_files: number;
  downloaded_bytes: number;
  total_bytes: number;
  percentage: number;
};

export type PatchStatus = {
  current_version: string;
  state: AppUpdatePhase;
  patch: PatchRelease | null;
  progress: PatchProgress | null;
  error: string | null;
};

export type UpdateDialogPhase = "ready" | "checking" | "downloading" | "verifying" | "installing" | "failed";

export type UpdateDialogState = {
  visible: boolean;
  mandatory: boolean;
  currentVersion: string;
  targetVersion: string;
  notes: string[];
  phase: UpdateDialogPhase;
  progress: number | null;
  message: string;
  error: string;
};

const ACTIVE_PHASES = new Set<AppUpdatePhase>(["checking", "available", "downloading", "verifying", "installing", "failed"]);

function messageForPhase(phase: UpdateDialogPhase): string {
  switch (phase) {
    case "checking": return "正在检查更新信息…";
    case "downloading": return "正在下载更新包…";
    case "verifying": return "正在验证更新包完整性…";
    case "installing": return "正在安装更新，本机服务即将重启。请勿关闭此窗口。";
    case "failed": return "更新失败，请检查网络后重试。";
    default: return "发现新版本，可立即更新。";
  }
}

function dialogPhase(phase: AppUpdatePhase): UpdateDialogPhase {
  if (phase === "checking" || phase === "downloading" || phase === "verifying" || phase === "installing" || phase === "failed") return phase;
  return "ready";
}

export function toUpdateDialogState(status: Partial<AppUpdateStatus>): UpdateDialogState {
  const phase = dialogPhase(status.state ?? "idle");
  const release = status.release ?? null;
  const rawProgress = status.progress?.percent;
  const progress = typeof rawProgress === "number" && Number.isFinite(rawProgress)
    ? Math.min(100, Math.max(0, rawProgress))
    : null;

  return {
    visible: release !== null && ACTIVE_PHASES.has(status.state ?? "idle"),
    mandatory: release?.mandatory === true,
    currentVersion: status.current_version ?? "当前版本",
    targetVersion: release?.version ?? "",
    notes: release?.release_notes.split(/\r?\n/).map((note) => note.trim()).filter(Boolean) ?? [],
    phase,
    progress,
    message: messageForPhase(phase),
    error: status.error ?? "",
  };
}

/** Convert an incremental-patch status into the shared dialog state (preferred over full install). */
export function toPatchDialogState(status: PatchStatus | null): UpdateDialogState | null {
  if (!status || !status.patch) return null;
  const phase = dialogPhase(status.state);
  const rawProgress = status.progress?.percentage;
  const progress = typeof rawProgress === "number" && Number.isFinite(rawProgress)
    ? Math.min(100, Math.max(0, rawProgress))
    : null;

  return {
    visible: status.patch !== null && ACTIVE_PHASES.has(status.state),
    mandatory: false,
    currentVersion: status.current_version ?? "当前版本",
    targetVersion: status.patch.to_version,
    notes: [`增量更新：仅下载 ${status.patch.files.length} 个变更文件（${status.patch.from_version} → ${status.patch.to_version}），速度更快。`],
    phase,
    progress,
    message: messageForPhase(phase),
    error: status.error ?? "",
  };
}

export function preserveVerifiedRelease(
  previous: AppUpdateStatus | null,
  next: AppUpdateStatus,
): AppUpdateStatus {
  if (
    previous?.release
    && next.release === null
    && (next.state === "checking" || next.state === "failed")
  ) {
    return { ...next, release: previous.release };
  }
  return next;
}

export function shouldPollUpdateStatus(status: { state: AppUpdatePhase } | null): boolean {
  return status?.state === "checking" || status?.state === "downloading" || status?.state === "verifying" || status?.state === "installing";
}

export function shouldShowDeferAction(view: Pick<UpdateDialogState, "mandatory" | "phase">): boolean {
  return !view.mandatory && view.phase !== "checking" && view.phase !== "downloading" && view.phase !== "verifying" && view.phase !== "installing";
}

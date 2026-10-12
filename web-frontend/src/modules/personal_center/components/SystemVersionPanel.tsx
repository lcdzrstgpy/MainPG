import { useEffect, useRef, useState } from "react";

import { appUpdateApi } from "../../app_update/api/appUpdateApi";
import {
  preserveVerifiedRelease,
  shouldPollUpdateStatus,
  type AppUpdateStatus,
  type PatchStatus,
} from "../../app_update/updateState";

const POLL_INTERVAL_MS = 1200;

type VersionPhase = "idle" | "checking" | "available" | "downloading" | "verifying" | "installing" | "failed" | "unavailable";

type VersionView = {
  phase: VersionPhase;
  currentVersion: string;
  targetVersion: string;
  notes: string[];
  progress: number | null;
  progressDetail: string;
  error: string;
  mandatory: boolean;
};

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/** 后端全量/增量更新均返回 percentage 字段，旧版前端类型记为 percent，这里统一兼容。 */
function resolveProgress(status: AppUpdateStatus | PatchStatus | null): number | null {
  const raw = status?.progress;
  if (!raw) return null;
  const value = (raw as { percentage?: number; percent?: number }).percentage ?? (raw as { percent?: number }).percent;
  return typeof value === "number" && Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null;
}

function phaseOf(state: string, hasRelease: boolean): VersionPhase {
  switch (state) {
    case "checking": return "checking";
    case "downloading": return "downloading";
    case "verifying": return "verifying";
    case "installing": return "installing";
    case "failed": return "failed";
    case "unavailable": return "unavailable";
    case "available": return "available";
    default: return hasRelease ? "available" : "idle";
  }
}

function toView(status: AppUpdateStatus | null, patch: PatchStatus | null): VersionView {
  const currentVersion = patch?.current_version || status?.current_version || "--";
  if (patch?.patch) {
    const matchingRelease = status?.release?.version === patch.patch.to_version ? status.release : null;
    return {
      phase: phaseOf(patch.state, true),
      currentVersion,
      targetVersion: patch.patch.to_version,
      notes: [
        `增量更新：仅下载 ${patch.patch.files.length} 个变更文件（${patch.patch.from_version} → ${patch.patch.to_version}），速度更快。`,
        ...(matchingRelease?.release_notes.split(/\r?\n/).map((note) => note.trim()).filter(Boolean) ?? []),
      ],
      progress: resolveProgress(patch),
      progressDetail: patch.progress
        ? `${formatBytes(patch.progress.downloaded_bytes)} / ${formatBytes(patch.progress.total_bytes)} · ${patch.progress.downloaded_files}/${patch.progress.total_files} 个文件`
        : "",
      error: patch.error ?? "",
      mandatory: matchingRelease?.mandatory === true,
    };
  }
  const release = status?.release ?? null;
  return {
    phase: phaseOf(status?.state ?? "idle", release != null),
    currentVersion,
    targetVersion: release?.version ?? "",
    notes: release?.release_notes.split(/\r?\n/).map((note) => note.trim()).filter(Boolean) ?? [],
    progress: resolveProgress(status),
    progressDetail: status?.progress?.total_bytes
      ? `${formatBytes(status.progress.downloaded_bytes)} / ${formatBytes(status.progress.total_bytes)}`
      : "",
    error: status?.error ?? "",
    mandatory: release?.mandatory === true,
  };
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "更新请求失败，请稍后重试。";
}

export function SystemVersionPanel() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [patch, setPatch] = useState<PatchStatus | null>(null);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async () => {
      let poll = false;
      try {
        const next = await appUpdateApi.status();
        if (cancelled) return;
        setStatus((previous) => preserveVerifiedRelease(previous, next));
        if (shouldPollUpdateStatus(next)) poll = true;
      } catch (error) {
        if (cancelled) return;
        setStatus((previous) => {
          if (!previous || previous.state === "installing") return previous;
          return { ...previous, state: "failed", error: errorMessage(error) };
        });
      }
      try {
        const next = await appUpdateApi.patchStatus();
        if (cancelled) return;
        setPatch(next);
        if (shouldPollUpdateStatus(next)) poll = true;
      } catch (error) {
        if (cancelled) return;
        setPatch((previous) => previous && previous.state === "installing" ? previous : { state: "failed", current_version: "", patch: null, progress: null, error: errorMessage(error) });
      }
      if (poll) timer = setTimeout(() => void refresh(), POLL_INTERVAL_MS);
    };

    void refresh();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [pollGeneration]);

  const view = toView(status, patch);
  const inProgress = view.phase === "checking" || view.phase === "downloading" || view.phase === "verifying" || view.phase === "installing";
  const canInstall = view.phase === "available" && !inProgress && !busy;

  const beginPost = () => {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    return true;
  };
  const finishPost = () => {
    busyRef.current = false;
    setBusy(false);
  };

  const check = async () => {
    if (!beginPost()) return;
    try {
      await appUpdateApi.check();
      await appUpdateApi.patchCheck();
      setPollGeneration((generation) => generation + 1);
    } catch (error) {
      setStatus((previous) => previous ? { ...previous, state: "failed", error: errorMessage(error) } : previous);
      setPatch({ state: "failed", current_version: "", patch: null, progress: null, error: errorMessage(error) });
    } finally {
      finishPost();
    }
  };

  const install = async () => {
    if (!beginPost()) return;
    try {
      if (patch?.patch) {
        await appUpdateApi.patchInstall();
      } else {
        await appUpdateApi.install();
      }
      setPollGeneration((generation) => generation + 1);
    } catch (error) {
      setStatus((previous) => previous ? { ...previous, state: "failed", error: errorMessage(error) } : previous);
      setPatch((previous) => previous ? { ...previous, state: "failed", error: errorMessage(error) } : { state: "failed", current_version: "", patch: null, progress: null, error: errorMessage(error) });
    } finally {
      finishPost();
    }
  };

  const phaseLabel: Record<VersionPhase, string> = {
    idle: "当前已是最新版本",
    checking: "正在检查更新信息…",
    available: "发现新版本，可立即更新。",
    downloading: "正在下载更新包…",
    verifying: "正在验证更新包完整性…",
    installing: "更新完毕，请重新启动程序以使用新版本。",
    failed: "更新失败，请检查网络后重试。",
    unavailable: "当前环境暂不支持自动更新。",
  };

  return (
    <article className="personal-card version-card">
      <div className="personal-card-title">
        <span className="iconfont icon-setting" aria-hidden="true" />
        <div>
          <h2>系统版本</h2>
          <small>远程更新后版本号会及时刷新，避免重复或降级更新。</small>
        </div>
        <button type="button" onClick={() => void check()} disabled={busy || inProgress}>检查更新</button>
      </div>

      <dl className="version-fields">
        <div><dt>当前版本</dt><dd>{view.currentVersion}</dd></div>
        {view.targetVersion && <div><dt>目标版本</dt><dd>{view.targetVersion}</dd></div>}
      </dl>

      {view.mandatory && <p className="version-message is-warning">该版本为强制更新，请立即安装。</p>}

      {inProgress && <div className="version-progress" aria-live="polite">
        {view.progress !== null && <>
          <div className="version-progress-track" role="progressbar" aria-label="更新下载进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.progress)}>
            <span style={{ width: `${view.progress}%` }} />
          </div>
          <strong>{Math.round(view.progress)}%</strong>
        </>}
        <small>{view.phase === "installing" ? "请退出当前程序后重新启动，以完成版本更新。" : phaseLabel[view.phase]}</small>
        {view.progressDetail && <small>{view.progressDetail}</small>}
      </div>}

      {view.notes.length > 0 && !inProgress && <div className="version-notes"><h3>更新说明</h3><ul>{view.notes.map((note) => <li key={note}>{note}</li>)}</ul></div>}

      {view.phase === "failed" && <p className="version-message is-error" role="alert">{view.error || "更新失败，请稍后重试。"}</p>}

      <div className="version-actions">
        {view.phase === "failed" ? (
          <button type="button" onClick={() => void check()} disabled={busy}>重新检查并重试</button>
        ) : canInstall ? (
          <button type="button" onClick={() => void install()} disabled={busy}>立即更新</button>
        ) : (
          <button type="button" onClick={() => void check()} disabled={busy || inProgress}>{view.phase === "idle" ? "检查更新" : "刷新"}</button>
        )}
      </div>
    </article>
  );
}

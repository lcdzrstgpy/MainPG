import { useCallback, useEffect, useRef, useState } from "react";

import { appUpdateApi } from "../api/appUpdateApi";
import {
  preserveVerifiedRelease,
  shouldPollUpdateStatus,
  shouldShowDeferAction,
  toPatchDialogState,
  toUpdateDialogState,
  type AppUpdateStatus,
  type PatchStatus,
} from "../updateState";
import "../styles/app-update.css";

const CHECK_INTERVAL_MS = 30 * 60 * 1000;
const STATUS_POLL_MS = 1200;

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 1 : 2)} MB`;
}

/**
 * 工作台运行期间的更新提醒。
 * 启动更新门完成首次检查；这里每 30 分钟再检查一次，避免用户长期不重启时
 * 收不到后台刚发布的版本。增量更新优先于完整安装包更新。
 */
export function RuntimeUpdateNotifier() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [patch, setPatch] = useState<PatchStatus | null>(null);
  const [dismissedTarget, setDismissedTarget] = useState("");
  const checkRunningRef = useRef(false);
  const lastCheckAtRef = useRef(Date.now());

  const refreshStatuses = useCallback(async () => {
    const [patchResult, fullResult] = await Promise.allSettled([
      appUpdateApi.patchStatus(),
      appUpdateApi.status(),
    ]);
    if (patchResult.status === "fulfilled") setPatch(patchResult.value);
    if (fullResult.status === "fulfilled") {
      setStatus((previous) => preserveVerifiedRelease(previous, fullResult.value));
    }
    return {
      patch: patchResult.status === "fulfilled" ? patchResult.value : null,
      full: fullResult.status === "fulfilled" ? fullResult.value : null,
    };
  }, []);

  const checkForUpdates = useCallback(async () => {
    if (checkRunningRef.current) return;
    checkRunningRef.current = true;
    lastCheckAtRef.current = Date.now();
    try {
      await Promise.allSettled([appUpdateApi.patchCheck(), appUpdateApi.check()]);
      await refreshStatuses();
    } finally {
      checkRunningRef.current = false;
    }
  }, [refreshStatuses]);

  useEffect(() => {
    const timer = window.setInterval(() => void checkForUpdates(), CHECK_INTERVAL_MS);
    const checkWhenVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastCheckAtRef.current >= CHECK_INTERVAL_MS) void checkForUpdates();
    };
    document.addEventListener("visibilitychange", checkWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", checkWhenVisible);
    };
  }, [checkForUpdates]);

  useEffect(() => {
    const active = shouldPollUpdateStatus(patch) || shouldPollUpdateStatus(status);
    if (!active) return;
    const timer = window.setInterval(() => void refreshStatuses(), STATUS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [patch, status, refreshStatuses]);

  const rawPatchView = toPatchDialogState(patch);
  const fullView = status ? toUpdateDialogState(status) : null;
  const patchView = rawPatchView && fullView?.targetVersion === rawPatchView.targetVersion
    ? {
        ...rawPatchView,
        mandatory: fullView.mandatory,
        notes: [...rawPatchView.notes, ...fullView.notes],
      }
    : rawPatchView;
  const view = patchView?.visible ? patchView : fullView?.visible ? fullView : null;
  const target = view?.targetVersion ?? "";
  if (!view || !target || dismissedTarget === target) return null;

  const isPatch = Boolean(patchView?.visible);
  const inProgress = view.phase === "checking" || view.phase === "downloading" || view.phase === "verifying" || view.phase === "installing";
  const canDefer = shouldShowDeferAction(view);
  const progressDetail = isPatch && patch?.progress
    ? `${formatBytes(patch.progress.downloaded_bytes)} / ${formatBytes(patch.progress.total_bytes)} · ${patch.progress.downloaded_files}/${patch.progress.total_files} 个文件`
    : status?.progress?.total_bytes
      ? `${formatBytes(status.progress.downloaded_bytes)} / ${formatBytes(status.progress.total_bytes)}`
      : "";

  const install = async () => {
    try {
      if (isPatch) {
        setPatch(await appUpdateApi.patchInstall());
      } else {
        setStatus(await appUpdateApi.install());
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "更新请求失败";
      if (isPatch) {
        setPatch((previous) => previous ? { ...previous, state: "failed", error: message } : previous);
      } else {
        setStatus((previous) => previous ? { ...previous, state: "failed", error: message } : previous);
      }
    }
  };

  const defer = async () => {
    setDismissedTarget(target);
    if (!isPatch && !view.mandatory) {
      try { await appUpdateApi.snooze(); } catch { /* 本次会话已经隐藏，不影响继续工作 */ }
    }
  };

  return (
    <div className="app-update-backdrop" role="presentation">
      <section className="app-update-dialog" role="dialog" aria-modal="true" aria-labelledby="runtime-update-title" aria-describedby="runtime-update-description" tabIndex={-1}>
        <div className="app-update-heading">
          <span className="app-update-badge">发现新版本</span>
          <h2 id="runtime-update-title">MainPG {view.targetVersion}</h2>
        </div>
        <p id="runtime-update-description" className="app-update-message">{view.message}</p>
        <dl className="app-update-versions">
          <div><dt>当前版本</dt><dd>{view.currentVersion}</dd></div>
          <div><dt>目标版本</dt><dd>{view.targetVersion}</dd></div>
        </dl>
        {view.notes.length > 0 && <ul className="app-update-notes">{view.notes.map((note, index) => <li key={`${index}-${note}`}>{note}</li>)}</ul>}
        {inProgress && <div className="app-update-progress" aria-live="polite">
          {view.progress !== null && <>
            <div className="app-update-progress-track" role="progressbar" aria-label="更新下载进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.progress)}><span style={{ width: `${view.progress}%` }} /></div>
            <strong>{Math.round(view.progress)}%</strong>
          </>}
          {progressDetail && <small>{progressDetail}</small>}
        </div>}
        {view.phase === "failed" && <p className="app-update-error" role="alert">{view.error || "更新失败，请检查网络后重试。"}</p>}
        <div className="app-update-actions">
          {!inProgress && <button type="button" className="app-update-primary" onClick={() => void install()}>{view.phase === "failed" ? "重新更新" : "立即更新"}</button>}
          {canDefer && <button type="button" className="app-update-secondary" onClick={() => void defer()}>稍后处理</button>}
        </div>
      </section>
    </div>
  );
}

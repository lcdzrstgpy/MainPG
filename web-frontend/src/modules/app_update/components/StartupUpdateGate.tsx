import { useEffect, useRef, useState } from "react";

import { appUpdateApi } from "../api/appUpdateApi";
import { shouldPollUpdateStatus, type AppUpdateStatus, type PatchStatus } from "../updateState";
import "../styles/app-update.css";

const POLL_INTERVAL_MS = 1200;
const BLOCKING_PHASES = new Set(["checking", "downloading", "verifying", "installing", "available", "failed"]);

type GatePhase = "checking" | "downloading" | "verifying" | "installing" | "failed" | "done";

type GateView = {
  phase: GatePhase;
  currentVersion: string;
  targetVersion: string;
  progress: number | null;
  message: string;
  error: string;
};

function resolveProgress(status: AppUpdateStatus | PatchStatus | null): number | null {
  const raw = status?.progress;
  if (!raw) return null;
  const value = (raw as { percentage?: number; percent?: number }).percentage ?? (raw as { percent?: number }).percent;
  return typeof value === "number" && Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null;
}

function phaseOf(state: string): GatePhase {
  if (state === "downloading" || state === "verifying" || state === "checking" || state === "installing" || state === "failed") return state;
  return "done";
}

function toGateView(status: AppUpdateStatus | null, patch: PatchStatus | null): GateView {
  const currentVersion = patch?.current_version || status?.current_version || "--";
  if (patch?.patch) {
    const phase = phaseOf(patch.state ?? "idle");
    const message = phase === "installing"
      ? "更新安装已启动，程序即将自动重启，完成后自动回到工作台。"
      : phase === "failed"
        ? "更新失败，请检查网络后重试。"
        : `正在准备增量更新（${patch.patch.from_version} → ${patch.patch.to_version}）…`;
    return { phase, currentVersion, targetVersion: patch.patch.to_version, progress: resolveProgress(patch), message, error: patch.error ?? "" };
  }
  const release = status?.release ?? null;
  const phase = phaseOf(status?.state ?? "idle");
  const message = phase === "installing"
    ? "更新安装已启动，程序即将自动重启，完成后自动回到工作台。"
    : phase === "failed"
      ? "更新失败，请检查网络后重试。"
      : "检测到软件新版本，正在自动更新。";
  return { phase, currentVersion, targetVersion: release?.version ?? "", progress: resolveProgress(status), message, error: status?.error ?? "" };
}

function isPendingUpdate(status: AppUpdateStatus | null, patch: PatchStatus | null): boolean {
  if (patch?.patch && BLOCKING_PHASES.has(patch.state)) return true;
  if (status?.release && BLOCKING_PHASES.has(status.state)) return true;
  return false;
}

/**
 * 启动前置更新门：软件打开后、进入任何业务页面前，先检查并安装新版本。
 * - 无新版本 / 接口异常（fail-open）：立即放行，避免把无网或本地环境问题卡死用户。
 * - 有新版本：全屏展示下载/安装进度，强制更新（mandatory）时静默自动安装、不可跳过；
 *   非强制更新给予「立即更新 / 跳过，直接进入」选择。安装完成后依赖后端已拉起的
 *   安装器/自退重启链路完成版本切换并自动回到工作台。
 */
export function StartupUpdateGate({ onPassed }: { onPassed: () => void }) {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [patch, setPatch] = useState<PatchStatus | null>(null);
  const [restarting, setRestarting] = useState(false);
  const passedRef = useRef(false);
  const updateSeenRef = useRef(false);

  const pass = () => {
    if (passedRef.current) return;
    passedRef.current = true;
    onPassed();
  };

  // 轮询两个状态接口；fail-open：接口异常或明确无更新即放行。
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async () => {
      let poll = false;
      try {
        const next = await appUpdateApi.patchStatus();
        if (cancelled) return;
        setPatch(next);
        if (next.patch) updateSeenRef.current = true;
        if (shouldPollUpdateStatus(next)) poll = true;
      } catch (error) {
        if (cancelled) return;
        setPatch((previous) => previous && previous.state === "installing" ? previous : { state: "failed", current_version: "", patch: null, progress: null, error: error instanceof Error ? error.message : "更新请求失败" });
      }
      try {
        const next = await appUpdateApi.status();
        if (cancelled) return;
        setStatus(next);
        if (next.release) updateSeenRef.current = true;
        if (shouldPollUpdateStatus(next)) poll = true;
      } catch (error) {
        if (cancelled) return;
        setStatus((previous) => previous && previous.state === "installing"
          ? previous
          : { state: "failed", current_version: previous?.current_version ?? "", release: previous?.release ?? null, progress: previous?.progress ?? null, error: error instanceof Error ? error.message : "更新请求失败" });
      }
      // fail-open：两端均未检测到更新（且无活跃任务）即放行。
      if (!cancelled && !updateSeenRef.current && !poll) {
        pass();
        return;
      }
      if (poll) timer = setTimeout(() => void refresh(), POLL_INTERVAL_MS);
    };

    void refresh();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, []);

  const view = toGateView(status, patch);
  const pending = isPendingUpdate(status, patch);
  const inProgress = view.phase === "checking" || view.phase === "downloading" || view.phase === "verifying" || view.phase === "installing";
  const mandatory = Boolean(status?.release?.mandatory);
  const canInstall = view.phase === "done" && pending === false;

  // 检测到更新后自动开始安装（patch 优先）；mandatory 静默自动，非强制由用户点击。
  const startInstall = (auto: boolean) => {
    if (passedRef.current) return;
    if (patch?.patch) {
      void appUpdateApi.patchInstall().catch(() => setPatch((previous) => previous ? { ...previous, state: "failed", error: "更新请求失败" } : previous));
      if (auto) setRestarting(true);
      return;
    }
    if (status?.release) {
      void appUpdateApi.install().catch(() => setStatus((previous) => previous ? { ...previous, state: "failed", error: "更新请求失败" } : previous));
      if (auto) setRestarting(true);
    }
  };

  useEffect(() => {
    if (!pending || passedRef.current) return;
    if (mandatory) {
      // 强制更新：自动开始安装，不给跳过。
      if (view.phase === "done" || view.phase === "checking") startInstall(true);
    }
    // 非强制：等待用户点击「立即更新」或「跳过」。
  }, [pending, mandatory, view.phase]);

  // 安装进入完成态（后端已拉起安装器/自退重启）后，若页面仍在则提示手动重启兜底。
  useEffect(() => {
    if (restarting && view.phase === "failed") setRestarting(false);
  }, [view.phase, restarting]);

  const showScreen = !passedRef.current && (pending || view.phase === "failed");

  if (!showScreen) return null;

  const isFailed = view.phase === "failed";
  return (
    <div className="app-update-backdrop" role="presentation" style={{ background: "rgb(15 23 42 / 96%)" }}>
      <section className="app-update-dialog" role="dialog" aria-modal="true" aria-labelledby="startup-update-title" aria-describedby="startup-update-description" tabIndex={-1}>
        <div className="app-update-heading">
          <span className="app-update-badge">软件更新</span>
          <h2 id="startup-update-title">正在同步最新版本</h2>
        </div>
        <p id="startup-update-description" className="app-update-message">{view.message}</p>
        <dl className="app-update-versions">
          <div><dt>当前版本</dt><dd>{view.currentVersion}</dd></div>
          {view.targetVersion && <div><dt>目标版本</dt><dd>{view.targetVersion}</dd></div>}
        </dl>
        {inProgress && <div className="app-update-progress" aria-live="polite">
          {view.progress !== null && <>
            <div className="app-update-progress-track" role="progressbar" aria-label="更新下载进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.progress)}><span style={{ width: `${view.progress}%` }} /></div>
            <strong>{Math.round(view.progress)}%</strong>
          </>}
          {view.phase === "installing" && <small>安装启动后程序会自动重启，完成时间取决于安装程序。</small>}
        </div>}
        {isFailed && <p className="app-update-error" role="alert">{view.error || "更新过程中发生未知错误，请检查网络后重试。"}</p>}
        {isFailed && !mandatory && (
          <div className="app-update-actions">
            <button type="button" className="app-update-primary" onClick={() => window.location.reload()}>重新检查并重试</button>
            <button type="button" className="app-update-secondary" onClick={pass}>跳过，直接进入</button>
          </div>
        )}
        {isFailed && mandatory && (
          <div className="app-update-actions">
            <button type="button" className="app-update-primary" onClick={() => window.location.reload()}>重新检查并重试</button>
          </div>
        )}
        {!inProgress && !isFailed && pending && !mandatory && (
          <div className="app-update-actions">
            <button type="button" className="app-update-primary" onClick={() => startInstall(false)}>立即更新</button>
            <button type="button" className="app-update-secondary" onClick={pass}>跳过，直接进入</button>
          </div>
        )}
      </section>
    </div>
  );
}

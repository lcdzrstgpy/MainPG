import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

import { appUpdateApi } from "../api/appUpdateApi";
import {
  preserveVerifiedRelease,
  shouldPollUpdateStatus,
  shouldShowDeferAction,
  toUpdateDialogState,
  type AppUpdateStatus,
} from "../updateState";
import { getFocusTrapTargetIndex, shouldRecoverDialogFocus } from "./focusRecovery";
import "../styles/app-update.css";

const POLL_INTERVAL_MS = 1200;
const FOCUSABLE_SELECTOR = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "更新请求失败，请稍后重试。";
}

export function AppUpdateDialog() {
  const [status, setStatus] = useState<AppUpdateStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [pollGeneration, setPollGeneration] = useState(0);
  const [postPending, setPostPending] = useState(false);
  const postPendingRef = useRef(false);
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const wasVisibleRef = useRef(false);

  useLayoutEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const refresh = async () => {
      try {
        const next = await appUpdateApi.status();
        if (cancelled) return;
        setStatus((previous) => preserveVerifiedRelease(previous, next));
        if (shouldPollUpdateStatus(next)) timer = setTimeout(() => void refresh(), POLL_INTERVAL_MS);
      } catch (error) {
        if (cancelled) return;
        setStatus((previous) => {
          if (!previous || previous.state === "installing") return previous;
          return { ...previous, state: "failed", error: errorMessage(error) };
        });
      }
    };

    void refresh();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [pollGeneration]);

  const view = toUpdateDialogState(status ?? {});
  const isVisible = view.visible && !(dismissed && !view.mandatory);

  useEffect(() => {
    if (!isVisible) {
      if (wasVisibleRef.current) {
        previousFocusRef.current?.focus();
        previousFocusRef.current = null;
        wasVisibleRef.current = false;
      }
      return;
    }

    if (!wasVisibleRef.current) {
      previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      wasVisibleRef.current = true;
      const initialFocus = dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ?? dialogRef.current;
      initialFocus?.focus();
    }
  }, [isVisible]);

  useEffect(() => () => previousFocusRef.current?.focus(), []);

  const inProgress = view.phase === "checking" || view.phase === "downloading" || view.phase === "verifying" || view.phase === "installing";
  const deferAvailable = shouldShowDeferAction(view);

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !isVisible) return;

    const activeElement = document.activeElement;
    if (shouldRecoverDialogFocus({
      visible: isVisible,
      focusedElementStillInDialog: activeElement === dialog || dialog.contains(activeElement),
      focusedElementDisabled: activeElement instanceof HTMLButtonElement && activeElement.disabled,
    })) {
      dialog.focus();
    }
  }, [deferAvailable, isVisible, postPending, view.phase]);

  const trapFocus = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? []);
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }

    const activeIndex = focusable.indexOf(document.activeElement as HTMLElement);
    const targetIndex = getFocusTrapTargetIndex({ focusableCount: focusable.length, activeIndex, shiftKey: event.shiftKey });
    if (targetIndex !== null) {
      event.preventDefault();
      focusable[targetIndex].focus();
    }
  };

  if (!isVisible) return null;

  const beginPost = () => {
    if (postPendingRef.current) return false;
    postPendingRef.current = true;
    setPostPending(true);
    return true;
  };

  const finishPost = () => {
    postPendingRef.current = false;
    setPostPending(false);
  };

  const install = async () => {
    if (!beginPost()) return;
    try {
      const next = await appUpdateApi.install();
      setDismissed(false);
      setStatus((previous) => preserveVerifiedRelease(previous, next));
      setPollGeneration((generation) => generation + 1);
    } catch (error) {
      setStatus((previous) => previous ? { ...previous, state: "failed", error: errorMessage(error) } : previous);
    } finally {
      finishPost();
    }
  };

  const retry = async () => {
    if (!beginPost()) return;
    try {
      const next = await appUpdateApi.check();
      setStatus((previous) => preserveVerifiedRelease(previous, next));
      setPollGeneration((generation) => generation + 1);
    } catch (error) {
      setStatus((previous) => previous ? { ...previous, state: "failed", error: errorMessage(error) } : previous);
    } finally {
      finishPost();
    }
  };

  return (
    <div className="app-update-backdrop" role="presentation">
      <section ref={dialogRef} className="app-update-dialog" role="dialog" aria-modal="true" aria-labelledby="app-update-title" aria-describedby="app-update-description" tabIndex={-1} onKeyDown={trapFocus}>
        <div className="app-update-heading">
          <span className="app-update-badge">{view.mandatory ? "必须更新" : "发现新版本"}</span>
          <h2 id="app-update-title">Windows 客户端更新</h2>
        </div>
        <p id="app-update-description" className="app-update-message">{view.message}</p>
        <dl className="app-update-versions">
          <div><dt>当前版本</dt><dd>{view.currentVersion}</dd></div>
          <div><dt>目标版本</dt><dd>{view.targetVersion}</dd></div>
        </dl>
        {view.notes.length > 0 && <div className="app-update-notes"><h3>更新说明</h3><ul>{view.notes.map((note) => <li key={note}>{note}</li>)}</ul></div>}
        {inProgress && <div className="app-update-progress" aria-live="polite">
          {view.progress !== null && <><div className="app-update-progress-track" role="progressbar" aria-label="更新下载进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(view.progress)}><span style={{ width: `${view.progress}%` }} /></div><strong>{Math.round(view.progress)}%</strong></>}
          {view.phase === "installing" && <small>安装启动后服务会自动重启，完成时间取决于 Windows 安装程序。</small>}
        </div>}
        {view.phase === "failed" && <p className="app-update-error" role="alert">{view.error || "更新过程中发生未知错误。"}</p>}
        <div className="app-update-actions">
          {view.phase === "failed" ? <button type="button" className="app-update-primary" onClick={() => void retry()} disabled={postPending}>重新检查并重试</button> : !inProgress && <button type="button" className="app-update-primary" onClick={() => void install()} disabled={postPending}>立即更新</button>}
          {deferAvailable && <button type="button" className="app-update-secondary" onClick={() => setDismissed(true)} disabled={postPending}>稍后</button>}
        </div>
      </section>
    </div>
  );
}

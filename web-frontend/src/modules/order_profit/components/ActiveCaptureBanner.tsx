import { useEffect, useRef, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { isActiveCaptureStatus } from "../data/captureTaskModel";
import {
  CAPTURE_TASK_EVENT,
  clearActiveCaptureTask,
  loadActiveCaptureTask,
  type PersistedCaptureTask,
} from "../data/captureTaskStorage";
import type { OrderProfitCaptureStatus } from "../types";

/**
 * 跨页面的采集任务条：
 * 用户离开“数据采集”后，任务仍在跑；这里负责继续显示进度、允许终止，
 * 并在结束后刷新当前数据页。
 */
export function ActiveCaptureBanner({
  enabled,
  onOpenCapture,
  onFinished,
}: {
  enabled: boolean;
  onOpenCapture?: () => void;
  onFinished?: () => void;
}) {
  const [task, setTask] = useState<PersistedCaptureTask | null>(() => loadActiveCaptureTask());
  const [status, setStatus] = useState<OrderProfitCaptureStatus | null>(null);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    const sync = () => setTask(loadActiveCaptureTask());
    window.addEventListener(CAPTURE_TASK_EVENT, sync);
    return () => window.removeEventListener(CAPTURE_TASK_EVENT, sync);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    setTask(loadActiveCaptureTask());
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !task) {
      setStatus(null);
      return;
    }
    let cancelled = false;
    let timer: number | null = null;
    const tick = async () => {
      try {
        const next = await orderProfitApi.captureStatus(task.commandId);
        if (cancelled) return;
        setStatus(next);
        if (isActiveCaptureStatus(next.status)) {
          timer = window.setTimeout(() => void tick(), 2000);
          return;
        }
        showToast(
          next.status_text || (next.ok ? "采集完成" : "采集已结束"),
          next.ok ? "success" : next.status === "cancelled" ? "info" : "error",
        );
        clearActiveCaptureTask(task.commandId);
        setTask(null);
        onFinishedRef.current?.();
      } catch {
        if (cancelled) return;
        clearActiveCaptureTask(task.commandId);
        setTask(null);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [enabled, task?.commandId]);

  if (!enabled || !task) return null;

  const percent = status?.progress?.percent ?? 0;
  const step = status?.progress?.step_label
    || (status?.status === "queued" || status?.status === "sent" ? "等待插件接单" : "正在读取进度");

  const stop = async () => {
    try {
      await orderProfitApi.cancelCapture(task.commandId);
      showToast("已请求终止采集：已完成的数据会保留", "info");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  return (
    <section className="op-task-banner" aria-label="采集任务进行中">
      <span className="op-chip is-ok">采集中</span>
      <strong>{task.label}</strong>
      <span className="op-task-step">{step}</span>
      <div className="op-progress-track">
        <div className="op-progress-bar" style={{ width: `${percent}%` }} />
      </div>
      <b className="op-task-percent">{percent}%</b>
      {onOpenCapture ? (
        <button type="button" className="op-btn op-btn-sm" onClick={onOpenCapture}>
          查看
        </button>
      ) : null}
      <button type="button" className="op-btn op-btn-sm" onClick={() => void stop()}>
        终止
      </button>
    </section>
  );
}

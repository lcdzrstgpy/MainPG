import { useEffect, useRef } from "react";
import { getAuthToken } from "../../transport/http/client";

type UseChangePollerOptions = {
  /** 轻量变更指纹接口（返回 `{ revision: string }`） */
  url: string;
  /** 指纹变化时回调：拉取全量数据刷新容器（quiet 模式，不显示 loading） */
  onChange: () => void;
  /** 额外请求头（如 X-Workspace-ID），需与数据列表请求保持一致 */
  headers?: Record<string, string>;
  /** 轮询间隔（毫秒），默认 8000 */
  intervalMs?: number;
  /** 是否启用，默认 true */
  enabled?: boolean;
};

/**
 * 容器级自动刷新轮询（避免整页刷新）：
 * - 仅页面可见（document.visibilityState === "visible"）时轮询，切走标签页自动暂停
 * - 页面重新获得焦点时立即检查一次（"采集完/核价完回到页面自动刷新"）
 * - 指纹（revision）不变时不触发 onChange，避免日常无意义的全量刷新
 * - 首次建立基线指纹，只记录不触发回调
 */
export function useChangePoller({ url, onChange, headers, intervalMs = 8000, enabled = true }: UseChangePollerOptions) {
  const revisionRef = useRef<string | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const headersRef = useRef(headers);
  headersRef.current = headers;

  useEffect(() => {
    if (!enabled) return;
    let timerId = 0;
    let activeController: AbortController | null = null;

    const checkOnce = async () => {
      if (document.visibilityState !== "visible") return;
      // 上一请求尚未返回时跳过本轮，而不是 abort 重启：否则慢响应(耗时≥interval)
      // 下每 tick 都中断重发，revision 永不更新、onChange 永不触发、持续空打服务器。
      if (activeController) return;
      const controller = new AbortController();
      activeController = controller;
      try {
        const requestHeaders: Record<string, string> = { ...(headersRef.current ?? {}) };
        const token = getAuthToken();
        if (token) requestHeaders.authorization = `Bearer ${token}`;
        const response = await fetch(url, { signal: controller.signal, cache: "no-store", headers: requestHeaders });
        if (!response.ok) return;
        const payload = (await response.json()) as { revision?: string | null };
        const revision = payload.revision ?? "";
        if (revisionRef.current !== null && revision !== revisionRef.current) {
          onChangeRef.current();
        }
        revisionRef.current = revision;
      } catch {
        // 网络抖动 / 请求被取消：静默跳过，等待下一轮
      } finally {
        if (activeController === controller) activeController = null;
      }
    };

    const startTimer = () => {
      if (timerId || document.visibilityState !== "visible") return;
      timerId = window.setInterval(() => {
        void checkOnce();
      }, intervalMs);
    };
    const stopTimer = () => {
      if (timerId) {
        window.clearInterval(timerId);
        timerId = 0;
      }
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        void checkOnce();
        startTimer();
      } else {
        stopTimer();
      }
    };
    const handleFocus = () => {
      void checkOnce();
    };

    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", handleFocus);
    // 初始建立基线指纹并启动轮询（首轮只记录，不触发 onChange）
    void checkOnce();
    startTimer();

    return () => {
      stopTimer();
      document.removeEventListener("visibilitychange", handleVisibility);
      window.removeEventListener("focus", handleFocus);
      activeController?.abort();
    };
  }, [url, intervalMs, enabled]);
}

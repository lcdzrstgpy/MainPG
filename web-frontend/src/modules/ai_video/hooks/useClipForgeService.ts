import { useEffect, useRef, useState } from "react";

import { getClipForgeStatus, startClipForge, type ClipForgeStatus } from "../api/clipforgeApi";
import { pollDelayForState } from "../state/clipforgeServiceState";

// sidecar 的生命周期是「起 → 就绪 → 退出」，一次性请求必然读到过期结果。
// 这里用 setTimeout 串行轮询（绝不与上一次请求重叠），并用请求序号丢弃过期响应，
// 避免慢的旧请求覆盖新状态（服务退出后仍显示「已连接」的根因）。
export function useClipForgeService(): {
  status: ClipForgeStatus | null;
  error: string;
  refresh: () => void;
  start: () => void;
} {
  const [status, setStatus] = useState<ClipForgeStatus | null>(null);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | null>(null);
  // 定时器回调需要拿到最新的请求闭包，用 ref 持有，避免把副作用塞进 setState 更新函数。
  const runRef = useRef<(asStart: boolean) => void>(() => {});

  runRef.current = (asStart: boolean) => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    // 新请求前终止在途请求：轮询永不并发，回调里无需再区分「旧实例」。
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const request = asStart ? startClipForge(controller.signal) : getClipForgeStatus(controller.signal);
    request
      .then((next) => {
        // 只有仍是最新一次请求的响应才允许写入，防止慢的旧请求回滚新状态。
        if (requestSequence.current !== sequence) return;
        setStatus(next);
        setError("");
        const delay = pollDelayForState(next.state);
        if (delay === null) return;
        timerRef.current = window.setTimeout(() => {
          timerRef.current = null;
          runRef.current(false);
        }, delay);
      })
      .catch((cause: unknown) => {
        if (requestSequence.current !== sequence) return;
        // 只暴露错误文案，不清空 status：后端持久化的 failed/error 必须继续可见。
        const message = cause instanceof Error ? cause.message : "";
        setError(message || (asStart ? "AI 视频服务启动失败" : "无法读取 AI 视频服务状态"));
        // 一次瞬时失败（后端重启 / 503）不能让轮询永久停摆：否则 status 停在上一次
        // 的值、徽标长期显示「已连接」，sidecar 已死也无从发现。退避续订，卸载时会被清理。
        if (timerRef.current === null) {
          timerRef.current = window.setTimeout(() => {
            timerRef.current = null;
            runRef.current(false);
          }, 3000);
        }
      });
  };

  useEffect(() => {
    runRef.current(false);
    return () => {
      // 卸载：让所有在途响应失效，并终止请求与定时器。
      requestSequence.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  return {
    status,
    error,
    refresh: () => runRef.current(false),
    start: () => runRef.current(true),
  };
}

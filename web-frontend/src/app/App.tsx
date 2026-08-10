import { useEffect, useState } from "react";

import { WorkspaceShell } from "./layout/WorkspaceShell";
import { AuthPage } from "../modules/customer/pages/AuthPage";
import { clearAuthSession, getAuthToken, httpJson } from "../transport/http/client";

type MeResponse = {
  user_id?: string;
  username?: string;
  role?: string;
  workspace_code?: string;
};

// 心跳间隔：需小于云端会话失联阈值（90 秒），保证在线页面不被误判离线。
const HEARTBEAT_INTERVAL_MS = 30_000;

export function App() {
  const [enteredWorkspace, setEnteredWorkspace] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      setReady(true);
      return;
    }
    httpJson<MeResponse>("/api/customer/me")
      .then(() => setEnteredWorkspace(true))
      .catch(() => {
        clearAuthSession();
        setEnteredWorkspace(false);
      })
      .finally(() => setReady(true));
  }, []);

  // 登录态期间：定期心跳保持云端会话活跃；关闭页面时主动登出（刷新页面不登出）。
  useEffect(() => {
    if (!enteredWorkspace) return;
    const heartbeatTimer = window.setInterval(() => {
      httpJson<{ ok?: boolean }>("/api/customer/heartbeat", { method: "POST" }).catch(() => {
        // 心跳失败不中断页面；云端有失联阈值兜底。
      });
    }, HEARTBEAT_INTERVAL_MS);

    const onPageHide = (event: PageTransitionEvent) => {
      if (event.persisted) return; // bfcache 回退恢复，不登出
      const token = getAuthToken();
      if (!token) return;
      const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
      if (navigation?.type === "reload") return; // 刷新页面不退出登录
      try {
        fetch("/api/customer/logout", {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
          keepalive: true,
        }).catch(() => {
          // 关闭页面时尽力登出；即使失败，云端失联兜底也会在阈值后释放登录态。
        });
      } catch {
        // 忽略异常
      }
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.clearInterval(heartbeatTimer);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [enteredWorkspace]);

  async function signOut() {
    try {
      await httpJson<{ ok?: boolean }>("/api/customer/logout", { method: "POST" });
    } catch {
      // 退出接口异常不阻塞本地登出
    } finally {
      clearAuthSession();
      setEnteredWorkspace(false);
    }
  }

  if (!ready) return null;

  return enteredWorkspace ? <WorkspaceShell onSignOut={signOut} /> : <AuthPage onEnter={() => setEnteredWorkspace(true)} />;
}

import { useEffect, useState } from "react";

import { WorkspaceShell } from "./layout/WorkspaceShell";
import { AuthPage } from "../modules/customer/pages/AuthPage";
import { StartupUpdateGate } from "../modules/app_update/components/StartupUpdateGate";
import { GlobalToast } from "../shared/components/GlobalToast";
import { clearAuthSession, getAuthAccount, getAuthToken, httpJson } from "../transport/http/client";

type MeResponse = {
  user_id?: string;
  username?: string;
  role?: string;
  workspace_code?: string;
};

// 查询仍在处理中的产品任务数（queued / running），供「关闭页面前提醒」判断。
async function fetchActiveTaskCount(): Promise<number> {
  try {
    const account = getAuthAccount<{ workspace_id?: string; workspace_code?: string }>() ?? {};
    const headers: Record<string, string> = { "X-Workspace-ID": account.workspace_id || account.workspace_code || "local" };
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetch("/api/product-processing/tasks/active-count", { headers });
    if (!response.ok) return 0;
    const data = (await response.json().catch(() => ({}))) as { count?: unknown };
    return typeof data?.count === "number" ? data.count : 0;
  } catch {
    return 0;
  }
}

export function App() {
  const [enteredWorkspace, setEnteredWorkspace] = useState(false);
  const [playEntryAnimation, setPlayEntryAnimation] = useState(false);
  const [ready, setReady] = useState(false);
  const [accountRole, setAccountRole] = useState("operator");
  // 启动前置更新门：软件打开后先检查/安装新版本，通过后才进入登录或主页。
  // 更新门自身 fail-open，无检测到更新或接口异常时立即放行，不阻塞正常使用。
  const [updateGatePassed, setUpdateGatePassed] = useState(false);
  // 仍在处理中的产品任务数：用于「关闭页面前列队任务提醒」。
  const [activeTaskCount, setActiveTaskCount] = useState(0);

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      setReady(true);
      return;
    }
    httpJson<MeResponse>("/api/customer/me")
      .then((me) => {
        setEnteredWorkspace(true);
        setAccountRole(me.role ?? "operator");
      })
      .catch(() => {
        clearAuthSession();
        setEnteredWorkspace(false);
      })
      .finally(() => setReady(true));
  }, []);

  // 任意接口返回登录失效（登录超时 / 远程会话缺失）时统一回到登录页，避免用户
  // 停留在工作区内反复看到报错提示。
  useEffect(() => {
    const onSessionExpired = () => {
      clearAuthSession();
      setPlayEntryAnimation(false);
      setEnteredWorkspace(false);
    };
    window.addEventListener("auth:session-expired", onSessionExpired);
    return () => window.removeEventListener("auth:session-expired", onSessionExpired);
  }, []);

  // 桌面端心跳：告知后端页面仍存活；页面关闭(pagehide)时上报 bye，
  // 让后端自动终止进程并释放锁定的 MainPG.exe（刷新重载由新页面心跳取消上次退出）。
  // 仅桌面运行时启用 watchdog；服务器环境这些接口为 no-op，不会自动退出。
  useEffect(() => {
    const heartbeat = () => {
      try {
        navigator.sendBeacon("/api/runtime/heartbeat", "{}");
      } catch {
        // 发送失败忽略，后端有 idle 兜底
      }
    };
    const bye = () => {
      try {
        navigator.sendBeacon("/api/runtime/bye", "{}");
      } catch {
        // 忽略
      }
    };
    heartbeat();
    const timer = window.setInterval(heartbeat, 15_000);
    window.addEventListener("pagehide", bye);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("pagehide", bye);
    };
  }, []);

  // 轮询仍在处理中的产品任务数：登录进入工作区后才轮询，供关闭提醒判断。
  useEffect(() => {
    if (!enteredWorkspace || !getAuthToken()) return;
    let stopped = false;
    const poll = async () => {
      if (stopped) return;
      const count = await fetchActiveTaskCount();
      if (!stopped) setActiveTaskCount(count);
    };
    void poll();
    const timer = window.setInterval(poll, 15_000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [enteredWorkspace]);

  // 关闭/刷新页面前提醒：只要还有任务在处理，就弹原生确认框。
  // 用户选「离开」→ pagehide 上报 bye → 后端在确认真退出时取消任务并按 50% 结算；
  // 选「留在本页」则不关闭。刷新(新页面心跳)会在后台救回，不误取消任务。
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (activeTaskCount <= 0) return;
      event.preventDefault();
      event.returnValue = "有任务正在处理中，关闭将取消任务并按冻结积分 50% 收取费用，确定要离开吗？";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [activeTaskCount]);

  async function signOut() {
    try {
      await httpJson<{ ok?: boolean }>("/api/customer/logout", { method: "POST" });
    } catch {
      // 退出接口异常不阻塞本地登出
    } finally {
      clearAuthSession();
      setPlayEntryAnimation(false);
      setEnteredWorkspace(false);
    }
  }

  function enterWorkspaceAfterLogin() {
    setPlayEntryAnimation(true);
    setEnteredWorkspace(true);
  }

  // 启动更新门优先于一切页面：软件打开即检查并安装新版本，未通过前不渲染登录/主页。
  if (!updateGatePassed) {
    return <StartupUpdateGate onPassed={() => setUpdateGatePassed(true)} />;
  }

  if (!ready) return null;

  return <>
    {enteredWorkspace ? (
      <WorkspaceShell
        currentRole={accountRole}
        onSignOut={signOut}
        playEntryAnimation={playEntryAnimation}
        onEntryAnimationComplete={() => setPlayEntryAnimation(false)}
      />
    ) : (
      <AuthPage onEnter={enterWorkspaceAfterLogin} />
    )}
    <GlobalToast />
  </>;
}

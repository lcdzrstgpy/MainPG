import { useEffect, useState } from "react";

import { WorkspaceShell } from "./layout/WorkspaceShell";
import { AuthPage } from "../modules/customer/pages/AuthPage";
import { AppUpdateDialog } from "../modules/app_update/components/AppUpdateDialog";
import { GlobalToast } from "../shared/components/GlobalToast";
import { clearAuthSession, getAuthToken, httpJson } from "../transport/http/client";

type MeResponse = {
  user_id?: string;
  username?: string;
  role?: string;
  workspace_code?: string;
};

export function App() {
  const [enteredWorkspace, setEnteredWorkspace] = useState(false);
  const [playEntryAnimation, setPlayEntryAnimation] = useState(false);
  const [ready, setReady] = useState(false);
  const [accountRole, setAccountRole] = useState("operator");

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
    <AppUpdateDialog />
    <GlobalToast />
  </>;
}

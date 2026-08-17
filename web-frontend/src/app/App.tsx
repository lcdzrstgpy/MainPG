import { useEffect, useState } from "react";

import { WorkspaceShell } from "./layout/WorkspaceShell";
import { AuthPage } from "../modules/customer/pages/AuthPage";
import { AppUpdateDialog } from "../modules/app_update/components/AppUpdateDialog";
import { clearAuthSession, getAuthToken, httpJson } from "../transport/http/client";

type MeResponse = {
  user_id?: string;
  username?: string;
  role?: string;
  workspace_code?: string;
};

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

  return <>
    {enteredWorkspace ? <WorkspaceShell onSignOut={signOut} /> : <AuthPage onEnter={() => setEnteredWorkspace(true)} />}
    <AppUpdateDialog />
  </>;
}

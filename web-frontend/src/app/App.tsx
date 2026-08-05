import { useState } from "react";

import { WorkspaceShell } from "./layout/WorkspaceShell";
import { AuthPage } from "../modules/customer/pages/AuthPage";

export function App() {
  const [enteredWorkspace, setEnteredWorkspace] = useState(false);

  return enteredWorkspace ? <WorkspaceShell onSignOut={() => setEnteredWorkspace(false)} /> : <AuthPage onEnter={() => setEnteredWorkspace(true)} />;
}

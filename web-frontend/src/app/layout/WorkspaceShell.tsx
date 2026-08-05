import { useMemo, useState } from "react";

import { workspaceModules, type WorkspaceModuleId } from "../navigation/modules";
import { Sidebar } from "./Sidebar";
import { TopNavigation } from "./TopNavigation";
import { WorkspaceHomePage } from "../../modules/dashboard/pages/WorkspaceHomePage";
import { ProfitActivityTestPage } from "../../modules/profit_activity/pages/ProfitActivityTestPage";
import { EmptyModulePage } from "../../shared/components/EmptyModulePage";

type WorkspaceShellProps = { onSignOut: () => void };

export function WorkspaceShell({ onSignOut }: WorkspaceShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeId, setActiveId] = useState<WorkspaceModuleId>("dashboard");
  const [tabIds, setTabIds] = useState<WorkspaceModuleId[]>(["dashboard"]);
  const modulesById = useMemo(() => new Map(workspaceModules.map((module) => [module.id, module])), []);
  const tabs = tabIds.map((id) => modulesById.get(id)!).filter(Boolean);
  const activeModule = modulesById.get(activeId)!;

  const openModule = (id: WorkspaceModuleId) => {
    setTabIds((current) => current.includes(id) ? current : [...current, id]);
    setActiveId(id);
  };
  const closeTab = (id: WorkspaceModuleId) => {
    setTabIds((current) => {
      const next = current.filter((item) => item !== id);
      if (activeId === id) setActiveId(next[next.length - 1] ?? "dashboard");
      return next;
    });
  };

  return (
    <main className="workspace-shell">
      <Sidebar collapsed={sidebarCollapsed} activeId={activeId} modules={workspaceModules} onSelect={openModule} />
      <section className="workspace-main">
        <TopNavigation activeId={activeId} tabs={tabs} onToggleSidebar={() => setSidebarCollapsed((value) => !value)} onSelectTab={setActiveId} onCloseTab={closeTab} onSignOut={onSignOut} />
        <div className="content-card">
          {activeId === "dashboard" ? <WorkspaceHomePage onOpenModule={openModule} /> : activeId === "profit_activity" ? <ProfitActivityTestPage /> : <EmptyModulePage module={activeModule} />}
        </div>
      </section>
    </main>
  );
}

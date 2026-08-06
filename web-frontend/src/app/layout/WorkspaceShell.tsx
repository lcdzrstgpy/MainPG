import { useMemo, useRef, useState } from "react";

import { workspaceModules, type WorkspaceModuleId } from "../navigation/modules";
import { Sidebar } from "./Sidebar";
import { TopNavigation, type WorkspaceTab } from "./TopNavigation";
import { WorkspaceHomePage } from "../../modules/dashboard/pages/WorkspaceHomePage";
import { DailySelectionPage } from "../../modules/daily_selection/pages/DailySelectionPage";
import { BasicSettingsPage } from "../../modules/basic_settings/pages/BasicSettingsPage";
import { ProfitActivityTestPage } from "../../modules/profit_activity/pages/ProfitActivityTestPage";
import { PriceVerificationPage } from "../../modules/price_verification/pages/PriceVerificationPage";
import { ProductProcessingPage } from "../../modules/product_processing/pages/ProductProcessingPage";
import { EmptyModulePage } from "../../shared/components/EmptyModulePage";

type WorkspaceShellProps = { onSignOut: () => void };

const MAX_COLLECTION_PANELS = 6;

function moduleTab(id: WorkspaceModuleId): WorkspaceTab {
  const module = workspaceModules.find((item) => item.id === id)!;
  return { key: id, moduleId: id, label: module.label, icon: module.icon };
}

export function WorkspaceShell({ onSignOut }: WorkspaceShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeTabKey, setActiveTabKey] = useState("dashboard");
  const [tabs, setTabs] = useState<WorkspaceTab[]>([moduleTab("dashboard")]);
  const [workspaceNotice, setWorkspaceNotice] = useState("");
  const collectionSequence = useRef(0);
  const modulesById = useMemo(() => new Map(workspaceModules.map((module) => [module.id, module])), []);
  const activeTab = tabs.find((tab) => tab.key === activeTabKey) ?? tabs[0];
  const activeModuleId = activeTab?.moduleId ?? "dashboard";
  const activeModule = modulesById.get(activeModuleId)!;

  const openModule = (id: WorkspaceModuleId) => {
    if (id === "daily_selection_collection") return;
    setTabs((current) => current.some((tab) => tab.key === id) ? current : [...current, moduleTab(id)]);
    setActiveTabKey(id);
    setWorkspaceNotice("");
  };

  const closeTab = (key: string) => {
    setTabs((current) => {
      const next = current.filter((tab) => tab.key !== key);
      if (activeTabKey === key) setActiveTabKey(next[next.length - 1]?.key ?? "dashboard");
      return next;
    });
  };

  const openCollectionPanel = (directionId: string, directionName: string) => {
    const openPanelCount = tabs.filter((tab) => tab.moduleId === "daily_selection_collection").length;
    if (openPanelCount >= MAX_COLLECTION_PANELS) {
      setWorkspaceNotice(`最多同时打开 ${MAX_COLLECTION_PANELS} 个采集面板，请先关闭一个再继续。`);
      return;
    }

    collectionSequence.current += 1;
    const key = `daily-selection-collection-${collectionSequence.current}`;
    setTabs((current) => [...current, {
      key,
      moduleId: "daily_selection_collection",
      label: `采集·${directionName}`,
      icon: "⌕",
      directionId,
    }]);
    setActiveTabKey(key);
    setWorkspaceNotice("");
  };

  const collectionTabs = tabs.filter((tab) => tab.moduleId === "daily_selection_collection");

  return (
    <main className="workspace-shell">
      <Sidebar collapsed={sidebarCollapsed} activeId={activeModuleId} modules={workspaceModules} onSelect={openModule} />
      <section className="workspace-main">
        <TopNavigation activeKey={activeTabKey} tabs={tabs} onToggleSidebar={() => setSidebarCollapsed((value) => !value)} onSelectTab={setActiveTabKey} onCloseTab={closeTab} onSignOut={onSignOut} />
        <div className="content-card">
          {workspaceNotice && (
            <div className="workspace-notice" role="status">
              <span>!</span>
              <strong>{workspaceNotice}</strong>
              <button type="button" onClick={() => setWorkspaceNotice("")} aria-label="关闭提示">×</button>
            </div>
          )}
          {activeModuleId === "dashboard" && <WorkspaceHomePage onOpenModule={openModule} />}
          {activeModuleId === "daily_selection" && <DailySelectionPage onOpenCollection={openCollectionPanel} />}
          {activeModuleId === "profit_activity" && <ProfitActivityTestPage />}
          {activeModuleId === "basic_settings" && <BasicSettingsPage />}
          {activeModuleId === "price_verification" && <PriceVerificationPage />}
          {activeModuleId === "product_processing" && <ProductProcessingPage />}
          {collectionTabs.map((tab) => (
            <div key={tab.key} hidden={activeTabKey !== tab.key}>
              <DailySelectionPage view="collection" initialDirectionId={tab.directionId} />
            </div>
          ))}
          {activeModuleId !== "dashboard" && activeModuleId !== "daily_selection" && activeModuleId !== "daily_selection_collection" && activeModuleId !== "profit_activity" && activeModuleId !== "basic_settings" && activeModuleId !== "price_verification" && activeModuleId !== "product_processing" && <EmptyModulePage module={activeModule} />}
        </div>
      </section>
    </main>
  );
}

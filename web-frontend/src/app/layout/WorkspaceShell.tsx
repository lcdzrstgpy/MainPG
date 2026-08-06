import { useEffect, useMemo, useRef, useState } from "react";

import { workspaceModules, type WorkspaceModuleId } from "../navigation/modules";
import { Sidebar } from "./Sidebar";
import { TopNavigation, type WorkspaceTab } from "./TopNavigation";
import { WorkspaceHomePage } from "../../modules/dashboard/pages/WorkspaceHomePage";
import { DailySelectionPage } from "../../modules/daily_selection/pages/DailySelectionPage";
import { BasicSettingsPage } from "../../modules/basic_settings/pages/BasicSettingsPage";
import { ProfitActivityProductsPage } from "../../modules/profit_activity/pages/ProfitActivityProductsPage";
import { ProfitActivityTestPage } from "../../modules/profit_activity/pages/ProfitActivityTestPage";
import { PriceVerificationPage } from "../../modules/price_verification/pages/PriceVerificationPage";
import { ProductProcessingVerifyPage } from "../../modules/product_processing/pages/ProductProcessingVerifyPage";
import { EmptyModulePage } from "../../shared/components/EmptyModulePage";

type WorkspaceShellProps = { onSignOut: () => void };

const MAX_COLLECTION_PANELS = 6;

const flatModules = workspaceModules.flatMap((module) => [module, ...(module.children ?? [])]);

function moduleTab(id: WorkspaceModuleId): WorkspaceTab {
  const module = flatModules.find((item) => item.id === id)!;
  return { key: id, moduleId: id, label: module.label, icon: module.icon, iconClass: module.iconClass };
}

export function WorkspaceShell({ onSignOut }: WorkspaceShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const [topbarPinned, setTopbarPinned] = useState(true);
  const [activeTabKey, setActiveTabKey] = useState("dashboard");
  const [tabs, setTabs] = useState<WorkspaceTab[]>([moduleTab("dashboard")]);
  const [workspaceNotice, setWorkspaceNotice] = useState("");
  const [showScrollTop, setShowScrollTop] = useState(false);
  const collectionSequence = useRef(0);
  const contentRef = useRef<HTMLDivElement>(null);
  const modulesById = useMemo(() => new Map(flatModules.map((module) => [module.id, module])), []);
  const activeTab = tabs.find((tab) => tab.key === activeTabKey) ?? tabs[0];
  const activeModuleId = activeTab?.moduleId ?? "dashboard";
  const activeModule = modulesById.get(activeModuleId)!;

  useEffect(() => {
    const content = contentRef.current;

    const updateVisibility = () => {
      const documentHeight = document.documentElement.scrollHeight;
      const windowDistanceToBottom = documentHeight - window.scrollY - window.innerHeight;
      const windowNearBottom = documentHeight > window.innerHeight + 80
        && window.scrollY > 240
        && windowDistanceToBottom < 320;

      const contentDistanceToBottom = content
        ? content.scrollHeight - content.scrollTop - content.clientHeight
        : Number.POSITIVE_INFINITY;
      const contentNearBottom = Boolean(content
        && content.scrollHeight > content.clientHeight + 80
        && content.scrollTop > 180
        && contentDistanceToBottom < 260);

      setShowScrollTop(windowNearBottom || contentNearBottom);
    };

    window.addEventListener("scroll", updateVisibility, { passive: true });
    content?.addEventListener("scroll", updateVisibility, { passive: true });
    window.addEventListener("resize", updateVisibility);
    updateVisibility();

    return () => {
      window.removeEventListener("scroll", updateVisibility);
      content?.removeEventListener("scroll", updateVisibility);
      window.removeEventListener("resize", updateVisibility);
    };
  }, []);

  useEffect(() => {
    setShowScrollTop(false);
  }, [activeTabKey]);

  const scrollBackToTop = () => {
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

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
  const expandedSidebarModuleIds = tabs.map((tab) => tab.moduleId);
  const sidebarTemporarilyExpanded = sidebarCollapsed && sidebarHovered;

  return (
    <main className="workspace-shell">
      <Sidebar
        collapsed={sidebarCollapsed && !sidebarTemporarilyExpanded}
        activeId={activeModuleId}
        expandedModuleIds={expandedSidebarModuleIds}
        modules={workspaceModules}
        onSelect={openModule}
        onHoverChange={setSidebarHovered}
      />
      <section className="workspace-main">
        <TopNavigation sidebarPinned={!sidebarCollapsed} topbarPinned={topbarPinned} activeKey={activeTabKey} tabs={tabs} onToggleSidebar={() => setSidebarCollapsed((value) => !value)} onToggleTopbarPin={() => setTopbarPinned((value) => !value)} onSelectTab={setActiveTabKey} onCloseTab={closeTab} onSignOut={onSignOut} />
        <div className="content-card" ref={contentRef}>
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
          {activeModuleId === "profit_activity_products" && <ProfitActivityProductsPage />}
          {activeModuleId === "basic_settings" && <BasicSettingsPage />}
          {activeModuleId === "price_verification" && <PriceVerificationPage />}
          {activeModuleId === "product_processing" && <ProductProcessingVerifyPage />}
          {collectionTabs.map((tab) => (
            <div key={tab.key} hidden={activeTabKey !== tab.key}>
              <DailySelectionPage view="collection" initialDirectionId={tab.directionId} />
            </div>
          ))}
          {activeModuleId !== "dashboard" && activeModuleId !== "daily_selection" && activeModuleId !== "daily_selection_collection" && activeModuleId !== "profit_activity" && activeModuleId !== "profit_activity_products" && activeModuleId !== "basic_settings" && activeModuleId !== "price_verification" && activeModuleId !== "product_processing" && <EmptyModulePage module={activeModule} />}
        </div>
      </section>
      <button
        type="button"
        className={`scroll-to-top ${showScrollTop ? "is-visible" : ""}`}
        onClick={scrollBackToTop}
        aria-label="返回页面顶部"
        title="返回顶部"
      >
        <span aria-hidden="true">↑</span>
      </button>
    </main>
  );
}

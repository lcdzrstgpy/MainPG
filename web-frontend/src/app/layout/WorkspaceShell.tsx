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
import { ProductProcessingTaskPage } from "../../modules/product_processing/pages/ProductProcessingTaskPage";
import type { ProductProcessingOptions } from "../../modules/product_processing/types";
import { EmptyModulePage } from "../../shared/components/EmptyModulePage";

type WorkspaceShellProps = { onSignOut: () => void };

const MAX_COLLECTION_PANELS = 6;
const MAX_PROCESSING_PANELS = 3;

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
  // 利润活动页 keep-alive：首次打开后保持挂载，切换走仅隐藏，表单/文件/过滤进度不丢失
  const [profitActivityMounted, setProfitActivityMounted] = useState(false);
  // 核价页 keep-alive：图搜/货源匹配执行中切走不中断，返回时结果仍在（与每日选品面板一致）
  const [priceVerificationMounted, setPriceVerificationMounted] = useState(false);
  const collectionSequence = useRef(0);
  const processingSequence = useRef(0);
  const contentRef = useRef<HTMLDivElement>(null);
  const modulesById = useMemo(() => new Map(flatModules.map((module) => [module.id, module])), []);
  const activeTab = tabs.find((tab) => tab.key === activeTabKey) ?? tabs[0];
  const activeModuleId = activeTab?.moduleId ?? "dashboard";
  const activeModule = modulesById.get(activeModuleId)!;

  useEffect(() => {
    const content = contentRef.current;

    const updateVisibility = () => {
      const documentHeight = document.documentElement.scrollHeight;
      const windowScrollableDistance = Math.max(documentHeight - window.innerHeight, 0);
      const windowScrollProgress = windowScrollableDistance > 0
        ? window.scrollY / windowScrollableDistance
        : 0;

      const contentScrollableDistance = content
        ? Math.max(content.scrollHeight - content.clientHeight, 0)
        : 0;
      const contentScrollProgress = content && contentScrollableDistance > 0
        ? content.scrollTop / contentScrollableDistance
        : 0;

      setShowScrollTop(windowScrollProgress >= 0.25 || contentScrollProgress >= 0.25);
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

  useEffect(() => {
    if (activeModuleId === "profit_activity") setProfitActivityMounted(true);
  }, [activeModuleId]);

  useEffect(() => {
    if (activeModuleId === "price_verification") setPriceVerificationMounted(true);
  }, [activeModuleId]);

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

  const openProcessingTask = (draftIds: number[], options: ProductProcessingOptions) => {
    const openPanelCount = tabs.filter((tab) => tab.moduleId === "product_processing_tasks").length;
    if (openPanelCount >= MAX_PROCESSING_PANELS) {
      setWorkspaceNotice(`最多同时打开 ${MAX_PROCESSING_PANELS} 个处理任务，请先关闭一个再继续。`);
      return;
    }

    processingSequence.current += 1;
    const key = `product-processing-tasks-${processingSequence.current}`;
    setTabs((current) => [...current, {
      key,
      moduleId: "product_processing_tasks",
      label: `处理·${draftIds.length}项`,
      icon: "⚙",
      draftIds,
      processingOptions: options,
    }]);
    setActiveTabKey(key);
    setWorkspaceNotice("");
  };

  // 历史采集入口：从草稿池直接打开「历史任务」页（无处理参数，仅查看记录与输出）
  const openHistoryTasks = () => {
    const openPanelCount = tabs.filter((tab) => tab.moduleId === "product_processing_tasks").length;
    if (openPanelCount >= MAX_PROCESSING_PANELS) {
      setWorkspaceNotice(`最多同时打开 ${MAX_PROCESSING_PANELS} 个处理任务，请先关闭一个再继续。`);
      return;
    }
    processingSequence.current += 1;
    const key = `product-processing-tasks-${processingSequence.current}`;
    setTabs((current) => [...current, {
      key,
      moduleId: "product_processing_tasks",
      label: "历史任务",
      icon: "◷",
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
          {/* 每日选品主面板常驻挂载（隐藏而非卸载），保证采集进行中切走再回来时进度和结果不丢失 */}
          <div hidden={activeModuleId !== "daily_selection"}>
            <DailySelectionPage view="collection" topbarStatusVisible={activeModuleId === "daily_selection"} />
          </div>
          {profitActivityMounted && (
            <div hidden={activeModuleId !== "profit_activity"}>
              <ProfitActivityTestPage />
            </div>
          )}
          {activeModuleId === "profit_activity_products" && <ProfitActivityProductsPage />}
          {activeModuleId === "basic_settings" && <BasicSettingsPage />}
          {priceVerificationMounted && (
            <div hidden={activeModuleId !== "price_verification"}>
              <PriceVerificationPage />
            </div>
          )}
          {activeModuleId === "product_processing" && <ProductProcessingVerifyPage onStartProcessing={openProcessingTask} onOpenHistoryTasks={openHistoryTasks} />}
          {collectionTabs.map((tab) => (
            <div key={tab.key} hidden={activeTabKey !== tab.key}>
              <DailySelectionPage view="collection" initialDirectionId={tab.directionId} topbarStatusVisible={activeTabKey === tab.key} />
            </div>
          ))}
          {tabs.filter((tab) => tab.moduleId === "product_processing_tasks").map((tab) => (
            <div key={tab.key} hidden={activeTabKey !== tab.key}>
              <ProductProcessingTaskPage
                initialDraftIds={tab.draftIds}
                initialOptions={tab.processingOptions as ProductProcessingOptions | undefined}
              />
            </div>
          ))}
          {activeModuleId !== "dashboard" && activeModuleId !== "daily_selection" && activeModuleId !== "daily_selection_collection" && activeModuleId !== "product_processing" && activeModuleId !== "product_processing_tasks" && activeModuleId !== "profit_activity" && activeModuleId !== "profit_activity_products" && activeModuleId !== "basic_settings" && activeModuleId !== "price_verification" && <EmptyModulePage module={activeModule} />}
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

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
import { ProductProcessingPrecheckPage } from "../../modules/product_processing/pages/ProductProcessingPrecheckPage";
import { DimensionCanvasPage } from "../../modules/product_processing/pages/DimensionCanvasPage";
import {
  importPreviewItem,
  listDimensionNotifications,
  markDimensionNotificationRead,
} from "../../modules/product_processing/api/dimensionCanvasApi";
import { AiServicePage } from "../../modules/ai_service/pages/AiServicePage";
import type { ProductProcessingOptions } from "../../modules/product_processing/types";
import type { DimensionCanvasItem, DimensionNotification } from "../../modules/product_processing/types/dimensionCanvas";
import { DimensionNotificationRefreshFence } from "../../modules/product_processing/data/dimensionNotificationRefresh";
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
  const [dimensionNotifications, setDimensionNotifications] = useState<DimensionNotification[]>([]);
  const [showScrollTop, setShowScrollTop] = useState(false);
  // 利润活动页 keep-alive：首次打开后保持挂载，切换走仅隐藏，表单/文件/过滤进度不丢失
  const [profitActivityMounted, setProfitActivityMounted] = useState(false);
  // 核价页 keep-alive：图搜/货源匹配执行中切走不中断，返回时结果仍在（与每日选品面板一致）
  const [priceVerificationMounted, setPriceVerificationMounted] = useState(false);
  const collectionSequence = useRef(0);
  const processingSequence = useRef(0);
  const precheckSequence = useRef(0);
  const dimensionOpenRequests = useRef(new Map<string, Promise<DimensionCanvasItem>>());
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
    let stopped = false;
    let timer: number | null = null;
    let abortController: AbortController | null = null;
    const fence = new DimensionNotificationRefreshFence<DimensionNotification[]>();

    const refresh = () => {
      if (stopped) return;
      const generation = fence.begin();
      if (generation == null) return;
      const controller = new AbortController();
      abortController = controller;
      listDimensionNotifications("", controller.signal)
        .then((items) => {
          fence.succeed(generation, items, (fresh) => {
            if (!stopped) {
              setDimensionNotifications(fresh.filter((item) => !item.read));
            }
          });
        })
        .catch(() => {
          fence.fail(generation);
        })
        .finally(() => {
          if (abortController === controller) abortController = null;
        });
    };

    const resetTimer = () => {
      if (timer != null) window.clearInterval(timer);
      timer = null;
      const visible = document.visibilityState === "visible";
      fence.setVisible(visible);
      if (visible) {
        refresh();
        timer = window.setInterval(refresh, 15_000);
      } else {
        abortController?.abort();
        abortController = null;
      }
    };

    const handleFocus = () => refresh();
    const handleLocalChangeSet = () => refresh();
    window.addEventListener("focus", handleFocus);
    window.addEventListener("mainpg:dimension-change-set", handleLocalChangeSet);
    document.addEventListener("visibilitychange", resetTimer);
    resetTimer();

    return () => {
      stopped = true;
      fence.stop();
      abortController?.abort();
      if (timer != null) window.clearInterval(timer);
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("mainpg:dimension-change-set", handleLocalChangeSet);
      document.removeEventListener("visibilitychange", resetTimer);
    };
  }, []);

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
      return false;
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
    return true;
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

  // 任务完成后的预检入口：打开「预检与导出最终版」页（生成表格 → 预检修改 → 导出最终版 → 导入店小秘）
  const openProcessingPrecheck = (taskId: number, changeSetId?: string) => {
    if (changeSetId) {
      const existing = tabs.find((tab) => tab.taskId === taskId && tab.dimensionChangeSetId === changeSetId);
      if (existing) {
        setActiveTabKey(existing.key);
        return;
      }
    }
    precheckSequence.current += 1;
    const key = `product-processing-precheck-${precheckSequence.current}`;
    setTabs((current) => [...current, {
      key,
      moduleId: "product_processing_tasks",
      label: `预检·#${taskId}`,
      icon: "✓",
      taskId,
      dimensionChangeSetId: changeSetId,
    }]);
    setActiveTabKey(key);
    setWorkspaceNotice("");
  };

  const openDimensionItem = async (taskId: number, taskItemId: number) => {
    const requestKey = `${taskId}:${taskItemId}`;
    let request = dimensionOpenRequests.current.get(requestKey);
    if (!request) {
      request = importPreviewItem({ task_id: taskId, task_item_id: taskItemId });
      dimensionOpenRequests.current.set(requestKey, request);
    }
    try {
      const item = await request;
      const existing = tabs.find((tab) => tab.dimensionItemId === item.id);
      if (existing) {
        setActiveTabKey(existing.key);
        return;
      }
      const key = `dimension-canvas-${item.id}`;
      setTabs((current) => current.some((tab) => tab.dimensionItemId === item.id) ? current : [...current, {
        key,
        moduleId: "dimension_canvas",
        label: `尺寸·${item.skc || item.productDraftId}`,
        icon: "↔",
        dimensionBatchId: item.batchId,
        dimensionItemId: item.id,
        returnTaskId: taskId,
      }]);
      setActiveTabKey(key);
      setWorkspaceNotice("");
    } catch (cause) {
      setWorkspaceNotice(cause instanceof Error ? cause.message : String(cause));
    } finally {
      dimensionOpenRequests.current.delete(requestKey);
    }
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
        badges={{ dimension_canvas: dimensionNotifications.length }}
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
          {dimensionNotifications[0] && (
            <div className="workspace-notice" role="status">
              <span>↔</span>
              <strong>尺寸画布返回 {dimensionNotifications[0].completedCount} 项</strong>
              <button type="button" onClick={() => {
                const notice = dimensionNotifications[0];
                void markDimensionNotificationRead(notice.id).catch(() => undefined);
                setDimensionNotifications((current) => current.filter((item) => item.id !== notice.id));
                openProcessingPrecheck(notice.sourceTaskId, notice.changeSetId);
              }}>打开审核</button>
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
          {activeModuleId === "ai_service" && <AiServicePage />}
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
              {tab.taskId != null ? (
                <ProductProcessingPrecheckPage taskId={tab.taskId} initialChangeSetId={tab.dimensionChangeSetId} onOpenDimensionItem={openDimensionItem} />
              ) : (
                <ProductProcessingTaskPage
                  initialDraftIds={tab.draftIds}
                  initialOptions={tab.processingOptions as ProductProcessingOptions | undefined}
                  onOpenPrecheck={openProcessingPrecheck}
                />
              )}
            </div>
          ))}
          {tabs.filter((tab) => tab.moduleId === "dimension_canvas").map((tab) => (
            <div key={tab.key} hidden={activeTabKey !== tab.key}>
              <DimensionCanvasPage
                initialBatchId={tab.dimensionBatchId}
                initialItemId={tab.dimensionItemId}
                onOpenPrecheck={openProcessingPrecheck}
              />
            </div>
          ))}
          {activeModuleId !== "dashboard" && activeModuleId !== "daily_selection" && activeModuleId !== "daily_selection_collection" && activeModuleId !== "product_processing" && activeModuleId !== "product_processing_tasks" && activeModuleId !== "dimension_canvas" && activeModuleId !== "profit_activity" && activeModuleId !== "profit_activity_products" && activeModuleId !== "ai_service" && activeModuleId !== "basic_settings" && activeModuleId !== "price_verification" && <EmptyModulePage module={activeModule} />}
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

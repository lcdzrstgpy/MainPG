import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  isWorkspaceNavigationGroup,
  workspaceModules,
  workspacePageModules,
  type WorkspaceModule,
  type WorkspaceModuleId,
  type WorkspaceNavigationGroup,
  type WorkspaceNavigationGroupId,
  type WorkspaceNavigationItem,
} from "../navigation/modules";
import { Sidebar } from "./Sidebar";
import { TopNavigation, type WorkspaceTab } from "./TopNavigation";
import { WorkspaceHomePage } from "../../modules/dashboard/pages/WorkspaceHomePage";
import { DailySelectionPage } from "../../modules/daily_selection/pages/DailySelectionPage";
import { ProfitActivityProductsPage } from "../../modules/profit_activity/pages/ProfitActivityProductsPage";
import { ProfitActivityTestPage } from "../../modules/profit_activity/pages/ProfitActivityTestPage";
import { PriceVerificationPage } from "../../modules/price_verification/pages/PriceVerificationPage";
import { ProductProcessingVerifyPage } from "../../modules/product_processing/pages/ProductProcessingVerifyPage";
import { ProductProcessingTaskPage } from "../../modules/product_processing/pages/ProductProcessingTaskPage";
import { ProductProcessingHistoryPage } from "../../modules/product_processing/pages/ProductProcessingHistoryPage";
import { ProductProcessingPrecheckPage } from "../../modules/product_processing/pages/ProductProcessingPrecheckPage";
import { DimensionCanvasPage } from "../../modules/product_processing/pages/DimensionCanvasPage";
import {
  importPreviewItem,
  listDimensionNotifications,
  markDimensionNotificationRead,
} from "../../modules/product_processing/api/dimensionCanvasApi";
import { PersonalCenterPage } from "../../modules/personal_center/pages/PersonalCenterPage";
import { SystemAdminPage } from "../../modules/basic_settings/pages/SystemAdminPage";
import type { ProductProcessingOptions } from "../../modules/product_processing/types";
import type { DimensionCanvasItem, DimensionNotification } from "../../modules/product_processing/types/dimensionCanvas";
import { DimensionNotificationRefreshFence } from "../../modules/product_processing/data/dimensionNotificationRefresh";
import { EmptyModulePage } from "../../shared/components/EmptyModulePage";
import { BrandEntryAnimation } from "../../shared/components/BrandEntryAnimation";
import { WorkspaceTabScrollStore } from "./workspaceTabState";

type WorkspaceShellProps = {
  currentRole?: string;
  onSignOut: () => void;
  playEntryAnimation?: boolean;
  onEntryAnimationComplete?: () => void;
};

const MAX_COLLECTION_PANELS = 6;
const MAX_PROCESSING_PANELS = 3;
const NARROW_DESKTOP_QUERY = "(min-width: 801px) and (max-width: 1100px)";

function isAdminRole(role: string | undefined): boolean {
  const normalized = (role ?? "operator").toLowerCase();
  return normalized === "admin" || normalized === "owner";
}

/** 按角色过滤 adminOnly 模块；非 admin 只保留普通模块与包含可见子项的分组。 */
function filterModulesForRole(items: WorkspaceNavigationItem[], isAdmin: boolean): WorkspaceNavigationItem[] {
  if (isAdmin) return items;
  const visible: WorkspaceNavigationItem[] = [];
  for (const item of items) {
    if (isWorkspaceNavigationGroup(item)) {
      const children = item.children.filter((child) => !child.adminOnly);
      if (children.length) visible.push({ ...item, children });
    } else if (!item.adminOnly) {
      visible.push(item);
    }
  }
  return visible;
}

function navigationGroupForModule(id: WorkspaceModuleId, groups: WorkspaceNavigationGroup[]) {
  return groups.find((group) => group.children.some((child) => child.id === id));
}

function moduleTab(id: WorkspaceModuleId, flatModules: WorkspaceModule[]): WorkspaceTab {
  const module = flatModules.find((item) => item.id === id)!;
  return { key: id, moduleId: id, label: module.label, icon: module.icon, iconClass: module.iconClass };
}

export function WorkspaceShell({ currentRole = "operator", onSignOut, playEntryAnimation = false, onEntryAnimationComplete = () => undefined }: WorkspaceShellProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const [isNarrowDesktop, setIsNarrowDesktop] = useState(() => window.matchMedia(NARROW_DESKTOP_QUERY).matches);
  const [expandedGroupId, setExpandedGroupId] = useState<WorkspaceNavigationGroupId | null>(null);
  const [activeTabKey, setActiveTabKey] = useState("dashboard");
  const [tabs, setTabs] = useState<WorkspaceTab[]>([moduleTab("dashboard", workspacePageModules)]);
  const [workspaceNotice, setWorkspaceNotice] = useState("");
  const [dimensionNotifications, setDimensionNotifications] = useState<DimensionNotification[]>([]);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const collectionSequence = useRef(0);
  const processingSequence = useRef(0);
  const precheckSequence = useRef(0);
  const dimensionOpenRequests = useRef(new Map<string, Promise<DimensionCanvasItem>>());
  const contentRef = useRef<HTMLDivElement>(null);
  const scrollPositions = useRef(new WorkspaceTabScrollStore());
  const isAdmin = isAdminRole(currentRole);
  const visibleModules = useMemo(() => filterModulesForRole(workspaceModules, isAdmin), [isAdmin]);
  const flatModules = useMemo(() => filterModulesForRole(workspacePageModules, isAdmin) as WorkspaceModule[], [isAdmin]);
  const navigationGroups = useMemo(() => visibleModules.filter(isWorkspaceNavigationGroup), [visibleModules]);
  const modulesById = useMemo(() => new Map(flatModules.map((module) => [module.id, module])), [flatModules]);
  const activeTab = tabs.find((tab) => tab.key === activeTabKey) ?? tabs[0];
  const activeModuleId = activeTab?.moduleId ?? "dashboard";

  useEffect(() => {
    const mediaQuery = window.matchMedia(NARROW_DESKTOP_QUERY);
    const updateNarrowDesktop = () => setIsNarrowDesktop(mediaQuery.matches);
    updateNarrowDesktop();
    mediaQuery.addEventListener("change", updateNarrowDesktop);
    return () => mediaQuery.removeEventListener("change", updateNarrowDesktop);
  }, []);

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

  useLayoutEffect(() => {
    const position = scrollPositions.current.restore(activeTabKey) ?? { windowY: 0, contentY: 0 };
    const frame = window.requestAnimationFrame(() => {
      contentRef.current?.scrollTo({ top: position.contentY, behavior: "auto" });
      window.scrollTo({ top: position.windowY, behavior: "auto" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeTabKey]);

  const scrollBackToTop = () => {
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const saveActiveTabScroll = () => {
    scrollPositions.current.save(activeTabKey, {
      windowY: window.scrollY,
      contentY: contentRef.current?.scrollTop ?? 0,
    });
  };

  const activateTab = (key: string) => {
    if (key === activeTabKey) return;
    saveActiveTabScroll();
    setActiveTabKey(key);
  };

  const openModule = (id: WorkspaceModuleId) => {
    if (id === "daily_selection_collection") return;
    setExpandedGroupId(navigationGroupForModule(id, navigationGroups)?.id ?? null);
    setTabs((current) => current.some((tab) => tab.key === id) ? current : [...current, moduleTab(id, flatModules)]);
    activateTab(id);
    setWorkspaceNotice("");
  };

  const openNavigationGroup = (group: WorkspaceNavigationGroup) => {
    if (expandedGroupId === group.id) {
      setExpandedGroupId(null);
      return;
    }
    setExpandedGroupId(group.id);
    openModule(group.defaultChildId);
  };

  const selectTab = (key: string) => {
    const tab = tabs.find((item) => item.key === key);
    if (tab) setExpandedGroupId(navigationGroupForModule(tab.moduleId, navigationGroups)?.id ?? null);
    activateTab(key);
  };

  const closeTab = (key: string) => {
    if (key === activeTabKey) saveActiveTabScroll();
    scrollPositions.current.remove(key);
    setTabs((current) => {
      const next = current.filter((tab) => tab.key !== key);
      if (activeTabKey === key) {
        const nextActive = next[next.length - 1] ?? moduleTab("dashboard", flatModules);
        setExpandedGroupId(navigationGroupForModule(nextActive.moduleId, navigationGroups)?.id ?? null);
        setActiveTabKey(nextActive.key);
      }
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
    activateTab(key);
    setWorkspaceNotice("");
  };

  const openProcessingTask = (draftIds: number[], options: ProductProcessingOptions, premiumDraftIds: number[] = []) => {
    const openPanelCount = tabs.filter((tab) => tab.moduleId === "product_processing_tasks").length;
    if (openPanelCount >= MAX_PROCESSING_PANELS) {
      setWorkspaceNotice(`最多同时打开 ${MAX_PROCESSING_PANELS} 个处理任务，请先关闭一个再继续。`);
      return false;
    }

    processingSequence.current += 1;
    const key = `product-processing-tasks-${processingSequence.current}`;
    const premiumCount = premiumDraftIds.length;
    setTabs((current) => [...current, {
      key,
      moduleId: "product_processing_tasks",
      label: `处理·${draftIds.length}项${premiumCount ? `·精品${premiumCount}` : ''}`,
      icon: "⚙",
      draftIds,
      premiumDraftIds,
      processingOptions: options,
    }]);
    activateTab(key);
    setWorkspaceNotice("");
    return true;
  };

  const openProcessingTaskDetail = (taskId: number) => {
    const existing = tabs.find((tab) => tab.taskRunId === taskId);
    if (existing) {
      selectTab(existing.key);
      return;
    }
    processingSequence.current += 1;
    const key = `product-processing-task-${taskId}-${processingSequence.current}`;
    setTabs((current) => [...current, {
      key,
      moduleId: "product_processing_tasks",
      label: `处理·#${taskId}`,
      icon: "⚙",
      taskRunId: taskId,
    }]);
    activateTab(key);
    setWorkspaceNotice("");
  };

  // 任务完成后的预检入口：打开「预检与导出最终版」页（生成表格 → 预检修改 → 导出最终版 → 导入店小秘）
  const openProcessingPrecheck = (taskId: number, changeSetId?: string) => {
    if (changeSetId) {
      const existing = tabs.find((tab) => tab.taskId === taskId && tab.dimensionChangeSetId === changeSetId);
      if (existing) {
        activateTab(existing.key);
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
    activateTab(key);
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
        selectTab(existing.key);
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
      activateTab(key);
      setWorkspaceNotice("");
    } catch (cause) {
      setWorkspaceNotice(cause instanceof Error ? cause.message : String(cause));
    } finally {
      dimensionOpenRequests.current.delete(requestKey);
    }
  };

  const renderTab = (tab: WorkspaceTab) => {
    const isActive = activeTabKey === tab.key;
    switch (tab.moduleId) {
      case "dashboard":
        return <WorkspaceHomePage onOpenModule={openModule} />;
      case "daily_selection":
      case "daily_selection_collection":
        return <DailySelectionPage view="collection" initialDirectionId={tab.directionId} topbarStatusVisible={isActive} isActive={isActive} />;
      case "profit_activity":
        return <ProfitActivityTestPage isActive={isActive} />;
      case "profit_activity_products":
        return <ProfitActivityProductsPage isActive={isActive} />;
      case "price_verification":
        return <PriceVerificationPage isActive={isActive} />;
      case "product_processing":
        return <ProductProcessingVerifyPage onStartProcessing={openProcessingTask} isActive={isActive} />;
      case "product_processing_history":
        return <ProductProcessingHistoryPage onOpenTask={openProcessingTaskDetail} onOpenPrecheck={openProcessingPrecheck} />;
      case "product_processing_tasks":
        return tab.taskId != null ? (
          <ProductProcessingPrecheckPage taskId={tab.taskId} initialChangeSetId={tab.dimensionChangeSetId} onOpenDimensionItem={openDimensionItem} isActive={isActive} />
        ) : (
          <ProductProcessingTaskPage
            initialTaskId={tab.taskRunId}
            initialDraftIds={tab.draftIds}
            initialPremiumDraftIds={tab.premiumDraftIds}
            initialOptions={tab.processingOptions as ProductProcessingOptions | undefined}
            onOpenPrecheck={openProcessingPrecheck}
          />
        );
      case "dimension_canvas":
        return <DimensionCanvasPage initialBatchId={tab.dimensionBatchId} initialItemId={tab.dimensionItemId} onOpenPrecheck={openProcessingPrecheck} isActive={isActive} />;
      case "personal_center":
        return <PersonalCenterPage />;
      case "system_admin":
        return <SystemAdminPage />;
      default:
        return <EmptyModulePage module={modulesById.get(tab.moduleId)!} />;
    }
  };

  const sidebarIsCollapsed = sidebarCollapsed || isNarrowDesktop;
  const sidebarTemporarilyExpanded = sidebarIsCollapsed && sidebarHovered;

  return (
    <main className={`workspace-shell${playEntryAnimation ? " is-brand-entering" : ""}`}>
      <Sidebar
        collapsed={sidebarIsCollapsed && !sidebarTemporarilyExpanded}
        activeId={activeModuleId}
        expandedGroupId={expandedGroupId}
        modules={visibleModules}
        onOpenModule={openModule}
        onToggleGroup={openNavigationGroup}
        onHoverChange={setSidebarHovered}
        badges={{ dimension_canvas: dimensionNotifications.length }}
      />
      <section className="workspace-main">
        <TopNavigation sidebarPinned={!sidebarIsCollapsed} activeKey={activeTabKey} tabs={tabs} onToggleSidebar={() => setSidebarCollapsed((value) => !value)} onSelectTab={selectTab} onCloseTab={closeTab} onOpenPersonalCenter={() => openModule("personal_center")} onSignOut={onSignOut} />
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
          {tabs.map((tab) => (
            <div
              key={tab.key}
              className={`workspace-tab-panel${activeTabKey === tab.key ? " is-active" : ""}`}
              hidden={activeTabKey !== tab.key}
            >
              {renderTab(tab)}
            </div>
          ))}
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
      <BrandEntryAnimation active={playEntryAnimation} onComplete={onEntryAnimationComplete} />
    </main>
  );
}

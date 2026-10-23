import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  isWorkspaceNavigationGroup,
  workspaceModules,
  workspacePageModules,
  type PersonalCenterPanelId,
  type WorkspaceModule,
  type WorkspaceModuleId,
  type WorkspaceNavigationGroup,
  type WorkspaceNavigationGroupId,
  type WorkspaceNavigationItem,
} from "../navigation/modules";
import { Sidebar } from "./Sidebar";
import { TopNavigation, type WorkspaceTab } from "./TopNavigation";
import { PeachGarden } from "../../shared/components/PeachGarden";
import { AlpineMeadow } from "../../shared/components/AlpineMeadow";
import { InkTap } from "../../shared/components/InkTap";
import { DessertTap } from "../../shared/components/DessertTap";
import { useTheme } from "../../shared/hooks/useTheme";
import { useUiMode } from "../../shared/hooks/useUiMode";
import { useEffectPreferences } from "../../shared/hooks/useEffectPreferences";
import { useSidebarPreferences } from "../../shared/hooks/useSidebarState";
import { WorkspaceHomePage } from "../../modules/dashboard/pages/WorkspaceHomePage";
import { aiVideoNavHrefForPath, type AiVideoNavHref } from "../../modules/ai_video/nav";
import type { AiVideoNavRequest } from "../../modules/ai_video/pages/AiVideoPage";
import {
  importPreviewItem,
  listDimensionNotifications,
  markDimensionNotificationRead,
} from "../../modules/product_processing/api/dimensionCanvasApi";

// 页面组件按需懒加载（路由级代码分割，缩小首屏 bundle）。
// dashboard 是默认首屏 tab，保持同步加载，避免首屏出现加载闪烁。
const DailySelectionPage = lazy(() => import("../../modules/daily_selection/pages/DailySelectionPage").then((m) => ({ default: m.DailySelectionPage })));
const ProfitActivityProductsPage = lazy(() => import("../../modules/profit_activity/pages/ProfitActivityProductsPage").then((m) => ({ default: m.ProfitActivityProductsPage })));
const ProfitActivityTestPage = lazy(() => import("../../modules/profit_activity/pages/ProfitActivityTestPage").then((m) => ({ default: m.ProfitActivityTestPage })));
const PriceVerificationPage = lazy(() => import("../../modules/price_verification/pages/PriceVerificationPage").then((m) => ({ default: m.PriceVerificationPage })));
const SourcingLedgerPage = lazy(() => import("../../modules/sourcing_ledger/pages/SourcingLedgerPage").then((m) => ({ default: m.SourcingLedgerPage })));
const ProductProcessingVerifyPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingVerifyPage").then((m) => ({ default: m.ProductProcessingVerifyPage })));
const ProductProcessingTaskPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingTaskPage").then((m) => ({ default: m.ProductProcessingTaskPage })));
const ProductProcessingHistoryPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingHistoryPage").then((m) => ({ default: m.ProductProcessingHistoryPage })));
const ProductSourcingCostPage = lazy(() => import("../../modules/product_processing/pages/ProductSourcingCostPage").then((m) => ({ default: m.ProductSourcingCostPage })));
const ProductProcessingPrecheckPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingPrecheckPage").then((m) => ({ default: m.ProductProcessingPrecheckPage })));
const ComboKitPage = lazy(() => import("../../modules/combo_kit/pages/ComboKitPage").then((m) => ({ default: m.ComboKitPage })));
const ComboKitPromptPresetPage = lazy(() => import("../../modules/combo_kit/pages/ComboKitPromptPresetPage").then((m) => ({ default: m.ComboKitPromptPresetPage })));
const ComboKitHistoryPage = lazy(() => import("../../modules/combo_kit/pages/ComboKitHistoryPage").then((m) => ({ default: m.ComboKitHistoryPage })));
const DimensionCanvasPage = lazy(() => import("../../modules/product_processing/pages/DimensionCanvasPage").then((m) => ({ default: m.DimensionCanvasPage })));
const PodCustomizationPage = lazy(() => import("../../modules/pod_customization/pages/PodCustomizationPage").then((m) => ({ default: m.PodCustomizationPage })));
const PodSemiCustomizationPage = lazy(() => import("../../modules/pod_semi_customization/pages/PodSemiCustomizationPage").then((m) => ({ default: m.PodSemiCustomizationPage })));
const PodReplicaPage = lazy(() => import("../../modules/pod_replica/pages/PodReplicaPage").then((m) => ({ default: m.PodReplicaPage })));
const PodFissionPage = lazy(() => import("../../modules/pod_fission/pages/PodFissionPage").then((m) => ({ default: m.PodFissionPage })));
const AiVideoPage = lazy(() => import("../../modules/ai_video/pages/AiVideoPage").then((m) => ({ default: m.AiVideoPage })));
const PersonalCenterPage = lazy(() => import("../../modules/personal_center/pages/PersonalCenterPage").then((m) => ({ default: m.PersonalCenterPage })));
import type { ProductProcessingOptions } from "../../modules/product_processing/types";
import type { ProfitActivityPrefill } from "../../modules/profit_activity/types/products";
import type { DimensionCanvasItem, DimensionNotification } from "../../modules/product_processing/types/dimensionCanvas";
import { DimensionNotificationRefreshFence } from "../../modules/product_processing/data/dimensionNotificationRefresh";
import { EmptyModulePage } from "../../shared/components/EmptyModulePage";
import { BrandEntryAnimation } from "../../shared/components/BrandEntryAnimation";
import {
  GuideBoardPanel,
  firstPendingGuideSubTask,
  hasSeenGuidePanel,
  markGuidePanelSeen,
  markGuideSubTaskDone,
  startGuideTour,
  type GuideBoardId,
  type GuideSubTaskId,
} from "../../shared/components/GuideTour";
import { GuideEditor, type GuidePageOption } from "../../shared/components/guide/GuideEditor";
import {
  cloneGuideConfig,
  fetchGuideConfig,
  getActiveGuideConfig,
  saveGuideConfig,
  setActiveGuideConfig,
  type GuideConfig,
} from "../../shared/components/guide/guideConfig";
import {
  AnnouncementModal,
  REPLAY_ANNOUNCEMENT_EVENT,
  hasSeenAnnouncement,
  isAnnouncementPopupEligible,
  markAnnouncementSeen,
} from "../../shared/components/AnnouncementModal";
import { fetchMessages, markMessageRead, syncMessages, type InboxMessage } from "../../shared/api/messagesApi";
import { showToast } from "../../shared/components/toastStore";
import {
  PodUsageAgreementModal,
  clearPodAgreementPending,
  isPodAgreementPending,
} from "../../shared/components/PodUsageAgreementModal";
import { HelpAgentWidget } from "../../modules/help_agent/components/HelpAgentWidget";
import { WorkspaceTabScrollStore } from "./workspaceTabState";
import {
  readWorkspaceTabsSnapshot,
  trailingSequence,
  writeWorkspaceTabsSnapshot,
  type WorkspaceTabsSnapshot,
} from "./workspaceTabsPersistence";

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

/**
 * 采集面板与插件会话、进行中的采集批次绑定：恢复出来只会拿到失效会话，还可能误触发重新采集，
 * 因此不参与页签持久化（用户从侧栏或插件重新打开即可）。
 */
const NON_RESTORABLE_MODULE_IDS: ReadonlySet<string> = new Set(["daily_selection_collection"]);

/** 四个 POD 模块共用同一份使用协议：每次登录后首次进入任一个都会强制弹一次。 */
const POD_MODULE_IDS: ReadonlySet<WorkspaceModuleId> = new Set<WorkspaceModuleId>([
  "pod_customization",
  "pod_semi_customization",
  "pod_replica",
  "pod_fission",
]);


type RestoredWorkspaceTabs = {
  tabs: WorkspaceTab[];
  activeTabKey: string;
  collectionSequence: number;
  processingSequence: number;
  precheckSequence: number;
};

/**
 * 恢复上次的页签布局。恢复页签不只是体验问题：任务继续跑的前提是「还有页面在看它」，
 * 页签一丢（浏览器刷新、前端自动重载）就等于没人看了（刷新时页面自己也发了离开信标），
 * 连续静默后任务会被后端自动暂停。
 *
 * localStorage 里的数据一律当不可信输入，清洗失败就退回只有一个「工作台首页」页签的默认状态。
 */
function restoreWorkspaceTabs(isAdmin: boolean): RestoredWorkspaceTabs {
  const modules = filterModulesForRole(workspacePageModules, isAdmin) as WorkspaceModule[];
  const fallback: RestoredWorkspaceTabs = {
    tabs: [moduleTab("dashboard", modules)],
    activeTabKey: "dashboard",
    collectionSequence: 0,
    processingSequence: 0,
    precheckSequence: 0,
  };

  let snapshot: WorkspaceTabsSnapshot | null;
  try {
    snapshot = readWorkspaceTabsSnapshot(window.localStorage, new Set(modules.map((module) => module.id)));
  } catch {
    return fallback; // localStorage 被禁用（隐私模式等）：直接用默认布局。
  }
  if (!snapshot) return fallback;

  const tabs = snapshot.tabs
    .filter((tab) => !NON_RESTORABLE_MODULE_IDS.has(tab.moduleId))
    .map((tab) => {
      // 页签标题是动态文案（"处理·12项"）时沿用持久化值，缺失时才回落到模块默认名。
      const base = moduleTab(tab.moduleId, modules);
      const restored: WorkspaceTab = { ...tab, label: tab.label || base.label, icon: tab.icon || base.icon, iconClass: tab.iconClass ?? base.iconClass };
      // 开工中的处理页签按「任务详情」恢复：它因此继续轮询 /outputs（= 还有人在看），
      // 页面上也没有「开始处理」入口，不会再建出一个重复任务。
      if (restored.taskRunId == null && restored.activeTaskId != null) restored.taskRunId = restored.activeTaskId;
      return restored;
    });
  if (!tabs.length) return fallback;

  const keys = tabs.map((tab) => tab.key);
  return {
    tabs,
    activeTabKey: keys.includes(snapshot.activeTabKey) ? snapshot.activeTabKey : keys[0],
    // 续上 key 里的递增序号，避免恢复后新开页签与恢复的 key 撞车。
    collectionSequence: trailingSequence(keys, "daily-selection-collection-"),
    processingSequence: Math.max(
      trailingSequence(keys, "product-processing-tasks-"),
      trailingSequence(keys, "product-processing-task-"),
    ),
    precheckSequence: trailingSequence(keys, "product-processing-precheck-"),
  };
}

/** 懒加载模块的加载占位：轻量骨架，避免切换模块时出现空白闪烁。 */
function ModuleFallback() {
  return (
    <div className="workspace-module-fallback" role="status" aria-label="模块加载中">
      <span className="workspace-module-fallback-spinner" aria-hidden="true" />
      <span>正在加载模块…</span>
    </div>
  );
}

export function WorkspaceShell({ currentRole = "operator", onSignOut, playEntryAnimation = false, onEntryAnimationComplete = () => undefined }: WorkspaceShellProps) {
  const { theme } = useTheme();
  const { uiMode } = useUiMode();
  // 特效偏好（个人中心 → 偏好设置）：点击特效 / 全屏特效，localStorage 持久化。
  const { tap: tapEffects, ambient: ambientEffects } = useEffectPreferences();

  // 侧边栏偏好持久化在 localStorage：折叠状态（首次默认展开）+ 收起后是否触碰展开。
  const {
    collapsed: sidebarCollapsed,
    hoverExpand: sidebarHoverExpand,
    toggleCollapsed: toggleSidebarCollapsed,
  } = useSidebarPreferences();
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const [isNarrowDesktop, setIsNarrowDesktop] = useState(() => window.matchMedia(NARROW_DESKTOP_QUERY).matches);
  const [expandedGroupId, setExpandedGroupId] = useState<WorkspaceNavigationGroupId | null>(null);
  // 个人中心当前面板：由侧栏子导航驱动，状态放在这一层，切页签回来仍保持上次位置。
  const [personalPanel, setPersonalPanel] = useState<PersonalCenterPanelId>("wallet");
  const isAdmin = isAdminRole(currentRole);
  // 页签布局持久化：刷新 / 前端自动重载后恢复上次打开的页签与当前页签，任务页因此能继续轮询
  // （页签丢失 = 没人再看 = 连续静默后任务被自动暂停）。清洗逻辑见 restoreWorkspaceTabs。
  const [initialWorkspace] = useState(() => restoreWorkspaceTabs(isAdmin));
  // AI 视频当前页面路径：原生页面与 iframe 内换页都会上报；随页签一起持久化。
  const [aiVideoPath, setAiVideoPath] = useState<string>(() => {
    const restored = initialWorkspace.tabs.find((tab) => tab.moduleId === "ai_video")?.aiVideoPath;
    return restored || "/start";
  });
  // AI 视频当前入口：刷新时按恢复路径直接高亮，之后由页面回报更新。
  const [aiVideoHref, setAiVideoHref] = useState<AiVideoNavHref>(() => aiVideoNavHrefForPath(aiVideoPath) ?? "/start");
  // 侧栏点击下发的跳转请求；nonce 让「同一入口重复点击」也能重新下发。
  const [aiVideoRequest, setAiVideoRequest] = useState<AiVideoNavRequest | null>(null);
  const [activeTabKey, setActiveTabKey] = useState(initialWorkspace.activeTabKey);
  const [tabs, setTabs] = useState<WorkspaceTab[]>(initialWorkspace.tabs);
  const [workspaceNotice, setWorkspaceNotice] = useState("");
  const [dimensionNotifications, setDimensionNotifications] = useState<DimensionNotification[]>([]);
  const [showScrollTop, setShowScrollTop] = useState(false);
  const collectionSequence = useRef(initialWorkspace.collectionSequence);
  const processingSequence = useRef(initialWorkspace.processingSequence);
  const precheckSequence = useRef(initialWorkspace.precheckSequence);
  const dimensionOpenRequests = useRef(new Map<string, Promise<DimensionCanvasItem>>());
  const contentRef = useRef<HTMLDivElement>(null);
  const shellRef = useRef<HTMLElement>(null);
  const scrollPositions = useRef(new WorkspaceTabScrollStore());
  // 最新 tabs 的镜像：供异步回调（await 之后）与同步判断读取最新值，
  // 避免使用已过期的渲染闭包 tabs 导致激活/去重/上限判定失准。
  const tabsRef = useRef(tabs);
  useEffect(() => {
    tabsRef.current = tabs;
  }, [tabs]);
  // 页签变化即写回 localStorage：只在浏览器本地存一份布局，不上传、不影响后端。
  // 采集面板不存：它与插件会话绑定，恢复出来只会拿到失效会话（见 NON_RESTORABLE_MODULE_IDS）。
  useEffect(() => {
    try {
      writeWorkspaceTabsSnapshot(window.localStorage, {
        tabs: tabs
          .filter((tab) => !NON_RESTORABLE_MODULE_IDS.has(tab.moduleId))
          // AI 视频的当前页面属于会话状态，不是模块默认值：写盘时注入，刷新后回到同一路径。
          .map((tab) => (tab.moduleId === "ai_video" ? { ...tab, aiVideoPath } : tab)),
        activeTabKey,
      });
    } catch {
      // localStorage 被禁用：忽略，不影响本次使用。
    }
  }, [tabs, activeTabKey, aiVideoPath]);
  const visibleModules = useMemo(() => filterModulesForRole(workspaceModules, isAdmin), [isAdmin]);
  const flatModules = useMemo(() => filterModulesForRole(workspacePageModules, isAdmin) as WorkspaceModule[], [isAdmin]);
  const navigationGroups = useMemo(() => visibleModules.filter(isWorkspaceNavigationGroup), [visibleModules]);
  const modulesById = useMemo(() => new Map(flatModules.map((module) => [module.id, module])), [flatModules]);
  // 引导编辑器里「所在页面」的下拉项，取自实际模块，避免手写出不存在的页面 id。
  const guidePages = useMemo<GuidePageOption[]>(
    () => flatModules.map((module) => ({ id: module.id, label: module.label })),
    [flatModules],
  );
  const activeTab = tabs.find((tab) => tab.key === activeTabKey) ?? tabs[0];
  const activeModuleId = activeTab?.moduleId ?? "dashboard";

  // POD 使用协议：登录时置位「待阅读」，首次进入任一个 POD 模块时弹出；
  // 用户点「已阅读」后清除，本次登录内不再打扰。未同意就刷新/重新进入会再次弹出。
  // 弹窗只覆盖右侧工作区，左侧栏始终可点：切到非 POD 模块时先收起，回来再弹。
  const [podAgreementOpen, setPodAgreementOpen] = useState(false);
  useEffect(() => {
    if (!POD_MODULE_IDS.has(activeModuleId)) {
      setPodAgreementOpen(false);
      return;
    }
    if (!isPodAgreementPending()) return;
    setPodAgreementOpen(true);
  }, [activeModuleId]);


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

  // 桌面布局：顶栏是内容滚动区的视觉边界，内容遮罩与首屏留白都锚定在它身上。
  // 这里必须同步「布局高度」而不是视觉矩形：
  // - useLayoutEffect 在首帧绘制前写入，刷新后不会先用 120px 兜底、随后再跳一下；
  // - offsetHeight 不含 transform，主题切换的入场动画即使缩放顶栏也量不歪；
  // - 变量写在 workspace-shell 上，随组件卸载自然回收，不污染 documentElement。
  useLayoutEffect(() => {
    const shell = shellRef.current;
    const topbar = shell?.querySelector<HTMLElement>(".topbar-card");
    if (!shell || !topbar) return;
    const apply = () => {
      const height = Math.ceil(topbar.offsetHeight);
      if (height > 0) shell.style.setProperty("--workspace-topbar-height", `${height}px`);
    };
    apply();
    // ResizeObserver 回调在布局之后、绘制之前执行，尺寸变化不会先画出错位的边界。
    const observer = new ResizeObserver(apply);
    observer.observe(topbar);
    return () => {
      observer.disconnect();
      shell.style.removeProperty("--workspace-topbar-height");
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
    // 所有页签共用同一个滚动容器（.content-card），切页签时容器的 scrollTop 不会自己归零。
    // 新页签（该 key 从未存过位置）必须**在本帧内同步归零** —— 只靠下面的 rAF 会晚一帧，
    // 上一页签的 scrollTop 会残留到新内容上；主线程一忙（惰性模块加载/大列表渲染）
    // 这一帧就能被肉眼看到，表现为「打开新页签，内容停在滚轮中间」。
    // 有保存位置的页签不受影响，仍按下方保存值恢复。
    if (!scrollPositions.current.restore(activeTabKey)) {
      if (contentRef.current) contentRef.current.scrollTop = 0;
      window.scrollTo({ top: 0, behavior: "auto" });
    }
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

  /** 侧栏「个人中心」子导航：只切面板，不新开页签；个人中心未打开时顺带打开。 */
  const openPersonalPanel = (panel: PersonalCenterPanelId) => {
    setPersonalPanel(panel);
    openModule("personal_center");
  };

  /** 侧栏「AI 视频」子导航：写当前路径，必要时先打开页签；未原生化路径仍由 iframe 承接。 */
  const openAiVideoPanel = (href: AiVideoNavHref) => {
    setAiVideoPath(href);
    setAiVideoHref(href);
    setAiVideoRequest((current) => ({ href, nonce: (current?.nonce ?? 0) + 1 }));
    openModule("ai_video");
  };

  /** 侧栏面板分组的统一入口：按分组把点击分派到各自的页内面板。 */
  const openNavigationPanel = (group: WorkspaceNavigationGroup, panelId: string) => {
    if (group.id === "personal_center") {
      openPersonalPanel(panelId as PersonalCenterPanelId);
      return;
    }
    if (group.id === "ai_video") openAiVideoPanel(panelId as AiVideoNavHref);
  };

  // 操作答疑兜底按钮「去提交问题反馈」：先打开/切到「个人中心」页签，再把原问题交给它预填。
  //
  // 监听必须放在这一层，不能放在 PersonalCenterPage 里：个人中心是页签内容，没打开就不挂载，
  // 页面内的监听根本收不到事件（用户在别的模块点按钮会毫无反应）。
  //
  // 而且这里开了页签也不够——React 是「先切页签、后挂载」，等 PersonalCenterPage 挂载完，
  // 事件早已派发结束。所以问题原文由本层持有，再以 prop 传下去，挂载时即可直接消费。
  //
  // nonce 保证「同一个问题重复点」也能再次触发：只存字符串的话，重复 set 同一个值
  // 会被 React 判定为无变化而跳过，用户清了输入框再点就预填不上了。
  const [feedbackPrefill, setFeedbackPrefill] = useState<{ question: string; nonce: number } | null>(null);
  // openModule 每次渲染都是新函数，用 ref 取最新实现，避免监听被 [] 依赖锁死在首次闭包上
  // （否则 activateTab 会拿着过期的 activeTabKey 判断，可能不切页签）。
  const openModuleRef = useRef(openModule);
  openModuleRef.current = openModule;
  useEffect(() => {
    const onOpenFeedback = (event: Event) => {
      const detail = (event as CustomEvent<{ question?: string }>).detail;
      const question = typeof detail?.question === "string" ? detail.question.trim() : "";
      setFeedbackPrefill((current) => ({ question, nonce: (current?.nonce ?? 0) + 1 }));
      setPersonalPanel("feedback");
      openModuleRef.current("personal_center");
    };
    window.addEventListener("mainpg:open-feedback", onOpenFeedback);
    return () => window.removeEventListener("mainpg:open-feedback", onOpenFeedback);
  }, []);

  const guideTourRef = useRef<ReturnType<typeof startGuideTour> | null>(null);
  const guideAutoStartedRef = useRef(false);
  const [guideBoardPanelOpen, setGuideBoardPanelOpen] = useState(false);
  /** 引导教程是否正在走：教程期间公告弹窗让位，避免两层遮罩同时盖在屏幕上。 */
  const [guideTourActive, setGuideTourActive] = useState(false);
  /** 服务端引导配置是否已加载完（成功或失败都算，失败时用内置默认引导）。 */
  const [guideConfigReady, setGuideConfigReady] = useState(false);
  /** 编辑器打开时使用的配置快照；非空即代表编辑器开着。 */
  const [guideEditorSeed, setGuideEditorSeed] = useState<GuideConfig | null>(null);
  /** 登录后待弹出的公告队列（多条时在同一个弹窗里逐条看）。 */
  const [announcementQueue, setAnnouncementQueue] = useState<InboxMessage[]>([]);
  /** 公告只在进入工作台后自动检查一次，避免轮询式反复弹窗。 */
  const announcementCheckedRef = useRef(false);

  /**
   * 引导请求切页：只认工作台真实存在的模块。
   * 配置是服务端数据，页面 id 可能因为版本差异失效，这时忽略切页而不是让工作台崩掉。
   */
  const requestGuidePage = (page: string) => {
    const target = flatModules.find((module) => module.id === page);
    if (target) openModule(target.id);
  };

  /** 顶部引导入口：先弹板块面板，由面板决定走哪个板块的引导。 */
  const openGuideBoardPanel = () => {
    if (guideTourRef.current?.isActive()) return;
    setGuideBoardPanelOpen(true);
  };

  /** 走某个二级子任务的引导：跨页时由 onRequestPage 切页，走完记一次该子任务的完成标记。 */
  const startGuideSubTask = (boardId: GuideBoardId, subTaskId: GuideSubTaskId) => {
    if (guideTourRef.current?.isActive()) return;
    setGuideBoardPanelOpen(false);
    const tour = startGuideTour(boardId, subTaskId, {
      onRequestPage: requestGuidePage,
      onFinish: (completed) => {
        guideTourRef.current = null;
        setGuideTourActive(false);
        if (!completed) return;
        markGuideSubTaskDone(boardId, subTaskId);
        // 回到面板，让用户看到更新后的进度并接着看下一个子任务。
        setGuideBoardPanelOpen(true);
      },
    });
    if (!tour) return;
    guideTourRef.current = tour;
    setGuideTourActive(true);
  };

  // 引导内容存在本地服务端：启动时拉一次写入运行时配置，换浏览器/重装都还在；
  // 拉取失败（离线等）就退回内置默认引导，不打扰用户。
  useEffect(() => {
    let cancelled = false;
    fetchGuideConfig()
      .then((snapshot) => {
        if (!cancelled) setActiveGuideConfig(snapshot.config);
      })
      .catch(() => undefined)
      // 首次进入要看服务端配置决定播哪一段，拉取（成功或失败）后才算就绪。
      .finally(() => {
        if (!cancelled) setGuideConfigReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const openGuideEditor = () => {
    setGuideBoardPanelOpen(false);
    setGuideEditorSeed(cloneGuideConfig(getActiveGuideConfig()));
  };

  const saveGuideEditor = async (config: GuideConfig) => {
    try {
      const snapshot = await saveGuideConfig(config);
      setActiveGuideConfig(snapshot.config ?? config);
      showToast("引导配置已保存，其他人下次打开引导即可看到", "success");
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : "保存引导配置失败", "error");
      throw cause;
    }
  };

  /**
   * 预览：临时把草稿当生效配置播一遍，播完还原，不写库也不记完成标记。
   * startIndex 指定从第几步开播，编辑器「演示这一步」用它停到正在编辑的那一步，
   * 之后可以照常点「下一步」把后面的步骤演示完。
   */
  const previewGuideDraft = async (
    config: GuideConfig,
    boardId: GuideBoardId,
    subTaskId: string,
    startIndex = 0,
  ) => {
    const previous = getActiveGuideConfig();
    setGuideBoardPanelOpen(false);
    setActiveGuideConfig(cloneGuideConfig(config));
    try {
      await new Promise<void>((resolve) => {
        const tour = startGuideTour(boardId, subTaskId, {
          onRequestPage: requestGuidePage,
          onFinish: () => resolve(),
          startIndex,
        });
        if (!tour) {
          resolve();
          return;
        }
        guideTourRef.current = tour;
      });
    } finally {
      guideTourRef.current = null;
      setActiveGuideConfig(previous);
    }
  };

  // 首次进入工作台直接播放第一段还没看过的引导；提示卡右上角的关闭按钮就是「跳过」，
  // 跳过不计完成，之后仍可从顶部栏的入口重新播放。自动播过之后只保留手动入口。
  // 一段可播的教程都没有（都看过了 / 都还没写）时退回原来的板块面板。
  useEffect(() => {
    if (playEntryAnimation || !guideConfigReady || guideAutoStartedRef.current || hasSeenGuidePanel()) return;
    const timer = window.setTimeout(() => {
      guideAutoStartedRef.current = true;
      markGuidePanelSeen();
      const next = firstPendingGuideSubTask();
      if (next) startGuideSubTask(next.boardId, next.subTaskId);
      else setGuideBoardPanelOpen(true);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [playEntryAnimation, guideConfigReady]);

  /** 拉取公告（带图片），滤掉本机已弹过的，组成待展示队列。 */
  const loadAnnouncementQueue = async () => {
    try {
      // 先等一次同步完成：本地消息表没有账号维度，同机换号后上个账号的定向公告
      // 会残留到下一轮后台同步（180s）才被撤回。放在读取之前，渲染时就不会泄漏。
      await syncMessages();
      const items = await fetchMessages({ withImages: true });
      // 「只弹一次」由本机 localStorage 标记负责；服务端 read 只用于铃铛红点。
      // 不能拿 read 当过滤条件：用户可能在铃铛里点开过公告（那时就被标了已读），
      // 结果登录弹窗反而永远不出现。
      const pending = items.filter(
        (item) =>
          item.kind === "announcement" &&
          !hasSeenAnnouncement(item.serverId, item.id) &&
          isAnnouncementPopupEligible(item.publishedAt),
      );
      if (pending.length) setAnnouncementQueue(pending);
    } catch {
      // 离线等场景静默：公告不是关键路径，留到下次登录。
    }
  };

  // 登录后的公告大弹窗：等入场动画播完、且新手引导不占屏时再弹（首次会被引导先拦住，
  // 引导关掉后本效果因依赖变化会重新跑一次）。比引导的 800ms 稍晚，避免和它抢秒。
  useEffect(() => {
    if (announcementCheckedRef.current) return;
    if (playEntryAnimation || !guideConfigReady) return;
    if (guideBoardPanelOpen || guideEditorSeed || guideTourActive) return;
    const timer = window.setTimeout(() => {
      announcementCheckedRef.current = true;
      void loadAnnouncementQueue();
    }, 900);
    return () => window.clearTimeout(timer);
  }, [playEntryAnimation, guideConfigReady, guideBoardPanelOpen, guideEditorSeed, guideTourActive]);

  /** 单条公告算看过：本机写标记（服务端没有「已弹出」概念），同时上报已读并刷新铃铛红点。 */
  const handleAnnouncementSeen = (item: InboxMessage) => {
    // 标记键用 serverId（跨同步稳定，否则撤回重插会换 id 导致重复弹）；
    // 已读上报仍用本地 id —— 后端 mark_read 认的是本地 messages.id。
    markAnnouncementSeen(item.serverId, item.id);
    void markMessageRead(item.id).catch(() => undefined);
    window.dispatchEvent(new Event("mainpg:messages-change"));
  };

  /** 消息中心里点公告 → 重开大弹窗回看（重新拉带图片的版本，排版与登录弹窗完全一致）。 */
  useEffect(() => {
    const handleReplay = (event: Event) => {
      const messageId = (event as CustomEvent<{ messageId?: number }>).detail?.messageId;
      if (!messageId) return;
      void (async () => {
        try {
          const items = await fetchMessages({ withImages: true });
          const target = items.find((item) => item.id === messageId);
          if (target) setAnnouncementQueue([target]);
        } catch {
          // 离线等场景静默
        }
      })();
    };
    window.addEventListener(REPLAY_ANNOUNCEMENT_EVENT, handleReplay);
    return () => window.removeEventListener(REPLAY_ANNOUNCEMENT_EVENT, handleReplay);
  }, []);

  const openComboGenerate = (setId: string) => {
    setExpandedGroupId("combo_workflow");
    setTabs((current) => {
      const existing = current.find((tab) => tab.moduleId === "combo_generate");
      if (existing) {
        return current.map((tab) => (tab.moduleId === "combo_generate" ? { ...tab, initialSetId: setId } : tab));
      }
      return [...current, { key: "combo_generate", moduleId: "combo_generate", label: "组合生图", icon: "", initialSetId: setId }];
    });
    activateTab("combo_generate");
    setWorkspaceNotice("");
  };

  // 从「商品货源及成本展示」的 SKU 明细跳转到「利润活动」单品利润表单，并预填成本与货源链接。
  const openProfitActivity = (prefill: ProfitActivityPrefill) => {
    setExpandedGroupId("sourcing_workflow");
    setTabs((current) => {
      const existing = current.find((tab) => tab.moduleId === "profit_activity");
      if (existing) {
        return current.map((tab) => (tab.moduleId === "profit_activity" ? { ...tab, profitPrefill: prefill } : tab));
      }
      return [...current, { key: "profit_activity", moduleId: "profit_activity", label: "利润活动", icon: "", profitPrefill: prefill }];
    });
    activateTab("profit_activity");
    setWorkspaceNotice("");
  };

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.get("module") !== "personal_center" || query.get("payment") !== "success") return;

    openModule("personal_center");
    setWorkspaceNotice("支付宝支付完成，正在读取服务器积分余额。");
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  const openNavigationGroup = (group: WorkspaceNavigationGroup) => {
    if (expandedGroupId === group.id) {
      setExpandedGroupId(null);
      return;
    }
    setExpandedGroupId(group.id);
    openModule(group.defaultChildId);
  };

  const selectTab = (key: string) => {
    const tab = tabsRef.current.find((item) => item.key === key);
    // 目标标签已不存在（异步流程里被关闭）时直接返回，避免把 activeTabKey
    // 设成无效值导致所有面板 hidden、内容区整片空白。
    if (!tab) return;
    setExpandedGroupId(navigationGroupForModule(tab.moduleId, navigationGroups)?.id ?? null);
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
    const panels = tabsRef.current.filter((tab) => tab.moduleId === "daily_selection_collection");
    // 同一方向已打开则直接激活，避免同一采集数据被多个面板并发读写互相覆盖。
    const existing = panels.find((tab) => tab.directionId === directionId);
    if (existing) {
      selectTab(existing.key);
      return;
    }
    if (panels.length >= MAX_COLLECTION_PANELS) {
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

  const openProcessingTask = (draftIds: number[], options: ProductProcessingOptions, premiumDraftIds: number[] = [], skuCount = 0) => {
    const openPanelCount = tabsRef.current.filter((tab) => tab.moduleId === "product_processing_tasks").length;
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
      draftSkuCount: skuCount,
      processingOptions: options,
    }]);
    activateTab(key);
    setWorkspaceNotice("");
    return true;
  };

  // 处理页签开工/接手任务后，把任务号记到页签元数据上（只影响刷新后的恢复，不参与实时渲染）。
  // 记了它，刷新恢复出来的就是这个任务本身（继续轮询心跳），而不是一张空的「处理设置」页。
  const markTabActiveTask = (tabKey: string, taskId: number) => {
    setTabs((current) => current.map((tab) => (tab.key === tabKey ? { ...tab, activeTaskId: taskId } : tab)));
  };

  const openProcessingTaskDetail = (taskId: number) => {
    const existing = tabsRef.current.find((tab) => tab.taskRunId === taskId);
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
    // 同一任务只保留一个预检页签：重复点「打开预检」会叠出多个页签，每个都在轮询
    // 整份预检，是 WebView2 内存/CPU 被放大的常见来源。
    const existing = tabsRef.current.find(
      (tab) =>
        tab.taskId === taskId &&
        (changeSetId === undefined || tab.dimensionChangeSetId === changeSetId),
    );
    if (existing) {
      activateTab(existing.key);
      return;
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
      const existing = tabsRef.current.find((tab) => tab.dimensionItemId === item.id);
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
    let content: ReactNode;
    switch (tab.moduleId) {
      case "dashboard":
        content = <WorkspaceHomePage onOpenModule={openModule} />;
        break;
      case "daily_selection":
      case "daily_selection_collection":
        content = <DailySelectionPage view="collection" initialDirectionId={tab.directionId} onOpenProductProcessingDraft={() => openModule("product_processing")} topbarStatusVisible={isActive} isActive={isActive} />;
        break;
      case "profit_activity":
        content = <ProfitActivityTestPage isActive={isActive} prefill={tab.profitPrefill} />;
        break;
      case "profit_activity_products":
        content = <ProfitActivityProductsPage isActive={isActive} />;
        break;
      case "price_verification":
        content = <PriceVerificationPage isActive={isActive} />;
        break;
      case "sourcing_ledger":
        content = <SourcingLedgerPage isActive={isActive} />;
        break;
      case "product_processing":
        content = <ProductProcessingVerifyPage onStartProcessing={openProcessingTask} onOpenCollection={() => openModule("daily_selection")} isActive={isActive} />;
        break;
      case "product_processing_history":
        content = <ProductProcessingHistoryPage onOpenTask={openProcessingTaskDetail} onOpenPrecheck={openProcessingPrecheck} />;
        break;
      case "product_processing_sourcing":
        content = <ProductSourcingCostPage isActive={isActive} onOpenProfitActivity={openProfitActivity} />;
        break;
      case "product_processing_tasks":
        content = tab.taskId != null ? (
          <ProductProcessingPrecheckPage taskId={tab.taskId} initialChangeSetId={tab.dimensionChangeSetId} onOpenDimensionItem={openDimensionItem} isActive={isActive} />
        ) : (
          <ProductProcessingTaskPage
            isActive={isActive}
            initialTaskId={tab.taskRunId}
            initialDraftIds={tab.draftIds}
            initialPremiumDraftIds={tab.premiumDraftIds}
            initialSkuCount={tab.draftSkuCount}
            initialOptions={tab.processingOptions as ProductProcessingOptions | undefined}
            onOpenPrecheck={openProcessingPrecheck}
            onTaskStarted={(taskId) => markTabActiveTask(tab.key, taskId)}
            onBackToDraftPool={() => {
              // 返回草稿池删减 SKU：关掉本处理页签，再切回草稿池。
              closeTab(tab.key);
              openModule("product_processing");
            }}
          />
        );
        break;
      case "combo_generate":
        content = <ComboKitPage isActive={isActive} initialSetId={tab.initialSetId} />;
        break;
      case "combo_prompt_preset":
        content = <ComboKitPromptPresetPage isActive={isActive} />;
        break;
      case "combo_history":
        content = <ComboKitHistoryPage isActive={isActive} onOpenSet={openComboGenerate} />;
        break;
      case "dimension_canvas":
        content = <DimensionCanvasPage initialBatchId={tab.dimensionBatchId} initialItemId={tab.dimensionItemId} onOpenPrecheck={openProcessingPrecheck} isActive={isActive} />;
        break;
      case "pod_customization":
        content = <PodCustomizationPage isActive={isActive} />;
        break;
      case "pod_semi_customization":
        content = <PodSemiCustomizationPage isActive={isActive} />;
        break;
      case "pod_replica":
        content = <PodReplicaPage isActive={isActive} />;
        break;
      case "pod_fission":
        content = <PodFissionPage isActive={isActive} />;
        break;
      case "ai_video":
        content = (
          <AiVideoPage
            path={aiVideoPath}
            request={aiVideoRequest}
            onPathChange={setAiVideoPath}
            onActiveHrefChange={setAiVideoHref}
          />
        );
        break;
      case "personal_center":
        content = <PersonalCenterPage feedbackPrefill={feedbackPrefill} panel={personalPanel} onPanelChange={setPersonalPanel} />;
        break;
      default:
        content = <EmptyModulePage module={modulesById.get(tab.moduleId)!} />;
    }
    return <Suspense fallback={<ModuleFallback />}>{content}</Suspense>;
  };

  const sidebarIsCollapsed = sidebarCollapsed || isNarrowDesktop;
  // 收起后鼠标触碰是否临时浮出，由偏好设置控制（关掉后只能点顶栏按钮展开）。
  // 有分组处于展开时，不响应「触碰临时展开」：收起态下子类是挂在侧栏外 12px 的悬浮层，
  // 一旦 hoverExpand 把 is-collapsed 撤掉，悬浮层样式随之失效、菜单缩回侧栏内部，
  // 鼠标落空触发 mouseleave → 又收起 → 悬浮层重出 → 无限循环闪烁。
  // 只在悬浮层打开时抑制；平时（expandedGroupId 为空）触碰展开照常。
  const sidebarTemporarilyExpanded =
    sidebarIsCollapsed && sidebarHovered && sidebarHoverExpand && expandedGroupId === null;

  return (
    <main className={`workspace-shell${playEntryAnimation ? " is-brand-entering" : ""}`} ref={shellRef}>
      <PeachGarden theme={theme} uiMode={uiMode} tapEffects={tapEffects} ambientEffects={ambientEffects} />
      <AlpineMeadow theme={theme} uiMode={uiMode} tapEffects={tapEffects} ambientEffects={ambientEffects} />
      <InkTap theme={theme} uiMode={uiMode} enabled={tapEffects} />
      <DessertTap theme={theme} uiMode={uiMode} enabled={tapEffects} />
      <Sidebar
        collapsed={sidebarIsCollapsed && !sidebarTemporarilyExpanded}
        activeId={activeModuleId}
        expandedGroupId={expandedGroupId}
        modules={visibleModules}
        onOpenModule={openModule}
        onToggleGroup={openNavigationGroup}
        onHoverChange={setSidebarHovered}
        activePanelId={
          activeModuleId === "personal_center"
            ? personalPanel
            : activeModuleId === "ai_video"
              ? aiVideoHref
              : null
        }
        onOpenPanel={openNavigationPanel}
        badges={{ dimension_canvas: dimensionNotifications.length }}
      />
      <section className="workspace-main">
        <TopNavigation sidebarPinned={!sidebarIsCollapsed} activeKey={activeTabKey} tabs={tabs} onToggleSidebar={toggleSidebarCollapsed} onSelectTab={selectTab} onCloseTab={closeTab} onOpenPersonalCenter={() => openModule("personal_center")} onOpenGuide={openGuideBoardPanel} onSignOut={onSignOut} />
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
      <HelpAgentWidget showBalance />
      {guideBoardPanelOpen && (
        <GuideBoardPanel
          onClose={() => setGuideBoardPanelOpen(false)}
          onStartSubTask={startGuideSubTask}
        />
      )}
      {guideEditorSeed && (
        <GuideEditor
          config={guideEditorSeed}
          pages={guidePages}
          activePageId={activeModuleId}
          onSave={saveGuideEditor}
          onClose={() => setGuideEditorSeed(null)}
          onPreview={previewGuideDraft}
        />
      )}
      {announcementQueue.length > 0 && (
        <AnnouncementModal
          announcements={announcementQueue}
          onSeen={handleAnnouncementSeen}
          onClose={() => setAnnouncementQueue([])}
        />
      )}
      <PodUsageAgreementModal
        open={podAgreementOpen}
        onAgree={() => {
          clearPodAgreementPending();
          setPodAgreementOpen(false);
        }}
      />
      <BrandEntryAnimation active={playEntryAnimation} onComplete={onEntryAnimationComplete} />
    </main>
  );
}

import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

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
import { PeachGarden } from "../../shared/components/PeachGarden";
import { InkTap } from "../../shared/components/InkTap";
import { useTheme } from "../../shared/hooks/useTheme";
import { useUiMode } from "../../shared/hooks/useUiMode";
import { WorkspaceHomePage } from "../../modules/dashboard/pages/WorkspaceHomePage";
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
const ProductProcessingVerifyPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingVerifyPage").then((m) => ({ default: m.ProductProcessingVerifyPage })));
const ProductProcessingTaskPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingTaskPage").then((m) => ({ default: m.ProductProcessingTaskPage })));
const ProductProcessingHistoryPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingHistoryPage").then((m) => ({ default: m.ProductProcessingHistoryPage })));
const ProductProcessingPrecheckPage = lazy(() => import("../../modules/product_processing/pages/ProductProcessingPrecheckPage").then((m) => ({ default: m.ProductProcessingPrecheckPage })));
const ComboKitPage = lazy(() => import("../../modules/combo_kit/pages/ComboKitPage").then((m) => ({ default: m.ComboKitPage })));
const ComboKitPromptPresetPage = lazy(() => import("../../modules/combo_kit/pages/ComboKitPromptPresetPage").then((m) => ({ default: m.ComboKitPromptPresetPage })));
const ComboKitHistoryPage = lazy(() => import("../../modules/combo_kit/pages/ComboKitHistoryPage").then((m) => ({ default: m.ComboKitHistoryPage })));
const DimensionCanvasPage = lazy(() => import("../../modules/product_processing/pages/DimensionCanvasPage").then((m) => ({ default: m.DimensionCanvasPage })));
const PodCustomizationPage = lazy(() => import("../../modules/pod_customization/pages/PodCustomizationPage").then((m) => ({ default: m.PodCustomizationPage })));
const PodSemiCustomizationPage = lazy(() => import("../../modules/pod_semi_customization/pages/PodSemiCustomizationPage").then((m) => ({ default: m.PodSemiCustomizationPage })));
const PersonalCenterPage = lazy(() => import("../../modules/personal_center/pages/PersonalCenterPage").then((m) => ({ default: m.PersonalCenterPage })));
import type { ProductProcessingOptions } from "../../modules/product_processing/types";
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
import { fetchMessages, markMessageRead, type InboxMessage } from "../../shared/api/messagesApi";
import { showToast } from "../../shared/components/toastStore";
import { HelpAgentWidget } from "../../modules/help_agent/components/HelpAgentWidget";
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
  // 最新 tabs 的镜像：供异步回调（await 之后）与同步判断读取最新值，
  // 避免使用已过期的渲染闭包 tabs 导致激活/去重/上限判定失准。
  const tabsRef = useRef(tabs);
  useEffect(() => {
    tabsRef.current = tabs;
  }, [tabs]);
  const isAdmin = isAdminRole(currentRole);
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
      const items = await fetchMessages({ withImages: true });
      // 「只弹一次」由本机 localStorage 标记负责；服务端 read 只用于铃铛红点。
      // 不能拿 read 当过滤条件：用户可能在铃铛里点开过公告（那时就被标了已读），
      // 结果登录弹窗反而永远不出现。
      const pending = items.filter(
        (item) =>
          item.kind === "announcement" &&
          !hasSeenAnnouncement(item.id) &&
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
  const handleAnnouncementSeen = (messageId: number) => {
    markAnnouncementSeen(messageId);
    void markMessageRead(messageId).catch(() => undefined);
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

  const openProcessingTask = (draftIds: number[], options: ProductProcessingOptions, premiumDraftIds: number[] = []) => {
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
      processingOptions: options,
    }]);
    activateTab(key);
    setWorkspaceNotice("");
    return true;
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
    if (changeSetId) {
      const existing = tabsRef.current.find((tab) => tab.taskId === taskId && tab.dimensionChangeSetId === changeSetId);
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
        content = <ProfitActivityTestPage isActive={isActive} />;
        break;
      case "profit_activity_products":
        content = <ProfitActivityProductsPage isActive={isActive} />;
        break;
      case "price_verification":
        content = <PriceVerificationPage isActive={isActive} />;
        break;
      case "product_processing":
        content = <ProductProcessingVerifyPage onStartProcessing={openProcessingTask} onOpenPrecheck={openProcessingPrecheck} onOpenCollection={() => openModule("daily_selection")} isActive={isActive} />;
        break;
      case "product_processing_history":
        content = <ProductProcessingHistoryPage onOpenTask={openProcessingTaskDetail} onOpenPrecheck={openProcessingPrecheck} />;
        break;
      case "product_processing_tasks":
        content = tab.taskId != null ? (
          <ProductProcessingPrecheckPage taskId={tab.taskId} initialChangeSetId={tab.dimensionChangeSetId} onOpenDimensionItem={openDimensionItem} onOpenDraftPool={() => openModule("product_processing")} isActive={isActive} />
        ) : (
          <ProductProcessingTaskPage
            initialTaskId={tab.taskRunId}
            initialDraftIds={tab.draftIds}
            initialPremiumDraftIds={tab.premiumDraftIds}
            initialOptions={tab.processingOptions as ProductProcessingOptions | undefined}
            onOpenPrecheck={openProcessingPrecheck}
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
      case "personal_center":
        content = <PersonalCenterPage feedbackPrefill={feedbackPrefill} />;
        break;
      default:
        content = <EmptyModulePage module={modulesById.get(tab.moduleId)!} />;
    }
    return <Suspense fallback={<ModuleFallback />}>{content}</Suspense>;
  };

  const sidebarIsCollapsed = sidebarCollapsed || isNarrowDesktop;
  const sidebarTemporarilyExpanded = sidebarIsCollapsed && sidebarHovered;

  return (
    <main className={`workspace-shell${playEntryAnimation ? " is-brand-entering" : ""}`}>
      <PeachGarden theme={theme} uiMode={uiMode} />
      <InkTap theme={theme} uiMode={uiMode} />
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
        <TopNavigation sidebarPinned={!sidebarIsCollapsed} activeKey={activeTabKey} tabs={tabs} onToggleSidebar={() => setSidebarCollapsed((value) => !value)} onSelectTab={selectTab} onCloseTab={closeTab} onOpenPersonalCenter={() => openModule("personal_center")} onOpenGuide={openGuideBoardPanel} onSignOut={onSignOut} />
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
      <BrandEntryAnimation active={playEntryAnimation} onComplete={onEntryAnimationComplete} />
    </main>
  );
}

import { useEffect, useRef, useState } from "react";
import { BRAND_LOGO_URL, BRAND_MARK_URL, BRAND_NAME } from "../../shared/brand";
import { AdSlot } from "../../shared/components/AdSlot";
import type { AdTarget } from "../../shared/ads/adConfig";
import { AppleAppGlyph } from "../../shared/components/AppleAppGlyph";
import { slidingIndicatorStyle, useSlidingIndicator } from "../../shared/hooks/useSlidingIndicator";
import {
  isWorkspaceNavigationGroup,
  type WorkspaceModule,
  type WorkspaceModuleId,
  type WorkspaceNavigationGroup,
  type WorkspaceNavigationGroupId,
  type WorkspaceNavigationItem,
} from "../navigation/modules";

type SidebarProps = {
  collapsed: boolean;
  activeId: WorkspaceModuleId;
  expandedGroupId: WorkspaceNavigationGroupId | null;
  /** 一级分组下正在展开的二级分组（图像服务 → POD定制）。 */
  expandedSubgroupId: WorkspaceNavigationGroupId | null;
  /** 子菜单是否以浮层呈现（收起态或桌面 Dock 模式）：选中后应收起，避免挡住内容。 */
  flyout: boolean;
  modules: WorkspaceNavigationItem[];
  onOpenModule: (id: WorkspaceModuleId) => void;
  onToggleGroup: (group: WorkspaceNavigationGroup) => void;
  onToggleSubgroup: (group: WorkspaceNavigationGroup) => void;
  onHoverChange: (hovered: boolean) => void;
  /** 当前页的页内面板：只在对应页面激活时传入，用于点亮侧栏子项。 */
  activePanelId?: string | null;
  onOpenPanel?: (group: WorkspaceNavigationGroup, panelId: string) => void;
  badges?: Partial<Record<WorkspaceModuleId, number>>;
  /** 收起态下关闭浮层：选中页面、点到侧栏外面时都收回，避免浮层一直挡着内容。 */
  onCloseGroups: () => void;
  /** 点击侧栏推广位后的跳转（模块 / 个人中心面板 / 外部链接）。 */
  onOpenAdTarget: (target: AdTarget) => void;
};

const MENU_ACTIVE_SELECTOR = ":scope > .is-active, :scope > .sidebar-group > .sidebar-item.is-active";
const SUBMENU_ACTIVE_SELECTOR = ":scope > .is-active, :scope > .sidebar-subgroup > .sidebar-subgroup-items > .is-active";

/** 分组内是否有当前页 / 面板：三层结构下需要递归判断，用于点亮一级入口。 */
function groupHasActiveItem(group: WorkspaceNavigationGroup, activeId: WorkspaceModuleId, activePanelId: string | null): boolean {
  if (group.panels?.length) return activePanelId !== null && group.panels.some((panel) => panel.id === activePanelId);
  return group.children.some((child) => (
    isWorkspaceNavigationGroup(child)
      ? groupHasActiveItem(child, activeId, activePanelId)
      : !child.hiddenFromSidebar && child.id === activeId
  ));
}

function SubmenuItem({
  item,
  active,
  badges,
  nested = false,
  onOpenModule,
}: {
  item: WorkspaceModule;
  active: boolean;
  badges: Partial<Record<WorkspaceModuleId, number>>;
  /** 三级菜单项：缩进显示在二级分组标题下方。 */
  nested?: boolean;
  onOpenModule: (id: WorkspaceModuleId) => void;
}) {
  return (
    <button
      className={`sidebar-subitem${nested ? " is-nested" : ""}${active ? " is-active" : ""}`}
      onClick={() => onOpenModule(item.id)}
      title={item.label}
    >
      <span className={item.iconClass} aria-hidden="true">{item.icon}</span>
      <span>{item.label}</span>
      {Boolean(badges[item.id]) && (
        <span className="sidebar-module-badge" aria-label={`${badges[item.id]} 条待处理通知`}>{badges[item.id]}</span>
      )}
    </button>
  );
}

/** 二级分组（产品处理 / POD定制 / …）：点标题只展开三级菜单，不直接跳页。 */
function SubgroupSection({
  group,
  activeId,
  activePanelId,
  expanded,
  badges,
  onToggle,
  onOpenModule,
  onOpenPanel,
}: {
  group: WorkspaceNavigationGroup;
  activeId: WorkspaceModuleId;
  activePanelId: string | null;
  expanded: boolean;
  badges: Partial<Record<WorkspaceModuleId, number>>;
  onToggle: (group: WorkspaceNavigationGroup) => void;
  onOpenModule: (id: WorkspaceModuleId) => void;
  onOpenPanel?: (group: WorkspaceNavigationGroup, panelId: string) => void;
}) {
  const panels = group.panels ?? [];
  const children = group.children.filter((child): child is WorkspaceModule => !isWorkspaceNavigationGroup(child) && !child.hiddenFromSidebar);
  const hasActiveItem = groupHasActiveItem(group, activeId, activePanelId);
  return (
    <div className={`sidebar-subgroup${expanded ? " is-expanded" : ""}`}>
      <button
        className={`sidebar-subitem sidebar-subgroup-toggle${hasActiveItem ? " is-active-parent" : ""}`}
        onClick={() => onToggle(group)}
        title={group.label}
        aria-expanded={expanded}
      >
        <span className={group.iconClass} aria-hidden="true">{group.icon}</span>
        <span>{group.label}</span>
        <span className={`sidebar-subgroup-caret iconfont icon-down${expanded ? " is-expanded" : ""}`} aria-hidden="true" />
      </button>
      {expanded && (
        <div className="sidebar-subgroup-items">
          {panels.length
            ? panels.map((panel) => (
              <button
                key={panel.id}
                className={`sidebar-subitem is-nested${activePanelId === panel.id ? " is-active" : ""}`}
                onClick={() => onOpenPanel?.(group, panel.id)}
                title={panel.label}
                aria-pressed={activePanelId === panel.id}
              >
                <span className={panel.iconClass} aria-hidden="true">{panel.icon}</span>
                <span>{panel.label}</span>
              </button>
            ))
            : children.map((child) => (
              <SubmenuItem
                key={child.id}
                item={child}
                active={activeId === child.id}
                badges={badges}
                nested
                onOpenModule={onOpenModule}
              />
            ))}
        </div>
      )}
    </div>
  );
}

export function Sidebar({ collapsed, flyout, activeId, expandedGroupId, expandedSubgroupId, modules, onOpenModule, onToggleGroup, onToggleSubgroup, onHoverChange, activePanelId = null, onOpenPanel, badges = {}, onCloseGroups, onOpenAdTarget }: SidebarProps) {
  const cardRef = useRef<HTMLElement | null>(null);
  // 浮层高度/位移动画结束后补测一次滑块，否则三级菜单展开后胶囊会停在旧的落点。
  const [measureNonce, setMeasureNonce] = useState(0);
  const menuIndicator = useSlidingIndicator<HTMLElement>(MENU_ACTIVE_SELECTOR, [collapsed, activeId, expandedGroupId, measureNonce]);
  const submenuIndicator = useSlidingIndicator<HTMLDivElement>(SUBMENU_ACTIVE_SELECTOR, [collapsed, activeId, expandedGroupId, expandedSubgroupId, activePanelId, measureNonce]);

  /**
   * 子菜单浮层展开后夹回视口内。
   *
   * dock 在右侧，个人中心是最后一个（最低的）图标，浮层向下展开时经常没有空间——
   * 1100×540 这类扁窗口下实测浮层从 y=391 开始、底部到 761，超出视口 313px，
   * 后面几项被窗口下沿切掉（2026-10-22 用户反馈「下面的显示不全」）。
   * 之前只调 max-height 不够，那治不了「起点太靠下」。这里展开后量一次，
   * 整体上下平移到视口内，与触发项的相对位置保持不变。
   */
  useEffect(() => {
    if (!expandedGroupId) return;
    const frame = window.requestAnimationFrame(() => {
      const pop = document.querySelector<HTMLElement>(".sidebar-submenu.is-expanded");
      if (!pop) return;
      pop.style.transform = "";
      const rect = pop.getBoundingClientRect();
      const overflowBottom = rect.bottom - window.innerHeight;
      const overflowTop = -rect.top;
      let shift = 0;
      if (overflowBottom > 0) shift -= overflowBottom + 8;
      if (overflowTop > 0) shift += overflowTop + 8;
      if (shift !== 0) pop.style.transform = `translateY(${Math.round(shift)}px) scale(1)`;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [expandedGroupId, expandedSubgroupId, collapsed]);

  // 浮层是浮在内容上的：点侧栏（含浮层）以外的任何地方都收回。
  useEffect(() => {
    if (!flyout || !expandedGroupId) return;
    const onPointerDown = (event: PointerEvent) => {
      const card = cardRef.current;
      if (card && event.target instanceof Node && card.contains(event.target)) return;
      onCloseGroups();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [flyout, expandedGroupId, onCloseGroups]);

  const selectModule = (id: WorkspaceModuleId) => {
    onOpenModule(id);
    if (flyout) onCloseGroups();
  };

  const selectPanel = (group: WorkspaceNavigationGroup, panelId: string) => {
    onOpenPanel?.(group, panelId);
    if (flyout) onCloseGroups();
  };

  return (
    <aside
      ref={cardRef}
      className={`sidebar-card workspace-dock ${collapsed ? "is-collapsed" : ""}`}
      aria-label="主导航"
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
    >
      <div className="brand-lockup">
        <img
          className={`brand-logo${collapsed ? " is-mark" : ""}`}
          src={collapsed ? BRAND_MARK_URL : BRAND_LOGO_URL}
          alt={BRAND_NAME}
          data-brand-entry-target
        />
      </div>
      <p className="sidebar-caption">本地运营中台</p>
      <nav
        className="sidebar-menu dock-menu"
        ref={menuIndicator.containerRef}
        data-indicator-ready={menuIndicator.ready ? "true" : "false"}
      >
        {menuIndicator.rect && <span className="sidebar-selection-thumb" style={slidingIndicatorStyle(menuIndicator.rect)} aria-hidden="true" />}
        {modules.map((module) => {
          const isGroup = isWorkspaceNavigationGroup(module);
          const panels = isGroup ? module.panels ?? [] : [];
          const isPanelGroup = panels.length > 0;
          const groupExpanded = isGroup && expandedGroupId === module.id;
          const itemActive = isGroup
            ? groupHasActiveItem(module, activeId, activePanelId)
            : activeId === module.id;
          return (
            <div className={`sidebar-group dock-item-wrap dock-${module.id}`} key={module.id}>
              <button
                className={`sidebar-item dock-item ${itemActive ? "is-active" : ""}`}
                onClick={() => isGroup ? onToggleGroup(module) : selectModule(module.id)}
                title={module.label}
                aria-expanded={isGroup ? groupExpanded : undefined}
              >
                <span className={module.iconClass} aria-hidden="true">{module.icon}</span>
                <span className="dock-apple-icon" aria-hidden="true"><AppleAppGlyph name={module.id} /></span>
                <span className="sidebar-label dock-label">{module.label}</span>
                {!isGroup && Boolean(badges[module.id]) && <span className="sidebar-module-badge" aria-label={`${badges[module.id]} 条待处理通知`}>{badges[module.id]}</span>}
                {isGroup && <span className={`sidebar-group-caret iconfont icon-down ${groupExpanded ? "is-expanded" : ""}`} aria-hidden="true" />}
              </button>
              {isGroup ? (
                <div
                  className={`sidebar-submenu ${groupExpanded ? "is-expanded" : ""} dock-popover${isPanelGroup ? " is-panel-group" : ""}`}
                  aria-label={`${module.label}子导航`}
                  aria-hidden={!groupExpanded}
                  onTransitionEnd={() => setMeasureNonce((current) => current + 1)}
                >
                  <div
                    className="sidebar-submenu-inner dock-popover-inner"
                    ref={groupExpanded ? submenuIndicator.containerRef : undefined}
                    data-indicator-ready={groupExpanded ? (submenuIndicator.ready ? "true" : "false") : undefined}
                  >
                    {groupExpanded && submenuIndicator.rect && <span className="sidebar-selection-thumb is-subitem" style={slidingIndicatorStyle(submenuIndicator.rect)} aria-hidden="true" />}
                    {isPanelGroup
                      ? panels.map((panel) => (
                        <button
                          key={panel.id}
                          className={`sidebar-subitem ${activePanelId === panel.id ? "is-active" : ""}`}
                          onClick={() => selectPanel(module, panel.id)}
                          title={panel.label}
                          aria-pressed={activePanelId === panel.id}
                        >
                          <span className={panel.iconClass} aria-hidden="true">{panel.icon}</span>
                          <span>{panel.label}</span>
                        </button>
                      ))
                      : module.children.map((child) => (
                        isWorkspaceNavigationGroup(child) ? (
                          <SubgroupSection
                            key={child.id}
                            group={child}
                            activeId={activeId}
                            activePanelId={activePanelId}
                            expanded={expandedSubgroupId === child.id}
                            badges={badges}
                            onToggle={onToggleSubgroup}
                            onOpenModule={selectModule}
                            onOpenPanel={selectPanel}
                          />
                        ) : child.hiddenFromSidebar ? null : (
                          <SubmenuItem
                            key={child.id}
                            item={child}
                            active={activeId === child.id}
                            badges={badges}
                            onOpenModule={selectModule}
                          />
                        )
                      ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>
      <AdSlot slot="sidebar" placement="sidebar" onOpenTarget={onOpenAdTarget} />
    </aside>
  );
}

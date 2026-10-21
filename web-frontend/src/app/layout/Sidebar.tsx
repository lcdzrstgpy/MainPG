import { BRAND_LOGO_URL, BRAND_MARK_URL, BRAND_NAME } from "../../shared/brand";
import { AppleAppGlyph } from "../../shared/components/AppleAppGlyph";
import { slidingIndicatorStyle, useSlidingIndicator } from "../../shared/hooks/useSlidingIndicator";
import {
  isWorkspaceNavigationGroup,
  type WorkspaceModuleId,
  type WorkspaceNavigationGroup,
  type WorkspaceNavigationGroupId,
  type WorkspaceNavigationItem,
} from "../navigation/modules";

type SidebarProps = {
  collapsed: boolean;
  activeId: WorkspaceModuleId;
  expandedGroupId: WorkspaceNavigationGroupId | null;
  modules: WorkspaceNavigationItem[];
  onOpenModule: (id: WorkspaceModuleId) => void;
  onToggleGroup: (group: WorkspaceNavigationGroup) => void;
  onHoverChange: (hovered: boolean) => void;
  /** 当前页的页内面板：只在对应页面激活时传入，用于点亮侧栏子项。 */
  activePanelId?: string | null;
  onOpenPanel?: (group: WorkspaceNavigationGroup, panelId: string) => void;
  badges?: Partial<Record<WorkspaceModuleId, number>>;
};

const MENU_ACTIVE_SELECTOR = ":scope > .is-active, :scope > .sidebar-group > .sidebar-item.is-active";
const SUBMENU_ACTIVE_SELECTOR = ":scope > .is-active";

export function Sidebar({ collapsed, activeId, expandedGroupId, modules, onOpenModule, onToggleGroup, onHoverChange, activePanelId = null, onOpenPanel, badges = {} }: SidebarProps) {
  const menuIndicator = useSlidingIndicator<HTMLElement>(MENU_ACTIVE_SELECTOR, [collapsed, activeId, expandedGroupId]);
  const submenuIndicator = useSlidingIndicator<HTMLDivElement>(SUBMENU_ACTIVE_SELECTOR, [collapsed, activeId, expandedGroupId, activePanelId]);

  return (
    <aside
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
          const visibleChildren = isGroup && !isPanelGroup ? module.children.filter((child) => !child.hiddenFromSidebar) : [];
          const childActive = isPanelGroup
            ? activePanelId !== null && panels.some((panel) => panel.id === activePanelId)
            : visibleChildren.some((child) => child.id === activeId);
          const groupExpanded = isGroup && expandedGroupId === module.id;
          const itemActive = isGroup
            ? (isPanelGroup ? activeId === module.defaultChildId : childActive)
            : activeId === module.id;
          return (
            <div className={`sidebar-group dock-item-wrap dock-${module.id}`} key={module.id}>
              <button
                className={`sidebar-item dock-item ${itemActive ? "is-active" : ""}`}
                onClick={() => isGroup ? onToggleGroup(module) : onOpenModule(module.id)}
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
                <div className={`sidebar-submenu ${groupExpanded ? "is-expanded" : ""} dock-popover${isPanelGroup ? " is-panel-group" : ""}`} aria-label={`${module.label}子导航`} aria-hidden={!groupExpanded}>
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
                          onClick={() => onOpenPanel?.(module, panel.id)}
                          title={panel.label}
                          aria-pressed={activePanelId === panel.id}
                        >
                          <span className={panel.iconClass} aria-hidden="true">{panel.icon}</span>
                          <span>{panel.label}</span>
                        </button>
                      ))
                      : visibleChildren.map((child) => (
                        <button key={child.id} className={`sidebar-subitem ${activeId === child.id ? "is-active" : ""}`} onClick={() => onOpenModule(child.id)} title={child.label}>
                          <span className={child.iconClass} aria-hidden="true">{child.icon}</span>
                          <span>{child.label}</span>
                          {Boolean(badges[child.id]) && <span className="sidebar-module-badge" aria-label={`${badges[child.id]} 条待处理通知`}>{badges[child.id]}</span>}
                        </button>
                      ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}

import { BRAND_LOGO_URL, BRAND_MARK_URL, BRAND_NAME } from "../../shared/brand";
import { AppleAppGlyph } from "../../shared/components/AppleAppGlyph";
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
  badges?: Partial<Record<WorkspaceModuleId, number>>;
};

export function Sidebar({ collapsed, activeId, expandedGroupId, modules, onOpenModule, onToggleGroup, onHoverChange, badges = {} }: SidebarProps) {
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
      <nav className="sidebar-menu dock-menu">
        {modules.map((module) => {
          const isGroup = isWorkspaceNavigationGroup(module);
          const visibleChildren = isGroup ? module.children.filter((child) => !child.hiddenFromSidebar) : [];
          const childActive = visibleChildren.some((child) => child.id === activeId);
          const groupExpanded = isGroup && expandedGroupId === module.id;
          const itemActive = isGroup ? childActive : activeId === module.id;
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
                <div className={`sidebar-submenu ${groupExpanded ? "is-expanded" : ""} dock-popover`} aria-label={`${module.label}子导航`} aria-hidden={!groupExpanded}>
                  <div className="sidebar-submenu-inner dock-popover-inner">
                  {visibleChildren.map((child) => (
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

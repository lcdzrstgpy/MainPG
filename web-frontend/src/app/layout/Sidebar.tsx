import { BRAND_LOGO_URL, BRAND_MARK_URL, BRAND_NAME } from "../../shared/brand";
import type { WorkspaceModule, WorkspaceModuleId } from "../navigation/modules";

type SidebarProps = {
  collapsed: boolean;
  activeId: WorkspaceModuleId;
  expandedModuleIds: WorkspaceModuleId[];
  modules: WorkspaceModule[];
  onSelect: (id: WorkspaceModuleId) => void;
  onHoverChange: (hovered: boolean) => void;
  badges?: Partial<Record<WorkspaceModuleId, number>>;
};

export function Sidebar({ collapsed, activeId, expandedModuleIds, modules, onSelect, onHoverChange, badges = {} }: SidebarProps) {
  return (
    <aside
      className={`sidebar-card ${collapsed ? "is-collapsed" : ""}`}
      aria-label="主导航"
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
    >
      <div className="brand-lockup">
        <img className={`brand-logo${collapsed ? " is-mark" : ""}`} src={collapsed ? BRAND_MARK_URL : BRAND_LOGO_URL} alt={BRAND_NAME} />
      </div>
      <p className="sidebar-caption">本地运营中台</p>
      <nav className="sidebar-menu">
        {modules.filter((module) => !module.hiddenFromSidebar).map((module) => {
          const visibleChildren = module.children?.filter((child) => !child.hiddenFromSidebar) ?? [];
          const childActive = visibleChildren.some((child) => child.id === activeId);
          const groupExpanded = expandedModuleIds.includes(module.id);
          return (
            <div className="sidebar-group" key={module.id}>
              <button
                className={`sidebar-item ${activeId === module.id || childActive ? "is-active" : ""}`}
                onClick={() => onSelect(module.id)}
                title={module.label}
                aria-expanded={visibleChildren.length ? groupExpanded : undefined}
              >
                <span className={module.iconClass} aria-hidden="true">{module.icon}</span>
                <span className="sidebar-label">{module.label}</span>
                {Boolean(badges[module.id]) && <span className="sidebar-module-badge" aria-label={`${badges[module.id]} 条待处理通知`}>{badges[module.id]}</span>}
              </button>
              {visibleChildren.length && groupExpanded ? (
                <div className="sidebar-submenu" aria-label={`${module.label}子导航`}>
                  {visibleChildren.map((child) => (
                    <button key={child.id} className={`sidebar-subitem ${activeId === child.id ? "is-active" : ""}`} onClick={() => onSelect(child.id)} title={child.label}>
                      <span className={child.iconClass} aria-hidden="true">{child.icon}</span>
                      <span>{child.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}

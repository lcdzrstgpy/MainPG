import type { WorkspaceModule, WorkspaceModuleId } from "../navigation/modules";

type SidebarProps = {
  collapsed: boolean;
  activeId: WorkspaceModuleId;
  expandedModuleIds: WorkspaceModuleId[];
  modules: WorkspaceModule[];
  onSelect: (id: WorkspaceModuleId) => void;
  onHoverChange: (hovered: boolean) => void;
};

export function Sidebar({ collapsed, activeId, expandedModuleIds, modules, onSelect, onHoverChange }: SidebarProps) {
  return (
    <aside
      className={`sidebar-card ${collapsed ? "is-collapsed" : ""}`}
      aria-label="主导航"
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
    >
      <div className="brand-lockup">
        <span className="brand-mark">智</span>
        <span className="brand-name">智能电商平台</span>
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

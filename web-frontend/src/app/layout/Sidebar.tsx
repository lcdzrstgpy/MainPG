import type { WorkspaceModule, WorkspaceModuleId } from "../navigation/modules";

type SidebarProps = {
  collapsed: boolean;
  activeId: WorkspaceModuleId;
  modules: WorkspaceModule[];
  onSelect: (id: WorkspaceModuleId) => void;
};

export function Sidebar({ collapsed, activeId, modules, onSelect }: SidebarProps) {
  return (
    <aside className={`sidebar-card ${collapsed ? "is-collapsed" : ""}`} aria-label="主导航">
      <div className="brand-lockup">
        <span className="brand-mark">智</span>
        {!collapsed && <span>智能电商平台</span>}
      </div>
      <p className="sidebar-caption">本地运营中台</p>
      <nav className="sidebar-menu">
        {modules.filter((module) => !module.hiddenFromSidebar).map((module) => {
          const visibleChildren = module.children?.filter((child) => !child.hiddenFromSidebar) ?? [];
          const childActive = visibleChildren.some((child) => child.id === activeId);
          return (
            <div className="sidebar-group" key={module.id}>
              <button className={`sidebar-item ${activeId === module.id || childActive ? "is-active" : ""}`} onClick={() => onSelect(module.id)} title={module.label}>
                <span aria-hidden="true">{module.icon}</span>
                {!collapsed && <span>{module.label}</span>}
              </button>
              {!collapsed && visibleChildren.length ? (
                <div className="sidebar-submenu" aria-label={`${module.label}子导航`}>
                  {visibleChildren.map((child) => (
                    <button key={child.id} className={`sidebar-subitem ${activeId === child.id ? "is-active" : ""}`} onClick={() => onSelect(child.id)} title={child.label}>
                      <span aria-hidden="true">{child.icon}</span>
                      <span>{child.label}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>
      {!collapsed && <div className="sidebar-status"><span className="status-dot" />本地演示模式</div>}
    </aside>
  );
}

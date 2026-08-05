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
        {modules.map((module) => (
          <button key={module.id} className={`sidebar-item ${activeId === module.id ? "is-active" : ""}`} onClick={() => onSelect(module.id)} title={module.label}>
            <span aria-hidden="true">{module.icon}</span>
            {!collapsed && <span>{module.label}</span>}
          </button>
        ))}
      </nav>
      {!collapsed && <div className="sidebar-status"><span className="status-dot" />本地演示模式</div>}
    </aside>
  );
}

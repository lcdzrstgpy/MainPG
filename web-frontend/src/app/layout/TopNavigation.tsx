import type { WorkspaceModule, WorkspaceModuleId } from "../navigation/modules";

type TopNavigationProps = {
  activeId: WorkspaceModuleId;
  tabs: WorkspaceModule[];
  onToggleSidebar: () => void;
  onSelectTab: (id: WorkspaceModuleId) => void;
  onCloseTab: (id: WorkspaceModuleId) => void;
  onSignOut: () => void;
};

export function TopNavigation({ activeId, tabs, onToggleSidebar, onSelectTab, onCloseTab, onSignOut }: TopNavigationProps) {
  return (
    <header className="topbar-card">
      <div className="topbar-main">
        <button className="icon-button" onClick={onToggleSidebar} aria-label="展开或收起侧边栏">☰</button>
        <div className="breadcrumb"><span>工作台</span><span>/</span><strong>{tabs.find((tab) => tab.id === activeId)?.label}</strong></div>
        <div className="topbar-actions">
          <button className="soft-action" type="button">⌕ 搜索</button>
          <details className="user-menu">
            <summary><span className="avatar">U</span><span>本地演示用户</span><span className="caret">⌄</span></summary>
            <div className="user-popover">
              <strong>个人中心</strong>
              <span>员工账号与偏好设置将在此接入</span>
              <button type="button" onClick={onSignOut}>退出演示</button>
            </div>
          </details>
        </div>
      </div>
      <div className="tab-strip" aria-label="内容页标签导航">
        {tabs.map((tab) => (
          <div key={tab.id} className={`page-tab ${activeId === tab.id ? "is-active" : ""}`}>
            <button onClick={() => onSelectTab(tab.id)}>{tab.icon} {tab.label}</button>
            {tab.id !== "dashboard" && <button className="tab-close" onClick={() => onCloseTab(tab.id)} aria-label={`关闭${tab.label}`}>×</button>}
          </div>
        ))}
      </div>
    </header>
  );
}

import type { WorkspaceModuleId } from "../navigation/modules";

export type WorkspaceTab = {
  key: string;
  moduleId: WorkspaceModuleId;
  label: string;
  icon: string;
  directionId?: string;
};

type TopNavigationProps = {
  activeKey: string;
  tabs: WorkspaceTab[];
  onToggleSidebar: () => void;
  onSelectTab: (key: string) => void;
  onCloseTab: (key: string) => void;
  onSignOut: () => void;
};

export function TopNavigation({ activeKey, tabs, onToggleSidebar, onSelectTab, onCloseTab, onSignOut }: TopNavigationProps) {
  return (
    <header className="topbar-card">
      <div className="topbar-main">
        <button className="icon-button" onClick={onToggleSidebar} aria-label="展开或收起侧边栏">☰</button>
        <div className="breadcrumb"><span>工作台</span><span>/</span><strong>{tabs.find((tab) => tab.key === activeKey)?.label}</strong></div>
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
          <div key={tab.key} className={`page-tab ${activeKey === tab.key ? "is-active" : ""}`}>
            <button onClick={() => onSelectTab(tab.key)}>{tab.icon} {tab.label}</button>
            {tab.moduleId !== "dashboard" && <button className="tab-close" onClick={() => onCloseTab(tab.key)} aria-label={`关闭${tab.label}`}>×</button>}
          </div>
        ))}
      </div>
    </header>
  );
}

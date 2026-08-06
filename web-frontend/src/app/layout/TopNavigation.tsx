import { useState } from "react";

import type { WorkspaceModuleId } from "../navigation/modules";

export type WorkspaceTab = {
  key: string;
  moduleId: WorkspaceModuleId;
  label: string;
  icon: string;
  iconClass?: string;
  directionId?: string;
};

type TopNavigationProps = {
  sidebarPinned: boolean;
  activeKey: string;
  tabs: WorkspaceTab[];
  onToggleSidebar: () => void;
  onSelectTab: (key: string) => void;
  onCloseTab: (key: string) => void;
  onSignOut: () => void;
};

export function TopNavigation({ sidebarPinned, activeKey, tabs, onToggleSidebar, onSelectTab, onCloseTab, onSignOut }: TopNavigationProps) {
  const [closingKeys, setClosingKeys] = useState<string[]>([]);

  const closeTabWithEffect = (key: string) => {
    if (closingKeys.includes(key)) return;
    setClosingKeys((current) => [...current, key]);
    window.setTimeout(() => {
      onCloseTab(key);
      setClosingKeys((current) => current.filter((item) => item !== key));
    }, 180);
  };

  return (
    <header className="topbar-card">
      <div className="topbar-main">
        <button
          className={`icon-button sidebar-pin-button ${sidebarPinned ? "is-active" : ""}`}
          onClick={onToggleSidebar}
          aria-label={sidebarPinned ? "取消固定侧边栏" : "固定展开侧边栏"}
          aria-pressed={sidebarPinned}
          title={sidebarPinned ? "取消固定侧边栏" : "固定展开侧边栏"}
        >
          ☰
        </button>
        <div className="breadcrumb"><span>工作台</span><span>/</span><strong>{tabs.find((tab) => tab.key === activeKey)?.label}</strong></div>
        <div className="topbar-actions">
          <details className="user-menu">
            <summary><span className="avatar">U</span><span>本地演示用户</span><span className="caret">⌄</span></summary>
            <div className="user-popover">
              <strong>个人中心</strong>
              <span>管理当前员工账号和个人使用偏好</span>
              <div className="user-menu-actions">
                <button className="user-menu-action" type="button">
                  <span className="iconfont icon-edit" aria-hidden="true" />
                  <span>用户账号</span>
                </button>
                <button className="user-menu-action" type="button">
                  <span className="iconfont icon-setting" aria-hidden="true" />
                  <span>偏好设置</span>
                </button>
              </div>
              <button className="user-menu-signout" type="button" onClick={onSignOut}>退出演示</button>
            </div>
          </details>
        </div>
      </div>
      <div className="tab-strip" aria-label="内容页标签导航">
        {tabs.map((tab) => (
          <div key={tab.key} className={`page-tab ${activeKey === tab.key ? "is-active" : ""} ${closingKeys.includes(tab.key) ? "is-closing" : ""}`}>
            <button onClick={() => onSelectTab(tab.key)}><span className={tab.iconClass} aria-hidden="true">{tab.icon}</span> {tab.label}</button>
            {tab.moduleId !== "dashboard" && <button className="tab-close" onClick={() => closeTabWithEffect(tab.key)} aria-label={`关闭${tab.label}`}><span className="tab-close-icon" aria-hidden="true">×</span></button>}
          </div>
        ))}
      </div>
    </header>
  );
}

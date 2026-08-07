import { useEffect, useRef, useState } from "react";

import type { WorkspaceModuleId } from "../navigation/modules";

export type WorkspaceTab = {
  key: string;
  moduleId: WorkspaceModuleId;
  label: string;
  icon: string;
  iconClass?: string;
  directionId?: string;
  draftIds?: number[];
  processingOptions?: unknown;
};

type TopNavigationProps = {
  sidebarPinned: boolean;
  topbarPinned: boolean;
  activeKey: string;
  tabs: WorkspaceTab[];
  onToggleSidebar: () => void;
  onToggleTopbarPin: () => void;
  onSelectTab: (key: string) => void;
  onCloseTab: (key: string) => void;
  onSignOut: () => void;
};

export function TopNavigation({ sidebarPinned, topbarPinned, activeKey, tabs, onToggleSidebar, onToggleTopbarPin, onSelectTab, onCloseTab, onSignOut }: TopNavigationProps) {
  const [closingKeys, setClosingKeys] = useState<string[]>([]);
  const [topbarStuck, setTopbarStuck] = useState(false);
  const topbarRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!topbarPinned) {
      setTopbarStuck(false);
      return;
    }

    const updateStuckState = () => {
      const top = topbarRef.current?.getBoundingClientRect().top ?? 18;
      setTopbarStuck(window.scrollY > 0 && top <= 0.5);
    };

    window.addEventListener("scroll", updateStuckState, { passive: true });
    window.addEventListener("resize", updateStuckState);
    updateStuckState();

    return () => {
      window.removeEventListener("scroll", updateStuckState);
      window.removeEventListener("resize", updateStuckState);
    };
  }, [topbarPinned]);

  const closeTabWithEffect = (key: string) => {
    if (closingKeys.includes(key)) return;
    setClosingKeys((current) => [...current, key]);
    window.setTimeout(() => {
      onCloseTab(key);
      setClosingKeys((current) => current.filter((item) => item !== key));
    }, 180);
  };

  return (
    <header ref={topbarRef} className={`topbar-card ${topbarPinned ? "is-pinned" : ""} ${topbarStuck ? "is-stuck" : ""}`}>
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
        <div id="workspace-topbar-status" className="topbar-status-slot" />
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
      <div className="topbar-lower-row">
        <div className="tab-strip" aria-label="内容页标签导航">
          {tabs.map((tab) => (
            <div key={tab.key} className={`page-tab ${activeKey === tab.key ? "is-active" : ""} ${closingKeys.includes(tab.key) ? "is-closing" : ""}`}>
              <button onClick={() => onSelectTab(tab.key)}><span className={tab.iconClass} aria-hidden="true">{tab.icon}</span> {tab.label}</button>
              {tab.moduleId !== "dashboard" && <button className="tab-close" onClick={() => closeTabWithEffect(tab.key)} aria-label={`关闭${tab.label}`}><span className="tab-close-icon" aria-hidden="true">×</span></button>}
            </div>
          ))}
        </div>
        <button
          type="button"
          className={`topbar-pin-toggle ${topbarPinned ? "is-active" : ""}`}
          onClick={onToggleTopbarPin}
          aria-label={topbarPinned ? "取消固定顶部导航" : "固定顶部导航"}
          aria-pressed={topbarPinned}
          title={topbarPinned ? "取消固定" : "固定顶部导航"}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8.2 2.5h7.6l-1.3 5.9c2.2.9 3.5 2.7 3.5 4.8 0 .8-.6 1.3-1.4 1.3H13l-.5 7h-1l-.5-7H7.4c-.8 0-1.4-.5-1.4-1.3 0-2.1 1.3-3.9 3.5-4.8L8.2 2.5Z" />
          </svg>
        </button>
      </div>
    </header>
  );
}

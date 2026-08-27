import { useEffect, useRef, useState } from "react";

import type { WorkspaceModuleId } from "../navigation/modules";
import { useTheme, THEME_META, type ThemeId } from "../../shared/hooks/useTheme";
import { UI_MODE_META, useUiMode, type UiModeId } from "../../shared/hooks/useUiMode";
import { InboxBell } from "../../shared/components/InboxBell";

export type WorkspaceTab = {
  key: string;
  moduleId: WorkspaceModuleId;
  label: string;
  icon: string;
  iconClass?: string;
  directionId?: string;
  draftIds?: number[];
  premiumDraftIds?: number[];
  processingOptions?: unknown;
  taskRunId?: number;
  taskId?: number;
  dimensionBatchId?: string;
  dimensionItemId?: string;
  returnTaskId?: number;
  dimensionChangeSetId?: string;
};

type TopNavigationProps = {
  sidebarPinned: boolean;
  activeKey: string;
  tabs: WorkspaceTab[];
  onToggleSidebar: () => void;
  onSelectTab: (key: string) => void;
  onCloseTab: (key: string) => void;
  onOpenPersonalCenter: () => void;
  onSignOut: () => void;
};

export function TopNavigation({ sidebarPinned, activeKey, tabs, onToggleSidebar, onSelectTab, onCloseTab, onOpenPersonalCenter, onSignOut }: TopNavigationProps) {
  const [closingKeys, setClosingKeys] = useState<string[]>([]);
  const [topbarStuck, setTopbarStuck] = useState(false);
  const topbarRef = useRef<HTMLElement>(null);
  const { theme, setTheme } = useTheme();
  const { uiMode, setUiMode } = useUiMode();

  useEffect(() => {
    const updateStuckState = () => {
      const scrollTop = window.scrollY;
      setTopbarStuck((stuck) => stuck ? scrollTop > 8 : scrollTop > 36);
    };

    window.addEventListener("scroll", updateStuckState, { passive: true });
    window.addEventListener("resize", updateStuckState);
    updateStuckState();

    return () => {
      window.removeEventListener("scroll", updateStuckState);
      window.removeEventListener("resize", updateStuckState);
    };
  }, []);

  const closeTabWithEffect = (key: string) => {
    if (closingKeys.includes(key)) return;
    setClosingKeys((current) => [...current, key]);
    window.setTimeout(() => {
      onCloseTab(key);
      setClosingKeys((current) => current.filter((item) => item !== key));
    }, 180);
  };

  return (
    <header ref={topbarRef} className={`topbar-card is-pinned ${topbarStuck ? "is-stuck" : ""}`}>
      <div className="topbar-main">
        <button
          className={`icon-button sidebar-pin-button ${sidebarPinned ? "is-active" : ""}`}
          onClick={onToggleSidebar}
          aria-label={sidebarPinned ? "取消固定侧边栏" : "固定展开侧边栏"}
          aria-pressed={sidebarPinned}
          title={sidebarPinned ? "取消固定侧边栏" : "固定展开侧边栏"}
        >
          {uiMode === "apple" ? <span className="iconfont icon-appstore" aria-hidden="true" /> : "☰"}
        </button>
        {uiMode === "apple" ? (
          <div className="mac-toolbar-title"><strong>{tabs.find((tab) => tab.key === activeKey)?.label}</strong><span>界野工作台</span></div>
        ) : (
          <div className="breadcrumb"><span>工作台</span><span>/</span><strong>{tabs.find((tab) => tab.key === activeKey)?.label}</strong></div>
        )}
        <div id="workspace-topbar-status" className="topbar-status-slot" />
        <div className="topbar-actions">
          <InboxBell />
          <details className="user-menu">
            <summary><span className="avatar">{uiMode === "apple" ? "界" : "U"}</span><span>本地用户</span><span className="caret">⌄</span></summary>
            <div className="user-popover">
              <strong>个人中心</strong>
              <span>管理当前员工账号</span>
              <div className="user-menu-actions">
                <button className="user-menu-action" type="button" onClick={onOpenPersonalCenter}>
                  <span className="iconfont icon-edit" aria-hidden="true" />
                  <span>用户账号</span>
                </button>
              </div>
              <div className="ui-mode-switcher">
                <span className="ui-mode-switcher-label">界面布局</span>
                <div className="ui-mode-options">
                  {(Object.keys(UI_MODE_META) as UiModeId[]).map((id) => (
                    <button
                      key={id}
                      type="button"
                      className={`ui-mode-option ${uiMode === id ? "is-active" : ""}`}
                      onClick={() => setUiMode(id)}
                    >
                      <span className={`ui-mode-preview is-${id}`} aria-hidden="true"><i /><i /></span>
                      <span className="ui-mode-copy"><strong>{UI_MODE_META[id].label}</strong><small>{UI_MODE_META[id].description}</small></span>
                      {uiMode === id && <span className="ui-mode-check">✓</span>}
                    </button>
                  ))}
                </div>
              </div>
              {uiMode === "classic" && (
                <div className="theme-switcher">
                  <span className="theme-switcher-label">主题风格</span>
                  <div className="theme-options">
                    {(Object.keys(THEME_META) as ThemeId[]).map((id) => (
                      <button
                        key={id}
                        type="button"
                        className={`theme-option ${theme === id ? "is-active" : ""}`}
                        onClick={() => setTheme(id)}
                      >
                        <span className="theme-swatch" style={{ background: THEME_META[id].swatch }} />
                        <span className="theme-option-name">{THEME_META[id].label}</span>
                        {theme === id && <span className="theme-check">✓</span>}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <button className="user-menu-signout" type="button" onClick={onSignOut}>退出登录</button>
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
      </div>
    </header>
  );
}

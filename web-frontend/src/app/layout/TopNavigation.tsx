import { useEffect, useRef, useState } from "react";

import type { WorkspaceModuleId } from "../navigation/modules";
import { useTheme, THEME_META, BUILTIN_THEME_IDS, DOWNLOADABLE_THEME_IDS, type ThemeId } from "../../shared/hooks/useTheme";
import { UI_MODE_META, useUiMode, type UiModeId } from "../../shared/hooks/useUiMode";
import { InboxBell } from "../../shared/components/InboxBell";
import { getAuthAccount } from "../../transport/http/client";

/** 与个人中心共享的头像存储键，右键头像与个人中心头像保持一致。 */
export const AVATAR_STORAGE_KEY = "jye_workspace_avatar";
/** 头像变化通知：保存后派发，供同页其它位置（如个人中心）实时同步。 */
export const AVATAR_CHANGED_EVENT = "jye_avatar_changed";

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
  initialSetId?: string;
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
  const [themePanelOpen, setThemePanelOpen] = useState(false);
  const [themeStoreOpen, setThemeStoreOpen] = useState(false);
  const topbarRef = useRef<HTMLElement>(null);
  const themeMenuRef = useRef<HTMLDivElement>(null);
  const { theme, setTheme, downloadedThemes, isDownloaded, downloadTheme } = useTheme();
  const { uiMode, setUiMode } = useUiMode();

  // 本地头像：仅用于本地展示，base64 存 localStorage。
  const AVATAR_KEY = AVATAR_STORAGE_KEY;
  const [avatarSrc, setAvatarSrc] = useState<string | null>(() => {
    try {
      return localStorage.getItem(AVATAR_KEY);
    } catch {
      return null;
    }
  });
  const account = getAuthAccount<{ username?: string }>() ?? {};
  const displayName = account.username?.trim() || "";
  const fileInputRef = useRef<HTMLInputElement>(null);
  // 头像大图预览。
  const [avatarPreviewOpen, setAvatarPreviewOpen] = useState(false);

  const openAvatarPicker = () => fileInputRef.current?.click();

  const openAvatarPreview = () => {
    if (avatarSrc) setAvatarPreviewOpen(true);
  };

  const handleAvatarFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = ""; // 允许再次选择同一文件
    if (!file || !file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : null;
      if (!result) return;
      setAvatarSrc(result);
      try {
        localStorage.setItem(AVATAR_KEY, result);
      } catch {
        // 存储空间不足时仅保留内存中的头像用于本次显示。
      }
      // 通知同页其它位置（如个人中心）头像已更新。
      self.dispatchEvent(new Event(AVATAR_CHANGED_EVENT));
    };
    reader.readAsDataURL(file);
  };
  // 关闭动画延迟期间判活用的最新 activeKey 与在途 timer（卸载时清理）。
  const activeKeyRef = useRef(activeKey);
  useEffect(() => {
    activeKeyRef.current = activeKey;
  }, [activeKey]);
  const closingTimers = useRef(new Set<number>());
  useEffect(() => {
    const timers = closingTimers.current;
    return () => {
      timers.forEach((id) => window.clearTimeout(id));
      timers.clear();
    };
  }, []);

  useEffect(() => {
    if (!themePanelOpen && !themeStoreOpen) return;
    const onDocMouseDown = (event: MouseEvent) => {
      if (themeMenuRef.current && !themeMenuRef.current.contains(event.target as Node)) {
        setThemePanelOpen(false);
        setThemeStoreOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [themePanelOpen, themeStoreOpen]);

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
    const timer = window.setTimeout(() => {
      closingTimers.current.delete(timer);
      setClosingKeys((current) => current.filter((item) => item !== key));
      // 180ms 动画期间该标签若被重新打开并激活（activeKey 又指向它），说明用户
      // 想保留，跳过删除——否则会误删刚重开的标签。
      if (activeKeyRef.current === key) return;
      onCloseTab(key);
    }, 180);
    closingTimers.current.add(timer);
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
          <div className="theme-quick-menu" ref={themeMenuRef}>
            <button
              type="button"
              className={`theme-quick-trigger ${themePanelOpen || themeStoreOpen ? "is-active" : ""}`}
              onClick={() => {
                if (themeStoreOpen) {
                  setThemeStoreOpen(false);
                  setThemePanelOpen(false);
                } else {
                  setThemePanelOpen((open) => !open);
                }
              }}
              aria-label={`主题风格（当前：${THEME_META[theme].label}）`}
              aria-expanded={themePanelOpen || themeStoreOpen}
              title="主题风格"
            >
              <span className="face-mouth" aria-hidden="true" />
            </button>
            {(themePanelOpen || themeStoreOpen) && (
              <div className={`theme-quick-popover ${themeStoreOpen ? "is-store" : ""}`} role="dialog" aria-label="主题风格">
                {themeStoreOpen ? (
                  <div className="theme-store">
                    <header className="theme-quick-header">
                      <button
                        type="button"
                        className="theme-store-back"
                        onClick={() => setThemeStoreOpen(false)}
                        aria-label="返回快捷面板"
                      >
                        ←
                      </button>
                      <strong>更多主题</strong>
                      <span aria-hidden="true" />
                    </header>
                    <div className="theme-store-grid">
                      {DOWNLOADABLE_THEME_IDS.map((id) => {
                        const downloaded = isDownloaded(id);
                        const active = theme === id;
                        return (
                          <div key={id} className={`theme-store-card ${active ? "is-active" : ""}`}>
                            <span className="theme-store-swatch" style={{ background: THEME_META[id].swatch }} />
                            <span className="theme-store-name">{THEME_META[id].label}</span>
                            <button
                              type="button"
                              className={`theme-store-action ${downloaded ? "is-use" : "is-download"}`}
                              onClick={() => {
                                if (!downloaded) downloadTheme(id);
                                setTheme(id);
                                setThemeStoreOpen(false);
                                setThemePanelOpen(false);
                              }}
                            >
                              {active ? "使用中" : downloaded ? "使用" : "下载"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <>
                    <header className="theme-quick-header"><strong>主题风格</strong></header>
                    <div className="theme-quick-options">
                      {BUILTIN_THEME_IDS.map((id) => (
                        <button
                          key={id}
                          type="button"
                          className={`theme-option ${theme === id ? "is-active" : ""}`}
                          onClick={() => {
                            setTheme(id);
                            setThemePanelOpen(false);
                          }}
                        >
                          <span className="theme-swatch" style={{ background: THEME_META[id].swatch }} />
                          <span className="theme-option-name">{THEME_META[id].label}</span>
                          {theme === id && <span className="theme-check">✓</span>}
                        </button>
                      ))}
                      {DOWNLOADABLE_THEME_IDS.filter((id) => isDownloaded(id)).map((id) => (
                        <button
                          key={id}
                          type="button"
                          className={`theme-option ${theme === id ? "is-active" : ""}`}
                          onClick={() => {
                            setTheme(id);
                            setThemePanelOpen(false);
                          }}
                        >
                          <span className="theme-swatch" style={{ background: THEME_META[id].swatch }} />
                          <span className="theme-option-name">{THEME_META[id].label}</span>
                          {theme === id && <span className="theme-check">✓</span>}
                        </button>
                      ))}
                    </div>
                    <div className="theme-quick-footer">
                      <button
                        type="button"
                        className="theme-more-button"
                        onClick={() => setThemeStoreOpen(true)}
                      >
                        更多主题
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
          <InboxBell />
          <details className="user-menu">
            <summary className={avatarSrc ? "has-avatar" : ""}>
              {avatarSrc ? (
                <img className="avatar-img" src={avatarSrc} alt="本地用户头像" title="查看大图" onClick={openAvatarPreview} />
              ) : (
                <span className="avatar">{uiMode === "apple" ? "界" : "U"}</span>
              )}
              {displayName ? <span>{displayName}</span> : (!avatarSrc && <span>本地用户</span>)}
              <span className="caret">⌄</span>
            </summary>
            <div className="user-popover">
              <strong>个人中心</strong>
              <span>管理当前员工账号</span>
              <div className="user-menu-actions">
                <button className="user-menu-action" type="button" onClick={openAvatarPicker}>
                  <span className="iconfont icon-camera" aria-hidden="true" />
                  <span>{avatarSrc ? "更换头像" : "上传头像"}</span>
                </button>
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
              <button className="user-menu-signout" type="button" onClick={onSignOut}>退出登录</button>
            </div>
          </details>
          <input
            ref={fileInputRef}
            className="avatar-file-input"
            type="file"
            accept="image/*"
            onChange={handleAvatarFile}
            aria-hidden="true"
            tabIndex={-1}
          />
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
      {avatarPreviewOpen && avatarSrc && (
        <div className="avatar-preview-layer" onMouseDown={() => setAvatarPreviewOpen(false)} role="dialog" aria-modal="true" aria-label="头像预览">
          <div className="avatar-preview-panel" onMouseDown={(event) => event.stopPropagation()}>
            <button className="avatar-preview-close" type="button" onClick={() => setAvatarPreviewOpen(false)} aria-label="关闭预览">×</button>
            <img className="avatar-preview-img" src={avatarSrc} alt="头像大图" />
          </div>
        </div>
      )}
    </header>
  );
}

import { useEffect, useState } from "react";
import { BRAND_LOGO_URL, BRAND_MANUAL_URL, BRAND_MARK_URL, BRAND_NAME } from "../../../shared/brand";
import type { WorkspaceModule, WorkspaceModuleId } from "../../../app/navigation/modules";
import { isWorkspaceNavigationGroup, workspaceModules } from "../../../app/navigation/modules";
import { DashboardStats } from "../components/DashboardStats";
import { useUiMode } from "../../../shared/hooks/useUiMode";
import { AppleAppGlyph } from "../../../shared/components/AppleAppGlyph";

function getGreeting(): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Shanghai", hour: "numeric", hour12: false }).format(new Date())
  );
  if (hour >= 5 && hour < 12) return "早上好";
  if (hour >= 12 && hour < 18) return "下午好";
  return "晚上好";
}

type WorkspaceHomePageProps = { onOpenModule: (id: WorkspaceModuleId) => void };

type NavTone = "blue" | "cyan" | "violet" | "green" | "amber" | "rose" | "slate" | "orange" | "pink" | "indigo";

type NavModule = { id: WorkspaceModuleId; label: string; description: string; iconClass?: string; tone: NavTone };

/** 模块强调色：与 apple 模式应用图标共用同一套色名。 */
const MODULE_TONE: Record<string, NavTone> = {
  daily_selection: "blue",
  product_processing: "violet",
  product_processing_history: "slate",
  dimension_canvas: "cyan",
  combo_generate: "orange",
  combo_prompt_preset: "orange",
  combo_history: "slate",
  pod_customization: "indigo",
  price_verification: "green",
  profit_activity: "green",
  profit_activity_products: "pink",
  personal_center: "slate",
};

function toNavModule(module: WorkspaceModule): NavModule {
  return {
    id: module.id,
    label: module.label,
    description: module.description,
    iconClass: module.iconClass,
    tone: MODULE_TONE[module.id] ?? "blue",
  };
}

/** 直接复用侧边栏的模块配置，避免功能名称与描述出现第二处口径。 */
const navGroups: Array<{ id: string; label: string; items: NavModule[] }> = (() => {
  const groups: Array<{ id: string; label: string; items: NavModule[] }> = [];
  const standalone: NavModule[] = [];
  for (const item of workspaceModules) {
    if (item.id === "dashboard") continue;
    if (isWorkspaceNavigationGroup(item)) {
      groups.push({ id: item.id, label: item.label, items: item.children.map(toNavModule) });
    } else {
      standalone.push(toNavModule(item));
    }
  }
  if (standalone.length) groups.push({ id: "standalone", label: "更多工具", items: standalone });
  return groups;
})();

function ClassicModuleNav({ onOpenModule }: { onOpenModule: (id: WorkspaceModuleId) => void }) {
  return (
    <section className="dash-panel dash-nav" aria-label="功能导航">
      <header className="dash-panel-head">
        <div className="dash-panel-title">
          <h3>功能导航</h3>
          <p>按业务流程整理的全部模块，点击卡片直达</p>
        </div>
      </header>
      {navGroups.map((group) => (
        <div className="dash-nav-group" key={group.id}>
          <h4 className="dash-nav-group-title">{group.label}</h4>
          <div className="dash-nav-grid">
            {group.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`dash-nav-card is-${item.tone}`}
                onClick={() => onOpenModule(item.id)}
              >
                <span className="dash-nav-icon" aria-hidden="true"><span className={item.iconClass} /></span>
                <span className="dash-nav-body">
                  <strong>{item.label}</strong>
                  <em>{item.description}</em>
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function formatChineseDate() {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
}

function AppleWorkspaceHome({ greeting, onOpenModule }: { greeting: string; onOpenModule: (id: WorkspaceModuleId) => void }) {
  return (
    <div className="mac-home">
      <section className="mac-welcome-card">
        <div className="mac-welcome-brand">
          <span className="mac-brand-tile"><img src={BRAND_MARK_URL} alt="" /></span>
          <div><span>{formatChineseDate()}</span><h1>{greeting}，本地用户</h1><p>今天也从清晰、有序的工作台开始。</p></div>
        </div>
        <div className="mac-welcome-actions">
          <a className="mac-manual-link" href={BRAND_MANUAL_URL} target="_blank" rel="noopener noreferrer">使用手册 ↗</a>
          <button type="button" onClick={() => onOpenModule("daily_selection")}><span>＋</span> 新建采集</button>
        </div>
      </section>

      <DashboardStats onOpenModule={onOpenModule} variant="apple" />

      <section className="mac-section">
        <div className="mac-section-heading"><div><span>LAUNCHPAD</span><h2>全部功能</h2></div><small>按业务流程分组，与左侧导航同源</small></div>
        <div className="mac-launchpad">
          {navGroups.map((group) => (
            <div className="mac-launchpad-group" key={group.id}>
              <h3>{group.label}</h3>
              <div className="mac-launchpad-grid">
                {group.items.map((item) => (
                  <button type="button" key={item.id} onClick={() => onOpenModule(item.id)}>
                    <span className={`mac-app-icon is-${item.tone}`}><AppleAppGlyph name={item.id} /></span>
                    <strong>{item.label}</strong>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="mac-section mac-continue-section">
        <div className="mac-section-heading"><div><span>CONTINUE</span><h2>继续处理</h2></div></div>
        <div className="mac-continue-grid">
          <button type="button" onClick={() => onOpenModule("product_processing")}>
            <span className="mac-continue-icon is-purple"><AppleAppGlyph name="product_processing" /></span>
            <span><small>产品处理</small><strong>检查草稿与 AI 处理任务</strong><em>继续工作 →</em></span>
          </button>
          <button type="button" onClick={() => onOpenModule("profit_activity_products")}>
            <span className="mac-continue-icon is-blue"><AppleAppGlyph name="profit_activity_products" /></span>
            <span><small>货源产品库</small><strong>查看最近入库的产品</strong><em>打开产品库 →</em></span>
          </button>
        </div>
      </section>
    </div>
  );
}

export function WorkspaceHomePage({ onOpenModule }: WorkspaceHomePageProps) {
  const [greeting, setGreeting] = useState(() => getGreeting());
  const { uiMode } = useUiMode();

  useEffect(() => {
    setGreeting(getGreeting());
  }, []);

  if (uiMode === "apple") {
    return <AppleWorkspaceHome greeting={greeting} onOpenModule={onOpenModule} />;
  }

  return (
    <div className="page-stack dashboard-page">
      <span className="dashboard-meteor" aria-hidden="true" />
      <span className="dashboard-meteor is-two" aria-hidden="true" />
      <span className="dashboard-meteor is-three" aria-hidden="true" />
      <section className="page-hero-card">
        <img className="brand-logo-hero" src={BRAND_LOGO_URL} alt={BRAND_NAME} />
        <p className="eyebrow">JIEYE ECOMMERCE PLATFORM · 界野电商平台</p>
        <h1>{greeting}，准备开始今天的工作。</h1>
        <a className="dashboard-manual-link" href={BRAND_MANUAL_URL} target="_blank" rel="noopener noreferrer">使用手册 ↗</a>
      </section>
      <DashboardStats onOpenModule={onOpenModule} />
      <ClassicModuleNav onOpenModule={onOpenModule} />
    </div>
  );
}

import { useEffect, useState } from "react";
import { BRAND_LOGO_URL, BRAND_MARK_URL, BRAND_NAME } from "../../../shared/brand";
import type { WorkspaceModuleId } from "../../../app/navigation/modules";
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

type ShortcutItem =
  | { type: "module"; id: WorkspaceModuleId; label: string; text: string }
  | { type: "external"; label: string; text: string; url: string };

const shortcuts: ShortcutItem[] = [
  { type: "module", id: "daily_selection", label: "每日选品", text: "开始采集并查看候选商品" },
  { type: "module", id: "product_processing", label: "产品处理", text: "进入草稿池和处理任务" },
  { type: "module", id: "profit_activity", label: "利润活动", text: "查看利润核算和活动筛选" },
  { type: "external", label: "中转查看", text: "打开 AI 中转服务页面", url: "https://station-88.aicoming.top/" },
];

const launchpadItems: Array<{ id: WorkspaceModuleId; label: string; tone: string }> = [
  { id: "daily_selection", label: "每日选品", tone: "blue" },
  { id: "product_processing", label: "AI 产品处理", tone: "violet" },
  { id: "product_processing_history", label: "历史记录", tone: "slate" },
  { id: "dimension_canvas", label: "尺寸画布", tone: "cyan" },
  { id: "price_verification", label: "核价匹配", tone: "orange" },
  { id: "profit_activity", label: "利润活动", tone: "green" },
  { id: "profit_activity_products", label: "产品库", tone: "pink" },
  { id: "ai_service", label: "AI 服务", tone: "indigo" },
];

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
        <button type="button" onClick={() => onOpenModule("daily_selection")}><span>＋</span> 新建采集</button>
      </section>

      <DashboardStats onOpenModule={onOpenModule} variant="apple" />

      <section className="mac-section">
        <div className="mac-section-heading"><div><span>LAUNCHPAD</span><h2>应用</h2></div><small>常用工具集中在这里</small></div>
        <div className="mac-launchpad">
          {launchpadItems.map((item) => (
            <button type="button" key={item.id} onClick={() => onOpenModule(item.id)}>
              <span className={`mac-app-icon is-${item.tone}`}><AppleAppGlyph name={item.id} /></span>
              <strong>{item.label}</strong>
            </button>
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
      </section>
      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">QUICK START</p>
            <h2>快捷入口</h2>
          </div>
        </div>
        <div className="shortcut-grid">
          {shortcuts.map((item) =>
            item.type === "external" ? (
              <button
                className="shortcut-card"
                key={item.url}
                type="button"
                onClick={() => window.open(item.url, "_blank", "noopener,noreferrer")}
              >
                <strong>{item.label}</strong>
                <span>{item.text}</span>
                <b>打开 →</b>
              </button>
            ) : (
              <button className="shortcut-card" key={item.id} onClick={() => onOpenModule(item.id)}>
                <strong>{item.label}</strong>
                <span>{item.text}</span>
                <b>打开 →</b>
              </button>
            )
          )}
        </div>
      </section>
      <DashboardStats onOpenModule={onOpenModule} />
    </div>
  );
}

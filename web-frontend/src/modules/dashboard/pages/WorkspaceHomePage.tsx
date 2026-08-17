import { useEffect, useState } from "react";
import { BRAND_LOGO_URL, BRAND_NAME } from "../../../shared/brand";
import type { WorkspaceModuleId } from "../../../app/navigation/modules";
import { DashboardStats } from "../components/DashboardStats";

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

export function WorkspaceHomePage({ onOpenModule }: WorkspaceHomePageProps) {
  const [greeting, setGreeting] = useState(() => getGreeting());

  useEffect(() => {
    setGreeting(getGreeting());
  }, []);

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

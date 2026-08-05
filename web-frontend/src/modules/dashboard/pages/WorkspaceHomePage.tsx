import type { WorkspaceModuleId } from "../../../app/navigation/modules";

type WorkspaceHomePageProps = { onOpenModule: (id: WorkspaceModuleId) => void };

const shortcuts: Array<{ id: WorkspaceModuleId; label: string; text: string }> = [
  { id: "daily_selection", label: "每日选品", text: "开始采集并查看候选商品" },
  { id: "product_processing", label: "产品处理", text: "进入草稿池和处理任务" },
  { id: "profit_activity", label: "利润活动", text: "查看利润核算和活动筛选" },
];

export function WorkspaceHomePage({ onOpenModule }: WorkspaceHomePageProps) {
  return <div className="page-stack">
    <section className="page-hero-card"><p className="eyebrow">LOCAL WORKSPACE</p><h1>早上好，准备开始今天的工作。</h1><p>这是主界面框架。后续将在这里接入任务概览、运行状态和跨模块待办。</p></section>
    <section><div className="section-heading"><div><p className="eyebrow">QUICK START</p><h2>快捷入口</h2></div><span className="muted">演示框架</span></div><div className="shortcut-grid">{shortcuts.map((item) => <button className="shortcut-card" key={item.id} onClick={() => onOpenModule(item.id)}><strong>{item.label}</strong><span>{item.text}</span><b>打开 →</b></button>)}</div></section>
    <section className="empty-dashboard-card"><span>◌</span><div><h3>今日任务区域已预留</h3><p>待后端接口确定后，在对应模块中加载真实数据。</p></div></section>
  </div>;
}

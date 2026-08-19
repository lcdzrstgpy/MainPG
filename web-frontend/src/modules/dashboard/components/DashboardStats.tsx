import { useEffect, useState } from "react";
import type { WorkspaceModuleId } from "../../../app/navigation/modules";
import { getDashboardStats, type DashboardStats } from "../api/dashboardApi";
import "./../styles/dashboardStats.css";

type DashboardStatsProps = { onOpenModule: (id: WorkspaceModuleId) => void; variant?: "classic" | "apple" };

export function DashboardStats({ onOpenModule, variant = "classic" }: DashboardStatsProps) {
  const [stats, setStats] = useState<DashboardStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void getDashboardStats().then((data) => {
        if (!cancelled) setStats(data);
      });
    };
    load();
    // 数据概览定时刷新：产品处理任务/入库在别处进行时，这里能跟上变化
    const timer = window.setInterval(load, 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const format = (value: number | null | undefined) => (value === null || value === undefined ? "--" : String(value));

  if (variant === "apple") {
    return (
      <section className="mac-glance-section">
        <div className="mac-section-heading"><div><span>AT A GLANCE</span><h2>今日概览</h2></div><small>每分钟自动更新</small></div>
        <div className="mac-glance-grid">
          <button type="button" onClick={() => onOpenModule("profit_activity_products")}>
            <span className="mac-glance-icon is-blue"><i className="iconfont icon-folder" /></span>
            <span><small>产品总数</small><strong>{format(stats?.productCount)}</strong><em>全部市场</em></span>
          </button>
          <button type="button" onClick={() => onOpenModule("profit_activity_products")}>
            <span className="mac-glance-icon is-green"><i className="iconfont icon-download" /></span>
            <span><small>今日入库</small><strong>{format(stats?.todayInboundCount)}</strong><em>北京时间</em></span>
          </button>
          <button type="button" onClick={() => onOpenModule("product_processing")}>
            <span className="mac-glance-icon is-violet"><i className="iconfont icon-setting" /></span>
            <span><small>今日处理</small><strong>{format(stats?.todayProcessedCount)}</strong><em>AI 处理任务</em></span>
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="dashboard-stats-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW</p>
          <h2>数据概览</h2>
        </div>
        <span className="muted">实时统计</span>
      </div>
      <div className="stats-grid">
        <button className="stats-card" onClick={() => onOpenModule("profit_activity_products")} title="点击打开产品库">
          <span className="stats-icon iconfont icon-folder"></span>
          <span className="stats-label">产品库产品总数</span>
          <strong className="stats-value">{format(stats?.productCount)}</strong>
          <span className="stats-note">美区 / 哥伦比亚 / 厄瓜多尔</span>
        </button>

        <button className="stats-card" onClick={() => onOpenModule("profit_activity_products")} title="点击打开产品库">
          <span className="stats-icon iconfont icon-download"></span>
          <span className="stats-label">今日入库数量</span>
          <strong className="stats-value">{format(stats?.todayInboundCount)}</strong>
          <span className="stats-note">按北京时间统计</span>
        </button>

        <button className="stats-card" onClick={() => onOpenModule("product_processing")} title="点击打开产品处理">
          <span className="stats-icon iconfont icon-setting"></span>
          <span className="stats-label">今日产品处理数量</span>
          <strong className="stats-value">{format(stats?.todayProcessedCount)}</strong>
          <span className="stats-note">按北京时间统计</span>
        </button>
      </div>
    </section>
  );
}

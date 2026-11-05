import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { beijingTime, money, numberText, percent } from "../format";
import type { OperationOverview } from "../types";

export function OrderProfitOverviewTab({
  isActive,
  refreshToken,
  onNavigate,
}: {
  isActive: boolean;
  refreshToken: number;
  onNavigate: (view: string) => void;
}) {
  const [data, setData] = useState<OperationOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .overview()
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, refreshToken]);

  const overview = data;

  return (
    <section className="op-view">
      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-stat-grid">
        {(overview?.cards || []).map((card) => (
          <div className="op-stat" key={card.key}>
            <span>{card.label}</span>
            <strong className={card.tone || ""}>
              {card.key === "ad_cost" || card.key === "penalty" ? money(card.value) : numberText(card.value)}
            </strong>
          </div>
        ))}
        {loading && !overview ? <div className="op-alert">加载中…</div> : null}
      </div>

      <div className="op-overview-columns">
        <div className="op-card op-panel">
          <div className="op-panel-head">
            <strong>待办总览</strong>
            <span className="op-chip">按当前订单数据实时汇总</span>
          </div>
          {overview?.todos?.length ? (
            <div className="op-todo-list">
              {overview.todos.map((todo) => (
                <button
                  key={todo.key}
                  type="button"
                  className="op-todo"
                  onClick={() => onNavigate(todo.view)}
                >
                  <span className="op-todo-count">{numberText(todo.count)}</span>
                  <span className="op-todo-copy">
                    <strong>{todo.label}</strong>
                    <small>{todo.hint}</small>
                  </span>
                  <span className="op-todo-arrow">→</span>
                </button>
              ))}
            </div>
          ) : (
            <div className="op-empty op-empty-compact">
              <strong>暂无待办</strong>
              <span>采集订单并补齐金额后，待办会自动更新。</span>
            </div>
          )}
        </div>

        <div className="op-card op-panel">
          <div className="op-panel-head">
            <strong>数据源状态</strong>
          </div>
          <div className="op-source-list">
            {(overview?.data_sources || []).map((source) => (
              <div className="op-source-row" key={source.key}>
                <span>{source.label}</span>
                <span className={`op-chip ${source.status === "ready" ? "is-ok" : source.status === "empty" ? "is-warn" : ""}`}>
                  {source.status === "ready" ? "已接入" : source.status === "empty" ? "待采集" : "待接入"}
                </span>
              </div>
            ))}
          </div>
          {overview?.latest_capture ? (
            <p className="op-hint">
              最近采集：{beijingTime(overview.latest_capture.captured_at as string | undefined)} ·
              {String(overview.latest_capture.capture_mode || "") === "order_detail" ? " 订单详情" : " 订单列表"} ·
              {numberText(Number(overview.latest_capture.order_count) || 0)} 单
            </p>
          ) : null}
        </div>
      </div>

      {overview?.daily ? (
        <div className="op-card op-panel">
          <div className="op-panel-head">
            <strong>最新业务日预估</strong>
            <span className="op-chip">{overview.daily.date || "最新"}</span>
          </div>
          <div className="op-overview-daily">
            <div><span>GMV</span><strong>{money(overview.daily.gmv_total)}</strong></div>
            <div><span>预估利润</span><strong className={(overview.daily.profit || 0) >= 0 ? "is-positive" : "is-negative"}>{money(overview.daily.profit)}</strong></div>
            <div><span>预估利润率</span><strong>{percent(overview.daily.profit_rate)}</strong></div>
            <div><span>子订单</span><strong>{numberText(overview.daily.sub_order_count)}</strong></div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

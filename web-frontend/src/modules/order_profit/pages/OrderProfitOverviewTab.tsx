import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { beijingTime, money, numberText } from "../format";
import type { OperationOverview } from "../types";
import { EmptyBlock, Metric, MetricBand, SectionCard } from "../components/ShopDataPrimitives";

type OverviewTarget = "home" | "traffic" | "sales" | "orders";

const SOURCE_STATUS: Record<string, { label: string; tone: string }> = {
  ready: { label: "已接入", tone: "is-ok" },
  empty: { label: "待采集", tone: "is-warn" },
  pending: { label: "待接入", tone: "" },
};

const ENTRIES: Array<{ key: OverviewTarget; title: string; desc: string; tone: string }> = [
  { key: "home", title: "店铺首页", desc: "待发货、在售商品与近 30 日销量，先看整体水位。", tone: "is-mint" },
  { key: "traffic", title: "流量分析", desc: "曝光、点击、访客与渠道构成，判断流量从哪来。", tone: "is-blue" },
  { key: "sales", title: "销量分析", desc: "销量、售罄与库存健康，判断货卖得动不动。", tone: "is-amber" },
];

/**
 * 经营概览的落地页：先回答「现在怎么样、今天先做什么」，
 * 再让用户按需进入店铺首页 / 流量 / 销量三个专题，避免一进页面就堆满图表。
 */
export function OrderProfitOverviewTab({
  isActive,
  refreshToken,
  shop,
  onNavigate,
  onOpenOrders,
  onOpenCapture,
}: {
  isActive: boolean;
  refreshToken: number;
  shop: string;
  onNavigate: (target: OverviewTarget) => void;
  onOpenOrders?: () => void;
  onOpenCapture?: () => void;
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

  const cards = data?.cards || [];
  const card = (key: string) => cards.find((item) => item.key === key)?.value ?? 0;
  const daily = data?.daily;
  const todos = data?.todos || [];
  const sources = data?.data_sources || [];
  const readyCount = sources.filter((source) => source.status === "ready").length;

  const openTodo = (view: string) => {
    if (view === "traffic" || view === "sales" || view === "home") {
      onNavigate(view);
      return;
    }
    onOpenOrders?.();
  };

  return (
    <section className="op-view">
      {error ? <div className="op-alert is-error">{error}</div> : null}

      <MetricBand>
        <Metric
          label="订单总数"
          value={loading && !data ? "…" : numberText(card("orders"))}
          hint={`${numberText(card("unreceived"))} 单未接收`}
          glyph="orders"
        />
        <Metric
          label="待发货"
          value={numberText(card("pending_shipment"))}
          tone={card("pending_shipment") > 0 ? "is-warning" : ""}
          hint="来自店铺首页指标"
          glyph="truck"
        />
        <Metric
          label="近 30 日销量"
          value={numberText(card("sales_30d"))}
          hint={`在售商品 ${numberText(card("active_products"))}`}
          glyph="box"
        />
        <Metric
          label="预估利润"
          value={daily ? money(daily.profit) : "—"}
          tone={daily ? ((daily.profit || 0) >= 0 ? "is-positive" : "is-negative") : ""}
          hint={daily?.date ? `业务日 ${daily.date}` : "采集后按最新业务日估算"}
          glyph="profit"
        />
      </MetricBand>

      <div className="op-overview-columns">
        <SectionCard
          title="今天该做什么"
          desc="按当前订单与成本数据自动汇总，点一条直接去处理。"
          meta={<span className="op-chip">{todos.length ? `${todos.length} 项待办` : "暂无待办"}</span>}
          scene="todo"
        >
          {todos.length ? (
            <div className="op-todo-list">
              {todos.map((todo) => (
                <button
                  key={todo.key}
                  type="button"
                  className="op-todo"
                  onClick={() => openTodo(todo.view)}
                >
                  <span className="op-todo-count">{numberText(todo.count)}</span>
                  <span className="op-todo-copy">
                    <strong>{todo.label}</strong>
                    <small>{todo.hint}</small>
                  </span>
                  <span className="op-todo-arrow" aria-hidden="true">→</span>
                </button>
              ))}
            </div>
          ) : (
            <EmptyBlock
              compact
              title="今天没有待办"
              desc="订单金额与采购成本都齐了，采集新数据后这里会自动更新。"
            />
          )}
        </SectionCard>

        <SectionCard
          title="数据接入"
          desc="只显示能否用于看板；缺哪一项就补哪一项。"
          meta={
            <span className={`op-chip ${readyCount === sources.length ? "is-ok" : "is-warn"}`}>
              {loading && !data ? "读取中…" : `${readyCount}/${sources.length} 已就绪`}
            </span>
          }
          scene="sources"
        >
          <div className="op-source-grid">
            {sources.map((source) => {
              const status = SOURCE_STATUS[source.status] || SOURCE_STATUS.pending;
              return (
                <div className="op-source-cell" key={source.key}>
                  <span>{source.label}</span>
                  <em className={status.tone}>{status.label}</em>
                </div>
              );
            })}
          </div>
          {data?.latest_capture ? (
            <p className="op-hint">
              最近采集：{beijingTime(data.latest_capture.captured_at as string | undefined)}
              {" · "}
              {String(data.latest_capture.capture_mode || "") === "order_detail" ? "订单详情" : "订单列表"}
              {" · "}
              {numberText(Number(data.latest_capture.order_count) || 0)} 单
            </p>
          ) : (
            <p className="op-hint">还没有采集记录。先在「店铺账号」绑定店铺，再到「数据采集」同步一次。</p>
          )}
          {onOpenCapture ? (
            <button type="button" className="op-btn op-btn-primary" onClick={onOpenCapture}>
              去数据采集
            </button>
          ) : null}
        </SectionCard>
      </div>

      <SectionCard
        title="继续查看"
        desc="总览只看结论；具体原因分到三个专题页，进哪一页由你现在的问题决定。"
        meta={<span className="op-chip">当前店铺：{shop === "all" ? "全部店铺" : shop || "跟随工作台"}</span>}
        scene="explore"
      >
        <div className="op-entry-grid op-entry-grid-row">
          {ENTRIES.map((entry) => (
            <button
              key={entry.key}
              type="button"
              className={`op-jump ${entry.tone}`}
              onClick={() => onNavigate(entry.key)}
            >
              <span className="op-jump-index">{"一二三"[ENTRIES.indexOf(entry)]}</span>
              <span className="op-jump-copy">
                <strong>{entry.title}</strong>
                <small>{entry.desc}</small>
              </span>
              <span className="op-jump-arrow" aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      </SectionCard>
    </section>
  );
}

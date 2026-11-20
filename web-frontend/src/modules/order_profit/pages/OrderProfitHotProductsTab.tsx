import { useCallback, useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { MonthMultiSelect } from "../components/ChartControls";
import { money, numberText } from "../format";
import type { OrderProfitRankingResponse } from "../types";

/**
 * 爆品统计：按月份范围，从订单里聚合同一商品链接（SKC）或 SKU 的销量排行。
 *
 * 数据完全来自已经采集入库的订单行，不再单独调 Temu 的榜单接口；因此榜单的覆盖
 * 范围取决于订单采集到了哪些月份，页面上会提示当前月份是否有数据。
 */
export function OrderProfitHotProductsTab({
  isActive,
  refreshToken = 0,
  shop = "",
}: {
  isActive: boolean;
  refreshToken?: number;
  shop?: string;
}) {
  const [months, setMonths] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState<"link" | "sku">("link");
  const [metric, setMetric] = useState<"quantity" | "payback">("quantity");
  const [data, setData] = useState<OrderProfitRankingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 可选月份：与订单页共用同一份数据，默认看最近一个有订单的月份。
  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    orderProfitApi
      .ordersMonths(shop)
      .then((payload) => {
        if (cancelled) return;
        const list = payload.months || [];
        setMonths(list);
        setSelected((current) => {
          if (!list.length) return [];
          const kept = current.filter((month) => list.includes(month));
          return kept.length ? kept : list.slice(0, 1);
        });
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, shop, refreshToken]);

  const metricKey = metric;
  const groupKey = groupBy;
  const selectedKey = selected.join(",");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const payload = await orderProfitApi.ordersRanking({
        months: selectedKey ? selectedKey.split(",") : [],
        shop,
        groupBy: groupKey,
        metric: metricKey,
        limit: 50,
      });
      setData(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [selectedKey, shop, groupKey, metricKey]);

  useEffect(() => {
    if (!isActive) return;
    void load();
  }, [isActive, load, refreshToken]);

  const rows = data?.rows || [];
  const maxValue = rows.reduce(
    (max, row) => Math.max(max, metric === "payback" ? Number(row.payback || 0) : Number(row.quantity || 0)),
    1,
  );
  const rankTitle = groupBy === "link" ? "商品链接榜" : "SKU 榜";

  return (
    <div className="op-view">
      <div className="op-view-toolbar">
        <div className="op-seg" role="tablist" aria-label="归并维度">
          <button
            type="button"
            role="tab"
            aria-selected={groupBy === "link"}
            className={`op-seg-item ${groupBy === "link" ? "is-active" : ""}`}
            onClick={() => setGroupBy("link")}
          >
            按商品链接
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={groupBy === "sku"}
            className={`op-seg-item ${groupBy === "sku" ? "is-active" : ""}`}
            onClick={() => setGroupBy("sku")}
          >
            按 SKU
          </button>
        </div>
        <div className="op-seg" role="tablist" aria-label="排序依据">
          <button
            type="button"
            role="tab"
            aria-selected={metric === "quantity"}
            className={`op-seg-item ${metric === "quantity" ? "is-active" : ""}`}
            onClick={() => setMetric("quantity")}
          >
            按销量件数
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={metric === "payback"}
            className={`op-seg-item ${metric === "payback" ? "is-active" : ""}`}
            onClick={() => setMetric("payback")}
          >
            按销售回款
          </button>
        </div>
        <span className="op-view-toolbar-meta">
          {loading
            ? "加载中…"
            : `共 ${numberText(rows.length)} 个${groupBy === "link" ? "链接" : " SKU"} · ${numberText(data?.total_quantity ?? 0)} 件`}
        </span>
      </div>

      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <div className="op-field">
            <span>统计月份（可多选）</span>
            <MonthMultiSelect
              months={months}
              value={selected}
              onChange={setSelected}
              placeholder="选择月份"
            />
          </div>
          <div className="op-hint op-hot-tip">
            只会统计已采集入库的订单；跨月比较建议同时选中多个月份。
          </div>
        </div>
      </div>

      {error ? (
        <div className="op-empty">
          <strong>爆品榜加载失败</strong>
          <p>{error}</p>
        </div>
      ) : null}

      {!error && !loading && !rows.length ? (
        <div className="op-empty">
          <strong>这个月份还没有可排行的订单</strong>
          <p>先在「数据采集」里按月份采集订单，再回来看销量排行。</p>
        </div>
      ) : null}

      {rows.length ? (
        <div className="op-card op-hot-card">
          <div className="op-hot-head">
            <strong>{rankTitle}</strong>
            <span className="op-chip">
              {metric === "quantity" ? "按销量件数" : "按销售回款"}
            </span>
            <span className="op-view-toolbar-meta">
              回款合计 {data?.total_payback == null ? "—" : money(data.total_payback)}
            </span>
          </div>
          <ol className="op-hot-list">
            {rows.map((row, index) => {
              const value = metric === "payback" ? Number(row.payback || 0) : Number(row.quantity || 0);
              const width = Math.max(4, Math.round((value / maxValue) * 100));
              return (
                <li key={`${row.key}-${index}`} className="op-hot-row">
                  <span className={`op-hot-rank ${index < 3 ? "is-top" : ""}`}>{index + 1}</span>
                  {row.image_url ? (
                    <img className="op-hot-thumb" src={row.image_url} alt="" loading="lazy" />
                  ) : (
                    <span className="op-hot-thumb is-empty" aria-hidden="true" />
                  )}
                  <div className="op-hot-main">
                    <strong>{row.goods_name || row.key || "未命名商品"}</strong>
                    <small>
                      {groupBy === "link"
                        ? `SKC ${row.skc_id || row.skc_code || "—"}`
                        : `SKU ${row.sku_id || "—"}`}
                      {` · ${numberText(row.order_count)} 单`}
                    </small>
                    <div className="op-hot-bar">
                      <i style={{ width: `${width}%` }} />
                    </div>
                  </div>
                  <div className="op-hot-nums">
                    <strong>{numberText(row.quantity)} 件</strong>
                    <small>
                      回款 {row.payback == null ? "—" : money(row.payback)}
                      {" · "}
                      利润 {row.profit == null ? "—" : money(row.profit)}
                    </small>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

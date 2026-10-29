import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { OrderTrendPanel } from "../components/OrderTrendPanel";
import { numberText } from "../format";
import type { StoreMetrics } from "../types";

export function OrderProfitStoreDataTab({
  isActive,
  refreshToken,
  shop = "",
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
}) {
  const [metrics, setMetrics] = useState<StoreMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .storeMetrics(shop)
      .then((payload) => {
        if (!cancelled) setMetrics(payload?.metrics || null);
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
  }, [isActive, refreshToken, shop]);

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <p className="op-hint">
          在 Temu Seller Central 首页用插件弹窗「采集店铺首页」同步待发货、在售商品与近 30 日销量；已采集的订单会在这里汇总成经营趋势。
        </p>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      {/* 经营趋势放在最上面：进页先看两张走势图，指标只留三项核心数字。 */}
      <OrderTrendPanel isActive={isActive} shop={shop} refreshToken={refreshToken} />

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>店铺首页指标</strong>
          <span>{metrics?.shop_name || "未采集"}</span>
          <small>{loading ? "加载中…" : metrics?.captured_at ? `采集于 ${metrics.captured_at}` : "尚未采集"}</small>
        </div>
        {loading && !metrics ? (
          <div className="op-stats-grid">
            {Array.from({ length: 3 }).map((_, index) => (
              <div className="op-skeleton op-skeleton-stat" key={index} />
            ))}
          </div>
        ) : (
          <div className="op-stats-grid">
            <div className="op-stat"><span>待发货</span><strong>{numberText(metrics?.pending_shipment)}</strong></div>
            <div className="op-stat"><span>在售商品</span><strong>{numberText(metrics?.active_products)}</strong></div>
            <div className="op-stat"><span>近 30 日销量</span><strong>{numberText(metrics?.sales_30d)}</strong></div>
          </div>
        )}
      </div>
    </section>
  );
}

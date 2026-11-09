import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { EmptyBlock, Metric, MetricBand } from "../components/ShopDataPrimitives";
import { OrderTrendPanel } from "../components/OrderTrendPanel";
import { numberText } from "../format";
import type { StoreMetrics } from "../types";

export function OrderProfitStoreDataTab({
  isActive,
  refreshToken,
  shop = "",
  onOpenCapture,
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
  onOpenCapture?: () => void;
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
      {error ? <div className="op-alert is-error">{error}</div> : null}

      {loading && !metrics ? (
        <MetricBand>
          {Array.from({ length: 3 }).map((_, index) => (
            <div className="op-skeleton op-skeleton-stat" key={index} />
          ))}
        </MetricBand>
      ) : metrics ? (
        <>
          <MetricBand>
            <Metric
              label="待发货"
              value={numberText(metrics.pending_shipment)}
              tone={Number(metrics.pending_shipment) > 0 ? "is-warning" : ""}
              hint="看是否需要尽快处理发货"
              glyph="truck"
            />
            <Metric label="在售商品" value={numberText(metrics.active_products)} hint="店铺当前可售的商品数" glyph="box" />
            <Metric label="近 30 日销量" value={numberText(metrics.sales_30d)} hint="判断近期动销水位" glyph="chart" />
            <Metric
              label="价格申报中"
              value={numberText(metrics.listing_price_pending ?? 0)}
              hint="首页「商品上新状态」：等待价格审核"
            />
            <Metric
              label="未发布到站点"
              value={numberText(metrics.listing_not_published ?? 0)}
              hint="已建品但还没发布到站点"
            />
            <Metric
              label="已发布到站点"
              value={numberText(metrics.listing_published ?? 0)}
              hint="已经成功发布上架"
            />
            <Metric
              label="已下架 / 终止"
              value={numberText(metrics.listing_delisted ?? 0)}
              hint="已下架或终止的商品"
            />
          </MetricBand>
          <p className="op-freshness">
            {metrics.shop_name || "当前店铺"}
            {metrics.captured_at ? ` · 采集于 ${metrics.captured_at}` : ""}
            {onOpenCapture ? (
              <button type="button" className="op-link" onClick={onOpenCapture}>
                重新采集
              </button>
            ) : null}
          </p>
        </>
      ) : (
        <EmptyBlock
          art="overview"
          title="还没有店铺首页数据"
          desc="在 Temu Seller Central 首页用插件采集一次，待发货、在售商品和销量会自动汇总到这里。"
          action={
            onOpenCapture ? (
              <button type="button" className="op-btn op-btn-primary" onClick={onOpenCapture}>
                去数据采集
              </button>
            ) : null
          }
        />
      )}

      {/* 经营趋势放在指标之后：先看水位，再看走势。 */}
      <OrderTrendPanel isActive={isActive} shop={shop} refreshToken={refreshToken} />

      <p className="op-footnote">
        首页指标来自店铺首页采集；趋势由已入库订单按月汇总，订单越多曲线越准确。
      </p>
    </section>
  );
}

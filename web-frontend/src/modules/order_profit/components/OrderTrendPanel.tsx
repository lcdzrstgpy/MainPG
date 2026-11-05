import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { money, numberText } from "../format";
import type { OrderTrendPoint, OrderTrendResponse } from "../types";
import { ChartKindSwitch, MonthMultiSelect } from "./ChartControls";
import { TrendChart, type ChartKind, type TrendSeries } from "./TrendChart";

const STATUS_SERIES: Array<{ key: string; label: string; color: string; field: keyof OrderTrendPoint }> = [
  { key: "all", label: "全部", color: "#2f6fed", field: "order_count" },
  { key: "pending", label: "待发货", color: "#f59e0b", field: "pending" },
  { key: "shipped", label: "已发货", color: "#0ea5e9", field: "shipped" },
  { key: "signed", label: "已签收", color: "#10b981", field: "signed" },
  { key: "cancelled", label: "已取消", color: "#ef4444", field: "cancelled" },
  { key: "other", label: "其他状态", color: "#94a3b8", field: "other" },
];

const AMOUNT_SERIES: Array<{ key: string; label: string; color: string; field: keyof OrderTrendPoint }> = [
  { key: "gmv", label: "GMV", color: "#6366f1", field: "gmv_total" },
  { key: "payback", label: "销售回款", color: "#10b981", field: "sales_payback" },
  { key: "profit", label: "总利润", color: "#f59e0b", field: "profit" },
];

function monthText(month: string): string {
  const [year, rest] = String(month || "").split("-");
  if (!rest) return month;
  return `${year.slice(2)}年${Number(rest)}月`;
}

/**
 * 经营趋势：订单状态数量 + 金额变化。
 * 两张图上下排布、各占整行，避免挤在一起。
 */
export function OrderTrendPanel({
  isActive,
  shop = "",
  refreshToken,
}: {
  isActive: boolean;
  shop?: string;
  refreshToken: number;
}) {
  const [months, setMonths] = useState<string[]>([]);
  const [kind, setKind] = useState<ChartKind>("spline");
  const [data, setData] = useState<OrderTrendResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // 切换店铺后回到该店默认区间，避免沿用上一个店铺选中的月份。
  useEffect(() => {
    setMonths([]);
  }, [shop]);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    orderProfitApi
      .ordersTrend({ months, shop })
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch(() => {
        if (!cancelled) setData(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, months, shop, refreshToken]);

  const points = data?.series || [];
  const labels = points.map((point) => monthText(point.month));
  const statusSource = kind === "stacked" ? STATUS_SERIES.slice(1) : STATUS_SERIES;
  const statusSeries: TrendSeries[] = statusSource.map((item) => ({
    key: item.key,
    label: item.label,
    color: item.color,
    values: points.map((point) => Number(point[item.field] || 0)),
  }));
  const amountSeries: TrendSeries[] = AMOUNT_SERIES.map((item) => ({
    key: item.key,
    label: item.label,
    color: item.color,
    values: points.map((point) => Number(point[item.field] || 0)),
  }));

  return (
    <div className="op-card op-chart-card">
      <div className="op-table-head">
        <strong>经营趋势</strong>
        <span>{labels.length ? labels.join(" · ") : "所选月份暂无数据"}</span>
        <MonthMultiSelect
          months={data?.available_months || []}
          value={months.length ? months : data?.months || []}
          onChange={setMonths}
        />
        <ChartKindSwitch value={kind} onChange={setKind} />
      </div>
      <div className="op-chart-stack">
        <div className="op-chart-block">
          <div className="op-chart-title">
            <strong>订单状态数量</strong>
            <small>按月统计各状态订单数，悬停查看明细</small>
          </div>
          {loading ? (
            <div className="op-skeleton op-skeleton-chart-lg" />
          ) : (
            <TrendChart
              labels={labels}
              series={statusSeries}
              kind={kind}
              height={300}
              formatValue={(value) => `${numberText(value)} 单`}
              emptyText="还没有订单数据，去「数据采集」采集一次"
            />
          )}
        </div>
        <div className="op-chart-block">
          <div className="op-chart-title">
            <strong>金额变化</strong>
            <small>GMV / 销售回款 / 总利润（人民币）</small>
          </div>
          {loading ? (
            <div className="op-skeleton op-skeleton-chart-lg" />
          ) : (
            <TrendChart
              labels={labels}
              series={amountSeries}
              kind={kind}
              height={300}
              formatValue={(value) => money(value)}
              emptyText="还没有金额数据，去「数据采集」采集一次"
            />
          )}
        </div>
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { money } from "../format";

type OperationKind = "links" | "after_sales" | "appeals" | "traffic";

const META: Record<OperationKind, { title: string; columns: string[]; hint: string }> = {
  links: {
    title: "链接变动",
    columns: ["商品 / 货号", "变动类型", "原链接", "新链接", "检测时间", "状态"],
    hint: "监控 Temu 商品改价、改标题、换主图；后续接入商品接口后自动写入。",
  },
  after_sales: {
    title: "售后分类",
    columns: ["父订单 / 子订单", "SKU", "售后类型", "原因", "金额", "发生时间", "状态"],
    hint: "按质量问题、物流问题、买家原因等分类统计售后；后续接入售后接口。",
  },
  appeals: {
    title: "申诉时效",
    columns: ["父订单 / 子订单", "申诉类型", "截止时间", "状态", "处理结果"],
    hint: "跟踪平台罚款/售后申诉的截止时间；后续接入申诉接口。",
  },
  traffic: {
    title: "流量周报",
    columns: ["周开始", "站点", "访客", "浏览量", "订单", "GMV", "转化率"],
    hint: "按周/站点汇总访客、订单、GMV 和转化率；后续接入流量接口。",
  },
};

export function OrderProfitOperationStubTab({
  kind,
  isActive,
  refreshToken,
}: {
  kind: OperationKind;
  isActive: boolean;
  refreshToken: number;
}) {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [message, setMessage] = useState("");
  const [dataSource, setDataSource] = useState("pending");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    const request =
      kind === "links" ? orderProfitApi.linkChanges()
        : kind === "after_sales" ? orderProfitApi.afterSales()
          : kind === "appeals" ? orderProfitApi.appeals()
            : orderProfitApi.trafficWeekly();
    request
      .then((payload) => {
        if (cancelled) return;
        setItems((payload.items || []) as unknown as Array<Record<string, unknown>>);
        setMessage(payload.message || "");
        setDataSource(payload.data_source || "pending");
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
  }, [isActive, kind, refreshToken]);

  const meta = META[kind];

  return (
    <section className="op-view">
      <div className="op-card op-panel">
        <div className="op-panel-head">
          <strong>{meta.title}</strong>
          <span className={`op-chip ${dataSource === "ready" ? "is-ok" : "is-warn"}`}>
            {dataSource === "ready" ? "已接入" : "待接入数据源"}
          </span>
        </div>
        <p className="op-hint">{meta.hint}</p>
        {message ? <div className="op-alert">{message}</div> : null}
        {error ? <div className="op-alert is-error">{error}</div> : null}
      </div>

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>{meta.title}明细</strong>
          <span>{loading ? "加载中…" : `${items.length} 条`}</span>
          <small>表结构和接口已就绪，接入采集源后自动展示</small>
        </div>
        <div className="op-table-wrap">
          <table className="op-table">
            <thead>
              <tr>
                {meta.columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!items.length && !loading ? (
                <tr>
                  <td colSpan={meta.columns.length}>
                    <div className="op-empty">
                      <strong>暂无{meta.title}数据</strong>
                      <span>{meta.hint}</span>
                    </div>
                  </td>
                </tr>
              ) : null}
              {items.map((row, index) => (
                <tr key={String(row.id || index)}>
                  {kind === "links" ? (
                    <>
                      <td><span className="op-mono">{String(row.product_key || "—")}</span><small className="op-block">{String(row.product_title || "")}</small></td>
                      <td>{String(row.change_type || "—")}</td>
                      <td className="op-title" title={String(row.old_url || "")}>{String(row.old_url || "—")}</td>
                      <td className="op-title" title={String(row.new_url || "")}>{String(row.new_url || "—")}</td>
                      <td>{String(row.detected_at || "—")}</td>
                      <td><span className="op-badge">{String(row.status || "—")}</span></td>
                    </>
                  ) : null}
                  {kind === "after_sales" ? (
                    <>
                      <td><span className="op-mono">{String(row.parent_order_sn || "—")}</span><small className="op-block op-mono">{String(row.sub_order_sn || "")}</small></td>
                      <td className="op-mono">{String(row.sku_id || "—")}</td>
                      <td>{String(row.type || "—")}</td>
                      <td>{String(row.reason || "—")}</td>
                      <td className="op-num">{money(Number(row.amount))}</td>
                      <td>{String(row.occurred_at || "—")}</td>
                      <td><span className="op-badge">{String(row.status || "—")}</span></td>
                    </>
                  ) : null}
                  {kind === "appeals" ? (
                    <>
                      <td><span className="op-mono">{String(row.parent_order_sn || "—")}</span><small className="op-block op-mono">{String(row.sub_order_sn || "")}</small></td>
                      <td>{String(row.appeal_type || "—")}</td>
                      <td>{String(row.deadline || "—")}</td>
                      <td><span className="op-badge">{String(row.status || "—")}</span></td>
                      <td>{String(row.result || "—")}</td>
                    </>
                  ) : null}
                  {kind === "traffic" ? (
                    <>
                      <td>{String(row.week_start || "—")}</td>
                      <td>{String(row.site || "—")}</td>
                      <td className="op-num">{String(row.visitors ?? "—")}</td>
                      <td className="op-num">{String(row.page_views ?? "—")}</td>
                      <td className="op-num">{String(row.orders ?? "—")}</td>
                      <td className="op-num">{money(Number(row.gmv))}</td>
                      <td className="op-num">{row.conversion_rate == null ? "—" : `${(Number(row.conversion_rate) * 100).toFixed(2)}%`}</td>
                    </>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

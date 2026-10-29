import { useEffect, useMemo, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { amountTone, money, numberText, percent } from "../format";
import type { OrderProfitDailyResponse, OrderProfitStore } from "../types";

function defaultFilters() {
  return { date: "", store: "", keyword: "", headUnitPrice: "" };
}

export function OrderProfitDailyTab({
  isActive,
  refreshToken,
}: {
  isActive: boolean;
  refreshToken: number;
}) {
  const [draft, setDraft] = useState(defaultFilters);
  const [applied, setApplied] = useState(defaultFilters);
  const [stores, setStores] = useState<OrderProfitStore[]>([]);
  const [data, setData] = useState<OrderProfitDailyResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    orderProfitApi
      .listStores()
      .then((payload) => {
        if (!cancelled) setStores(payload.stores || []);
      })
      .catch(() => {
        if (!cancelled) setStores([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, refreshToken]);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    const head = applied.headUnitPrice.trim() === "" ? undefined : Number(applied.headUnitPrice);
    orderProfitApi
      .daily({
        date: applied.date,
        store: applied.store,
        keyword: applied.keyword,
        headUnitPrice: head !== undefined && Number.isFinite(head) ? head : undefined,
      })
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
  }, [isActive, applied, refreshToken]);

  const summary = data?.summary;
  const items = useMemo(() => {
    const list = data?.items || [];
    if (!onlyMissing) return list;
    return list.filter((item) =>
      !item.missing?.purchase_cost
      || !item.missing?.weight
      || !item.missing?.declared_price
      || !item.missing?.gmv,
    );
  }, [data, onlyMissing]);

  const cards = [
    { label: "订单数", value: numberText(summary?.order_count), tone: "" },
    { label: "子订单数", value: numberText(summary?.sub_order_count), tone: "" },
    { label: "总件数", value: numberText(summary?.item_count), tone: "" },
    { label: "GMV (申报价×件数)", value: money(summary?.gmv_total), tone: "is-positive" },
    { label: "采购成本", value: money(summary?.purchase_cost), tone: "" },
    {
      label: "头程运费",
      value: (summary?.head_unit_price_cny || 0) > 0 ? money(summary?.head_freight) : "待填单价",
      tone: (summary?.head_unit_price_cny || 0) > 0 ? "" : "is-warning",
    },
    { label: "尾程运费", value: money(summary?.tail_freight), tone: "is-info" },
    {
      label: "预估利润",
      value: money(summary?.profit),
      tone: (summary?.profit || 0) >= 0 ? "is-info" : "is-negative",
    },
    {
      label: "预估利润率",
      value: percent(summary?.profit_rate),
      tone: (summary?.profit || 0) >= 0 ? "is-info" : "is-negative",
    },
  ];

  const query = () => setApplied({ ...draft });
  const reset = () => {
    const next = defaultFilters();
    setDraft(next);
    setApplied(next);
    setOnlyMissing(false);
  };

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <label className="op-field">
            <span>业务日期（留空=最新）</span>
            <input
              type="date"
              value={draft.date}
              onChange={(event) => setDraft({ ...draft, date: event.target.value })}
            />
          </label>
          <label className="op-field op-field-grow">
            <span>店铺</span>
            <select value={draft.store} onChange={(event) => setDraft({ ...draft, store: event.target.value })}>
              <option value="">全部店铺</option>
              {stores.map((store) => (
                <option key={`${store.id}-${store.name}`} value={store.id || store.name}>
                  {store.name}
                </option>
              ))}
            </select>
          </label>
          <label className="op-field op-field-wide">
            <span>关键词</span>
            <input
              type="text"
              value={draft.keyword}
              placeholder="订单号 / SKU / SKC / 商品名"
              onChange={(event) => setDraft({ ...draft, keyword: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter") query();
              }}
            />
          </label>
          <label className="op-field op-field-slim">
            <span>头程单价</span>
            <div className="op-input-suffix">
              <input
                type="number"
                min={0}
                step={0.5}
                value={draft.headUnitPrice}
                placeholder="如 12"
                onChange={(event) => setDraft({ ...draft, headUnitPrice: event.target.value })}
              />
              <span>元/公斤</span>
            </div>
          </label>
          <label className="op-check">
            <input
              type="checkbox"
              checked={onlyMissing}
              onChange={(event) => setOnlyMissing(event.target.checked)}
            />
            <span>只看未维护</span>
          </label>
          <div className="op-filter-actions">
            <button type="button" className="op-btn op-btn-primary" onClick={query}>
              查询
            </button>
            <button type="button" className="op-btn" onClick={reset}>
              重置
            </button>
          </div>
        </div>
      </div>

      <div className="op-warn-list">
        {(data?.warnings || []).map((warning) => (
          <button
            key={`${warning.type}-${warning.message}`}
            type="button"
            className="op-warn"
            onClick={() => setOnlyMissing(true)}
          >
            <span className="op-warn-icon">!</span>
            <span>
              <strong>{warning.message}</strong>
              <small>
                {warning.count} 个子订单 / {warning.sku_count} 个 SKU；补齐后再看利润。
              </small>
            </span>
            <em>只看未维护</em>
          </button>
        ))}
      </div>

      <div className="op-stats-grid op-stats-nine">
        {cards.map((card) => (
          <div className="op-stat" key={card.label}>
            <span>{card.label}</span>
            <strong className={card.tone}>{card.value}</strong>
          </div>
        ))}
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>子订单明细</strong>
          <span>{loading ? "加载中…" : `${summary?.date || "最新"} · ${items.length} 行`}</span>
          <small>待维护行不参与真实利润判断；尾程按汇率从 USD 换算为 CNY</small>
        </div>
        <div className="op-table-wrap">
          <table className="op-table op-table-daily">
            <thead>
              <tr>
                <th>店铺</th>
                <th>父订单 / 子订单</th>
                <th>SKC</th>
                <th>SKU / 货号</th>
                <th>商品名称</th>
                <th className="op-num">件数</th>
                <th className="op-num">申报价</th>
                <th className="op-num">GMV(申报价×件数)</th>
                <th className="op-num">尾程</th>
                <th className="op-num">采购成本</th>
                <th className="op-num">头程</th>
                <th className="op-num">预估利润</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {!items.length && !loading ? (
                <tr>
                  <td colSpan={13}>
                    <div className="op-empty">
                      <strong>该日期还没有可展示的子订单</strong>
                      <span>换一个业务日期，或去「数据采集」采集订单列表和详情页。</span>
                    </div>
                  </td>
                </tr>
              ) : null}
              {items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <div className="op-store">
                      <span className="op-store-name">{item.shop_name || item.mall_id || "—"}</span>
                      {item.mall_id && item.shop_name ? <small>{item.mall_id}</small> : null}
                    </div>
                  </td>
                  <td>
                    <span className="op-mono">{item.parent_order_sn || "—"}</span>
                    <small className="op-block op-mono">{item.sub_order_sn || "—"}</small>
                  </td>
                  <td className="op-mono">{item.skc_id || item.skc_code || "—"}</td>
                  <td>
                    <span className="op-mono">{item.sku_id || "—"}</span>
                    {item.ext_code ? <small className="op-block">{item.ext_code}</small> : null}
                  </td>
                  <td className="op-title" title={item.goods_name}>{item.goods_name || "—"}</td>
                  <td className="op-num">{item.quantity}</td>
                  <td className="op-num">
                    {item.missing?.declared_price ? <span className="op-chip is-warn">待补</span> : money(item.declared_price)}
                  </td>
                  <td className="op-num">
                    {item.missing?.gmv ? <span className="op-chip is-warn">待补</span> : money(item.gmv_estimate_cny ?? item.gmv_cny)}
                  </td>
                  <td className="op-num">
                    {item.missing?.tail_freight ? <span className="op-chip is-warn">待补</span> : money(item.tail_freight_cny)}
                  </td>
                  <td className="op-num">
                    {item.missing?.purchase_cost ? (
                      <span className="op-chip is-warn">待维护</span>
                    ) : (
                      money(item.purchase_cost_cny)
                    )}
                  </td>
                  <td className="op-num">
                    {item.missing?.weight ? (
                      <span className="op-chip is-warn">待维护</span>
                    ) : (
                      money(item.head_freight_cny)
                    )}
                  </td>
                  <td className={`op-num ${amountTone(item.profit_estimate_cny)}`}>
                    {money(item.profit_estimate_cny)}
                  </td>
                  <td>
                    <span className="op-badge">{item.order_status || "—"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

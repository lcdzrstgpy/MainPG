import { useEffect, useMemo, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import {
  bridgeApi,
  type BridgeBinding,
  type BridgeItem,
} from "../api/bridgeApi";
import {
  EmptyBlock,
  Metric,
  MetricBand,
  PageHero,
  SectionCard,
} from "../components/ShopDataPrimitives";
import { beijingTime, money, numberText } from "../format";
import "../styles/orderProfit.css";

const STATUS_TABS = [
  { key: "", label: "全部" },
  { key: "pending", label: "待发货" },
  { key: "shipped", label: "已发货" },
  { key: "signed", label: "已签收" },
  { key: "canceled", label: "已取消" },
];

const PAGE_SIZES = [50, 100, 200];

const rowKey = (item: BridgeItem) => `${item.account_id}|${item.skc_id}`;

function costValue(text: string): number | null {
  if (!text.trim()) return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function OrderProfitCostPage({ isActive = true }: { isActive?: boolean }) {
  const [configured, setConfigured] = useState(true);
  const [bindings, setBindings] = useState<BridgeBinding[]>([]);
  const [synced, setSynced] = useState(false);
  const [syncedAt, setSyncedAt] = useState("");

  const [items, setItems] = useState<BridgeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [statusFilter, setStatusFilter] = useState("");
  const [missingOnly, setMissingOnly] = useState(false);
  const [shopFilter, setShopFilter] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [reloadTick, setReloadTick] = useState(0);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const [edits, setEdits] = useState<Record<string, string>>({});
  const [batchValue, setBatchValue] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const shopFilterKey = shopFilter.join(",");

  const loadBridge = async () => {
    try {
      const [statusPayload, bindingPayload] = await Promise.all([
        bridgeApi.status(),
        bridgeApi.bindings(),
      ]);
      setConfigured(statusPayload.configured);
      setBindings(bindingPayload.accounts || []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  useEffect(() => {
    void loadBridge();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isActive) return;
    if (!synced) return;
    if (!bindings.length) {
      setItems([]);
      setTotal(0);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    bridgeApi
      .items({
        account_ids: shopFilterKey,
        status: statusFilter,
        date_from: dateFrom,
        date_to: dateTo,
        keyword,
        missing_cost: missingOnly ? 1 : undefined,
        page,
        page_size: pageSize,
      })
      .then((payload) => {
        if (cancelled) return;
        setItems(payload.items || []);
        setTotal(payload.total || 0);
        setNote(
          payload.skipped_no_skc
            ? `已扫描 ${numberText(payload.scanned)} 行订单，其中 ${numberText(
                payload.skipped_no_skc,
              )} 行缺少 SKC 已跳过`
            : "",
        );
      })
      .catch((cause) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    isActive,
    synced,
    bindings.length,
    shopFilterKey,
    statusFilter,
    missingOnly,
    dateFrom,
    dateTo,
    keyword,
    page,
    pageSize,
    reloadTick,
  ]);

  const groups = useMemo(() => {
    const map = new Map<string, { accountId: string; shopName: string; rows: BridgeItem[] }>();
    for (const item of items) {
      const group = map.get(item.account_id) || {
        accountId: item.account_id,
        shopName: item.shop_name || item.account_id,
        rows: [],
      };
      group.rows.push(item);
      map.set(item.account_id, group);
    }
    return Array.from(map.values());
  }, [items]);

  const isDirty = (item: BridgeItem) => {
    const raw = edits[rowKey(item)];
    if (raw === undefined) return false;
    const value = costValue(raw);
    if (value === null) return false;
    return value !== (item.unit_cost ?? -1);
  };

  const dirtyRows = items.filter(isDirty);
  const missingCost = items.filter((item) => item.unit_cost === null).length;

  const toggleShop = (accountId: string) => {
    setPage(1);
    setShopFilter((previous) =>
      previous.includes(accountId)
        ? previous.filter((id) => id !== accountId)
        : [...previous, accountId],
    );
  };

  const sync = () => {
    setSynced(true);
    setSyncedAt(new Date().toISOString());
    setReloadTick((tick) => tick + 1);
  };

  const applyBatch = () => {
    const value = costValue(batchValue);
    if (value === null) {
      showToast("批量成本请填 0 或正数", "error");
      return;
    }
    setEdits((previous) => {
      const next = { ...previous };
      for (const item of items) next[rowKey(item)] = String(value);
      return next;
    });
  };

  const submit = async () => {
    const pending: Record<string, { skc_id: string; unit_cost: number; currency: string }[]> = {};
    let editable = 0;
    for (const item of items) {
      if (!isDirty(item)) continue;
      const value = costValue(edits[rowKey(item)] ?? "");
      if (value === null) continue;
      editable += 1;
      (pending[item.account_id] ||= []).push({
        skc_id: item.skc_id,
        unit_cost: value,
        currency: item.currency || "CNY",
      });
    }
    if (!editable) {
      showToast("没有可提交的改动（留空或非法值不会被提交）", "error");
      return;
    }
    setSubmitting(true);
    try {
      const results = await Promise.all(
        Object.entries(pending).map(([accountId, rows]) =>
          bridgeApi.saveCosts(accountId, rows),
        ),
      );
      const applied = results.reduce((sum, row) => sum + row.applied, 0);
      showToast(`已提交 ${applied} 行成本`, "success");
      setEdits((previous) => {
        const next = { ...previous };
        for (const item of items) if (isDirty(item)) delete next[rowKey(item)];
        return next;
      });
      setReloadTick((tick) => tick + 1);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="op-stage">
      <PageHero
        eyebrow="店铺数据 · 成本回填"
        title="链接成本"
        desc="先在「店铺绑定」校验 Temu 账号并绑定店铺，回来后手动点击「同步」拉取链接数据，逐行填写单件成本并提交。"
        art="funds"
        aside={
          <>
            <span className="op-chip">{bindings.length} 个店铺</span>
            <span className="op-chip">{numberText(total)} 个链接</span>
          </>
        }
      />

      {!configured ? (
        <SectionCard title="桥接未配置" desc="本地没有配置采集服务器地址或共享密钥。" icon="warning">
          <EmptyBlock
            title="无法连接采集服务器"
            desc="请在本地 runtime 配置 WH_LOCAL_BRIDGE_KEY 后重启工作台。"
            art="empty"
            compact
          />
        </SectionCard>
      ) : null}

      <MetricBand>
        <Metric label="已绑定店铺" value={bindings.length} glyph="cloud" />
        <Metric label="筛选到的链接" value={numberText(total)} glyph="link" />
        <Metric label="当前页待填成本" value={missingCost} glyph="warning" />
        <Metric label="待提交改动" value={dirtyRows.length} glyph="money" />
      </MetricBand>

      <SectionCard
        title="链接成本"
        desc="按「店铺 → 链接」聚合，成本按链接（SKC）的单件成本提交，只提交改动行。"
        icon="link"
        scene="orders"
        meta={
          <>
            <span className="op-chip">{numberText(total)} 个链接</span>
            {syncedAt ? (
              <span className="op-chip">上次同步 {beijingTime(syncedAt)}</span>
            ) : (
              <span className="op-chip">未同步</span>
            )}
          </>
        }
        action={
          <div className="op-filter-actions">
            <button
              className="op-btn op-btn-sm"
              type="button"
              disabled={loading || !bindings.length}
              onClick={sync}
            >
              {loading ? "同步中…" : "同步"}
            </button>
            <button
              className="op-btn op-btn-primary op-btn-sm"
              type="button"
              disabled={submitting || !dirtyRows.length}
              onClick={() => void submit()}
            >
              {submitting ? "提交中…" : `提交改动（${dirtyRows.length}）`}
            </button>
          </div>
        }
      >
        {!bindings.length ? (
          <EmptyBlock
            title="还没有绑定店铺"
            desc="先到左侧「店铺绑定」输入 Temu 账号密码完成绑定，再回来同步数据。"
            art="empty"
          />
        ) : !synced ? (
          <EmptyBlock
            title="尚未同步店铺数据"
            desc="绑定完成后，点击上方「同步」从采集服务器拉取链接数据。"
            art="empty"
          />
        ) : (
          <>
            <div className="op-filter-card">
              <div className="op-toolbar">
                <strong>筛选</strong>
                {STATUS_TABS.map((tab) => (
                  <button
                    key={tab.key || "all"}
                    type="button"
                    className={`op-chip${statusFilter === tab.key ? " is-ok" : ""}`}
                    onClick={() => {
                      setStatusFilter(tab.key);
                      setPage(1);
                    }}
                  >
                    {tab.label}
                  </button>
                ))}
                <span className="op-chip-warn-divider" aria-hidden="true" />
                <button
                  type="button"
                  className={`op-chip${missingOnly ? " is-warn" : ""}`}
                  aria-pressed={missingOnly}
                  onClick={() => {
                    setMissingOnly((previous) => !previous);
                    setPage(1);
                  }}
                >
                  只看成本缺失
                </button>
              </div>
              <div className="op-filter-row">
                <label className="op-field op-field-slim">
                  <span>下单日期从</span>
                  <input
                    type="date"
                    value={dateFrom}
                    onChange={(event) => {
                      setDateFrom(event.target.value);
                      setPage(1);
                    }}
                  />
                </label>
                <label className="op-field op-field-slim">
                  <span>到</span>
                  <input
                    type="date"
                    value={dateTo}
                    onChange={(event) => {
                      setDateTo(event.target.value);
                      setPage(1);
                    }}
                  />
                </label>
                <label className="op-field op-field-wide">
                  <span>关键字（商品名 / SKC / SKU / 订单号）</span>
                  <input
                    type="search"
                    value={keywordInput}
                    placeholder="输入后回车查询"
                    onChange={(event) => setKeywordInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        setKeyword(keywordInput.trim());
                        setPage(1);
                      }
                    }}
                  />
                </label>
                <div className="op-filter-actions">
                  <button
                    className="op-btn op-btn-sm"
                    type="button"
                    onClick={() => {
                      setKeyword(keywordInput.trim());
                      setPage(1);
                    }}
                  >
                    查询
                  </button>
                  <button
                    className="op-btn op-btn-sm"
                    type="button"
                    onClick={() => {
                      setStatusFilter("");
                      setMissingOnly(false);
                      setShopFilter([]);
                      setDateFrom("");
                      setDateTo("");
                      setKeywordInput("");
                      setKeyword("");
                      setPage(1);
                    }}
                  >
                    清空筛选
                  </button>
                  <button
                    className="op-btn op-btn-sm"
                    type="button"
                    onClick={() => setReloadTick((tick) => tick + 1)}
                  >
                    刷新数据
                  </button>
                </div>
              </div>
              <div className="op-toolbar">
                <strong>店铺</strong>
                {bindings.map((binding) => (
                  <button
                    key={binding.account_id}
                    type="button"
                    className={`op-chip${shopFilter.includes(binding.account_id) ? " is-ok" : ""}`}
                    onClick={() => toggleShop(binding.account_id)}
                  >
                    {binding.shop_name || binding.account_id}
                  </button>
                ))}
                {shopFilter.length ? (
                  <button
                    type="button"
                    className="op-chip"
                    onClick={() => {
                      setShopFilter([]);
                      setPage(1);
                    }}
                  >
                    清除店铺选择
                  </button>
                ) : null}
              </div>
              <div className="op-filter-row">
                <label className="op-field op-field-slim">
                  <span>批量填成本（当前页全部链接）</span>
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={batchValue}
                    onChange={(event) => setBatchValue(event.target.value)}
                    placeholder="0.00"
                  />
                </label>
                <div className="op-filter-actions">
                  <button
                    className="op-btn op-btn-sm"
                    type="button"
                    disabled={!items.length}
                    onClick={applyBatch}
                  >
                    填充当前页
                  </button>
                </div>
              </div>
            </div>
            <div className={`op-table-wrap${loading ? " is-loading" : ""}`}>
              {error ? <p className="op-hint is-error">{error}</p> : null}
              {note ? <p className="op-hint">{note}</p> : null}
              {items.length ? (
                <table className="op-table op-table-bridge">
                  <thead>
                    <tr>
                      <th>商品 / 链接</th>
                      <th>最新状态</th>
                      <th className="op-num">单量</th>
                      <th className="op-num">申报价</th>
                      <th className="op-num">回款</th>
                      <th className="op-num">最近下单</th>
                      <th className="op-num">单件成本</th>
                      <th className="op-num">已保存</th>
                    </tr>
                  </thead>
                  {groups.map((group) => (
                    <tbody key={group.accountId}>
                      <tr className="op-bridge-group-row">
                        <td colSpan={8}>
                          <strong>{group.shopName}</strong>
                          <span>{group.rows.length} 个链接</span>
                        </td>
                      </tr>
                      {group.rows.map((item) => (
                        <tr key={rowKey(item)} className={isDirty(item) ? "is-dirty" : ""}>
                          <td className="op-bridge-name">
                            <strong title={item.goods_name}>
                              {item.goods_name || "(未命名商品)"}
                            </strong>
                            <span>
                              {item.skc_id}
                              {item.sku_count > 1 ? ` · ${item.sku_count} 个 SKU` : ""}
                            </span>
                          </td>
                          <td>
                            {item.order_status ? (
                              <span className="op-chip">{item.order_status}</span>
                            ) : (
                              "-"
                            )}
                          </td>
                          <td className="op-num">
                            {numberText(item.order_count)} 单 / {numberText(item.quantity)} 件
                          </td>
                          <td className="op-num">{money(item.declared_price, item.currency)}</td>
                          <td className="op-num">{money(item.gmv, item.currency)}</td>
                          <td className="op-num">{beijingTime(item.last_order_time) || "-"}</td>
                          <td className="op-num">
                            <input
                              className="op-cost-input"
                              type="number"
                              min={0}
                              step="0.01"
                              value={
                                edits[rowKey(item)] ??
                                (item.unit_cost === null ? "" : String(item.unit_cost))
                              }
                              placeholder={item.unit_cost === null ? "待填写" : ""}
                              onChange={(event) =>
                                setEdits((previous) => ({
                                  ...previous,
                                  [rowKey(item)]: event.target.value,
                                }))
                              }
                            />
                          </td>
                          <td className="op-num">
                            {item.unit_cost === null ? (
                              <span className="op-chip is-warn">缺成本</span>
                            ) : (
                              money(item.unit_cost, item.cost_currency || item.currency)
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  ))}
                </table>
              ) : loading ? null : (
                <EmptyBlock
                  title="没有符合筛选的数据"
                  desc="换个状态、日期范围或关键字再试。"
                  art="empty"
                  compact
                />
              )}
            </div>
            <div className="op-pager">
              <span className="op-pager-info">
                第 {page} / {pages} 页 · 共 {numberText(total)} 个链接
              </span>
              <label className="op-pager-size">
                每页
                <select
                  value={pageSize}
                  onChange={(event) => {
                    setPageSize(Number(event.target.value));
                    setPage(1);
                  }}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </label>
              <div className="op-pager-actions">
                <button
                  className="op-btn op-btn-sm"
                  type="button"
                  disabled={page <= 1 || loading}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  上一页
                </button>
                <button
                  className="op-btn op-btn-sm"
                  type="button"
                  disabled={page >= pages || loading}
                  onClick={() => setPage((current) => current + 1)}
                >
                  下一页
                </button>
              </div>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
}

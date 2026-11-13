import { useEffect, useMemo, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { EmptyBlock, Metric, MetricBand, PageHero, SectionCard } from "../components/ShopDataPrimitives";
import { money, numberText } from "../format";
import "../styles/orderProfit.css";

type SkuCostRow = {
  shop_name: string;
  sku_id: string;
  skc_id: string;
  skc_code: string;
  goods_name: string;
  sku_manual_cost_cny: number | null;
  quantity: number;
  order_lines: number;
  updated_at: string;
};

const keyOf = (row: SkuCostRow) => `${row.shop_name}|${row.sku_id}`;

function parseCost(value: string): number | null {
  if (!/^\d+(\.\d+)?$/.test(value.trim())) return null;
  const amount = Number(value);
  return Number.isFinite(amount) && amount >= 0 ? amount : null;
}

function csvEscape(value: string) {
  return `"${value.split('"').join('""')}"`;
}

export function OrderProfitCostPage({ isActive = true }: { isActive?: boolean }) {
  const [rows, setRows] = useState<SkuCostRow[]>([]);
  const [shop, setShop] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [query, setQuery] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await orderProfitApi.skuCosts({ shop, keyword: query });
      setRows(response.items || []);
      setDrafts({});
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isActive) void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive, shop, query]);

  const isDirty = (row: SkuCostRow) => {
    const draft = drafts[keyOf(row)];
    if (draft === undefined) return false;
    if (draft === "") return false;
    const parsed = parseCost(draft);
    return parsed === null || parsed !== row.sku_manual_cost_cny;
  };
  const dirtyRows = rows.filter(isDirty);
  const filledCount = rows.filter((row) => row.sku_manual_cost_cny !== null).length;
  const totalQuantity = useMemo(() => rows.reduce((sum, row) => sum + Number(row.quantity || 0), 0), [rows]);

  const setValue = (row: SkuCostRow, value: string) => {
    const key = keyOf(row);
    setDrafts((previous) => ({ ...previous, [key]: value }));
    const original = value === "" ? row.sku_manual_cost_cny : parseCost(value);
    const siblings = rows.filter((item) => item.shop_name === row.shop_name && item.sku_id === row.sku_id);
    setRows((previous) => previous.map((item) =>
      siblings.some((sibling) => keyOf(sibling) === keyOf(item))
        ? { ...item, sku_manual_cost_cny: value === "" ? null : original }
        : item,
    ));
  };

  const save = async () => {
    const invalid = dirtyRows.find((row) => {
      const value = drafts[keyOf(row)];
      return value !== "" && parseCost(value) === null;
    });
    if (invalid) {
      showToast(`SKU ${invalid.sku_id} 成本格式不正确；只接受 0 或正数`, "error");
      return;
    }
    const byShop = new Map<string, Map<string, number | null>>();
    for (const row of dirtyRows) {
      const cost = drafts[keyOf(row)] === "" ? null : parseCost(drafts[keyOf(row)]);
      const perSku = byShop.get(row.shop_name) || new Map<string, number | null>();
      const previous = perSku.get(row.sku_id);
      if (previous !== undefined && previous !== cost) {
        showToast(`同店铺 SKU ${row.sku_id} 存在不一致的成本修改`, "error");
        return;
      }
      perSku.set(row.sku_id, cost);
      byShop.set(row.shop_name, perSku);
    }
    setSaving(true);
    try {
      const outcomes = await Promise.all(Array.from(byShop.entries()).map(([shopName, costs]) =>
        orderProfitApi.saveSkuCosts(shopName, Array.from(costs.entries()).map(([sku_id, unit_cost_cny]) => ({ sku_id, unit_cost_cny }))),
      ));
      const applied = outcomes.flatMap((outcome) => outcome.applied);
      const skipped = outcomes.flatMap((outcome) => outcome.skipped);
      showToast(`已保存 ${applied.length} 个 SKU${skipped.length ? `；${skipped.length} 个未保存` : ""}`, skipped.length ? "error" : "success");
      await refresh();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSaving(false);
    }
  };

  const downloadCsvTemplate = () => {
    const lines = [["SKU号", "成本"], ...rows.map((row) => [row.sku_id, ""])];
    const csv = `\uFEFF${lines.map((line) => line.map((value) => csvEscape(String(value))).join(",")).join("\r\n")}`;
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "sku-cost-template.csv";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const importCosts = async () => {
    if (!importFile) {
      showToast("请选择两列 SKU 成本模板", "error");
      return;
    }
    try {
      const result = await orderProfitApi.importSkuCosts(shop, importFile);
      const applied = result.applied?.length || 0;
      const issues = [...(result.issues || []), ...(result.skipped || [])];
      showToast(`导入完成：更新 ${applied} 个 SKU，未导入 ${issues.length} 行`, issues.length ? "error" : "success");
      if (issues.length) setError(issues.slice(0, 8).map((item) => `${"row" in item ? `第 ${item.row} 行 · ` : ""}${item.sku_id || "(空 SKU)"}：${item.reason}`).join("；"));
      setImportFile(null);
      await refresh();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    }
  };

  return (
    <div className="op-stage">
      <PageHero
        eyebrow="成本填入"
        title="订单成本"
        desc="按店铺与 SKU 维护单件成本，并同步显示到该 SKU 的所有已采订单行。此成本字段当前不参与利润计算。"
        art="funds"
        aside={<><span className="op-chip">{numberText(rows.length)} 个 SKU</span><span className="op-chip">{shop === "all" ? "全部店铺" : shop}</span></>}
      />
      <MetricBand>
        <Metric label="筛选 SKU" value={numberText(rows.length)} glyph="link" />
        <Metric label="已填成本" value={numberText(filledCount)} glyph="money" />
        <Metric label="待填成本" value={numberText(rows.length - filledCount)} glyph="warning" />
        <Metric label="订单件数" value={numberText(totalQuantity)} glyph="orders" />
      </MetricBand>
      <SectionCard title="SKU 成本模板" desc="模板固定两列 SKU号、成本；导出按当前店铺筛选生成全部 SKU，不受关键字影响。" icon="cost">
        <div className="op-filter-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="op-btn op-btn-sm" type="button" onClick={() => void orderProfitApi.skuCostTemplate(shop).catch((cause) => showToast(cause instanceof Error ? cause.message : String(cause), "error"))}>下载 XLSX 模板</button>
          <button className="op-btn op-btn-sm" type="button" onClick={downloadCsvTemplate}>下载 CSV 模板</button>
          <label className="op-btn op-btn-sm">选择导入文件<input type="file" accept=".xlsx,.csv" onChange={(event) => setImportFile(event.target.files?.[0] || null)} style={{ display: "none" }} /></label>
          <span className="op-chip">{importFile?.name || "未选择文件"}</span>
          <button className="op-btn op-btn-primary op-btn-sm" type="button" disabled={!importFile || saving} onClick={() => void importCosts()}>校验并导入</button>
        </div>
        <p className="op-hint">空成本跳过、不清除既有值；0 是有效成本；负数、未知 SKU 和重复冲突会列为未导入。推荐使用 XLSX 保留 SKU 文本和前导零。</p>
      </SectionCard>
      <SectionCard title="已采订单 SKU" desc="订单商品仍作为成本填写上下文；同一店铺与 SKU 共用单件成本。" icon="orders" action={<button className="op-btn op-btn-primary op-btn-sm" type="button" disabled={saving || !dirtyRows.length} onClick={() => void save()}>{saving ? "保存中…" : `保存改动（${dirtyRows.length}）`}</button>}>
        <div className="op-filter-row">
          <label className="op-field op-field-slim"><span>店铺</span><select value={shop} onChange={(event) => setShop(event.target.value)}><option value="all">全部店铺</option>{Array.from(new Set(rows.map((row) => row.shop_name).filter(Boolean))).map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
          <label className="op-field op-field-wide"><span>搜索 SKU / PO / 商品</span><input value={keyword} type="search" onChange={(event) => setKeyword(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") setQuery(keyword.trim()); }} placeholder="输入后回车" /></label>
          <button className="op-btn op-btn-sm" type="button" onClick={() => setQuery(keyword.trim())}>查询</button>
          <button className="op-btn op-btn-sm" type="button" onClick={() => { setKeyword(""); setQuery(""); }}>清除筛选</button>
        </div>
        {error ? <p className="op-hint is-error">{error}</p> : null}
        <div className={`op-table-wrap${loading ? " is-loading" : ""}`}>
          {rows.length ? <table className="op-table"><thead><tr><th>店铺</th><th>商品</th><th>SKU</th><th className="op-num">件数</th><th className="op-num">关联订单行</th><th className="op-num">单件成本 · CNY</th><th className="op-num">成本小计 · CNY</th></tr></thead><tbody>{rows.map((row) => {
            const current = drafts[keyOf(row)] ?? (row.sku_manual_cost_cny === null ? "" : String(row.sku_manual_cost_cny));
            const quantity = Number(row.quantity || 0);
            const value = current === "" ? null : parseCost(current);
            return <tr key={keyOf(row)} className={isDirty(row) ? "is-dirty" : ""}><td>{row.shop_name || "—"}</td><td>{row.goods_name || "—"}</td><td><code>{row.sku_id}</code></td><td className="op-num">{numberText(quantity)}</td><td className="op-num">{numberText(row.order_lines)}</td><td className="op-num"><input className="op-cost-input" type="number" min={0} step="0.01" value={current} placeholder="未填写" aria-label={`${row.shop_name} ${row.sku_id} 单件成本`} onChange={(event) => setValue(row, event.target.value)} /></td><td className="op-num">{value === null ? "—" : money(value * quantity, "CNY")}</td></tr>;
          })}</tbody></table> : loading ? null : <EmptyBlock title="没有符合条件的 SKU" desc="先完成订单数据采集，或调整店铺和关键词筛选。" art="empty" compact />}
        </div>
      </SectionCard>
    </div>
  );
}

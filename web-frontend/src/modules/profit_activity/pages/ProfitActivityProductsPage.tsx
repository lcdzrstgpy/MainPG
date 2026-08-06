import { useMemo, useState } from "react";

import {
  deleteProfitActivityProducts,
  downloadProfitActivityCatalog,
  listProfitActivityProducts,
} from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";
import "../styles/profitActivityProducts.css";

const siteLabels: Record<ProfitActivitySite, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };
const pageSizeOptions = [10, 50, 100] as const;

export function ProfitActivityProductsPage() {
  const [site, setSite] = useState<ProfitActivitySite>("US");
  const [scope, setScope] = useState<ProfitActivityScope>("default");
  const [querySkcs, setQuerySkcs] = useState("");
  const [products, setProducts] = useState<ProfitActivityProduct[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof pageSizeOptions)[number]>(10);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("输入 SKC 查询；留空展示数据库中当前权限可见产品。");

  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageProducts = useMemo(
    () => products.slice((safePage - 1) * pageSize, safePage * pageSize),
    [products, safePage, pageSize],
  );
  const selectedSkcs = useMemo(() => [...selected], [selected]);
  const pageSelected = pageProducts.length > 0 && pageProducts.every((item) => selected.has(item.skc));

  const withBusy = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setMessage(`${label}中...`);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  const queryProducts = () => withBusy("查询产品", async () => {
    const nextProducts = await listProfitActivityProducts({ site, scope, skcs: querySkcs });
    setProducts(nextProducts);
    setSelected(new Set());
    setPage(1);
    setMessage(`已查询到 ${nextProducts.length} 个产品。`);
  });

  const togglePageSelected = () => {
    setSelected((current) => {
      const next = new Set(current);
      for (const product of pageProducts) {
        if (pageSelected) next.delete(product.skc);
        else next.add(product.skc);
      }
      return next;
    });
  };

  const deleteSelected = () => withBusy("删除已选产品", async () => {
    if (!selectedSkcs.length) return;
    const confirmed = window.confirm(`确认删除已选 ${selectedSkcs.length} 个产品？`);
    if (!confirmed) {
      setMessage("已取消删除。");
      return;
    }
    const result = await deleteProfitActivityProducts({ site, skcs: selectedSkcs });
    const nextProducts = await listProfitActivityProducts({ site, scope, skcs: querySkcs });
    setProducts(nextProducts);
    setSelected(new Set());
    setPage((current) => Math.min(current, Math.max(1, Math.ceil(nextProducts.length / pageSize))));
    setMessage(`删除完成，后端确认删除 ${result.deleted ?? selectedSkcs.length} 个产品。`);
  });

  const downloadCatalog = () => withBusy("下载产品档案", async () => {
    await downloadProfitActivityCatalog({ site, scope });
    setMessage("产品档案已开始下载。");
  });

  return (
    <div className="profit-products-page">
      <section className="profit-products-head">
        <div>
          <p className="eyebrow">PROFIT ACTIVITY</p>
          <h1>产品库</h1>
          <p>查询数据库产品，按站点管理当前账号权限可见的利润活动产品。</p>
        </div>
      </section>

      <section className="profit-products-panel">
        <div className="profit-products-filters">
          <div className="profit-site-tabs">
            {(["US", "CO", "EC"] as ProfitActivitySite[]).map((item) => (
              <button key={item} className={site === item ? "is-active" : ""} onClick={() => { setSite(item); setPage(1); }}>
                {siteLabels[item]}
              </button>
            ))}
          </div>
          <label>查询范围<select value={scope} onChange={(event) => { setScope(event.target.value as ProfitActivityScope); setPage(1); }}><option value="default">只查本人/默认权限</option><option value="company">查本公司在档产品</option></select></label>
          <label>每页条数<select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value) as typeof pageSize); setPage(1); }}>{pageSizeOptions.map((value) => <option key={value} value={value}>每页 {value} 条</option>)}</select></label>
        </div>

        <div className="profit-products-query">
          <textarea value={querySkcs} onChange={(event) => setQuerySkcs(event.target.value)} placeholder="输入 SKC，支持换行、空格、逗号批量查询；留空展示数据库里当前权限可见产品" />
          <div className="profit-products-actions">
            <button className="primary-button" onClick={queryProducts} disabled={!!busy}>查询产品</button>
            <button onClick={togglePageSelected} disabled={!pageProducts.length}>{pageSelected ? "取消本页" : "全选本页"}</button>
            <button className="danger-button" onClick={deleteSelected} disabled={!selectedSkcs.length || !!busy}>删除已选 {selectedSkcs.length}</button>
            <button onClick={downloadCatalog} disabled={!!busy}>下载产品档案</button>
          </div>
        </div>
        <p className="profit-products-message">{busy || message}</p>

        <div className="profit-table-wrap">
          <table className="profit-table">
            <thead>
              <tr><th>选择</th><th>SKC</th><th>公司</th><th>创建人</th><th>售价</th><th>成本</th><th>重量</th><th>利润</th><th>利润率</th><th>货源</th><th>图片</th><th>权限</th></tr>
            </thead>
            <tbody>
              {pageProducts.length ? pageProducts.map((item) => (
                <tr key={`${item.site || item.site_code}-${item.skc}`}>
                  <td><input type="checkbox" checked={selected.has(item.skc)} onChange={(event) => setSelected(toggleSet(selected, item.skc, event.target.checked))} /></td>
                  <td>{item.skc}</td>
                  <td>{item.workspace_name || "-"}</td>
                  <td>{item.created_by_username || item.created_by || "-"}</td>
                  <td>{money(item.selling_price)}</td>
                  <td>{money(item.cost_price)}</td>
                  <td>{item.weight_kg ?? "-"}</td>
                  <td className={(item.net_profit ?? 0) >= 0 ? "profit-good" : "profit-bad"}>{money(item.net_profit)}</td>
                  <td>{percent(item.profit_rate)}</td>
                  <td>{item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">打开</a> : "-"}</td>
                  <td>{item.image_path ? "主图" : "-"} {item.source_image_path ? "货源图" : ""}</td>
                  <td>{item.is_owner ? "本人" : item.can_edit ? "可编辑" : "只读"}</td>
                </tr>
              )) : (
                <tr><td colSpan={12}>暂无产品。输入 SKC 后查询，或留空查询当前权限可见产品。</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="profit-products-pagination">
          <span>共 {products.length} 条，当前第 {safePage} / {totalPages} 页</span>
          <div>
            <button onClick={() => setPage(1)} disabled={safePage <= 1}>首页</button>
            <button onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={safePage <= 1}>上一页</button>
            <input value={safePage} onChange={(event) => setPage(Math.max(1, Number(event.target.value) || 1))} aria-label="当前页码" />
            <button onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={safePage >= totalPages}>下一页</button>
            <button onClick={() => setPage(totalPages)} disabled={safePage >= totalPages}>末页</button>
          </div>
        </div>
      </section>
    </div>
  );
}

function toggleSet(source: Set<string>, value: string, checked: boolean) {
  const next = new Set(source);
  if (checked) next.add(value);
  else next.delete(value);
  return next;
}

function money(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function percent(value: unknown) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}


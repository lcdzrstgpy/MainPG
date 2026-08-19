import { useEffect, useMemo, useRef, useState } from "react";

import {
  deleteProfitActivityProducts,
  downloadProfitActivityCatalog,
  listProfitActivitySites,
  listProfitActivityProducts,
  loadProductImage,
  saveProfitActivityProductEdit,
} from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";
import { ProductSourceDrawer } from "../components/ProductSourceDrawer";
import "../styles/profitActivityProducts.css";

const siteLabels: Record<string, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };
const allSites: ProfitActivitySite[] = ["US", "CO", "EC"];
const pageSizeOptions = [10, 50, 100] as const;
type ProductSourceFilter = "manual" | "price_verification" | "all";
const productKey = (item: ProfitActivityProduct) => `${item.site || item.site_code || "US"}-${item.skc}`;
const productIdText = (item: ProfitActivityProduct) => item.product_id ?? item.skc;
const productCreatedTime = (item: ProfitActivityProduct) => {
  const value = Date.parse(item.library_created_at || item.created_at || item.updated_at || "");
  return Number.isFinite(value) ? value : 0;
};
const sortProductsByCreatedDesc = (items: ProfitActivityProduct[]) => [...items].sort((left, right) => (
  productCreatedTime(right) - productCreatedTime(left) || (right.id ?? 0) - (left.id ?? 0)
));

// 产品库跨挂载缓存：切换页面再返回时立即展示上次数据，避免每次空表 + 重新等待查询
type LibraryPageSize = (typeof pageSizeOptions)[number];
const productLibraryCache: {
  sites?: ProfitActivitySite[];
  scope?: ProfitActivityScope;
  querySkcs?: string;
  products?: ProfitActivityProduct[];
  page?: number;
  pageSize?: LibraryPageSize;
} = {};

export function ProfitActivityProductsPage({ isActive = true }: { isActive?: boolean }) {
  const [sites, setSites] = useState<Set<ProfitActivitySite>>(() => new Set(productLibraryCache.sites ?? allSites));
  const [siteOptions, setSiteOptions] = useState(() => allSites.map((site) => ({ site_code: site, display_name: siteLabels[site], builtin: true })));
  const [scope] = useState<ProfitActivityScope>(productLibraryCache.scope ?? "default");
  const [sourceFilter, setSourceFilter] = useState<ProductSourceFilter>("all");
  const [querySkcs, setQuerySkcs] = useState(productLibraryCache.querySkcs ?? "");
  const [products, setProducts] = useState<ProfitActivityProduct[]>(productLibraryCache.products ?? []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(productLibraryCache.page ?? 1);
  const [pageSize, setPageSize] = useState<(typeof pageSizeOptions)[number]>(productLibraryCache.pageSize ?? 10);
  // 页码输入框的草稿值：允许用户自由填写，回车/失焦/点“跳转”才生效
  const [pageInput, setPageInput] = useState("1");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("输入商品ID（支持 SKU、SKC、SPU）查询；留空展示数据库中当前权限可见产品。");
  const [activeProduct, setActiveProduct] = useState<ProfitActivityProduct | null>(null);
  const [editingProduct, setEditingProduct] = useState<ProfitActivityProduct | null>(null);
  const [previewImage, setPreviewImage] = useState<{ url: string; label: string } | null>(null);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const tableScrollbarRef = useRef<HTMLDivElement>(null);
  const theadRef = useRef<HTMLTableSectionElement>(null);
  const fixedHeaderRef = useRef<HTMLDivElement>(null);
  const fixedHeaderScrollRef = useRef<HTMLDivElement>(null);
  const [headerStuck, setHeaderStuck] = useState(false);
  // 鼠标拖拽横向平移表格：记录按下时的起点与初始横向偏移
  const tableDragRef = useRef<{ startX: number; startScrollLeft: number } | null>(null);
  const [tableDragging, setTableDragging] = useState(false);
  // 跟随视口的横向滚动条：track 宽度等于表格实际宽度
  const [tableScrollWidth, setTableScrollWidth] = useState(0);

  useEffect(() => {
    if (isActive) return;
    setActiveProduct(null);
    setEditingProduct(null);
    setPreviewImage(null);
  }, [isActive]);

  useEffect(() => {
    void listProfitActivitySites().then((items) => {
      if (items.length) setSiteOptions(items);
    }).catch(() => undefined);
  }, []);

  const availableSites = siteOptions.map((item) => item.site_code);
  const siteLabel = (value: ProfitActivitySite) => siteOptions.find((item) => item.site_code === value)?.display_name || siteLabels[value] || value;

  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageProducts = useMemo(
    () => products.slice((safePage - 1) * pageSize, safePage * pageSize),
    [products, safePage, pageSize],
  );
  const selectedCount = selected.size;
  const pageSelected = pageProducts.length > 0 && pageProducts.every((item) => selected.has(productKey(item)));
  // 页码输入框与当前页保持同步（翻页按钮/查询刷新后同步显示），但不打断输入过程
  useEffect(() => {
    setPageInput(String(safePage));
  }, [safePage]);

  const goToPageInput = () => {
    const target = Math.max(1, Math.min(totalPages, Math.floor(Number(pageInput) || 1)));
    setPage(target);
    setPageInput(String(target));
  };

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

  const fetchProducts = async () => {
    if (!sites.size) return [];
    // 单个站点查询失败不应导致整个产品库显示为空：失败站点提示，其余站点正常展示
    const settled = await Promise.allSettled(
      [...sites].map((site) => listProfitActivityProducts({ site, scope, skcs: querySkcs })),
    );
    const results: ProfitActivityProduct[] = [];
    const failures: string[] = [];
    settled.forEach((item, index) => {
      if (item.status === "fulfilled") results.push(...item.value);
      else {
        const site = [...sites][index];
        failures.push(`${siteLabel(site)}: ${item.reason instanceof Error ? item.reason.message : String(item.reason)}`);
      }
    });
    if (failures.length) setMessage(`部分站点查询失败：${failures.join("；")}`);
    const filtered = sourceFilter === "all"
      ? results
      : results.filter((item) => sourceFilter === "price_verification"
        ? item.source_type === "price_verification"
        : item.source_type !== "price_verification");
    return sortProductsByCreatedDesc(filtered);
  };

  const queryProducts = () => withBusy("查询产品", async () => {
    if (!sites.size) {
      setProducts([]);
      setSelected(new Set());
      setPage(1);
      setMessage("请至少勾选一个站点。");
      return;
    }
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
    setSelected(new Set());
    setPage(1);
    setMessage(`查询数据库产品 完成。\n已查询到 ${nextProducts.length} 个产品。`);
  });

  useEffect(() => {
    // 有缓存：立即展示上次数据并在后台静默刷新（不置 busy、不重置页码/选择）；
    // 无缓存：首次进入自动查询。
    if (productLibraryCache.products) {
      void (async () => {
        try {
          const next = await fetchProducts();
          setProducts(next);
          setMessage(`已刷新为最新产品数据，共 ${next.length} 个产品。`);
        } catch {
          setMessage("后台刷新失败，当前展示上次缓存数据；可点击“查询产品”重试。");
        }
      })();
    } else {
      void queryProducts();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 页面状态变化时同步到跨挂载缓存，切走再切回可立即恢复上次展示
  useEffect(() => {
    productLibraryCache.sites = [...sites];
    productLibraryCache.scope = scope;
    productLibraryCache.querySkcs = querySkcs;
    productLibraryCache.products = products;
    productLibraryCache.page = page;
    productLibraryCache.pageSize = pageSize;
  }, [sites, scope, querySkcs, products, page, pageSize]);

  // 刷新产品列表但保留页码/选择/未保存修改：用于图片上传等局部更新，避免把用户“弹回”首页。
  const refreshProducts = async () => {
    if (!sites.size) return;
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
  };

  // 跟随视口的横向滚动条：与表格容器的 scrollLeft 双向同步，track 宽度 = 表格实际宽度。
  // 这样滚动条始终贴在视口顶部可见，不用滑到页面最底部去拖动。
  useEffect(() => {
    const wrap = tableWrapRef.current;
    const bar = tableScrollbarRef.current;
    if (!wrap || !bar) return;
    const syncBar = () => {
      if (bar.scrollLeft !== wrap.scrollLeft) bar.scrollLeft = wrap.scrollLeft;
    };
    const syncWrap = () => {
      if (wrap.scrollLeft !== bar.scrollLeft) wrap.scrollLeft = bar.scrollLeft;
    };
    const updateWidth = () => setTableScrollWidth(wrap.scrollWidth);
    const resizeObserver = new ResizeObserver(updateWidth);
    wrap.addEventListener("scroll", syncBar, { passive: true });
    bar.addEventListener("scroll", syncWrap, { passive: true });
    resizeObserver.observe(wrap);
    updateWidth();
    return () => {
      wrap.removeEventListener("scroll", syncBar);
      bar.removeEventListener("scroll", syncWrap);
      resizeObserver.disconnect();
    };
  }, []);

  // 独立固定表头：原表头滚出可视区时，显示一份固定在顶部的克隆表头，
  // 保证滚动到第 20 行等位置时仍能看到"选择/站点/SKC..."标题行。
  useEffect(() => {
    const contentCard = document.querySelector(".content-card") as HTMLElement | null;
    const wrap = tableWrapRef.current;
    const clone = fixedHeaderRef.current;
    const thead = theadRef.current;
    if (!contentCard || !wrap || !clone || !thead) return;

    const update = () => {
      const contentRect = contentCard.getBoundingClientRect();
      const wrapRect = wrap.getBoundingClientRect();
      const theadRect = thead.getBoundingClientRect();
      // 克隆表头横向与内容区对齐，内层再留出表格左边距，使列与原表头完全对齐
      clone.style.left = `${Math.round(contentRect.left)}px`;
      clone.style.width = `${Math.round(contentRect.width)}px`;
      clone.style.paddingLeft = `${Math.round(wrapRect.left - contentRect.left)}px`;
      // 顶部位置：顶栏被固定时贴在顶栏下方；否则贴在悬浮横向滚动条下方
      let top = 8;
      const topbar = document.querySelector(".topbar-card") as HTMLElement | null;
      if (topbar?.classList.contains("is-pinned")) {
        const topbarBottom = Math.round(topbar.getBoundingClientRect().bottom);
        if (topbarBottom > 0) top = topbarBottom + 6;
      } else {
        const scrollbar = document.querySelector(".profit-table-scrollbar") as HTMLElement | null;
        if (scrollbar) {
          const scrollbarBottom = Math.round(scrollbar.getBoundingClientRect().bottom);
          if (scrollbarBottom > 0) top = scrollbarBottom + 4;
        }
      }
      clone.style.top = `${top}px`;
      // 原表头滚出顶部可视区后显示克隆表头
      setHeaderStuck(theadRect.bottom < top);
    };

    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    contentCard.addEventListener("scroll", update, { passive: true });
    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(contentCard);
    resizeObserver.observe(wrap);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      contentCard.removeEventListener("scroll", update);
      resizeObserver.disconnect();
    };
  }, []);

  useEffect(() => {
    const thead = theadRef.current;
    const fixedHeader = fixedHeaderRef.current;
    if (!thead || !fixedHeader) return;
    const syncWidths = () => {
      const sourceTable = thead.closest("table") as HTMLTableElement | null;
      const cloneTable = fixedHeader.querySelector("table") as HTMLTableElement | null;
      if (sourceTable && cloneTable) {
        cloneTable.style.width = `${sourceTable.offsetWidth}px`;
        cloneTable.style.minWidth = `${sourceTable.offsetWidth}px`;
        cloneTable.style.tableLayout = "fixed";
      }
      const sourceThs = thead.querySelectorAll("th");
      const cloneThs = fixedHeader.querySelectorAll("th");
      sourceThs.forEach((th, index) => {
        const cloneTh = cloneThs[index] as HTMLElement | undefined;
        if (cloneTh) {
          const width = `${(th as HTMLElement).offsetWidth}px`;
          cloneTh.style.width = width;
          cloneTh.style.minWidth = width;
        }
      });
    };
    syncWidths();
    const resizeObserver = new ResizeObserver(syncWidths);
    resizeObserver.observe(thead);
    return () => resizeObserver.disconnect();
  }, [pageProducts, headerStuck]);

  useEffect(() => {
    const wrap = tableWrapRef.current;
    const cloneScroll = fixedHeaderScrollRef.current;
    if (!wrap || !cloneScroll) return;
    const syncClone = () => {
      if (cloneScroll.scrollLeft !== wrap.scrollLeft) cloneScroll.scrollLeft = wrap.scrollLeft;
    };
    const syncWrap = () => {
      if (wrap.scrollLeft !== cloneScroll.scrollLeft) wrap.scrollLeft = cloneScroll.scrollLeft;
    };
    wrap.addEventListener("scroll", syncClone, { passive: true });
    cloneScroll.addEventListener("scroll", syncWrap, { passive: true });
    return () => {
      wrap.removeEventListener("scroll", syncClone);
      cloneScroll.removeEventListener("scroll", syncWrap);
    };
  }, []);

  // 鼠标拖拽横向平移表格：命中文本内容时保留浏览器默认选中/复制；
  // 只有落在空白处（单元格留白、表格周边空白、滚动条条带）才触发平移。
  const tableDraggingRef = useRef(false);
  // 判断指针落点是否在文本上，用于区分"选择复制"与"拖拽平移"
  const pointerOverText = (x: number, y: number) => {
    const range = document.caretRangeFromPoint?.(x, y);
    if (range) return range.startContainer.nodeType === Node.TEXT_NODE;
    const position = (
      document as Document & {
        caretPositionFromPoint?: (clientX: number, clientY: number) =>
          | { offsetNode: Node }
          | null;
      }
    ).caretPositionFromPoint?.(x, y);
    return !!position && position.offsetNode.nodeType === Node.TEXT_NODE;
  };
  const onTablePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    // 表单控件、链接、按钮均不触发横向平移
    if (target.closest("input, textarea, select, button, a, label")) return;
    const wrap = tableWrapRef.current;
    if (!wrap || wrap.scrollWidth <= wrap.clientWidth || event.button !== 0) return;
    // 按下位置命中文本 → 让浏览器正常选择/复制，不进入平移
    if (pointerOverText(event.clientX, event.clientY)) return;
    tableDragRef.current = { startX: event.clientX, startScrollLeft: wrap.scrollLeft };
    tableDraggingRef.current = false;
  };
  const onTablePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = tableDragRef.current;
    const wrap = tableWrapRef.current;
    if (!drag || !wrap) return;
    const deltaX = event.clientX - drag.startX;
    if (!tableDraggingRef.current && Math.abs(deltaX) < 6) return;
    tableDraggingRef.current = true;
    setTableDragging(true);
    wrap.scrollLeft = drag.startScrollLeft - deltaX;
  };
  const endTableDrag = () => {
    tableDragRef.current = null;
    tableDraggingRef.current = false;
    setTableDragging(false);
  };

  const togglePageSelected = () => {
    setSelected((current) => {
      const next = new Set(current);
      for (const product of pageProducts) {
        const key = productKey(product);
        if (pageSelected) next.delete(key);
        else next.add(key);
      }
      return next;
    });
  };

  const deleteSelected = () => withBusy("删除已选产品", async () => {
    if (!selectedCount) return;
    const confirmed = window.confirm(`确认删除已选 ${selectedCount} 个产品？`);
    if (!confirmed) {
      setMessage("已取消删除。");
      return;
    }
    const bySite = new Map<ProfitActivitySite, string[]>();
    for (const product of products) {
      if (!selected.has(productKey(product))) continue;
      const site = (product.site || product.site_code || "US") as ProfitActivitySite;
      bySite.set(site, [...(bySite.get(site) || []), product.skc]);
    }
    let deleted = 0;
    for (const [site, skcs] of bySite) {
      const result = await deleteProfitActivityProducts({ site, skcs });
      deleted += result.deleted ?? skcs.length;
    }
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
    setSelected(new Set());
    setPage((current) => Math.min(current, Math.max(1, Math.ceil(nextProducts.length / pageSize))));
    setMessage(`删除完成，后端确认删除 ${deleted} 个产品。`);
  });

  const copySelectedProductIds = () => withBusy("复制商品ID", async () => {
    const ids = products
      .filter((product) => selected.has(productKey(product)))
      .map(productIdText)
      .filter(Boolean);
    if (!ids.length) return;
    await navigator.clipboard.writeText(ids.join("\n"));
    setMessage(`已复制 ${ids.length} 个商品ID。`);
  });

  const downloadCatalog = () => withBusy("下载产品档案", async () => {
    if (!sites.size) throw new Error("请至少勾选一个站点。");
    await downloadProfitActivityCatalog({ sites: [...sites], scope });
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

      <section className="profit-products-workspace">
        <div className="profit-products-search-bar">
          <label className="profit-products-search-field">
            <span>SKC 查询</span>
            <textarea value={querySkcs} onChange={(event) => setQuerySkcs(event.target.value)} placeholder="输入 SKU、SKC、SPU；多条可用空格、逗号或换行分隔" />
          </label>
          <label className="profit-products-select-field">
            <span>地区</span>
            <select
              value={sites.size === 1 ? [...sites][0] : "all"}
              onChange={(event) => setSites(event.target.value === "all" ? new Set(availableSites) : new Set([event.target.value as ProfitActivitySite]))}
            >
              <option value="all">全部站点</option>
              {siteOptions.map((item) => <option key={item.site_code} value={item.site_code}>{item.display_name}</option>)}
            </select>
          </label>
          <label className="profit-products-select-field">
            <span>查询范围</span>
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value as ProductSourceFilter)}>
              <option value="manual">手动</option>
              <option value="price_verification">核价</option>
              <option value="all">核价 + 手动</option>
            </select>
          </label>
          <button className="primary-button profit-products-query-button" onClick={queryProducts} disabled={!!busy}>查询产品</button>
        </div>
        <p className="profit-products-message">{busy || message.split("\n").map((line, index) => (<span key={index}>{line}<br /></span>))}</p>

        <div className="profit-table-scrollbar" ref={tableScrollbarRef} aria-label="横向滚动产品表格">
          <div className="profit-table-scrollbar-track" style={{ width: tableScrollWidth || "100%" }} />
        </div>
        <div
          className={`profit-table-head-sticky ${headerStuck ? "is-visible" : ""}`}
          ref={fixedHeaderRef}
          style={{ position: "fixed" }}
          aria-hidden="true"
        >
          <div className="profit-table-head-scroll" ref={fixedHeaderScrollRef}>
            <table className="profit-table">
              <ProductTableColumns />
              <thead>
                <tr><th>选择</th><th>站点</th><th>商品ID</th><th>入库日期</th><th>商品图</th><th>售价</th><th>成本</th><th>重量</th><th>利润</th><th>利润率</th><th>备注</th><th>货源</th><th>图片</th><th>操作</th></tr>
              </thead>
            </table>
          </div>
        </div>
        <div
          className={`profit-table-wrap ${tableDragging ? "is-dragging" : ""}`}
          ref={tableWrapRef}
          onPointerDown={onTablePointerDown}
          onPointerMove={onTablePointerMove}
          onPointerUp={endTableDrag}
          onPointerCancel={endTableDrag}
          onPointerLeave={endTableDrag}
        >
          <table className="profit-table">
            <ProductTableColumns />
            <thead ref={theadRef}>
              <tr><th>选择</th><th>站点</th><th>商品ID</th><th>入库日期</th><th>商品图</th><th>售价</th><th>成本</th><th>重量</th><th>利润</th><th>利润率</th><th>备注</th><th>货源</th><th>图片</th><th>操作</th></tr>
            </thead>
            <tbody>
              {pageProducts.length ? pageProducts.map((item) => {
                const key = productKey(item);
                return (
                  <tr key={key}>
                    <td><input type="checkbox" checked={selected.has(key)} onChange={(event) => setSelected(toggleSet(selected, key, event.target.checked))} /></td>
                    <td>{siteLabel((item.site || item.site_code || "US") as ProfitActivitySite)}</td>
                    <td>{productIdText(item)}{item.source_type === "price_verification" && <em className="profit-source-badge" title="来自核价及货源板块自动入库">核价</em>}</td>
                    <td>{libraryDate(item.library_created_at || item.created_at)}</td>
                    <td><ProductImageCell item={item} onPreview={(url) => setPreviewImage({ url, label: `${item.skc} 商品对应图` })} /></td>
                    <td>{money(item.selling_price)}</td>
                    <td>{money(item.cost_price)}</td>
                    <td>{money(item.weight_kg)}</td>
                    <td className={(item.net_profit ?? 0) >= 0 ? "profit-good" : "profit-bad"}>{money(item.net_profit)}</td>
                    <td>{percent(item.profit_rate)}</td>
                    <td className="profit-note-cell" title={item.note || ""}>{shortNote(item.note) || "-"}</td>
                    <td className="profit-source-cell"><button className="profit-source-open" onClick={() => setActiveProduct(item)} title="查看/编辑该 SKC 的货源链接">打开（{(item.source_groups ?? []).filter((group) => group?.source_url).length}）</button></td>
                    <td><AttachmentImageCell item={item} onPreview={(url) => setPreviewImage({ url, label: `${item.skc} 备注图片` })} /></td>
                    <td className="profit-product-action-cell"><button className="profit-product-edit-button" type="button" disabled={item.can_edit === false} onClick={() => setEditingProduct(item)}>编辑</button></td>
                  </tr>
                );
              }) : (
                <tr><td colSpan={14}>暂无产品。输入商品ID（SKU、SKC 或 SPU）后查询，或留空查询当前权限可见产品。</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="profit-products-pagination">
          <div className="profit-products-pagination-summary">
            <span>共 {products.length} 条，当前第 {safePage} / {totalPages} 页</span>
            <button onClick={togglePageSelected} disabled={!pageProducts.length}>{pageSelected ? "取消本页" : "全选本页"}</button>
            <button onClick={copySelectedProductIds} disabled={!selectedCount || !!busy}>复制已选商品ID</button>
            <button className="danger-button" onClick={deleteSelected} disabled={!selectedCount || !!busy}>删除已选 {selectedCount}</button>
            <button onClick={downloadCatalog} disabled={!!busy}>下载产品档案</button>
            <label className="profit-products-page-size">每页
              <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value) as typeof pageSize); setPage(1); }}>
                {pageSizeOptions.map((value) => <option key={value} value={value}>{value} 条</option>)}
              </select>
            </label>
          </div>
          <div className="profit-products-page-controls">
            <button onClick={() => setPage(1)} disabled={safePage <= 1}>首页</button>
            <button onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={safePage <= 1}>上一页</button>
            <label className="profit-page-jump">第
              <input
                value={pageInput}
                type="number"
                min={1}
                max={totalPages}
                onChange={(event) => setPageInput(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter") goToPageInput(); }}
                onBlur={goToPageInput}
                aria-label="跳转页码"
              />
              页</label>
            <button onClick={goToPageInput}>跳转</button>
            <button onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={safePage >= totalPages}>下一页</button>
            <button onClick={() => setPage(totalPages)} disabled={safePage >= totalPages}>末页</button>
          </div>
        </div>
      </section>
      <ProductSourceDrawer
        product={activeProduct}
        onClose={() => setActiveProduct(null)}
        onChanged={refreshProducts}
      />
      <ProductEditDialog
        product={editingProduct}
        onClose={() => setEditingProduct(null)}
        onSaved={(updated) => {
          setProducts((current) => current.map((product) => productKey(product) === productKey(updated) ? updated : product));
          setEditingProduct(null);
          setMessage(`已保存 ${updated.skc} 的产品信息。`);
        }}
      />
      {previewImage ? (
        <ImagePreviewModal
          url={previewImage.url}
          label={previewImage.label}
          onClose={() => setPreviewImage(null)}
        />
      ) : null}
    </div>
  );
}

function ProductTableColumns() {
  return (
    <colgroup>
      <col style={{ width: 42 }} /><col style={{ width: 40 }} /><col style={{ width: 110 }} /><col style={{ width: 72 }} />
      <col style={{ width: 50 }} /><col style={{ width: 48 }} /><col style={{ width: 48 }} /><col style={{ width: 42 }} />
      <col style={{ width: 64 }} /><col style={{ width: 54 }} /><col style={{ width: 48 }} /><col style={{ width: 50 }} />
      <col style={{ width: 36 }} /><col style={{ width: 48 }} />
    </colgroup>
  );
}

/** 大图预览弹窗：点击遮罩或关闭按钮关闭。 */
function ImagePreviewModal({ url, label, onClose }: { url: string; label: string; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="profit-image-preview-mask" onClick={onClose}>
      <div className="profit-image-preview-modal" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="profit-image-preview-close" onClick={onClose} title="关闭">×</button>
        <img className="profit-image-preview-img" src={url} alt={label} />
      </div>
    </div>
  );
}

function toggleSet(source: Set<string>, value: string, checked: boolean) {
  const next = new Set(source);
  if (checked) next.add(value);
  else next.delete(value);
  return next;
}

/** SKC 对应图单元格：表格只负责展示，图片替换统一在编辑弹窗处理。 */
function ProductImageCell({ item, onPreview }: { item: ProfitActivityProduct; onPreview: (url: string) => void }) {
  const site = (item.site || item.site_code || "US") as ProfitActivitySite;
  const [imageUrl, setImageUrl] = useState("");
  const [sourceImageFailed, setSourceImageFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    setSourceImageFailed(false);
    if (item.image_path) {
      loadProductImage({ skc: item.skc, site, kind: "product", version: item.image_path })
        .then((url) => {
          if (cancelled) {
            URL.revokeObjectURL(url);
            return;
          }
          objectUrl = url;
          setImageUrl(url);
        })
        .catch(() => {});
    } else {
      setImageUrl("");
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.skc, site, item.image_path, item.source_main_image_url]);

  const displayImage = imageUrl || (sourceImageFailed ? "" : item.source_main_image_url || "");

  return (
    <button type="button" className="profit-product-image-cell" onClick={() => displayImage && onPreview(displayImage)} title={displayImage ? "点击查看商品对应图" : "暂无商品对应图"}>
      {displayImage ? (
        <img
          className="profit-product-image"
          src={displayImage}
          alt={`${item.skc} 主图`}
          referrerPolicy={imageUrl ? undefined : "no-referrer"}
          onError={() => {
            if (!imageUrl) setSourceImageFailed(true);
          }}
        />
      ) : (
        <span className="profit-product-image-empty">无图</span>
      )}
    </button>
  );
}

/** 运营备注图片：独立于货源组，最多一张，可为空。 */
function AttachmentImageCell({ item, onPreview }: { item: ProfitActivityProduct; onPreview: (url: string) => void }) {
  const site = (item.site || item.site_code || "US") as ProfitActivitySite;
  const [url, setUrl] = useState("");

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    if (!item.attachment_image_path) {
      setUrl("");
      return;
    }
    loadProductImage({ skc: item.skc, site, kind: "attachment", version: item.attachment_image_path })
      .then((loaded) => {
        if (cancelled) {
          URL.revokeObjectURL(loaded);
          return;
        }
        objectUrl = loaded;
        setUrl(loaded);
      })
      .catch(() => setUrl(""));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.skc, site, item.attachment_image_path]);

  if (!url) return <span className="profit-product-image-empty">无图</span>;
  return (
    <button type="button" className="profit-source-thumb-btn" title="点击查看备注图片" onClick={() => onPreview(url)}>
      <img className="profit-source-thumb" src={url} alt={`${item.skc} 备注图片`} loading="lazy" />
    </button>
  );
}

function ProductEditDialog({ product, onClose, onSaved }: {
  product: ProfitActivityProduct | null;
  onClose: () => void;
  onSaved: (product: ProfitActivityProduct) => void;
}) {
  const [sellingPrice, setSellingPrice] = useState("");
  const [costPrice, setCostPrice] = useState("");
  const [weightKg, setWeightKg] = useState("");
  const [note, setNote] = useState("");
  const [productImage, setProductImage] = useState<File | null>(null);
  const [attachmentImage, setAttachmentImage] = useState<File | null>(null);
  const [clearAttachmentImage, setClearAttachmentImage] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!product) return;
    setSellingPrice(product.selling_price == null ? "" : String(product.selling_price));
    setCostPrice(product.cost_price == null ? "" : String(product.cost_price));
    setWeightKg(product.weight_kg == null ? "" : String(product.weight_kg));
    setNote(product.note ?? "");
    setProductImage(null);
    setAttachmentImage(null);
    setClearAttachmentImage(false);
    setError("");
  }, [product]);

  useEffect(() => {
    if (!product) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [product, saving, onClose]);

  if (!product) return null;
  const site = (product.site || product.site_code || "US") as ProfitActivitySite;
  const save = async () => {
    const numbers = [sellingPrice, costPrice, weightKg].map(Number);
    if (numbers.some((value) => !Number.isFinite(value) || value < 0)) {
      setError("售价、成本和重量必须为大于或等于 0 的数字。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const result = await saveProfitActivityProductEdit({
        site,
        skc: product.skc,
        sellingPrice,
        costPrice,
        weightKg,
        note,
        productImage,
        attachmentImage,
        clearAttachmentImage,
      });
      onSaved(result.product);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="profit-product-edit-mask" onClick={() => !saving && onClose()}>
      <section className="profit-product-edit-dialog" role="dialog" aria-modal="true" aria-label="编辑产品" onClick={(event) => event.stopPropagation()}>
        <header><div><p className="eyebrow">PRODUCT EDIT</p><h2>编辑产品</h2></div><button type="button" className="profit-product-edit-close" onClick={onClose} disabled={saving}>×</button></header>
        <div className="profit-product-edit-meta"><span>商品 ID：{productIdText(product)}</span><span>站点：{site}</span><span>入库日期：{libraryDate(product.library_created_at || product.created_at)}</span></div>
        <div className="profit-product-edit-grid">
          <label>售价<input type="number" min="0" step="any" value={sellingPrice} onChange={(event) => setSellingPrice(event.target.value)} /></label>
          <label>成本<input type="number" min="0" step="any" value={costPrice} onChange={(event) => setCostPrice(event.target.value)} /></label>
          <label>重量<input type="number" min="0" step="any" value={weightKg} onChange={(event) => setWeightKg(event.target.value)} /></label>
          <label className="profit-product-edit-note">备注<textarea maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} /></label>
          <label>商品对应图<input type="file" accept="image/*" onChange={(event) => setProductImage(event.target.files?.[0] ?? null)} /><small>{productImage?.name || (product.image_path ? "已保存，选择文件可替换" : "未上传")}</small></label>
          <label>备注图片<input type="file" accept="image/*" onChange={(event) => { setAttachmentImage(event.target.files?.[0] ?? null); setClearAttachmentImage(false); }} /><small>{attachmentImage?.name || (clearAttachmentImage ? "保存后清空" : product.attachment_image_path ? "已保存，选择文件可替换" : "未上传")}</small></label>
        </div>
        <div className="profit-product-edit-actions"><button type="button" className="danger-button" disabled={saving || (!product.attachment_image_path && !attachmentImage)} onClick={() => { setAttachmentImage(null); setClearAttachmentImage(true); }}>清空备注图片</button><span />{error ? <p>{error}</p> : null}<button type="button" onClick={onClose} disabled={saving}>取消</button><button type="button" className="primary-button" onClick={() => void save()} disabled={saving}>{saving ? "保存中…" : "保存修改"}</button></div>
      </section>
    </div>
  );
}

function libraryDate(value?: string) {
  const date = value ? new Date(value) : null;
  if (!date || !Number.isFinite(date.valueOf())) return "-";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function shortNote(value?: string) {
  const note = value?.trim() ?? "";
  return note.length > 4 ? `${note.slice(0, 4)}…` : note;
}

function money(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function percent(value: unknown) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}

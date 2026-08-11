import { useEffect, useMemo, useRef, useState, type ClipboardEvent } from "react";

import {
  deleteProfitActivityProducts,
  downloadProfitActivityCatalog,
  listProfitActivityProducts,
  loadProductImage,
  updateProductImage,
  updateProfitActivityProduct,
} from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";
import { ProductSourceDrawer } from "../components/ProductSourceDrawer";
import "../styles/profitActivityProducts.css";

const siteLabels: Record<ProfitActivitySite, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };
const allSites: ProfitActivitySite[] = ["US", "CO", "EC"];
const pageSizeOptions = [10, 50, 100] as const;
const editFieldLabels: Record<"selling_price" | "cost_price" | "weight_kg" | "note", string> = {
  selling_price: "售价",
  cost_price: "成本",
  weight_kg: "重量",
  note: "备注",
};

const productKey = (item: ProfitActivityProduct) => `${item.site || item.site_code || "US"}-${item.skc}`;

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

export function ProfitActivityProductsPage() {
  const [sites, setSites] = useState<Set<ProfitActivitySite>>(() => new Set(productLibraryCache.sites ?? allSites));
  const masterRef = useRef<HTMLInputElement>(null);
  const [scope, setScope] = useState<ProfitActivityScope>(productLibraryCache.scope ?? "default");
  const [querySkcs, setQuerySkcs] = useState(productLibraryCache.querySkcs ?? "");
  const [products, setProducts] = useState<ProfitActivityProduct[]>(productLibraryCache.products ?? []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(productLibraryCache.page ?? 1);
  const [pageSize, setPageSize] = useState<(typeof pageSizeOptions)[number]>(productLibraryCache.pageSize ?? 10);
  // 页码输入框的草稿值：允许用户自由填写，回车/失焦/点“跳转”才生效
  const [pageInput, setPageInput] = useState("1");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("输入 SKC 查询；留空展示数据库中当前权限可见产品。");
  const [activeProduct, setActiveProduct] = useState<ProfitActivityProduct | null>(null);
  const [previewImage, setPreviewImage] = useState<{ url: string; label: string } | null>(null);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const tableScrollbarRef = useRef<HTMLDivElement>(null);
  const theadRef = useRef<HTMLTableSectionElement>(null);
  const fixedHeaderRef = useRef<HTMLDivElement>(null);
  const fixedHeaderScrollRef = useRef<HTMLDivElement>(null);
  const [headerStuck, setHeaderStuck] = useState(false);
  // 首次进入时若备注列过宽会自动收敛列宽；用户手动拖动后不再自动调整。
  const columnAdjusted = useRef(false);
  // 鼠标拖拽横向平移表格：记录按下时的起点与初始横向偏移
  const tableDragRef = useRef<{ startX: number; startScrollLeft: number } | null>(null);
  const [tableDragging, setTableDragging] = useState(false);
  // 跟随视口的横向滚动条：track 宽度等于表格实际宽度
  const [tableScrollWidth, setTableScrollWidth] = useState(0);

  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageProducts = useMemo(
    () => products.slice((safePage - 1) * pageSize, safePage * pageSize),
    [products, safePage, pageSize],
  );
  const selectedCount = selected.size;
  const pageSelected = pageProducts.length > 0 && pageProducts.every((item) => selected.has(productKey(item)));
  const allSitesChecked = sites.size === allSites.length;
  const someSitesChecked = sites.size > 0 && !allSitesChecked;

  useEffect(() => {
    if (masterRef.current) masterRef.current.indeterminate = someSitesChecked;
  }, [someSitesChecked]);

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
        failures.push(`${siteLabels[site]}: ${item.reason instanceof Error ? item.reason.message : String(item.reason)}`);
      }
    });
    if (failures.length) setMessage(`部分站点查询失败：${failures.join("；")}`);
    return results;
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

  useEffect(() => {
    // 表头拖拽调整列宽
    const table = tableWrapRef.current?.querySelector("table") as HTMLTableElement | null;
    if (!table) return;
    let drag: { th: HTMLElement; startX: number; startWidth: number } | null = null;

    // auto 布局下仅改目标列宽度会被浏览器按内容/剩余空间重新分配而“吞掉”，
    // 拖拽开始时先冻结整表所有列当前宽度并临时切到 fixed 布局，保证拖动精确生效。
    // 松手后保持 fixed 布局不恢复，否则 auto 布局会按内容把列宽（尤其是备注列）撑回原样。
    const freezeColumns = () => {
      table.querySelectorAll("th").forEach((th) => {
        const element = th as HTMLElement;
        element.style.width = `${element.offsetWidth}px`;
        element.style.minWidth = `${element.offsetWidth}px`;
      });
      table.style.tableLayout = "fixed";
    };

    // 首次进入：备注列若被长文本内容撑得过宽，先收敛为固定宽度，避免一进页面就占满整行。
    if (!columnAdjusted.current) {
      const noteTh = table.querySelector("th:nth-child(10)") as HTMLElement | null;
      if (noteTh && noteTh.offsetWidth > 220) {
        freezeColumns();
        noteTh.style.width = "200px";
        noteTh.style.minWidth = "200px";
        columnAdjusted.current = true;
      }
    }

    const onMouseDown = (event: MouseEvent) => {
      const handle = (event.target as HTMLElement).closest(".profit-col-resizer") as HTMLElement | null;
      if (!handle) return;
      const th = handle.closest("th") as HTMLElement | null;
      if (!th) return;
      freezeColumns();
      columnAdjusted.current = true;
      drag = { th, startX: event.clientX, startWidth: th.offsetWidth };
      handle.classList.add("is-dragging");
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      event.preventDefault();
    };

    const onMouseMove = (event: MouseEvent) => {
      if (!drag) return;
      const width = Math.max(64, drag.startWidth + (event.clientX - drag.startX));
      drag.th.style.width = `${width}px`;
      drag.th.style.minWidth = `${width}px`;
    };

    const onMouseUp = () => {
      if (!drag) return;
      drag.th.querySelector(".profit-col-resizer")?.classList.remove("is-dragging");
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      drag = null;
    };

    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, [pageProducts]);

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
    const position = document.caretPositionFromPoint?.(x, y);
    return !!position && position.offsetNode.nodeType === Node.TEXT_NODE;
  };
  const onTablePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    // 列宽拖拽、表单控件、链接、按钮均不触发平移
    if (target.closest(".profit-col-resizer") || target.closest("input, textarea, select, button, a, label")) return;
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

  const toggleSite = (item: ProfitActivitySite, checked: boolean) => {
    setSites((current) => {
      const next = new Set(current);
      if (checked) next.add(item);
      else next.delete(item);
      return next;
    });
  };

  const toggleAllSites = () => {
    setSites(allSitesChecked ? new Set() : new Set(allSites));
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

  const downloadCatalog = () => withBusy("下载产品档案", async () => {
    if (!sites.size) throw new Error("请至少勾选一个站点。");
    await downloadProfitActivityCatalog({ sites: [...sites], scope });
    setMessage("产品档案已开始下载。");
  });

  // 行内即时保存单个字段：回车或点“保存”按钮触发，成功后仅更新本地列表，不重置翻页/选择。
  const saveProductField = async (
    item: ProfitActivityProduct,
    field: "selling_price" | "cost_price" | "weight_kg" | "note",
    rawValue: string,
  ): Promise<boolean | "ignore"> => {
    // 数字字段清空：视为撤销，不提交
    if (field !== "note" && rawValue.trim() === "") return "ignore";
    try {
      await updateProfitActivityProduct({
        site: (item.site || item.site_code || "US") as ProfitActivitySite,
        skc: item.skc,
        [field]: rawValue,
      });
      const nextValue = field === "note" ? rawValue : Number(rawValue);
      setProducts((current) => current.map((product) => (
        productKey(product) === productKey(item) ? { ...product, [field]: nextValue } : product
      )));
      setMessage(`已保存 ${item.skc} 的${editFieldLabels[field]}。`);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      return false;
    }
  };

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
          <div className="profit-site-check-group">
            <label className={`profit-site-master ${allSitesChecked ? "is-active" : ""}`}>
              <input ref={masterRef} type="checkbox" checked={allSitesChecked} onChange={toggleAllSites} />
              <span>全选</span>
            </label>
            {allSites.map((item) => (
              <label key={item} className={`profit-site-check ${sites.has(item) ? "is-active" : ""}`}>
                <input type="checkbox" checked={sites.has(item)} onChange={(event) => toggleSite(item, event.target.checked)} />
                <span>{siteLabels[item]}</span>
              </label>
            ))}
          </div>
          <label>查询范围<select value={scope} onChange={(event) => setScope(event.target.value as ProfitActivityScope)}><option value="default">本人 + 核价入库</option><option value="company">查本公司在档产品</option></select></label>
          <label>每页条数<select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value) as typeof pageSize); setPage(1); }}>{pageSizeOptions.map((value) => <option key={value} value={value}>每页 {value} 条</option>)}</select></label>
        </div>

        <div className="profit-products-query">
          <textarea value={querySkcs} onChange={(event) => setQuerySkcs(event.target.value)} placeholder="输入 SKC，支持换行、空格、逗号批量查询；留空展示数据库里当前权限可见产品" />
          <div className="profit-products-actions">
            <button className="primary-button" onClick={queryProducts} disabled={!!busy}>查询产品</button>
            <button onClick={togglePageSelected} disabled={!pageProducts.length}>{pageSelected ? "取消本页" : "全选本页"}</button>
            <button className="danger-button" onClick={deleteSelected} disabled={!selectedCount || !!busy}>删除已选 {selectedCount}</button>
            <button onClick={downloadCatalog} disabled={!!busy}>下载产品档案</button>
          </div>
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
              <thead>
                <tr><th>选择</th><th>站点</th><th>SKC</th><th>SKC对应图</th><th>售价</th><th>成本</th><th>重量</th><th>利润</th><th>利润率</th><th>备注</th><th>货源</th><th>图片</th></tr>
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
            <thead ref={theadRef}>
              <tr><th>选择<span className="profit-col-resizer" /></th><th>站点<span className="profit-col-resizer" /></th><th>SKC<span className="profit-col-resizer" /></th><th>SKC对应图<span className="profit-col-resizer" /></th><th>售价<span className="profit-col-resizer" /></th><th>成本<span className="profit-col-resizer" /></th><th>重量<span className="profit-col-resizer" /></th><th>利润<span className="profit-col-resizer" /></th><th>利润率<span className="profit-col-resizer" /></th><th>备注<span className="profit-col-resizer" /></th><th>货源<span className="profit-col-resizer" /></th><th>图片<span className="profit-col-resizer" /></th></tr>
            </thead>
            <tbody>
              {pageProducts.length ? pageProducts.map((item) => {
                const key = productKey(item);
                return (
                  <tr key={key}>
                    <td><input type="checkbox" checked={selected.has(key)} onChange={(event) => setSelected(toggleSet(selected, key, event.target.checked))} /></td>
                    <td>{siteLabels[(item.site || item.site_code || "US") as ProfitActivitySite]}</td>
                    <td>{item.skc}{item.source_type === "price_verification" && <em className="profit-source-badge" title="来自核价及货源板块自动入库">核价</em>}</td>
                    <td><ProductImageCell item={item} onChanged={refreshProducts} /></td>
                    <td><EditableCell type="number" value={typeof item.selling_price === "number" ? String(item.selling_price) : ""} onSave={(value) => saveProductField(item, "selling_price", value)} /></td>
                    <td><EditableCell type="number" value={typeof item.cost_price === "number" ? String(item.cost_price) : ""} onSave={(value) => saveProductField(item, "cost_price", value)} /></td>
                    <td><EditableCell type="number" value={typeof item.weight_kg === "number" ? String(item.weight_kg) : ""} onSave={(value) => saveProductField(item, "weight_kg", value)} /></td>
                    <td className={(item.net_profit ?? 0) >= 0 ? "profit-good" : "profit-bad"}>{money(item.net_profit)}</td>
                    <td>{percent(item.profit_rate)}</td>
                    <td><EditableCell type="text" className="profit-note-input" value={item.note ?? ""} onSave={(value) => saveProductField(item, "note", value)} /></td>
                    <td><button className="profit-source-open" onClick={() => setActiveProduct(item)} title="查看/编辑该 SKC 的货源链接">打开（{(item.source_groups ?? []).filter((group) => group?.source_url).length}）</button></td>
                    <td><SourceImagesCell item={item} onPreview={(url) => setPreviewImage({ url, label: `${item.skc} 货源图` })} /></td>
                  </tr>
                );
              }) : (
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

/** SKC 对应图单元格：展示产品主图，支持 Ctrl+V 粘贴或手动上传替换并保存。 */
function ProductImageCell({ item, onChanged }: { item: ProfitActivityProduct; onChanged: () => void }) {
  const site = (item.site || item.site_code || "US") as ProfitActivitySite;
  const fileRef = useRef<HTMLInputElement>(null);
  const [imageUrl, setImageUrl] = useState("");
  const [localPreview, setLocalPreview] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [sourceImageFailed, setSourceImageFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    setError("");
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
          // 新图已从服务器加载，清除粘贴/选择的本地预览
          setLocalPreview("");
        })
        .catch(() => {});
    } else {
      setImageUrl("");
      setLocalPreview("");
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.skc, site, item.image_path, item.source_main_image_url]);

  const displayImage = localPreview || imageUrl || (sourceImageFailed ? "" : item.source_main_image_url || "");

  const upload = async (file: File | undefined) => {
    if (!file) return;
    // 先本地预览，让用户立刻看到粘贴/选择结果；上传完成后保留预览，
    // 直到列表刷新后服务器新图加载出来再切换，避免闪回旧图。
    setLocalPreview(URL.createObjectURL(file));
    setUploading(true);
    setError("");
    try {
      await updateProductImage({
        site,
        skc: item.skc,
        image: file,
        selling_price: item.selling_price,
        cost_price: item.cost_price,
        weight_kg: item.weight_kg,
        note: item.note,
        source_url: item.source_url,
        source_groups: item.source_groups,
      });
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const image = [...event.clipboardData.files].find((item) => item.type.startsWith("image/"));
    if (image) void upload(image);
  };

  return (
    <div className="profit-product-image-cell" tabIndex={0} onPaste={onPaste} title="可 Ctrl+V 粘贴截图，或点击上传">
      {displayImage ? (
        <img
          className="profit-product-image"
          src={displayImage}
          alt={`${item.skc} 主图`}
          referrerPolicy={localPreview || imageUrl ? undefined : "no-referrer"}
          onError={() => {
            if (!localPreview && !imageUrl) setSourceImageFailed(true);
          }}
        />
      ) : (
        <span className="profit-product-image-empty">无图</span>
      )}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(event) => {
          void upload(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <button
        type="button"
        className="profit-product-image-upload"
        disabled={uploading}
        onClick={() => fileRef.current?.click()}
      >
        {uploading ? "上传中…" : displayImage ? "替换" : "上传"}
      </button>
      {error ? <small className="profit-product-image-error" title={error}>保存失败</small> : null}
    </div>
  );
}

/** 图片列：展示标准审核表识别到的货源截图（source_groups 各组图片），点击可放大查看规格。 */
function SourceImagesCell({ item, onPreview }: { item: ProfitActivityProduct; onPreview: (url: string) => void }) {
  const site = (item.site || item.site_code || "US") as ProfitActivitySite;
  const groups = item.source_groups ?? [];
  const imageCount = groups.reduce((sum, group) => sum + (group?.image_paths?.length ?? 0), 0);
  // 图片资源指纹：换图后 image_paths 中的文件路径（uuid 文件名）变化，
  // 用它作依赖，保证保存替换货源图后表格缩略图立即重新加载，而不是复用旧图。
  const imagePathsKey = JSON.stringify(groups.map((group) => group?.image_paths ?? []));
  const [urls, setUrls] = useState<(string | null)[]>([]);

  useEffect(() => {
    let cancelled = false;
    const created: string[] = [];
    if (!imageCount) {
      setUrls([]);
      return;
    }
    const entries: Array<{ group: number; index: number }> = [];
    groups.forEach((group, groupIndex) => {
      (group?.image_paths ?? []).forEach((_, index) => entries.push({ group: groupIndex, index }));
    });
    Promise.all(
      entries.map((entry) =>
        loadProductImage({
          skc: item.skc,
          site,
          kind: "source",
          group: entry.group,
          index: entry.index,
          version: imagePathsKey,
        })
          .then((url) => {
            if (cancelled) {
              URL.revokeObjectURL(url);
              return null;
            }
            created.push(url);
            return url;
          })
          .catch(() => null),
      ),
    ).then((loaded) => {
      if (!cancelled) {
        setUrls(loaded);
        // 调试：打印表格缩略图加载结果（保存换图后应能看到 imagePathsKey 变化并重新加载）
        console.log("[表格缩略图] skc:", item.skc, "| imageCount:", imageCount,
          "| imagePathsKey:", imagePathsKey, "| 加载到图片数:", loaded.filter(Boolean).length);
      }
    });
    return () => {
      cancelled = true;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.skc, site, imageCount, imagePathsKey]);

  if (!imageCount) return <span className="profit-product-image-empty">无图</span>;
  return (
    <div className="profit-source-thumbs">
      {urls.map((url, index) => (
        <button key={index} type="button" className="profit-source-thumb-btn" title="点击查看大图" onClick={() => url && onPreview(url)}>
          {url ? <img className="profit-source-thumb" src={url} alt={`货源图 ${index + 1}`} loading="lazy" /> : null}
        </button>
      ))}
    </div>
  );
}

/**
 * 行内可编辑单元格：聚焦后显示“保存/撤销”按钮；回车或点“保存”即时保存，
 * 保存成功后按钮消失；失焦未保存的修改会还原。
 */
function EditableCell({
  value,
  type = "text",
  className = "",
  onSave,
}: {
  value: string;
  type?: "number" | "text";
  className?: string;
  /** 返回值：true=已保存；false=保存失败（保留编辑态）；"ignore"=无需提交（还原） */
  onSave: (next: string) => Promise<boolean | "ignore">;
}) {
  const [draft, setDraft] = useState(value);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  // 保存进行中失焦时不还原（保存完成后由结果决定编辑态）
  const savingRef = useRef(false);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const dirty = draft !== value;

  const save = async () => {
    if (savingRef.current) return;
    if (!dirty) {
      setEditing(false);
      return;
    }
    savingRef.current = true;
    setSaving(true);
    const result = await onSave(draft);
    savingRef.current = false;
    setSaving(false);
    if (result === true) {
      setEditing(false);
    } else if (result === "ignore") {
      setDraft(value);
      setEditing(false);
    }
    // 失败：保留编辑态与输入内容，顶部提示错误
  };

  const cancel = () => {
    setDraft(value);
    setEditing(false);
  };

  const handleBlur = () => {
    if (savingRef.current) return; // 保存中不打断
    setDraft(value); // 未保存的修改失焦还原
    setEditing(false);
  };

  return (
    <span className={`profit-edit-cell ${className}`}>
      <input
        className={`profit-edit-input ${type === "number" ? "profit-edit-number" : "profit-edit-text"}`}
        type={type}
        step={type === "number" ? "any" : undefined}
        min={type === "number" ? 0 : undefined}
        maxLength={type === "text" ? 500 : undefined}
        placeholder={type === "text" ? "-" : undefined}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onFocus={() => setEditing(true)}
        onBlur={handleBlur}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            void save();
          }
        }}
      />
      {editing ? (
        <span className="profit-edit-actions">
          <button
            type="button"
            className="profit-edit-save"
            disabled={saving}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => void save()}
          >
            {saving ? "保存中…" : "保存"}
          </button>
          <button
            type="button"
            className="profit-edit-cancel"
            disabled={saving}
            onMouseDown={(event) => event.preventDefault()}
            onClick={cancel}
          >
            撤销
          </button>
        </span>
      ) : null}
    </span>
  );
}

function money(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function percent(value: unknown) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}

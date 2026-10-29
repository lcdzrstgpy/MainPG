import { useCallback, useEffect, useMemo, useState, type ClipboardEvent } from "react";
import { createPortal } from "react-dom";
import { SiteRateSettingsDialog } from "../components/SiteRateSettingsDialog";
import {
  sourcingLedgerApi,
  type LedgerBatchOption,
  type LedgerItem,
  type LedgerItemDetail,
  type LedgerSource,
  type LedgerSourcePayload,
  type LedgerStatus,
} from "../api/sourcingLedgerApi";
import "../styles/sourcingLedger.css";

const STATUS_LABELS: Record<LedgerStatus, string> = {
  ready: "可入库",
  low_profit: "利润偏低",
  needs_price: "待采申报价",
  needs_source: "待补货源",
  needs_site: "站点未识别",
  unmatched_source: "货源待关联",
  in_library: "已入产品库",
};

const PENDING_STATUSES: LedgerStatus[] = [
  "needs_price",
  "needs_source",
  "needs_site",
  "unmatched_source",
];

type StatusTab = { key: string; label: string; statuses: LedgerStatus[] };

const STATUS_TABS: StatusTab[] = [
  { key: "", label: "全部", statuses: [] },
  { key: "pending", label: "未采核价", statuses: PENDING_STATUSES },
  { key: "ready", label: "可入库", statuses: ["ready"] },
  { key: "low_profit", label: "利润偏低", statuses: ["low_profit"] },
  { key: "in_library", label: "已入库", statuses: ["in_library"] },
];

type SourceForm = {
  id: string;
  source_url: string;
  source_title: string;
  price_cny: string;
  domestic_freight_cny: string;
  weight_kg: string;
  note: string;
  images: string[];
};

const EMPTY_FORM: SourceForm = {
  id: "",
  source_url: "",
  source_title: "",
  price_cny: "",
  domestic_freight_cny: "",
  weight_kg: "",
  note: "",
  images: [],
};

function money(value?: string) {
  const text = String(value || "").trim();
  if (!text) return "—";
  const number = Number(text);
  if (!Number.isFinite(number)) return text;
  return `¥${number.toFixed(2)}`;
}

function profitText(profit?: Record<string, unknown>) {
  const net = profit?.net_profit;
  const rate = profit?.profit_rate;
  if (net === undefined || net === null || net === "") return "—";
  const netText = money(String(net));
  const rateNumber = Number(rate);
  const rateText = Number.isFinite(rateNumber) ? `${(rateNumber * 100).toFixed(1)}%` : "";
  return rateText ? `${netText} · ${rateText}` : netText;
}

function statusTone(status: LedgerStatus) {
  if (status === "ready") return "is-ready";
  if (status === "low_profit") return "is-warn";
  if (status === "in_library") return "is-done";
  return "is-muted";
}

function pageWindow(current: number, total: number): Array<number | "gap"> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const picked = new Set<number>([1, total, current]);
  [current - 1, current + 1].forEach((value) => picked.add(value));
  if (current <= 4) [2, 3, 4, 5].forEach((value) => picked.add(value));
  if (current >= total - 3) [total - 1, total - 2, total - 3, total - 4].forEach((value) => picked.add(value));
  const pages = [...picked].filter((value) => value >= 1 && value <= total).sort((left, right) => left - right);
  const result: Array<number | "gap"> = [];
  let previous = 0;
  for (const value of pages) {
    if (previous && value - previous > 1) result.push("gap");
    result.push(value);
    previous = value;
  }
  return result;
}

function numberText(value: unknown, fallback = ""): string {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  const number = Number(text);
  return Number.isFinite(number) ? String(number) : text;
}

function formFromSource(source: LedgerSource): SourceForm {
  return {
    id: String(source.id || ""),
    source_url: source.source_url || "",
    source_title: source.source_title || "",
    // 直接展示利润实际用到的数值：运费空按 0、重量空按默认 0.5kg，避免看起来「没算就出利润」
    price_cny: numberText(source.price_cny),
    domestic_freight_cny: numberText(source.domestic_freight_cny, "0"),
    weight_kg: numberText(source.weight_kg, "0.5"),
    note: source.note || "",
    images: [],
  };
}

export function SourcingLedgerPage({ isActive = true }: { isActive?: boolean }) {
  const [items, setItems] = useState<LedgerItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [status, setStatus] = useState("");
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [site, setSite] = useState("");
  const [batchId, setBatchId] = useState("");
  const [batches, setBatches] = useState<LedgerBatchOption[]>([]);
  const [keyword, setKeyword] = useState("");
  const [sites, setSites] = useState<Array<{ site_code: string; display_name: string }>>([]);
  const [detail, setDetail] = useState<LedgerItemDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [form, setForm] = useState<SourceForm | null>(null);
  const [busy, setBusy] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [weightDraft, setWeightDraft] = useState("");
  const [previewImage, setPreviewImage] = useState("");
  const [expandedTitle, setExpandedTitle] = useState("");
  const [rateOpen, setRateOpen] = useState(false);
  const [imageDraft, setImageDraft] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [reloadToken, setReloadToken] = useState(0);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const statusParam = STATUS_TABS.find((tab) => tab.key === status)?.statuses.join(",") ?? "";

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    sourcingLedgerApi
      .listItems({
        status: statusParam,
        site,
        keyword: keyword.trim(),
        batch: batchId,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      })
      .then((payload) => {
        if (cancelled) return;
        const nextTotal = payload.total || 0;
        setItems(payload.items || []);
        setCounts(payload.counts || {});
        setBatches(payload.batches || []);
        setTotal(nextTotal);
        const lastPage = Math.max(1, Math.ceil(nextTotal / pageSize));
        if (page > lastPage) setPage(lastPage);
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
  }, [batchId, isActive, keyword, page, pageSize, reloadToken, site, statusParam]);

  const reload = useCallback(() => {
    setReloadToken((value) => value + 1);
  }, []);

  useEffect(() => {
    void sourcingLedgerApi.listSites().then(setSites).catch(() => setSites([]));
  }, []);

  const changeSite = async (siteCode: string) => {
    if (!detail) return;
    setBusy("site");
    setNotice("");
    setError("");
    try {
      const payload = await sourcingLedgerApi.setItemSite(detail.skc, siteCode);
      setDetail(payload);
      setNotice(
        siteCode
          ? `站点已改为 ${payload.site || siteCode}，利润按该站点费率重算`
          : "已恢复按采集站点自动判断",
      );
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const openDetail = useCallback(async (item: LedgerItem) => {
    setDetailBusy(true);
    setNotice("");
    setError("");
    setForm(null);
    try {
      const payload = await sourcingLedgerApi.getItem(item.skc, item.site);
      setDetail(payload);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDetailBusy(false);
    }
  }, []);

  const reloadDetail = useCallback(async (skc: string, targetSite: string) => {
    const payload = await sourcingLedgerApi.getItem(skc, targetSite);
    setDetail(payload);
    reload();
  }, [reload]);

  const run = async (key: string, action: () => Promise<void>, success: string) => {
    setBusy(key);
    setNotice("");
    setError("");
    try {
      await action();
      setNotice(success);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const saveSource = async () => {
    if (!detail || !form) return;
    const payload: LedgerSourcePayload = {
      source_url: form.source_url.trim(),
      source_title: form.source_title.trim(),
      price_cny: form.price_cny.trim(),
      // 不再让用户填起订量：一律按一件计算，国内运费不再摊销。
      moq: 1,
      domestic_freight_cny: form.domestic_freight_cny.trim(),
      weight_kg: form.weight_kg.trim(),
      note: form.note.trim(),
    };
    const key = form.id ? `source-${form.id}` : "source-new";
    await run(key, async () => {
      if (form.id) {
        await sourcingLedgerApi.updateSource(detail.skc, form.id, payload);
      } else {
        const created = await sourcingLedgerApi.createSource(detail.skc, payload);
        // 新增时先在表单里攒图片，货源创建成功后再逐张挂到这条货源上
        const target = (created.sources || []).find((item) => item.source_url === payload.source_url);
        if (target) {
          for (const imageUrl of form.images) {
            await sourcingLedgerApi.addSourceImage(detail.skc, target.id, imageUrl);
          }
        }
      }
      setForm(null);
      await reloadDetail(detail.skc, detail.site);
    }, form.id ? "货源已更新" : "货源已添加");
  };

  const addFormImage = () => {
    if (!form) return;
    const url = (imageDraft.new || "").trim();
    if (!url) return;
    setForm({ ...form, images: [...form.images, url] });
    setImageDraft((current) => ({ ...current, new: "" }));
  };

  const removeFormImage = (index: number) => {
    if (!form) return;
    setForm({ ...form, images: form.images.filter((_, position) => position !== index) });
  };

  const removeItems = async (skcs: string[]) => {
    if (!skcs.length) return;
    const label = skcs.length === 1 ? `商品 ${skcs[0]}` : `${skcs.length} 个商品`;
    const confirmed = window.confirm(
      `确定把${label}从「货源与利润」删除？\n会同时删除它的货源、图片和申报价记录（不影响已采集的原始商品与上架数据）。`,
    );
    if (!confirmed) return;
    setBusy("delete");
    setNotice("");
    setError("");
    try {
      const result = await sourcingLedgerApi.deleteItems(skcs);
      setNotice(
        `已删除 ${result.deleted.length} 个商品（货源 ${result.sources} 条、图片 ${result.images} 张、申报价 ${result.declared} 条）`,
      );
      setSelected((current) => current.filter((code) => !skcs.includes(code)));
      if (detail && skcs.includes(detail.skc)) setDetail(null);
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const toggleSelect = (skc: string) => {
    setSelected((current) =>
      current.includes(skc) ? current.filter((value) => value !== skc) : [...current, skc],
    );
  };

  const pageSelected = items.length > 0 && items.every((item) => selected.includes(item.skc));

  const toggleSelectPage = () => {
    setSelected((current) => {
      if (pageSelected) return current.filter((value) => !items.some((item) => item.skc === value));
      return [...new Set([...current, ...items.map((item) => item.skc)])];
    });
  };

  const applyWeights = async () => {
    if (!selected.length) {
      setError("请先勾选要填重量的商品");
      return;
    }
    setBusy("weights");
    setNotice("");
    setError("");
    try {
      const result = await sourcingLedgerApi.setWeights(selected, weightDraft.trim());
      setNotice(`已给 ${selected.length} 个商品的 ${result.updated} 条货源填入 ${result.weight_kg} kg，利润已按新重量重算`);
      setSelected([]);
      setWeightDraft("");
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const addImage = async (source: LedgerSource) => {
    await addImageUrl(source, (imageDraft[source.id] || "").trim());
  };

  const addImageUrl = async (source: LedgerSource, url: string) => {
    if (!detail || !url) return;
    setBusy(`image-${source.id}`);
    setNotice("");
    setError("");
    try {
      const payload = await sourcingLedgerApi.addSourceImage(detail.skc, source.id, url);
      setDetail(payload);
      setImageDraft((current) => ({ ...current, [source.id]: "" }));
      setNotice("图片已添加");
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const readImageFile = async (file: File | null): Promise<string> => {
    if (!file) return "";
    if (!file.type.startsWith("image/")) {
      setError("请选择图片文件");
      return "";
    }
    if (file.size > 2 * 1024 * 1024) {
      setError("图片不能超过 2MB，请压缩后再上传");
      return "";
    }
    try {
      return await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("读取图片失败"));
        reader.readAsDataURL(file);
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return "";
    }
  };

  const uploadImage = async (source: LedgerSource, file: File | null) => {
    const dataUrl = await readImageFile(file);
    if (dataUrl) await addImageUrl(source, dataUrl);
  };

  const uploadFormImage = async (file: File | null) => {
    const dataUrl = await readImageFile(file);
    if (dataUrl && form) setForm({ ...form, images: [...form.images, dataUrl] });
  };

  const removeImage = async (source: LedgerSource, imageId: string) => {
    if (!detail) return;
    setBusy(`image-${source.id}`);
    setNotice("");
    setError("");
    try {
      const payload = await sourcingLedgerApi.removeSourceImage(detail.skc, source.id, imageId);
      setDetail(payload);
      setNotice("图片已删除");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const removeMainImage = async (source: LedgerSource) => {
    if (!detail) return;
    if (!window.confirm("删除这条货源的主图？删除后可用「上传图片」或 Ctrl+V 重新添加。")) return;
    setBusy(`image-${source.id}`);
    setNotice("");
    setError("");
    try {
      const payload = await sourcingLedgerApi.removeSourceMainImage(detail.skc, source.id);
      setDetail(payload);
      setNotice("主图已删除");
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const pasteImage = async (event: ClipboardEvent, apply: (url: string) => void) => {
    const file = [...event.clipboardData.files].find((item) => item.type.startsWith("image/"));
    if (!file) return;
    event.preventDefault();
    const url = await readImageFile(file);
    if (url) apply(url);
  };

  const bestSource = useMemo(() => {
    if (!detail) return null;
    const sources = detail.sources || [];
    if (!sources.length) return null;
    const primary = sources.find((source) => Boolean(source.is_primary));
    return primary || sources[0];
  }, [detail]);

  const editingSource = useMemo(
    () => (detail && form?.id ? detail.sources.find((item) => item.id === form.id) || null : null),
    [detail, form?.id],
  );

  return (
    <div className="ledger-page">
      <section className="ledger-hero">
        <div className="ledger-hero-main">
          <h1>货源与利润</h1>
          <p>采集货源 → 上架 → Temu 申报价 → 利润核算 → 入产品库</p>
        </div>
        <div className="ledger-hero-actions">
          <button type="button" className="ledger-hero-btn" onClick={() => setRateOpen(true)}>
            站点费率设置
          </button>
          <button type="button" className="ledger-hero-btn" onClick={reload} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
        </div>
      </section>

      <div className="ledger-toolbar">
        <nav className="ledger-status-tabs">
          {STATUS_TABS.map((tab) => {
            const count = tab.statuses.length
              ? tab.statuses.reduce((sum, value) => sum + (counts[value] || 0), 0)
              : Object.values(counts).reduce((sum, value) => sum + value, 0);
            const active = tab.key === status;
            const hint =
              tab.key === "pending"
                ? PENDING_STATUSES.map((value) => `${STATUS_LABELS[value]} ${counts[value] || 0}`).join(" · ")
                : tab.key === "ready"
                  ? "净利润 ≥ ¥10 即可入库"
                  : tab.key === "low_profit"
                    ? "已采核价，但净利润 < ¥10"
                    : undefined;
            return (
              <button
                key={tab.key || "all"}
                type="button"
                className={`ledger-tab${active ? " is-active" : ""}`}
                title={hint}
                onClick={() => {
                  setStatus(tab.key);
                  setPage(1);
                }}
              >
                {tab.label}
                <em>{count}</em>
              </button>
            );
          })}
        </nav>

        <div className="ledger-toolbar-actions">
          <div className="ledger-batch">
            <span className="ledger-batch-title">批量重量（kg）</span>
            <input
              value={weightDraft}
              placeholder="如 0.8"
              onChange={(event) => setWeightDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void applyWeights();
              }}
            />
            <button
              type="button"
              className="ledger-btn is-primary"
              onClick={() => void applyWeights()}
              disabled={Boolean(busy) || !selected.length}
            >
              {busy === "weights" ? "应用中…" : `应用到已选 ${selected.length} 个`}
            </button>
          </div>
          {selected.length > 0 && (
            <button
              type="button"
              className="ledger-btn is-danger"
              onClick={() => void removeItems(selected)}
              disabled={Boolean(busy)}
            >
              {busy === "delete" ? "删除中…" : `删除已选 ${selected.length}`}
            </button>
          )}
        </div>
      </div>

      <section className="ledger-filters">
        <label>
          <span>站点</span>
          <select
            value={site}
            onChange={(event) => {
              setSite(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部站点</option>
            {sites.map((option) => (
              <option key={option.site_code} value={option.site_code}>{option.display_name}</option>
            ))}
          </select>
        </label>
        <label className="ledger-batch-filter">
          <span>采集批次</span>
          <select
            value={batchId}
            onChange={(event) => {
              setBatchId(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部批次</option>
            {batches.map((option) => (
              <option key={option.batch_id} value={option.batch_id}>
                {option.label}（{option.count}）
              </option>
            ))}
          </select>
        </label>
        <label className="ledger-keyword">
          <span>关键词</span>
          <input
            value={keyword}
            placeholder="货号 / SKC / 标题"
            onChange={(event) => {
              setKeyword(event.target.value);
              setPage(1);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") reload();
            }}
          />
        </label>
      </section>

      {notice && <p className="ledger-notice">{notice}</p>}
      {error && <p className="ledger-error">{error}</p>}

      <div className="ledger-table-wrap">
        <table className="ledger-table">
          <thead>
            <tr>
              <th className="ledger-check">
                <input
                  type="checkbox"
                  checked={pageSelected}
                  onChange={toggleSelectPage}
                  aria-label="全选当前页"
                />
              </th>
              <th className="ledger-col-product">商品</th>
              <th className="ledger-col-site">站点</th>
              <th className="ledger-col-source">货源</th>
              <th className="ledger-col-num">到手成本</th>
              <th className="ledger-col-num">申报价（调整后）</th>
              <th className="ledger-col-num">
                利润
                <small className="ledger-th-hint" title="货源未填重量时，利润按默认 0.5kg 计算">
                  按 0.5kg 估算
                </small>
              </th>
              <th className="ledger-col-status">状态</th>
              <th className="ledger-col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={`${item.skc}-${item.site}`} className={selected.includes(item.skc) ? "is-selected" : ""}>
                <td className="ledger-check">
                  <input
                    type="checkbox"
                    checked={selected.includes(item.skc)}
                    onChange={() => toggleSelect(item.skc)}
                    aria-label={`选择 ${item.skc}`}
                  />
                </td>
                <td className="ledger-cell-product ledger-col-product">
                  <div className="ledger-product-inner">
                    {item.image_url ? (
                      <button
                        type="button"
                        className="ledger-product-thumb"
                        title="点击查看大图"
                        onClick={() => setPreviewImage(item.image_url)}
                      >
                        <img src={item.image_url} alt="" referrerPolicy="no-referrer" />
                      </button>
                    ) : (
                      <i />
                    )}
                    <div className="ledger-product-text">
                      <button
                        type="button"
                        className={`ledger-title${
                          expandedTitle === `${item.skc}-${item.site}` ? " is-expanded" : ""
                        }`}
                        title={expandedTitle === `${item.skc}-${item.site}` ? "点击收起" : "点击展开完整标题"}
                        onClick={() =>
                          setExpandedTitle((current) =>
                            current === `${item.skc}-${item.site}` ? "" : `${item.skc}-${item.site}`,
                          )
                        }
                      >
                        {item.title || item.skc}
                      </button>
                      <small>
                        {item.skc}
                        {item.market_skc_id ? ` · SKC ${item.market_skc_id}` : ""}
                        {item.market_skc_code && item.market_skc_code !== item.skc
                          ? ` · 平台 ${item.market_skc_code}`
                          : ""}
                        {item.sku_count ? ` · ${item.sku_count} 个 SKU` : ""}
                      </small>
                    </div>
                  </div>
                </td>
                <td className="ledger-col-site">{item.site ? item.site.replace(/站$/, "") : "—"}</td>
                <td className="ledger-col-source">{item.source_count ? `${item.source_count} 条` : "—"}</td>
                <td className="ledger-col-num">{money(item.best_source?.landed_cost_cny)}</td>
                <td className="ledger-col-num">
                  {money(item.adjusted_min)}
                  {item.original_min && item.original_min !== item.adjusted_min ? (
                    <small className="ledger-muted"> 原 {money(item.original_min)}</small>
                  ) : null}
                </td>
                <td className="ledger-col-num">{profitText(item.profit)}</td>
                <td className="ledger-col-status">
                  <span className={`ledger-status ${statusTone(item.status)}`}>
                    {STATUS_LABELS[item.status] || item.status}
                  </span>
                </td>
                <td className="ledger-col-actions">
                  <button type="button" className="ledger-link" onClick={() => void openDetail(item)}>
                    货源 / 利润
                  </button>
                  <button
                    type="button"
                    className="ledger-link is-danger"
                    onClick={() => void removeItems([item.skc])}
                    disabled={Boolean(busy)}
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
            {!items.length && !loading && (
              <tr>
                <td colSpan={9} className="ledger-empty">
                  没有商品数据。先在「采集」里采集商品，再用插件采集 Temu 申报价。
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {total > 0 && (
        <div className="ledger-pager">
          <span className="ledger-pager-info">
            第 {page} / {totalPages} 页 · 共 {total} 个商品 · 最近更新优先
          </span>
          <div className="ledger-pager-actions">
            <select
              className="ledger-pager-size"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
              disabled={loading}
            >
              {[20, 50, 100, 200].map((size) => (
                <option key={size} value={size}>{size} 条/页</option>
              ))}
            </select>
            <button type="button" className="ledger-btn" onClick={() => setPage(1)} disabled={loading || page <= 1}>
              首页
            </button>
            <button type="button" className="ledger-btn" onClick={() => setPage(page - 1)} disabled={loading || page <= 1}>
              上一页
            </button>
            {pageWindow(page, totalPages).map((value, index) =>
              value === "gap" ? (
                <span key={`gap-${index}`} className="ledger-pager-gap">…</span>
              ) : (
                <button
                  key={value}
                  type="button"
                  className={`ledger-btn ledger-pager-page${value === page ? " is-primary" : ""}`}
                  onClick={() => setPage(value)}
                  disabled={loading || value === page}
                >
                  {value}
                </button>
              ),
            )}
            <button
              type="button"
              className="ledger-btn"
              onClick={() => setPage(page + 1)}
              disabled={loading || page >= totalPages}
            >
              下一页
            </button>
            <button
              type="button"
              className="ledger-btn"
              onClick={() => setPage(totalPages)}
              disabled={loading || page >= totalPages}
            >
              末页
            </button>
          </div>
        </div>
      )}

      {detail &&
        createPortal(
          <div className="ledger-drawer-mask" onClick={() => setDetail(null)}>
            <aside className="ledger-drawer" onClick={(event) => event.stopPropagation()}>
              <header>
                <div className="ledger-detail-head">
                  <strong>{detail.title || detail.skc}</strong>
                  <small>
                    {detail.skc}
                    {detail.market_skc_id ? ` · SKC ${detail.market_skc_id}` : ""}
                    {detail.adjusted_min ? ` · 调整后 ${money(detail.adjusted_min)}` : ""}
                  </small>
                  <label className="ledger-site-picker">
                    <span>站点</span>
                    <select
                      value={detail.site_code || ""}
                      disabled={busy === "site"}
                      onChange={(event) => void changeSite(event.target.value)}
                    >
                      <option value="">按采集自动</option>
                      {detail.sites.map((option) => (
                        <option key={option.site_code} value={option.site_code}>
                          {option.display_name}
                        </option>
                      ))}
                    </select>
                    <small>采集来源：{detail.site || "未识别"}</small>
                  </label>
                </div>
                <button type="button" className="ledger-close" onClick={() => setDetail(null)}>×</button>
              </header>

              <section className="ledger-section">
              <div className="ledger-section-head">
                <h3>货源（{detail.sources.length}）</h3>
                <button
                  type="button"
                  className="ledger-btn"
                  onClick={() => setForm({ ...EMPTY_FORM })}
                  disabled={Boolean(busy)}
                >
                  新增货源
                </button>
              </div>
              <div className="ledger-source-list">
                {detail.sources.map((source) => (
                  <article key={source.id} className={`ledger-source${source.is_primary ? " is-primary" : ""}`}>
                    <div className="ledger-source-body">
                      <div className="ledger-source-main">
                      <a href={source.source_url} target="_blank" rel="noreferrer">
                        {source.source_title || source.offer_id || source.source_url}
                      </a>
                      <small>
                        单价 {money(source.price_cny)} · 运费 {money(source.domestic_freight_cny)} · 到手{" "}
                        {money(source.landed_cost_cny)}
                        {source.weight_kg ? ` · ${source.weight_kg}kg` : " · 按 0.5kg 估算"}
                      </small>
                      <small>利润 {profitText(source.profit as Record<string, unknown>)}</small>
                    </div>

                    <div
                      className="ledger-thumbs"
                      tabIndex={0}
                      title="点一下这里，再按 Ctrl+V 可粘贴图片"
                      onPaste={(event) =>
                        void pasteImage(event, (url) => void addImageUrl(source, url))
                      }
                    >
                      {source.main_image_url ? (
                        <span className="ledger-thumb-wrap">
                          <button
                            type="button"
                            className="ledger-thumb"
                            title="点击查看大图"
                            onClick={() => setPreviewImage(String(source.main_image_url))}
                          >
                            <img src={source.main_image_url} alt="" referrerPolicy="no-referrer" />
                          </button>
                          <button
                            type="button"
                            className="ledger-thumb-del"
                            title="删除主图"
                            onClick={() => void removeMainImage(source)}
                            disabled={Boolean(busy)}
                          >
                            ×
                          </button>
                        </span>
                      ) : null}
                      {(source.images || []).map((image) => (
                        <span key={image.id} className="ledger-thumb-wrap">
                          <button
                            type="button"
                            className="ledger-thumb"
                            title="点击查看大图"
                            onClick={() => setPreviewImage(image.image_url)}
                          >
                            <img src={image.image_url} alt="" referrerPolicy="no-referrer" />
                          </button>
                          <button
                            type="button"
                            className="ledger-thumb-del"
                            title="删除图片"
                            onClick={() => void removeImage(source, image.id)}
                            disabled={Boolean(busy)}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>

                    <div className="ledger-thumb-add">
                      <input
                        value={imageDraft[source.id] || ""}
                        placeholder="粘贴图片链接 https://…"
                        onChange={(event) =>
                          setImageDraft((current) => ({ ...current, [source.id]: event.target.value }))
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter") void addImage(source);
                        }}
                      />
                      <button
                        type="button"
                        className="ledger-link"
                        onClick={() => void addImage(source)}
                        disabled={Boolean(busy) || !(imageDraft[source.id] || "").trim()}
                      >
                        {busy === `image-${source.id}` ? "处理中…" : "添加链接"}
                      </button>
                      <label className="ledger-link ledger-upload">
                        上传图片
                        <input
                          type="file"
                          accept="image/*"
                          disabled={Boolean(busy)}
                          onChange={(event) => {
                            const file = event.target.files?.[0] || null;
                            event.target.value = "";
                            void uploadImage(source, file);
                          }}
                        />
                      </label>
                    </div>
                    </div>

                    <div className="ledger-source-actions">
                      <button
                        type="button"
                        className="ledger-link"
                        onClick={() => setForm(formFromSource(source))}
                        disabled={Boolean(busy)}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        className="ledger-link"
                        onClick={() =>
                          void run(`primary-${source.id}`, async () => {
                            await sourcingLedgerApi.setPrimarySource(detail.skc, source.id);
                            await reloadDetail(detail.skc, detail.site);
                          }, "已设为默认货源")
                        }
                        disabled={Boolean(busy) || Boolean(source.is_primary)}
                      >
                        {source.is_primary ? "默认" : "设为默认"}
                      </button>
                      <button
                        type="button"
                        className="ledger-link is-danger"
                        onClick={() =>
                          void run(`remove-${source.id}`, async () => {
                            await sourcingLedgerApi.deleteSource(detail.skc, source.id);
                            await reloadDetail(detail.skc, detail.site);
                          }, "货源已删除")
                        }
                        disabled={Boolean(busy)}
                      >
                        删除
                      </button>
                    </div>
                  </article>
                ))}
                {!detail.sources.length && <p className="ledger-empty">还没有货源，先添加一条 1688 链接。</p>}
              </div>
            </section>

            {form && (
              <section className="ledger-section ledger-form">
                <h3>{form.id ? "编辑货源" : "新增货源"}</h3>
                <label>
                  <span>1688 链接</span>
                  <input
                    value={form.source_url}
                    placeholder="https://detail.1688.com/offer/..."
                    onChange={(event) => setForm({ ...form, source_url: event.target.value })}
                  />
                </label>
                <label>
                  <span>货源标题</span>
                  <input value={form.source_title} onChange={(event) => setForm({ ...form, source_title: event.target.value })} />
                </label>
                <div className="ledger-form-row">
                  <label>
                    <span>单价（元）</span>
                    <input value={form.price_cny} onChange={(event) => setForm({ ...form, price_cny: event.target.value })} />
                  </label>
                  <label>
                    <span>国内运费</span>
                    <input
                      value={form.domestic_freight_cny}
                      onChange={(event) => setForm({ ...form, domestic_freight_cny: event.target.value })}
                    />
                  </label>
                  <label>
                    <span>重量 kg</span>
                    <input value={form.weight_kg} onChange={(event) => setForm({ ...form, weight_kg: event.target.value })} />
                  </label>
                </div>
                {!form.id && (
                  <div
                    className="ledger-form-images"
                    tabIndex={0}
                    title="可直接 Ctrl+V 粘贴图片"
                    onPaste={(event) =>
                      void pasteImage(event, (url) =>
                        setForm((current) =>
                          current ? { ...current, images: [...current.images, url] } : current,
                        ),
                      )
                    }
                  >
                    <span className="ledger-form-label">货源图片（{form.images.length}）</span>
                    {form.images.length ? (
                      <div className="ledger-thumbs">
                        {form.images.map((url, index) => (
                          <span key={`${url.slice(0, 32)}-${index}`} className="ledger-thumb-wrap">
                            <button
                              type="button"
                              className="ledger-thumb"
                              title="点击查看大图"
                              onClick={() => setPreviewImage(url)}
                            >
                              <img src={url} alt="" referrerPolicy="no-referrer" />
                            </button>
                            <button
                              type="button"
                              className="ledger-thumb-del"
                              title="删除图片"
                              onClick={() => removeFormImage(index)}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    ) : null}
                    <div className="ledger-thumb-add">
                      <input
                        value={imageDraft.new || ""}
                        placeholder="粘贴图片链接 https://…"
                        onChange={(event) =>
                          setImageDraft((current) => ({ ...current, new: event.target.value }))
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            addFormImage();
                          }
                        }}
                      />
                      <button
                        type="button"
                        className="ledger-link"
                        onClick={addFormImage}
                        disabled={!(imageDraft.new || "").trim()}
                      >
                        添加链接
                      </button>
                      <label className="ledger-link ledger-upload">
                        上传图片
                        <input
                          type="file"
                          accept="image/*"
                          onChange={(event) => {
                            const file = event.target.files?.[0] || null;
                            event.target.value = "";
                            void uploadFormImage(file);
                          }}
                        />
                      </label>
                    </div>
                  </div>
                )}
                <p className="ledger-hint">
                  到手成本 = 单价 + 国内运费（起订量按 1 件，不再摊销）。默认按到手成本最低的一条核算利润，可手动设为默认。
                </p>
                {editingSource?.weight_estimated ? (
                  <p className="ledger-hint">
                    这条货源原本没填重量，利润按默认 0.5kg 计算，已把 0.5 填在上方；改成实际重量后保存即可。
                  </p>
                ) : null}
                <div className="ledger-form-actions">
                  <button type="button" className="ledger-btn is-primary" onClick={() => void saveSource()} disabled={Boolean(busy)}>
                    {busy === "source-new" || busy.startsWith("source-") ? "保存中…" : "保存"}
                  </button>
                  <button type="button" className="ledger-btn" onClick={() => setForm(null)} disabled={Boolean(busy)}>
                    取消
                  </button>
                </div>
              </section>
            )}

            {bestSource && (
              <section className="ledger-section">
                <h3>利润构成（{detail.site_code || "站点未识别"}）</h3>
                <dl className="ledger-breakdown">
                  <div>
                    <dt>申报价（调整后最小）</dt>
                    <dd>{money(detail.adjusted_min)}</dd>
                  </div>
                  <div>
                    <dt>到手成本</dt>
                    <dd>
                      单价 {money(bestSource.price_cny)} + 国内运费 {money(bestSource.domestic_freight_cny)} ={" "}
                      {money(bestSource.landed_cost_cny)}
                    </dd>
                  </div>
                  <div>
                    <dt>重量（首程/尾程按它分档）</dt>
                    <dd>
                      {bestSource.weight_kg ? `${bestSource.weight_kg} kg` : "未填，按 0.5kg 估算"}
                      {bestSource.weight_estimated ? (
                        <span className="ledger-estimate">按 0.5kg 估算</span>
                      ) : null}
                    </dd>
                  </div>
                  <div>
                    <dt>首程运费</dt>
                    <dd>{money(String((bestSource.profit as Record<string, unknown>)?.shipping_cost ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>国内操作费</dt>
                    <dd>{money(String((bestSource.profit as Record<string, unknown>)?.domestic_fee ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>尾程费</dt>
                    <dd>{money(String((bestSource.profit as Record<string, unknown>)?.end_fee ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>运费补贴</dt>
                    <dd>+{money(String((bestSource.profit as Record<string, unknown>)?.shipping_subsidy ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>总成本</dt>
                    <dd>{money(String((bestSource.profit as Record<string, unknown>)?.total_cost ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>毛利润</dt>
                    <dd>{money(String((bestSource.profit as Record<string, unknown>)?.gross_profit ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>净利润</dt>
                    <dd>{money(String((bestSource.profit as Record<string, unknown>)?.net_profit ?? ""))}</dd>
                  </div>
                  <div>
                    <dt>利润率（净利润 ÷ 总成本）</dt>
                    <dd>
                      {(() => {
                        const rate = Number((bestSource.profit as Record<string, unknown>)?.profit_rate);
                        return Number.isFinite(rate) ? `${(rate * 100).toFixed(2)}%` : "—";
                      })()}
                    </dd>
                  </div>
                </dl>
                <p className="ledger-hint">
                  费率来自「站点费率设置」，与「利润活动」共用；保存后两端与产品库同步重算。
                </p>
              </section>
            )}

            <section className="ledger-section">
              <h3>申报价明细（{detail.declared_prices.length}）</h3>
              <div className="ledger-price-table">
                <table>
                  <thead>
                    <tr>
                      <th>SKU 货号</th>
                      <th>SKU 属性</th>
                      <th>原申报价</th>
                      <th>调整后</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.declared_prices.map((row, index) => (
                      <tr key={`${row.sku_id}-${index}`}>
                        <td>{row.sku_merchant_code || row.sku_id || "—"}</td>
                        <td title={row.sku_attribute_text}>{row.sku_attribute_text || "—"}</td>
                        <td>{money(row.original_price_cny)}</td>
                        <td>{money(row.adjusted_price_cny)}</td>
                      </tr>
                    ))}
                    {!detail.declared_prices.length && (
                      <tr>
                        <td colSpan={4} className="ledger-empty">还没有采集到申报价</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <footer className="ledger-drawer-foot">
              <div>
                <strong>利润 {profitText(bestSource?.profit as Record<string, unknown>)}</strong>
                <small>
                  {bestSource
                    ? `按货源 ${money(bestSource.landed_cost_cny)} 计算`
                    : "先添加货源并采集申报价"}
                </small>
              </div>
              {detail.in_library ? (
                <button
                  type="button"
                  className="ledger-btn"
                  onClick={() =>
                    void run("leave", async () => {
                      await sourcingLedgerApi.leaveLibrary(detail.skc, detail.site);
                      await reloadDetail(detail.skc, detail.site);
                    }, "已从产品库移除")
                  }
                  disabled={Boolean(busy)}
                >
                  从产品库移除
                </button>
              ) : (
                <button
                  type="button"
                  className="ledger-btn is-primary"
                  onClick={() =>
                    void run("enter", async () => {
                      await sourcingLedgerApi.enterLibrary(
                        detail.skc,
                        detail.site,
                        String(bestSource?.id || ""),
                      );
                      await reloadDetail(detail.skc, detail.site);
                    }, "已入产品库")
                  }
                  disabled={Boolean(busy) || !bestSource || !detail.adjusted_min}
                >
                  入产品库
                </button>
              )}
            </footer>
            {detailBusy && <p className="ledger-hint">读取中…</p>}
            </aside>
          </div>,
          document.body,
        )}

      {previewImage &&
        createPortal(
          <div className="ledger-lightbox" onClick={() => setPreviewImage("")}>
            <img src={previewImage} alt="" referrerPolicy="no-referrer" />
            <button
              type="button"
              className="ledger-lightbox-close"
              onClick={() => setPreviewImage("")}
            >
              ×
            </button>
          </div>,
          document.body,
        )}

      <SiteRateSettingsDialog
        open={rateOpen}
        onClose={() => setRateOpen(false)}
        onSaved={(message) => {
          setNotice(message);
          reload();
        }}
      />
    </div>
  );
}

export default SourcingLedgerPage;

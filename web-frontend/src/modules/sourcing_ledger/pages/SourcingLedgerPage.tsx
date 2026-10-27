import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ClipboardEvent,
  type DragEvent,
} from "react";
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
  { key: "priced", label: "已核价", statuses: ["ready", "low_profit"] },
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

/** 采集插件偶尔把整块价格审核弹窗文字塞进货源标题，展示时按垃圾文本丢弃。 */
function sourceTitleText(source: LedgerSource): string {
  const text = String(source.source_title || "").trim();
  if (!text) return "";
  const junk =
    text.includes("采集来源") ||
    (text.includes("移除") && text.includes("SKU")) ||
    text.length > 120;
  return junk ? "" : text;
}

/** 抽屉打开即编辑：默认编辑主货源，没有主货源就用第一条。 */
function editFormFor(payload: LedgerItemDetail): SourceForm | null {
  const source = payload.sources.find((item) => Boolean(item.is_primary)) || payload.sources[0];
  return source ? formFromSource(source) : null;
}

function formFromSource(source: LedgerSource): SourceForm {
  return {
    id: String(source.id || ""),
    source_url: source.source_url || "",
    source_title: sourceTitleText(source),
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
  const [storeDraft, setStoreDraft] = useState("");
  const [storeOpen, setStoreOpen] = useState(false);
  const [batchId, setBatchId] = useState("");
  const [batches, setBatches] = useState<LedgerBatchOption[]>([]);
  const [storeFilter, setStoreFilter] = useState("");
  const [storeOptions, setStoreOptions] = useState<string[]>([]);
  const [storeChoices, setStoreChoices] = useState<string[]>([]);
  const [keyword, setKeyword] = useState("");
  const [sites, setSites] = useState<Array<{ site_code: string; display_name: string }>>([]);
  const [detail, setDetail] = useState<LedgerItemDetail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [form, setForm] = useState<SourceForm | null>(null);
  const [busy, setBusy] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [weightDraft, setWeightDraft] = useState("");
  const [batchStoreDraft, setBatchStoreDraft] = useState("");
  const [previewImage, setPreviewImage] = useState("");
  const [expandedTitle, setExpandedTitle] = useState("");
  const [rateOpen, setRateOpen] = useState(false);
  const [imageDraft, setImageDraft] = useState<Record<string, string>>({});
  const [manualPriceDraft, setManualPriceDraft] = useState("");
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
        store: storeFilter,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      })
      .then((payload) => {
        if (cancelled) return;
        const nextTotal = payload.total || 0;
        setItems(payload.items || []);
        setCounts(payload.counts || {});
        setBatches(payload.batches || []);
        setStoreOptions(payload.stores || []);
        setStoreChoices(payload.store_names || []);
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
  }, [batchId, isActive, keyword, page, pageSize, reloadToken, site, statusParam, storeFilter]);

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

  const choosePriceSku = async (skuRef: string) => {
    if (!detail) return;
    await run("price-selection", async () => {
      const payload = await sourcingLedgerApi.setPriceSelection(detail.skc, skuRef);
      setDetail(payload);
      reload();
    }, skuRef ? "已改用该 SKU 的申报价" : "已恢复默认最低申报价");
  };

  const removeDeclaredPrice = async (rowId: string) => {
    if (!detail || !rowId) return;
    if (!window.confirm("删除这个 SKU 的申报价？删除后不再参与最低价与利润计算。")) return;
    await run("declared-delete", async () => {
      const payload = await sourcingLedgerApi.deleteDeclaredPrice(detail.skc, rowId);
      setDetail(payload);
      reload();
    }, "SKU 已删除");
  };

  const saveManualPrice = async () => {
    if (!detail) return;
    const price = manualPriceDraft.trim();
    if (!price) {
      setError("请填写申报价");
      return;
    }
    await run("manual-price", async () => {
      const payload = await sourcingLedgerApi.setManualPrice(detail.skc, price);
      setDetail(payload);
      reload();
    }, "申报价已保存，利润已按新价格重算");
  };

  const openDetail = useCallback(async (item: LedgerItem) => {
    setDetailBusy(true);
    setNotice("");
    setError("");
    setForm(null);
    try {
      const payload = await sourcingLedgerApi.getItem(item.skc, item.site);
      setDetail(payload);
      // 打开抽屉即可直接编辑：默认展开主货源的成本表单，不再需要先点「编辑」。
      setForm(editFormFor(payload));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDetailBusy(false);
    }
  }, []);

  const reloadDetail = useCallback(async (skc: string, targetSite: string) => {
    const payload = await sourcingLedgerApi.getItem(skc, targetSite);
    setDetail(payload);
    setForm(editFormFor(payload));
    reload();
  }, [reload]);

  useEffect(() => {
    setStoreDraft(detail?.store_name || "");
  }, [detail?.skc, detail?.store_name]);

  useEffect(() => {
    const manual = (detail?.declared_prices || []).find((row) => row.manual);
    setManualPriceDraft(manual ? manual.adjusted_price_cny : "");
  }, [detail]);

  const saveStore = async (value: string) => {
    if (!detail) return;
    const next = value.trim();
    if (next === (detail.store_name || "")) return;
    await run("store", async () => {
      const payload = await sourcingLedgerApi.setItemStore(detail.skc, next);
      setDetail(payload);
      reload();
    }, next ? `店铺已设为 ${next}` : "已清空店铺");
  };

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

  const applyStores = async () => {
    if (!selected.length) {
      setError("请先勾选要设置店铺的商品");
      return;
    }
    const name = batchStoreDraft.trim();
    if (!name) {
      setError("请填写店铺");
      return;
    }
    setBusy("stores");
    setNotice("");
    setError("");
    try {
      const result = await sourcingLedgerApi.setStores(selected, name);
      setNotice(`已把 ${result.updated} 个商品的店铺设为「${result.store_name}」`);
      reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const replaceMainImage = async (source: LedgerSource, url: string) => {
    if (!detail || !url) return;
    setBusy(`image-${source.id}`);
    setNotice("");
    setError("");
    try {
      const payload = await sourcingLedgerApi.setSourceMainImage(detail.skc, source.id, url);
      setDetail(payload);
      setNotice("货源图片已更新");
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

  const uploadMainImage = async (source: LedgerSource, file: File | null) => {
    const dataUrl = await readImageFile(file);
    if (dataUrl) await replaceMainImage(source, dataUrl);
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
      <header className="ledger-head">
        <div>
          <h1>货源与利润</h1>
          <p>采集货源 → 上架 → Temu 申报价 → 利润核算 → 入产品库</p>
        </div>
        <div className="ledger-head-actions">
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
          <button type="button" className="ledger-btn" onClick={() => setRateOpen(true)}>
            站点费率设置
          </button>
          <button type="button" className="ledger-btn" onClick={reload} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
        </div>
        <div className="ledger-batch-bar">
          <span className="ledger-total">共 {total} 个商品 · 利润从高到低</span>
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
          <div className="ledger-batch ledger-batch-store">
            <span className="ledger-batch-title">批量店铺</span>
            <select
              className="ledger-batch-store-select"
              value={batchStoreDraft}
              onChange={(event) => setBatchStoreDraft(event.target.value)}
            >
              <option value="">选择店铺</option>
              {storeChoices.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
            <button
              type="button"
              className="ledger-btn is-primary"
              onClick={() => void applyStores()}
              disabled={Boolean(busy) || !selected.length || !batchStoreDraft.trim()}
            >
              {busy === "stores" ? "应用中…" : `应用到已选 ${selected.length} 个`}
            </button>
          </div>
        </div>
      </header>

      <nav className="ledger-status-tabs">
        {STATUS_TABS.map((tab) => {
          const count = tab.statuses.length
            ? tab.statuses.reduce((sum, value) => sum + (counts[value] || 0), 0)
            : Object.values(counts).reduce((sum, value) => sum + value, 0);
          const active = tab.key === status;
          const hint =
            tab.key === "pending"
              ? PENDING_STATUSES.map((value) => `${STATUS_LABELS[value]} ${counts[value] || 0}`).join(" · ")
              : tab.key === "priced"
                ? "净利润 ≥ ¥10 可入库，低于 ¥10 标为利润偏低"
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
        <label className="ledger-store-filter">
          <span>店铺</span>
          <select
            value={storeFilter}
            onChange={(event) => {
              setStoreFilter(event.target.value);
              setPage(1);
            }}
          >
            <option value="">全部店铺</option>
            {storeOptions.map((name) => (
              <option key={name} value={name}>{name}</option>
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
              <th>商品</th>
              <th>站点</th>
              <th>店铺</th>
              <th>到手成本</th>
              <th>申报价（调整后）</th>
              <th>利润</th>
              <th>状态</th>
              <th>操作</th>
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
                <td className="ledger-cell-product">
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
                </td>
                <td className="ledger-site-cell">{item.site ? item.site.replace(/站$/, "") : "—"}</td>
                <td className="ledger-store-cell" title={item.store_name || undefined}>
                  {item.store_name || "—"}
                </td>
                <td>{money(item.best_source?.landed_cost_cny)}</td>
                <td>
                  {money(item.adjusted_min)}
                  {item.price_sku_ref ? (
                    <small className="ledger-estimate" title="已在抽屉里指定用哪个 SKU 核算">指定 SKU</small>
                  ) : null}
                  {item.original_min && item.original_min !== item.adjusted_min ? (
                    <small className="ledger-muted"> 原 {money(item.original_min)}</small>
                  ) : null}
                </td>
                <td>
                  {profitText(item.profit)}
                  {item.weight_estimated ? (
                    <small className="ledger-estimate" title="货源未填重量，利润按默认 0.5kg 计算">
                      按 0.5kg 估算
                    </small>
                  ) : null}
                </td>
                <td>
                  <span className={`ledger-status ${statusTone(item.status)}`}>
                    {STATUS_LABELS[item.status] || item.status}
                  </span>
                </td>
                <td className="ledger-action-cell">
                  <button
                    type="button"
                    className="ledger-link"
                    title="打开货源 / 利润详情"
                    onClick={() => void openDetail(item)}
                  >
                    详情
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
            第 {page} / {totalPages} 页 · 共 {total} 个商品
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
                  <label className="ledger-site-picker ledger-store-picker">
                    <span>店铺（可选）</span>
                    <span className="ledger-store-control">
                      <input
                        value={storeDraft}
                        maxLength={120}
                        placeholder="选择或填写店铺"
                        disabled={busy === "store"}
                        onChange={(event) => {
                          setStoreDraft(event.target.value);
                          setStoreOpen(true);
                        }}
                        onFocus={() => setStoreOpen(true)}
                        onClick={() => setStoreOpen(true)}
                        onBlur={() => window.setTimeout(() => setStoreOpen(false), 120)}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") setStoreOpen(false);
                          if (event.key === "Enter") {
                            event.preventDefault();
                            setStoreOpen(false);
                            void saveStore(storeDraft);
                          }
                        }}
                      />
                      <button
                        type="button"
                        aria-label="展开店铺列表"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => setStoreOpen((value) => !value)}
                      >
                        ⌄
                      </button>
                      {storeOpen && (detail.stores || []).length ? (
                        <span className="ledger-store-menu" role="listbox">
                          {(detail.stores || []).map((name) => (
                            <button
                              key={name}
                              type="button"
                              role="option"
                              aria-selected={storeDraft.trim() === name}
                              className={storeDraft.trim() === name ? "is-active" : undefined}
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => {
                                setStoreDraft(name);
                                setStoreOpen(false);
                                void saveStore(name);
                              }}
                            >
                              {name}
                            </button>
                          ))}
                        </span>
                      ) : null}
                    </span>
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
                        {sourceTitleText(source) || source.offer_id || source.source_url}
                      </a>
                      <small>
                        单价 {money(source.price_cny)} · 运费 {money(source.domestic_freight_cny)} · 到手{" "}
                        {money(source.landed_cost_cny)}
                        {source.weight_kg ? ` · ${source.weight_kg}kg` : " · 按 0.5kg 估算"}
                      </small>
                      <small>利润 {profitText(source.profit as Record<string, unknown>)}</small>
                    </div>

                    <SourceImageDrop
                      imageUrl={source.main_image_url || (source.images || [])[0]?.image_url || ""}
                      busy={Boolean(busy)}
                      onFile={(file) => void uploadMainImage(source, file)}
                      onRemove={() => {
                        const firstImage = (source.images || [])[0];
                        if (source.main_image_url) void removeMainImage(source);
                        else if (firstImage) void removeImage(source, firstImage.id);
                      }}
                    />
                    </div>

                    <div className="ledger-source-actions">
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
                {/* 已有货源只改成本：链接和标题由采集带入，不允许用户改。 */}
                {!form.id && (
                  <>
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
                  </>
                )}
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

            <section className="ledger-section">
              <div className="ledger-price-head">
                <h3>申报价明细（{detail.declared_prices.length}）</h3>
                <div className="ledger-price-head-actions">
                  {detail.price_selection ? (
                    <button
                      type="button"
                      className="ledger-link"
                      onClick={() => void choosePriceSku("")}
                      disabled={Boolean(busy)}
                    >
                      恢复默认最低价
                    </button>
                  ) : null}
                  <small>多个 SKU 价格不同时可指定一个；未指定默认取最低价</small>
                </div>
              </div>
              <div className="ledger-manual-price">
                <span>手动填写申报价（元）</span>
                <input
                  value={manualPriceDraft}
                  inputMode="decimal"
                  placeholder="如 150"
                  onChange={(event) => setManualPriceDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void saveManualPrice();
                  }}
                />
                <button
                  type="button"
                  className="ledger-btn is-primary"
                  onClick={() => void saveManualPrice()}
                  disabled={Boolean(busy) || !manualPriceDraft.trim()}
                >
                  {busy === "manual-price" ? "保存中…" : "保存"}
                </button>
                <small>采集不到时可直接填 Temu 上可上架的申报价，同样参与利润核算</small>
              </div>
              <div className="ledger-price-table">
                <table>
                  <thead>
                    <tr>
                      <th>SKU 货号</th>
                      <th>SKU 属性</th>
                      <th>原申报价</th>
                      <th>调整后</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.declared_prices.map((row, index) => (
                      <tr key={row.id || `${row.sku_id}-${index}`} className={row.selected ? "is-selected" : ""}>
                        <td>{row.sku_merchant_code || row.sku_id || "—"}</td>
                        <td title={row.sku_attribute_text}>{row.sku_attribute_text || "—"}</td>
                        <td>{money(row.original_price_cny)}</td>
                        <td>{money(row.adjusted_price_cny)}</td>
                        <td className="ledger-price-actions">
                          {row.selected ? (
                            <span className="ledger-price-chosen">
                              {detail.price_selection ? "使用中" : "默认最低"}
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="ledger-link"
                              onClick={() => void choosePriceSku(row.sku_ref || "")}
                              disabled={!row.sku_ref || Boolean(busy)}
                            >
                              使用
                            </button>
                          )}
                          <button
                            type="button"
                            className="ledger-link is-danger"
                            onClick={() => void removeDeclaredPrice(row.id || "")}
                            disabled={!row.id || Boolean(busy)}
                          >
                            删除
                          </button>
                        </td>
                      </tr>
                    ))}
                    {!detail.declared_prices.length && (
                      <tr>
                        <td colSpan={5} className="ledger-empty">还没有采集到申报价</td>
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
                {bestSource && detail.adjusted_min && !detail.site_code ? (
                  <small className="ledger-foot-tip">
                    还没有站点，无法计算利润：请在上方「站点」里选择站点
                  </small>
                ) : null}
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

function SourceImageDrop({
  imageUrl,
  busy,
  onFile,
  onRemove,
}: {
  imageUrl: string;
  busy: boolean;
  onFile: (file: File | null) => void;
  onRemove: () => void;
}) {
  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const image = Array.from(event.clipboardData.files).find((item) => item.type.startsWith("image/"));
    if (!image) return;
    event.preventDefault();
    onFile(image);
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const image = Array.from(event.dataTransfer.files).find((item) => item.type.startsWith("image/"));
    if (image) onFile(image);
  };
  return (
    <div
      className="ledger-image-drop"
      tabIndex={0}
      title="点一下这里，再按 Ctrl+V 可粘贴图片"
      onPaste={onPaste}
      onDragOver={(event) => event.preventDefault()}
      onDrop={onDrop}
    >
      {imageUrl ? (
        <img
          className="ledger-image-preview"
          src={imageUrl}
          alt="货源图片"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div className="ledger-image-icon">▢</div>
      )}
      <div className="ledger-image-copy">
        <strong>货源图片 Ctrl+V</strong>
        <span>
          {busy
            ? "处理中…"
            : imageUrl
              ? "已设置主图，可粘贴、拖入或选择新图替换"
              : "可选：粘贴、拖入或选择图片"}
        </span>
      </div>
      {imageUrl ? (
        <button type="button" className="ledger-image-remove" onClick={onRemove} disabled={busy}>
          移除
        </button>
      ) : null}
      <label className="ledger-file-button">
        选择图片
        <input
          type="file"
          accept="image/*"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0] || null;
            event.target.value = "";
            if (file) onFile(file);
          }}
        />
      </label>
    </div>
  );
}

export default SourcingLedgerPage;

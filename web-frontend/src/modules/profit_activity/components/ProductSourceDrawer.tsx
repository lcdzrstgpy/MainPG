import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ClipboardEvent as ReactClipboardEvent, MouseEvent as ReactMouseEvent } from "react";

import { listProductSources, loadProductImage, updateProductSourceGroup } from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProductSourceLink, ProductSources } from "../types/products";
import { priceVerificationApi } from "../../price_verification/api/priceVerificationApi";
import { ImagePreviewModal } from "./ImagePreviewModal";
import type { PreviewImage } from "./ImagePreviewModal";

type Props = {
  product: ProfitActivityProduct | null;
  onClose: () => void;
  onChanged?: () => void;
};

/** 非核价产品货源编辑态的一行：一条货源链接 + 可选的替换截图 */
type EditSourceRow = {
  key: number;
  /** 原始货源组号，保存时据此判断链接/图片是否变化 */
  originalGroup: number;
  url: string;
  image: File | null;
  imagePreview: string;
};

const siteLabel = (site?: string) => {
  if (site === "US") return "美区";
  if (site === "CO") return "哥伦比亚";
  if (site === "EC") return "厄瓜多尔";
  return site || "-";
};

function toNumber(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : Number.NaN;
  if (value === null || value === undefined || value === "") return Number.NaN;
  const parsed = Number(String(value).replace(/[¥,\s]/g, ""));
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function moneyText(value: unknown, symbol = "¥") {
  const number = toNumber(value);
  if (!Number.isFinite(number)) return "—";
  return `${symbol}${number.toFixed(2)}`;
}

/** 货源单图：一条链接对应一张图。优先标准审核表截图，回退 1688 主图。 */
function SourceCardImage({ skc, site, group, imagePaths, fallbackUrl, onPreview }: { skc: string; site: string; group?: number; imagePaths: string[]; fallbackUrl?: string; onPreview?: (image: PreviewImage) => void }) {
  const [url, setUrl] = useState("");
  const first = imagePaths[0] ?? "";

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    if (first) {
      loadProductImage({
        skc,
        site: (site || "US") as "US" | "CO" | "EC",
        kind: "source",
        group: group ?? 0,
        index: 0,
        version: first,
      })
        .then((loaded) => {
          if (cancelled) {
            URL.revokeObjectURL(loaded);
            return;
          }
          objectUrl = loaded;
          setUrl(loaded);
        })
        .catch((err) => {
          console.error("[货源图显示] 加载失败 → skc:", skc, "| group:", group, "| first:", first, err);
        });
    }
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skc, site, group, first]);

  const src = url || fallbackUrl || "";
  if (!src) return <span className="profit-source-card-img-fallback">无图</span>;
  const label = url ? "货源截图" : "1688 商品图";
  // 卡片外层可能是跳转链接，这里拦掉默认行为，只打开大图预览
  const openPreview = onPreview
    ? (event: ReactMouseEvent<HTMLImageElement>) => {
        event.preventDefault();
        event.stopPropagation();
        onPreview({ url: src, label });
      }
    : undefined;
  return (
    <img
      className={`profit-source-card-shot${onPreview ? " is-zoomable" : ""}`}
      src={src}
      alt={label}
      loading="lazy"
      title={onPreview ? "点击查看大图" : undefined}
      onClick={openPreview}
    />
  );
}

export function ProductSourceDrawer({ product, onClose, onChanged }: Props) {
  const [sources, setSources] = useState<ProductSources | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [unlinkBusy, setUnlinkBusy] = useState<number | null>(null);
  const [unlinkError, setUnlinkError] = useState("");
  // 非核价产品货源卡片的编辑态：可新增/删除多条货源链接与截图
  const [editingSources, setEditingSources] = useState(false);
  const [editRows, setEditRows] = useState<EditSourceRow[]>([]);
  const [savingSource, setSavingSource] = useState(false);
  // 货源截图大图预览
  const [previewImage, setPreviewImage] = useState<PreviewImage | null>(null);
  const nextEditRowKeyRef = useRef(0);
  // 抽屉内维护最新产品数据：保存/解除关联后更新，避免编辑态复用旧的 source_groups
  const [productData, setProductData] = useState<ProfitActivityProduct | null>(product);

  const open = product !== null;

  useEffect(() => {
    setProductData(product);
  }, [product]);

  const refresh = useCallback(async () => {
    const target = productData ?? product;
    if (!target) return;
    setLoading(true);
    setError("");
    try {
      const site = (target.site || target.site_code || "US") as "US" | "CO" | "EC";
      const data = await listProductSources({ skc: target.skc, site });
      setSources(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [product, productData]);

  useEffect(() => {
    if (open) void refresh();
    else {
      setSources(null);
      setUnlinkError("");
      setEditingSources(false);
      setEditRows([]);
      setSavingSource(false);
      setPreviewImage(null);
    }
  }, [open, refresh]);

  if (!open || !product) return null;

  // 使用抽屉内最新产品数据（保存后刷新），避免编辑态复用旧的 source_groups
  const current = productData ?? product;

  // 核价入库产品额外展示「解除关联」；货源卡片统一展示单价/运费/到手/重量与图片（不做利润细则）。
  const isPriceVerification = current.source_type === "price_verification";

  const unlink = async (link: ProductSourceLink) => {
    if (!link.batch_id) return;
    setUnlinkBusy(link.id);
    setUnlinkError("");
    try {
      await priceVerificationApi.removeSkcSourceLink(link.batch_id, link.id);
      await refresh();
      onChanged?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setUnlinkError(/401|token|登录|过期|会话/.test(message)
        ? "登录会话已失效或无权解除该关联：请登录数据所属工作区后重试。"
        : `解除关联失败：${message}`);
    } finally {
      setUnlinkBusy(null);
    }
  };

  // 进入“修改货源链接”编辑态，载入全部货源组
  const startEditSources = () => {
    const groups = (productData ?? product).source_groups ?? [];
    const rows: EditSourceRow[] = groups.length
      ? groups.map((group, index) => ({
          key: index,
          originalGroup: index,
          url: group?.source_url ?? "",
          image: null,
          imagePreview: "",
        }))
      : [{ key: 0, originalGroup: 0, url: "", image: null, imagePreview: "" }];
    nextEditRowKeyRef.current = rows.length;
    setEditRows(rows);
    setEditingSources(true);
    setUnlinkError("");
  };

  const cancelEditSources = () => {
    for (const row of editRows) {
      if (row.imagePreview) URL.revokeObjectURL(row.imagePreview);
    }
    setEditingSources(false);
    setEditRows([]);
  };

  const addEditRow = () => {
    setEditRows((current) => [
      ...current,
      { key: nextEditRowKeyRef.current++, originalGroup: current.length, url: "", image: null, imagePreview: "" },
    ]);
  };

  const removeEditRow = (key: number) => {
    setEditRows((current) => {
      if (current.length <= 1) return current;
      const target = current.find((row) => row.key === key);
      if (target?.imagePreview) URL.revokeObjectURL(target.imagePreview);
      return current.filter((row) => row.key !== key);
    });
  };

  const changeEditRowUrl = (key: number, url: string) => {
    setEditRows((current) => current.map((row) => (row.key === key ? { ...row, url } : row)));
  };

  const onEditRowImageSelected = (key: number, file: File | undefined) => {
    if (!file) return;
    setEditRows((current) => current.map((row) => {
      if (row.key !== key) return row;
      if (row.imagePreview) URL.revokeObjectURL(row.imagePreview);
      return { ...row, image: file, imagePreview: URL.createObjectURL(file) };
    }));
  };

  const onEditRowImagePaste = (key: number, event: ReactClipboardEvent<HTMLDivElement>) => {
    const file = [...event.clipboardData.files].find((item) => item.type.startsWith("image/"));
    if (!file) return;
    event.preventDefault();
    onEditRowImageSelected(key, file);
  };

  const saveEditSources = async () => {
    if (savingSource || !editingSources) return;
    const filled = editRows.filter((row) => row.url.trim().length > 0);
    if (!filled.length) {
      setUnlinkError("请至少保留一个货源链接再保存。");
      return;
    }
    setSavingSource(true);
    setUnlinkError("");
    try {
      // 重建货源组：仅保留有链接的组，组号紧凑重排；链接未变且未换图时保留原截图
      const originals = (productData ?? product).source_groups ?? [];
      const groups: Array<{ source_url: string; image_paths: string[]; cost?: number | null }> = [];
      const groupImages: Record<number, File> = {};
      filled.forEach((row, index) => {
        const original = originals[row.originalGroup];
        const urlUnchanged = original && (original.source_url ?? "").trim() === row.url.trim();
        groups.push({
          source_url: row.url.trim(),
          image_paths: urlUnchanged && !row.image ? [...(original.image_paths ?? [])] : [],
          cost: original?.cost ?? null,
        });
        if (row.image) groupImages[index] = row.image;
      });
      const site = ((productData ?? product).site || (productData ?? product).site_code || "US") as "US" | "CO" | "EC";
      const saved = await updateProductSourceGroup({
        site,
        skc: (productData ?? product).skc,
        group: 0,
        sourceGroups: groups,
        groupImages,
      });
      // 用接口返回的最新产品数据刷新抽屉内状态，再次编辑时使用最新的 source_groups
      if (saved?.product) setProductData(saved.product);
      for (const row of editRows) {
        if (row.imagePreview) URL.revokeObjectURL(row.imagePreview);
      }
      cancelEditSources();
      await refresh();
      onChanged?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[货源保存-错误] 保存失败:", err);
      setUnlinkError(`保存失败：${message}`);
    } finally {
      setSavingSource(false);
    }
  };

  // portal 到 body：tab 面板 fill-mode 动画的层叠上下文会锁住 fixed 抽屉 z-index，被顶栏盖住
  return createPortal(
    <div className="profit-source-drawer-root">
      <div className="profit-source-drawer-mask" onClick={onClose} />
      <aside className="profit-source-drawer">
        <header className="profit-source-drawer-head">
          <div>
            <h2>{current.skc}</h2>
            <p>
              <span>{siteLabel(current.site || current.site_code)}</span>
              <span className="profit-source-drawer-sep">·</span>
              <span>调整后申报价 {moneyText(sources?.selling_price ?? current.selling_price)}</span>
              <span className="profit-source-drawer-sep">·</span>
              <span>已关联 1688 {sources?.links.length ?? 0} 条</span>
            </p>
          </div>
          <div className="profit-source-drawer-head-actions">
            {!editingSources ? (
              <button className="profit-source-edit-button" onClick={startEditSources}>
                修改货源
              </button>
            ) : null}
            <button className="profit-source-drawer-close" onClick={onClose} aria-label="关闭">×</button>
          </div>
        </header>

        <div className="profit-source-drawer-body">
          {loading ? <p className="profit-source-drawer-status">加载货源明细中…</p> : null}
          {!loading && error ? <p className="profit-source-drawer-status is-error">{error}</p> : null}
          {!loading && !error && !editingSources && (sources?.links.length ?? 0) === 0 ? (
            <div className="profit-source-drawer-empty">
              <p className="profit-source-drawer-status">该 SKC 暂无货源链接，点击下方按钮新增。</p>
              <button className="profit-source-add-row" onClick={startEditSources}>新增货源链接</button>
            </div>
          ) : null}

          {editingSources ? null : (sources?.links ?? []).map((link) => {
            const price = toNumber(link.price_cny);
            const freight = toNumber(link.domestic_freight_cny);
            const landed = price + (Number.isFinite(freight) ? freight : 0);
            const weight = toNumber(current.weight_kg);
            return (
              <div className={`profit-source-card ${isPriceVerification ? "" : "profit-source-card-simple"}`} key={link.id}>
                <div className="profit-source-card-row">
                  <a className="profit-source-card-main" href={link.source_url} target="_blank" rel="noreferrer">
                    <SourceCardImage
                      skc={current.skc}
                      site={sources?.site || current.site || current.site_code || "US"}
                      group={link.group}
                      imagePaths={link.image_paths ?? []}
                      fallbackUrl={link.main_image_url}
                      onPreview={setPreviewImage}
                    />
                    <span className="profit-source-card-body">
                      <span className="profit-source-card-title">
                        {link.source_title || (isPriceVerification ? "候选商品" : "货源链接")}
                      </span>
                      <small className="profit-source-card-meta">
                        单价 {moneyText(link.price_cny)} · 运费 {moneyText(link.domestic_freight_cny)} · 到手{" "}
                        {moneyText(landed)}
                        {Number.isFinite(weight) ? ` · 重量 ${weight} kg` : ""}
                      </small>
                      <small className="profit-source-card-meta profit-source-card-url" title={link.source_url}>
                        {link.source_url || "—"}
                      </small>
                    </span>
                  </a>
                  {isPriceVerification ? (
                    <button
                      className="profit-source-unlink"
                      onClick={() => void unlink(link)}
                      disabled={unlinkBusy === link.id}
                    >
                      {unlinkBusy === link.id ? "解除中…" : "解除关联"}
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })}
          {editingSources ? (
            <div className="profit-source-edit-panel">
              <div className="profit-source-edit-rows">
                {editRows.map((row, index) => {
                  const original = (current.source_groups ?? [])[row.originalGroup];
                  return (
                    <div className="profit-source-edit-row" key={row.key}>
                      <div className="profit-source-edit-main">
                        <div
                          className="profit-source-edit-image"
                          tabIndex={0}
                          title="点击后可 Ctrl+V 粘贴图片"
                          onPaste={(event) => onEditRowImagePaste(row.key, event)}
                        >
                          {row.imagePreview ? (
                            <img
                              className="profit-source-card-shot is-zoomable"
                              src={row.imagePreview}
                              alt="新货源截图"
                              loading="lazy"
                              title="点击查看大图"
                              onClick={() => setPreviewImage({ url: row.imagePreview, label: "新货源截图" })}
                            />
                          ) : (
                            <SourceCardImage
                              skc={current.skc}
                              site={sources?.site || current.site || current.site_code || "US"}
                              group={row.originalGroup}
                              imagePaths={original?.image_paths ?? []}
                              fallbackUrl=""
                              onPreview={setPreviewImage}
                            />
                          )}
                          <label className="profit-source-edit-image-button">
                            {row.imagePreview ? "重新选择" : "选择图片"}
                            <input
                              type="file"
                              accept="image/*"
                              hidden
                              onChange={(event) => {
                                onEditRowImageSelected(row.key, event.target.files?.[0]);
                                event.target.value = "";
                              }}
                            />
                          </label>
                        </div>
                        <label className="profit-source-edit-url">
                          <span>货源链接 {index + 1}</span>
                          <input
                            type="url"
                            placeholder="https://…"
                            value={row.url}
                            onChange={(event) => changeEditRowUrl(row.key, event.target.value)}
                          />
                        </label>
                      </div>
                      <button
                        className="profit-source-remove-row"
                        onClick={() => removeEditRow(row.key)}
                        disabled={editRows.length <= 1}
                        title={editRows.length <= 1 ? "至少保留一个货源链接" : "删除该货源链接"}
                      >
                        删除
                      </button>
                    </div>
                  );
                })}
              </div>
              <div className="profit-source-edit-toolbar">
                <button className="profit-source-add-row" onClick={addEditRow}>新增链接</button>
                <span className="profit-source-edit-toolbar-actions">
                  <button className="profit-edit-save" onClick={() => void saveEditSources()} disabled={savingSource}>
                    {savingSource ? "保存中…" : "保存"}
                  </button>
                  <button className="profit-edit-cancel" onClick={cancelEditSources} disabled={savingSource}>
                    撤销
                  </button>
                </span>
              </div>
            </div>
          ) : null}
          {unlinkError ? <p className="profit-source-drawer-status is-error">{unlinkError}</p> : null}
        </div>
      </aside>
      {previewImage ? (
        <ImagePreviewModal
          images={[previewImage]}
          index={0}
          onIndexChange={() => undefined}
          onClose={() => setPreviewImage(null)}
        />
      ) : null}
    </div>,
    document.body,
  );
}

import { useCallback, useEffect, useRef, useState } from "react";

import { listProductSources, loadProductImage, updateProductSourceGroup } from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProductSourceLink, ProductSources } from "../types/products";
import { priceVerificationApi } from "../../price_verification/api/priceVerificationApi";
import type { SourceTopProfit } from "../../price_verification/types";

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

function percentText(value: unknown) {
  const number = toNumber(value);
  if (!Number.isFinite(number)) return "";
  return `${Math.round(number * 100)}%`;
}

/** 货源单图：一条链接对应一张图。优先标准审核表截图，回退 1688 主图。 */
function SourceCardImage({ skc, site, group, imagePaths, fallbackUrl }: { skc: string; site: string; group?: number; imagePaths: string[]; fallbackUrl?: string }) {
  const [url, setUrl] = useState("");
  const first = imagePaths[0] ?? "";

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    // 调试：打印每次加载货源图的参数（group 与 imagePaths 变化时应重新请求）
    console.log("[货源图显示] 尝试加载 → skc:", skc, "| site:", site, "| group:", group,
      "| first:", first, "| imagePaths:", JSON.stringify(imagePaths));
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
          console.log("[货源图显示] 加载成功 → skc:", skc, "| group:", group, "| objectURL:", loaded);
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

  if (url) return <img className="profit-source-card-shot" src={url} alt="货源截图" loading="lazy" />;
  if (fallbackUrl) return <img className="profit-source-card-shot" src={fallbackUrl} alt="1688 商品图" loading="lazy" />;
  return <span className="profit-source-card-img-fallback">无图</span>;
}

function qualificationText(value?: string) {
  if (value === "net_profit_and_profit_rate_passed") return "净利润与利润率双达标";
  if (value === "net_profit_passed") return "净利润达标";
  if (value === "profit_rate_passed") return "利润率达标";
  if (value === "net_profit_and_profit_rate_below_threshold") return "净利润与利润率未达标";
  return "";
}

function reasonText(reason?: string) {
  if (!reason) return "无法核算";
  if (reason === "missing_source_price") return "候选无有效价格";
  if (reason === "missing_selling_price") return "缺少调整后申报价";
  if (reason === "missing_site") return "站点未识别";
  if (reason === "profit_calculation_failed") return "利润计算失败";
  return reason;
}

async function computeProfit(
  link: ProductSourceLink,
  price: string | undefined,
  weight: string,
  site: string | undefined,
  selling: number | null | undefined,
): Promise<SourceTopProfit | null> {
  if (!selling || !link.batch_id) return null;
  if (price === undefined || price === null || price === "") return null;
  try {
    return await priceVerificationApi.previewSourceProfit(link.batch_id, {
      site: site || "US",
      selling_price: String(selling),
      price: String(price),
      moq: link.moq ?? null,
      domestic_freight: link.domestic_freight_cny ?? null,
      weight_kg: weight,
    });
  } catch {
    return null;
  }
}

export function ProductSourceDrawer({ product, onClose, onChanged }: Props) {
  const [sources, setSources] = useState<ProductSources | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [prices, setPrices] = useState<Record<number, string>>({});
  const [weights, setWeights] = useState<Record<number, string>>({});
  const [profits, setProfits] = useState<Record<number, SourceTopProfit | null>>({});
  const [profitBusy, setProfitBusy] = useState<number | null>(null);
  const [unlinkBusy, setUnlinkBusy] = useState<number | null>(null);
  const [unlinkError, setUnlinkError] = useState("");
  // 非核价产品货源卡片的编辑态：可新增/删除多条货源链接与截图
  const [editingSources, setEditingSources] = useState(false);
  const [editRows, setEditRows] = useState<EditSourceRow[]>([]);
  const [savingSource, setSavingSource] = useState(false);
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
      const nextPrices: Record<number, string> = {};
      const nextWeights: Record<number, string> = {};
      data.links.forEach((link) => {
        const price = toNumber(link.price_cny);
        nextPrices[link.id] = Number.isFinite(price) ? String(price) : "";
        nextWeights[link.id] = "0.5";
      });
      setPrices(nextPrices);
      setWeights(nextWeights);
      const selling = data.selling_price ?? target.selling_price;
      const nextProfits: Record<number, SourceTopProfit | null> = {};
      await Promise.all(data.links.map(async (link) => {
        const profit = await computeProfit(link, nextPrices[link.id], nextWeights[link.id], data.site, selling);
        if (profit) nextProfits[link.id] = profit;
      }));
      setProfits(nextProfits);
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
      setProfits({});
      setPrices({});
      setWeights({});
      setUnlinkError("");
      setEditingSources(false);
      setEditRows([]);
      setSavingSource(false);
    }
  }, [open, refresh]);

  if (!open || !product) return null;

  // 使用抽屉内最新产品数据（保存后刷新），避免编辑态复用旧的 source_groups
  const current = productData ?? product;

  // 核价入库产品保留候选源价/重量/利润核算与解除关联；其他产品仅展示并允许修改图片与链接
  const isPriceVerification = current.source_type === "price_verification";

  const changePrice = (link: ProductSourceLink, rawValue: string) => {
    if (!/^\d*\.?\d*$/.test(rawValue)) return;
    setPrices((current) => ({ ...current, [link.id]: rawValue }));
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setProfitBusy(link.id);
    void computeProfit(link, rawValue, weights[link.id], sources?.site, sources?.selling_price ?? current.selling_price)
      .then((profit) => setProfits((current) => ({ ...current, [link.id]: profit })))
      .finally(() => setProfitBusy((current) => (current === link.id ? null : current)));
  };

  const changeWeight = (link: ProductSourceLink, rawValue: string) => {
    if (!/^\d*\.?\d*$/.test(rawValue)) return;
    setWeights((current) => ({ ...current, [link.id]: rawValue }));
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setProfitBusy(link.id);
    void computeProfit(link, prices[link.id], rawValue, sources?.site, sources?.selling_price ?? current.selling_price)
      .then((profit) => setProfits((current) => ({ ...current, [link.id]: profit })))
      .finally(() => setProfitBusy((current) => (current === link.id ? null : current)));
  };

  const unlink = async (link: ProductSourceLink) => {
    if (!link.batch_id) return;
    setUnlinkBusy(link.id);
    setUnlinkError("");
    try {
      await priceVerificationApi.removeSkcSourceLink(link.batch_id, link.id);
      setProfits((current) => {
        const next = { ...current };
        delete next[link.id];
        return next;
      });
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

  // 非核价产品：进入“修改货源链接”编辑态，载入全部货源组
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

  const saveEditSources = async () => {
    if (savingSource || !editingSources) return;
    const filled = editRows.filter((row) => row.url.trim().length > 0);
    if (!filled.length) {
      setUnlinkError("请至少保留一个货源链接再保存。");
      return;
    }
    // 调试：打印编辑态每行的原始信息
    console.log("[货源保存-1] editRows =", editRows.map((r) => ({
      key: r.key, originalGroup: r.originalGroup, url: r.url,
      image: r.image ? { name: r.image.name, size: r.image.size, type: r.image.type } : null,
      imagePreview: r.imagePreview ? "(有预览)" : "(无预览)",
    })));
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
      // 调试：打印重建后的货源组与待上传的组图
      console.log("[货源保存-2] 重建 groups =", JSON.stringify(groups, null, 2));
      console.log("[货源保存-2] groupImages 键(组号) =", Object.keys(groupImages),
        "| 值 =", Object.fromEntries(Object.entries(groupImages).map(([k, f]) => [k, f.name])));
      const site = ((productData ?? product).site || (productData ?? product).site_code || "US") as "US" | "CO" | "EC";
      const saved = await updateProductSourceGroup({
        site,
        skc: (productData ?? product).skc,
        group: 0,
        sourceGroups: groups,
        groupImages,
      });
      // 调试：打印后端保存后返回的产品 source_groups（应包含新上传的图片路径）
      console.log("[货源保存-3] 后端返回 product.source_groups =", JSON.stringify(saved?.product?.source_groups ?? [], null, 2));
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

  return (
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
            {!isPriceVerification && !editingSources ? (
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
            isPriceVerification ? (
              <p className="profit-source-drawer-status">该 SKC 暂无已关联的 1688 货源。</p>
            ) : (
              <div className="profit-source-drawer-empty">
                <p className="profit-source-drawer-status">该 SKC 暂无货源链接，点击下方按钮新增。</p>
                <button className="profit-source-add-row" onClick={startEditSources}>新增货源链接</button>
              </div>
            )
          ) : null}

          {!isPriceVerification && editingSources ? null : (sources?.links ?? []).map((link) => {
            const profit = profits[link.id] ?? null;
            const priceText = prices[link.id] !== undefined ? prices[link.id] : String(link.price_cny ?? "");
            const weightText = weights[link.id] ?? "0.5";
            return (
              <div className={`profit-source-card ${isPriceVerification ? "" : "profit-source-card-simple"}`} key={link.id}>
                <div className="profit-source-card-row">
                  {isPriceVerification ? (
                    <>
                      <a className="profit-source-card-main" href={link.source_url} target="_blank" rel="noreferrer">
                        <SourceCardImage
                          skc={current.skc}
                          site={sources?.site || current.site || current.site_code || "US"}
                          group={link.group}
                          imagePaths={link.image_paths ?? []}
                          fallbackUrl={link.main_image_url}
                        />
                        <span className="profit-source-card-body">
                          <span className="profit-source-card-title">{link.source_title || "候选商品"}</span>
                          <small className="profit-source-card-meta">
                            offer {link.offer_id} · 起订量 {link.moq ? `${link.moq} 件` : "—"} · 国内运费 {link.domestic_freight_cny ? `¥${link.domestic_freight_cny}` : "—"}
                          </small>
                        </span>
                        <b>{moneyText(link.price_cny)}</b>
                      </a>
                      <button
                        className="profit-source-unlink"
                        onClick={() => void unlink(link)}
                        disabled={unlinkBusy === link.id}
                      >
                        {unlinkBusy === link.id ? "解除中…" : "解除关联"}
                      </button>
                    </>
                  ) : (
                    <>
                      <a className="profit-source-card-main" href={link.source_url} target="_blank" rel="noreferrer">
                        <SourceCardImage
                          skc={current.skc}
                          site={sources?.site || current.site || current.site_code || "US"}
                          group={link.group}
                          imagePaths={link.image_paths ?? []}
                          fallbackUrl={link.main_image_url}
                        />
                        <span className="profit-source-card-body">
                          <span className="profit-source-card-title">{link.source_title || "货源链接"}</span>
                          <small className="profit-source-card-meta profit-source-card-url" title={link.source_url}>{link.source_url || "—"}</small>
                        </span>
                      </a>
                    </>
                  )}
                </div>

                {isPriceVerification ? (
                  <div className="profit-source-card-profit">
                    <dl className="profit-source-card-fields">
                      <div className="is-editable">
                        <dt>候选源价（可调）</dt>
                        <dd><input type="number" min="0.01" step="0.01" value={priceText} onChange={(event) => changePrice(link, event.target.value)} disabled={profitBusy === link.id} /> 元</dd>
                      </div>
                      <div className="is-editable is-weight">
                        <dt>重量（可调）</dt>
                        <dd><input type="number" min="0.1" max="10" step="0.1" value={weightText} onChange={(event) => changeWeight(link, event.target.value)} disabled={profitBusy === link.id} /> kg</dd>
                      </div>
                    </dl>
                    {profit?.available ? (
                      <div className={`profit-source-card-result ${profit.qualified ? "is-qualified" : ""}`}>
                        <dl className="profit-source-card-result-fields">
                          <div><dt>总成本</dt><dd>{moneyText(profit.total_cost)}</dd></div>
                          <div><dt>净利润</dt><dd>{moneyText(profit.net_profit)}</dd></div>
                          <div><dt>利润率</dt><dd>{percentText(profit.profit_rate)}</dd></div>
                        </dl>
                        <em className={profit.qualified ? "is-qualified" : ""} title={qualificationText(profit.qualification)}>
                          {profit.qualified ? "达标 ✓" : "未达标"}
                        </em>
                      </div>
                    ) : (
                      <div className="profit-source-card-result is-empty">
                        <span>利润核算不可用：{reasonText(profit?.reason)}</span>
                        {profitBusy === link.id ? <small>核算中…</small> : null}
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            );
          })}
          {!isPriceVerification && editingSources ? (
            <div className="profit-source-edit-panel">
              <div className="profit-source-edit-rows">
                {editRows.map((row, index) => {
                  const original = (current.source_groups ?? [])[row.originalGroup];
                  return (
                    <div className="profit-source-edit-row" key={row.key}>
                      <div className="profit-source-edit-main">
                        <div className="profit-source-edit-image">
                          {row.imagePreview ? (
                            <img className="profit-source-card-shot" src={row.imagePreview} alt="新货源截图" />
                          ) : (
                            <SourceCardImage
                              skc={current.skc}
                              site={sources?.site || current.site || current.site_code || "US"}
                              group={row.originalGroup}
                              imagePaths={original?.image_paths ?? []}
                              fallbackUrl=""
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
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";

import { listProductSources } from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProductSourceLink, ProductSources } from "../types/products";
import { priceVerificationApi } from "../../price_verification/api/priceVerificationApi";
import type { SourceTopProfit } from "../../price_verification/types";

type Props = {
  product: ProfitActivityProduct | null;
  onClose: () => void;
  onChanged?: () => void;
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

  const open = product !== null;

  const refresh = useCallback(async () => {
    if (!product) return;
    setLoading(true);
    setError("");
    try {
      const site = (product.site || product.site_code || "US") as "US" | "CO" | "EC";
      const data = await listProductSources({ skc: product.skc, site });
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
      const selling = data.selling_price ?? product.selling_price;
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
  }, [product]);

  useEffect(() => {
    if (open) void refresh();
    else {
      setSources(null);
      setProfits({});
      setPrices({});
      setWeights({});
      setUnlinkError("");
    }
  }, [open, refresh]);

  if (!open || !product) return null;

  const changePrice = (link: ProductSourceLink, rawValue: string) => {
    if (!/^\d*\.?\d*$/.test(rawValue)) return;
    setPrices((current) => ({ ...current, [link.id]: rawValue }));
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setProfitBusy(link.id);
    void computeProfit(link, rawValue, weights[link.id], sources?.site, sources?.selling_price ?? product.selling_price)
      .then((profit) => setProfits((current) => ({ ...current, [link.id]: profit })))
      .finally(() => setProfitBusy((current) => (current === link.id ? null : current)));
  };

  const changeWeight = (link: ProductSourceLink, rawValue: string) => {
    if (!/^\d*\.?\d*$/.test(rawValue)) return;
    setWeights((current) => ({ ...current, [link.id]: rawValue }));
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setProfitBusy(link.id);
    void computeProfit(link, prices[link.id], rawValue, sources?.site, sources?.selling_price ?? product.selling_price)
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
      setUnlinkError(message.includes("401") || message.includes("token")
        ? "登录会话已失效或无权解除该关联：请登录数据所属工作区后重试。"
        : `解除关联失败：${message}`);
    } finally {
      setUnlinkBusy(null);
    }
  };

  return (
    <div className="profit-source-drawer-root">
      <div className="profit-source-drawer-mask" onClick={onClose} />
      <aside className="profit-source-drawer">
        <header className="profit-source-drawer-head">
          <div>
            <p className="eyebrow">PRODUCT SOURCES</p>
            <h2>{product.skc}</h2>
            <p>
              <span>{siteLabel(product.site || product.site_code)}</span>
              <span className="profit-source-drawer-sep">·</span>
              <span>调整后申报价 {moneyText(sources?.selling_price ?? product.selling_price)}</span>
              <span className="profit-source-drawer-sep">·</span>
              <span>已关联 1688 {sources?.links.length ?? 0} 条</span>
            </p>
          </div>
          <button className="profit-source-drawer-close" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="profit-source-drawer-body">
          {loading ? <p className="profit-source-drawer-status">加载货源明细中…</p> : null}
          {!loading && error ? <p className="profit-source-drawer-status is-error">{error}</p> : null}
          {!loading && !error && (sources?.links.length ?? 0) === 0 ? (
            <p className="profit-source-drawer-status">该 SKC 暂无已关联的 1688 货源。</p>
          ) : null}

          {(sources?.links ?? []).map((link) => {
            const profit = profits[link.id] ?? null;
            const priceText = prices[link.id] !== undefined ? prices[link.id] : String(link.price_cny ?? "");
            const weightText = weights[link.id] ?? "0.5";
            return (
              <div className="profit-source-card" key={link.id}>
                <div className="profit-source-card-row">
                  <a className="profit-source-card-main" href={link.source_url} target="_blank" rel="noreferrer">
                    {link.main_image_url ? <img src={link.main_image_url} alt="" loading="lazy" /> : <span className="profit-source-card-img-fallback">1688</span>}
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
                </div>

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
              </div>
            );
          })}
          {unlinkError ? <p className="profit-source-drawer-status is-error">{unlinkError}</p> : null}
        </div>
      </aside>
    </div>
  );
}

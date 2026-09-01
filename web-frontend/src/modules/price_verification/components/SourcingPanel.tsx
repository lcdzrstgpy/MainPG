import { type FormEvent, useEffect, useRef, useState } from "react";

import { priceVerificationApi } from "../api/priceVerificationApi";
import type { QuoteBatchSkuPrice, SkcSourceLink, SourceCandidate, SourceCandidateSelection, SourcePreview, SourcePreviewItem, SourcePreviewSkcGroup, SourceTopProfit } from "../types";
import { SectionHelp } from "./SectionHelp";
import "../styles/priceVerificationSource.css";

type Props = {
  preview: SourcePreview | null;
  batchId: string;
  busy: boolean;
  sourceCount?: number;
  links: SkcSourceLink[];
  selectedCandidates: SourceCandidateSelection[];
  onLink: (skcId: string, offerId: string, candidate: SourceCandidate, priceOverride?: string, weightOverride?: string) => Promise<void>;
  onManualLookup: (skcId: string, sourceUrl: string) => Promise<void>;
  onUnlink: (linkId: number) => Promise<void>;
  onUnselectCandidate: (skcId: string, offerId: string) => Promise<void>;
  onComplete: () => void;
  onStart: () => void;
  onError: (message: string) => void;
  matchingCompleted?: boolean;
  isActive?: boolean;
};

function actionError(error: unknown) {
  return error instanceof Error ? error.message : String(error || "请求失败");
}

const CANDIDATE_LIMIT = 5;

function statusText(status: string | undefined) {
  if (!status) return "待执行";
  if (status === "succeeded") return "已完成";
  if (status === "failed" || status === "error") return "失败";
  return status;
}

function sourceErrorText(error?: string) {
  if (!error) return "";
  if (error.includes("budget") || error.includes("额度")) return "当日 OB 调用额度已用完，次日自动恢复";
  if (error.includes("timeout")) return "请求超时，可重试";
  if (error.includes("provider request failed")) return "OB 接口请求失败，可重试";
  return error;
}

function decisionLabel(decision?: string) {
  if (decision === "recommended") return "推荐";
  if (decision === "review") return "待核";
  if (decision === "sku_validation") return "需验SKU";
  if (decision === "no_reliable_source") return "证据不足";
  return "候选";
}

function offerIdFromUrl(url?: string) {
  const match = url?.match(/(?:offer\/|offerId=|offer_id=)(\d{3,})/i);
  return match?.[1] ?? "";
}

function offerIdFor(candidate: SourceCandidate) {
  const fromUrl = offerIdFromUrl(candidate.source_url);
  if (fromUrl) return fromUrl;
  const id = candidate.offer_id;
  return id && /^\d{3,}$/.test(String(id)) ? String(id) : "";
}

function toNumber(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  if (value === null || value === undefined || value === "") return Number.POSITIVE_INFINITY;
  const parsed = Number(String(value).replace(/[¥,\s]/g, ""));
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

function moneyText(value: unknown) {
  const number = toNumber(value);
  if (!Number.isFinite(number)) return "—";
  return `¥${number.toFixed(2)}`;
}

function temuSkuPrice(sku: QuoteBatchSkuPrice) {
  return sku.new_declared_price_cny
    ?? sku.adjusted_declared_price_cny
    ?? sku.original_declared_price_cny
    ?? null;
}

function temuPriceText(sku: QuoteBatchSkuPrice) {
  const value = temuSkuPrice(sku);
  if (value === null || value === undefined || value === "") return "-";
  return `¥${value}`;
}

function percentText(value: unknown) {
  const number = toNumber(value);
  if (!Number.isFinite(number)) return "";
  return `${Math.round(number * 100)}%`;
}

function isNegative(value: unknown) {
  const number = toNumber(value);
  return Number.isFinite(number) && number < 0;
}

function siteLabel(site?: string) {
  if (site === "US") return "美国站";
  if (site === "CO") return "哥伦比亚站";
  if (site === "EC") return "厄瓜多尔站";
  return site || "—";
}

function qualificationText(value?: string) {
  if (value === "net_profit_and_profit_rate_passed") return "净利润与利润率双达标";
  if (value === "net_profit_passed") return "净利润达标";
  if (value === "profit_rate_passed") return "利润率达标";
  if (value === "net_profit_and_profit_rate_below_threshold") return "净利润与利润率未达标";
  return "";
}

function profitReasonText(reason?: string) {
  if (!reason) return "无法核算";
  if (reason === "no_candidates") return "无候选";
  if (reason === "missing_selection") return "缺少 SKC 信息";
  if (reason === "missing_site") return "站点未识别";
  if (reason === "missing_selling_price") return "缺少调整后申报价";
  if (reason === "missing_source_price") return "候选无有效价格";
  if (reason === "profit_calculation_failed") return "利润计算失败";
  return reason;
}

function auditText(item?: SourcePreviewItem) {
  const audit = item?.image_search_audit;
  if (!audit) return "本次图搜未提供上传审计";
  if (audit.downloaded && audit.uploaded && audit.searched) {
    const size = audit.image_size_bytes ? ` · ${Math.ceil(audit.image_size_bytes / 1024)} KB` : "";
    return `主图已下载、已上传、已图搜${size}`;
  }
  const failed = [audit.downloaded ? "" : "下载", audit.uploaded ? "" : "上传", audit.searched ? "" : "图搜"].filter(Boolean);
  return `图搜链路未完成：${failed.join("、")}`;
}

function visualAuditText(item?: SourcePreviewItem) {
  const audit = item?.visual_verification;
  if (!audit) return "未提供本地验图审计";
  if ((audit.input_count ?? 0) === 0) return "无候选进入本地验图";
  if (!audit.reference_available) return "参考图下载或解析失败";
  const threshold = audit.threshold !== undefined ? ` · 阈值 ${Math.round(audit.threshold * 100)}%` : "";
  const fallback = audit.fallback_count ? ` · 补位 ${audit.fallback_count}` : "";
  const distractors = audit.distractor_suppression?.applied
    ? ` · 已抑制干扰物 ${audit.distractor_suppression.distractor_count ?? 0}`
    : "";
  return `本地验图 ${audit.verified_count ?? 0}/${audit.input_count ?? 0}${fallback}${distractors}${threshold}`;
}

export function SourcingPanel({ preview, batchId, busy, sourceCount, links, selectedCandidates, onLink, onManualLookup, onUnlink, onUnselectCandidate, onComplete, onStart, onError, matchingCompleted = false, isActive = true }: Props) {
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [priceOverrides, setPriceOverrides] = useState<Record<string, string>>({});
  const [profitOverrides, setProfitOverrides] = useState<Record<string, SourceTopProfit | null>>({});
  const [profitBusy, setProfitBusy] = useState("");
  const [busyLink, setBusyLink] = useState("");
  const [manualLookupSkcId, setManualLookupSkcId] = useState("");
  const [manualLookupUrl, setManualLookupUrl] = useState("");
  const [manualLookupBusy, setManualLookupBusy] = useState(false);
  const [imagePreviewUrl, setImagePreviewUrl] = useState("");
  const [temuSkuDrawer, setTemuSkuDrawer] = useState<{
    productId: string;
    productTitle: string;
    imageUrl?: string;
    skus: QuoteBatchSkuPrice[];
    loading: boolean;
    error: string;
  } | null>(null);
  const sourceRunButtonRef = useRef<HTMLButtonElement>(null);
  const [showRefloatButton, setShowRefloatButton] = useState(false);
  const canRunSourceSearch = !busy && (sourceCount ?? 0) > 0;
  const canRefloatSourceSearch = Boolean(preview) && canRunSourceSearch;

  useEffect(() => {
    if (isActive) return;
    setManualLookupSkcId("");
    setImagePreviewUrl("");
  }, [isActive]);

  const itemKey = (item: SourcePreviewItem) => item.skc_id ?? item.quote_key;
  const candidateKeyFor = (skcKey: string, candidate: SourceCandidate | null) =>
    candidate ? `${skcKey}:${candidate.offer_id ?? candidate.source_url ?? ""}` : "";

  useEffect(() => {
    setProfitOverrides({});
    setPriceOverrides({});
    setWeights({});
    setImagePreviewUrl("");
  }, [preview]);

  useEffect(() => {
    if (!imagePreviewUrl) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImagePreviewUrl("");
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [imagePreviewUrl]);

  useEffect(() => {
    if (!temuSkuDrawer) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTemuSkuDrawer(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [temuSkuDrawer]);

  useEffect(() => {
    const contentCard = document.querySelector<HTMLElement>(".content-card");
    const updateRefloatVisibility = () => {
      const button = sourceRunButtonRef.current;
      if (!button || !canRefloatSourceSearch) {
        setShowRefloatButton(false);
        return;
      }
      const topbarBottom = document.querySelector<HTMLElement>(".topbar-card")?.getBoundingClientRect().bottom ?? 0;
      setShowRefloatButton(button.getBoundingClientRect().bottom <= topbarBottom + 8);
    };

    window.addEventListener("scroll", updateRefloatVisibility, { passive: true });
    contentCard?.addEventListener("scroll", updateRefloatVisibility, { passive: true });
    window.addEventListener("resize", updateRefloatVisibility);
    updateRefloatVisibility();
    return () => {
      window.removeEventListener("scroll", updateRefloatVisibility);
      contentCard?.removeEventListener("scroll", updateRefloatVisibility);
      window.removeEventListener("resize", updateRefloatVisibility);
    };
  }, [canRefloatSourceSearch, preview]);

  const openTemuSkuDrawer = async (productId: string, item?: SourcePreviewItem) => {
    if (!batchId || !productId) return;
    const productTitle = item?.product_title || "Temu 商品";
    const imageUrl = item?.main_image_url;
    setTemuSkuDrawer({ productId, productTitle, imageUrl, skus: [], loading: true, error: "" });
    try {
      const selections = await priceVerificationApi.listBatchSelections(batchId);
      const selection = selections.find((entry) => entry.skc_id === productId);
      if (!selection) {
        setTemuSkuDrawer({ productId, productTitle, imageUrl, skus: [], loading: false, error: "当前批次未找到该商品的 SKU 采集记录。" });
        return;
      }
      setTemuSkuDrawer({
        productId,
        productTitle: selection.product_title || productTitle,
        imageUrl: selection.main_image_url || imageUrl,
        skus: selection.sku_prices,
        loading: false,
        error: "",
      });
    } catch (error) {
      setTemuSkuDrawer({ productId, productTitle, imageUrl, skus: [], loading: false, error: `读取 Temu SKU 失败：${actionError(error)}` });
    }
  };

  const computeCandidateProfit = async (item: SourcePreviewItem, candidate: SourceCandidate, weightKg: number, priceOverride?: string): Promise<SourceTopProfit | null> => {
    if (!batchId) return null;
    const ctx = item.profit_context;
    if (!ctx?.site || !ctx.selling_price) return null;
    const price = priceOverride !== undefined && priceOverride !== "" ? priceOverride : (candidate.promotion_price ?? candidate.price);
    if (price === undefined || price === null || price === "") return null;
    try {
      return await priceVerificationApi.previewSourceProfit(batchId, {
        site: ctx.site,
        selling_price: ctx.selling_price,
        price,
        moq: candidate.moq ?? null,
        domestic_freight: candidate.domestic_freight ?? null,
        weight_kg: weightKg,
      });
    } catch {
      return null;
    }
  };

  const linkedRecord = (skcKey: string, candidate: SourceCandidate) => {
    const offerId = offerIdFor(candidate);
    return links.find((link) => link.skc_id === skcKey && (link.offer_id === offerId || (candidate.source_url ? link.source_url === candidate.source_url : false)));
  };

  const selectedRecord = (skcKey: string, candidate: SourceCandidate) => {
    const offerId = offerIdFor(candidate);
    return selectedCandidates.find((selected) => selected.skc_id === skcKey && (selected.offer_id === offerId || (candidate.source_url ? selected.source_url === candidate.source_url : false)));
  };

  const isAssociatedCandidate = (skcKey: string, candidate: SourceCandidate) => Boolean(linkedRecord(skcKey, candidate) || selectedRecord(skcKey, candidate));

  const changeCandidatePrice = (item: SourcePreviewItem, candidate: SourceCandidate, rawValue: string) => {
    const candKey = candidateKeyFor(itemKey(item), candidate);
    if (!/^\d*\.?\d*$/.test(rawValue)) return;
    setPriceOverrides((current) => ({ ...current, [candKey]: rawValue }));
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    const rawWeight = Number(weights[candKey]);
    const weightKg = Number.isFinite(rawWeight) && rawWeight > 0 ? rawWeight : 0.5;
    setProfitBusy(candKey);
    void computeCandidateProfit(item, candidate, weightKg, rawValue)
      .then((profit) => setProfitOverrides((current) => ({ ...current, [candKey]: profit })))
      .finally(() => setProfitBusy(""));
    if (isAssociatedCandidate(itemKey(item), candidate)) {
      void onLink(itemKey(item), offerIdFor(candidate), candidate, rawValue, weights[candKey]).catch((error) => {
        onError(`候选源价同步失败：${actionError(error)}。当前输入未丢失，请重试。`);
      });
    }
  };

  const changeCandidateWeight = (item: SourcePreviewItem, candidate: SourceCandidate, rawValue: string) => {
    const candKey = candidateKeyFor(itemKey(item), candidate);
    if (!/^\d*\.?\d*$/.test(rawValue)) return;
    setWeights((current) => ({ ...current, [candKey]: rawValue }));
    const parsed = Number(rawValue);
    if (!Number.isFinite(parsed) || parsed <= 0) return;
    setProfitBusy(candKey);
    void computeCandidateProfit(item, candidate, parsed, priceOverrides[candKey])
      .then((profit) => setProfitOverrides((current) => ({ ...current, [candKey]: profit })))
      .finally(() => setProfitBusy(""));
    if (isAssociatedCandidate(itemKey(item), candidate)) {
      void onLink(itemKey(item), offerIdFor(candidate), candidate, priceOverrides[candKey], rawValue).catch((error) => {
        onError(`候选重量同步失败：${actionError(error)}。当前输入未丢失，请重试。`);
      });
    }
  };

  const linkCandidate = async (group: SourcePreviewSkcGroup, candidate: SourceCandidate) => {
    if (!batchId) return;
    const key = `${group.skc_id}:${candidate.source_url ?? ""}`;
    setBusyLink(key);
    const candKey = candidateKeyFor(group.skc_id, candidate);
    const adjustedPrice = priceOverrides[candKey] !== undefined && priceOverrides[candKey] !== "" ? priceOverrides[candKey] : undefined;
    try {
      const adjustedWeight = weights[candKey] !== undefined && weights[candKey] !== "" ? weights[candKey] : undefined;
      await onLink(group.skc_id, offerIdFor(candidate), candidate, adjustedPrice, adjustedWeight);
    } catch (error) {
      onError(`关联失败：${actionError(error)}。该候选未标记为完成，请重试。`);
    } finally {
      setBusyLink("");
    }
  };

  const unlinkCandidate = async (key: string, linkId: number) => {
    setBusyLink(key);
    try {
      await onUnlink(linkId);
    } catch (error) {
      onError(`解除关联失败：${actionError(error)}。请刷新核对后重试。`);
    } finally {
      setBusyLink("");
    }
  };

  const unselectCandidate = async (key: string, skcId: string, offerId: string) => {
    setBusyLink(key);
    try {
      await onUnselectCandidate(skcId, offerId);
    } catch (error) {
      onError(`撤回候选失败：${actionError(error)}。请刷新核对后重试。`);
    } finally {
      setBusyLink("");
    }
  };

  const submitManualLookup = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!manualLookupSkcId || !manualLookupUrl.trim()) return;
    setManualLookupBusy(true);
    try {
      await onManualLookup(manualLookupSkcId, manualLookupUrl.trim());
      setManualLookupSkcId("");
      setManualLookupUrl("");
    } catch (error) {
      onError(`手动查询失败：${actionError(error)}`);
    } finally {
      setManualLookupBusy(false);
    }
  };

  const groups = preview?.skc_groups?.length ? preview.skc_groups : preview?.items.map((item) => ({
    skc_id: item.skc_id ?? item.quote_key,
    quote_keys: [item.quote_key],
    sku_ids: [],
    items: [item],
  }));
  const candidateCount = preview?.items.reduce((sum, item) => sum + (item.all_candidates?.length ?? item.candidates.length), 0) ?? 0;
  const linkedCount = links.length;

  return (
    <section className="pv-source-workspace">
      <div className="pv-source-head">
        <div>
          {matchingCompleted ? <p className="pv-source-collapsed-note">图搜结果已收起，已关联货源见下方第四板块；点击「重新图搜」可恢复候选列表。</p> : null}
          <h2>货源关联<SectionHelp title="每次搜索均以图片图搜结果为候选依据；商品标题翻译后的中文关键词仅用于辅助确认同一货源，不能单独成为候选。结果按 SKC 分组，每个 SKC 默认展示前 5 条；每条候选均按 Temu 调整后申报价核算利润，源价与重量可调。" /></h2>
        </div>
        <div className="pv-source-head-actions">
          <div className="pv-source-inline-stats" aria-label="图搜统计">
            <span>图搜 SKC <strong>{preview?.items.length ?? 0}</strong></span>
            <span>货源候选 <strong>{candidateCount}</strong></span>
            <span>已关联 1688 <strong>{linkedCount}</strong></span>
          </div>
          <button ref={sourceRunButtonRef} className="pv-source-run-button" onClick={onStart} disabled={!canRunSourceSearch}>{busy ? "图搜执行中…" : preview ? "重新图搜" : `执行图搜（${sourceCount ?? 0}）`}</button>
          {selectedCandidates.length > 0 && !matchingCompleted ? <button className="price-verification-secondary-button" onClick={onComplete} disabled={busy}>完成关联（{selectedCandidates.length}）</button> : null}
        </div>
      </div>

      {/* SKC 分组候选列表 */}
      {!matchingCompleted && (groups?.length ? (
        <div className="pv-source-cards">
          {groups.map((group) => {
            const item = group.items[0];
            const mainImageUrl = item?.main_image_url;
            const cap = item?.max_candidates && item.max_candidates > 0 ? item.max_candidates : 10;
            const all = item?.all_candidates?.length ? item.all_candidates : item?.candidates ?? [];
            const displayLimit = Math.min(CANDIDATE_LIMIT, cap);
            const historyCandidates = all.filter((candidate) => candidate.history_lookup).slice(0, 1);
            const regularCandidates = all.filter((candidate) => !candidate.history_lookup);
            const manualCandidates = regularCandidates.filter((candidate) => candidate.manual_lookup);
            const imageCandidates = regularCandidates.filter((candidate) => !candidate.manual_lookup);
            // 手动核验的链接必须优先展示；纯图搜候选沿用现有首条后移展示规则。
            const rankedCandidates = manualCandidates.length
              ? [...manualCandidates, ...imageCandidates].slice(0, displayLimit)
              : regularCandidates.slice(0, displayLimit);
            const originalCandidates = !manualCandidates.length && rankedCandidates.length > 1
              ? [...rankedCandidates.slice(1), rankedCandidates[0]]
              : rankedCandidates;
            const candidates = [...originalCandidates, ...historyCandidates];
            return (
              <div className="pv-source-result-stack" key={group.skc_id}>
                    <div className="pv-source-result-head">
                      <div className="pv-source-group-title">
                        {mainImageUrl ? <button type="button" className="pv-source-temu-image-trigger" onClick={() => setImagePreviewUrl(mainImageUrl)} aria-label="查看商品大图">
                          <img className="pv-source-temu-image" src={mainImageUrl} alt="" loading="lazy" referrerPolicy="no-referrer" />
                        </button> : null}
                    <button type="button" className="pv-source-temu-id-button" onClick={() => void openTemuSkuDrawer(group.skc_id, item)} title="查看 Temu 商品 SKU">
                      {group.skc_id}
                    </button>
                    <strong>{item?.product_title || "未命名商品"}</strong>
                    <button type="button" className="pv-source-manual-button" disabled={busy || manualLookupBusy} onClick={() => { setManualLookupSkcId(group.skc_id); setManualLookupUrl(""); }}>手动查1688</button>
                  </div>
                </div>
                {candidates.length ? (
                  <div className="pv-source-cards">
                    {candidates.map((candidate) => {
                      const linked = linkedRecord(group.skc_id, candidate);
                      const selected = selectedRecord(group.skc_id, candidate);
                      const isLinked = Boolean(linked || selected);
                      const busyKey = `${group.skc_id}:${candidate.source_url ?? ""}`;
                      const candKey = candidateKeyFor(group.skc_id, candidate);
                      const profit = profitOverrides[candKey] ?? candidate.profit ?? null;
                      const priceText = priceOverrides[candKey] !== undefined ? priceOverrides[candKey] : String(selected?.price_cny ?? candidate.promotion_price ?? candidate.price ?? "");
                      const weightText = weights[candKey] ?? String(selected?.weight_kg ?? "0.5");
                      const metaParts = [
                        candidate.image_search_rank ? `图搜第 ${candidate.image_search_rank} 位` : "",
                        candidate.image_similarity_score != null ? `本地相似度 ${Math.round(candidate.image_similarity_score * 100)}%` : "",
                        candidate.image_similarity_fallback ? (candidate.image_similarity_score == null ? "图搜顺序兜底" : "低于阈值补位") : "",
                        candidate.sales !== undefined ? `销量 ${candidate.sales}` : "",
                      ].filter(Boolean);
                      return (
                        <div className="pv-source-card" key={candKey}>
                          <a className="pv-source-card-media" href={candidate.source_url} target="_blank" rel="noreferrer" title={candidate.source_url}>
                            {candidate.main_image_url ? <img src={candidate.main_image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
                          </a>
                          <div className="pv-source-card-main">
                            <div className="pv-source-card-top">
                              <div className="pv-source-card-info">
                                <span className="pv-source-card-title">{candidate.source_title || "候选商品"}</span>
                                  <div className="pv-source-card-meta">
                                  {candidate.manual_lookup ? <em className="is-manual">手动查询</em> : null}
                                  {candidate.history_lookup ? <em className="is-history">历史匹配</em> : null}
                                  {candidate.source_decision ? <em className="is-plain">{decisionLabel(candidate.source_decision)}</em> : null}
                                  {metaParts.length ? <small>{metaParts.join(" · ")}</small> : null}
                                </div>
                              </div>
                              <div className="pv-source-card-side">
                                <b className="pv-source-price">{moneyText(candidate.promotion_price ?? candidate.price)}</b>
                                <button className={isLinked ? "pv-source-link-button is-linked" : "pv-source-link-button"} onClick={() => linked ? void unlinkCandidate(busyKey, linked.id) : selected ? void unselectCandidate(busyKey, group.skc_id, selected.offer_id) : void linkCandidate(group, candidate)} disabled={busyLink === busyKey || !candidate.source_url} title={isLinked ? "再次点击取消关联" : candidate.source_url || "该候选无 1688 链接，无法关联"}>
                                  {busyLink === busyKey ? "处理中…" : linked ? "✓ 已关联（点击撤回）" : selected ? "✓ 已选择（点击撤回）" : "关联"}
                                </button>
                              </div>
                            </div>
                            {profit?.available ? (
                              <div className="pv-source-profit-summary">
                                <dl className="pv-profit-grid">
                                  <div><dt>站点</dt><dd>{siteLabel(profit.site)}</dd></div>
                                  <div><dt>调整后申报价</dt><dd>{moneyText(profit.selling_price)}</dd></div>
                                  <div><dt>总成本</dt><dd>{moneyText(profit.total_cost)}</dd></div>
                                  <div><dt>净利润</dt><dd className={isNegative(profit.net_profit) ? "is-negative" : "is-positive"}>{moneyText(profit.net_profit)}</dd></div>
                                  <div><dt>利润率</dt><dd className={isNegative(profit.profit_rate) ? "is-negative" : "is-positive"}>{percentText(profit.profit_rate)}</dd></div>
                                </dl>
                                <div className="pv-profit-edit">
                                  <label className="pv-profit-edit-field">候选源价（可调）
                                    <input type="number" min="0.01" step="0.01" value={priceText} onChange={(event) => changeCandidatePrice(item, candidate, event.target.value)} disabled={busy} />
                                    <small>元</small>
                                  </label>
                                  <label className="pv-profit-edit-field">重量（可调）
                                    <input type="number" min="0.1" max="10" step="0.1" value={weightText} onChange={(event) => changeCandidateWeight(item, candidate, event.target.value)} disabled={busy} />
                                    <small>kg</small>
                                  </label>
                                  <em className={profit.qualified ? "pv-profit-badge is-qualified" : "pv-profit-badge is-below"} title={qualificationText(profit.qualification)}>{profit.qualified ? "达标 ✓" : "未达标"}</em>
                                </div>
                              </div>
                            ) : (
                              <div className="pv-profit-empty">
                                <span>利润核算不可用：{profitReasonText(profit?.reason)}</span>
                                {profitBusy === candKey ? <small>（核算中…）</small> : null}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <small className="pv-source-no-candidate">暂无相似品候选</small>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="pv-profit-empty">{preview ? "暂无货源候选" : "等待图搜结果"}</div>
      ))}
      <button
        type="button"
        className={`pv-source-refloat-button ${showRefloatButton ? "is-visible" : ""}`}
        onClick={onStart}
        disabled={!canRunSourceSearch}
        tabIndex={showRefloatButton ? 0 : -1}
        aria-label="重新图搜"
      >
        {busy ? "图搜执行中…" : "重新图搜"}
      </button>
      {temuSkuDrawer ? <div className="pv-temu-sku-drawer-layer" role="presentation" onMouseDown={(event) => {
        if (event.currentTarget === event.target) setTemuSkuDrawer(null);
      }}>
        <aside className="pv-temu-sku-drawer" role="dialog" aria-modal="true" aria-label="Temu 商品 SKU 信息">
          <header className="pv-temu-sku-drawer-head">
            <div><span>TEMU PRODUCT</span><strong>商品 SKU 信息</strong><small>商品 ID：{temuSkuDrawer.productId}</small></div>
            <button type="button" onClick={() => setTemuSkuDrawer(null)} aria-label="关闭 SKU 信息">×</button>
          </header>
          <div className="pv-temu-sku-product">
            {temuSkuDrawer.imageUrl ? <img src={temuSkuDrawer.imageUrl} alt="" referrerPolicy="no-referrer" /> : null}
            <strong>{temuSkuDrawer.productTitle}</strong>
          </div>
          <div className="pv-temu-sku-drawer-body">
            {temuSkuDrawer.loading ? <p className="pv-temu-sku-status">正在读取 SKU 信息…</p> : null}
            {!temuSkuDrawer.loading && temuSkuDrawer.error ? <p className="pv-temu-sku-status is-error">{temuSkuDrawer.error}</p> : null}
            {!temuSkuDrawer.loading && !temuSkuDrawer.error && temuSkuDrawer.skus.length === 0 ? <p className="pv-temu-sku-status">该商品采集时未提供 SKU 明细。</p> : null}
            {!temuSkuDrawer.loading && !temuSkuDrawer.error && temuSkuDrawer.skus.length > 0 ? <div className="pv-temu-sku-table-wrap">
              <table className="pv-temu-sku-table">
                <thead><tr><th>SKU 货号</th><th>调整后申报价格</th></tr></thead>
                <tbody>{temuSkuDrawer.skus.map((sku, index) => <tr key={`${sku.sku_id || "sku"}-${index}`}>
                  <td><strong>{sku.sku_id || "-"}</strong>{sku.sku_attribute_text ? <small>{sku.sku_attribute_text}</small> : null}</td>
                  <td>{temuPriceText(sku)}</td>
                </tr>)}</tbody>
              </table>
            </div> : null}
          </div>
        </aside>
      </div> : null}
      {manualLookupSkcId ? <div className="pv-source-manual-dialog-backdrop" role="presentation" onMouseDown={() => !manualLookupBusy && setManualLookupSkcId("")}>
        <form className="pv-source-manual-dialog" role="dialog" aria-modal="true" aria-labelledby="pv-manual-lookup-title" onSubmit={(event) => void submitManualLookup(event)} onMouseDown={(event) => event.stopPropagation()}>
          <div>
            <p>SKC {manualLookupSkcId}</p>
            <h3 id="pv-manual-lookup-title">手动查1688商品</h3>
          </div>
          <label>1688 商品详情链接
            <input autoFocus value={manualLookupUrl} onChange={(event) => setManualLookupUrl(event.target.value)} placeholder="请输入1688商品详情链接" disabled={manualLookupBusy} />
          </label>
          <small>支持 detail.1688.com/offer/商品ID.html 或带 offer_id 的商品链接。</small>
          <div className="pv-source-manual-dialog-actions">
            <button type="button" onClick={() => setManualLookupSkcId("")} disabled={manualLookupBusy}>取消</button>
            <button type="submit" disabled={!manualLookupUrl.trim() || manualLookupBusy}>{manualLookupBusy ? "查询中…" : "查询并置顶"}</button>
          </div>
        </form>
      </div> : null}
      {imagePreviewUrl ? <div className="pv-source-image-preview-backdrop" role="presentation" onMouseDown={() => setImagePreviewUrl("")}>
        <section className="pv-source-image-preview" role="dialog" aria-modal="true" aria-label="商品大图预览" onMouseDown={(event) => event.stopPropagation()}>
          <button type="button" className="pv-source-image-preview-close" onClick={() => setImagePreviewUrl("")} aria-label="关闭大图预览" autoFocus>×</button>
          <img src={imagePreviewUrl} alt="商品大图" referrerPolicy="no-referrer" />
        </section>
      </div> : null}
    </section>
  );
}

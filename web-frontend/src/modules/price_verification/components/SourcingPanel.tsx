import { useEffect, useState } from "react";

import { priceVerificationApi } from "../api/priceVerificationApi";
import type { SkcSourceLink, SourceCandidate, SourceCandidateSelection, SourcePreview, SourcePreviewItem, SourcePreviewSkcGroup, SourceTopProfit } from "../types";
import { SectionHelp } from "./SectionHelp";
import { WorkflowActionBar, useFloatingActionBar } from "./WorkflowActionBar";
import "../styles/priceVerificationSource.css";

type Props = {
  preview: SourcePreview | null;
  batchId: string;
  busy: boolean;
  sourceCount?: number;
  links: SkcSourceLink[];
  selectedCandidates: SourceCandidateSelection[];
  onLink: (skcId: string, offerId: string, candidate: SourceCandidate, priceOverride?: string, weightOverride?: string) => Promise<void>;
  onUnlink: (linkId: number) => Promise<void>;
  onUnselectCandidate: (skcId: string, offerId: string) => Promise<void>;
  onComplete: () => void;
  onStart: () => void;
  matchingCompleted?: boolean;
};

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
    return `主图已下载、已上传万邦、已图搜${size}`;
  }
  const failed = [audit.downloaded ? "" : "下载", audit.uploaded ? "" : "上传", audit.searched ? "" : "图搜"].filter(Boolean);
  return `图搜链路未完成：${failed.join("、")}`;
}

export function SourcingPanel({ preview, batchId, busy, sourceCount, links, selectedCandidates, onLink, onUnlink, onUnselectCandidate, onComplete, onStart, matchingCompleted = false }: Props) {
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [priceOverrides, setPriceOverrides] = useState<Record<string, string>>({});
  const [profitOverrides, setProfitOverrides] = useState<Record<string, SourceTopProfit | null>>({});
  const [profitBusy, setProfitBusy] = useState("");
  const [busyLink, setBusyLink] = useState("");
  const { actionBarRef, spacerRef } = useFloatingActionBar("top");

  const itemKey = (item: SourcePreviewItem) => item.skc_id ?? item.quote_key;
  const candidateKeyFor = (skcKey: string, candidate: SourceCandidate | null) =>
    candidate ? `${skcKey}:${candidate.offer_id ?? candidate.source_url ?? ""}` : "";

  useEffect(() => {
    setProfitOverrides({});
    setPriceOverrides({});
    setWeights({});
  }, [preview]);

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
      void onLink(itemKey(item), offerIdFor(candidate), candidate, rawValue, weights[candKey]).catch(() => {
        // 静默失败：关联记录价格保留上次已同步的值
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
      void onLink(itemKey(item), offerIdFor(candidate), candidate, priceOverrides[candKey], rawValue).catch(() => {
        // 关联记录保留上次已同步的值
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
    } catch {
      // link errors surface only via the row button staying available
    } finally {
      setBusyLink("");
    }
  };

  const unlinkCandidate = async (key: string, linkId: number) => {
    setBusyLink(key);
    try {
      await onUnlink(linkId);
    } finally {
      setBusyLink("");
    }
  };

  const unselectCandidate = async (key: string, skcId: string, offerId: string) => {
    setBusyLink(key);
    try {
      await onUnselectCandidate(skcId, offerId);
    } finally {
      setBusyLink("");
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
    <section className="pv-source-panel">
      <div className="pv-source-head">
        <div>
          <p className="pv-eyebrow">STEP 03 · SOURCE</p>
          <h2>货源关联<SectionHelp title="每次搜索均以万邦图片图搜结果为候选依据；商品标题翻译后的中文关键词仅用于辅助确认同一货源，不能单独成为候选。结果按 SKC 分组，每个 SKC 默认展示前 5 条；每条候选均按 Temu 调整后申报价核算利润，源价与重量可调。" /></h2>
        </div>
      </div>

      <div ref={spacerRef} className="price-verification-floating-action-spacer" aria-hidden="true" />
      <WorkflowActionBar label="货源匹配操作" floating ref={actionBarRef}>
        <div className="price-verification-action-summary"><span>待图搜</span><strong>{sourceCount ?? 0} 个 SKC</strong></div>
        <div className="price-verification-action-buttons">
          <span className="pv-source-sort-label">候选保留万邦排序，首条置于当前展示末尾</span>
          <button className="price-verification-primary-button" onClick={onStart} disabled={busy || (sourceCount ?? 0) === 0} title={(sourceCount ?? 0) === 0 ? "本轮 SKC 均已复用产品库货源" : undefined}>{busy ? "图搜执行中…" : preview ? "重新图搜" : `执行图搜（${sourceCount ?? 0} 个 SKC）`}</button>
          {selectedCandidates.length > 0 && !matchingCompleted ? <button className="price-verification-secondary-button" onClick={onComplete} disabled={busy}>完成关联（{selectedCandidates.length}）</button> : null}
        </div>
      </WorkflowActionBar>

      {/* 统计 + 排序控件 */}
      <div className="pv-source-stats">
        <div className="pv-source-stat"><span>图搜 SKC</span><strong>{preview?.items.length ?? 0}</strong></div>
        <div className="pv-source-stat"><span>货源候选</span><strong>{candidateCount}</strong></div>
        <div className="pv-source-stat"><span>已关联 1688</span><strong>{linkedCount}</strong></div>
      </div>

      {/* SKC 分组候选列表 */}
      {matchingCompleted ? (
        <div className="pv-profit-empty">
          图搜结果已收起（完成关联），已关联 1688 货源见下方第四板块；点击「重新图搜」可恢复候选列表。
        </div>
      ) : groups?.length ? (
        <div className="pv-source-cards">
          {groups.map((group) => {
            const item = group.items[0];
            const cap = item?.max_candidates && item.max_candidates > 0 ? item.max_candidates : 10;
            const all = item?.all_candidates?.length ? item.all_candidates : item?.candidates ?? [];
            const displayLimit = Math.min(CANDIDATE_LIMIT, cap);
            // 保留万邦原始排序；仅把当前展示范围内的首条放到最后，避免其默认占据首屏。
            const rankedCandidates = all.slice(0, displayLimit);
            const candidates = rankedCandidates.length > 1
              ? [...rankedCandidates.slice(1), rankedCandidates[0]]
              : rankedCandidates;
            const groupLinks = links.filter((link) => link.skc_id === group.skc_id);
            const searchFailed = item?.source_search_status === "failed" || item?.source_search_status === "error";
            return (
              <div className="pv-source-group" key={group.skc_id}>
                <div className="pv-source-group-head">
                  <div className="pv-source-group-title">
                    {item?.main_image_url ? <img className="pv-source-temu-image" src={item.main_image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
                    <span className="pv-source-skc-badge">{group.skc_id}</span>
                    <strong>{item?.product_title || "未命名商品"}</strong>
                  </div>
                  <div className="pv-source-group-badges">
                    <em>图搜 {statusText(item?.source_search_status)}</em>
                    <em className={item?.image_search_audit?.downloaded && item?.image_search_audit?.uploaded && item?.image_search_audit?.searched ? "is-audit-success" : "is-error"} title={item?.image_search_audit?.request_id ? `万邦请求 ${item.image_search_audit.request_id}` : undefined}>{auditText(item)}</em>
                    <em>展示 {candidates.length}/{all.length} 条</em>
                    <em>已关联 1688 {groupLinks.length} 条（下方第四板块）</em>
                    {searchFailed && item?.source_search_error ? <em className="is-error" title={sourceErrorText(item.source_search_error)}>{sourceErrorText(item.source_search_error)}</em> : null}
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
                        candidate.image_search_rank ? `万邦图搜第 ${candidate.image_search_rank} 位` : "",
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
                              <div className="pv-profit-box">
                                <dl className="pv-profit-grid">
                                  <div><dt>站点</dt><dd>{siteLabel(profit.site)}</dd></div>
                                  <div><dt>调整后申报价</dt><dd>{moneyText(profit.selling_price)}</dd></div>
                                  <div><dt>总成本</dt><dd>{moneyText(profit.total_cost)}</dd></div>
                                  <div><dt>净利润</dt><dd className={isNegative(profit.net_profit) ? "is-negative" : "is-positive"}>{moneyText(profit.net_profit)}</dd></div>
                                  <div><dt>利润率</dt><dd className={isNegative(profit.profit_rate) ? "is-negative" : "is-positive"}>{percentText(profit.profit_rate)}</dd></div>
                                </dl>
                                <div className="pv-profit-edit">
                                  <label className="pv-profit-edit-field">候选源价（可调）
                                    <input type="number" min="0.01" step="0.01" value={priceText} onChange={(event) => changeCandidatePrice(item, candidate, event.target.value)} disabled={profitBusy === candKey} />
                                    <small>元</small>
                                  </label>
                                  <label className="pv-profit-edit-field">重量（可调）
                                    <input type="number" min="0.1" max="10" step="0.1" value={weightText} onChange={(event) => changeCandidateWeight(item, candidate, event.target.value)} disabled={profitBusy === candKey} />
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
      )}
    </section>
  );
}

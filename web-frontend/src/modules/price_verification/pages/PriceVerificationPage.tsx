import { useEffect, useMemo, useRef, useState } from "react";

import { priceVerificationApi } from "../api/priceVerificationApi";
import { BatchReviewConfirmPanel } from "../components/BatchReviewConfirmPanel";
import { BatchReviewPanel } from "../components/BatchReviewPanel";
import { LinkedSourcePanel } from "../components/LinkedSourcePanel";
import { PrescreenPanel } from "../components/PrescreenPanel";
import { SourcingPanel } from "../components/SourcingPanel";
import { WorkflowSteps } from "../components/WorkflowSteps";
import type { BatchSelection, PriceVerificationStage, PrescreenSettings, QuoteBatchReviewItem, QuoteCaptureBatch, SkcSourceLink, SourceCandidate, SourcePreview } from "../types";
import "../styles/priceVerification.css";
import "../styles/priceVerificationHero.css";
import "../styles/priceVerificationApi.css";

function errorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : "请求失败";
  if (message.includes("no retained")) return "当前没有已保留的 SKC，无法创建货源图搜任务。";
  return message;
}

// 图搜结果缓存：切走再回来不丢结果，避免重复执行图搜（按批次隔离）
const PV_SOURCE_CACHE_KEY = "price-verification:source-cache:v1";

type SourceCacheEntry = {
  activeStage: PriceVerificationStage;
  sourceSkcIds: string[];
  sourcePreview: SourcePreview | null;
  savedAt: number;
};

function loadSourceCache(batchId: string): SourceCacheEntry | null {
  try {
    const raw = localStorage.getItem(PV_SOURCE_CACHE_KEY);
    if (!raw) return null;
    const map = JSON.parse(raw) as Record<string, SourceCacheEntry>;
    return map[batchId] ?? null;
  } catch {
    return null;
  }
}

function saveSourceCache(batchId: string, entry: Omit<SourceCacheEntry, "savedAt">) {
  try {
    const raw = localStorage.getItem(PV_SOURCE_CACHE_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, SourceCacheEntry>) : {};
    map[batchId] = { ...entry, savedAt: Date.now() };
    localStorage.setItem(PV_SOURCE_CACHE_KEY, JSON.stringify(map));
  } catch {
    // localStorage 不可用或超出配额时静默忽略，不影响主流程
  }
}

export function PriceVerificationPage() {
  const [activeStage, setActiveStage] = useState<PriceVerificationStage>("collect");
  const [captureBatches, setCaptureBatches] = useState<QuoteCaptureBatch[]>([]);
  const [batchItems, setBatchItems] = useState<QuoteBatchReviewItem[]>([]);
  const [batchSelections, setBatchSelections] = useState<BatchSelection[]>([]);
  const [prescreen, setPrescreen] = useState<PrescreenSettings | null>(null);
  const [sourceSkcIds, setSourceSkcIds] = useState<string[]>([]);
  const [sourcePreview, setSourcePreview] = useState<SourcePreview | null>(null);
  const [sourceLinks, setSourceLinks] = useState<SkcSourceLink[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [notice, setNotice] = useState("正在读取核价批次…");

  const currentBatchId = useMemo(() => captureBatches.find((batch) => batch.is_current)?.batch_id ?? "", [captureBatches]);
  const sourceSkcBatchRef = useRef("");

  const loadBatchSelections = async (batchId: string) => {
    try {
      const selections = await priceVerificationApi.listBatchSelections(batchId);
      setBatchSelections(selections);
      if (sourceSkcBatchRef.current !== batchId) {
        sourceSkcBatchRef.current = batchId;
        const cached = loadSourceCache(batchId);
        if (!cached?.sourceSkcIds?.length) {
          setSourceSkcIds([]);
        }
      }
    } catch {
      setBatchSelections([]);
    }
  };

  const loadSourceLinks = async (batchId: string) => {
    if (!batchId) return setSourceLinks([]);
    try {
      setSourceLinks(await priceVerificationApi.listSkcSourceLinks(batchId));
    } catch {
      setSourceLinks([]);
    }
  };

  const refresh = async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const nextBatches = await priceVerificationApi.listCaptureBatches();
      setCaptureBatches(nextBatches);
      const currentBatch = nextBatches.find((batch) => batch.is_current);
      try {
        setPrescreen(await priceVerificationApi.getPrescreen());
      } catch { setPrescreen(null); }
      if (currentBatch) {
        try {
          setBatchItems(await priceVerificationApi.listCaptureBatchItems(currentBatch.batch_id));
        } catch { setBatchItems([]); }
        await loadBatchSelections(currentBatch.batch_id);
        await loadSourceLinks(currentBatch.batch_id);
      } else {
        setBatchItems([]);
        setBatchSelections([]);
        setSourceLinks([]);
      }
      if (!quiet) {
        const batchMessage = currentBatch
          ? currentBatch.quote_count
            ? `当前批次已入库 ${currentBatch.quote_count} 条报价，初筛后 ${batchItems.length} 条进入确认；勾选保留的商品确认后会进入待审列表。`
            : "当前批次暂无报价，可在 Temu 页面用插件采集本页数据后回此页刷新。"
          : "等待插件采集：在 Temu“批量查看并确认申报价”页点“采集核价本页”即可入库。";
        setNotice(batchMessage);
      }
    } catch (error) {
      setNotice(`读取运行状态失败：${errorMessage(error)}`);
    } finally {
      if (!quiet) setLoading(false);
    }
  };

  useEffect(() => { void refresh(false); }, []);

  // 批次就绪后从缓存恢复图搜结果 / 勾选 / 阶段
  useEffect(() => {
    if (!currentBatchId) return;
    const cached = loadSourceCache(currentBatchId);
    if (!cached) return;
    if (cached.sourcePreview) setSourcePreview(cached.sourcePreview);
    if (cached.sourceSkcIds?.length) setSourceSkcIds(cached.sourceSkcIds);
    if (cached.activeStage) {
      setActiveStage(cached.activeStage === "source" && !cached.sourcePreview ? "collect" : cached.activeStage);
    }
  }, [currentBatchId]);

  // 状态变化时写回缓存；恢复瞬间（preview 尚未从缓存取出）不要清空旧缓存
  useEffect(() => {
    if (!currentBatchId) return;
    const cached = loadSourceCache(currentBatchId);
    if (sourcePreview === null && cached?.sourcePreview) return;
    saveSourceCache(currentBatchId, { activeStage, sourceSkcIds, sourcePreview });
  }, [currentBatchId, activeStage, sourceSkcIds, sourcePreview]);

  const savePrescreen = async (minAdjustedPriceCny: string) => {
    try {
      const settings = await priceVerificationApi.setPrescreen(minAdjustedPriceCny);
      setPrescreen(settings);
      setNotice(settings.min_adjusted_price_cny != null && settings.min_adjusted_price_cny !== ""
        ? `初筛条件已保存：调整后申报价（CNY）需大于 ${settings.min_adjusted_price_cny}，STEP 02 将只显示通过初筛的商品。`
        : "初筛条件已清除，采集数据将全部进入 STEP 02。");
      await refresh(true);
    } catch (error) { setNotice(`保存初筛条件失败：${errorMessage(error)}`); }
  };

  const stageBatchToReview = async (batchId: string, skcIds: string[], maxCandidates: number) => {
    try {
      const selections = await priceVerificationApi.stageBatchSelections(batchId, skcIds, maxCandidates);
      setBatchSelections((current) => {
        const next = [...current];
        for (const selection of selections) {
          const index = next.findIndex((item) => item.skc_id === selection.skc_id);
          if (index >= 0) next[index] = selection; else next.push(selection);
        }
        return next;
      });
      setActiveStage("review");
      setNotice(`已将 ${selections.length} 个 SKC 加入待审列表（图搜相似品数量 ${maxCandidates}），请在下方“待审商品最终确认”中逐条保留或删除。`);
    } catch (error) { setNotice(`加入待审列表失败：${errorMessage(error)}`); }
  };

  const deleteBatchItem = async (batchId: string, skcId: string) => {
    if (!batchId) return;
    try {
      const result = await priceVerificationApi.removeCaptureBatchItem(batchId, skcId);
      setBatchItems((current) => current.filter((item) => item.skc_id !== skcId));
      setNotice(`已删除 SKC ${result.skc_id}（移除 ${result.removed} 条报价），不再出现在本次批次报价审核中。`);
      await refresh(true);
    } catch (error) { setNotice(`删除批次报价失败：${errorMessage(error)}`); }
  };

  const deleteBatchItems = async (batchId: string, skcIds: string[]) => {
    if (!batchId || !skcIds.length) return;
    try {
      let removed = 0;
      for (const skcId of skcIds) {
        const result = await priceVerificationApi.removeCaptureBatchItem(batchId, skcId);
        removed += result.removed;
      }
      const removedSet = new Set(skcIds);
      setBatchItems((current) => current.filter((item) => !removedSet.has(item.skc_id)));
      setNotice(`已删除选中的 ${skcIds.length} 个 SKC（移除 ${removed} 条报价），不再出现在本次批次报价审核中。`);
      await refresh(true);
    } catch (error) { setNotice(`删除选中报价失败：${errorMessage(error)}`); }
  };

  const reviewBatchSelection = async (batchId: string, selectionId: number, decision: "retained" | "deleted", maxCandidates: number) => {
    setBusyKey(String(selectionId));
    try {
      const updated = await priceVerificationApi.reviewBatchSelection(batchId, selectionId, decision, maxCandidates);
      setBatchSelections((current) => decision === "deleted"
        ? current.filter((item) => item.id !== selectionId)
        : current.map((item) => item.id === selectionId ? updated : item));
      if (decision === "deleted") {
        setSourceSkcIds((current) => current.filter((id) => id !== updated.skc_id));
      }
      setNotice(decision === "retained"
        ? `已保留 SKC ${updated.skc_id}，并写入草稿池${updated.draft_created ? "" : "（该商品已在草稿池中）"}。`
        : `已删除 SKC ${updated.skc_id}，不再参与货源匹配。`);
    } catch (error) { setNotice(`最终确认失败：${errorMessage(error)}`); } finally { setBusyKey(""); }
  };

  const startBatchSourcing = async (rankingMode: "similarity" | "price" = "similarity", keywordSearch = false) => {
    if (!currentBatchId || !sourceSkcIds.length) return;
    setBusyKey("source");
    try {
      const preview = await priceVerificationApi.sourceBatchSelections(currentBatchId, rankingMode, sourceSkcIds, keywordSearch);
      setSourcePreview(preview);
      setActiveStage("source");
      setNotice(`货源图搜完成（${keywordSearch ? "图搜 + 标题关键词" : "图搜"}，按${rankingMode === "price" ? "价格从低到高" : "相似度"}排序），获得 ${preview.counts?.candidate_count ?? 0} 个候选货源。`);
    } catch (error) { setNotice(`创建货源图搜任务失败：${errorMessage(error)}`); } finally { setBusyKey(""); }
  };

  const linkSkcSource = async (skcId: string, offerId: string, candidate: SourceCandidate, priceOverride?: string) => {
    if (!currentBatchId) return;
    const price = priceOverride && priceOverride !== "" ? priceOverride : (candidate.source_price_cny ?? candidate.price ?? null);
    const link = await priceVerificationApi.linkSkcSource(currentBatchId, {
      skc_id: skcId,
      offer_id: offerId,
      source_url: candidate.source_url ?? "",
      source_title: candidate.source_title,
      main_image_url: candidate.main_image_url,
      price_cny: price,
      moq: candidate.moq ?? null,
      domestic_freight_cny: candidate.domestic_freight ?? null,
      source_decision: candidate.source_decision,
    });
    setSourceLinks((current) => [...current.filter((item) => item.offer_id !== link.offer_id), link]);
    setNotice(`已关联 1688 货源 ${link.offer_id}，并自动同步至产品库。`);
  };

  const unlinkSkcSource = async (linkId: number) => {
    if (!currentBatchId) return;
    await priceVerificationApi.removeSkcSourceLink(currentBatchId, linkId);
    setSourceLinks((current) => current.filter((item) => item.id !== linkId));
    setNotice("已解除该 1688 货源关联，产品库记录已同步更新。");
  };

  return <div className="price-verification-page">
    <section className="price-verification-hero"><div><p className="eyebrow">PRICE VERIFICATION · LOCAL WORKSPACE</p><h1>核价及货源</h1><p>插件采集 Temu 本页报价（每页最多 50 个 SKC），新采集覆盖旧数据，后台按申报价初筛，人工确认后写入草稿池并匹配至可追溯货源。</p></div><div className="price-verification-hero-status"><span className="status-dot" />{currentBatchId ? "当前批次已就绪" : "等待插件采集"}</div></section>
    <section className="price-verification-workflow-card"><div className="price-verification-workflow-heading"><span>◇</span><strong>核价及货源工作流</strong><small>按顺序完成，数据全程保留关联关系</small></div><WorkflowSteps activeStage={activeStage} /></section>
    <p className="price-verification-notice" role="status" aria-live="polite">{notice}</p>
    <div className="price-verification-content-grid"><div className="price-verification-main-column"><PrescreenPanel isChecking={loading} totalItems={captureBatches.find((batch) => batch.is_current)?.quote_count ?? 0} totalSkc={captureBatches.find((batch) => batch.is_current)?.skc_count ?? 0} passedItems={batchItems.length} prescreen={prescreen} onPrescreenChange={(value) => savePrescreen(value)} onRefresh={() => void refresh()} /><BatchReviewPanel batchId={currentBatchId} items={batchItems} busy={loading} onConfirm={(batchId, skcIds, maxCandidates) => stageBatchToReview(batchId, skcIds, maxCandidates)} onDelete={(batchId, skcId) => deleteBatchItem(batchId, skcId)} onDeleteSelected={(batchId, skcIds) => deleteBatchItems(batchId, skcIds)} /><BatchReviewConfirmPanel batchId={currentBatchId} selections={batchSelections} busy={busyKey === "source" || loading} sourceSkcIds={sourceSkcIds} onSourceSelectionChange={setSourceSkcIds} onReview={(batchId, selectionId, decision, maxCandidates) => reviewBatchSelection(batchId, selectionId, decision, maxCandidates)} /><SourcingPanel preview={sourcePreview} batchId={currentBatchId} busy={busyKey === "source" || loading} sourceCount={batchSelections.filter((item) => item.status === "retained" && sourceSkcIds.includes(item.skc_id)).length} links={sourceLinks} onLink={(skcId, offerId, candidate, priceOverride) => linkSkcSource(skcId, offerId, candidate, priceOverride)} onStart={(mode) => void startBatchSourcing(mode)} /><LinkedSourcePanel links={sourceLinks} onUnlink={(linkId) => unlinkSkcSource(linkId)} /></div></div>
  </div>;
}

import { useEffect, useMemo, useRef, useState } from "react";

import { priceVerificationApi } from "../api/priceVerificationApi";
import { BatchReviewConfirmPanel } from "../components/BatchReviewConfirmPanel";
import { BatchReviewPanel } from "../components/BatchReviewPanel";
import { LinkedSourcePanel } from "../components/LinkedSourcePanel";
import { PluginSessionPanel } from "../components/PluginSessionPanel";
import { SourcingPanel } from "../components/SourcingPanel";
import { WorkflowSteps } from "../components/WorkflowSteps";
import type { BatchSelection, PriceVerificationStage, QuoteBatchReviewItem, QuoteCaptureBatch, SkcSourceLink, SourceCandidate, SourcePreview } from "../types";
import "../styles/priceVerification.css";
import "../styles/priceVerificationApi.css";

function errorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : "请求失败";
  if (message.includes("no retained")) return "当前没有已保留的 SKC，无法创建货源图搜任务。";
  return message;
}

export function PriceVerificationPage() {
  const [activeStage, setActiveStage] = useState<PriceVerificationStage>("collect");
  const [captureBatches, setCaptureBatches] = useState<QuoteCaptureBatch[]>([]);
  const [batchItems, setBatchItems] = useState<QuoteBatchReviewItem[]>([]);
  const [batchSelections, setBatchSelections] = useState<BatchSelection[]>([]);
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
        setSourceSkcIds(selections.filter((item) => item.status === "retained").map((item) => item.skc_id));
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
      if (currentBatch?.quote_count) {
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
        const batchMessage = currentBatch?.quote_count
          ? `当前批次已入库 ${currentBatch.quote_count} 条报价；勾选保留的商品确认后会进入待审列表。`
          : currentBatch ? "当前批次暂无报价，可在 Temu 页面用插件采集后回此页刷新。" : "请先新建核价批次。";
        setNotice(batchMessage);
      }
    } catch (error) {
      setNotice(`读取运行状态失败：${errorMessage(error)}`);
    } finally {
      if (!quiet) setLoading(false);
    }
  };

  useEffect(() => { void refresh(false); }, []);

  const createCaptureBatch = async () => {
    const name = window.prompt("请输入核价批次名称（例如：店铺 A 第 1 批）")?.trim();
    if (!name) return;
    setLoading(true);
    try {
      const batch = await priceVerificationApi.createCaptureBatch(name);
      await refresh(true);
      setNotice(`已启用核价批次“${batch.name}”。可在 Temu 页面用插件采集数据，完成后回此页刷新。`);
    } catch (error) { setNotice(`新建核价批次失败：${errorMessage(error)}`); } finally { setLoading(false); }
  };

  const activateCaptureBatch = async (batchId: string) => {
    if (!batchId) return;
    setLoading(true);
    try {
      const batch = await priceVerificationApi.activateCaptureBatch(batchId);
      await refresh(true);
      setNotice(`已切换到核价批次“${batch.name}”，可继续在 Temu 页面用插件采集。`);
    } catch (error) { setNotice(`切换核价批次失败：${errorMessage(error)}`); } finally { setLoading(false); }
  };

  const stageBatchToReview = async (batchId: string, skcIds: string[]) => {
    try {
      const selections = await priceVerificationApi.stageBatchSelections(batchId, skcIds);
      setBatchSelections((current) => {
        const next = [...current];
        for (const selection of selections) {
          const index = next.findIndex((item) => item.skc_id === selection.skc_id);
          if (index >= 0) next[index] = selection; else next.push(selection);
        }
        return next;
      });
      setActiveStage("review");
      setNotice(`已将 ${selections.length} 个 SKC 加入待审列表，请在下方“待审商品最终确认”中逐条保留或删除。`);
    } catch (error) { setNotice(`加入待审列表失败：${errorMessage(error)}`); }
  };

  const reviewBatchSelection = async (batchId: string, selectionId: number, decision: "retained" | "deleted", maxCandidates: number) => {
    setBusyKey(String(selectionId));
    try {
      const updated = await priceVerificationApi.reviewBatchSelection(batchId, selectionId, decision, maxCandidates);
      setBatchSelections((current) => decision === "deleted"
        ? current.filter((item) => item.id !== selectionId)
        : current.map((item) => item.id === selectionId ? updated : item));
      setSourceSkcIds((current) => decision === "retained"
        ? (current.includes(updated.skc_id) ? current : [...current, updated.skc_id])
        : current.filter((id) => id !== updated.skc_id));
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
    <section className="price-verification-hero"><div><p className="eyebrow">PRICE VERIFICATION · LOCAL WORKSPACE</p><h1>核价及货源</h1><p>核验平台报价，勾选确认后重组待审，最终保留的商品写入草稿池并匹配至可追溯货源。</p></div><div className="price-verification-hero-status"><span className="status-dot" />{currentBatchId ? "当前批次已就绪" : "未创建核价批次"}</div></section>
    <section className="price-verification-workflow-card"><div className="price-verification-workflow-heading"><span>◇</span><strong>核价及货源工作流</strong><small>按顺序完成，数据全程保留关联关系</small></div><WorkflowSteps activeStage={activeStage} /></section>
    <p className="price-verification-notice" role="status" aria-live="polite">{notice}</p>
    <div className="price-verification-content-grid"><div className="price-verification-main-column"><PluginSessionPanel isChecking={loading} batches={captureBatches} onRefresh={() => void refresh()} onCreateBatch={() => void createCaptureBatch()} onActivateBatch={(batchId) => void activateCaptureBatch(batchId)} /><BatchReviewPanel batchId={currentBatchId} items={batchItems} busy={loading} onConfirm={(batchId, skcIds) => stageBatchToReview(batchId, skcIds)} /><BatchReviewConfirmPanel batchId={currentBatchId} selections={batchSelections} busy={busyKey === "source" || loading} sourceSkcIds={sourceSkcIds} onSourceSelectionChange={setSourceSkcIds} onReview={(batchId, selectionId, decision, maxCandidates) => reviewBatchSelection(batchId, selectionId, decision, maxCandidates)} /><SourcingPanel preview={sourcePreview} batchId={currentBatchId} busy={busyKey === "source" || loading} sourceCount={batchSelections.filter((item) => item.status === "retained" && sourceSkcIds.includes(item.skc_id)).length} links={sourceLinks} onLink={(skcId, offerId, candidate, priceOverride) => linkSkcSource(skcId, offerId, candidate, priceOverride)} onStart={(mode) => void startBatchSourcing(mode)} /><LinkedSourcePanel links={sourceLinks} onUnlink={(linkId) => unlinkSkcSource(linkId)} /></div></div>
  </div>;
}

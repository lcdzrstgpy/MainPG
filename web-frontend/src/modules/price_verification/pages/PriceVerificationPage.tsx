import { useEffect, useMemo, useState } from "react";

import { useChangePoller } from "../../../shared/hooks/useChangePoller";
import { priceVerificationApi } from "../api/priceVerificationApi";
import { BatchReviewPanel } from "../components/BatchReviewPanel";
import { LinkedSourcePanel } from "../components/LinkedSourcePanel";
import { PrescreenPanel } from "../components/PrescreenPanel";
import { SourcingPanel } from "../components/SourcingPanel";
import { WorkflowSteps } from "../components/WorkflowSteps";
import type { BatchSelection, BatchSourcingState, PriceVerificationStage, PrescreenSettings, QuoteBatchReviewItem, QuoteCaptureBatch, SkcSourceLink, SourceCandidate, SourcePreview } from "../types";
import "../styles/priceVerification.css";
import "../styles/priceVerificationHero.css";
import "../styles/priceVerificationApi.css";

const emptySourcingState = (): BatchSourcingState => ({
  selected_skc_ids: [], unresolved_skc_ids: [], matched_products: [], preview: null, selected_candidates: [],
});

function errorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : "请求失败";
  if (message.includes("no retained")) return "当前没有已保留的 SKC，无法创建货源图搜任务。";
  if (message.includes("select at least")) return "请先在图搜结果中选择至少一个候选货源后再完成入库。";
  return message;
}

export function PriceVerificationPage() {
  const [activeStage, setActiveStage] = useState<PriceVerificationStage>("prescreen");
  const [captureBatches, setCaptureBatches] = useState<QuoteCaptureBatch[]>([]);
  const [batchItems, setBatchItems] = useState<QuoteBatchReviewItem[]>([]);
  const [batchSelections, setBatchSelections] = useState<BatchSelection[]>([]);
  const [prescreen, setPrescreen] = useState<PrescreenSettings | null>(null);
  const [sourceSkcIds, setSourceSkcIds] = useState<string[]>([]);
  const [sourcingState, setSourcingState] = useState<BatchSourcingState>(emptySourcingState);
  const [sourceLinks, setSourceLinks] = useState<SkcSourceLink[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [notice, setNotice] = useState("正在读取核价批次…");

  const currentBatchId = useMemo(() => captureBatches.find((batch) => batch.is_current)?.batch_id ?? "", [captureBatches]);
  const scrollWorkflowTop = () => requestAnimationFrame(() => {
    document.querySelector<HTMLElement>(".content-card")?.scrollTo({ top: 0, behavior: "smooth" });
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  const refresh = async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const batches = await priceVerificationApi.listCaptureBatches();
      setCaptureBatches(batches);
      const batch = batches.find((item) => item.is_current);
      try { setPrescreen(await priceVerificationApi.getPrescreen()); } catch { setPrescreen(null); }
      if (!batch) {
        setBatchItems([]); setBatchSelections([]); setSourceSkcIds([]); setSourcingState(emptySourcingState());
        if (!quiet) setNotice("等待插件采集：在 Temu“批量查看并确认申报价”页点“采集核价本页”即可入库。");
        return;
      }
      const [items, selections, sourceState] = await Promise.all([
        priceVerificationApi.listCaptureBatchItems(batch.batch_id).catch(() => []),
        priceVerificationApi.listBatchSelections(batch.batch_id).catch(() => []),
        priceVerificationApi.getBatchSourcingState(batch.batch_id).catch(emptySourcingState),
      ]);
      setBatchItems(items);
      setBatchSelections(selections);
      setSourcingState(sourceState);
      if (sourceState.selected_skc_ids.length) setSourceSkcIds(sourceState.selected_skc_ids);
      if (!quiet) setNotice(batch.quote_count
        ? `当前批次已入库 ${batch.quote_count} 条报价，初筛后 ${items.length} 个 SKC 可直接选择并执行图搜。`
        : "当前批次暂无报价，可在 Temu 页面用插件采集本页数据后回此页刷新。");
    } catch (error) {
      setNotice(`读取运行状态失败：${errorMessage(error)}`);
    } finally {
      if (!quiet) setLoading(false);
    }
  };

  useEffect(() => { void refresh(false); }, []);

  // 容器级自动刷新：插件采集核价本页/核价确认后，批次 revision 变化即静默重拉数据。
  useChangePoller({
    url: "/api/v1/price-verification/capture-batches/revision",
    onChange: () => void refresh(true),
  });

  const savePrescreen = async (minAdjustedPriceCny: string) => {
    try {
      const settings = await priceVerificationApi.setPrescreen(minAdjustedPriceCny);
      setPrescreen(settings);
      setNotice(settings.min_adjusted_price_cny != null && settings.min_adjusted_price_cny !== ""
        ? `初筛条件已保存：调整后申报价（CNY）需大于 ${settings.min_adjusted_price_cny}。`
        : "初筛条件已清除，采集数据将全部进入批次审核。");
      await refresh(true);
      return true;
    } catch (error) { setNotice(`保存初筛条件失败：${errorMessage(error)}`); return false; }
  };

  const stageBatchAndStartSourcing = async (batchId: string, skcIds: string[], _maxCandidates: number) => {
    setBusyKey("prepare-source");
    try {
      const selections = await priceVerificationApi.stageBatchSelections(batchId, skcIds, 5);
      let failed = 0;
      const retained: BatchSelection[] = [];
      for (const selection of selections) {
        try { retained.push(await priceVerificationApi.reviewBatchSelection(batchId, selection.id, "retained", 5)); } catch { failed += 1; }
      }
      setBatchSelections(retained);
      if (!retained.length) {
        setNotice("未能保留选中的 SKC，无法执行图搜。");
        return;
      }
      const state = await priceVerificationApi.prepareBatchSourcing(batchId, retained.map((selection) => selection.skc_id));
      setSourceSkcIds(state.selected_skc_ids);
      // 进入新一轮图搜时，不能继续展示上一轮候选；等本轮接口返回后再回填预览。
      setSourcingState({ ...state, preview: null, selected_candidates: [] });
      setSourceLinks([]);
      setActiveStage("sourcing");
      scrollWorkflowTop();
      if (!state.unresolved_skc_ids.length) {
        setSourcingState(state);
        setNotice(`已保留 ${retained.length} 个 SKC，全部复用产品库已有货源，无需图搜。`);
        return;
      }
      const preview = await priceVerificationApi.sourceBatchSelections(batchId, state.unresolved_skc_ids);
      setSourcingState({ ...state, preview });
      setNotice(failed
        ? `已保留 ${retained.length} 个 SKC，${failed} 个写入草稿池失败；其余 SKC 已完成图片+标题双路图搜。`
        : `已保留 ${retained.length} 个 SKC；产品库复用 ${state.matched_products.length} 个，其余 ${state.unresolved_skc_ids.length} 个已完成图片+标题双路图搜。`);
    } catch (error) { setNotice(`确认并执行图搜失败：${errorMessage(error)}`); } finally { setBusyKey(""); }
  };

  const deleteBatchItem = async (batchId: string, skcId: string) => {
    try {
      const result = await priceVerificationApi.removeCaptureBatchItem(batchId, skcId);
      setBatchItems((current) => current.filter((item) => item.skc_id !== skcId));
      setNotice(`已删除 SKC ${result.skc_id}（移除 ${result.removed} 条报价）。`);
    } catch (error) { setNotice(`删除批次报价失败：${errorMessage(error)}`); }
  };

  const deleteBatchItems = async (batchId: string, skcIds: string[]) => {
    try {
      for (const skcId of skcIds) await priceVerificationApi.removeCaptureBatchItem(batchId, skcId);
      setBatchItems((current) => current.filter((item) => !skcIds.includes(item.skc_id)));
      setNotice(`已删除选中的 ${skcIds.length} 个 SKC。`);
    } catch (error) { setNotice(`删除选中报价失败：${errorMessage(error)}`); }
  };

  const startBatchSourcing = async () => {
    if (!currentBatchId || !sourcingState.unresolved_skc_ids.length) return;
    setBusyKey("source");
    try {
      const preview = await priceVerificationApi.sourceBatchSelections(currentBatchId, sourcingState.unresolved_skc_ids);
      setSourcingState((current) => ({ ...current, preview }));
      setNotice(`货源图搜完成，获得 ${preview.counts?.candidate_count ?? 0} 个候选货源。请选择候选后完成入库。`);
    } catch (error) { setNotice(`执行图搜失败：${errorMessage(error)}`); } finally { setBusyKey(""); }
  };

  // 已关联 1688 货源列表（SourcingPanel 持久化关联模型），进入图搜阶段或关联变化时拉取
  const loadSourceLinks = async () => {
    if (!currentBatchId) return;
    try {
      setSourceLinks(await priceVerificationApi.listSkcSourceLinks(currentBatchId));
    } catch { /* 列表加载失败静默，面板内不阻塞操作 */ }
  };

  useEffect(() => {
    if (activeStage === "sourcing" && currentBatchId) void loadSourceLinks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStage, currentBatchId]);

  const selectSourceCandidate = async (skcId: string, candidate: SourceCandidate, priceOverride?: string, weightOverride?: string) => {
    if (!currentBatchId) return;
    const state = await priceVerificationApi.selectBatchSourceCandidate(currentBatchId, skcId, candidate, priceOverride, weightOverride);
    setSourcingState(state);
    await loadSourceLinks();
  };

  const unselectSourceCandidate = async (skcId: string, offerId: string) => {
    if (!currentBatchId) return;
    const state = await priceVerificationApi.unselectBatchSourceCandidate(currentBatchId, skcId, offerId);
    setSourcingState(state);
  };

  // SourcingPanel 新 API：按持久化链接 id 解除关联
  const removeSourceLink = async (linkId: number) => {
    if (!currentBatchId) return;
    try {
      await priceVerificationApi.removeSkcSourceLink(currentBatchId, linkId);
      await loadSourceLinks();
      setSourcingState(await priceVerificationApi.getBatchSourcingState(currentBatchId));
    } catch (error) { setNotice(`解除关联失败：${errorMessage(error)}`); }
  };

  const completeSourcing = async () => {
    if (!currentBatchId) return;
    setBusyKey("complete-source");
    try {
      const state = await priceVerificationApi.completeBatchSourcing(currentBatchId);
      setSourcingState(state);
      setNotice("已将本轮明确关联的候选入库；STEP 03 图搜临时数据已清空。");
    } catch (error) { setNotice(`完成关联失败：${errorMessage(error)}`); } finally { setBusyKey(""); }
  };

  const canOpenStage = (stage: PriceVerificationStage) => {
    if (stage === "prescreen") return true;
    if (stage === "batchReview") return Boolean(currentBatchId);
    return sourceSkcIds.length > 0 || sourcingState.selected_skc_ids.length > 0;
  };

  const openStage = (stage: PriceVerificationStage) => {
    if (!canOpenStage(stage)) { setNotice("请先按顺序完成上一步。 "); return; }
    setActiveStage(stage); scrollWorkflowTop();
  };

  const sourcedProducts = sourcingState.matched_products;
  const sourceCount = sourcingState.unresolved_skc_ids.length;
  const showLinkedSourcePanel = sourcedProducts.length > 0 && sourceCount === 0 && sourcingState.preview === null;
  const currentBatch = captureBatches.find((batch) => batch.is_current);
  const batchSummary = currentBatch?.quote_count
    ? `当前批次已入库 ${currentBatch.quote_count} 条报价，初筛后 ${batchItems.length} 个 SKC 可直接选择并执行图搜。`
    : "";
  const showNotice = Boolean(notice) && notice !== "正在读取核价批次…" && notice !== batchSummary;
  return <div className="price-verification-page">
    <section className="price-verification-hero"><div><p className="eyebrow">PRICE VERIFICATION · LOCAL WORKSPACE</p><h1>核价及货源</h1><p>插件采集 Temu 本页报价，人工确认本轮 SKC；优先复用产品库已有货源，仅对未入库 SKC 执行 1688 图搜。</p></div><div className="price-verification-hero-status"><span className="status-dot" />{currentBatchId ? "当前批次已就绪" : "等待插件采集"}</div></section>
    <section className="price-verification-workflow-strip"><div className="price-verification-workflow-heading"><span>◇</span><strong>核价及货源工作流</strong><small>本轮 SKC 独立流转，历史数据不参与图搜</small></div><WorkflowSteps activeStage={activeStage} canOpen={canOpenStage} onOpen={openStage} /></section>
    {showNotice ? <p className="price-verification-notice price-verification-notice-compact" role="status" aria-live="polite">{notice}</p> : null}
    <div className="price-verification-content-grid"><div className="price-verification-main-column">
      {activeStage !== "prescreen" && <div className="price-verification-stage-tools"><button type="button" className="price-verification-back-button" onClick={() => openStage(activeStage === "batchReview" ? "prescreen" : "batchReview")}>← 返回上一步</button>{batchSummary ? <span>{batchSummary}</span> : null}</div>}
      {activeStage === "prescreen" && <PrescreenPanel isChecking={loading} totalItems={captureBatches.find((batch) => batch.is_current)?.quote_count ?? 0} totalSkc={captureBatches.find((batch) => batch.is_current)?.skc_count ?? 0} passedItems={batchItems.length} prescreen={prescreen} onPrescreenChange={savePrescreen} onRefresh={() => void refresh()} onContinue={() => openStage("batchReview")} />}
      {activeStage === "batchReview" && <BatchReviewPanel batchId={currentBatchId} items={batchItems} busy={Boolean(busyKey) || loading} onConfirm={(batchId, skcIds, maxCandidates) => stageBatchAndStartSourcing(batchId, skcIds, maxCandidates)} onDelete={(batchId, skcId) => deleteBatchItem(batchId, skcId)} onDeleteSelected={(batchId, skcIds) => deleteBatchItems(batchId, skcIds)} />}
      {activeStage === "sourcing" && <><SourcingPanel preview={sourcingState.preview} batchId={currentBatchId} busy={Boolean(busyKey) || loading} sourceCount={sourceCount} links={sourceLinks} selectedCandidates={sourcingState.selected_candidates} onLink={(skcId, _offerId, candidate, priceOverride, weightOverride) => selectSourceCandidate(skcId, candidate, priceOverride, weightOverride)} onUnlink={(linkId) => removeSourceLink(linkId)} onUnselectCandidate={(skcId, offerId) => unselectSourceCandidate(skcId, offerId)} onComplete={() => void completeSourcing()} onStart={() => void startBatchSourcing()} matchingCompleted={sourceCount === 0 && sourcingState.preview === null} />{showLinkedSourcePanel ? <LinkedSourcePanel products={sourcedProducts} /> : null}</>}
    </div></div>
  </div>;
}

import { useEffect, useMemo, useRef, useState } from "react";
import { getAuthAccount } from "../../../transport/http/client";
import { podCustomizationApi } from "../../pod_customization/api/podCustomizationApi";
import { CompositionEditDrawer } from "../../pod_customization/components/CompositionEditDrawer";
import { CompositionGenerateDrawer } from "../../pod_customization/components/CompositionGenerateDrawer";
import { CompositionManagerDrawer } from "../../pod_customization/components/CompositionManagerDrawer";
import { PodBatchGallery } from "../../pod_customization/components/PodBatchGallery";
import { PodBatchHistoryDrawer } from "../../pod_customization/components/PodBatchHistoryDrawer";
import { PodFailedRetryDialog } from "../../pod_customization/components/PodFailedRetryDialog";
import { PodListingFieldsEditor, validateListingFields } from "../../pod_customization/components/PodListingFieldsEditor";
import { PodResultLightbox } from "../../pod_customization/components/PodResultLightbox";
import { SpecCardDrawer } from "../../pod_customization/components/SpecCardDrawer";
import { batchRetryCandidates, type PodBatchRetryRequest } from "../../pod_customization/data/podBatchRetry";
import { buildSpecCardCells, createEmptySpecCard, groupPodStyleRows } from "../../pod_customization/data/podCustomizationModel";
import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import type { PodBatch, PodBatchSummary, PodComposition, SpecCardConfig } from "../../pod_customization/types";
import "../../pod_customization/styles/podCustomization.css";
import "../styles/podFission.css";
import { podFissionApi } from "../api/podFissionApi";
import { FissionSourceUploader } from "../components/FissionSourceUploader";
import { FissionProductUploader } from "../components/FissionProductUploader";
import { createEmptyPodFissionDraft, loadPodFissionDraft, savePodFissionDraft } from "../data/podFissionDraft";
import { normalizeFissionBatch, type FissionBatchView } from "../data/podFissionModel";
import type { FissionAnalysisResponse, FissionImageRole, FissionListingFieldsDraft, FissionSkuDraft } from "../types";

type Account = { account_id?: string; customer_id?: string; workspace_id?: string; workspace_code?: string };
type Asset = { id: string; name: string; preview: string };
const newSku = (): FissionSkuDraft => ({ name: "", declared_price: "", weight_g: "" });
const message = (error: unknown) => error instanceof Error ? error.message : "操作失败，请稍后重试。";
const COUNT_PRESETS = [1, 5, 10] as const;
const MAX_FISSION_COUNT = 10;

function draftStorage() { try { return window.localStorage; } catch { return null; } }

type Props = {
  /** 是否为工作台当前激活页签；隐藏页签不轮询，切回时 effect 重跑并立即补拉一次。 */
  isActive?: boolean;
};

export function PodFissionPage({ isActive = true }: Props) {
  const scope = useMemo(() => {
    const account = getAuthAccount<Account>();
    const accountId = (account?.account_id || account?.customer_id)?.trim() ?? "";
    const workspaceId = (account?.workspace_id || account?.workspace_code)?.trim() ?? "";
    return accountId && workspaceId ? { accountId, workspaceId } : null;
  }, []);
  const initial = useMemo(() => scope ? loadPodFissionDraft(scope.accountId, scope.workspaceId, draftStorage()) : { state: createEmptyPodFissionDraft(), error: "登录账号信息不可用，暂不读取或保存爆款裂变本地草稿。" }, [scope]);
  const [source, setSource] = useState<Asset | undefined>(() => initial.state.sourceAssetId ? { id: initial.state.sourceAssetId, name: initial.state.sourceFilename ?? "", preview: `/api/pod-customization/assets/${encodeURIComponent(initial.state.sourceAssetId)}` } : undefined);
  const [target, setTarget] = useState<Asset | undefined>(() => initial.state.targetAssetId ? { id: initial.state.targetAssetId, name: initial.state.targetFilename ?? "", preview: `/api/pod-customization/assets/${encodeURIComponent(initial.state.targetAssetId)}` } : undefined);
  const [analysis, setAnalysis] = useState<FissionAnalysisResponse | undefined>(() => initial.state.analysis);
  const [count, setCount] = useState(initial.state.count);
  const [listingFields, setListingFields] = useState<FissionListingFieldsDraft>(initial.state.listingFields);
  const [specCard, setSpecCard] = useState<SpecCardConfig>(initial.state.specCard ?? createEmptySpecCard());
  const [error, setError] = useState(initial.error ?? "");
  const [notice, setNotice] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [batches, setBatches] = useState<PodBatchSummary[]>([]);
  const [activeBatch, setActiveBatch] = useState<FissionBatchView | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectedItemId, setSelectedItemId] = useState<string>();
  const [failedRetryOpen, setFailedRetryOpen] = useState(false);
  const [specCardDrawerOpen, setSpecCardDrawerOpen] = useState(false);
  const [specCardBatch, setSpecCardBatch] = useState<PodBatch | null>(null);
  const [compositionOpen, setCompositionOpen] = useState(false);
  const [compositionManagerOpen, setCompositionManagerOpen] = useState(false);
  const [compositionEditTarget, setCompositionEditTarget] = useState<PodComposition | null>(null);
  const [composition, setComposition] = useState<PodComposition | null>(null);
  const requestIdRef = useRef(crypto.randomUUID());
  const requestSignatureRef = useRef("");
  const activeBatchIdRef = useRef<string>();
  const inputRevision = useRef(0);
  const actionLock = useRef(false);
  const mounted = useRef(true);
  const busy = Boolean(busyAction);
  const skuErrors = validateListingFields(listingFields);
  const failedCandidates = activeBatch ? batchRetryCandidates(groupPodStyleRows(activeBatch)) : { image: [], title: [] };
  const selectedItem = activeBatch?.items.find((item) => item.id === selectedItemId);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const refreshHistory = async () => { const result = await podFissionApi.listBatches(); if (mounted.current) setBatches(result.batches); return result.batches; };
  const refreshActiveBatch = async (id: string) => {
    const detail = normalizeFissionBatch(await podFissionApi.getBatch(id));
    if (mounted.current && activeBatchIdRef.current === id) setActiveBatch(detail);
    return detail;
  };
  const openBatch = async (id: string) => { activeBatchIdRef.current = id; setSelectedItemId(undefined); await refreshActiveBatch(id); };
  const runAction = async (name: string, action: () => Promise<void>) => {
    if (actionLock.current) return;
    actionLock.current = true; setBusyAction(name); setError(""); setNotice("");
    try { await action(); } catch (cause) { if (mounted.current) setError(message(cause)); }
    finally { actionLock.current = false; if (mounted.current) setBusyAction(""); }
  };

  useEffect(() => {
    if (!isActive) return;
    let stopped = false;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      // 整页被切到后台时不再发请求，回前台由定时器自然恢复。
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
      refreshing = true;
      try {
        const result = await podFissionApi.listBatches();
        if (stopped) return;
        setBatches(result.batches);
        const id = activeBatchIdRef.current ?? result.batches[0]?.id;
        if (id) {
          activeBatchIdRef.current = id;
          const detail = await podFissionApi.getBatch(id);
          if (!stopped && activeBatchIdRef.current === id) setActiveBatch(normalizeFissionBatch(detail));
        }
      } catch { /* Active actions surface errors; background history polling can recover. */ }
      finally { refreshing = false; }
    };
    void refresh();
    void podCustomizationApi.getLatestComposition("fission").then((value) => { if (!stopped) setComposition(value); }).catch(() => {});
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [isActive]);

  useEffect(() => {
    let stopped = false;
    const restoredSource = initial.state.sourceAssetId;
    const restoredTarget = initial.state.targetAssetId;
    void Promise.all([restoredSource ? podFissionApi.assetExists(restoredSource) : true, restoredTarget ? podFissionApi.assetExists(restoredTarget) : true]).then(([sourceOk, targetOk]) => {
      if (stopped) return;
      if (!sourceOk) setSource((current) => current?.id === restoredSource ? undefined : current);
      if (!targetOk) setTarget((current) => current?.id === restoredTarget ? undefined : current);
      if (!sourceOk || !targetOk) {
        // 草稿里的图片已失效时，与之绑定的识别结果一并作废，避免摘要与素材对不上。
        setAnalysis(undefined);
        setError("草稿中的素材已失效，请重新上传后再继续。");
      }
    });
    return () => { stopped = true; };
  }, [initial]);

  // 尺寸详情表格与 SKU 预设联动：SKU 增减或改名时按顺序重建表头与 SKU 行（第 1 列写 SKU 名称）；
  // 已填的长/宽/高按 SKU 名（退化时按位置）保留，避免重建时清掉用户输入。
  const skuNamesSignature = JSON.stringify(listingFields.skus.map((sku) => sku.name));
  useEffect(() => {
    const names = JSON.parse(skuNamesSignature) as string[];
    setSpecCard((current) => ({ ...current, cells: buildSpecCardCells(names, current.cells) }));
  }, [skuNamesSignature]);

  useEffect(() => {
    if (!scope) return;
    const result = savePodFissionDraft(scope.accountId, scope.workspaceId, { ...createEmptyPodFissionDraft(), sourceAssetId: source?.id ?? "", targetAssetId: target?.id ?? "", sourceFilename: source?.name, targetFilename: target?.name, count, listingFields, specCard, analysis }, draftStorage());
    if (!result.ok) setError(result.error);
  }, [scope, source, target, count, listingFields, specCard, analysis]);

  const upload = async (file: File, role: FissionImageRole) => {
    if (actionLock.current) return;
    setAnalysis(undefined);
    const revision = ++inputRevision.current;
    await runAction(`upload:${role}`, async () => {
      const result = await podFissionApi.uploadImage(file, role);
      if (revision !== inputRevision.current || !mounted.current) return;
      const asset = { id: result.asset_id, name: file.name, preview: result.preview_url };
      if (role === "source_artwork") setSource(asset); else setTarget(asset);
    });
  };
  const analyze = async () => {
    if (!source || !target || actionLock.current) return;
    setAnalysis(undefined);
    const revision = ++inputRevision.current;
    await runAction("analyze", async () => {
      const result = await podFissionApi.analyzeAssets({ source_asset_id: source.id, target_asset_id: target.id });
      if (revision !== inputRevision.current || !mounted.current) return;
      setAnalysis(result);
      if (result.status === "failed") setError(result.error || "素材识别失败，请重传或取消。");
    });
  };
  const updateSku = (index: number, key: keyof FissionSkuDraft, value: string) => setListingFields((current) => ({ ...current, skus: current.skus.map((sku, i) => i === index ? { ...sku, [key]: value } : sku) }));
  const addSku = () => setListingFields((current) => ({ ...current, skus: [...current.skus, newSku()] }));
  const create = async () => {
    if (!analysis || analysis.status !== "succeeded") return;
    setSubmitted(true);
    if (!Number.isInteger(count) || count < 1 || count > MAX_FISSION_COUNT) { setError(`生成数量必须是 1–${MAX_FISSION_COUNT} 的整数。`); return; }
    if (Object.keys(skuErrors).length || !listingFields.skus.length) { setError("请补全有效的店小秘类目和 SKU 上架信息。"); return; }
    const listing_fields = { title_mode: listingFields.title_mode, category_name: listingFields.category_name.trim(), skus: listingFields.skus.map((sku) => ({ name: sku.name.trim(), declared_price: Number(sku.declared_price), weight_g: Number(sku.weight_g) })), spec_card: { ...specCard, cells: buildSpecCardCells(listingFields.skus.map((sku) => sku.name), specCard.cells) } };
    const payload = { analysis_id: analysis.analysis_id, requested_count: count, listing_fields };
    const signature = JSON.stringify(payload);
    if (requestSignatureRef.current !== signature) { requestSignatureRef.current = signature; requestIdRef.current = crypto.randomUUID(); }
    await runAction("create", async () => {
      const created = await podFissionApi.createBatch({ ...payload, client_request_id: requestIdRef.current });
      activeBatchIdRef.current = created.batch_id || created.id;
      setActiveBatch(normalizeFissionBatch(created));
      await refreshHistory();
      setNotice("批次已创建，成功款可随时查看和导出。");
    });
  };
  const retry = async (styleIndex: number) => {
    if (!activeBatch) return;
    const id = activeBatch.id;
    await runAction(`regenerate-style:${styleIndex}`, async () => {
      await podFissionApi.retryStyle(id, styleIndex);
      await refreshActiveBatch(id);
    });
  };
  const mutateBatch = async (name: string, operation: (id: string) => Promise<unknown>) => {
    if (!activeBatch) return;
    const id = activeBatch.id;
    await runAction(name, async () => { await operation(id); await refreshActiveBatch(id); });
  };
  const retryFailed = async (request: PodBatchRetryRequest) => {
    if (!activeBatch) return;
    const id = activeBatch.id;
    await runAction("retry-failed", async () => {
      await podCustomizationApi.retryFailed(id, request);
      await refreshActiveBatch(id);
      setFailedRetryOpen(false);
    });
  };
  const deleteSelected = async () => {
    await runAction("delete-batches", async () => {
      for (const id of selectedIds) await podCustomizationApi.deleteBatch(id);
      if (selectedIds.includes(activeBatchIdRef.current ?? "")) { activeBatchIdRef.current = undefined; setActiveBatch(null); }
      setSelectedIds([]); await refreshHistory();
    });
  };

  return <section className="pod-customization-page pod-fission-page" aria-label="POD 爆款裂变">
    <header className="pod-page-header pod-fission-hero">
      <div className="pod-page-title">
        <span className="pod-page-title-icon iconfont icon-branches" aria-hidden="true" />
        <div><span>POD FISSION</span><h1>爆款裂变</h1><p>从一款爆款出发，批量创作同系列商品。</p></div>
      </div>
      <div className="pod-fission-flow" aria-hidden="true">
        <span><i className="iconfont icon-image" />爆款样图</span>
        <i className="iconfont icon-arrowright" />
        <span><i className="iconfont icon-appstore" />白底产品图</span>
        <i className="iconfont icon-arrowright" />
        <span><i className="iconfont icon-branches" />N 款同系列</span>
      </div>
      <div className="pod-page-header-actions">
        <button type="button" onClick={() => setCompositionManagerOpen(true)}><span className="iconfont icon-skin" aria-hidden="true" />管理模板</button>
        <button type="button" onClick={() => setHistoryOpen(true)}><span className="iconfont icon-time-circle" aria-hidden="true" />裂变历史</button>
      </div>
    </header>
    {(error || notice) && <div className={`pod-fission-message${error ? " is-error" : ""}`} role={error ? "alert" : "status"}><span className={`iconfont ${error ? "icon-info-circle" : "icon-check-circle"}`} aria-hidden="true" /><p>{error || notice}</p><button type="button" aria-label="关闭提示" onClick={() => { setError(""); setNotice(""); }}>×</button></div>}
    <div className="pod-fission-layout">
      <aside className="pod-fission-setup pod-prefill-panel" aria-label="裂变设置">
        <section className="pod-setup-card pod-fission-inputs" aria-label="裂变素材">
          <header className="pod-fission-card-head"><span className="pod-fission-card-badge" aria-hidden="true">01</span><div className="pod-section-title"><span>SOURCE IMAGES</span><h2>上传裂变素材</h2></div></header>
          <div className="pod-fission-uploads">
            <FissionSourceUploader disabled={busy} uploading={busyAction === "upload:source_artwork"} filename={source?.name} preview={source?.preview} onFile={(file) => void upload(file, "source_artwork")} onClear={() => { ++inputRevision.current; setSource(undefined); setAnalysis(undefined); }} />
            <FissionProductUploader disabled={busy} uploading={busyAction === "upload:target_product"} filename={target?.name} preview={target?.preview} onFile={(file) => void upload(file, "target_product")} onClear={() => { ++inputRevision.current; setTarget(undefined); setAnalysis(undefined); }} />
          </div>
          <button type="button" className="pod-start-button" disabled={busy || !source || !target} onClick={() => void analyze()}><span className={`iconfont ${busyAction === "analyze" ? "icon-loading" : "icon-robot"}`} aria-hidden="true" />{busyAction === "analyze" ? "正在分析素材…" : analysis?.status === "succeeded" ? "重新分析素材" : "分析素材"}</button>
          <p className="pod-fission-hint">上传两张图片，分析后确认识别摘要与生成数量。</p>
        </section>
        <section className="pod-setup-card pod-fission-confirm" aria-label="分析摘要">
          <header className="pod-fission-card-head"><span className="pod-fission-card-badge" aria-hidden="true">02</span><div className="pod-section-title"><span>REVIEW &amp; QUANTITY</span><h2>确认识别结果</h2></div></header>
          {analysis?.status === "succeeded" ? <>
            <dl className="pod-fission-summaries"><div><dt>样图图案</dt><dd>{analysis.artwork_summary}</dd></div><div><dt>目标产品</dt><dd>{analysis.product_summary}</dd></div></dl>
            <div className="pod-count-options" role="radiogroup" aria-label="生成数量档位">
              {COUNT_PRESETS.map((preset) => <button key={preset} type="button" role="radio" aria-checked={count === preset} className={count === preset ? "is-active" : ""} onClick={() => setCount(preset)}><b>{preset}</b><span>款</span></button>)}
            </div>
            <label className="pod-custom-count"><span>生成数量</span><input aria-label="生成数量" type="number" min={1} max={MAX_FISSION_COUNT} step={1} value={Number.isNaN(count) ? "" : count} onChange={(event) => setCount(event.target.value === "" ? NaN : Number(event.target.value))} /><small>款 / 最多 {MAX_FISSION_COUNT}</small></label>
            <div className="pod-fission-review-footer"><p className="pod-fission-hint">识别不合适时，请返回替换图片。</p><button type="button" className="pod-fission-secondary-button" disabled={busy || !source || !target} onClick={() => void analyze()}>重新分析</button></div>
          </> : <p className="pod-fission-awaiting">{busyAction === "analyze" ? "正在识别图案与产品信息，请稍候…" : "素材分析完成后，在这里确认图案、产品与款式数量。"}</p>}
        </section>
        <section className="pod-setup-card pod-fission-output" aria-label="商品化设置">
          <header className="pod-fission-card-head"><span className="pod-fission-card-badge" aria-hidden="true">03</span><div className="pod-section-title"><span>LISTING SETTINGS</span><h2>商品化设置</h2></div></header>
          <section className="pod-composition-entry" aria-label="构图模板">
            <div className="pod-fission-composition-info"><small>当前构图模板</small><b>{composition?.name || "系统默认构图模板"}</b></div>
            <button type="button" className="pod-composition-entry-button" onClick={() => setCompositionOpen(true)}><span className="iconfont icon-robot" aria-hidden="true" />生成新构图</button>
          </section>
          <PodListingFieldsEditor listingFields={listingFields} specCard={specCard} skuFieldErrors={skuErrors} skuLimitReached={listingFields.skus.length >= 100} showRequiredErrors={submitted} onListingFieldChange={(key, value) => setListingFields((current) => ({ ...current, [key]: value }))} onAddSku={addSku} onUpdateSku={updateSku} onRemoveSku={(index) => setListingFields((current) => ({ ...current, skus: current.skus.length > 1 ? current.skus.filter((_, i) => i !== index) : current.skus }))} onOpenSpecCardDrawer={() => { setSpecCardBatch(null); setSpecCardDrawerOpen(true); }} />
          <button type="button" className="pod-start-button" disabled={busy || analysis?.status !== "succeeded"} onClick={() => void create()}><span className={`iconfont ${busyAction === "create" ? "icon-loading" : "icon-skin"}`} aria-hidden="true" />{busyAction === "create" ? "正在创建…" : `确认并生成 ${Number.isInteger(count) && count >= 1 && count <= MAX_FISSION_COUNT ? count : ""} 款`}</button>
          <p className="pod-fission-hint">整批共用上架信息，按成功款结算。</p>
        </section>
      </aside>
      <main className="pod-fission-results">
        {activeBatch && <section className="pod-current-template-summary">
          <header><div><span>CURRENT BATCH</span><h2>当前裂变批次</h2></div><button type="button" onClick={() => { setSpecCardBatch(activeBatch); setSpecCardDrawerOpen(true); }}>查看 / 重印规格卡</button></header>
          <div className="pod-fission-batch-inputs"><figure><PodAssetImage path={activeBatch.source?.preview_url} alt="本批次爆款样图" /><figcaption>A · 爆款样图</figcaption></figure><figure><PodAssetImage path={activeBatch.target?.preview_url} alt="本批次白底产品图" /><figcaption>B · 白底产品图</figcaption></figure><div><b>{activeBatch.composition?.name || "系统默认构图模板"}</b><p>整批共用上架信息</p></div></div>
        </section>}
        <PodBatchGallery emptyTitle="上传样图和白底图，开始本批裂变" emptyDescription="确认识别摘要与数量后，商品款会显示在这里。" batch={activeBatch} busyAction={busyAction} onOpenResult={(item) => setSelectedItemId(item.id)} onRegenerateStyle={(index) => void retry(index)} onRegenerateTitle={(index) => void mutateBatch(`regenerate-title:${index}`, (id) => podCustomizationApi.regenerateStyleTitle(id, index))} onUpdateExportSelection={(index, selected) => void mutateBatch("export-selection", (id) => podCustomizationApi.updateExportSelection(id, index, selected))} onSetAllExportSelection={(selected) => void mutateBatch("export-selection", async (id) => { for (const style of groupPodStyleRows(activeBatch!)) if (style.status === "completed" && style.listing_ready) await podCustomizationApi.updateExportSelection(id, style.index, selected); })} onSaveTitle={async (index, title) => { if (!activeBatch) return; await podCustomizationApi.updateManualTitle(activeBatch.id, index, title); await refreshActiveBatch(activeBatch.id); }} onExportDianxiaomi={() => void mutateBatch("export-dianxiaomi", (id) => podCustomizationApi.exportDianxiaomi(id))} onExportMiaoshou={(kind) => void mutateBatch(`export-miaoshou:${kind}`, (id) => podCustomizationApi.exportMiaoshou(id, kind))} onOpenFailedRetry={() => setFailedRetryOpen(true)} onPauseBatch={() => void mutateBatch("pause-batch", podCustomizationApi.pauseBatch)} onCancelBatch={() => void mutateBatch("cancel-batch", podCustomizationApi.cancelBatch)} onResumeBatch={() => void mutateBatch("resume-batch", podCustomizationApi.resumeBatch)} />
      </main>
    </div>
    <PodResultLightbox batch={activeBatch} item={selectedItem} busyAction={busyAction} onClose={() => setSelectedItemId(undefined)} onDownload={podCustomizationApi.downloadAsset} />
    <PodBatchHistoryDrawer open={historyOpen} batches={batches} activeBatchId={activeBatch?.id} loading={false} busyAction={busyAction} selectedIds={selectedIds} onToggleSelect={(id) => setSelectedIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} onDeleteSelected={() => void deleteSelected()} onOpen={(id) => { setHistoryOpen(false); void runAction(`batch:${id}`, () => openBatch(id)); }} onRefresh={() => void runAction("history", async () => { await refreshHistory(); })} onClose={() => setHistoryOpen(false)} />
    <PodFailedRetryDialog open={failedRetryOpen} imageCandidates={failedCandidates.image} titleCandidates={failedCandidates.title} busy={busy} onClose={() => setFailedRetryOpen(false)} onSubmit={(request) => void retryFailed(request)} />
    <SpecCardDrawer open={specCardDrawerOpen} config={specCardBatch?.listing_fields?.spec_card ?? specCard} batch={specCardBatch} onClose={() => setSpecCardDrawerOpen(false)} onSave={setSpecCard} onReprinted={(id) => { void refreshActiveBatch(id); setNotice("规格卡已更新，请重新导出。"); }} />
    <CompositionGenerateDrawer open={compositionOpen} scope="fission" onClose={() => setCompositionOpen(false)} onChanged={setComposition} />
    <CompositionManagerDrawer open={compositionManagerOpen} scope="fission" onClose={() => setCompositionManagerOpen(false)} onActiveChanged={setComposition} onEdit={(value) => { setCompositionManagerOpen(false); setCompositionEditTarget(value); }} />
    <CompositionEditDrawer open={Boolean(compositionEditTarget)} target={compositionEditTarget} onClose={() => setCompositionEditTarget(null)} onChanged={(value) => { if (value.is_active) setComposition(value); }} />
  </section>;
}

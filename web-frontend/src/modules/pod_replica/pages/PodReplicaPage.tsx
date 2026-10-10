import { useEffect, useMemo, useRef, useState } from "react";

import { getAuthAccount } from "../../../transport/http/client";
import { podCustomizationApi } from "../../pod_customization/api/podCustomizationApi";
import { PodBatchGallery } from "../../pod_customization/components/PodBatchGallery";
import { PodBatchHistoryDrawer } from "../../pod_customization/components/PodBatchHistoryDrawer";
import { PodFailedRetryDialog } from "../../pod_customization/components/PodFailedRetryDialog";
import { PodResultLightbox } from "../../pod_customization/components/PodResultLightbox";
import { SpecCardDrawer, type SpecCardBatchContext } from "../../pod_customization/components/SpecCardDrawer";
import { validateListingFields, type SkuField } from "../../pod_customization/components/PodListingFieldsEditor";
import { buildSpecCardCells, groupPodStyleRows, isActiveBatchStatus, isActivePodItemStatus, isActivePodStyleTitleStatus, shouldPollPodBatch } from "../../pod_customization/data/podCustomizationModel";
import { batchRetryCandidates, type PodBatchRetryRequest } from "../../pod_customization/data/podBatchRetry";
import type { PodBatchItem, PodMiaoshouTemplateKind } from "../../pod_customization/types";

import { podReplicaApi } from "../api/podReplicaApi";
import { ReplicaSourceUploader, type ReplicaSourceView } from "../components/ReplicaSourceUploader";
import { ReplicaTargetDrawer } from "../components/ReplicaTargetDrawer";
import { ReplicaTargetList } from "../components/ReplicaTargetList";
import {
  attachReplicaTargetAsset,
  buildReplicaBatchRequest,
  createEmptyReplicaTarget,
  firstInvalidReplicaTargetIndex,
  nextReplicaClientRequestId,
  removeReplicaTarget,
  validateReplicaTargetDraft,
  type ReplicaTargetDraft,
} from "../data/podReplicaModel";
import {
  POD_REPLICA_DRAFT_VERSION,
  loadPodReplicaDraft,
  savePodReplicaDraft,
} from "../data/podReplicaDraft";
import type { ReplicaBatch, ReplicaBatchSummary } from "../types";
import "../styles/podReplica.css";
// 复用全定制的次级顶栏与图库等共享皮肤（.pod-page-header 等），直接进本页也要有样式。
import "../../pod_customization/styles/podCustomization.css";

type Props = { isActive?: boolean };
type ReplicaDraftAccount = { account_id?: string; customer_id?: string; workspace_id?: string; workspace_code?: string };

const MAX_SKU = 100;

export function PodReplicaPage({ isActive = true }: Props) {
  const draftScope = useMemo(() => {
    const account = getAuthAccount<ReplicaDraftAccount>();
    const accountId = (account?.account_id || account?.customer_id)?.trim() ?? "";
    const workspaceId = (account?.workspace_id || account?.workspace_code)?.trim() ?? "";
    return accountId && workspaceId ? { accountId, workspaceId } : null;
  }, []);
  const [initialDraft] = useState(() => draftScope
    ? loadPodReplicaDraft(draftScope.accountId, draftScope.workspaceId)
    : { state: { version: POD_REPLICA_DRAFT_VERSION as 1, source: null, targets: [] as ReplicaTargetDraft[] }, error: "登录账号信息不可用，暂不读取或保存爆款复刻本地草稿。" });

  const [source, setSource] = useState<ReplicaSourceView>(() => {
    const stored = initialDraft.state.source;
    return stored?.assetId ? { assetId: stored.assetId, filename: stored.filename } : null;
  });
  const [targets, setTargets] = useState<ReplicaTargetDraft[]>(initialDraft.state.targets);
  const [activeTargetId, setActiveTargetId] = useState<string>();
  // 上架信息实时校验：抽屉里按当前产品实时重算并内联展示；「必填为空」在提交过一次后才提示。
  const [listingErrorsRevealed, setListingErrorsRevealed] = useState(false);
  const [specCardTargetId, setSpecCardTargetId] = useState<string>();
  const [activeBatch, setActiveBatch] = useState<ReplicaBatch | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string>();
  const [history, setHistory] = useState<ReplicaBatchSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [failedRetryOpen, setFailedRetryOpen] = useState(false);
  // 复刻历史抽屉的多选：勾选后可批量删除（只有终态批次可勾，见 canDeletePodBatch）。
  const [selectedBatchIds, setSelectedBatchIds] = useState<string[]>([]);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState(initialDraft.error ?? "");
  const [visibility, setVisibility] = useState<DocumentVisibilityState>(() => document.visibilityState);
  const requestIdRef = useRef<{ signature: string; id: string } | null>(null);
  const lastDraftErrorRef = useRef("");

  // 同一份提交内容（含网络重试）复用同一 client_request_id；草稿改动后签名变化即换新 ID。
  const stableRequestId = (signature: string): string => {
    if (!requestIdRef.current || requestIdRef.current.signature !== signature) {
      requestIdRef.current = { signature, id: nextReplicaClientRequestId() };
    }
    return requestIdRef.current.id;
  };

  const activeTarget = targets.find((target) => target.clientId === activeTargetId) ?? null;
  const specCardTarget = targets.find((target) => target.clientId === specCardTargetId) ?? null;
  const selectedItem = activeBatch?.items.find((item) => item.id === selectedItemId);
  const failedRetryCandidates = activeBatch ? batchRetryCandidates(groupPodStyleRows(activeBatch)) : { image: [], title: [] };

  const activeItemStatuses = activeBatch?.items.map((item) => item.status).join("|") ?? "";
  const activeTitleStatuses = activeBatch?.style_titles?.map((title) => title.status).join("|") ?? "";
  const batchRunning = activeBatch
    ? isActiveBatchStatus(activeBatch.status)
      || activeBatch.items.some((item) => isActivePodItemStatus(item.status))
      || activeBatch.style_titles?.some((title) => isActivePodStyleTitleStatus(title.status))
    : false;

  const clearMessages = () => { setError(""); };

  /** 一键清空：样图、全部白底图与已填写的上架信息一并重置（草稿随之下一次保存为空）。 */
  const clearAll = () => {
    setSource(null);
    setTargets([]);
    setListingErrorsRevealed(false);
    setActiveTargetId(undefined);
    setSpecCardTargetId(undefined);
    requestIdRef.current = null;
    setError("");
  };

  useEffect(() => {
    const updateVisibility = () => setVisibility(document.visibilityState);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  // 恢复草稿后校验资产存在性：缺失的样图/目标要求重新上传，不静默使用失效 ID。
  useEffect(() => {
    let stopped = false;
    const verify = async () => {
      const missingTargets: string[] = [];
      for (const target of targets) {
        if (target.assetId && !(await podReplicaApi.assetExists(target.assetId))) missingTargets.push(target.clientId);
      }
      if (stopped) return;
      if (missingTargets.length) {
        setTargets((current) => current.filter((target) => !missingTargets.includes(target.clientId)));
        setError("部分目标产品图已失效，请重新上传后再继续。");
      }
      if (source?.assetId && !(await podReplicaApi.assetExists(source.assetId))) {
        if (!stopped) setSource(null);
      }
    };
    void verify();
    return () => { stopped = true; };
    // 仅在首次挂载时校验恢复出来的草稿。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let stopped = false;
    void podReplicaApi.listBatches().then((response) => {
      if (stopped) return;
      setHistory(response.batches);
      const latest = response.batches[0];
      if (latest) void podReplicaApi.getBatch(latest.id).then((batch) => { if (!stopped) setActiveBatch(batch); }).catch(() => undefined);
    }).catch(() => undefined);
    return () => { stopped = true; };
  }, []);

  useEffect(() => {
    if (!draftScope) return;
    const result = savePodReplicaDraft(draftScope.accountId, draftScope.workspaceId, {
      version: POD_REPLICA_DRAFT_VERSION,
      source: source ? { assetId: source.assetId, filename: source.filename } : null,
      targets,
    });
    if (!result.ok && lastDraftErrorRef.current !== result.error) {
      lastDraftErrorRef.current = result.error;
      setError(result.error);
    } else if (result.ok) {
      lastDraftErrorRef.current = "";
    }
  }, [draftScope?.accountId, draftScope?.workspaceId, source, targets]);

  // 结果轮询：仅在被激活且页面可见、批次运行中时进行。
  useEffect(() => {
    if (!activeBatch || !shouldPollPodBatch(
      isActive,
      visibility,
      activeBatch.status,
      activeBatch.items.map((item) => item.status),
      activeBatch.style_titles?.map((title) => title.status),
    )) return;
    let stopped = false;
    const poll = async () => {
      try {
        const fresh = await podReplicaApi.getBatch(activeBatch.id);
        if (!stopped) setActiveBatch((current) => (current && JSON.stringify(current) === JSON.stringify(fresh) ? current : fresh));
      } catch { /* 轮询失败不打断页面，下一次继续。 */ }
    };
    const timer = window.setInterval(() => void poll(), 1_500);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [activeBatch?.id, activeBatch?.status, activeItemStatuses, activeTitleStatuses, isActive, visibility]);

  const refreshBatch = async (batchId: string) => {
    try {
      const fresh = await podReplicaApi.getBatch(batchId);
      setActiveBatch(fresh);
      setHistory((current) => [fresh as unknown as ReplicaBatchSummary, ...current.filter((batch) => batch.id !== fresh.id)]);
    } catch { /* 已展示的内容保持可见。 */ }
  };

  const uploadSource = async (files: File[]) => {
    if (files.length > 1) {
      setError("POD 样图一次只能上传一张，请只粘贴或选择一张图片。");
      return;
    }
    const file = files[0];
    if (!file) return;
    clearMessages();
    setBusyAction("upload-source");
    try {
      const uploaded = await podReplicaApi.uploadImage(file, "source");
      setSource({ assetId: uploaded.asset_id, filename: file.name });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const uploadTargets = async (files: File[]) => {
    if (!files.length) return;
    clearMessages();
    setBusyAction("upload-target");
    const created: ReplicaTargetDraft[] = [];
    try {
      for (const file of files) {
        const uploaded = await podReplicaApi.uploadImage(file, "target");
        created.push(attachReplicaTargetAsset(createEmptyReplicaTarget(), {
          asset_id: uploaded.asset_id,
          filename: file.name,
          width: uploaded.width,
          height: uploaded.height,
        }));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (created.length) {
        setTargets((current) => [...current, ...created]);
      }
      setBusyAction("");
    }
  };

  const updateTarget = (clientId: string, updater: (target: ReplicaTargetDraft) => ReplicaTargetDraft) => {
    setTargets((current) => current.map((target) => target.clientId === clientId ? updater(target) : target));
  };

  const toggleBatchSelected = (batchId: string) => {
    setSelectedBatchIds((current) => current.includes(batchId)
      ? current.filter((id) => id !== batchId)
      : [...current, batchId]);
  };

  // SKU 增减/改名后按顺序重建尺寸表结构，保留已填长/宽/高。
  const withSyncedSpecCard = (target: ReplicaTargetDraft, listingFields: ReplicaTargetDraft["listingFields"]): ReplicaTargetDraft => ({
    ...target,
    listingFields,
    specCard: { ...target.specCard, cells: buildSpecCardCells(listingFields.skus.map((sku) => sku.name), target.specCard.cells) },
  });

  const changeProductName = (clientId: string, value: string) => updateTarget(clientId, (target) => ({ ...target, productName: value }));

  const changeListingField = (clientId: string, key: "title_mode" | "suggested_price_usd" | "category_name", value: string) =>
    updateTarget(clientId, (target) => ({ ...target, listingFields: { ...target.listingFields, [key]: value } as ReplicaTargetDraft["listingFields"] }));

  const addSku = (clientId: string) => updateTarget(clientId, (target) => {
    if (target.listingFields.skus.length >= MAX_SKU) return target;
    return withSyncedSpecCard(target, { ...target.listingFields, skus: [...target.listingFields.skus, { name: "", declared_price: "", weight_g: "" }] });
  });

  const updateSku = (clientId: string, index: number, key: SkuField, value: string) => {
    updateTarget(clientId, (target) => withSyncedSpecCard(target, {
      ...target.listingFields,
      skus: target.listingFields.skus.map((sku, currentIndex) => currentIndex === index ? { ...sku, [key]: value } : sku),
    }));
  };

  const removeSku = (clientId: string, index: number) => updateTarget(clientId, (target) =>
    withSyncedSpecCard(target, { ...target.listingFields, skus: target.listingFields.skus.filter((_, currentIndex) => currentIndex !== index) }));

  const startBatch = async () => {
    clearMessages();
    if (!source) { setError("请先上传 POD 样图。"); return; }
    if (!targets.length) { setError("请至少添加一个目标产品。"); return; }

    // 上架信息是实时校验的：这里揭示「必填为空」，并拦截比后端合约更严的字符/格式问题。
    setListingErrorsRevealed(true);
    const invalidListingTarget = targets.find((target) => Object.keys(validateListingFields(target.listingFields)).length > 0);
    if (invalidListingTarget) {
      const index = targets.indexOf(invalidListingTarget);
      setActiveTargetId(invalidListingTarget.clientId);
      setError(`第 ${index + 1} 个产品有误：请检查上架信息中标红的字段。`);
      return;
    }

    const firstInvalid = firstInvalidReplicaTargetIndex(targets);
    if (firstInvalid >= 0) {
      const target = targets[firstInvalid];
      const reason = validateReplicaTargetDraft(target);
      setActiveTargetId(target.clientId);
      setError(`第 ${firstInvalid + 1} 个产品有误：${reason.ok ? "请检查填写内容。" : reason.error}`);
      return;
    }

    let request;
    try {
      const title = `${source.filename || "POD 样图"} 复刻`;
      const signature = JSON.stringify({ source: source.assetId, title, targets: targets.map((target) => ({ asset: target.assetId, name: target.productName, listing: target.listingFields, spec: target.specCard })) });
      request = buildReplicaBatchRequest({
        client_request_id: stableRequestId(signature),
        source_asset_id: source.assetId,
        title,
        targets,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return;
    }

    setBusyAction("create-batch");
    try {
      const created = await podReplicaApi.createBatch(request);
      setActiveBatch(created);
      setSelectedItemId(undefined);
      setHistory((current) => [created as unknown as ReplicaBatchSummary, ...current.filter((batch) => batch.id !== created.id)]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const openBatch = async (batchId: string) => {
    setBusyAction(`batch:${batchId}`);
    clearMessages();
    try {
      setActiveBatch(await podReplicaApi.getBatch(batchId));
      setSelectedItemId(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
      setHistoryOpen(false);
    }
  };

  const deleteBatches = async (ids: string[]) => {
    if (!ids.length) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 个复刻批次？`)) return;
    setBusyAction("delete-batch");
    try {
      for (const id of ids) await podCustomizationApi.deleteBatch(id);
      setHistory((current) => current.filter((batch) => !ids.includes(batch.id)));
      if (activeBatch && ids.includes(activeBatch.id)) setActiveBatch(null);
      setSelectedBatchIds((current) => current.filter((id) => !ids.includes(id)));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const regenerateStyle = async (styleIndex: number) => {
    if (!activeBatch) return;
    setBusyAction(`regenerate-style:${styleIndex}`);
    clearMessages();
    try {
      const updated = await podCustomizationApi.regenerateStyle(activeBatch.id, styleIndex);
      setActiveBatch((current) => current ? { ...current, items: current.items.map((item) => updated.results.find((result) => result.id === item.id) ?? item) } : current);
      await refreshBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const regenerateStyleTitle = async (styleIndex: number) => {
    if (!activeBatch) return;
    setBusyAction(`regenerate-title:${styleIndex}`);
    clearMessages();
    try {
      await podCustomizationApi.regenerateStyleTitle(activeBatch.id, styleIndex);
      await refreshBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const updateExportSelection = async (styleIndex: number, selected: boolean) => {
    if (!activeBatch) return;
    clearMessages();
    setActiveBatch((current) => current ? { ...current, style_titles: current.style_titles?.map((title) => title.style_index === styleIndex ? { ...title, export_selected: selected } : title) } : current);
    try {
      await podCustomizationApi.updateExportSelection(activeBatch.id, styleIndex, selected);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      await refreshBatch(activeBatch.id);
    }
  };

  /** 一键反选：把全部已就绪款式整体设为选中/取消选中，不用逐个点。 */
  const setAllExportSelection = async (selected: boolean) => {
    if (!activeBatch) return;
    const readyTitles = (activeBatch.style_titles ?? []).filter((title) => title.listing_ready);
    const changed = readyTitles.filter((title) => title.export_selected !== selected);
    if (!changed.length) return;
    clearMessages();
    setActiveBatch((current) => current ? {
      ...current,
      dianxiaomi_export: current.dianxiaomi_export.selected_exportable_style_count === undefined ? current.dianxiaomi_export : {
        ...current.dianxiaomi_export,
        selected_exportable_style_count: selected ? readyTitles.length : 0,
        user_excluded_style_count: current.dianxiaomi_export.user_excluded_style_count === undefined ? undefined : selected ? 0 : readyTitles.length,
      },
      style_titles: current.style_titles?.map((title) => title.listing_ready ? { ...title, export_selected: selected } : title),
    } : current);
    try {
      await Promise.all(changed.map((title) => podCustomizationApi.updateExportSelection(activeBatch.id, title.style_index, selected)));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      await refreshBatch(activeBatch.id);
    }
  };

  const saveManualTitle = async (styleIndex: number, title: string) => {
    if (!activeBatch) return;
    setBusyAction(`save-title:${styleIndex}`);
    clearMessages();
    try {
      const updated = await podCustomizationApi.updateManualTitle(activeBatch.id, styleIndex, title);
      setActiveBatch((current) => current ? { ...current, style_titles: [...(current.style_titles ?? []).filter((item) => item.style_index !== updated.style_index), updated] } : current);
      await refreshBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    } finally {
      setBusyAction("");
    }
  };

  const simpleBatchAction = async (label: string, run: (id: string) => Promise<unknown>) => {
    if (!activeBatch) return;
    setBusyAction(label);
    clearMessages();
    try {
      await run(activeBatch.id);
      await refreshBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const retryFailed = async (request: PodBatchRetryRequest) => {
    if (!activeBatch) return;
    setBusyAction("retry-failed");
    clearMessages();
    try {
      await podCustomizationApi.retryFailed(activeBatch.id, request);
      setFailedRetryOpen(false);
      await refreshBatch(activeBatch.id);
    } catch (cause) {
      setFailedRetryOpen(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const exportDianxiaomi = async () => {
    if (!activeBatch) return;
    setBusyAction("export-dianxiaomi");
    clearMessages();
    try {
      await podCustomizationApi.exportDianxiaomi(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const exportMiaoshou = async (kind: PodMiaoshouTemplateKind) => {
    if (!activeBatch) return;
    setBusyAction(`export-miaoshou:${kind}`);
    clearMessages();
    try {
      await podCustomizationApi.exportMiaoshou(activeBatch.id, kind);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const openSelectedItem = (item: PodBatchItem, _styleIndex: number) => setSelectedItemId(item.id);

  const resolveStyleProductName = (styleIndex: number) => targets[styleIndex - 1]?.productName.trim() || undefined;
  const specCardBatchContext: SpecCardBatchContext | null = activeBatch ? { id: activeBatch.id, status: activeBatch.status, count: activeBatch.count } : null;
  const specCardStyleIndex = specCardTarget ? targets.findIndex((target) => target.clientId === specCardTarget.clientId) + 1 : undefined;
  const canSubmit = Boolean(source) && targets.length > 0 && !busyAction;

  return (
    <section className="pod-replica-page" aria-label="POD 爆款复刻">
      {/* 与全定制/半定制同一套次级顶栏：图标 + 眉标 + 标题 + 右侧操作。 */}
      <header className="pod-page-header">
        <div className="pod-page-title">
          <span className="pod-page-title-icon iconfont icon-skin" aria-hidden="true" />
          <div>
            <span>POD BESTSELLER REPLICA</span>
            <h1>爆款复刻</h1>
          </div>
        </div>
        <div className="pod-page-header-actions">
          {batchRunning && <span className="pod-live-badge"><i />批次后台运行中</span>}
          <button type="button" onClick={() => setHistoryOpen(true)}><span className="iconfont icon-time-circle" />复刻历史</button>
        </div>
      </header>

      {error && <div className="pod-replica-message is-error" role="alert"><span>!</span><p>{error}</p><button type="button" onClick={clearMessages} aria-label="关闭提示">×</button></div>}

      <div className="pod-replica-layout">
        <div className="pod-replica-setup">
          <ReplicaSourceUploader source={source} busy={busyAction === "upload-source"} onFiles={(files) => void uploadSource(files)} onClear={() => setSource(null)} />
          <ReplicaTargetList
            targets={targets}
            busy={busyAction === "upload-target"}
            onFiles={(files) => void uploadTargets(files)}
            onRemove={(clientId) => setTargets((current) => removeReplicaTarget(current, clientId))}
            onOpen={setActiveTargetId}
          />
          <div className="pod-replica-submit">
            <div className="pod-replica-submit-actions">
              <button type="button" className="pod-replica-start" disabled={!canSubmit} onClick={() => void startBatch()}>
                {busyAction === "create-batch" ? "正在提交…" : `开始复刻 ${targets.length} 个产品`}
              </button>
              <button type="button" className="pod-replica-clear" disabled={!source && !targets.length} onClick={clearAll}>一键清空</button>
            </div>
          </div>
        </div>

        <main className="pod-replica-results">
          <PodBatchGallery
            batch={activeBatch}
            busyAction={busyAction}
            resolveStyleProductName={resolveStyleProductName}
            onOpenResult={openSelectedItem}
            onRegenerateStyle={(styleIndex) => void regenerateStyle(styleIndex)}
            onRegenerateTitle={(styleIndex) => void regenerateStyleTitle(styleIndex)}
            onUpdateExportSelection={(styleIndex, selected) => void updateExportSelection(styleIndex, selected)}
            onSetAllExportSelection={(selected) => void setAllExportSelection(selected)}
            onSaveTitle={(styleIndex, title) => saveManualTitle(styleIndex, title)}
            onExportDianxiaomi={() => void exportDianxiaomi()}
            onExportMiaoshou={(kind) => void exportMiaoshou(kind)}
            onOpenFailedRetry={() => setFailedRetryOpen(true)}
            onPauseBatch={() => void simpleBatchAction("pause-batch", podCustomizationApi.pauseBatch)}
            onCancelBatch={() => void simpleBatchAction("cancel-batch", podCustomizationApi.cancelBatch)}
            onResumeBatch={() => void simpleBatchAction("resume-batch", podCustomizationApi.resumeBatch)}
          />
        </main>
      </div>

      <ReplicaTargetDrawer
        open={Boolean(activeTarget)}
        target={activeTarget}
        skuFieldErrors={activeTarget ? validateListingFields(activeTarget.listingFields) : {}}
        skuLimitReached={Boolean(activeTarget && activeTarget.listingFields.skus.length >= MAX_SKU)}
        showRequiredErrors={listingErrorsRevealed}
        onChangeProductName={changeProductName}
        onListingFieldChange={changeListingField}
        onAddSku={addSku}
        onUpdateSku={updateSku}
        onRemoveSku={removeSku}
        onOpenSpecCardDrawer={setSpecCardTargetId}
        onClose={() => setActiveTargetId(undefined)}
      />

      <SpecCardDrawer
        open={Boolean(specCardTarget)}
        config={specCardTarget?.specCard ?? { enabled: true, style: "light", corner: "bottom-right", display_unit: "cm", cells: [] }}
        batch={specCardBatchContext}
        reprintStyleIndex={specCardBatchContext ? specCardStyleIndex : undefined}
        onClose={() => setSpecCardTargetId(undefined)}
        onSave={(next) => {
          if (!specCardTarget) return;
          updateTarget(specCardTarget.clientId, (target) => ({ ...target, specCard: next }));
        }}
        onReprinted={(batchId) => {
          void refreshBatch(batchId);
        }}
      />

      <PodResultLightbox batch={activeBatch} item={selectedItem} busyAction={busyAction} onClose={() => setSelectedItemId(undefined)} onDownload={async (path, filename) => { await podCustomizationApi.downloadAsset(path, filename); }} />

      <PodFailedRetryDialog open={failedRetryOpen} imageCandidates={failedRetryCandidates.image} titleCandidates={failedRetryCandidates.title} busy={busyAction === "retry-failed"} onClose={() => setFailedRetryOpen(false)} onSubmit={(request) => void retryFailed(request)} />

      <PodBatchHistoryDrawer
        open={historyOpen}
        batches={history}
        activeBatchId={activeBatch?.id}
        loading={busyAction.startsWith("batch:")}
        busyAction={busyAction}
        selectedIds={selectedBatchIds}
        onToggleSelect={toggleBatchSelected}
        onDeleteSelected={() => void deleteBatches(selectedBatchIds)}
        onOpen={(batchId) => void openBatch(batchId)}
        onRefresh={() => void podReplicaApi.listBatches().then((response) => setHistory(response.batches)).catch(() => undefined)}
        onClose={() => setHistoryOpen(false)}
      />
    </section>
  );
}

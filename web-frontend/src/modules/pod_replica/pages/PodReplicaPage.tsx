import { useEffect, useMemo, useRef, useState } from "react";

import { getAuthAccount } from "../../../transport/http/client";
import { podCustomizationApi } from "../../pod_customization/api/podCustomizationApi";
import { CompositionEditDrawer } from "../../pod_customization/components/CompositionEditDrawer";
import { CompositionGenerateDrawer } from "../../pod_customization/components/CompositionGenerateDrawer";
import { CompositionManagerDrawer } from "../../pod_customization/components/CompositionManagerDrawer";
import { PodBatchGallery } from "../../pod_customization/components/PodBatchGallery";
import { PodBatchHistoryDrawer } from "../../pod_customization/components/PodBatchHistoryDrawer";
import { PodFailedRetryDialog } from "../../pod_customization/components/PodFailedRetryDialog";
import { PodResultLightbox } from "../../pod_customization/components/PodResultLightbox";
import { SpecCardDrawer, type SpecCardBatchContext } from "../../pod_customization/components/SpecCardDrawer";
import { validateListingFields, type SkuField } from "../../pod_customization/components/PodListingFieldsEditor";
import { buildSpecCardCells, groupPodStyleRows, isActiveBatchStatus, isActivePodItemStatus, isActivePodStyleTitleStatus, shouldPollPodBatch } from "../../pod_customization/data/podCustomizationModel";
import { batchRetryCandidates, type PodBatchRetryRequest } from "../../pod_customization/data/podBatchRetry";
import { formatPodModulePrice, usePodModulePricing } from "../../pod_customization/data/usePodModulePricing";
import type { PodBatchItem, PodComposition, PodMiaoshouTemplateKind } from "../../pod_customization/types";

import { podReplicaApi } from "../api/podReplicaApi";
import { ReplicaSourceUploader, type ReplicaSourceView } from "../components/ReplicaSourceUploader";
import { ReplicaTargetDrawer } from "../components/ReplicaTargetDrawer";
import { ReplicaTargetList } from "../components/ReplicaTargetList";
import {
  applyReplicaAnalysis,
  attachReplicaTargetAsset,
  buildReplicaBatchRequest,
  createEmptyReplicaTarget,
  emptyReplicaAnalysisFields,
  firstInvalidReplicaTargetIndex,
  markReplicaAnalysisAnalyzing,
  markReplicaAnalysisFailed,
  nextReplicaClientRequestId,
  removeReplicaTarget,
  replicaAnalysesReady,
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
  const modulePrice = formatPodModulePrice(usePodModulePricing("pod_replica_v1"));
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
  /** 提交被拦下时定位到的那一款：列表里标红，但不自动打开侧边栏。 */
  const [invalidTargetId, setInvalidTargetId] = useState("");
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
  // 复合场景：同一目标可能因状态变化重复触发分析，用 ref 去重。
  const analyzingRef = useRef<Set<string>>(new Set());
  const targetsRef = useRef<ReplicaTargetDraft[]>(targets);
  targetsRef.current = targets;
  const [composition, setComposition] = useState<PodComposition | null>(null);
  const [compositionDrawerOpen, setCompositionDrawerOpen] = useState(false);
  const [compositionManagerOpen, setCompositionManagerOpen] = useState(false);
  const [compositionEditOpen, setCompositionEditOpen] = useState(false);
  const [compositionEditTarget, setCompositionEditTarget] = useState<PodComposition | null>(null);

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
    setInvalidTargetId("");
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

  // 当前生效的复刻构图模板（复刻独立模板池）：提交时把 composition_id 一起冻结进计划。
  useEffect(() => {
    let stopped = false;
    void podCustomizationApi.getLatestComposition("replica")
      .then((latest) => { if (!stopped) setComposition(latest); })
      .catch(() => undefined);
    return () => { stopped = true; };
  }, []);

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
      // 换样图即换 A：所有目标的主图方案（基于上一张 A 生成）全部失效。
      setSource({ assetId: uploaded.asset_id, filename: file.name });
      setTargets((current) => current.map((target) => ({ ...target, ...emptyReplicaAnalysisFields() })));
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
    // 用户开始补这一款时撤掉提交失败的标红，不必等他再次点提交。
    setInvalidTargetId((current) => (current === clientId ? "" : current));
  };

  /** 调一次豆包，让这组 A/B 产出该款的主图方案；前端只保存方案 id 与状态。 */
  const analyzeTarget = async (clientId: string) => {
    const sourceAssetId = source?.assetId;
    const target = targetsRef.current.find((item) => item.clientId === clientId);
    if (!sourceAssetId || !target?.assetId) { setError("请先上传 POD 样图与该产品的白底图。"); return; }
    if (analyzingRef.current.has(clientId)) return;
    analyzingRef.current.add(clientId);
    updateTarget(clientId, markReplicaAnalysisAnalyzing);
    try {
      const response = await podReplicaApi.analyzeAssets({
        source_asset_id: sourceAssetId,
        target_asset_id: target.assetId,
      });
      updateTarget(clientId, (current) => applyReplicaAnalysis(current, response));
    } catch (cause) {
      updateTarget(clientId, (current) => markReplicaAnalysisFailed(current, cause instanceof Error ? cause.message : String(cause)));
    } finally {
      analyzingRef.current.delete(clientId);
    }
  };

  // A/B 资产齐备后自动生成主图方案：仅触发「待生成」的目标；失败不自动重试，交给用户点「重新生成方案」。
  useEffect(() => {
    if (!source?.assetId) return;
    for (const target of targets) {
      if (target.assetId && target.analysisStatus === "pending" && !analyzingRef.current.has(target.clientId)) {
        void analyzeTarget(target.clientId);
      }
    }
    // analyzeTarget 通过 targetsRef 读取最新目标，这里按状态变化触发即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source?.assetId, targets]);

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

  const changeListingField = (clientId: string, key: "title_mode" | "category_name", value: string) =>
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
    // 只标红出问题的那一款 + 顶部提示，不自动打开侧边栏（用户自己点「导出信息编辑」去补）。
    setListingErrorsRevealed(true);
    const invalidListingTarget = targets.find((target) => Object.keys(validateListingFields(target.listingFields)).length > 0);
    if (invalidListingTarget) {
      const index = targets.indexOf(invalidListingTarget);
      setInvalidTargetId(invalidListingTarget.clientId);
      setError(`第 ${index + 1} 个产品有误：请点该款的「导出信息编辑」，检查上架信息中标红的字段。`);
      return;
    }

    const firstInvalid = firstInvalidReplicaTargetIndex(targets);
    if (firstInvalid >= 0) {
      const target = targets[firstInvalid];
      const reason = validateReplicaTargetDraft(target);
      setInvalidTargetId(target.clientId);
      setError(`第 ${firstInvalid + 1} 个产品有误：${reason.ok ? "请检查填写内容。" : reason.error}请点该款的「导出信息编辑」补全。`);
      return;
    }
    setInvalidTargetId("");

    let request;
    try {
      const title = `${source.filename || "POD 样图"} 复刻`;
      const compositionId = composition?.composition_id;
      const signature = JSON.stringify({
        source: source.assetId,
        title,
        composition: compositionId ?? "",
        targets: targets.map((target) => ({
          asset: target.assetId,
          name: target.productName,
          listing: target.listingFields,
          spec: target.specCard,
          analysis: target.analysisId,
        })),
      });
      request = buildReplicaBatchRequest({
        client_request_id: stableRequestId(signature),
        source_asset_id: source.assetId,
        title,
        composition_id: compositionId,
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
  const canSubmit = Boolean(source) && replicaAnalysesReady(targets) && !busyAction;
  const compositionName = composition
    ? (composition.is_builtin ? "系统默认模板（默认机位）" : (composition.name.trim() || "未命名构图"))
    : "系统默认模板（默认机位）";

  return (
    <section className="pod-replica-page" aria-label="POD 爆款复刻">
      {/* 与全定制/半定制同一套次级顶栏：图标 + 眉标 + 标题 + 右侧操作。 */}
      <header className="pod-page-header">
        <div className="pod-page-title">
          <span className="pod-page-title-icon iconfont icon-skin" aria-hidden="true" />
          <div>
            <span>POD BESTSELLER REPLICA</span>
            <h1>爆款复刻</h1>
            {modulePrice && <span className="pod-page-price">{modulePrice}</span>}
          </div>
        </div>
        <div className="pod-page-header-actions">
          {batchRunning && <span className="pod-live-badge"><i />批次后台运行中</span>}
          <button type="button" onClick={() => setHistoryOpen(true)}><span className="iconfont icon-time-circle" />复刻历史</button>
          {/* 管理模板与全定制一致放页头右侧，构图卡片里只留「生成新构图」。 */}
          <button type="button" onClick={() => setCompositionManagerOpen(true)}><span className="iconfont icon-skin" />管理模板</button>
        </div>
      </header>

      {error && <div className="pod-replica-message is-error" role="alert"><span>!</span><p>{error}</p><button type="button" onClick={clearMessages} aria-label="关闭提示">×</button></div>}

      <div className="pod-replica-layout">
        <div className="pod-replica-setup pod-prefill-panel">
          <ReplicaSourceUploader source={source} busy={busyAction === "upload-source"} onFiles={(files) => void uploadSource(files)} onClear={() => setSource(null)} />
          <ReplicaTargetList
            targets={targets}
            busy={busyAction === "upload-target"}
            invalidClientId={invalidTargetId}
            onFiles={(files) => void uploadTargets(files)}
            onRemove={(clientId) => {
              setTargets((current) => removeReplicaTarget(current, clientId));
              setInvalidTargetId((current) => (current === clientId ? "" : current));
            }}
            onOpen={setActiveTargetId}
          />
          {/* 构图入口：与全定制分开的复刻专用模板池；管理入口在页头右侧，这里只留生成。 */}
          <section className="pod-replica-composition-entry" aria-label="构图模板">
            <div className="pod-replica-composition-info">
              <span>COMPOSITION · 构图模板</span>
              <b>当前生效模板：{compositionName}</b>
            </div>
            <div className="pod-replica-composition-actions">
              <button type="button" onClick={() => setCompositionDrawerOpen(true)}>
                <span className="iconfont icon-robot" aria-hidden="true" />生成新构图
              </button>
            </div>
          </section>
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
        analyzing={activeTarget?.analysisStatus === "analyzing"}
        onChangeProductName={changeProductName}
        onListingFieldChange={changeListingField}
        onAddSku={addSku}
        onUpdateSku={updateSku}
        onRemoveSku={removeSku}
        onOpenSpecCardDrawer={setSpecCardTargetId}
        onAnalyze={(clientId) => void analyzeTarget(clientId)}
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

      {/* 构图入口：复刻独立模板池（scope=replica），与全定制互不覆盖。 */}
      <CompositionGenerateDrawer
        open={compositionDrawerOpen}
        scope="replica"
        onClose={() => setCompositionDrawerOpen(false)}
        onChanged={(next) => setComposition(next)}
      />
      <CompositionEditDrawer
        open={compositionEditOpen}
        target={compositionEditTarget}
        scope="replica"
        onClose={() => setCompositionEditOpen(false)}
        onChanged={(saved) => setComposition(saved)}
      />
      <CompositionManagerDrawer
        open={compositionManagerOpen}
        scope="replica"
        onClose={() => setCompositionManagerOpen(false)}
        onEdit={(template) => {
          setCompositionManagerOpen(false);
          setCompositionEditTarget(template);
          setCompositionEditOpen(true);
        }}
        onActiveChanged={(active) => setComposition(active)}
      />
    </section>
  );
}

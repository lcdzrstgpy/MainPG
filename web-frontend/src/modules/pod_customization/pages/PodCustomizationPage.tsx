import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { podCustomizationApi } from "../api/podCustomizationApi";
import { PodBatchGallery } from "../components/PodBatchGallery";
import { PodBatchHistory } from "../components/PodBatchHistory";
import { PodFailedRetryDialog } from "../components/PodFailedRetryDialog";
import { PodResultLightbox } from "../components/PodResultLightbox";
import { TemplateLibraryDrawer } from "../components/TemplateLibraryDrawer";
import {
  POD_BATCH_COUNTS,
  buildPromptV1,
  businessFieldsForApi,
  isPodBatchCount,
  isActiveBatchStatus,
  isActivePodItemStatus,
  isActivePodStyleTitleStatus,
  groupPodStyleRows,
  resolveCreativePrompt,
  listingFieldsForApi,
  shouldPollPodBatch,
} from "../data/podCustomizationModel";
import { batchRetryCandidates, type PodBatchRetryRequest } from "../data/podBatchRetry";
import {
  createPodSystemTemplate,
  createEmptyPodCustomizationDraft,
  loadPodCustomizationDraft,
  removePodSystemTemplate,
  resolvePodSystemTemplate,
  savePodCustomizationDraft,
  type PodSystemTemplate,
} from "../data/podCustomizationDraft";
import { usePodAssetUrl } from "../data/usePodAssetUrl";
import { getAuthAccount } from "../../../transport/http/client";
import type {
  PodBatch,
  PodBatchCount,
  PodBatchSummary,
  PodBusinessFieldsDraft,
  PodListingFieldsDraft,
  PodTemplate,
  PodTemplateCalibration,
} from "../types";
import "../styles/podCustomization.css";

type Props = {
  isActive?: boolean;
};

type PodDraftAccount = {
  account_id?: string;
  customer_id?: string;
  workspace_id?: string;
  workspace_code?: string;
};

type SkuField = "name" | "length_cm" | "width_cm" | "height_cm" | "weight_g";
type SkuFieldErrors = Record<string, string>;

const SKU_FIELD_LABELS: Record<SkuField, string> = {
  name: "名称",
  length_cm: "长度",
  width_cm: "宽度",
  height_cm: "高度",
  weight_g: "重量",
};

function skuErrorKey(index: number, key: SkuField): string {
  return `${index}:${key}`;
}

function validateSkuFields(skus: PodListingFieldsDraft["skus"]): SkuFieldErrors {
  return skus.reduce<SkuFieldErrors>((errors, sku, index) => {
    const skuLabel = sku.name.trim() || `第 ${index + 1} 个 SKU`;
    (Object.keys(SKU_FIELD_LABELS) as SkuField[]).forEach((key) => {
      const value = sku[key].trim();
      if (!value) {
        errors[skuErrorKey(index, key)] = `SKU「${skuLabel}」的${SKU_FIELD_LABELS[key]}不能为空。`;
      } else if (key !== "name" && (!Number.isFinite(Number(value)) || Number(value) <= 0)) {
        errors[skuErrorKey(index, key)] = `SKU「${skuLabel}」的${SKU_FIELD_LABELS[key]}必须是大于 0 的有效数字。`;
      }
    });
    return errors;
  }, {});
}

const BUSINESS_FIELDS: Array<{
  key: keyof PodBusinessFieldsDraft;
  label: string;
  multiline?: boolean;
  required?: boolean;
}> = [
  { key: "product_name", label: "产品名称", required: true },
  { key: "product_category", label: "产品品类", required: true },
  { key: "target_market", label: "目标市场", required: true },
  { key: "target_audience", label: "目标人群" },
  { key: "core_selling_points", label: "核心卖点", multiline: true },
  { key: "design_theme", label: "设计主题", required: true },
  { key: "style_keywords", label: "风格关键词" },
  { key: "color_preferences", label: "偏好配色" },
  { key: "excluded_elements", label: "禁用元素", multiline: true },
];

function autoGrowBusinessTextarea(textarea: HTMLTextAreaElement): void {
  textarea.style.height = "36px";
  textarea.style.height = `${Math.max(36, textarea.scrollHeight)}px`;
}

const LISTING_FIELDS: Array<{
  key: "declared_price" | "suggested_price_usd" | "category_name";
  label: string;
  inputMode?: "decimal" | "numeric";
}> = [
  { key: "declared_price", label: "申报价", inputMode: "decimal" },
  { key: "suggested_price_usd", label: "建议售价（USD）", inputMode: "decimal" },
  { key: "category_name", label: "店小秘类目" },
];

function toSummary(batch: PodBatch): PodBatchSummary {
  const { items: _items, template: _template, prompt_version: _promptVersion, business_fields: _fields, listing_fields: _listingFields, dianxiaomi_export: _dianxiaomiExport, creative_prompt: _prompt, ...summary } = batch;
  return summary;
}

function sortBatches(batches: PodBatchSummary[]): PodBatchSummary[] {
  return [...batches].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

function replaceTemplate(templates: PodTemplate[], updated: PodTemplate): PodTemplate[] {
  return templates.some((template) => template.id === updated.id)
    ? templates.map((template) => template.id === updated.id ? updated : template)
    : [updated, ...templates];
}

export function PodCustomizationPage({ isActive = true }: Props) {
  const draftScope = useMemo(() => {
    const account = getAuthAccount<PodDraftAccount>();
    const accountId = (account?.account_id || account?.customer_id)?.trim() ?? "";
    const workspaceId = (account?.workspace_id || account?.workspace_code)?.trim() ?? "";
    return accountId && workspaceId ? { accountId, workspaceId } : null;
  }, []);
  const [initialDraft] = useState(() => draftScope
    ? loadPodCustomizationDraft(draftScope.accountId, draftScope.workspaceId)
    : { state: createEmptyPodCustomizationDraft(), error: "登录账号信息不可用，暂不读取或保存 POD 本地草稿。" });
  const [templates, setTemplates] = useState<PodTemplate[]>([]);
  const [batches, setBatches] = useState<PodBatchSummary[]>([]);
  const [activeBatch, setActiveBatch] = useState<PodBatch | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState(initialDraft.state.selected_template_id);
  const [selectedTemplateSnapshot, setSelectedTemplateSnapshot] = useState<PodTemplate | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string>();
  const [businessFields, setBusinessFields] = useState<PodBusinessFieldsDraft>(initialDraft.state.business_fields);
  const [listingFields, setListingFields] = useState<PodListingFieldsDraft>(initialDraft.state.listing_fields);
  const [batchCount, setBatchCount] = useState<PodBatchCount>(initialDraft.state.batch_count);
  const [customCountMode, setCustomCountMode] = useState(initialDraft.state.custom_count_mode);
  const [customCountInput, setCustomCountInput] = useState(initialDraft.state.custom_count_input);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [currentBatchEdit, setCurrentBatchEdit] = useState<string | null>(initialDraft.state.current_batch_edit);
  const [systemTemplates, setSystemTemplates] = useState<PodSystemTemplate[]>(initialDraft.state.system_templates);
  const [templateDrawerOpen, setTemplateDrawerOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [failedRetryOpen, setFailedRetryOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(initialDraft.error ?? "");
  const [skuFieldErrors, setSkuFieldErrors] = useState<SkuFieldErrors>({});
  const [visibility, setVisibility] = useState<DocumentVisibilityState>(() => document.visibilityState);
  const requestGenerationRef = useRef(0);
  const lastDraftSaveErrorRef = useRef("");
  const businessTextareasRef = useRef<Array<HTMLTextAreaElement | null>>([]);

  const selectedTemplate = templates.find((template) => template.id === selectedTemplateId);
  const summaryTemplate = selectedTemplateSnapshot ?? selectedTemplate;
  const summaryTemplatePreview = usePodAssetUrl(summaryTemplate?.preview_url || summaryTemplate?.original_url);
  const summaryFields = businessFieldsForApi(businessFields);
  const selectedItem = activeBatch?.items.find((item) => item.id === selectedItemId);
  const failedRetryCandidates = activeBatch ? batchRetryCandidates(groupPodStyleRows(activeBatch)) : { image: [], title: [] };
  const builtInPrompt = useMemo(() => buildPromptV1(businessFields), [businessFields]);
  const resolvedPrompt = resolveCreativePrompt(businessFields, currentBatchEdit ?? "");
  const activeItemStatuses = activeBatch?.items.map((item) => item.status).join("|") ?? "";
  const activeTitleStatuses = activeBatch?.style_titles?.map((title) => title.status).join("|") ?? "";
  const batchRunning = activeBatch
    ? isActiveBatchStatus(activeBatch.status)
      || activeBatch.items.some((item) => isActivePodItemStatus(item.status))
      || activeBatch.style_titles?.some((title) => isActivePodStyleTitleStatus(title.status))
    : false;
  const skuLimitReached = listingFields.skus.length >= 100;

  useEffect(() => {
    let stopped = false;
    const generation = ++requestGenerationRef.current;
    const bootstrap = async () => {
      setLoading(true);
      const [templateResult, historyResult] = await Promise.allSettled([
        podCustomizationApi.listTemplates(),
        podCustomizationApi.listBatches(),
      ]);
      if (stopped || requestGenerationRef.current !== generation) return;

      if (templateResult.status === "fulfilled") {
        setTemplates(templateResult.value.templates);
        setSelectedTemplateId((current) => {
          const fallback = templateResult.value.templates.find((template) => template.calibration_status === "ready")?.id
            || templateResult.value.templates[0]?.id
            || "";
          return current && templateResult.value.templates.some((template) => template.id === current) ? current : fallback;
        });
      }
      if (historyResult.status === "fulfilled") {
        const history = sortBatches(historyResult.value.batches);
        setBatches(history);
        if (history[0]) {
          try {
            const batch = await podCustomizationApi.getBatch(history[0].id);
            if (!stopped && requestGenerationRef.current === generation) setActiveBatch(batch);
          } catch (cause) {
            if (!stopped) setError(cause instanceof Error ? cause.message : String(cause));
          }
        }
      }
      const failures = [templateResult, historyResult]
        .filter((result): result is PromiseRejectedResult => result.status === "rejected")
        .map((result) => result.reason instanceof Error ? result.reason.message : String(result.reason));
      if (failures.length) setError(failures.join("；"));
      setLoading(false);
    };
    void bootstrap();
    return () => { stopped = true; };
  }, []);

  useEffect(() => {
    const updateVisibility = () => setVisibility(document.visibilityState);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  useEffect(() => {
    if (!isActive) setTemplateDrawerOpen(false);
  }, [isActive]);

  useEffect(() => {
    if (!activeBatch) {
      setSelectedItemId(undefined);
      return;
    }
    if (selectedItemId && !activeBatch.items.some((item) => item.id === selectedItemId)) setSelectedItemId(undefined);
  }, [activeBatch?.id, activeBatch?.items, selectedItemId]);

  useEffect(() => {
    if (!activeBatch || !shouldPollPodBatch(
      isActive,
      visibility,
      activeBatch.status,
      activeBatch.items.map((item) => item.status),
      activeBatch.style_titles?.map((title) => title.status),
    )) return;
    let stopped = false;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const fresh = await podCustomizationApi.getBatch(activeBatch.id);
        if (stopped) return;
        setActiveBatch(fresh);
        setBatches((current) => sortBatches([toSummary(fresh), ...current.filter((batch) => batch.id !== fresh.id)]));
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        inFlight = false;
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 1_500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeBatch?.id, activeBatch?.status, activeItemStatuses, activeTitleStatuses, isActive, visibility]);

  useEffect(() => {
    if (!draftScope) return;
    const result = savePodCustomizationDraft(draftScope.accountId, draftScope.workspaceId, {
      version: 3,
      business_fields: businessFields,
      listing_fields: listingFields,
      batch_count: batchCount,
      custom_count_mode: customCountMode,
      custom_count_input: customCountInput,
      selected_template_id: selectedTemplateId,
      current_batch_edit: currentBatchEdit,
      system_templates: systemTemplates,
    });
    if (!result.ok && lastDraftSaveErrorRef.current !== result.error) {
      lastDraftSaveErrorRef.current = result.error;
      setError(result.error);
    }
  }, [batchCount, businessFields, currentBatchEdit, customCountInput, customCountMode, draftScope?.accountId, draftScope?.workspaceId, listingFields, selectedTemplateId, systemTemplates]);

  // Resize multiline business textareas on mount and whenever their values change.
  // onChange handles live typing; this effect handles initial load and draft restore.
  useLayoutEffect(() => {
    businessTextareasRef.current.forEach((textarea) => {
      if (!textarea) return;
      autoGrowBusinessTextarea(textarea);
    });
  }, [businessFields.core_selling_points, businessFields.excluded_elements]);

  const clearMessages = () => {
    setNotice("");
    setError("");
  };

  const updateBusinessField = (key: keyof PodBusinessFieldsDraft, value: string) => {
    setBusinessFields((current) => ({ ...current, [key]: value }));
  };

  const updateListingField = (key: "title_mode" | "declared_price" | "suggested_price_usd" | "category_name", value: string) => {
    setListingFields((current) => ({ ...current, [key]: value }));
  };

  const addSku = () => {
    if (skuLimitReached) return;
    setListingFields((current) => ({
      ...current,
      skus: [...current.skus, { name: "", length_cm: "", width_cm: "", height_cm: "", weight_g: "" }],
    }));
  };

  const updateSku = (index: number, key: SkuField, value: string) => {
    setSkuFieldErrors((current) => {
      const { [skuErrorKey(index, key)]: _cleared, ...remaining } = current;
      return remaining;
    });
    setListingFields((current) => ({
      ...current,
      skus: current.skus.map((sku, currentIndex) => currentIndex === index ? { ...sku, [key]: value } : sku),
    }));
  };

  const removeSku = (index: number) => {
    setSkuFieldErrors({});
    setListingFields((current) => ({ ...current, skus: current.skus.filter((_, currentIndex) => currentIndex !== index) }));
  };

  const selectTemplate = (templateId: string) => {
    setSelectedTemplateId(templateId);
    setSelectedTemplateSnapshot(null);
  };

  const saveCurrentAsSystemTemplate = () => {
    clearMessages();
    const templateSnapshot = selectedTemplateSnapshot ?? selectedTemplate;
    if (!templateSnapshot) {
      setError("请先从模板库选择一个产品模板。");
      return;
    }
    const name = window.prompt("系统模板名称", businessFields.product_name.trim() || templateSnapshot.name);
    if (name === null) return;
    const created = createPodSystemTemplate({ name, creativePrompt: resolvedPrompt, template: templateSnapshot });
    if (!created.ok) {
      setError(created.error);
      return;
    }
    setSystemTemplates((current) => [created.template, ...current]);
    setNotice("系统模板已保存，仅当前账号可见。");
  };

  const applySystemTemplate = (systemTemplate: PodSystemTemplate) => {
    clearMessages();
    const resolved = resolvePodSystemTemplate(systemTemplate, templates);
    if (!resolved.valid) {
      setError(resolved.reason);
      return;
    }
    setSelectedTemplateId(resolved.template.id);
    setSelectedTemplateSnapshot(resolved.template);
    setCurrentBatchEdit(systemTemplate.creativePrompt);
    setNotice(`已套用系统模板“${systemTemplate.name}”。`);
  };

  const deleteSystemTemplate = (templateId: string) => {
    setSystemTemplates((current) => removePodSystemTemplate(current, templateId));
    setNotice("系统模板已删除。");
  };

  const refreshHistory = async () => {
    setLoading(true);
    clearMessages();
    try {
      const response = await podCustomizationApi.listBatches();
      setBatches(sortBatches(response.batches));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  };

  const openBatch = async (batchId: string) => {
    setBusyAction(`batch:${batchId}`);
    clearMessages();
    try {
      const batch = await podCustomizationApi.getBatch(batchId);
      setActiveBatch(batch);
      setSelectedItemId(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const startBatch = async () => {
    clearMessages();
    const requestedCount = customCountMode ? Number(customCountInput) : batchCount;
    if (!isPodBatchCount(requestedCount)) {
      setError("生成数量必须是 1–200 的整数。");
      return;
    }
    if (!selectedTemplate) {
      setError("请先从模板库选择一个产品模板。");
      return;
    }
    if (selectedTemplate.calibration_status !== "ready") {
      setError("当前模板尚未完成蒙版与锚点标定。");
      setTemplateDrawerOpen(true);
      return;
    }
    const missingRequired = BUSINESS_FIELDS.filter((field) => field.required && !businessFields[field.key].trim());
    if (missingRequired.length) {
      setError(`请填写：${missingRequired.map((field) => field.label).join("、")}。`);
      return;
    }
    const listingFieldsResult = listingFieldsForApi(listingFields);
    const nextSkuFieldErrors = validateSkuFields(listingFields.skus);
    setSkuFieldErrors(nextSkuFieldErrors);
    if (Object.keys(nextSkuFieldErrors).length) {
      setError("请检查 SKU 预设中标红的字段。");
      return;
    }
    if (!listingFieldsResult.value) {
      setError(listingFieldsResult.error ?? "请完整填写店小秘上架信息。" );
      return;
    }
    const normalizedListingFields = listingFieldsResult.value;
    setBusyAction("create-batch");
    try {
      const created = await podCustomizationApi.createBatch({
        template_id: selectedTemplate.id,
        count: requestedCount,
        prompt_version: "v1",
        business_fields: businessFieldsForApi(businessFields),
        listing_fields: normalizedListingFields,
        creative_prompt: resolvedPrompt,
      });
      setActiveBatch(created);
      setSelectedItemId(undefined);
      setBatches((current) => sortBatches([toSummary(created), ...current.filter((batch) => batch.id !== created.id)]));
      setNotice(`已提交 ${requestedCount} 款创作，失败时最多重试一次。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const uploadTemplate = async (file: File, name: string) => {
    setBusyAction("upload");
    clearMessages();
    try {
      const created = await podCustomizationApi.uploadTemplate(file, name);
      setTemplates((current) => replaceTemplate(current, created));
      selectTemplate(created.id);
      setNotice("模板上传成功，正在启动 AI 蒙版与锚点标定。");
      return created;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    } finally {
      setBusyAction("");
    }
  };

  const calibrateTemplate = async (templateId: string) => {
    setBusyAction(`calibrate:${templateId}`);
    clearMessages();
    try {
      const calibrated = await podCustomizationApi.calibrateTemplate(templateId);
      setTemplates((current) => replaceTemplate(current, calibrated));
      setNotice(calibrated.calibration_status === "ready" ? "AI 标定完成，可继续微调或直接使用。" : "AI 标定任务已提交。");
      return calibrated;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    } finally {
      setBusyAction("");
    }
  };

  const saveTemplateCalibration = async (templateId: string, calibration: PodTemplateCalibration) => {
    setBusyAction(`save-calibration:${templateId}`);
    clearMessages();
    try {
      const saved = await podCustomizationApi.saveTemplateCalibration(templateId, calibration);
      setTemplates((current) => replaceTemplate(current, saved));
      setNotice("模板标定已保存。");
      return saved;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    } finally {
      setBusyAction("");
    }
  };

  const refreshActiveBatch = async (batchId: string) => {
    try {
      const fresh = await podCustomizationApi.getBatch(batchId);
      setActiveBatch(fresh);
      setBatches((current) => sortBatches([toSummary(fresh), ...current.filter((batch) => batch.id !== fresh.id)]));
    } catch {
      // The returned item is already visible; the normal refresh path can recover batch metadata.
    }
  };

  const regenerateStyle = async (styleIndex: number) => {
    if (!activeBatch) return;
    setBusyAction(`regenerate-style:${styleIndex}`);
    clearMessages();
    try {
      const updated = await podCustomizationApi.regenerateStyle(activeBatch.id, styleIndex, activeBatch.creative_prompt);
      setActiveBatch((current) => current ? {
        ...current,
        items: current.items.map((item) => updated.results.find((result) => result.id === item.id) ?? item),
      } : current);
      setNotice(`款式 #${styleIndex} 已重新提交图片生成。`);
      await refreshActiveBatch(activeBatch.id);
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
      const updated = await podCustomizationApi.regenerateStyleTitle(activeBatch.id, styleIndex);
      setActiveBatch((current) => current ? {
        ...current,
        style_titles: [...(current.style_titles ?? []).filter((title) => title.style_index !== updated.style_index), updated],
      } : current);
      setNotice(`款式 #${styleIndex} 已提交标题生成。`);
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const updateExportSelection = async (styleIndex: number, selected: boolean) => {
    if (!activeBatch) return;
    const previousSelected = activeBatch.style_titles?.find((title) => title.style_index === styleIndex)?.export_selected ?? true;
    const previousExportStatus = activeBatch.dianxiaomi_export;
    const selectionDelta = selected === previousSelected ? 0 : selected ? 1 : -1;
    setBusyAction(`update-export-selection:${styleIndex}`);
    clearMessages();
    setActiveBatch((current) => current ? {
      ...current,
      dianxiaomi_export: current.dianxiaomi_export.selected_exportable_style_count === undefined ? current.dianxiaomi_export : {
        ...current.dianxiaomi_export,
        selected_exportable_style_count: Math.max(0, current.dianxiaomi_export.selected_exportable_style_count + selectionDelta),
        user_excluded_style_count: current.dianxiaomi_export.user_excluded_style_count === undefined
          ? undefined
          : Math.max(0, current.dianxiaomi_export.user_excluded_style_count - selectionDelta),
      },
      style_titles: current.style_titles?.map((title) => title.style_index === styleIndex ? { ...title, export_selected: selected } : title),
    } : current);
    try {
      const updated = await podCustomizationApi.updateExportSelection(activeBatch.id, styleIndex, selected);
      setActiveBatch((current) => current ? {
        ...current,
        style_titles: current.style_titles?.map((title) => title.style_index === updated.style_index ? { ...title, export_selected: updated.export_selected } : title),
      } : current);
      setNotice(updated.export_selected ? `款式 #${styleIndex} 已选中，导出时会包含该款。` : `款式 #${styleIndex} 已取消选中，导出时会剔除该款。`);
    } catch (cause) {
      setActiveBatch((current) => current ? {
        ...current,
        dianxiaomi_export: previousExportStatus,
        style_titles: current.style_titles?.map((title) => title.style_index === styleIndex ? { ...title, export_selected: previousSelected } : title),
      } : current);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const saveManualTitle = async (styleIndex: number, title: string) => {
    if (!activeBatch) return;
    setBusyAction(`save-title:${styleIndex}`);
    clearMessages();
    try {
      const updated = await podCustomizationApi.updateManualTitle(activeBatch.id, styleIndex, title);
      setActiveBatch((current) => current ? {
        ...current,
        style_titles: [...(current.style_titles ?? []).filter((item) => item.style_index !== updated.style_index), updated],
      } : current);
      setNotice(`款式 #${styleIndex} 已保存手动标题。`);
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      throw cause;
    } finally {
      setBusyAction("");
    }
  };

  const pauseBatch = async () => {
    if (!activeBatch) return;
    setBusyAction("pause-batch");
    clearMessages();
    try {
      await podCustomizationApi.pauseBatch(activeBatch.id);
      setNotice("已请求暂停：已提交的款会完成整款，其余款不会继续发起。");
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const cancelBatch = async () => {
    if (!activeBatch) return;
    setBusyAction("cancel-batch");
    clearMessages();
    try {
      await podCustomizationApi.cancelBatch(activeBatch.id);
      setNotice("已请求取消，正在停止批次。");
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const resumeBatch = async () => {
    if (!activeBatch) return;
    setBusyAction("resume-batch");
    clearMessages();
    try {
      await podCustomizationApi.resumeBatch(activeBatch.id);
      setNotice("已提交继续，任务将在后台继续。");
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const downloadAsset = async (path: string, filename: string) => {
    const itemId = selectedItem?.id ?? "asset";
    setBusyAction(`download:${itemId}`);
    clearMessages();
    try {
      await podCustomizationApi.downloadAsset(path, filename);
    } catch (cause) {
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
      const exported = await podCustomizationApi.exportDianxiaomi(activeBatch.id);
      setNotice(`导出 ${exported.exportedStyles} 款、跳过 ${exported.skippedStyles} 款。`);
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
      setNotice(`已提交图片重试 ${request.image_style_indices.length} 款、标题重试 ${request.title_style_indices.length} 款。`);
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setFailedRetryOpen(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  return (
    <section className="pod-customization-page" aria-label="POD 定制">
      <header className="pod-page-header">
        <div className="pod-page-title"><span className="pod-page-title-icon iconfont icon-skin" aria-hidden="true" /><div><span>POD CUSTOMIZATION · DIRECT LISTING</span><h1>POD 定制</h1></div></div>
        <div className="pod-page-header-actions">
          {batchRunning && <span className="pod-live-badge"><i />批次后台运行中</span>}
          <button type="button" onClick={() => setTemplateDrawerOpen(true)}><span className="iconfont icon-upload" />上传当前批次模板</button>
          <button type="button" onClick={() => setTemplateDrawerOpen(true)}><span className="iconfont icon-appstore" />查看历史批次模板</button>
        </div>
      </header>

      {(notice || error) && <div className={`pod-page-message ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}><span>{error ? "!" : "✓"}</span><p>{error || notice}</p><button type="button" onClick={clearMessages} aria-label="关闭提示">×</button></div>}

      <div className="pod-workbench-grid">
        <aside className="pod-setup-column pod-brief-sidebar">
          <section className="pod-setup-card pod-business-editor">
            <div className="pod-section-title"><span>BRIEF EDITOR</span><h2>业务信息编辑</h2><small>用于直出 Prompt</small></div>
            <div className="pod-business-fields">
              {BUSINESS_FIELDS.map((field, fieldIndex) => {
                const multilineIndex = BUSINESS_FIELDS.slice(0, fieldIndex).filter((f) => f.multiline).length;
                return <label key={field.key} className={field.multiline ? "is-multiline" : ""}><span>{field.label}{field.required && <em>*</em>}</span>{field.multiline
                  ? <textarea rows={1} ref={(el) => { businessTextareasRef.current[multilineIndex] = el; }} value={businessFields[field.key]} onChange={(event) => {
                    updateBusinessField(field.key, event.currentTarget.value);
                    autoGrowBusinessTextarea(event.currentTarget);
                  }} />
                  : <input value={businessFields[field.key]} onChange={(event) => updateBusinessField(field.key, event.target.value)} />}</label>;
              })}
            </div>
            <section className="pod-listing-fields" aria-labelledby="pod-dianxiaomi-listing-title">
              <div className="pod-listing-fields-heading"><span>DIANXIAOMI LISTING</span><h3 id="pod-dianxiaomi-listing-title">店小秘上架信息</h3><small>创建批次时保存为不可缺失的上架快照</small></div>
              <div className="pod-title-mode" role="radiogroup" aria-label="标题模式">
                <span>标题模式<em>*</em></span>
                <div>
                  <button type="button" role="radio" aria-checked={listingFields.title_mode === "long"} className={listingFields.title_mode === "long" ? "is-active" : ""} onClick={() => updateListingField("title_mode", "long")}><b>长标题</b></button>
                  <button type="button" role="radio" aria-checked={listingFields.title_mode === "short"} className={listingFields.title_mode === "short" ? "is-active" : ""} onClick={() => updateListingField("title_mode", "short")}><b>短标题</b></button>
                </div>
              </div>
              <div className="pod-business-fields">
                {LISTING_FIELDS.map((field) => <label key={field.key}><span>{field.label}<em>*</em></span><input value={listingFields[field.key]} inputMode={field.inputMode} onChange={(event) => updateListingField(field.key, event.target.value)} /></label>)}
              </div>
              <div className="pod-sku-editor" aria-label="SKU 预设">
                <div className="pod-sku-editor-heading"><span>SKU 预设<small>每个 SKU 需填写名称、长、宽、高与重量</small></span><button type="button" onClick={addSku} disabled={skuLimitReached} aria-describedby={skuLimitReached ? "pod-sku-limit-notice" : undefined} title={skuLimitReached ? "最多可添加 100 个 SKU" : undefined}><span className="iconfont icon-plus" aria-hidden="true" />新增 SKU</button></div>
                {skuLimitReached && <p id="pod-sku-limit-notice" className="pod-sku-limit-notice" role="status">已达到 100 个 SKU 上限。</p>}
                <div className="pod-sku-inputs">
                  {listingFields.skus.map((sku, index) => <div key={index} className="pod-sku-input-row">
                    <label><span>SKU 名称 {index + 1}</span><input value={sku.name} onChange={(event) => updateSku(index, "name", event.target.value)} aria-label="SKU 名称" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "name")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "name")] ? `pod-sku-error-${index}-name` : undefined} />{skuFieldErrors[skuErrorKey(index, "name")] && <small id={`pod-sku-error-${index}-name`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "name")]}</small>}</label>
                    <label><span>长（cm）</span><input value={sku.length_cm} inputMode="decimal" onChange={(event) => updateSku(index, "length_cm", event.target.value)} aria-label="SKU 长（cm）" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "length_cm")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "length_cm")] ? `pod-sku-error-${index}-length_cm` : undefined} />{skuFieldErrors[skuErrorKey(index, "length_cm")] && <small id={`pod-sku-error-${index}-length_cm`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "length_cm")]}</small>}</label>
                    <label><span>宽（cm）</span><input value={sku.width_cm} inputMode="decimal" onChange={(event) => updateSku(index, "width_cm", event.target.value)} aria-label="SKU 宽（cm）" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "width_cm")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "width_cm")] ? `pod-sku-error-${index}-width_cm` : undefined} />{skuFieldErrors[skuErrorKey(index, "width_cm")] && <small id={`pod-sku-error-${index}-width_cm`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "width_cm")]}</small>}</label>
                    <label><span>高（cm）</span><input value={sku.height_cm} inputMode="decimal" onChange={(event) => updateSku(index, "height_cm", event.target.value)} aria-label="SKU 高（cm）" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "height_cm")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "height_cm")] ? `pod-sku-error-${index}-height_cm` : undefined} />{skuFieldErrors[skuErrorKey(index, "height_cm")] && <small id={`pod-sku-error-${index}-height_cm`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "height_cm")]}</small>}</label>
                    <label><span>重量（g）</span><input value={sku.weight_g} inputMode="decimal" onChange={(event) => updateSku(index, "weight_g", event.target.value)} aria-label="SKU 重量（g）" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "weight_g")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "weight_g")] ? `pod-sku-error-${index}-weight_g` : undefined} />{skuFieldErrors[skuErrorKey(index, "weight_g")] && <small id={`pod-sku-error-${index}-weight_g`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "weight_g")]}</small>}</label>
                    <button type="button" onClick={() => removeSku(index)} aria-label="删除 SKU">×</button>
                  </div>)}
                </div>
              </div>
            </section>
            <div className="pod-advanced-prompt">
              <button type="button" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen((open) => !open)}><span><b>高级：本批次创意编辑</b><small>内置 POD Direct Listing Prompt v1</small></span><i className={`iconfont icon-down ${advancedOpen ? "is-open" : ""}`} /></button>
              {advancedOpen && <div className="pod-advanced-prompt-editor"><textarea value={currentBatchEdit ?? builtInPrompt} onChange={(event) => setCurrentBatchEdit(event.target.value)} aria-label="本批次创意提示词" /><div><span>{currentBatchEdit === null ? "正在使用内置 v1" : "已为本批次自定义"}</span><button type="button" onClick={() => setCurrentBatchEdit(null)}>重置为 v1</button></div></div>}
            </div>
            <div className="pod-volume-inline"><b>生成数量</b></div>
            <div className="pod-count-options" role="radiogroup" aria-label="生成数量">
              {POD_BATCH_COUNTS.map((count) => <button key={count} type="button" role="radio" aria-checked={!customCountMode && batchCount === count} className={!customCountMode && batchCount === count ? "is-active" : ""} onClick={() => { setCustomCountMode(false); setBatchCount(count); }}><b>{count}</b><span>款</span></button>)}
              <button type="button" role="radio" aria-checked={customCountMode} className={customCountMode ? "is-active" : ""} onClick={() => { setCustomCountMode(true); setCustomCountInput(String(batchCount)); }}><b>自定义</b></button>
            </div>
            {customCountMode && <label className="pod-custom-count"><span>自定义数量</span><input type="number" min={1} max={200} step={1} value={customCountInput} aria-label="自定义生成数量" onChange={(event) => setCustomCountInput(event.target.value)} /><small>1–200 款</small></label>}
            <button type="button" className="pod-save-system-template-button" disabled={!selectedTemplate} onClick={saveCurrentAsSystemTemplate}>
              <span className="iconfont icon-save" aria-hidden="true" />
              <span className="pod-save-system-template-copy"><b>保存为系统模板</b><small>保存当前提示词与模板图</small></span>
              <span className="iconfont icon-arrowright" aria-hidden="true" />
            </button>
            <button type="button" className="pod-start-button" disabled={busyAction === "create-batch" || !selectedTemplate} onClick={() => void startBatch()}>{busyAction === "create-batch" ? <><span className="iconfont icon-loading" />正在提交</> : <><span className="iconfont icon-rocket" />开始生成 {customCountMode ? customCountInput || "自定义" : batchCount} 款</>}</button>
          </section>
          <button type="button" className={`pod-history-trigger ${historyOpen ? "is-open" : ""}`} onClick={() => setHistoryOpen((open) => !open)}>定制记录<span>{historyOpen ? "收起" : `${batches.length} 个批次`}</span></button>
          {historyOpen && <PodBatchHistory batches={batches} activeBatchId={activeBatch?.id} loading={loading || busyAction.startsWith("batch:")} onOpen={(batchId) => void openBatch(batchId)} onRefresh={() => void refreshHistory()} />}
        </aside>
        <main className="pod-results-column">
          <section className="pod-current-template-summary">
            <header><div><span>CURRENT TEMPLATE</span><h2>当前批次模板图</h2></div><button type="button" onClick={() => setTemplateDrawerOpen(true)}>更换模板</button></header>
            <div className="pod-current-template-body">
              <button type="button" className="pod-current-template-image" onClick={() => setTemplateDrawerOpen(true)}>
                {summaryTemplatePreview ? <img src={summaryTemplatePreview} alt={summaryTemplate?.name || "当前模板"} /> : <span>选择模板</span>}
              </button>
              <dl>
                <div><dt>产品主体</dt><dd>{summaryFields.product_name || "未填写"}</dd></div>
                <div><dt>目标市场</dt><dd>{summaryFields.target_market || "未填写"}</dd></div>
                <div><dt>目标人群</dt><dd>{summaryFields.target_audience || "未填写"}</dd></div>
                <div><dt>设计主题</dt><dd>{summaryFields.design_theme || "未填写"}</dd></div>
              </dl>
            </div>
          </section>
          <PodBatchGallery
            batch={activeBatch}
            busyAction={busyAction}
            onOpenResult={(item) => setSelectedItemId(item.id)}
            onRegenerateStyle={(styleIndex) => void regenerateStyle(styleIndex)}
            onRegenerateTitle={(styleIndex) => void regenerateStyleTitle(styleIndex)}
            onUpdateExportSelection={(styleIndex, selected) => void updateExportSelection(styleIndex, selected)}
            onSaveTitle={(styleIndex, title) => saveManualTitle(styleIndex, title)}
            onExportDianxiaomi={() => void exportDianxiaomi()}
            onOpenFailedRetry={() => setFailedRetryOpen(true)}
            onPauseBatch={() => void pauseBatch()}
            onCancelBatch={() => void cancelBatch()}
            onResumeBatch={() => void resumeBatch()}
          />
        </main>
      </div>

      <PodResultLightbox
        batch={activeBatch}
        item={selectedItem}
        busyAction={busyAction}
        onClose={() => setSelectedItemId(undefined)}
        onDownload={downloadAsset}
      />

      <PodFailedRetryDialog
        open={failedRetryOpen}
        imageCandidates={failedRetryCandidates.image}
        titleCandidates={failedRetryCandidates.title}
        busy={busyAction === "retry-failed"}
        onClose={() => setFailedRetryOpen(false)}
        onSubmit={(request) => void retryFailed(request)}
      />

      <TemplateLibraryDrawer
        open={templateDrawerOpen}
        templates={templates}
        systemTemplates={systemTemplates}
        selectedTemplateId={selectedTemplateId}
        busyAction={busyAction}
        onClose={() => setTemplateDrawerOpen(false)}
        onSelect={selectTemplate}
        onApplySystemTemplate={applySystemTemplate}
        onDeleteSystemTemplate={deleteSystemTemplate}
        onUpload={uploadTemplate}
        onCalibrate={calibrateTemplate}
        onSaveCalibration={saveTemplateCalibration}
      />
    </section>
  );
}

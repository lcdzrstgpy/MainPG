import { useEffect, useMemo, useRef, useState } from "react";

import { podCustomizationApi } from "../api/podCustomizationApi";
import { PodBatchGallery } from "../components/PodBatchGallery";
import { PodBatchHistory } from "../components/PodBatchHistory";
import { PodResultLightbox } from "../components/PodResultLightbox";
import { TemplateLibraryDrawer } from "../components/TemplateLibraryDrawer";
import {
  EMPTY_POD_BUSINESS_FIELDS,
  EMPTY_POD_LISTING_FIELDS,
  POD_BATCH_COUNTS,
  buildPromptV1,
  businessFieldsForApi,
  canRegeneratePodStyle,
  canRegeneratePodStyleTitle,
  groupPodStyleRows,
  isPodBatchCount,
  isActiveBatchStatus,
  isActivePodItemStatus,
  isActivePodStyleTitleStatus,
  resolveCreativePrompt,
  listingFieldsForApi,
  shouldPollPodBatch,
  summarizeSkippedPodStyles,
  skippedPodStylesToastMessage,
} from "../data/podCustomizationModel";
import { usePodAssetUrl } from "../data/usePodAssetUrl";
import { showToast } from "../../../shared/components/toastStore";
import type {
  PodBatch,
  PodBatchCount,
  PodBatchSummary,
  PodBillingRun,
  PodBusinessFieldsDraft,
  PodListingFieldsDraft,
  PodTemplate,
  PodTemplateCalibration,
} from "../types";
import "../styles/podCustomization.css";

type Props = {
  isActive?: boolean;
};

// 每条链接（款式）免费手动重试次数，与后端 service.POD_FREE_RETRY_LIMIT 保持一致；
// 超过后需用户确认「无论失败与成功均会扣费」才允许继续重试。
const DEFAULT_FREE_RETRY_LIMIT = 2;

function paidRetryConfirmationMessage(limit: number): string {
  return `该链接免费重试额度（${limit} 次）已用完，继续重试无论失败与成功均会扣费（按款式价格，约 40-50 积分），确认后方可继续。`;
}

function isPaidRetryExhaustedError(cause: unknown): boolean {
  return /免费重试额度|免费.*用完/.test(cause instanceof Error ? cause.message : String(cause));
}

const BUSINESS_FIELDS: Array<{
  key: keyof PodBusinessFieldsDraft;
  label: string;
  placeholder: string;
  multiline?: boolean;
  required?: boolean;
}> = [
  { key: "product_name", label: "产品名称", placeholder: "例如：旅行保温杯", required: true },
  { key: "product_category", label: "产品品类", placeholder: "例如：户外饮具", required: true },
  { key: "target_market", label: "目标市场", placeholder: "例如：美国", required: true },
  { key: "target_audience", label: "目标人群", placeholder: "例如：露营与通勤人群" },
  { key: "core_selling_points", label: "核心卖点", placeholder: "轻量、防漏、保温 12 小时", multiline: true },
  { key: "design_theme", label: "设计主题", placeholder: "例如：国家公园", required: true },
  { key: "style_keywords", label: "风格关键词", placeholder: "复古丝网印刷、粗线条" },
  { key: "color_preferences", label: "偏好配色", placeholder: "松绿、砂岩黄" },
  { key: "excluded_elements", label: "禁用元素", placeholder: "品牌 Logo、人物肖像", multiline: true },
];

function autoGrowBusinessTextarea(textarea: HTMLTextAreaElement): void {
  textarea.style.height = "36px";
  textarea.style.height = `${Math.max(36, textarea.scrollHeight)}px`;
}

const LISTING_FIELDS: Array<{
  key: keyof PodListingFieldsDraft;
  label: string;
  placeholder: string;
  inputMode?: "decimal" | "numeric";
}> = [
  { key: "declared_price", label: "申报价", placeholder: "例如：19.95", inputMode: "decimal" },
  { key: "suggested_price_usd", label: "建议售价（USD）", placeholder: "例如：24.50", inputMode: "decimal" },
  { key: "length_cm", label: "长（cm）", placeholder: "例如：31", inputMode: "decimal" },
  { key: "width_cm", label: "宽（cm）", placeholder: "例如：20", inputMode: "decimal" },
  { key: "height_cm", label: "高（cm）", placeholder: "例如：7.5", inputMode: "decimal" },
  { key: "weight_g", label: "重量（g）", placeholder: "例如：840", inputMode: "decimal" },
  { key: "category_id", label: "店小秘类目 ID", placeholder: "仅数字", inputMode: "numeric" },
  { key: "product_code_prefix", label: "商品编码前缀", placeholder: "例如：POD-US" },
  { key: "sku_prefix", label: "SKU 前缀", placeholder: "例如：TUMBLER" },
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
  const [templates, setTemplates] = useState<PodTemplate[]>([]);
  const [batches, setBatches] = useState<PodBatchSummary[]>([]);
  const [pendingBillingRuns, setPendingBillingRuns] = useState<PodBillingRun[]>([]);
  const [activeBatch, setActiveBatch] = useState<PodBatch | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [selectedItemId, setSelectedItemId] = useState<string>();
  const [businessFields, setBusinessFields] = useState<PodBusinessFieldsDraft>({ ...EMPTY_POD_BUSINESS_FIELDS });
  const [listingFields, setListingFields] = useState<PodListingFieldsDraft>({ ...EMPTY_POD_LISTING_FIELDS });
  const [batchCount, setBatchCount] = useState<PodBatchCount>(20);
  const [customCountMode, setCustomCountMode] = useState(false);
  const [customCountInput, setCustomCountInput] = useState("20");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [currentBatchEdit, setCurrentBatchEdit] = useState<string | null>(null);
  const [templateDrawerOpen, setTemplateDrawerOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [visibility, setVisibility] = useState<DocumentVisibilityState>(() => document.visibilityState);
  const requestGenerationRef = useRef(0);

  const selectedTemplate = templates.find((template) => template.id === selectedTemplateId);
  const summaryTemplate = activeBatch?.template ?? selectedTemplate;
  const summaryTemplatePreview = usePodAssetUrl(summaryTemplate?.preview_url || summaryTemplate?.original_url);
  const summaryFields = activeBatch?.business_fields ?? businessFieldsForApi(businessFields);
  const selectedItem = activeBatch?.items.find((item) => item.id === selectedItemId);
  const builtInPrompt = useMemo(() => buildPromptV1(businessFields), [businessFields]);
  const resolvedPrompt = resolveCreativePrompt(businessFields, currentBatchEdit ?? "");
  const activeItemStatuses = activeBatch?.items.map((item) => item.status).join("|") ?? "";
  const activeTitleStatuses = activeBatch?.style_titles?.map((title) => title.status).join("|") ?? "";
  const batchRunning = activeBatch
    ? isActiveBatchStatus(activeBatch.status)
      || activeBatch.items.some((item) => isActivePodItemStatus(item.status))
      || activeBatch.style_titles?.some((title) => isActivePodStyleTitleStatus(title.status))
    : false;

  useEffect(() => {
    let stopped = false;
    const generation = ++requestGenerationRef.current;
    const bootstrap = async () => {
      setLoading(true);
      setError("");
      const [templateResult, historyResult, billingResult] = await Promise.allSettled([
        podCustomizationApi.listTemplates(),
        podCustomizationApi.listBatches(),
        podCustomizationApi.listPendingBillingRuns(),
      ]);
      if (stopped || requestGenerationRef.current !== generation) return;

      if (templateResult.status === "fulfilled") {
        setTemplates(templateResult.value.templates);
        setSelectedTemplateId((current) => current || templateResult.value.templates.find((template) => template.calibration_status === "ready")?.id || templateResult.value.templates[0]?.id || "");
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
      if (billingResult.status === "fulfilled") setPendingBillingRuns(billingResult.value.runs);
      const failures = [templateResult, historyResult, billingResult]
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
        if (fresh.status === "billing_auth_required" || fresh.status === "settlement_pending") {
          const pending = await podCustomizationApi.listPendingBillingRuns();
          if (!stopped) setPendingBillingRuns(pending.runs);
        }
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

  const clearMessages = () => {
    setNotice("");
    setError("");
  };

  const updateBusinessField = (key: keyof PodBusinessFieldsDraft, value: string) => {
    setBusinessFields((current) => ({ ...current, [key]: value }));
  };

  const updateListingField = (key: keyof PodListingFieldsDraft, value: string) => {
    setListingFields((current) => ({ ...current, [key]: value }));
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
      setError("当前模板尚未完成 AI 标定。");
      setTemplateDrawerOpen(true);
      return;
    }
    const missingRequired = BUSINESS_FIELDS.filter((field) => field.required && !businessFields[field.key].trim());
    if (missingRequired.length) {
      setError(`请填写：${missingRequired.map((field) => field.label).join("、")}。`);
      return;
    }
    const listingFieldsResult = listingFieldsForApi(listingFields);
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
      setCurrentBatchEdit(null);
      setNotice(`已提交 ${requestedCount} 款创作，正在后台生成。`);
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
      setSelectedTemplateId(created.id);
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

  const freeRetryLimit = activeBatch?.free_retry_limit ?? DEFAULT_FREE_RETRY_LIMIT;

  const styleNeedsPaidRetry = (batch: PodBatch, styleIndex: number): boolean => {
    const retryCount = batch.style_retries?.find((retry) => retry.style_index === styleIndex)?.retry_count ?? 0;
    return retryCount >= (batch.free_retry_limit ?? DEFAULT_FREE_RETRY_LIMIT);
  };

  const confirmPaidRetryIfNeeded = (styleIndex: number): boolean => {
    if (!activeBatch) return false;
    if (!styleNeedsPaidRetry(activeBatch, styleIndex)) return true;
    return window.confirm(paidRetryConfirmationMessage(freeRetryLimit));
  };

  const retryAfterPaidRetryConfirmation = async (styleIndex: number, cause: unknown, kind: "style" | "title"): Promise<boolean> => {
    if (!activeBatch || !isPaidRetryExhaustedError(cause)) return false;
    // 前端重试计数未及时刷新时的兜底：再次确认后按付费重试提交。
    if (!window.confirm(paidRetryConfirmationMessage(freeRetryLimit))) return false;
    try {
      if (kind === "style") {
        const updated = await podCustomizationApi.regenerateStyle(activeBatch.id, styleIndex, activeBatch.creative_prompt, true);
        setActiveBatch((current) => current ? {
          ...current,
          items: current.items.map((item) => updated.results.find((result) => result.id === item.id) ?? item),
        } : current);
        setNotice(`款式 #${styleIndex} 已重新提交生成（付费重试）。`);
      } else {
        const updated = await podCustomizationApi.regenerateStyleTitle(activeBatch.id, styleIndex, true);
        setActiveBatch((current) => current ? {
          ...current,
          style_titles: [...(current.style_titles ?? []).filter((title) => title.style_index !== updated.style_index), updated],
        } : current);
        setNotice(`款式 #${styleIndex} 已提交标题生成（付费重试）。`);
      }
      await refreshActiveBatch(activeBatch.id);
      return true;
    } catch (retryCause) {
      setError(retryCause instanceof Error ? retryCause.message : String(retryCause));
      return true;
    }
  };

  const regenerateStyle = async (styleIndex: number) => {
    if (!activeBatch) return;
    if (!confirmPaidRetryIfNeeded(styleIndex)) return;
    setBusyAction(`regenerate-style:${styleIndex}`);
    clearMessages();
    try {
      const updated = await podCustomizationApi.regenerateStyle(
        activeBatch.id,
        styleIndex,
        activeBatch.creative_prompt,
        styleNeedsPaidRetry(activeBatch, styleIndex),
      );
      setActiveBatch((current) => current ? {
        ...current,
        items: current.items.map((item) => updated.results.find((result) => result.id === item.id) ?? item),
      } : current);
      setNotice(`款式 #${styleIndex} 已重新提交生成。`);
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      if (await retryAfterPaidRetryConfirmation(styleIndex, cause, "style")) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const regenerateStyleTitle = async (styleIndex: number) => {
    if (!activeBatch) return;
    if (!confirmPaidRetryIfNeeded(styleIndex)) return;
    setBusyAction(`regenerate-title:${styleIndex}`);
    clearMessages();
    try {
      const updated = await podCustomizationApi.regenerateStyleTitle(
        activeBatch.id,
        styleIndex,
        styleNeedsPaidRetry(activeBatch, styleIndex),
      );
      setActiveBatch((current) => current ? {
        ...current,
        style_titles: [...(current.style_titles ?? []).filter((title) => title.style_index !== updated.style_index), updated],
      } : current);
      setNotice(`款式 #${styleIndex} 已提交标题生成。`);
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      if (await retryAfterPaidRetryConfirmation(styleIndex, cause, "title")) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const retryFailedStyles = async () => {
    if (!activeBatch) return;
    const failed = groupPodStyleRows(activeBatch).filter(
      (style) =>
        canRegeneratePodStyle(activeBatch.status, style.status)
        || canRegeneratePodStyleTitle(activeBatch.status, style.title_status, style.results),
    );
    if (!failed.length) return;
    const overQuota = failed.filter((style) => style.retry_count >= freeRetryLimit);
    if (
      overQuota.length > 0
      && !window.confirm(
        `${overQuota.length} 款已超过免费重试额度（${freeRetryLimit} 次），继续重试无论失败与成功均会扣费（按款式价格，约 40-50 积分/款），确认继续？`,
      )
    ) return;
    const overQuotaIndices = new Set(overQuota.map((style) => style.index));
    setBusyAction("batch-retry-failed");
    clearMessages();
    let submitted = 0;
    let failedCount = 0;
    try {
      // 并行重试所有失败款式：全部一次性提交，互不阻塞；超过免费额度的款式按付费重试
      // （无论成败均扣费），失败项只计入 failedCount 不影响其余款式。
      const settled = await Promise.allSettled(
        failed.map(async (style) => {
          const ackPaidRetry = overQuotaIndices.has(style.index);
          if (canRegeneratePodStyleTitle(activeBatch.status, style.title_status, style.results)) {
            await podCustomizationApi.regenerateStyleTitle(activeBatch.id, style.index, ackPaidRetry);
          } else {
            await podCustomizationApi.regenerateStyle(activeBatch.id, style.index, activeBatch.creative_prompt, ackPaidRetry);
          }
        }),
      );
      submitted = settled.filter((result) => result.status === "fulfilled").length;
      failedCount = settled.filter((result) => result.status === "rejected").length;
      setNotice(
        submitted > 0
          ? `已批量重试 ${submitted} 款${failedCount ? `，${failedCount} 款提交失败` : ""}。`
          : "没有可重试的款式。",
      );
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const resumeBillingRun = async (run: PodBillingRun) => {
    setBusyAction(`resume-billing:${run.id}`);
    clearMessages();
    try {
      await podCustomizationApi.resumeBillingRun(run.id);
      const pending = await podCustomizationApi.listPendingBillingRuns();
      setPendingBillingRuns(pending.runs);
      setNotice("已重新授权并提交恢复，任务将在后台继续。");
      if (run.batch_id) await refreshActiveBatch(run.batch_id);
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
      if (exported.skippedStyles > 0) {
        const breakdown = summarizeSkippedPodStyles(activeBatch);
        showToast(skippedPodStylesToastMessage(exported.skippedStyles, breakdown), "error");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  return (
    <section className="pod-customization-page" aria-label="POD 定制">
      <header className="pod-page-header">
        <div className="pod-page-title"><span className="pod-page-title-icon iconfont icon-skin" aria-hidden="true" /><div><span>POD CUSTOMIZATION</span><h1>POD 定制</h1><p>一个产品模板贯穿整批，自动为每款生成商品图。</p></div></div>
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
            <div className="pod-section-title"><h2>业务信息编辑</h2></div>
            <div className="pod-business-fields">
              {BUSINESS_FIELDS.map((field) => <label key={field.key} className={field.multiline ? "is-multiline" : ""}><span>{field.label}{field.required && <em>*</em>}</span>{field.multiline
                ? <textarea rows={1} value={businessFields[field.key]} onChange={(event) => {
                  updateBusinessField(field.key, event.currentTarget.value);
                  autoGrowBusinessTextarea(event.currentTarget);
                }} placeholder={field.placeholder} />
                : <input value={businessFields[field.key]} onChange={(event) => updateBusinessField(field.key, event.target.value)} placeholder={field.placeholder} />}</label>)}
            </div>
            <section className="pod-listing-fields" aria-labelledby="pod-dianxiaomi-listing-title">
              <div className="pod-listing-fields-heading"><h3 id="pod-dianxiaomi-listing-title">店小秘上架信息</h3></div>
              <div className="pod-title-mode" role="radiogroup" aria-label="标题模式">
                <span>标题模式<em>*</em></span>
                <div>
                  <button type="button" role="radio" aria-checked={listingFields.title_mode === "long"} className={listingFields.title_mode === "long" ? "is-active" : ""} onClick={() => updateListingField("title_mode", "long")}><b>长标题</b><small>默认，信息更完整</small></button>
                  <button type="button" role="radio" aria-checked={listingFields.title_mode === "short"} className={listingFields.title_mode === "short" ? "is-active" : ""} onClick={() => updateListingField("title_mode", "short")}><b>短标题</b><small>更精简</small></button>
                </div>
              </div>
              <div className="pod-business-fields">
                {LISTING_FIELDS.map((field) => <label key={field.key}><span>{field.label}<em>*</em></span><input value={listingFields[field.key]} inputMode={field.inputMode} onChange={(event) => updateListingField(field.key, event.target.value)} placeholder={field.placeholder} /></label>)}
              </div>
            </section>
            <div className="pod-advanced-prompt">
              <button type="button" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen((open) => !open)}><span><b>高级：本批次创意编辑</b><small>使用内置创意模板</small></span><i className={`iconfont icon-down ${advancedOpen ? "is-open" : ""}`} /></button>
              {advancedOpen && <div className="pod-advanced-prompt-editor"><textarea value={currentBatchEdit ?? builtInPrompt} onChange={(event) => setCurrentBatchEdit(event.target.value)} aria-label="本批次创意提示词" /><div><span>{currentBatchEdit === null ? "正在使用内置模板" : "已为本批次自定义"}</span><button type="button" onClick={() => setCurrentBatchEdit(null)}>重置为内置模板</button></div></div>}
            </div>
            <div className="pod-volume-inline"><b>生成数量</b><small>每款 1 次 AI 生图</small></div>
            <div className="pod-count-options" role="radiogroup" aria-label="生成数量">
              {POD_BATCH_COUNTS.map((count) => <button key={count} type="button" role="radio" aria-checked={!customCountMode && batchCount === count} className={!customCountMode && batchCount === count ? "is-active" : ""} onClick={() => { setCustomCountMode(false); setBatchCount(count); }}><b>{count}</b><span>款</span></button>)}
              <button type="button" role="radio" aria-checked={customCountMode} className={customCountMode ? "is-active" : ""} onClick={() => { setCustomCountMode(true); setCustomCountInput(String(batchCount)); }}><b>自定义</b></button>
            </div>
            {customCountMode && <label className="pod-custom-count"><span>自定义数量</span><input type="number" min={1} max={200} step={1} value={customCountInput} aria-label="自定义生成数量" onChange={(event) => setCustomCountInput(event.target.value)} /><small>1–200 款</small></label>}
            <button type="button" className="pod-start-button" disabled={busyAction === "create-batch" || !selectedTemplate} onClick={() => void startBatch()}>{busyAction === "create-batch" ? <><span className="iconfont icon-loading" />正在提交</> : <><span className="iconfont icon-rocket" />开始生成 {customCountMode ? customCountInput || "自定义" : batchCount} 款</>}</button>
            <p className="pod-direct-note"><span className="iconfont icon-thunderbolt" />生成后自动拆分并发布，无需中途操作。</p>
          </section>
          <button type="button" className={`pod-history-trigger ${historyOpen ? "is-open" : ""}`} onClick={() => setHistoryOpen((open) => !open)}>定制记录<span>{historyOpen ? "收起" : `${batches.length} 个批次`}</span></button>
          {historyOpen && <PodBatchHistory batches={batches} activeBatchId={activeBatch?.id} loading={loading || busyAction.startsWith("batch:")} onOpen={(batchId) => void openBatch(batchId)} onRefresh={() => void refreshHistory()} />}
        </aside>
        <main className="pod-results-column">
          <section className="pod-current-template-summary">
            <header><div><h2>当前批次模板图</h2></div><button type="button" onClick={() => setTemplateDrawerOpen(true)}>更换模板</button></header>
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
            onRetryFailed={() => void retryFailedStyles()}
            onExportDianxiaomi={() => void exportDianxiaomi()}
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

      <TemplateLibraryDrawer
        open={templateDrawerOpen}
        templates={templates}
        selectedTemplateId={selectedTemplateId}
        busyAction={busyAction}
        onClose={() => setTemplateDrawerOpen(false)}
        onSelect={setSelectedTemplateId}
        onUpload={uploadTemplate}
        onCalibrate={calibrateTemplate}
        onSaveCalibration={saveTemplateCalibration}
      />
    </section>
  );
}

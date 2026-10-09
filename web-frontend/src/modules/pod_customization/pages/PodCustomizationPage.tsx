import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { podCustomizationApi } from "../api/podCustomizationApi";
import { CompositionEditDrawer } from "../components/CompositionEditDrawer";
import { CompositionGenerateDrawer } from "../components/CompositionGenerateDrawer";
import { CompositionManagerDrawer } from "../components/CompositionManagerDrawer";
import { PodBatchGallery } from "../components/PodBatchGallery";
import { PodBatchHistoryDrawer } from "../components/PodBatchHistoryDrawer";
import { PodBriefInput } from "../components/PodBriefInput";
import { PodFailedRetryDialog } from "../components/PodFailedRetryDialog";
import { PodResultLightbox } from "../components/PodResultLightbox";
import { SpecCardDrawer } from "../components/SpecCardDrawer";
import { TemplateLibraryDrawer } from "../components/TemplateLibraryDrawer";
import { PodUnsavedTemplateConfirmDialog } from "../components/PodUnsavedTemplateConfirmDialog";
import { PodListingFieldsEditor, validateListingFields, type SkuField } from "../components/PodListingFieldsEditor";
import {
  POD_BATCH_COUNTS,
  POD_BUSINESS_LIST_ITEM_MAX_LENGTH,
  POD_BUSINESS_LIST_MAX_ITEMS,
  POD_BUSINESS_TEXT_MAX_LENGTH,
  POD_COPY_RESTRICTIONS_MAX_LENGTH,
  EMPTY_SPEC_CARD,
  buildPromptV1,
  buildSpecCardCells,
  businessFieldsForApi,
  canDeletePodBatch,
  isPristineCreativeEdit,
  isPodBatchCount,
  isActiveBatchStatus,
  isActivePodItemStatus,
  isActivePodStyleTitleStatus,
  isSpecCardConfigured,
  groupPodStyleRows,
  resolveCreativePrompt,
  listingFieldsForApi,
  shouldPollPodBatch,
  splitBusinessField,
  specCardSummaryText,
} from "../data/podCustomizationModel";
import { batchRetryCandidates, type PodBatchRetryRequest } from "../data/podBatchRetry";
import { createBriefHistoryItem, mergeBusinessFields, recordBriefHistory } from "../data/podBrief";
import {
  createPodSystemTemplate,
  createEmptyPodCustomizationDraft,
  loadPodCustomizationDraft,
  removePodSystemTemplate,
  resolvePodSystemTemplate,
  savePodCustomizationDraft,
  POD_CUSTOMIZATION_DRAFT_VERSION,
  type PodSystemTemplate,
} from "../data/podCustomizationDraft";
import { usePodAssetUrl } from "../data/usePodAssetUrl";
import { getAuthAccount } from "../../../transport/http/client";
import type {
  PodBatch,
  PodBatchCount,
  PodBatchSummary,
  PodBriefFieldsDraft,
  PodBriefHistoryItem,
  PodBusinessFieldsDraft,
  PodComposition,
  PodListingFieldsDraft,
  PodMiaoshouTemplateKind,
  PodTemplate,
  PodTemplateCalibration,
  SpecCardConfig,
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

// 成功提示（右上角 toast）只在出现后短暂停留，到点自动收起，不做常驻；错误提示仍保留到用户处理。
const NOTICE_AUTO_DISMISS_MS = 6_000;

const BUSINESS_FIELDS: Array<{
  key: keyof PodBusinessFieldsDraft;
  label: string;
  required?: boolean;
  hint?: string;
  placeholder?: string;
  /** 单值字段的整段字符上限（原生 maxLength 硬挡）。
   *  多值字段不设整段上限，改为按「单条 ≤200 / 最多 100 条」校验。 */
  maxLength?: number;
  /** 多值字段：提交前会按分隔符拆成数组。 */
  list?: boolean;
}> = [
  // 只保留需要人工确认的字段：目标市场/目标人群/卖点/主题/元素/配色/禁用元素由「智能填写」自动生成，
  // 不在表单里展示（数据仍保存在 businessFields 中，照常进入 Prompt 与提交载荷）。
  { key: "product_name", label: "产品名称", required: true, maxLength: POD_BUSINESS_TEXT_MAX_LENGTH },
  { key: "product_category", label: "产品品类", required: true, maxLength: POD_BUSINESS_TEXT_MAX_LENGTH },
  {
    key: "copy_restrictions",
    label: "标题/描述限制",
    maxLength: POD_COPY_RESTRICTIONS_MAX_LENGTH,
    placeholder: "谨慎填写：如「标题不要出现刺绣」「明确带上 2D Flat」",
    hint: "选填，建议留空、谨慎填写。填了就请写明确说法，例如「标题不要出现刺绣」「标题和描述都要明确带上 2D Flat」；该限制只作用于 AI 生成的标题与描述，不影响图片，也不会放宽平台的违禁词、品牌、长度等硬性规则",
  },
];

type BusinessFieldLimit = {
  /** 多值字段最长一条的字符数（单值字段恒为 0）。 */
  longest: number;
  /** 是否超过后端字符上限。 */
  over: boolean;
};

// 与后端一致的口径：单值字段按整段长度，多值字段按「最多 100 条、单条 ≤200」。
function businessFieldLimit(
  field: (typeof BUSINESS_FIELDS)[number],
  value: string,
): BusinessFieldLimit {
  if (field.list) {
    const items = splitBusinessField(value);
    const longest = items.reduce((max, item) => Math.max(max, item.length), 0);
    return {
      longest,
      over: items.length > POD_BUSINESS_LIST_MAX_ITEMS || longest > POD_BUSINESS_LIST_ITEM_MAX_LENGTH,
    };
  }
  return { longest: 0, over: value.length > (field.maxLength ?? Number.POSITIVE_INFINITY) };
}

function businessFieldLimitMessage(field: (typeof BUSINESS_FIELDS)[number]): string {
  return field.list
    ? `${field.label}：每条最多 ${POD_BUSINESS_LIST_ITEM_MAX_LENGTH} 个字符、最多 ${POD_BUSINESS_LIST_MAX_ITEMS} 条，请先删减。`
    : `${field.label}最多 ${field.maxLength} 个字符，请先删减。`;
}

function autoGrowBusinessTextarea(textarea: HTMLTextAreaElement): void {
  textarea.style.height = "36px";
  textarea.style.height = `${Math.max(36, textarea.scrollHeight)}px`;
}

function toSummary(batch: PodBatch): PodBatchSummary {
  const { items: _items, template: _template, prompt_version: _promptVersion, business_fields: _fields, listing_fields: _listingFields, dianxiaomi_export: _dianxiaomiExport, creative_prompt: _prompt, ...summary } = batch;
  return summary;
}

function sortBatches(batches: PodBatchSummary[]): PodBatchSummary[] {
  return [...batches].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

const EMPTY_BUSINESS_FIELDS_FOR_SWITCH: Record<keyof PodBusinessFieldsDraft, string> = {
  product_name: "",
  product_category: "",
  target_market: "",
  target_audience: "",
  core_selling_points: "",
  design_theme: "",
  style_keywords: "",
  color_preferences: "",
  excluded_elements: "",
  copy_restrictions: "",
};

const EMPTY_LISTING_FIELDS_FOR_SWITCH: PodListingFieldsDraft = {
  title_mode: "long",
  suggested_price_usd: "",
  category_name: "",
  skus: [{ name: "", declared_price: "", weight_g: "" }],
};

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
  const [selectedBatchIds, setSelectedBatchIds] = useState<string[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState(initialDraft.state.selected_template_id);
  const [selectedTemplateSnapshot, setSelectedTemplateSnapshot] = useState<PodTemplate | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string>();
  const [businessFields, setBusinessFields] = useState<PodBusinessFieldsDraft>(() => ({
    ...initialDraft.state.business_fields,
  }));
  const [briefHistory, setBriefHistory] = useState<PodBriefHistoryItem[]>(initialDraft.state.brief_history ?? []);
  const [listingFields, setListingFields] = useState<PodListingFieldsDraft>(initialDraft.state.listing_fields);
  const [specCard, setSpecCard] = useState<SpecCardConfig>(initialDraft.state.spec_card ?? EMPTY_SPEC_CARD);
  const [batchCount, setBatchCount] = useState<PodBatchCount>(initialDraft.state.batch_count);
  const [customCountMode, setCustomCountMode] = useState(initialDraft.state.custom_count_mode);
  const [customCountInput, setCustomCountInput] = useState(initialDraft.state.custom_count_input);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [currentBatchEdit, setCurrentBatchEdit] = useState<string | null>(initialDraft.state.current_batch_edit);
  const [systemTemplates, setSystemTemplates] = useState<PodSystemTemplate[]>(initialDraft.state.system_templates);
  const [templateDrawerOpen, setTemplateDrawerOpen] = useState(false);
  const [specCardDrawerOpen, setSpecCardDrawerOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [compositionDrawerOpen, setCompositionDrawerOpen] = useState(false);
  const [compositionManagerOpen, setCompositionManagerOpen] = useState(false);
  const [compositionEditOpen, setCompositionEditOpen] = useState(false);
  const [compositionEditTarget, setCompositionEditTarget] = useState<PodComposition | null>(null);
  const [composition, setComposition] = useState<PodComposition | null>(null);
  const [failedRetryOpen, setFailedRetryOpen] = useState(false);
  const [pendingTemplateSwitch, setPendingTemplateSwitch] = useState<string | null>(null);
  const [switchingTemplate, setSwitchingTemplate] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(initialDraft.error ?? "");
  // 上架信息实时校验：值一改就重算，错误在内联槽位即时展示；「必填为空」在提交过一次后才提示。
  const listingFieldErrors = useMemo(() => validateListingFields(listingFields), [listingFields]);
  const [listingErrorsRevealed, setListingErrorsRevealed] = useState(false);
  const [visibility, setVisibility] = useState<DocumentVisibilityState>(() => document.visibilityState);
  const requestGenerationRef = useRef(0);
  const lastDraftSaveErrorRef = useRef("");
  const activeBatchEpochRef = useRef(0);
  const businessTextareasRef = useRef<Array<HTMLTextAreaElement | null>>([]);

  const selectedTemplate = templates.find((template) => template.id === selectedTemplateId);
  const summaryTemplate = selectedTemplateSnapshot ?? selectedTemplate;
  const summaryTemplatePreview = usePodAssetUrl(summaryTemplate?.preview_url || summaryTemplate?.original_url);
  const summaryFields = businessFieldsForApi(businessFields);
  const selectedItem = activeBatch?.items.find((item) => item.id === selectedItemId);
  const failedRetryCandidates = activeBatch ? batchRetryCandidates(groupPodStyleRows(activeBatch)) : { image: [], title: [] };
  const builtInPrompt = useMemo(() => buildPromptV1(businessFields), [businessFields]);
  const resolvedPrompt = resolveCreativePrompt(businessFields, currentBatchEdit ?? "");
  // A frozen custom edit that is just a stored copy of the built-in v1 snapshot
  // must follow business-field edits (sync), not stay stale. Hand-written
  // creative directions (not v1-structured) are left untouched.
  const previousBusinessFieldsRef = useRef(businessFields);
  useEffect(() => {
    const changed = previousBusinessFieldsRef.current !== businessFields;
    previousBusinessFieldsRef.current = businessFields;
    if (changed && currentBatchEdit !== null && isPristineCreativeEdit(currentBatchEdit)) {
      setCurrentBatchEdit(null);
    }
  }, [businessFields, currentBatchEdit]);
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

  // 读取该账号的最新构图（只保留最新一份），用于「新建批次将套用」提示。
  useEffect(() => {
    let stopped = false;
    void (async () => {
      try {
        const latest = await podCustomizationApi.getLatestComposition();
        if (!stopped) setComposition(latest);
      } catch {
        // 构图读取失败不影响主流程，静默忽略；用户打开抽屉时会再拉一次。
      }
    })();
    return () => {
      stopped = true;
    };
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
        // 内容没变就保留旧引用：fresh 永远是新对象，无条件 set 会让整页
        // 每 1.5s 白白重渲染一次（画廊几十款 × 4 图的协调开销很大）。
        const freshJson = JSON.stringify(fresh);
        setActiveBatch((current) => (current && JSON.stringify(current) === freshJson ? current : fresh));
        const summary = toSummary(fresh);
        setBatches((current) => {
          const next = sortBatches([summary, ...current.filter((batch) => batch.id !== summary.id)]);
          const unchanged = current.length === next.length
            && current.every((batch, index) => batch === next[index] || JSON.stringify(batch) === JSON.stringify(next[index]));
          return unchanged ? current : next;
        });
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
      version: POD_CUSTOMIZATION_DRAFT_VERSION,
      business_fields: businessFields,
      listing_fields: listingFields,
      spec_card: specCard,
      batch_count: batchCount,
      custom_count_mode: customCountMode,
      custom_count_input: customCountInput,
      selected_template_id: selectedTemplateId,
      current_batch_edit: currentBatchEdit,
      system_templates: systemTemplates,
      brief_history: briefHistory,
    });
    if (!result.ok && lastDraftSaveErrorRef.current !== result.error) {
      lastDraftSaveErrorRef.current = result.error;
      setError(result.error);
    } else if (result.ok) {
      lastDraftSaveErrorRef.current = "";
    }
  }, [batchCount, briefHistory, businessFields, currentBatchEdit, customCountInput, customCountMode, draftScope?.accountId, draftScope?.workspaceId, listingFields, selectedTemplateId, specCard, systemTemplates]);

  // 尺寸详情表格与 SKU 预设联动：SKU 增减或改名时按顺序重建表头与 SKU 行；
  // 已填的长/宽/高按 SKU 名（退化时按位置）保留，避免重建时清掉用户输入。
  const skuNamesSignature = JSON.stringify(listingFields.skus.map((sku) => sku.name));
  useEffect(() => {
    const names = JSON.parse(skuNamesSignature) as string[];
    setSpecCard((current) => ({ ...current, cells: buildSpecCardCells(names, current.cells) }));
  }, [skuNamesSignature]);

  // Resize multiline business textareas on mount and whenever their values change.
  // onChange handles live typing; this effect handles initial load and draft restore.
  useLayoutEffect(() => {
    businessTextareasRef.current.forEach((textarea) => {
      if (!textarea) return;
      autoGrowBusinessTextarea(textarea);
    });
  }, [businessFields]);

  // 提示生命周期：成功 toast 出现后自动收起，避免一次操作后一直挂在右上角。
  // 依赖 notice 本身：新提示出现会清掉旧计时器重新计时；手动关闭（notice 置空）也会清掉计时器。
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), NOTICE_AUTO_DISMISS_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const clearMessages = () => {
    setNotice("");
    setError("");
  };

  const updateBusinessField = (key: keyof PodBusinessFieldsDraft, value: string) => {
    setBusinessFields((current) => ({ ...current, [key]: value }));
  };

  // 智能填写：生成结果直接覆盖同名字段，并把本次输入记入「最近生成」历史。
  const handleBriefGenerated = (fields: PodBriefFieldsDraft, input: string) => {
    setBusinessFields((current) => mergeBusinessFields(current, fields));
    setBriefHistory((current) => recordBriefHistory(current, createBriefHistoryItem(input, fields)));
  };

  const selectBriefHistory = (item: PodBriefHistoryItem) => {
    setBusinessFields((current) => mergeBusinessFields(current, item.fields));
  };

  const updateListingField = (key: "title_mode" | "suggested_price_usd" | "category_name", value: string) => {
    setListingFields((current) => ({ ...current, [key]: value }));
  };

  const addSku = () => {
    if (skuLimitReached) return;
    setListingFields((current) => ({
      ...current,
      skus: [...current.skus, { name: "", declared_price: "", weight_g: "" }],
    }));
  };

  const updateSku = (index: number, key: SkuField, value: string) => {
    setListingFields((current) => ({
      ...current,
      skus: current.skus.map((sku, currentIndex) => currentIndex === index ? { ...sku, [key]: value } : sku),
    }));
  };

  const removeSku = (index: number) => {
    setListingFields((current) => ({ ...current, skus: current.skus.filter((_, currentIndex) => currentIndex !== index) }));
  };

  const selectTemplate = (templateId: string) => {
    if (templateId === selectedTemplateId) {
      setSelectedTemplateSnapshot(null);
      return;
    }
    const currentTemplateHasArchive = systemTemplates.some((template) => template.templateId === selectedTemplateId);
    const formHasContent = Object.values(businessFields).some((value) => value.trim())
      || listingFields.suggested_price_usd.trim()
      || listingFields.category_name.trim() || listingFields.skus.some((sku) => Object.values(sku).some((value) => value.trim()));
    if (!currentTemplateHasArchive && selectedTemplateId && formHasContent) {
      setPendingTemplateSwitch(templateId);
      return;
    }
    applyTemplateSwitch(templateId);
  };

  const applyTemplateSwitch = (templateId: string) => {
    setSelectedTemplateId(templateId);
    setSelectedTemplateSnapshot(null);
    setCurrentBatchEdit(null);
    setBusinessFields({ ...EMPTY_BUSINESS_FIELDS_FOR_SWITCH });
    setBriefHistory([]);
    setListingFields({ ...EMPTY_LISTING_FIELDS_FOR_SWITCH });
    setListingErrorsRevealed(false);
    setAdvancedOpen(false);
  };

  const confirmTemplateSwitch = () => {
    const templateId = pendingTemplateSwitch;
    if (!templateId) return;
    setSwitchingTemplate(true);
    applyTemplateSwitch(templateId);
    setSwitchingTemplate(false);
    setPendingTemplateSwitch(null);
    setTemplateDrawerOpen(false);
    setNotice(`已切换到新模板，表单预设已清空。`);
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
      const next = sortBatches(response.batches);
      setBatches(next);
      setSelectedBatchIds((current) => current.filter((id) => next.some((batch) => batch.id === id && canDeletePodBatch(batch.status))));
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
      activeBatchEpochRef.current += 1;
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
    // 前端硬挡不到的情况（智能填写回填、草稿恢复）在此拦截，避免超限被后端 422 拒绝。
    const overLimitField = BUSINESS_FIELDS.find((field) => businessFieldLimit(field, businessFields[field.key]).over);
    if (overLimitField) {
      setError(businessFieldLimitMessage(overLimitField));
      return;
    }
    const listingFieldsResult = listingFieldsForApi(listingFields, specCard);
    // 实时校验结果此刻已是最新：这里只需揭示「必填为空」并拒绝提交。
    setListingErrorsRevealed(true);
    if (Object.keys(listingFieldErrors).length) {
      setError("请检查上架信息中标红的字段。");
      return;
    }
    if (!listingFieldsResult.value) {
      setError(listingFieldsResult.error ?? "请完整填写店小秘上架信息。" );
      return;
    }
    if (!isSpecCardConfigured(specCard)) {
      setError("请先点击「批量添加尺寸」完成表格配置。");
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
      activeBatchEpochRef.current += 1;
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
    const epoch = activeBatchEpochRef.current;
    try {
      const fresh = await podCustomizationApi.getBatch(batchId);
      if (epoch !== activeBatchEpochRef.current) return;
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

    } catch (cause) {
      setActiveBatch((current) => current ? {
        ...current,
        dianxiaomi_export: previousExportStatus,
        style_titles: current.style_titles?.map((title) => title.style_index === styleIndex ? { ...title, export_selected: previousSelected } : title),
      } : current);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  /** 一键反选：把全部已就绪款式整体设为选中/取消选中，不用逐个点。 */
  const setAllExportSelection = async (selected: boolean) => {
    if (!activeBatch) return;
    const readyTitles = (activeBatch.style_titles ?? []).filter((title) => title.listing_ready);
    const changed = readyTitles.filter((title) => title.export_selected !== selected);
    if (!changed.length) return;
    clearMessages();
    const previousBatch = activeBatch;
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
      setActiveBatch(previousBatch);
      setError(cause instanceof Error ? cause.message : String(cause));
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
      await refreshActiveBatch(activeBatch.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  };

  const selectedDeletableIds = selectedBatchIds.filter((id) => batches.some((batch) => batch.id === id && canDeletePodBatch(batch.status)));

  const toggleSelectBatch = (batchId: string) => {
    setSelectedBatchIds((current) => current.includes(batchId)
      ? current.filter((id) => id !== batchId)
      : [...current, batchId]);
  };

  const deleteBatches = async (ids: string[]) => {
    if (!ids.length) return;
    if (!window.confirm(`确认删除选中的 ${ids.length} 个批次？删除后这些批次的本地图片将被清理，不可恢复。`)) return;
    setBusyAction("delete-batch");
    clearMessages();
    const deleted: string[] = [];
    let failed = false;
    try {
      for (const batchId of ids) {
        await podCustomizationApi.deleteBatch(batchId);
        deleted.push(batchId);
      }
    } catch (cause) {
      failed = true;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBatches((current) => current.filter((batch) => !deleted.includes(batch.id)));
      if (activeBatch && deleted.includes(activeBatch.id)) {
        setActiveBatch(null);
        setSelectedItemId(undefined);
      }
      setSelectedBatchIds((current) => current.filter((id) => !deleted.includes(id)));
      if (!failed && deleted.length) setNotice(`${deleted.length} 个批次已删除。`);
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

  const exportMiaoshou = async (kind: PodMiaoshouTemplateKind) => {
    if (!activeBatch) return;
    setBusyAction(`export-miaoshou:${kind}`);
    clearMessages();
    try {
      const exported = await podCustomizationApi.exportMiaoshou(activeBatch.id, kind);
      const label = kind === "apparel" ? "服饰类" : "非服饰类";
      setNotice(`已导出妙手${label}表格：${exported.exportedStyles} 款、跳过 ${exported.skippedStyles} 款。`);
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
        <div className="pod-page-title"><span className="pod-page-title-icon iconfont icon-skin" aria-hidden="true" /><div><span>POD CUSTOMIZATION · DIRECT LISTING</span><h1>POD 全定制</h1></div></div>
        <div className="pod-page-header-actions">
          {batchRunning && <span className="pod-live-badge"><i />批次后台运行中</span>}
          <button type="button" onClick={() => setTemplateDrawerOpen(true)}><span className="iconfont icon-upload" />上传当前批次模板</button>
          <button type="button" onClick={() => setTemplateDrawerOpen(true)}><span className="iconfont icon-appstore" />查看历史批次模板</button>
          <button type="button" onClick={() => setHistoryOpen(true)}><span className="iconfont icon-time-circle" />查看定制记录历史</button>
          <button type="button" onClick={() => setCompositionManagerOpen(true)}><span className="iconfont icon-skin" />构图模板管理</button>
        </div>
      </header>

      {/* portal 到 body：tab 面板 fill-mode 动画的层叠上下文会锁住 fixed toast 的 z-index，被顶栏盖住 */}
      {error && createPortal(<div className="pod-page-message is-error" role="alert"><span>!</span><p>{error}</p><button type="button" onClick={clearMessages} aria-label="关闭提示">×</button></div>, document.body)}

      {!error && notice && <div className="pod-page-message" role="status"><span>✓</span><p>{notice}</p><button type="button" onClick={clearMessages} aria-label="关闭提示">×</button></div>}

      <div className="pod-workbench-grid">
        <aside className="pod-setup-column pod-brief-sidebar">
          <section className="pod-setup-card pod-business-editor">
            <div className="pod-section-title"><span>BRIEF EDITOR</span><h2>创意描述</h2></div>
            <PodBriefInput onGenerated={handleBriefGenerated} history={briefHistory} onSelectHistory={selectBriefHistory} />
            <div className="pod-business-fields">
              {BUSINESS_FIELDS.map((field, fieldIndex) => {
                const value = businessFields[field.key];
                const limit = businessFieldLimit(field, value);
                return (
                  <label key={field.key} className={limit.over ? "is-over-limit" : undefined}>
                    <span>{field.label}{field.required && <em>*</em>}{field.hint && <i className="pod-field-info" data-tip={field.hint} aria-hidden="true">ⓘ</i>}</span>
                    <textarea rows={1} placeholder={field.placeholder} maxLength={field.maxLength} aria-invalid={limit.over || undefined} ref={(el) => { businessTextareasRef.current[fieldIndex] = el; }} value={value} onChange={(event) => {
                      updateBusinessField(field.key, event.currentTarget.value);
                      autoGrowBusinessTextarea(event.currentTarget);
                    }} />
                    <small className={limit.over ? "pod-field-counter is-over" : "pod-field-counter"}>{field.list ? `${limit.longest}/${POD_BUSINESS_LIST_ITEM_MAX_LENGTH}` : `${value.length}/${field.maxLength}`}</small>
                  </label>
                );
              })}
            </div>
            <div className="pod-advanced-prompt">
              <button type="button" aria-expanded={advancedOpen} onClick={() => setAdvancedOpen((open) => !open)}><span><b>高级：本批次创意编辑</b><small>内置 POD Direct Listing Prompt v1</small></span><i className={`iconfont icon-down ${advancedOpen ? "is-open" : ""}`} /></button>
              {advancedOpen && <div className="pod-advanced-prompt-editor"><textarea value={currentBatchEdit ?? builtInPrompt} onChange={(event) => setCurrentBatchEdit(event.target.value)} aria-label="本批次创意提示词" /><div><span>{currentBatchEdit === null ? "正在使用内置 v1" : "已为本批次自定义"}</span><button type="button" onClick={() => setCurrentBatchEdit(null)}>重置为 v1</button></div></div>}
            </div>
            <div className="pod-composition-entry">
              <button type="button" className="pod-composition-entry-button" onClick={() => setCompositionDrawerOpen(true)}>
                <span className="iconfont icon-skin" aria-hidden="true" />
                构图定制
              </button>
              <span className={composition ? "pod-composition-entry-summary" : "pod-composition-entry-summary is-empty"}>
                {composition
                  ? (composition.is_builtin ? "系统默认模板（默认机位）" : "已配置最新构图，新建批次将自动套用")
                  : "未配置：使用系统默认模板"}
              </span>
            </div>
            <PodListingFieldsEditor
              listingFields={listingFields}
              specCard={specCard}
              skuFieldErrors={listingFieldErrors}
              skuLimitReached={skuLimitReached}
              showRequiredErrors={listingErrorsRevealed}
              onListingFieldChange={updateListingField}
              onAddSku={addSku}
              onUpdateSku={updateSku}
              onRemoveSku={removeSku}
              onOpenSpecCardDrawer={() => setSpecCardDrawerOpen(true)}
            />
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
                <div><dt>统一风格</dt><dd>{summaryFields.design_theme || "未填写"}</dd></div>
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
            onSetAllExportSelection={(selected) => void setAllExportSelection(selected)}
            onSaveTitle={(styleIndex, title) => saveManualTitle(styleIndex, title)}
            onExportDianxiaomi={() => void exportDianxiaomi()}
            onExportMiaoshou={(kind) => void exportMiaoshou(kind)}
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

      <PodBatchHistoryDrawer
        open={historyOpen}
        batches={batches}
        activeBatchId={activeBatch?.id}
        loading={loading || busyAction.startsWith("batch:")}
        busyAction={busyAction}
        selectedIds={selectedDeletableIds}
        onToggleSelect={toggleSelectBatch}
        onDeleteSelected={() => void deleteBatches(selectedDeletableIds)}
        onOpen={(batchId) => { void openBatch(batchId); setHistoryOpen(false); }}
        onRefresh={() => void refreshHistory()}
        onClose={() => setHistoryOpen(false)}
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

      <PodUnsavedTemplateConfirmDialog
        open={Boolean(pendingTemplateSwitch)}
        templateName={selectedTemplate?.name ?? ""}
        busy={switchingTemplate}
        onClose={() => setPendingTemplateSwitch(null)}
        onConfirm={confirmTemplateSwitch}
      />

      <SpecCardDrawer
        open={specCardDrawerOpen}
        config={specCard}
        batch={activeBatch}
        baseTemplateId={selectedTemplate?.id}
        onClose={() => setSpecCardDrawerOpen(false)}
        onSave={(next) => {
          setSpecCard(next);
          setNotice(`规格卡配置已保存：${specCardSummaryText(next)}。`);
        }}
        onReprinted={(batchId, result) => {
          setNotice(result.failed > 0
            ? `全批重印完成：成功 ${result.reprinted} 款、失败 ${result.failed} 款，该批次需重新导出。`
            : `全批重印完成：${result.reprinted} 款已更新，该批次需重新导出。`);
          void refreshActiveBatch(batchId);
        }}
      />

      <CompositionGenerateDrawer
        open={compositionDrawerOpen}
        onClose={() => setCompositionDrawerOpen(false)}
        onChanged={(next: PodComposition | null) => {
          setComposition(next);
          if (next) setNotice("已生成新模板并设为生效，新建批次将自动套用。");
        }}
      />

      <CompositionEditDrawer
        open={compositionEditOpen}
        target={compositionEditTarget}
        onClose={() => setCompositionEditOpen(false)}
        onChanged={(saved: PodComposition) => {
          setComposition(saved);
          setNotice("构图已保存，新建批次将自动套用。");
        }}
      />

      <CompositionManagerDrawer
        open={compositionManagerOpen}
        onClose={() => setCompositionManagerOpen(false)}
        onEdit={(template) => {
          setCompositionManagerOpen(false);
          setCompositionEditTarget(template);
          setCompositionEditOpen(true);
        }}
        onActiveChanged={(active) => {
          setComposition(active);
          if (!active) setNotice("已无生效模板，四张图将使用系统默认模板。");
          else setNotice(active.is_builtin ? "当前使用系统默认模板（默认机位）。" : `当前生效模板：${active.name.trim() || "未命名构图"}。`);
        }}
      />
    </section>
  );
}

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";

import { getAuthAccount } from "../../../transport/http/client";
import { PodAssetImage } from "../../pod_customization/data/usePodAssetUrl";
import { PodListingFieldsEditor, validateListingFields, type SkuField } from "../../pod_customization/components/PodListingFieldsEditor";
import { SpecCardDrawer } from "../../pod_customization/components/SpecCardDrawer";
import {
  buildSpecCardCells,
  createEmptySpecCard,
  isSpecCardConfigured,
  listingFieldsForApi,
  specCardSummaryText,
} from "../../pod_customization/data/podCustomizationModel";
import {
  formatPodModulePrice,
  usePodModulePricing,
} from "../../pod_customization/data/usePodModulePricing";
import type { PodListingFieldsDraft, SpecCardConfig } from "../../pod_customization/types/index";
import { podFreeCustomizationApi, downloadBlobAs } from "../api/podFreeCustomizationApi";
import {
  createEmptyFreeDraft,
  loadFreeDraft,
  saveFreeDraft,
  type FreeDraft,
} from "../data/podFreeCustomizationDraft";
import type { FreeBatch, FreeBatchSummary, FreeImageModel } from "../types";
import "../../pod_customization/styles/podCustomization.css";
import "../styles/podFreeCustomization.css";

type Props = { isActive?: boolean };

const FLASH: FreeImageModel = "doubao-seedream-5-0-flash-260915";
const PRO: FreeImageModel = "doubao-seedream-5-0-pro-260628";
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "partial_failure",
  "failed",
  "cancelled",
]);
const RUNNING: ReadonlySet<string> = new Set([
  "queued",
  "generating_patterns",
  "compositing",
  "generating_titles",
  "pausing",
  "cancelling",
]);
const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  generating_patterns: "生成中",
  compositing: "处理中",
  generating_titles: "生成标题中",
  pausing: "暂停中",
  paused: "已暂停",
  cancelling: "取消中",
  completed: "已完成",
  partial_failure: "部分完成",
  failed: "失败",
  cancelled: "已取消",
};

const MODEL_INTRO: Record<FreeImageModel, string> = {
  [FLASH]: "当前服务由 Seedream 5.0 Flash 提供，平衡质量与成本",
  [PRO]: "当前服务由 Seedream 5.0 Pro 提供，图片质量更高级，出图更专业，细节部分更完美",
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function slotTitle(variant: number): string {
  return variant === 1 ? "主图" : `第 ${variant} 张`;
}

function itemImagePath(item: { pattern_preview_url?: string; composite_preview_url?: string; public_url?: string } | undefined): string {
  // 尺寸卡会烘进成品图：优先展示 composite，再回退干净母版/公网地址。
  return item?.composite_preview_url || item?.pattern_preview_url || item?.public_url || "";
}

/** 把批次里的上架快照还原成编辑器状态；规格卡缺失时按 SKU 重建空表。 */
function parseListingFields(batch: FreeBatch | null): {
  fields: PodListingFieldsDraft;
  specCard: SpecCardConfig;
} {
  const listing = batch?.listing_fields;
  if (!listing || !Array.isArray(listing.skus) || listing.skus.length === 0) {
    return {
      fields: { title_mode: "long", category_name: "", skus: [{ name: "", declared_price: "", weight_g: "" }] },
      specCard: createEmptySpecCard(),
    };
  }
  const fields: PodListingFieldsDraft = {
    title_mode: listing.title_mode ?? "long",
    category_name: listing.category_name,
    skus: listing.skus.map((sku) => ({
      name: sku.name,
      declared_price: String(sku.declared_price),
      weight_g: String(sku.weight_g),
    })),
  };
  const specCard: SpecCardConfig = listing.spec_card
    ? { ...listing.spec_card, cells: listing.spec_card.cells.map((row) => [...row]) }
    : { ...createEmptySpecCard(), cells: buildSpecCardCells(fields.skus.map((sku) => sku.name)) };
  return { fields, specCard };
}

export function PodFreeCustomizationPage({ isActive = true }: Props) {
  const draftScope = useMemo(() => {
    const account = getAuthAccount<{
      account_id?: string;
      customer_id?: string;
      workspace_id?: string;
      workspace_code?: string;
    }>();
    const accountId = (account?.account_id || account?.customer_id || "").trim();
    const workspaceId = (account?.workspace_id || account?.workspace_code || "").trim();
    return accountId && workspaceId ? { accountId, workspaceId } : null;
  }, []);

  const [draft, setDraft] = useState<FreeDraft>(() =>
    draftScope ? loadFreeDraft(draftScope.accountId, draftScope.workspaceId) : createEmptyFreeDraft(),
  );
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [batch, setBatch] = useState<FreeBatch | null>(null);
  const [batches, setBatches] = useState<FreeBatchSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  const [targetCount, setTargetCount] = useState(4);
  const [activeVariant, setActiveVariant] = useState(1);
  const [activePrompt, setActivePrompt] = useState("");
  const [advanceFrom, setAdvanceFrom] = useState<number | null>(null);

  const [titleDraft, setTitleDraft] = useState("");
  const [listingFields, setListingFields] = useState<PodListingFieldsDraft>({
    title_mode: "long",
    category_name: "",
    skus: [{ name: "", declared_price: "", weight_g: "" }],
  });
  const [specCard, setSpecCard] = useState<SpecCardConfig>(() => createEmptySpecCard());
  const [listingErrorsRevealed, setListingErrorsRevealed] = useState(false);
  const [specCardDrawerOpen, setSpecCardDrawerOpen] = useState(false);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [remakeMainOpen, setRemakeMainOpen] = useState(false);
  // 长按 2 秒进入拖动排序：ghost 是跟随鼠标的浮动卡片，dropVariant 是当前指向的槽位。
  const [dragGhost, setDragGhost] = useState<{
    variant: number;
    preview: string;
    x: number;
    y: number;
    width: number;
    height: number;
  } | null>(null);
  const [dropVariant, setDropVariant] = useState<number | null>(null);
  const pressRef = useRef<{ variant: number; preview: string; x: number; y: number } | null>(null);
  const dropVariantRef = useRef<number | null>(null);
  const longPressTimerRef = useRef<number | null>(null);
  const longPressFiredRef = useRef(false);

  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const exportMenuRef = useRef<HTMLDivElement | null>(null);
  // 最近一次成功保存的上架信息快照：用于自动保存去重与「已自动保存」提示。
  const savedListingSignatureRef = useRef("");
  const flashPrice = formatPodModulePrice(usePodModulePricing("pod_free_flash_v1"));
  const proPrice = formatPodModulePrice(usePodModulePricing("pod_free_pro_v1"));
  const activePrice = draft.image_model === PRO ? proPrice : flashPrice;

  const slotCount = batch ? Math.max(1, batch.target_image_count || 1) : targetCount;

  // 数量切换时让增/删的槽位走过场动画，避免右侧凭空多一块、少一块。
  const previousSlotCountRef = useRef(slotCount);
  const [visibleSlotCount, setVisibleSlotCount] = useState(slotCount);
  const [enteringVariants, setEnteringVariants] = useState<number[]>([]);
  const [leavingVariants, setLeavingVariants] = useState<number[]>([]);
  useEffect(() => {
    const previous = previousSlotCountRef.current;
    if (slotCount === previous) return;
    previousSlotCountRef.current = slotCount;
    setLeavingVariants([]);
    let timer: number | undefined;
    if (slotCount > previous) {
      setEnteringVariants(
        Array.from({ length: slotCount - previous }, (_, index) => previous + index + 1),
      );
      setVisibleSlotCount(slotCount);
      timer = window.setTimeout(() => setEnteringVariants([]), 320);
    } else {
      setLeavingVariants(
        Array.from({ length: previous - slotCount }, (_, index) => slotCount + index + 1),
      );
      timer = window.setTimeout(() => {
        setVisibleSlotCount(slotCount);
        setLeavingVariants([]);
      }, 200);
    }
    return () => {
      if (timer) window.clearTimeout(timer);
    };
  }, [slotCount]);

  const itemsByVariant = useMemo(() => {
    const map = new Map<number, FreeBatch["items"][number]>();
    for (const item of batch?.items ?? []) map.set(Number(item.variant_index), item);
    return map;
  }, [batch]);
  const activeItem = itemsByVariant.get(activeVariant);
  const mainReady = itemsByVariant.get(1)?.status === "completed";
  const allGenerated =
    slotCount > 0 &&
    Array.from({ length: slotCount }, (_, index) => index + 1).every(
      (variant) => itemsByVariant.get(variant)?.status === "completed",
    );
  // 第 2 张起是否已有出好的图：有的话重做主图要提示会一并清除。
  const downstreamGenerated =
    slotCount > 1 &&
    Array.from({ length: slotCount - 1 }, (_, index) => index + 2).some(
      (variant) => itemsByVariant.get(variant)?.status === "completed",
    );
  const activeCompleted = activeItem?.status === "completed";
  const activeFailed = activeItem?.status === "failed";
  const running = Boolean(batch && RUNNING.has(batch.status));

  useEffect(() => {
    if (!draftScope) return;
    saveFreeDraft(draftScope.accountId, draftScope.workspaceId, draft);
  }, [draft, draftScope]);

  useEffect(() => {
    if (!isActive) return;
    void loadBatches();
  }, [isActive]);

  useEffect(() => {
    if (!batch || TERMINAL.has(batch.status)) return;
    const timer = window.setInterval(() => void refreshBatch(batch.id, false), 2_000);
    return () => window.clearInterval(timer);
  }, [batch?.id, batch?.status]);

  // 切批次时把当前槽位和提示词对齐到主图。
  useEffect(() => {
    if (!batch) return;
    setActiveVariant(1);
    setActivePrompt(itemsByVariant.get(1)?.prompt || draft.prompt || "");
    const parsed = parseListingFields(batch);
    setListingFields(parsed.fields);
    setSpecCard(parsed.specCard);
    // 批次里的快照即已保存内容，避免刚切批次就触发一次自动保存。
    savedListingSignatureRef.current = JSON.stringify({
      listingFields: parsed.fields,
      specCard: parsed.specCard,
    });
  }, [batch?.id]);

  useEffect(() => {
    const generated = batch?.style_titles?.[0]?.title;
    if (generated) setTitleDraft(generated);
  }, [batch?.style_titles?.[0]?.title]);

  // 导出下拉：点空白处或按 Esc 收起。
  useEffect(() => {
    if (!exportMenuOpen) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (!exportMenuRef.current?.contains(event.target as Node)) setExportMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExportMenuOpen(false);
    };
    window.addEventListener("mousedown", closeOnOutside);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("mousedown", closeOnOutside);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [exportMenuOpen]);

  // SKU 增删改后同步尺寸表格行，已填的长/宽/高按 SKU 名称保留。
  const skuNamesSignature = JSON.stringify(listingFields.skus.map((sku) => sku.name));
  useEffect(() => {
    const names = JSON.parse(skuNamesSignature) as string[];
    setSpecCard((current) => ({ ...current, cells: buildSpecCardCells(names, current.cells) }));
  }, [skuNamesSignature]);

  // 尺寸卡印图目标默认落在最后一张；切换数量后越界自动收敛。
  useEffect(() => {
    setSpecCard((current) => {
      const target = Number(current.target_variant || 0);
      if (target >= 1 && target <= slotCount) return current;
      return { ...current, target_variant: slotCount };
    });
  }, [slotCount]);

  const listingFieldErrors = useMemo(() => validateListingFields(listingFields), [listingFields]);
  const skuLimitReached = listingFields.skus.length >= 100;
  const listingSignature = JSON.stringify({ listingFields, specCard });

  const updateListingField = (key: "title_mode" | "category_name", value: string) => {
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

  // 当前槽位生成完成后自动高亮下一张待生成槽位。
  useEffect(() => {
    if (!batch || advanceFrom === null) return;
    const item = itemsByVariant.get(advanceFrom);
    if (!item) return;
    if (item.status === "failed") {
      setAdvanceFrom(null);
      return;
    }
    if (item.status !== "completed") return;
    const next = Array.from({ length: slotCount }, (_, index) => index + 1).find(
      (variant) => itemsByVariant.get(variant)?.status !== "completed",
    );
    if (next) {
      setActiveVariant(next);
      setActivePrompt(itemsByVariant.get(next)?.prompt || "");
    }
    setAdvanceFrom(null);
  }, [advanceFrom, batch?.updated_at, itemsByVariant, slotCount]);

  const loadBatches = async () => {
    try {
      const response = await podFreeCustomizationApi.listBatches(30, 0);
      setBatches(response.batches);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const refreshBatch = async (batchId: string, surfaceError = true) => {
    try {
      const next = await podFreeCustomizationApi.getBatch(batchId);
      setBatch(next);
      if (TERMINAL.has(next.status)) void loadBatches();
    } catch (cause) {
      if (surfaceError) setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const showNotice = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 4_000);
  };

  const setField = <K extends keyof FreeDraft>(key: K, value: FreeDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const pickReferences = async (files: FileList | null) => {
    if (!files || !files.length || batch) return;
    const remaining = Math.max(0, 4 - draft.reference_asset_ids.length);
    const selected = Array.from(files).slice(0, remaining);
    if (!selected.length) return;
    setUploading(true);
    setError("");
    try {
      const uploaded: Array<{ id: string; preview: string }> = [];
      for (const file of selected) {
        const result = await podFreeCustomizationApi.uploadReference(file);
        uploaded.push({ id: result.asset_id, preview: result.preview_url });
      }
      setDraft((current) => ({
        ...current,
        reference_asset_ids: [...current.reference_asset_ids, ...uploaded.map((row) => row.id)],
        reference_previews: {
          ...current.reference_previews,
          ...Object.fromEntries(uploaded.map((row) => [row.id, row.preview])),
        },
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const removeReference = (assetId: string) => {
    if (batch) return;
    setDraft((current) => {
      const nextPreviews = { ...current.reference_previews };
      delete nextPreviews[assetId];
      return {
        ...current,
        reference_asset_ids: current.reference_asset_ids.filter((id) => id !== assetId),
        reference_previews: nextPreviews,
      };
    });
  };

  const createBatch = async () => {
    if (!draft.prompt.trim()) {
      setError("请先填写主图提示词");
      return;
    }
    setBusy("generate");
    setError("");
    try {
      const created = await podFreeCustomizationApi.createBatch({
        client_request_id: crypto.randomUUID(),
        image_model: draft.image_model,
        target_image_count: targetCount,
        prompt: draft.prompt,
        reference_asset_ids: draft.reference_asset_ids,
        title_enabled: draft.title_enabled,
        business_fields: {
          product_name: draft.product_name,
          product_category: draft.product_category,
          target_market: draft.target_market,
          target_audience: draft.target_audience,
          core_selling_points: draft.selling_points
            .split(/[,，\n]/)
            .map((value) => value.trim())
            .filter(Boolean),
          design_theme: "",
          fabric_craft: "",
          scene_mood: "",
          style_keywords: [],
          color_preferences: [],
          accent_colors: [],
          excluded_elements: [],
          copy_restrictions: draft.copy_restrictions,
        },
        title: draft.batch_title,
      });
      setBatch(created);
      // 建批前配好的尺寸卡要落到批次快照里，否则首张出图时还读不到目标槽位。
      if (isSpecCardConfigured(specCard)) {
        try {
          setBatch(await podFreeCustomizationApi.updateSpecCard(created.id, specCard));
        } catch (cause) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      }
      setActiveVariant(1);
      setActivePrompt(draft.prompt);
      setAdvanceFrom(1);
      void loadBatches();
      showNotice("已提交，正在生成主图");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const generateActive = async () => {
    if (!batch) {
      await createBatch();
      return;
    }
    // 重做主图会清空后续已生成的衍生图：先弹强制确认，避免误删。
    if (activeVariant === 1 && downstreamGenerated) {
      setRemakeMainOpen(true);
      return;
    }
    await runGenerate();
  };

  const runGenerate = async () => {
    if (!batch) return;
    if (activeVariant > 1 && !mainReady) {
      setError("请先生成主图，后续图固定以主图为主要参考");
      return;
    }
    if (!activePrompt.trim()) {
      setError("请先填写当前图片的提示词");
      return;
    }
    const wasCompleted = activeCompleted;
    setBusy("generate");
    setError("");
    try {
      const next = await podFreeCustomizationApi.generatePanel(
        batch.id,
        activeVariant,
        activePrompt,
      );
      setBatch(next);
      // 重生成留在当前槽位；首次生成才自动高亮下一张。
      setAdvanceFrom(wasCompleted ? null : activeVariant);
      showNotice(`已提交，正在生成${slotTitle(activeVariant)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const clearSlotLongPress = () => {
    if (longPressTimerRef.current !== null) {
      window.clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
    }
  };

  const beginSlotPress = (variant: number, preview: string, event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!batch || !preview || running || busy === "generate" || busy === "reorder") return;
    longPressFiredRef.current = false;
    clearSlotLongPress();
    pressRef.current = { variant, preview, x: event.clientX, y: event.clientY };
    const rect = event.currentTarget.getBoundingClientRect();
    longPressTimerRef.current = window.setTimeout(() => {
      const press = pressRef.current;
      if (!press) return;
      longPressFiredRef.current = true;
      setDragGhost({
        variant: press.variant,
        preview: press.preview,
        x: press.x,
        y: press.y,
        width: rect.width,
        height: rect.height,
      });
      dropVariantRef.current = press.variant;
      setDropVariant(press.variant);
    }, 2000);
  };

  /** 长按计时期间移动超过 8px 视为滚动/误触，取消拖动准备。 */
  const trackSlotPress = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const press = pressRef.current;
    if (!press || dragGhost) return;
    if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 8) clearSlotLongPress();
  };

  // 拖动中：ghost 跟随指针，并实时找出指针下方的槽位。
  const draggingSlot = dragGhost !== null;
  useEffect(() => {
    if (!draggingSlot) return;
    const handleMove = (event: PointerEvent) => {
      setDragGhost((current) => (current ? { ...current, x: event.clientX, y: event.clientY } : current));
      const node = document.elementFromPoint(event.clientX, event.clientY);
      const target = node instanceof Element ? node.closest<HTMLElement>("[data-free-variant]") : null;
      const next = Number(target?.dataset.freeVariant || 0);
      const resolved = next >= 1 ? next : null;
      dropVariantRef.current = resolved;
      setDropVariant(resolved);
    };
    const finish = () => {
      const from = dragGhost ? dragGhost.variant : null;
      const to = dropVariantRef.current;
      setDragGhost(null);
      setDropVariant(null);
      dropVariantRef.current = null;
      // 松手仍在原槽位不调接口；这次 click 也会被 longPressFired 拦掉，不触发选中切换。
      if (from && to && to !== from) void reorderSlots(from, to);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
    };
  }, [draggingSlot]);

  const reorderSlots = async (from: number, to: number) => {
    if (!batch) return;
    setBusy("reorder");
    setError("");
    try {
      const next = await podFreeCustomizationApi.reorderPanels(batch.id, from, to);
      setBatch(next);
      setActiveVariant(to);
      setActivePrompt(
        next.items.find((item) => Number(item.variant_index) === to)?.prompt || "",
      );
      showNotice(
        isSpecCardConfigured(specCard)
          ? "图片顺序已调整；尺寸卡点「重印本批次」后按新顺序生效"
          : "图片顺序已调整",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const selectSlot = (variant: number) => {
    if (!batch || running || busy === "generate") return;
    // 主图未出图前不允许切到后续槽位：后续图固定以主图为主要参考。
    if (variant > 1 && !mainReady) return;
    setActiveVariant(variant);
    setAdvanceFrom(null);
    setActivePrompt(itemsByVariant.get(variant)?.prompt || "");
  };

  const cancelBatch = async () => {
    if (!batch) return;
    setBusy("cancel");
    try {
      setBatch(await podFreeCustomizationApi.cancelBatch(batch.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const deleteBatch = async (batchId: string) => {
    if (!window.confirm("删除该批次及其本地图片？")) return;
    setBusy("delete");
    try {
      await podFreeCustomizationApi.deleteBatch(batchId);
      if (batch?.id === batchId) {
        setBatch(null);
        setActiveVariant(1);
        setActivePrompt(draft.prompt);
        setAdvanceFrom(null);
      }
      void loadBatches();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const saveTitle = async () => {
    if (!batch || !titleDraft.trim()) return;
    setBusy("title");
    try {
      await podFreeCustomizationApi.updateTitle(batch.id, titleDraft.trim());
      await refreshBatch(batch.id);
      showNotice("标题已保存");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const reprintSpecCard = async () => {
    if (!batch) return;
    setBusy("spec-reprint");
    setError("");
    try {
      setBatch(await podFreeCustomizationApi.reprintSpecCard(batch.id));
      showNotice("尺寸卡已按当前顺序重印");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const saveSpecCard = async (next: SpecCardConfig) => {
    setSpecCard(next);
    if (!batch) return;
    setBusy("spec-card");
    setError("");
    try {
      // 后端会把目标槽位重印/还原后返回最新批次，右侧图片随之刷新。
      setBatch(await podFreeCustomizationApi.updateSpecCard(batch.id, next));
      showNotice("尺寸卡已保存");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const regenerateTitle = async () => {
    if (!batch) return;
    setBusy("title-regen");
    try {
      await podFreeCustomizationApi.regenerateTitle(batch.id);
      showNotice("已提交标题重生");
      window.setTimeout(() => void refreshBatch(batch.id, false), 1_500);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  /** 上架信息落库：填写完整时静默自动保存，导出前也会强制刷一次。 */
  const persistListingFields = async (silent = false): Promise<FreeBatch | null> => {
    if (!batch) return null;
    const result = listingFieldsForApi(listingFields, specCard);
    if (!result.value || !isSpecCardConfigured(specCard)) {
      if (!silent) {
        setListingErrorsRevealed(true);
        setError(result.error ?? "请先在「批量添加尺寸」里补全每个 SKU 的长/宽/高");
      }
      return null;
    }
    try {
      const next = await podFreeCustomizationApi.updateListingFields(batch.id, result.value);
      setBatch(next);
      savedListingSignatureRef.current = listingSignature;
      if (!silent) showNotice("导出信息已保存");
      return next;
    } catch (cause) {
      if (!silent) setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    }
  };

  // 填写完整后自动保存：停止输入 0.9s 落库，避免还要用户手动点保存。
  useEffect(() => {
    if (!batch) return;
    if (savedListingSignatureRef.current === listingSignature) return;
    const result = listingFieldsForApi(listingFields, specCard);
    if (!result.value || !isSpecCardConfigured(specCard)) return;
    const timer = window.setTimeout(() => {
      void persistListingFields(true);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [listingSignature, batch?.id]);

  const exportWorkbook = async (kind: "dianxiaomi" | "apparel" | "general") => {
    if (!batch) return;
    // 先把最新的上架信息刷进批次，避免导出用的是旧快照。
    const saved = await persistListingFields(true);
    const exportStatus = saved ?? batch;
    if (!exportStatus.dianxiaomi_export.ready) {
      setError("导出信息还没补齐，或核心四张图尚未全部发布");
      return;
    }
    setBusy(`export-${kind}`);
    try {
      const blob =
        kind === "dianxiaomi"
          ? await podFreeCustomizationApi.exportDianxiaomi(batch.id)
          : await podFreeCustomizationApi.exportMiaoshou(batch.id, kind);
      const suffix = kind === "dianxiaomi" ? "dianxiaomi" : `miaoshou-${kind}`;
      downloadBlobAs(blob, `pod_free_${batch.id.slice(0, 8)}_${suffix}.xlsx`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  };

  const titleRow = batch?.style_titles?.[0];
  const buttonLabel = busy === "generate"
    ? "正在提交…"
    : !batch
      ? "开始生成主图"
      : activeCompleted
        ? "效果不理想？重写提示词重新生成"
        : activeFailed
          ? `重新生成${slotTitle(activeVariant)}`
          : `开始生成${slotTitle(activeVariant)}`;
  const promptPlaceholder = activeVariant === 1
    ? "请输入提示词"
    : `请输入第${activeVariant}张图片提示词，第${activeVariant}张图片将以第1张为主要参考，作为同一款的第${activeVariant}张图，主体描述请不要差距过大`;

  return (
    <section className="pod-free-customization-page" aria-label="POD 自由创意">
      <header className="pod-page-header">
        <div className="pod-page-title">
          <span className="pod-page-title-icon iconfont icon-bulb-fill" aria-hidden="true" />
          <div>
            <span>POD CREATIVE STUDIO · USER PROMPT</span>
            <div className="pod-free-title-row">
              <h1>自由创意</h1>
              <label className="pod-free-model-switch">
                <span>模型切换</span>
                <select
                  value={draft.image_model}
                  disabled={Boolean(batch) || running}
                  onChange={(event) => setField("image_model", event.currentTarget.value as FreeImageModel)}
                >
                  <option value={FLASH}>Seedream 5.0 Flash</option>
                  <option value={PRO}>Seedream 5.0 Pro</option>
                </select>
              </label>
              {activePrice && <span className="pod-page-price pod-free-inline-price">{activePrice}</span>}
            </div>
          </div>
        </div>
        <div className="pod-page-header-actions">
          <button type="button" onClick={() => { setHistoryOpen(true); void loadBatches(); }}>
            <span className="iconfont icon-time-circle" />查看定制记录历史
          </button>
          {batch && running && (
            <button type="button" disabled={busy === "cancel"} onClick={() => void cancelBatch()}>
              取消当前批次
            </button>
          )}
        </div>
      </header>

      {error && <p className="pod-free-message is-error" role="alert">{error}<button type="button" onClick={() => setError("")}>×</button></p>}
      {notice && <p className="pod-free-sr-only" role="status">{notice}</p>}

      <div className="pod-workbench-grid">
        <aside className="pod-setup-column">
          <section className="pod-setup-card pod-free-panel">
          <section className="pod-setup-card pod-free-model-intro">
            <p>{MODEL_INTRO[draft.image_model]}</p>
          </section>

          <section className="pod-setup-card">
            <textarea
              className="pod-free-prompt"
              value={batch ? activePrompt : draft.prompt}
              maxLength={4000}
              placeholder={promptPlaceholder}
              onChange={(event) => {
                const value = event.currentTarget.value;
                if (batch) setActivePrompt(value);
                else setField("prompt", value);
              }}
            />
            <small className="pod-free-counter">{(batch ? activePrompt : draft.prompt).length}/4000</small>
          </section>

          <div className={`pod-free-ref-collapse ${activeVariant === 1 ? "is-open" : ""}`} aria-hidden={activeVariant !== 1}>
            <section className="pod-setup-card">
              <div className="pod-section-title"><span>REFERENCES</span><h2>参考图（可选）</h2></div>
              <div className="pod-free-refs">
                {draft.reference_asset_ids.map((assetId) => (
                  <div key={assetId} className="pod-free-ref">
                    <PodAssetImage path={draft.reference_previews[assetId]} alt="参考图" />
                    {!batch && <button type="button" aria-label="移除参考图" onClick={() => removeReference(assetId)}>×</button>}
                  </div>
                ))}
                {!batch && draft.reference_asset_ids.length < 4 && (
                  <button type="button" className="pod-free-ref-add" disabled={uploading} onClick={() => fileInputRef.current?.click()}>
                    <span className="iconfont icon-upload" aria-hidden="true" />
                    <small>{uploading ? "上传中…" : "上传参考图"}</small>
                  </button>
                )}
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                hidden
                onChange={(event) => void pickReferences(event.target.files)}
              />
              <p className="pod-free-hint">
                {batch ? "参考图已随主图冻结。" : "最多 4 张，只对主图生效；第 2 张起固定参考主图。"}
              </p>
            </section>
          </div>

          <section className="pod-setup-card pod-free-count">
            <span>当前款式图片数量</span>
            <div className="pod-free-count-options" role="radiogroup" aria-label="当前款式图片数量">
              {[4, 5, 6].map((count) => (
                <button
                  key={count}
                  type="button"
                  disabled={Boolean(batch)}
                  className={(batch ? slotCount : targetCount) === count ? "is-active" : ""}
                  onClick={() => setTargetCount(count)}
                >
                  {count} 张
                </button>
              ))}
            </div>
          </section>

          <button
            type="button"
            className="pod-start-button"
            disabled={busy === "generate" || (Boolean(batch) && running) || (activeVariant > 1 && !mainReady)}
            onClick={() => void generateActive()}
          >
            {buttonLabel}
          </button>
          {batch && !allGenerated && <p className="pod-free-hint">生成完当前槽位后，右侧会自动高亮下一张；也可以直接点任意未生成槽位。</p>}

          {allGenerated && (
            <>
              <section className="pod-setup-card pod-free-title">
                <div className="pod-section-title"><span>LISTING TITLE</span><h2>标题与文案</h2></div>
                <textarea
                  value={titleDraft}
                  maxLength={200}
                  placeholder={batch?.title_enabled ? "标题生成中…" : "未启用自动标题，请手动填写"}
                  onChange={(event) => setTitleDraft(event.currentTarget.value)}
                />
                <div className="pod-free-title-actions">
                  <button type="button" disabled={busy === "title" || !titleDraft.trim()} onClick={() => void saveTitle()}>保存标题</button>
                  {batch?.title_enabled && <button type="button" disabled={busy === "title-regen"} onClick={() => void regenerateTitle()}>重新生成</button>}
                  <small>{titleRow ? statusLabel(titleRow.status) : ""}</small>
                </div>
              </section>

              <section className="pod-setup-card pod-free-export">
                <PodListingFieldsEditor
                  listingFields={listingFields}
                  specCard={specCard}
                  skuFieldErrors={listingFieldErrors}
                  skuLimitReached={skuLimitReached}
                  showRequiredErrors={listingErrorsRevealed}
                  hideSpecCardEntry
                  hideTitleMode
                  onListingFieldChange={updateListingField}
                  onAddSku={addSku}
                  onUpdateSku={updateSku}
                  onRemoveSku={removeSku}
                  onOpenSpecCardDrawer={() => setSpecCardDrawerOpen(true)}
                />
                <div className="pod-free-export-actions">
                  <div className="pod-spec-card-entry pod-free-spec-entry" aria-label="规格卡配置">
                    <button type="button" className="pod-spec-card-entry-button" onClick={() => setSpecCardDrawerOpen(true)}>
                      批量添加尺寸<em>*</em>
                    </button>
                    <span className={isSpecCardConfigured(specCard) ? "pod-spec-card-entry-summary" : "pod-spec-card-entry-summary is-warning"}>
                      {specCardSummaryText(specCard)}
                    </span>
                  </div>
                  {batch && isSpecCardConfigured(specCard) && (
                    <button
                      type="button"
                      className="pod-free-reprint"
                      disabled={busy === "spec-reprint"}
                      onClick={() => void reprintSpecCard()}
                    >
                      {busy === "spec-reprint" ? "重印中…" : "重印本批次"}
                    </button>
                  )}
                  <div className="pod-export-menu" ref={exportMenuRef}>
                    <button
                      type="button"
                      className="pod-export-menu-trigger"
                      disabled={!batch?.dianxiaomi_export.ready || busy.startsWith("export")}
                      aria-haspopup="menu"
                      aria-expanded={exportMenuOpen}
                      onClick={() => setExportMenuOpen((open) => !open)}
                    >
                      <span>{busy.startsWith("export") ? "正在导出表格" : "导出表格"}</span>
                      <i className="iconfont icon-down" aria-hidden="true" />
                    </button>
                    {exportMenuOpen && (
                      <div className="pod-export-menu-list" role="menu">
                        <button type="button" role="menuitem" onClick={() => { setExportMenuOpen(false); void exportWorkbook("dianxiaomi"); }}>导出店小秘表格</button>
                        <button type="button" role="menuitem" onClick={() => { setExportMenuOpen(false); void exportWorkbook("apparel"); }}>导出妙手表格（服饰）</button>
                        <button type="button" role="menuitem" onClick={() => { setExportMenuOpen(false); void exportWorkbook("general"); }}>导出妙手表格（非服饰）</button>
                      </div>
                    )}
                  </div>
                </div>
                {batch && !batch.dianxiaomi_export.ready && (
                  <p className="pod-free-hint">核心四张图全部完成并发布、且导出信息补齐后才能导出。</p>
                )}
              </section>
            </>
          )}
          </section>
        </aside>

        <main className="pod-results-column">
          <section className="pod-gallery pod-free-gallery" aria-label="图片槽位面板">
          <p className="pod-free-hint pod-free-gallery-hint">长按两秒拖动改变除主图外图片顺序，点小秘导出顺序同步改动</p>
          <section className={dragGhost ? "pod-free-slots is-dragging" : "pod-free-slots"} aria-label="图片槽位">
            {Array.from({ length: visibleSlotCount }, (_, index) => index + 1).map((variant) => {
              const item = itemsByVariant.get(variant);
              const preview = itemImagePath(item);
              const isActive = activeVariant === variant;
              const isLeaving = leavingVariants.includes(variant);
              const isGenerating =
                !preview &&
                (["queued", "generating_pattern", "compositing"].includes(item?.status ?? "") ||
                  (isActive && busy === "generate"));
              return (
                <button
                  key={variant}
                  type="button"
                  data-free-variant={variant}
                  draggable={false}
                  onDragStart={(event) => event.preventDefault()}
                  className={[
                    "pod-free-slot",
                    preview ? "has-image" : "",
                    isActive ? "is-active" : "",
                    isGenerating ? "is-generating" : "",
                    item?.status === "failed" ? "is-failed" : "",
                    enteringVariants.includes(variant) ? "is-entering" : "",
                    isLeaving ? "is-leaving" : "",
                    dragGhost?.variant === variant ? "is-dragging" : "",
                    dragGhost && dropVariant === variant ? "is-drop-target" : "",
                  ].join(" ")}
                  disabled={isLeaving || (variant > 1 && !mainReady)}
                  aria-hidden={isLeaving || undefined}
                  onPointerDown={(event) => beginSlotPress(variant, preview, event)}
                  onPointerMove={trackSlotPress}
                  onPointerLeave={clearSlotLongPress}
                  onClick={() => {
                    if (longPressFiredRef.current) {
                      longPressFiredRef.current = false;
                      return;
                    }
                    selectSlot(variant);
                  }}
                  aria-label={`${slotTitle(variant)}${preview ? "，已生成" : isGenerating ? "，生成中" : "，待生成"}`}
                  title={
                    variant > 1 && !mainReady
                      ? "先生成主图后才能生成后续图片"
                      : preview && batch
                        ? "长按 2 秒可拖动排序"
                        : undefined
                  }
                >
                  {preview ? (
                    <PodAssetImage path={preview} alt={slotTitle(variant)} loading="lazy" />
                  ) : (
                    <span>{isGenerating ? "生成中" : item?.status === "failed" ? "生成失败" : variant === 1 ? "主图" : "待生成"}</span>
                  )}
                  <em>{slotTitle(variant)}</em>
                </button>
              );
            })}
          </section>
          </section>
        </main>
      </div>

      {/* portal 到 body：避免 tab 面板的层叠上下文把 fixed 弹窗关在面板里。 */}
      {remakeMainOpen && createPortal(
        <div className="pod-free-confirm-layer">
          <div className="pod-free-confirm" role="alertdialog" aria-modal="true" aria-labelledby="pod-free-confirm-title">
            <h3 id="pod-free-confirm-title">重做主图？</h3>
            <p>重做主图后续已出衍生图将视为自动放弃并清除，积分不予返还。</p>
            <div className="pod-free-confirm-actions">
              <button type="button" onClick={() => setRemakeMainOpen(false)}>取消</button>
              <button
                type="button"
                className="is-danger"
                onClick={() => {
                  setRemakeMainOpen(false);
                  void runGenerate();
                }}
              >
                确认重做主图
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* 拖动中的浮动卡片：portal 到 body，跟随鼠标移动。 */}
      {dragGhost && createPortal(
        <div
          className="pod-free-drag-ghost"
          style={{
            width: dragGhost.width,
            height: dragGhost.height,
            transform: `translate3d(${dragGhost.x - dragGhost.width / 2}px, ${dragGhost.y - dragGhost.height / 2}px, 0)`,
          }}
          aria-hidden="true"
        >
          <PodAssetImage path={dragGhost.preview} alt="" />
        </div>,
        document.body,
      )}

      <SpecCardDrawer
        open={specCardDrawerOpen}
        config={specCard}
        targetVariants={Array.from({ length: slotCount }, (_, index) => index + 1)}
        onClose={() => setSpecCardDrawerOpen(false)}
        onSave={(next) => void saveSpecCard(next)}
      />

      {/* portal 到 body：tab 面板的入场动画创建层叠上下文，会把 fixed 抽屉锁在面板内。 */}
      {historyOpen && createPortal(
        <div className="pod-history-drawer-layer">
          <button type="button" className="pod-history-drawer-backdrop" onClick={() => setHistoryOpen(false)} aria-label="关闭批次记录" />
          <aside className="pod-history-drawer" role="dialog" aria-modal="true" aria-label="批次记录">
            <header className="pod-history-drawer-header">
              <div>
                <span>BATCH HISTORY</span>
                <h2>批次记录</h2>
                <p>点击任一批次切换到对应视图。</p>
              </div>
              <button type="button" onClick={() => setHistoryOpen(false)} aria-label="关闭">×</button>
            </header>
            <div className="pod-history-drawer-body">
              <section className="pod-history" aria-label="自由创意批次记录">
                <header>
                  <div><span>BATCH HISTORY</span><h3>最近批次</h3></div>
                  <div className="pod-history-header-actions">
                    <button type="button" onClick={() => void loadBatches()} aria-label="刷新批次记录"><span className="iconfont icon-sync" /></button>
                  </div>
                </header>
                <div className="pod-history-list">
                  {batches.map((row) => (
                    <div key={row.id} className={`pod-history-item ${batch?.id === row.id ? "is-active" : ""}`}>
                      <button type="button" className="pod-history-open" onClick={() => { setBatch(null); void refreshBatch(row.id); setHistoryOpen(false); }}>
                        <span className="pod-history-primary"><b>{row.title || row.id}</b><small>{statusLabel(row.status)}</small></span>
                        <span className="pod-history-progress">
                          <i><span style={{ width: `${row.item_count ? Math.round((row.completed_item_count / row.item_count) * 100) : 0}%` }} /></i>
                          <small>{row.completed_item_count}/{row.item_count}</small>
                        </span>
                        <time dateTime={row.created_at}>{new Date(row.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
                      </button>
                      <button
                        type="button"
                        className="pod-free-history-delete"
                        disabled={busy === "delete"}
                        aria-label={`删除批次 ${row.title || row.id}`}
                        title="删除该批次"
                        onClick={() => void deleteBatch(row.id)}
                      >
                        <span className="iconfont icon-delete" aria-hidden="true" />
                      </button>
                    </div>
                  ))}
                  {batches.length === 0 && <p>暂无批次；发起第一个自由创意批次后在此查看。</p>}
                </div>
              </section>
            </div>
          </aside>
        </div>,
        document.body,
      )}
    </section>
  );
}

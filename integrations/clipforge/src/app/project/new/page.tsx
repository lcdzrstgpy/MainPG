"use client";

/**
 * 兼容旧链接的「新建带货项目」入口。
 *
 * 它不再自建一套表单状态：渲染的是 /start 同一份共享创作简报表单，创建 DTO 与脚本 DTO 都只有一份
 * （脚本 DTO 由 buildScriptRequest 构造）。广告模板 / 我的模板 / AI 定制模板保留为「预填简报」的来源。
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { LuCircleAlert, LuZap } from "react-icons/lu";
import { useCharacterStore } from "@/lib/stores/project-store";
import { useTemplateStore } from "@/lib/stores/template-store";
import { useProductLibraryStore } from "@/lib/stores/product-library-store";
import { getExampleProducts, type ExampleProduct } from "@/lib/examples";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { AD_TEMPLATE_GROUPS, listAdTemplates, getAdTemplate, adTemplateScriptDirective, adTemplateStorageKey, recommendAdTemplates, encodeStoredAdTemplate, exportAdTemplateShare, exportAdTemplatePack, AD_TEMPLATE_EDIT_VOCAB, CUSTOM_AD_TEMPLATE_ID, type AdTemplate, type AdTemplateGroupId, type AdTemplateCategory } from "@/lib/ad-templates";
import { CAMERA_PRESETS } from "@/lib/camera-presets";
import { LOOK_PRESETS } from "@/lib/look-presets";
import { CAPTION_PRESET_IDS, type CaptionPresetId } from "@/lib/caption-presets";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  CreationBriefForm,
  type CreationBriefFormPrefill,
  type CreationBriefFormValues,
} from "@/components/project-creation/creation-brief-form";
import { DEFAULT_VIDEO_MODE } from "@/components/project-creation/creation-brief-defaults";
import { buildScriptRequest } from "@/components/project-creation/build-script-request";
import { fetchImagesAsFiles, importProductSource, isValidProductUrl } from "@/components/project-creation/link-import";
import {
  missingStyleRequirement,
  parseStyleRequirement,
  type ScriptStyleRequirement,
} from "@/components/project-creation/script-style-requirement";
import { StyleChoicePrompt } from "@/components/project-creation/style-choice-prompt";
import { recordStrategySelected } from "@/components/project-creation/creation-events";
import { DEFAULT_CREATION_BRIEF, sanitizeCreationBrief, type CreationBrief } from "@/lib/creation-brief";
import { useT, useLocale } from "@/lib/i18n";

/**
 * 页内的填表顺序提示。与 BGM_LABELS 一样属于页面内的双语数据，不进 i18n 词表：
 * 这三个区块名是 e2e（e2e/smoke.spec.ts:16-21）断言的可见文案，必须在页面源码里看得见。
 */
const FORM_STEPS = [
  { zh: "商品图片", en: "Product images" },
  { zh: "商品名称", en: "Product name" },
  { zh: "视频模式", en: "Video mode" },
  { zh: "出片策略", en: "Output strategy" },
] as const;

/** 表单快照的初始值（共享表单挂载后立刻会上报真实值）。 */
const EMPTY_FORM_VALUES: CreationBriefFormValues = {
  brief: DEFAULT_CREATION_BRIEF,
  productName: "",
  category: "",
  sellingPoints: "",
  images: [],
  linkUrl: "",
  topic: "",
  videoMode: DEFAULT_VIDEO_MODE,
};

// recipe-editor display labels for compose enums (bilingual data like the preset libraries, not i18n keys)
const BGM_LABELS: Record<string, { zh: string; en: string }> = {
  none: { zh: "无", en: "None" },
  upbeat: { zh: "轻快", en: "Upbeat" },
  chill: { zh: "舒缓", en: "Chill" },
  energetic: { zh: "动感", en: "Energetic" },
  emotional: { zh: "情感", en: "Emotional" },
};
const QUALITY_LABELS: Record<string, { zh: string; en: string }> = {
  fast: { zh: "快速", en: "Fast" },
  standard: { zh: "标准", en: "Standard" },
  hd: { zh: "高清", en: "HD" },
};
const CAPTION_LABELS: Record<CaptionPresetId, { zh: string; en: string }> = {
  standard: { zh: "标准", en: "Standard" },
  bold: { zh: "大字冲击", en: "Bold" },
  minimal: { zh: "极简", en: "Minimal" },
  karaoke: { zh: "卡拉OK", en: "Karaoke" },
};
// shot-type → i18n key for the camera-plan selects
const SHOT_LABEL_KEYS: Record<string, string> = {
  hook: "adTplShotHook",
  pain_point: "adTplShotPain",
  product_reveal: "adTplShotReveal",
  demo: "adTplShotDemo",
  social_proof: "adTplShotProof",
  cta: "adTplShotCta",
};
// shared input/select styling for the compact recipe editor
const EDITOR_INPUT_CLS =
  "w-full px-2 py-1.5 rounded-md text-xs border border-border/50 bg-background/60 outline-none focus:border-primary/60 placeholder:text-muted-foreground/60";

// script style options (label/desc changed to i18n keys, converted via t() at render time)
// Ordered by form (剧情形 → 物品形 → 口播形 → 场景形), smart-pick last — the full style system
const styleOptions = [
  { value: "drama", labelKey: "styleDramaLabel", descKey: "styleDramaDesc" },
  { value: "reversal", labelKey: "styleReversalLabel", descKey: "styleReversalDesc" },
  { value: "interview", labelKey: "styleInterviewLabel", descKey: "styleInterviewDesc" },
  { value: "story", labelKey: "styleStoryLabel", descKey: "styleStoryDesc" },
  { value: "unboxing", labelKey: "styleUnboxingLabel", descKey: "styleUnboxingDesc" },
  { value: "product_pov", labelKey: "styleProductPovLabel", descKey: "styleProductPovDesc" },
  { value: "comparison", labelKey: "styleComparisonLabel", descKey: "styleComparisonDesc" },
  { value: "talking_head", labelKey: "styleTalkingHeadLabel", descKey: "styleTalkingHeadDesc" },
  { value: "pain-point", labelKey: "stylePainPointLabel", descKey: "stylePainPointDesc" },
  { value: "scenario", labelKey: "styleScenarioLabel", descKey: "styleScenarioDesc" },
  { value: "auto", labelKey: "styleAutoLabel", descKey: "styleAutoDesc" },
];

// video mode options for the recipe-editor select (value + i18n label, no icon: the shared form renders the picker)
const videoModeOptions = [
  { value: "product_closeup", labelKey: "modeCloseupLabel" },
  { value: "graphic_montage", labelKey: "modeMontageLabel" },
  { value: "scene_demo", labelKey: "modeSceneLabel" },
  { value: "live_presenter", labelKey: "modePresenterLabel" },
];

/** 已建好项目、只差脚本的待重试请求（409 需要用户显式选风格时用）。 */
interface PendingScript {
  kind: "script";
  projectId: string;
  brief: CreationBrief;
  productImages: string[];
  referenceStructure?: string;
  customRequirements?: string;
}

/** 还没建项目就需要用户先选风格的提交内容。 */
interface PendingForm {
  kind: "form";
  values: CreationBriefFormValues;
}

type PendingCreation = PendingScript | PendingForm;

export default function NewProjectPage() {
  const router = useRouter();
  const t = useT("newProject");
  const locale = useLocale();

  // check LLM API configuration status
  const { llm, setVisualLook } = useSettingsStore();
  const isLLMConfigured = llm.apiKey.length > 0;
  const characters = useCharacterStore((state) => state.characters);
  const { templates, incrementUseCount } = useTemplateStore();

  // 共享表单是唯一状态源：这里只镜像最近一次快照，供模板推荐 / AI 定制使用
  const [formValues, setFormValues] = useState<CreationBriefFormValues>(EMPTY_FORM_VALUES);
  const [prefill, setPrefill] = useState<CreationBriefFormPrefill | undefined>();
  const [prefillKey, setPrefillKey] = useState("");
  // 链接导入状态（导入只预填简报）
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importedImages, setImportedImages] = useState<string[]>([]);
  // 需要用户显式选风格（本地未选 / 接口 409 无数据可推荐）
  const [stylePrompt, setStylePrompt] = useState<ScriptStyleRequirement | null>(null);

  // submission state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<{
    step: string;
    percent: number;
    message: string;
  } | null>(null);
  const pendingRef = useRef<PendingCreation | null>(null);

  // Ad template end-to-end recipe: pre-fills style/mode here,
  // injects the camera/look plan into script generation, and hands the compose recipe
  // to the video page via localStorage
  const [selectedAdTemplateId, setSelectedAdTemplateId] = useState<string>("");
  const [adTemplateGroup, setAdTemplateGroup] = useState<AdTemplateGroupId | "all" | "mine">("all");
  const [adTemplateQuery, setAdTemplateQuery] = useState("");
  // AI-generated custom template (one slot; lives in component state until project creation persists it)
  const [customAdTemplate, setCustomAdTemplate] = useState<AdTemplate | null>(null);
  const [aiTplLoading, setAiTplLoading] = useState(false);
  const [aiTplError, setAiTplError] = useState("");
  // user-owned templates (template economy): saved AI recipes + imported share files, DB-backed
  const [myTemplates, setMyTemplates] = useState<AdTemplate[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [mineNotice, setMineNotice] = useState("");
  const [aiTplSaved, setAiTplSaved] = useState(false);
  // recipe editor: fork any template (builtin/AI/mine) into an editable draft;
  // editorSourceId non-empty = a mine row being edited in place, empty = saving a new fork
  const [editorOpen, setEditorOpen] = useState(false);
  const [editorDraft, setEditorDraft] = useState<AdTemplate | null>(null);
  const [editorSourceId, setEditorSourceId] = useState("");
  const [editorBusy, setEditorBusy] = useState(false);
  useEffect(() => {
    // best-effort: an empty "mine" list (fresh install / fetch failure) just hides the section
    fetch("/api/ad-template/mine")
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d.templates)) setMyTemplates(d.templates as AdTemplate[]);
      })
      .catch(() => {});
  }, []);

  const pushPrefill = useCallback((next: CreationBriefFormPrefill) => {
    setPrefill(next);
    setPrefillKey(crypto.randomUUID());
  }, []);

  /** Resolve any selectable template id: AI slot → my templates → builtin library */
  const resolveAdTemplate = (id: string): AdTemplate | null => {
    if (!id) return null;
    if (id === CUSTOM_AD_TEMPLATE_ID) return customAdTemplate;
    return myTemplates.find((m) => m.id === id) ?? getAdTemplate(id) ?? null;
  };
  // `known` bypasses resolveAdTemplate for templates just added in the same event —
  // the myTemplates closure is still stale there, so a lookup would miss the pre-fill
  // 成片模板只做一件事：把风格与画面形态预填进统一简报（用户仍可在表单里覆盖）
  const pickAdTemplate = (id: string, known?: AdTemplate | null) => {
    setSelectedAdTemplateId(id);
    const tpl = known ?? resolveAdTemplate(id);
    if (tpl) {
      pushPrefill({ brief: { styleType: tpl.styleType, styleSource: "template" }, videoMode: tpl.videoMode });
    }
  };
  /** Persist the current AI recipe into "my templates" (server re-validates + compliance-screens) */
  const saveAiTemplateToMine = async () => {
    if (!customAdTemplate || aiTplSaved) return;
    setMineNotice("");
    try {
      const res = await fetch("/api/ad-template/mine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template: customAdTemplate }),
      });
      const data = await res.json();
      if (!res.ok || !data.template) throw new Error(data.error || t("adTemplateImportFailed"));
      setMyTemplates((prev) => [data.template as AdTemplate, ...prev]);
      setAiTplSaved(true);
      setMineNotice(t("adTemplateSaved"));
    } catch (e) {
      setMineNotice(e instanceof Error ? e.message : t("adTemplateImportFailed"));
    }
  };
  /** Import a shared template JSON (single OR pack); the server is the authoritative validator */
  const importAdTemplate = async () => {
    if (importBusy || !importText.trim()) return;
    setImportBusy(true);
    setMineNotice("");
    try {
      const res = await fetch("/api/ad-template/mine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ share: importText }),
      });
      const data = await res.json();
      const imported = (Array.isArray(data.templates) ? data.templates : [data.template]).filter(
        Boolean
      ) as AdTemplate[];
      if (!res.ok || imported.length === 0) throw new Error(data.error || t("adTemplateImportFailed"));
      setMyTemplates((prev) => [...imported, ...prev]);
      setImportOpen(false);
      setImportText("");
      pickAdTemplate(imported[0].id, imported[0]);
      const notices: string[] = [];
      if (imported.length > 1) notices.push(t("adTemplateImportedMany").replace("{n}", String(imported.length)));
      if (Array.isArray(data.warnings) && data.warnings.length > 0) {
        // multi-import already says "imported" — use the prefix-free warning to avoid saying it twice
        const warnKey = imported.length > 1 ? "adTemplateWarnOnly" : "adTemplateImportWarn";
        notices.push(`${t(warnKey)}${data.warnings.join("、")}`);
      }
      if (notices.length > 0) setMineNotice(notices.join(" "));
    } catch (e) {
      setMineNotice(e instanceof Error ? e.message : t("adTemplateImportFailed"));
    } finally {
      setImportBusy(false);
    }
  };
  const deleteMyTemplate = async (id: string) => {
    setMyTemplates((prev) => prev.filter((m) => m.id !== id));
    if (selectedAdTemplateId === id) setSelectedAdTemplateId("");
    // fire-and-forget: the optimistic removal above is the UX; a failed delete resurfaces on reload
    fetch(`/api/ad-template/mine?id=${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => {});
  };
  /** Download the selected template as a shareable .json file (works for builtin/AI/my templates) */
  const exportSelectedTemplate = () => {
    const tpl = resolveAdTemplate(selectedAdTemplateId);
    if (!tpl) return;
    const blob = new Blob([exportAdTemplateShare(tpl)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `clipforge-template-${tpl.name.en.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "recipe"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  /** Download ALL my templates as one shareable pack file */
  const exportMinePack = () => {
    if (myTemplates.length === 0) return;
    const blob = new Blob([exportAdTemplatePack(myTemplates)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `clipforge-template-pack-${myTemplates.length}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  /** Open the recipe editor for the current selection: mine → edit in place, anything else → fork */
  const openTemplateEditor = () => {
    const tpl = resolveAdTemplate(selectedAdTemplateId);
    if (!tpl) return;
    const isMine = myTemplates.some((m) => m.id === tpl.id);
    // deep copy — the draft must never mutate the builtin library / store objects
    setEditorDraft(JSON.parse(JSON.stringify(tpl)) as AdTemplate);
    setEditorSourceId(isMine ? tpl.id : "");
    setEditorOpen(true);
    setImportOpen(false);
    setMineNotice("");
  };
  /** Persist the editor draft: PUT updates a mine row in place, POST saves a new fork */
  const saveEditorTemplate = async () => {
    if (!editorDraft || editorBusy) return;
    setEditorBusy(true);
    setMineNotice("");
    try {
      const res = await fetch("/api/ad-template/mine", {
        method: editorSourceId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          editorSourceId
            ? { id: editorSourceId, template: editorDraft }
            : { template: editorDraft, source: "edit" }
        ),
      });
      const data = await res.json();
      if (!res.ok || !data.template) throw new Error(data.error || t("adTplEditorSaveFailed"));
      const tpl = data.template as AdTemplate;
      setMyTemplates((prev) =>
        editorSourceId ? prev.map((m) => (m.id === tpl.id ? tpl : m)) : [tpl, ...prev]
      );
      setEditorOpen(false);
      setEditorDraft(null);
      pickAdTemplate(tpl.id, tpl);
      if (Array.isArray(data.warnings) && data.warnings.length > 0) {
        setMineNotice(`${t("adTemplateImportWarn")}${data.warnings.join("、")}`);
      }
    } catch (e) {
      setMineNotice(e instanceof Error ? e.message : t("adTplEditorSaveFailed"));
    } finally {
      setEditorBusy(false);
    }
  };
  // AI custom template: one cheap LLM call that PICKS from the real preset vocabularies (server-side clamped)
  const generateAiTemplate = async () => {
    if (aiTplLoading) return;
    setAiTplError("");
    setAiTplLoading(true);
    try {
      const res = await fetch("/api/ad-template/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          productName: formValues.productName,
          category: formValues.category,
          sellingPoints: formValues.sellingPoints,
          llmConfig: { baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model },
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.template) throw new Error(data.error || t("adTemplateAiFailed"));
      setCustomAdTemplate(data.template as AdTemplate);
      setAiTplSaved(false); // a fresh recipe is savable again
      pickAdTemplate(CUSTOM_AD_TEMPLATE_ID, data.template as AdTemplate);
    } catch (e) {
      setAiTplError(e instanceof Error ? e.message : t("adTemplateAiFailed"));
    } finally {
      setAiTplLoading(false);
    }
  };

  // product library (used to pre-fill from the library when "make video" is triggered)
  const { products: libraryProducts } = useProductLibraryStore();

  // on mount, if ?productId is present, pre-fill once from the product library (products are only available after the store hydrates, hence the dependency)
  const prefilledRef = useRef(false);
  useEffect(() => {
    if (prefilledRef.current) return;
    const productId = new URLSearchParams(window.location.search).get("productId");
    if (!productId) return;
    const product = libraryProducts.find((p) => p.id === productId);
    if (!product) return;
    prefilledRef.current = true;
    void (async () => {
      // product library images are same-origin /api/files paths, so they can be fetched as Files;
      // local blob URLs expire across pages and are simply skipped (text stays filled)
      const files = await fetchImagesAsFiles(product.images);
      pushPrefill({
        productName: product.name,
        // the product library's "tech" category maps to "digital" here; all other values are the same
        category: product.category === "tech" ? "digital" : product.category,
        sellingPoints: product.description ?? "",
        brief: { inputMode: "product-library" },
        ...(files.length ? { images: files } : {}),
      });
    })();
  }, [libraryProducts, pushPrefill]);

  // one-click fill with example product (including a real sample image) to let beginners try without any setup
  const fillExample = useCallback(async (ex: ExampleProduct) => {
    const files = await fetchImagesAsFiles([ex.image]);
    pushPrefill({
      productName: ex.name,
      category: ex.category,
      sellingPoints: ex.sellingPoints,
      brief: { inputMode: "upload" },
      ...(files.length ? { images: files } : {}),
    });
  }, [pushPrefill]);

  // paste product link → backend parses title / price / images and PRE-FILLS the brief
  // (nothing is created here: the project is always created with the brief by the submit flow)
  const handleImportLink = useCallback(async (url: string) => {
    if (!isValidProductUrl(url)) {
      setImportError(t("ingestErrorUrl"));
      return;
    }
    setImportError("");
    setImporting(true);
    try {
      const result = await importProductSource(url);
      if (!result.ok) throw new Error(result.message || t("ingestErrorFail"));
      const { source } = result;
      setImportedImages(source.imageUrls);
      pushPrefill({
        productName: source.productName,
        sellingPoints: source.sellingPoints,
        linkUrl: source.linkUrl,
        brief: { inputMode: "link" },
        ...(source.files.length ? { images: source.files } : {}),
      });
    } catch (e) {
      setImportError(e instanceof Error ? e.message : t("ingestErrorFail"));
    } finally {
      setImporting(false);
    }
  }, [pushPrefill, t]);

  // summary badge for the folded templates drawer: surfaces what's currently applied while closed
  const pickedAdTpl = resolveAdTemplate(selectedAdTemplateId);
  const pickedTemplateNames = pickedAdTpl ? (locale === "zh" ? pickedAdTpl.name.zh : pickedAdTpl.name.en) : "";

  const patchProject = async (projectId: string, body: Record<string, unknown>): Promise<boolean> => {
    try {
      const res = await fetch(`/api/project/${projectId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return res.ok;
    } catch {
      return false;
    }
  };

  const uploadImages = async (projectId: string, files: CreationBriefFormValues["images"]): Promise<string[]> => {
    const formData = new FormData();
    files.forEach((img) => formData.append("files", img.file));
    formData.append("projectId", projectId);
    const uploadRes = await fetch("/api/upload", { method: "POST", body: formData });
    if (!uploadRes.ok) {
      const errData = await uploadRes.json().catch(() => ({}));
      throw new Error(errData.error || t("errorUploadFailed"));
    }
    const { paths } = await uploadRes.json();
    return Array.isArray(paths) ? paths : [];
  };

  /**
   * 脚本请求：唯一构造器是 buildScriptRequest；409 needs_explicit_style 不是失败，
   * 而是「请用户选一个风格」——记下待重试请求并返回。
   */
  const requestScript = async (pending: PendingScript): Promise<"ok" | "needs-style"> => {
    const character = pending.brief.characterId
      ? characters.find((c) => c.id === pending.brief.characterId)
      : undefined;
    const res = await fetch("/api/llm/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        buildScriptRequest({
          brief: pending.brief,
          projectId: pending.projectId,
          productName: formValues.productName,
          category: formValues.category,
          productDescription: formValues.sellingPoints,
          productImages: pending.productImages,
          videoMode: formValues.videoMode,
          llmConfig: {
            baseUrl: llm.baseUrl,
            apiKey: llm.apiKey,
            model: llm.model,
            visionModel: llm.visionModel,
          },
          referenceStructure: pending.referenceStructure,
          customRequirements: pending.customRequirements,
          character: character
            ? {
                id: character.id,
                name: character.name,
                appearance: character.appearance || "",
                voiceStyle: character.voiceProfile?.style,
              }
            : undefined,
        })
      ),
    });
    if (res.ok) return "ok";
    const data: { error?: string; code?: string; candidates?: unknown } = await res.json().catch(() => ({}));
    const requirement = parseStyleRequirement(res.status, data);
    if (requirement) {
      pendingRef.current = pending;
      setStylePrompt(requirement);
      return "needs-style";
    }
    throw new Error(t("errorScriptFailed"));
  };

  // submission handler: 建项目（带 creationBrief）→ 应用成片模板 → 上传商品图 → 生成脚本
  const handleSubmitForm = async (values: CreationBriefFormValues) => {
    if (isSubmitting) return;
    if (!isLLMConfigured) {
      setError(t("hintNeedLlm"));
      return;
    }
    const brief = sanitizeCreationBrief(values.brief);
    // 没有显式风格就不提交：先让用户选
    if (!brief.styleType) {
      pendingRef.current = { kind: "form", values };
      setStylePrompt(missingStyleRequirement());
      return;
    }
    setStylePrompt(null);
    setError(null);

    // 爆款脚本模板：把它真正“用起来”——序列化分镜结构作为 AI 参考
    const selectedTemplate = brief.templateId
      ? templates.find((tpl) => tpl.id === brief.templateId)
      : null;
    const referenceStructure = selectedTemplate
      ? selectedTemplate.shots
          .map((s, i) => `${i + 1}. [${s.type}] ${s.duration}s ${s.camera ?? ""} 口播参考：「${s.voiceover ?? ""}」`)
          .join("\n")
      : undefined;

    setIsSubmitting(true);
    try {
      // step 1: create the project (get projectId first) — the brief travels with it
      setProgress({ step: "creating", percent: 15, message: t("progressCreating") });
      const projectRes = await fetch("/api/project", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: `${values.productName} 推广`,
          productName: values.productName,
          productCategory: values.category,
          productDescription: values.sellingPoints,
          productImages: [],
          creationBrief: brief,
        }),
      });
      if (!projectRes.ok) throw new Error(t("errorCreateFailed"));
      const project = await projectRes.json();

      // 策略选择本身要留痕（project_created 之外的事件）；失败不影响创建
      try {
        await recordStrategySelected({ projectId: project.id, creationBrief: brief });
      } catch {
        /* observability only */
      }

      // ad template: apply the global look now and hand the compose recipe to the
      // video page (localStorage, same client-side convention as the template store);
      // AI custom and "my templates" are stored inline (custom:<json>) since they have no builtin id
      const adTemplate = resolveAdTemplate(selectedAdTemplateId);
      if (adTemplate) {
        setVisualLook(adTemplate.look);
        try {
          localStorage.setItem(adTemplateStorageKey(project.id), encodeStoredAdTemplate(adTemplate));
        } catch {
          // storage full/unavailable only loses the compose pre-fill, never the flow
        }
      }

      // step 2: upload images (with projectId); a successful link import already carries product images
      setProgress({ step: "uploading", percent: 35, message: t("progressUploading") });
      let productImages: string[] = [];
      if (values.images.length) {
        productImages = await uploadImages(project.id, values.images);
      } else if (importedImages.length) {
        productImages = importedImages;
      }
      if (productImages.length) await patchProject(project.id, { productImages });

      // step 3: generate the script
      setProgress({ step: "generating", percent: 60, message: t("progressGenerating") });
      const outcome = await requestScript({
        kind: "script",
        projectId: project.id,
        brief,
        productImages,
        referenceStructure,
        customRequirements: adTemplate ? adTemplateScriptDirective(adTemplate) : undefined,
      });
      if (outcome === "needs-style") {
        setIsSubmitting(false);
        setProgress(null);
        return;
      }
      if (brief.templateId) incrementUseCount(brief.templateId);

      // step 4: done
      setProgress({ step: "done", percent: 100, message: t("progressDone") });
      await new Promise((r) => setTimeout(r, 800));
      router.push(`/project/${project.id}/script`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errorGeneric"));
      setIsSubmitting(false);
      setProgress(null);
    }
  };

  /** 用户选完风格：没建项目就带着新风格重新走创建；已建项目就只重试脚本请求。 */
  const pickStyle = async (styleType: string) => {
    const pending = pendingRef.current;
    if (!pending || isSubmitting) return;
    if (pending.kind === "form") {
      setStylePrompt(null);
      await handleSubmitForm({
        ...pending.values,
        brief: sanitizeCreationBrief({ ...pending.values.brief, styleType, styleSource: "explicit" }),
      });
      return;
    }
    const brief = sanitizeCreationBrief({ ...pending.brief, styleType, styleSource: "explicit" });
    setStylePrompt(null);
    setError(null);
    setIsSubmitting(true);
    try {
      // 库里的简报也要跟着用户的实际选择走（失败不阻断这次生成）
      await patchProject(pending.projectId, { creationBrief: brief });
      const outcome = await requestScript({ ...pending, brief });
      if (outcome === "needs-style") {
        setIsSubmitting(false);
        return;
      }
      setProgress({ step: "done", percent: 100, message: t("progressDone") });
      await new Promise((r) => setTimeout(r, 800));
      router.push(`/project/${pending.projectId}/script`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errorGeneric"));
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen grid-bg">
      <main className="mx-auto max-w-2xl px-6 py-10">
        {/* page title */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold tracking-tight">
            {t("pageTitlePrefix")}<span className="brand-gradient-text">{t("pageTitleAccent")}</span>
          </h1>
          <p className="text-sm text-muted-foreground mt-1.5">
            {t("pageSubtitle")}
          </p>
        </div>

        {/* LLM not configured warning */}
        {!isLLMConfigured && (
          <Link href="/settings?tab=llm">
            <div className="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3 cursor-pointer hover:bg-amber-500/15 transition-colors">
              <LuCircleAlert className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-medium text-amber-200">{t("llmWarnTitle")}</p>
                <p className="text-xs text-amber-300/80 mt-0.5">{t("llmWarnDesc")}<span className="underline">{t("llmWarnCta")}</span></p>
              </div>
            </div>
          </Link>
        )}

        {/* fill order hint: the shared form's four sections */}
        <div className="mb-6 flex flex-wrap items-center gap-2 text-xs">
          {FORM_STEPS.map((step, index) => (
            <span
              key={step.en}
              className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-muted/20 px-3 py-1 text-muted-foreground"
            >
              <b className="font-semibold text-primary">{index + 1}</b>
              {locale === "zh" ? step.zh : step.en}
            </span>
          ))}
        </div>

        <div className="space-y-6">
          {/* templates drawer: ad recipes only — they pre-fill the shared brief instead of owning a second request body */}
          <Card className="glass-card">
            <CardContent className="p-5">
              <details className="group">
                <summary className="flex items-center justify-between gap-3 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
                  <div className="min-w-0">
                    <span className="text-sm font-medium flex items-center gap-1.5">
                      <LuZap className="w-4 h-4 text-primary" />
                      {t("templatesSummary")}
                      {pickedTemplateNames && (
                        <Badge variant="secondary" className="text-[10px] max-w-48 truncate">{pickedTemplateNames}</Badge>
                      )}
                    </span>
                    <p className="text-xs text-muted-foreground mt-1">{t("templatesSummaryDesc")}</p>
                  </div>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-muted-foreground transition-transform group-open:rotate-180">
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                </summary>
                <div className="mt-5">
                  {/* One-click finished-video ad template recipes:
                      picking one pre-fills style/mode/look/camera-plan/compose across the pipeline */}
                  <div>
                    <div className="mb-3">
                      <Label className="text-sm font-medium flex items-center gap-1.5">
                        <LuZap className="w-4 h-4 text-primary" />
                        {t("adTemplateTitle")}
                      </Label>
                      <p className="text-xs text-muted-foreground mt-1">{t("adTemplateDesc")}</p>
                    </div>
                    {/* product-aware recommendations: keyword signals + category tie, computed live from the form */}
                    {(() => {
                      const recommended = recommendAdTemplates({
                        category: formValues.category,
                        productName: formValues.productName,
                        sellingPoints: formValues.sellingPoints,
                      });
                      if (recommended.length === 0) return null;
                      return (
                        <div className="flex flex-wrap items-center gap-1.5 mb-3">
                          <span className="text-xs text-muted-foreground">{t("adTemplateRecommended")}</span>
                          {recommended.map((tpl) => (
                            <button
                              key={`rec-${tpl.id}`}
                              onClick={() => pickAdTemplate(tpl.id)}
                              className={`px-2.5 py-1 rounded-full text-xs border transition-all ${
                                selectedAdTemplateId === tpl.id
                                  ? "border-primary bg-primary/10 text-primary font-medium"
                                  : "border-primary/30 bg-primary/5 text-foreground hover:border-primary/60"
                              }`}
                            >
                              {tpl.emoji} {locale === "zh" ? tpl.name.zh : tpl.name.en}
                            </button>
                          ))}
                        </div>
                      );
                    })()}
                    {/* group filter chips — a large library needs a browse taxonomy, not one endless scroll row */}
                    <div className="flex flex-wrap items-center gap-1.5 mb-3">
                      {[{ id: "all" as const, name: { zh: "全部", en: "All" } }, ...AD_TEMPLATE_GROUPS].map((g) => (
                        <button
                          key={g.id}
                          onClick={() => setAdTemplateGroup(g.id)}
                          className={`px-2.5 py-1 rounded-full text-xs border transition-all ${
                            adTemplateGroup === g.id
                              ? "border-primary bg-primary/10 text-primary font-medium"
                              : "border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40"
                          }`}
                        >
                          {locale === "zh" ? g.name.zh : g.name.en}
                          {g.id !== "all" && (
                            <span className="ml-1 opacity-60">{listAdTemplates({ group: g.id }).length}</span>
                          )}
                        </button>
                      ))}
                      {/* user-owned templates get their own chip once any exist (template economy) */}
                      {myTemplates.length > 0 && (
                        <button
                          onClick={() => setAdTemplateGroup("mine")}
                          className={`px-2.5 py-1 rounded-full text-xs border transition-all ${
                            adTemplateGroup === "mine"
                              ? "border-primary bg-primary/10 text-primary font-medium"
                              : "border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40"
                          }`}
                        >
                          {t("adTemplateMine")}
                          <span className="ml-1 opacity-60">{myTemplates.length}</span>
                        </button>
                      )}
                      {/* keyword search — at 100+ templates, browsing alone stops scaling */}
                      <input
                        value={adTemplateQuery}
                        onChange={(e) => setAdTemplateQuery(e.target.value)}
                        placeholder={t("adTemplateSearch")}
                        className="w-48 px-2.5 py-1 rounded-full text-xs border border-border/50 bg-muted/20 outline-none focus:border-primary/60 placeholder:text-muted-foreground/60"
                      />
                      {/* AI custom recipe: one LLM call picks from the real preset vocabularies for THIS product */}
                      <button
                        onClick={generateAiTemplate}
                        disabled={aiTplLoading || !formValues.productName.trim() || !isLLMConfigured}
                        title={!formValues.productName.trim() ? t("adTemplateAiNeedName") : !isLLMConfigured ? t("adTemplateAiNeedLlm") : undefined}
                        className="px-2.5 py-1 rounded-full text-xs border border-primary/40 bg-primary/5 text-primary hover:border-primary disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                      >
                        {aiTplLoading ? t("adTemplateAiLoading") : t("adTemplateAiButton")}
                      </button>
                      {/* recipes travel: import a shared .json, export the current pick */}
                      <button
                        onClick={() => { setImportOpen((v) => !v); setMineNotice(""); }}
                        className="px-2.5 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 transition-all"
                      >
                        {t("adTemplateImportButton")}
                      </button>
                      {selectedAdTemplateId && resolveAdTemplate(selectedAdTemplateId) && (
                        <>
                          <button
                            onClick={exportSelectedTemplate}
                            className="px-2.5 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 transition-all"
                          >
                            {t("adTemplateExportButton")}
                          </button>
                          {/* fork/edit the selected recipe — builtin & AI save as new mine rows, mine edits in place */}
                          <button
                            onClick={openTemplateEditor}
                            className="px-2.5 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 transition-all"
                          >
                            {t("adTemplateEditButton")}
                          </button>
                        </>
                      )}
                      {/* pack export lives on the "mine" tab — everything I own, one share file */}
                      {adTemplateGroup === "mine" && myTemplates.length > 0 && (
                        <button
                          onClick={exportMinePack}
                          className="px-2.5 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 transition-all"
                        >
                          {t("adTemplatePackExport")} ({myTemplates.length})
                        </button>
                      )}
                    </div>
                    {importOpen && (
                      <div className="mb-3 space-y-2">
                        <textarea
                          value={importText}
                          onChange={(e) => setImportText(e.target.value)}
                          placeholder={t("adTemplateImportPlaceholder")}
                          rows={4}
                          className="w-full px-3 py-2 rounded-lg text-xs font-mono border border-border/50 bg-muted/20 outline-none focus:border-primary/60 placeholder:text-muted-foreground/60"
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={importAdTemplate}
                            disabled={importBusy || !importText.trim()}
                            className="px-3 py-1 rounded-full text-xs border border-primary/40 bg-primary/5 text-primary hover:border-primary disabled:opacity-40 transition-all"
                          >
                            {t("adTemplateImportConfirm")}
                          </button>
                          <button
                            onClick={() => { setImportOpen(false); setImportText(""); setMineNotice(""); }}
                            className="px-3 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 transition-all"
                          >
                            {t("adTemplateImportCancel")}
                          </button>
                        </div>
                      </div>
                    )}
                    {/* recipe editor — every select is fed from the same vocabularies the server clamps to */}
                    {editorOpen && editorDraft && (
                      <div className="mb-3 p-3 rounded-lg border border-primary/30 bg-primary/[0.03] space-y-3">
                        <p className="text-xs font-medium text-primary">
                          {editorSourceId ? t("adTplEditorTitleEdit") : t("adTplEditorTitleFork")}
                        </p>
                        <div className="grid grid-cols-[3.5rem_1fr_1fr] gap-2">
                          <input
                            value={editorDraft.emoji}
                            onChange={(e) => setEditorDraft((d) => (d ? { ...d, emoji: e.target.value } : d))}
                            placeholder={t("adTplFieldEmoji")}
                            className={EDITOR_INPUT_CLS}
                          />
                          <input
                            value={editorDraft.name.zh}
                            onChange={(e) => setEditorDraft((d) => (d ? { ...d, name: { ...d.name, zh: e.target.value } } : d))}
                            placeholder={t("adTplFieldNameZh")}
                            className={EDITOR_INPUT_CLS}
                          />
                          <input
                            value={editorDraft.name.en}
                            onChange={(e) => setEditorDraft((d) => (d ? { ...d, name: { ...d.name, en: e.target.value } } : d))}
                            placeholder={t("adTplFieldNameEn")}
                            className={EDITOR_INPUT_CLS}
                          />
                        </div>
                        <input
                          value={editorDraft.tagline.zh}
                          onChange={(e) => setEditorDraft((d) => (d ? { ...d, tagline: { ...d.tagline, zh: e.target.value } } : d))}
                          placeholder={t("adTplFieldTagline")}
                          className={EDITOR_INPUT_CLS}
                        />
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplFieldStyle")}
                            <select
                              value={editorDraft.styleType}
                              onChange={(e) => setEditorDraft((d) => (d ? { ...d, styleType: e.target.value } : d))}
                              className={EDITOR_INPUT_CLS}
                            >
                              {styleOptions.map((o) => (
                                <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                              ))}
                            </select>
                          </label>
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplFieldMode")}
                            <select
                              value={editorDraft.videoMode}
                              onChange={(e) =>
                                setEditorDraft((d) => (d ? { ...d, videoMode: e.target.value as AdTemplate["videoMode"] } : d))
                              }
                              className={EDITOR_INPUT_CLS}
                            >
                              {videoModeOptions.map((o) => (
                                <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                              ))}
                            </select>
                          </label>
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplFieldLook")}
                            <select
                              value={editorDraft.look}
                              onChange={(e) => setEditorDraft((d) => (d ? { ...d, look: e.target.value } : d))}
                              className={EDITOR_INPUT_CLS}
                            >
                              {LOOK_PRESETS.map((p) => (
                                <option key={p.id} value={p.id}>{locale === "zh" ? p.name.zh : p.name.en}</option>
                              ))}
                            </select>
                          </label>
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplFieldGroup")}
                            <select
                              value={editorDraft.group}
                              onChange={(e) =>
                                setEditorDraft((d) => (d ? { ...d, group: e.target.value as AdTemplateGroupId } : d))
                              }
                              className={EDITOR_INPUT_CLS}
                            >
                              {AD_TEMPLATE_GROUPS.map((g) => (
                                <option key={g.id} value={g.id}>{locale === "zh" ? g.name.zh : g.name.en}</option>
                              ))}
                            </select>
                          </label>
                        </div>
                        <div>
                          <p className="text-[11px] text-muted-foreground mb-1">{t("adTplFieldCamera")}</p>
                          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                            {AD_TEMPLATE_EDIT_VOCAB.shotTypes.map((st) => (
                              <label key={st} className="text-[11px] text-muted-foreground">
                                {t(SHOT_LABEL_KEYS[st] ?? st)}
                                <select
                                  value={editorDraft.cameraPlan[st] ?? ""}
                                  onChange={(e) =>
                                    setEditorDraft((d) => {
                                      if (!d) return d;
                                      const plan = { ...d.cameraPlan };
                                      if (e.target.value) plan[st] = e.target.value;
                                      else delete plan[st];
                                      return { ...d, cameraPlan: plan };
                                    })
                                  }
                                  className={EDITOR_INPUT_CLS}
                                >
                                  <option value="">{t("adTplCameraAuto")}</option>
                                  {CAMERA_PRESETS.map((p) => (
                                    <option key={p.id} value={p.id}>{locale === "zh" ? p.name.zh : p.name.en}</option>
                                  ))}
                                </select>
                              </label>
                            ))}
                          </div>
                        </div>
                        <div className="flex flex-wrap items-end gap-2">
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplComposeCaption")}
                            <select
                              value={editorDraft.compose.captionPreset}
                              onChange={(e) =>
                                setEditorDraft((d) =>
                                  d ? { ...d, compose: { ...d.compose, captionPreset: e.target.value as CaptionPresetId } } : d
                                )
                              }
                              className={EDITOR_INPUT_CLS}
                            >
                              {CAPTION_PRESET_IDS.map((id) => (
                                <option key={id} value={id}>{locale === "zh" ? CAPTION_LABELS[id].zh : CAPTION_LABELS[id].en}</option>
                              ))}
                            </select>
                          </label>
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplComposeBgm")}
                            <select
                              value={editorDraft.compose.bgm}
                              onChange={(e) =>
                                setEditorDraft((d) =>
                                  d ? { ...d, compose: { ...d.compose, bgm: e.target.value as AdTemplate["compose"]["bgm"] } } : d
                                )
                              }
                              className={EDITOR_INPUT_CLS}
                            >
                              {AD_TEMPLATE_EDIT_VOCAB.bgm.map((b) => (
                                <option key={b} value={b}>{locale === "zh" ? BGM_LABELS[b]?.zh ?? b : BGM_LABELS[b]?.en ?? b}</option>
                              ))}
                            </select>
                          </label>
                          <label className="text-[11px] text-muted-foreground">
                            {t("adTplComposeQuality")}
                            <select
                              value={editorDraft.compose.quality ?? ""}
                              onChange={(e) =>
                                setEditorDraft((d) => {
                                  if (!d) return d;
                                  const compose = { ...d.compose };
                                  if (e.target.value) compose.quality = e.target.value as NonNullable<AdTemplate["compose"]["quality"]>;
                                  else delete compose.quality;
                                  return { ...d, compose };
                                })
                              }
                              className={EDITOR_INPUT_CLS}
                            >
                              <option value="">{t("adTplComposeQualityDefault")}</option>
                              {AD_TEMPLATE_EDIT_VOCAB.quality.map((q) => (
                                <option key={q} value={q}>{locale === "zh" ? QUALITY_LABELS[q]?.zh ?? q : QUALITY_LABELS[q]?.en ?? q}</option>
                              ))}
                            </select>
                          </label>
                          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground pb-1.5">
                            <input
                              type="checkbox"
                              checked={editorDraft.compose.bgmDuck}
                              onChange={(e) =>
                                setEditorDraft((d) => (d ? { ...d, compose: { ...d.compose, bgmDuck: e.target.checked } } : d))
                              }
                            />
                            {t("adTplComposeDuck")}
                          </label>
                          <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground pb-1.5">
                            <input
                              type="checkbox"
                              checked={editorDraft.compose.productCard ?? false}
                              onChange={(e) =>
                                setEditorDraft((d) => (d ? { ...d, compose: { ...d.compose, productCard: e.target.checked } } : d))
                              }
                            />
                            {t("adTplComposeCard")}
                          </label>
                        </div>
                        <textarea
                          value={editorDraft.scriptHint.zh}
                          onChange={(e) => setEditorDraft((d) => (d ? { ...d, scriptHint: { zh: e.target.value } } : d))}
                          placeholder={t("adTplFieldHint")}
                          rows={2}
                          className={EDITOR_INPUT_CLS}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={saveEditorTemplate}
                            disabled={editorBusy}
                            className="px-3 py-1 rounded-full text-xs border border-primary/40 bg-primary/5 text-primary hover:border-primary disabled:opacity-40 transition-all"
                          >
                            {editorSourceId ? t("adTplEditorSaveEdit") : t("adTplEditorSaveFork")}
                          </button>
                          <button
                            onClick={() => { setEditorOpen(false); setEditorDraft(null); setMineNotice(""); }}
                            className="px-3 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 transition-all"
                          >
                            {t("adTemplateImportCancel")}
                          </button>
                        </div>
                      </div>
                    )}
                    {aiTplError && <p className="text-xs text-destructive mb-2">{aiTplError}</p>}
                    {mineNotice && <p className="text-xs text-muted-foreground mb-2">{mineNotice}</p>}
                    {/* wrapping grid capped in height — a 100-card library can't live on one scroll row */}
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 max-h-[30rem] overflow-y-auto pb-1 pr-1">
                      <button
                        onClick={() => setSelectedAdTemplateId("")}
                        className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all ${
                          selectedAdTemplateId === ""
                            ? "border-primary bg-primary/10"
                            : "border-border/50 bg-muted/20 hover:border-primary/40"
                        }`}
                      >
                        <span className={`text-sm font-medium ${selectedAdTemplateId === "" ? "text-primary" : "text-foreground"}`}>
                          {t("adTemplateNone")}
                        </span>
                        <span className="text-[11px] text-muted-foreground mt-0.5">{t("adTemplateNoneDesc")}</span>
                      </button>
                      {/* the AI-generated custom recipe renders as a first-class card at the front */}
                      {customAdTemplate && adTemplateGroup !== "mine" && (
                        <button
                          onClick={() => pickAdTemplate(CUSTOM_AD_TEMPLATE_ID)}
                          className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all ${
                            selectedAdTemplateId === CUSTOM_AD_TEMPLATE_ID
                              ? "border-primary bg-primary/10"
                              : "border-primary/40 bg-primary/5 hover:border-primary"
                          }`}
                        >
                          <span className={`text-sm font-medium ${selectedAdTemplateId === CUSTOM_AD_TEMPLATE_ID ? "text-primary" : "text-foreground"}`}>
                            {customAdTemplate.emoji} {locale === "zh" ? customAdTemplate.name.zh : customAdTemplate.name.en}
                            <span className="ml-1 text-[10px] px-1 py-0.5 rounded bg-primary/15 text-primary align-middle">AI</span>
                            {/* save-for-reuse chip (span, not button — cards are buttons already) */}
                            {!aiTplSaved && (
                              <span
                                onClick={(e) => { e.stopPropagation(); saveAiTemplateToMine(); }}
                                className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full border border-primary/40 text-primary hover:bg-primary/10 align-middle cursor-pointer"
                              >
                                {t("adTemplateSaveMine")}
                              </span>
                            )}
                          </span>
                          <span className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
                            {locale === "zh" ? customAdTemplate.tagline.zh : customAdTemplate.tagline.en}
                          </span>
                        </button>
                      )}
                      {/* user-owned templates: shown under "all" and their own chip, searchable like builtins */}
                      {(adTemplateGroup === "all" || adTemplateGroup === "mine") &&
                        myTemplates
                          .filter((tpl) => {
                            const q = adTemplateQuery.trim().toLowerCase();
                            if (!q) return true;
                            return `${tpl.name.zh} ${tpl.name.en} ${tpl.tagline.zh} ${tpl.tagline.en}`.toLowerCase().includes(q);
                          })
                          .map((tpl) => (
                            <button
                              key={tpl.id}
                              onClick={() => pickAdTemplate(tpl.id)}
                              className={`relative flex flex-col items-start p-3 rounded-lg border text-left transition-all ${
                                selectedAdTemplateId === tpl.id
                                  ? "border-primary bg-primary/10"
                                  : "border-border/50 bg-muted/20 hover:border-primary/40"
                              }`}
                            >
                              <span className={`text-sm font-medium ${selectedAdTemplateId === tpl.id ? "text-primary" : "text-foreground"}`}>
                                {tpl.emoji} {locale === "zh" ? tpl.name.zh : tpl.name.en}
                                <span className="ml-1 text-[10px] px-1 py-0.5 rounded bg-primary/15 text-primary align-middle">
                                  {t("adTemplateMine")}
                                </span>
                              </span>
                              <span className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
                                {locale === "zh" ? tpl.tagline.zh : tpl.tagline.en}
                              </span>
                              <span
                                onClick={(e) => { e.stopPropagation(); deleteMyTemplate(tpl.id); }}
                                title={t("adTemplateDeleteTitle")}
                                className="absolute top-1.5 right-1.5 text-[11px] leading-none px-1 py-0.5 rounded text-muted-foreground/60 hover:text-destructive hover:bg-destructive/10 cursor-pointer"
                              >
                                ✕
                              </span>
                            </button>
                          ))}
                      {adTemplateGroup !== "mine" && listAdTemplates({ group: adTemplateGroup, category: formValues.category, query: adTemplateQuery }).map((tpl) => (
                        <button
                          key={tpl.id}
                          onClick={() => pickAdTemplate(tpl.id)}
                          className={`flex flex-col items-start p-3 rounded-lg border text-left transition-all ${
                            selectedAdTemplateId === tpl.id
                              ? "border-primary bg-primary/10"
                              : "border-border/50 bg-muted/20 hover:border-primary/40"
                          }`}
                        >
                          <span className={`text-sm font-medium ${selectedAdTemplateId === tpl.id ? "text-primary" : "text-foreground"}`}>
                            {tpl.emoji} {locale === "zh" ? tpl.name.zh : tpl.name.en}
                            {formValues.category && tpl.goodFor?.includes(formValues.category as AdTemplateCategory) && (
                              <span className="ml-1 text-[10px] px-1 py-0.5 rounded bg-primary/15 text-primary align-middle">
                                {t("adTemplateGoodMatch")}
                              </span>
                            )}
                          </span>
                          <span className="text-[11px] text-muted-foreground mt-0.5 line-clamp-2">
                            {locale === "zh" ? tpl.tagline.zh : tpl.tagline.en}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </details>
            </CardContent>
          </Card>

          {/* 示例商品：一键预填简报（图片抓成本地 File，文本直接填好） */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">{t("sourceExampleLead")}</span>
            {getExampleProducts(locale).map((ex) => (
              <button
                key={ex.id}
                type="button"
                onClick={() => void fillExample(ex)}
                className="px-2.5 py-1 rounded-full text-xs border border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40 hover:text-foreground transition-all"
              >
                {ex.name} ¥{ex.price}
              </button>
            ))}
          </div>

          {/* error message */}
          {error && (
            <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/20">
              <p className="text-sm text-destructive flex items-center gap-2">
                <LuCircleAlert className="w-4 h-4 shrink-0" />
                {error}
              </p>
            </div>
          )}

          {/* progress bar */}
          {progress && (
            <div>
              <div className="h-2 bg-muted/30 rounded-full overflow-hidden">
                <div
                  className="h-full brand-gradient transition-all duration-500 rounded-full"
                  style={{ width: `${progress.percent}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground text-center mt-2">
                {progress.message}
              </p>
            </div>
          )}

          {/* 唯一的表单：与 /start 共用同一份创作简报组件 */}
          <CreationBriefForm
            submitLabel={isSubmitting ? t("submitProcessing") : t("submitGenerate")}
            showAdvanced
            /* 本页用 onSubmitForm：创建项目还需要商品名/图片/来源字段 */
            onSubmit={() => { /* 见 onSubmitForm */ }}
            onSubmitForm={handleSubmitForm}
            onValuesChange={setFormValues}
            prefill={prefill}
            prefillKey={prefillKey}
            onImportLink={handleImportLink}
            importing={importing}
            importError={importError}
            linkImported={importedImages.length > 0}
            disabled={isSubmitting}
          />

          {/* 需要用户显式选风格：不是错误，是继续生成所缺的一步 */}
          {stylePrompt && <StyleChoicePrompt requirement={stylePrompt} onPick={pickStyle} busy={isSubmitting} />}
        </div>
      </main>
    </div>
  );
}

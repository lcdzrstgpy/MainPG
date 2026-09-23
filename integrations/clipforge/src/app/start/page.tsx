"use client";

/**
 * MainPG 里的 AI 视频工作台，也是唯一的项目创建入口。
 *
 * 商品图 / 商品链接 / 一句话主题三种来源都落到同一份「创作简报」（CreationBrief）：
 * 表单状态、默认值和请求体只有一份；五种出片方案在创建时选定并写进 creationBrief.outputScheme，
 * 脚本请求只由 buildScriptRequest 构造。未配置模型时只给设置页引导，不再内联填 Key。
 */

import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useProductLibraryStore } from "@/lib/stores/product-library-store";
import { useCharacterStore } from "@/lib/stores/project-store";
import { getExampleProducts, type ExampleProduct } from "@/lib/examples";
import { useT, useLocale } from "@/lib/i18n";
import { formatRelativeTime } from "@/lib/relative-time";
import { classifyTrendTitle, pickDailyTrend, TREND_CATEGORY_IDS } from "@/lib/trends";
import type { TrendTopic, TrendCategoryId } from "@/lib/trends";
import {
  CreationBriefForm,
  type CreationBriefFormPrefill,
  type CreationBriefFormValues,
} from "@/components/project-creation/creation-brief-form";
import { validateCreationBriefForm, type VideoModeId } from "@/components/project-creation/creation-brief-defaults";
import { fetchImagesAsFiles, importProductSource, isValidProductUrl } from "@/components/project-creation/link-import";
import { buildScriptRequest } from "@/components/project-creation/build-script-request";
import {
  missingStyleRequirement,
  parseStyleRequirement,
  type ScriptStyleRequirement,
} from "@/components/project-creation/script-style-requirement";
import { StyleChoicePrompt } from "@/components/project-creation/style-choice-prompt";
import { recordStrategySelected } from "@/components/project-creation/creation-events";
import { sanitizeCreationBrief, type CreationBrief, type OutputStrategy } from "@/lib/creation-brief";
import { buildTopicScriptRequest } from "@/lib/creation-submit";
import { sanitizeCreativeIntent } from "@/lib/production-system";
import {
  CLONE_PREFILL_STORAGE_KEY,
  parseClonePrefill,
  parseStartPrefill,
  type CreationEntryId,
} from "@/lib/creation-entry-prefill";

/** How many trend chips are shown at once; "shuffle" pages through the full board. */
const TRENDS_PAGE_SIZE = 8;

/** localStorage keys for the daily-persona picker (device-local, no account concept) */
const DAILY_PERSONA_KEY = "clipforge_daily_persona";
const DAILY_LAST_KEY = "clipforge_daily_last";

/** Local calendar date (YYYY-MM-DD) — "today" for the daily-pick marker follows the user's clock. */
function localDateStamp(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

interface RecentProject {
  id: string;
  name: string;
  productName: string | null;
  status: string;
  updatedAt: string | null;
}

/** 已经建好项目、只差脚本的待重试请求（409 需要用户显式选风格时用）。 */
interface PendingScript {
  kind: "script";
  projectId: string;
  brief: CreationBrief;
  productName: string;
  category: string;
  description: string;
  /** 一句话主题：主题链路只认它，绝不拿空的 productName 去打带货脚本接口 */
  topic: string;
  productImages: string[];
  videoMode: VideoModeId;
  /** 爆款复刻交接的参考镜头节奏骨架：有值时随脚本请求下发，风格重试也要带上 */
  referenceStructure?: string;
}

/** 还没建项目就需要用户先选风格的提交内容。 */
interface PendingForm {
  kind: "form";
  values: CreationBriefFormValues;
}

type PendingCreation = PendingScript | PendingForm;

/** 出片策略只影响跳转：只有 draft 兼容旧的 ?auto=1 断点恢复，其它策略不隐式启动流水线。 */
function scriptPath(projectId: string, strategy: OutputStrategy): string {
  return `/project/${projectId}/script${strategy === "draft" ? "?auto=1" : ""}`;
}

/**
 * 次级入口的预填 → 主入口表单预填（设计 §4：三种来源都是同一个入口的预填）。
 *
 * `prefill` 之外还带两样「只用于创建/生成、不进表单字段」的复刻交接内容：
 * `referenceStructure`（脚本请求）与 `referenceVideoUrl`（项目 sourceVideoUrl）。
 */
export interface StartPrefillResolution {
  /** 表单来源，与 `CreationBrief.inputMode` 同值——不存在第二套来源枚举 */
  inputMode: CreationEntryId;
  /** 推给共享表单的预填；库内商品与复刻商品图由页面补齐 */
  prefill: CreationBriefFormPrefill;
  /** 商品库条目 id：页面据此读库内的名称/卖点/图片 */
  productId?: string;
  /** 爆款复刻已落盘的商品图地址：页面抓成 File（纯函数不发请求、不碰 DOM） */
  productImages?: string[];
  /** 爆款复刻的参考镜头节奏骨架：生成脚本时透传 */
  referenceStructure?: string;
  /** 爆款复刻的参考视频地址：创建项目时写入 sourceVideoUrl */
  referenceVideoUrl?: string;
}

/**
 * URL 预填参数 + 复刻暂存 → 一次表单预填。认不出的组合一律返回 null（绝不抛错），
 * 调用方据此保持空表单，而不是猜一个来源填进去。
 */
export function resolveStartPrefill(
  search: string,
  cloneStorageValue?: string | null
): StartPrefillResolution | null {
  const params = parseStartPrefill(search);

  if (params.entry === "topic") {
    if (!params.topic) return null;
    return { inputMode: "topic", prefill: { brief: { inputMode: "topic" }, topic: params.topic } };
  }

  if (params.entry === "clone") {
    // 暂存缺失/损坏时宁可空表单：只带 query 文本的半份复刻简报会让用户以为节奏骨架还在
    const payload = parseClonePrefill(cloneStorageValue ?? null);
    if (!payload) return null;
    return {
      inputMode: "clone",
      prefill: {
        productName: params.productName,
        sellingPoints: params.sellingPoints,
        brief: payload.brief,
      },
      ...(payload.productImages?.length ? { productImages: payload.productImages } : {}),
      ...(payload.referenceStructure ? { referenceStructure: payload.referenceStructure } : {}),
      ...(payload.referenceVideoUrl ? { referenceVideoUrl: payload.referenceVideoUrl } : {}),
    };
  }

  // 商品库：库页的「做视频」按钮曾经只带 ?productId=，与 ?entry=product-library&productId= 同一分支
  if (params.productId) {
    return {
      inputMode: "product-library",
      prefill: { brief: { inputMode: "product-library" } },
      productId: params.productId,
    };
  }

  return null;
}

/** 读取爆款复刻暂存；浏览器禁用本地存储时按「没有交接」处理。 */
function readClonePrefillStorage(): string | null {
  try {
    return localStorage.getItem(CLONE_PREFILL_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** 暂存用过即清：同一份交接不会被下一次刷新重放，用户之后的改动也不会被覆盖。 */
function clearClonePrefillStorage(): void {
  try {
    localStorage.removeItem(CLONE_PREFILL_STORAGE_KEY);
  } catch {
    /* storage unavailable → nothing to clear */
  }
}

export default function StartPage() {
  const router = useRouter();
  const t = useT("start");
  const locale = useLocale();
  const { llm } = useSettingsStore();
  const characters = useCharacterStore((s) => s.characters);
  const llmReady = llm.apiKey.trim().length > 0;
  // example products follow the UI language
  const examples = getExampleProducts(locale);

  const [busy, setBusy] = useState(false);
  const [busySteps, setBusySteps] = useState<string[]>([]);
  // which step of the busy takeover is running (index into busySteps)
  const [stageIdx, setStageIdx] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // 需要用户显式选风格（本地未选 / 接口 409 无数据可推荐）
  const [stylePrompt, setStylePrompt] = useState<ScriptStyleRequirement | null>(null);
  // 表单预填通道（商品库、热点、示例商品、链接导入）
  const [prefill, setPrefill] = useState<CreationBriefFormPrefill | undefined>();
  const [prefillKey, setPrefillKey] = useState("");
  // 商品链接导入
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importedImages, setImportedImages] = useState<string[]>([]);
  const [recent, setRecent] = useState<RecentProject[]>([]);
  const [trends, setTrends] = useState<TrendTopic[]>([]);
  const [trendsSource, setTrendsSource] = useState<string>("");
  const [trendsPage, setTrendsPage] = useState(0);
  const [trendsCat, setTrendsCat] = useState<"all" | TrendCategoryId>("all");
  // daily-persona picker state (persisted per device)
  const [dailyPersona, setDailyPersona] = useState("");
  const [dailyLast, setDailyLast] = useState<{ date: string; topic: string } | null>(null);
  const [dailyMsg, setDailyMsg] = useState<string>("");
  // first-visit guide card (dismiss persists per device; read after mount to keep SSR stable)
  const [showGuide, setShowGuide] = useState(false);
  useEffect(() => {
    // deferred to a microtask: same pattern as the daily-persona loader (no sync setState in effect)
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      try {
        if (localStorage.getItem("clipforge_guide_dismissed") !== "1") setShowGuide(true);
      } catch { /* storage unavailable → keep hidden */ }
    });
    return () => { cancelled = true; };
  }, []);
  const dismissGuide = () => {
    setShowGuide(false);
    try { localStorage.setItem("clipforge_guide_dismissed", "1"); } catch { /* ignore */ }
  };

  const briefRef = useRef<HTMLDivElement>(null);
  const pendingRef = useRef<PendingCreation | null>(null);

  const pushPrefill = useCallback((next: CreationBriefFormPrefill) => {
    setPrefill(next);
    setPrefillKey(crypto.randomUUID());
  }, []);

  // 次级入口交接：商品库 / 一句话主题 / 爆款复刻只把「一份预填简报」放在 query 里（复刻另带
  // localStorage 暂存），由本页消费一次，落到同一个共享表单上——不再各自创建项目。
  const { products: libraryProducts } = useProductLibraryStore();
  const prefilledRef = useRef(false);
  // 复刻交接的参考结构/来源视频不属于表单字段，预填时暂存在这里，创建与生成时透传
  const cloneRef = useRef<{ referenceStructure?: string; referenceVideoUrl?: string }>({});
  useEffect(() => {
    if (prefilledRef.current) return;
    const resolution = resolveStartPrefill(window.location.search, readClonePrefillStorage());
    if (!resolution) return;

    if (resolution.inputMode === "product-library") {
      const product = libraryProducts.find((p) => p.id === resolution.productId);
      if (!product) return; // store not hydrated yet (effect re-runs) or stale id
      prefilledRef.current = true;
      void (async () => {
        // fetch library images into File objects; local blob URLs from other pages may be dead — text stays filled either way
        const files = await fetchImagesAsFiles(product.images);
        pushPrefill({
          ...resolution.prefill,
          productName: product.name,
          sellingPoints: product.description ?? "",
          ...(files.length ? { images: files } : {}),
        });
      })();
      return;
    }

    prefilledRef.current = true;

    if (resolution.inputMode === "clone") {
      // 交接只发生一次：参考结构留给创建/生成，暂存立刻清掉，用户之后自己的改动不会再被覆盖
      cloneRef.current = {
        referenceStructure: resolution.referenceStructure,
        referenceVideoUrl: resolution.referenceVideoUrl,
      };
      clearClonePrefillStorage();
      void (async () => {
        const files = resolution.productImages?.length
          ? await fetchImagesAsFiles(resolution.productImages)
          : [];
        pushPrefill({ ...resolution.prefill, ...(files.length ? { images: files } : {}) });
      })();
      return;
    }

    pushPrefill(resolution.prefill);
  }, [libraryProducts, pushPrefill]);

  // fetch recent projects to give returning users a "continue" entry point (replaces the old homepage project list so they are not left stranded)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/project");
        const data = res.ok ? await res.json() : [];
        const list: RecentProject[] = Array.isArray(data) ? data : [];
        // sort by updatedAt desc so "recent" truly reflects last-edited order (null/invalid timestamps sink to the end)
        const ts = (p: RecentProject) => {
          if (!p.updatedAt) return 0;
          const time = new Date(p.updatedAt).getTime();
          return Number.isFinite(time) ? time : 0;
        };
        if (!cancelled) setRecent([...list].sort((a, b) => ts(b) - ts(a)).slice(0, 4));
      } catch {
        /* ignore */
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // trend radar ("what to post today"): Chinese UI reads domestic boards, English UI reads Google Trends.
  // Failure or an empty board silently hides the section — the landing page must never block on it.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(locale === "zh" ? "/api/trends?source=cn&limit=48" : "/api/trends?geo=US&limit=48");
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled || !Array.isArray(data.topics)) return;
        // Curate for sellable content: keep only topics that classify into a creator
        // category, and drop "society" (news/incidents/politics) — raw boards lead with
        // headlines that make no sense as commerce videos and are a compliance risk for
        // AI-generated content.
        setTrends(
          data.topics.filter((tp: TrendTopic) => {
            if (typeof tp?.title !== "string" || !tp.title.trim()) return false;
            const cat = classifyTrendTitle(tp.title);
            return cat !== null && cat !== "society";
          })
        );
        setTrendsSource(typeof data.source === "string" ? data.source : "");
        setTrendsPage(0);
      } catch {
        /* keyless free endpoint — silent degradation */
      }
    })();
    return () => { cancelled = true; };
  }, [locale]);

  // load the persisted daily persona + today's marker once on mount
  // (deferred to a microtask: hydrating from localStorage after paint keeps SSR markup stable
  // and satisfies the no-sync-setState-in-effect rule)
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      try {
        setDailyPersona(localStorage.getItem(DAILY_PERSONA_KEY) || "");
        const last = JSON.parse(localStorage.getItem(DAILY_LAST_KEY) || "null");
        if (last && typeof last.date === "string" && typeof last.topic === "string") setDailyLast(last);
      } catch {
        /* corrupted storage → start fresh */
      }
    });
    return () => { cancelled = true; };
  }, []);

  // categories present on the current board (chips render only for non-empty ones; hidden entirely when nothing classifies)
  const trendsCats = TREND_CATEGORY_IDS.filter((id) => trends.some((tp) => classifyTrendTitle(tp.title) === id));
  const catFiltered = trendsCat === "all" ? trends : trends.filter((tp) => classifyTrendTitle(tp.title) === trendsCat);

  // current slice of the filtered board; "shuffle" cycles through pages
  const trendsPageCount = Math.max(1, Math.ceil(catFiltered.length / TRENDS_PAGE_SIZE));
  const trendsShown = catFiltered.slice(
    (trendsPage % trendsPageCount) * TRENDS_PAGE_SIZE,
    (trendsPage % trendsPageCount) * TRENDS_PAGE_SIZE + TRENDS_PAGE_SIZE
  );
  const trendsSourceLabel =
    trendsSource === "douyin" ? t("trendsSourceDouyin") : trendsSource === "toutiao" ? t("trendsSourceToutiao") : "Google Trends";

  const scrollToBrief = () => {
    requestAnimationFrame(() => briefRef.current?.scrollIntoView({ block: "start", behavior: "smooth" }));
  };

  // tap a trend → prefill it as a one-sentence topic and bring the creation form into view
  const pickTrend = (tp: TrendTopic) => {
    pushPrefill({ brief: { inputMode: "topic" }, topic: tp.title });
    scrollToBrief();
  };

  // daily pick: score the full board against the persona keywords, prefill the winner, remember today's pick
  const runDailyPick = () => {
    const pick = pickDailyTrend(trends, dailyPersona);
    if (!pick) return;
    pickTrend(pick.topic);
    setDailyMsg(t(pick.matched ? "dailyPickedMatched" : "dailyPickedFallback").replace("{topic}", pick.topic.title));
    const last = { date: localDateStamp(), topic: pick.topic.title };
    setDailyLast(last);
    try {
      localStorage.setItem(DAILY_LAST_KEY, JSON.stringify(last));
    } catch {
      /* storage full/blocked — the marker is a convenience, not a requirement */
    }
  };

  const onPersonaChange = (v: string) => {
    setDailyPersona(v);
    try {
      localStorage.setItem(DAILY_PERSONA_KEY, v);
    } catch {
      /* ignore */
    }
  };

  // navigate to the appropriate step based on project status
  const stepFor = (status: string) =>
    status === "done" || status === "composing" || status === "video" ? "video" : status === "assets" ? "assets" : "script";

  // map project status to the short stage-label i18n key shown on recent-project cards
  const stageKeyFor = (status: string) =>
    status === "done" ? "pjStageDone" : status === "video" || status === "composing" ? "pjStageVideo" : status === "assets" ? "pjStageAssets" : "pjStageScript";

  // one-click fill example: fetch the example image as a File into the form + populate name/selling points
  const fillExample = useCallback(async (ex: ExampleProduct) => {
    const files = await fetchImagesAsFiles([ex.image]);
    pushPrefill({
      productName: ex.name,
      category: ex.category,
      sellingPoints: ex.sellingPoints,
      brief: { inputMode: "upload" },
      ...(files.length ? { images: files } : {}),
    });
    scrollToBrief();
  }, [pushPrefill]);

  // paste a product URL → server parses title / price / images → prefill the brief for review before creating
  const handleImportLink = useCallback(async (url: string) => {
    if (!isValidProductUrl(url)) {
      setImportError(t("errIngest"));
      return;
    }
    setImportError("");
    setImporting(true);
    try {
      const result = await importProductSource(url);
      if (!result.ok) throw new Error(result.message || t("errIngest"));
      const { source } = result;
      setImportedImages(source.imageUrls);
      // 链接导入只预填：项目由统一入口带着简报创建
      pushPrefill({
        productName: source.productName,
        sellingPoints: source.sellingPoints,
        linkUrl: source.linkUrl,
        brief: { inputMode: "link" },
        ...(source.files.length ? { images: source.files } : {}),
      });
    } catch (e) {
      setImportError(e instanceof Error ? e.message : t("errIngest"));
    } finally {
      setImporting(false);
    }
  }, [pushPrefill, t]);

  // read LLM config live from the store so a freshly saved Key is used in the same tick
  const llmConfig = () => {
    const l = useSettingsStore.getState().llm;
    return { baseUrl: l.baseUrl, apiKey: l.apiKey, model: l.model, visionModel: l.visionModel };
  };

  const characterFor = (characterId?: string) => {
    if (!characterId) return undefined;
    const c = characters.find((item) => item.id === characterId);
    return c ? { id: c.id, name: c.name, appearance: c.appearance || "", voiceStyle: c.voiceProfile?.style } : undefined;
  };

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
    const fd = new FormData();
    files.forEach((image) => fd.append("files", image.file));
    fd.append("projectId", projectId);
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    if (!res.ok) throw new Error(t("errUpload"));
    const data: { paths?: string[] } = await res.json().catch(() => ({}));
    return Array.isArray(data.paths) ? data.paths : [];
  };

  /**
   * 脚本请求：唯一构造器是 buildScriptRequest，且必须带用户显式选择的风格。
   * 409 needs_explicit_style 不是失败，而是「请用户选一个风格」——记下待重试请求并返回。
   *
   * 一句话主题改为走既有主题引擎 `/api/topic/script`：拿空的 productName 打带货脚本接口
   * 只会得到「请填写商品名称」的假失败。
   */
  const requestScript = async (pending: PendingScript): Promise<"ok" | "needs-style"> => {
    const isTopic = pending.brief.inputMode === "topic";
    const res = isTopic
      ? await fetch("/api/topic/script", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            buildTopicScriptRequest({
              projectId: pending.projectId,
              topic: pending.topic,
              brief: pending.brief,
              llmConfig: llmConfig(),
            })
          ),
        })
      : await fetch("/api/llm/script", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            buildScriptRequest({
              brief: pending.brief,
              projectId: pending.projectId,
              productName: pending.productName,
              category: pending.category,
              productDescription: pending.description,
              productImages: pending.productImages,
              videoMode: pending.videoMode,
              llmConfig: llmConfig(),
              character: characterFor(pending.brief.characterId),
              // 复刻交接的参考节奏骨架：有值才下发（buildScriptRequest 省略未给出的可选键）
              ...(pending.referenceStructure ? { referenceStructure: pending.referenceStructure } : {}),
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
    throw new Error(data.error ? `${t("errScript")}: ${data.error}` : t("errScript"));
  };

  /** 表单提交：创建项目（带 creationBrief）→ 上传商品图 → 生成脚本 → 跳转脚本页。 */
  const runCreation = async (values: CreationBriefFormValues) => {
    if (busy) return;
    // 提交前先过共享校验，确保来源字段完整。
    const validation = validateCreationBriefForm({
      productName: values.productName,
      images: values.images,
      topic: values.topic,
      inputMode: values.brief.inputMode,
      linkImported: importedImages.length > 0,
    });
    if (!validation.valid) {
      setError(Object.values(validation.errors).filter(Boolean).join("；"));
      return;
    }
    if (!llmReady) {
      setError(t("errNeedLlm"));
      return;
    }
    const brief = sanitizeCreationBrief(values.brief);
    // 没有显式风格就不提交：先让用户选，避免接口把 auto 当成推荐失败
    if (!brief.styleType) {
      pendingRef.current = { kind: "form", values };
      setStylePrompt(missingStyleRequirement());
      return;
    }
    setStylePrompt(null);
    setError(null);

    const isTopic = brief.inputMode === "topic";
    const topicText = values.topic.trim();
    const productName = isTopic ? topicText : values.productName.trim();
    const description = isTopic ? topicText : values.sellingPoints.trim();
    const steps = [t("stageCreate"), ...(!isTopic && values.images.length ? [t("stageUpload")] : []), t("stageScript")];

    setBusy(true);
    setBusySteps(steps);
    setStageIdx(0);
    try {
      const projectRes = await fetch("/api/project", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: isTopic ? topicText : t("projectName", { name: productName }),
          productName,
          productCategory: values.category,
          productDescription: description,
          productImages: [],
          creationBrief: brief,
          // 表单产出的画面约束（再经共享 sanitizer）与角色绑定：创建时就写进项目，
          // 后续生图/生视频阶段才不会丢失
          creativeIntent: sanitizeCreativeIntent(values.creativeIntent),
          ...(values.visualBible ? { visualBible: values.visualBible } : {}),
          ...(brief.characterId ? { characterId: brief.characterId } : {}),
          // 一句话主题：项目类型与主题文本落库，脚本链路据此走主题引擎
          ...(isTopic ? { contentType: "topic" as const, topic: topicText } : {}),
          // 复刻交接的参考视频：创建时落到项目 sourceVideoUrl，供后续复刻/修复阶段取用
          ...(cloneRef.current.referenceVideoUrl
            ? { sourceVideoUrl: cloneRef.current.referenceVideoUrl }
            : {}),
        }),
      });
      if (!projectRes.ok) {
        const errData: { error?: string } = await projectRes.json().catch(() => ({}));
        throw new Error(errData.error ? `${t("errProjectCreate")}: ${errData.error}` : t("errProjectCreate"));
      }
      const project: { id: string } = await projectRes.json();

      // 策略选择本身要留痕（project_created 之外的单列事件）；失败不影响创建
      try {
        await recordStrategySelected({ projectId: project.id, creationBrief: brief });
      } catch {
        /* observability only */
      }

      let productImages: string[] = [];
      if (values.images.length) {
        setStageIdx(1);
        productImages = await uploadImages(project.id, values.images);
      } else if (importedImages.length) {
        // 链接导入抓到的商品图（服务端解析得到），没有本地文件时直接沿用
        productImages = importedImages;
      }
      if (productImages.length) await patchProject(project.id, { productImages });

      setStageIdx(steps.length - 1);
      const outcome = await requestScript({
        kind: "script",
        projectId: project.id,
        brief,
        productName,
        category: values.category,
        description,
        topic: topicText,
        productImages,
        videoMode: values.videoMode,
        ...(cloneRef.current.referenceStructure
          ? { referenceStructure: cloneRef.current.referenceStructure }
          : {}),
      });
      if (outcome === "needs-style") {
        setBusy(false);
        return;
      }
      setBusy(false);
      router.push(scriptPath(project.id, brief.outputStrategy));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errGeneric"));
      setBusy(false);
      setStageIdx(0);
    }
  };

  /** 用户选完风格：没建项目就带着新风格重新走创建；已建项目就只重试脚本请求。 */
  const pickStyle = async (styleType: string) => {
    const pending = pendingRef.current;
    if (!pending || busy) return;
    if (pending.kind === "form") {
      setStylePrompt(null);
      await runCreation({
        ...pending.values,
        brief: sanitizeCreationBrief({ ...pending.values.brief, styleType, styleSource: "explicit" }),
      });
      return;
    }
    const brief = sanitizeCreationBrief({ ...pending.brief, styleType, styleSource: "explicit" });
    setStylePrompt(null);
    setError(null);
    setBusy(true);
    setBusySteps([t("stageScript")]);
    setStageIdx(0);
    try {
      // 库里的简报也要跟着用户的实际选择走（失败不阻断这次生成）
      await patchProject(pending.projectId, { creationBrief: brief });
      // 原样带上 pending：复刻交接的 referenceStructure 不会在风格重试时丢失
      const outcome = await requestScript({ ...pending, brief });
      if (outcome === "needs-style") {
        setBusy(false);
        return;
      }
      setBusy(false);
      router.push(scriptPath(pending.projectId, brief.outputStrategy));
    } catch (e) {
      setError(e instanceof Error ? e.message : t("errGeneric"));
      setBusy(false);
      setStageIdx(0);
    }
  };

  return (
    <div className="cf-root">
      <style>{`
        .cf-root{--teal:#a78bfa;--ink:#ffffff;--text:#EDEFF4;--dim:#98A2B3;--muted:#5A6473;--surface:rgba(255,255,255,.035);--surface2:rgba(255,255,255,.06);--bd:rgba(255,255,255,.08);--bd2:rgba(255,255,255,.14);
          min-height:100vh;background:#0B0D12;color:var(--text);position:relative;overflow-x:hidden;
          font-family:ui-sans-serif,"PingFang SC","Microsoft YaHei",system-ui,-apple-system,"Segoe UI",sans-serif;}
        .cf-amb{position:absolute;inset:0;pointer-events:none;background:radial-gradient(900px 420px at 50% -8%,rgba(139,92,246,.10),transparent 70%),radial-gradient(700px 500px at 85% 0%,rgba(124,92,255,.07),transparent 65%);}
        .cf-grid{position:absolute;inset:0;pointer-events:none;opacity:.5;background-image:linear-gradient(var(--bd) 1px,transparent 1px),linear-gradient(90deg,var(--bd) 1px,transparent 1px);background-size:64px 64px;-webkit-mask-image:radial-gradient(circle at 50% 22%,#000,transparent 72%);mask-image:radial-gradient(circle at 50% 22%,#000,transparent 72%);}
        .cf-wrap{position:relative;max-width:980px;margin:0 auto;padding:0 24px}
        .cf-hero{padding:52px 0 30px;text-align:center}
        .cf-eyebrow{font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:var(--teal);opacity:.85;margin-bottom:18px}
        .cf-h1{font-weight:700;font-size:clamp(34px,5.6vw,60px);line-height:1.04;letter-spacing:-.02em;margin-bottom:16px}
        .cf-h1 .hl{color:var(--teal);text-shadow:0 0 34px rgba(139,92,246,.35)}
        .cf-sub{color:var(--dim);font-size:16px;line-height:1.7;max-width:560px;margin:0 auto 26px}
        .cf-brief{max-width:720px;margin:26px auto 0;text-align:left;display:flex;flex-direction:column;gap:16px}
        .cf-card{background:var(--surface);border:1px solid var(--bd);border-radius:20px;padding:14px;backdrop-filter:blur(14px);box-shadow:0 30px 80px -40px rgba(0,0,0,.8);text-align:left}
        .cf-notice{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px;border:1px solid rgba(139,92,246,.3);background:rgba(139,92,246,.07);border-radius:12px;padding:12px 14px;font-size:13px;color:var(--dim)}
        .cf-notice a{color:var(--ink);background:linear-gradient(100deg,#6366f1,#8b5cf6);padding:7px 13px;border-radius:9px;font-weight:600;text-decoration:none;white-space:nowrap}
        .cf-err{margin-top:4px;color:#FCA5A5;font-size:13px}
        .cf-prog{padding:30px 18px 22px;display:flex;flex-direction:column;align-items:center;gap:18px}
        .cf-prog-title{font-size:16px;font-weight:600;color:var(--text);display:flex;align-items:center;gap:10px}
        .cf-spin{width:18px;height:18px;flex:none;border-radius:999px;border:2px solid rgba(139,92,246,.25);border-top-color:var(--teal);animation:cfSpin .8s linear infinite}
        .cf-spin.sm{width:10px;height:10px;border-width:1.5px}
        @keyframes cfSpin{to{transform:rotate(360deg)}}
        .cf-prog-steps{display:flex;flex-direction:column;gap:10px;width:min(320px,100%)}
        .cf-prog-step{display:flex;align-items:center;gap:11px;font-size:13.5px;color:var(--muted);transition:color .2s}
        .cf-prog-step.on{color:var(--text)}
        .cf-prog-step.done{color:var(--dim)}
        .cf-prog-step .ic{width:20px;height:20px;flex:none;display:grid;place-items:center;border-radius:999px;border:1px solid var(--bd2);font-size:11px;font-style:normal}
        .cf-prog-step.on .ic{border-color:rgba(139,92,246,.6)}
        .cf-prog-step.done .ic{border-color:rgba(139,92,246,.5);color:var(--teal)}
        .cf-prog-hint{font-size:12px;color:var(--muted);text-align:center;line-height:1.6}
        .cf-guide{max-width:720px;margin:18px auto 0;text-align:left;background:rgba(139,92,246,.06);border:1px solid rgba(139,92,246,.25);border-radius:16px;padding:14px 16px;position:relative}
        .cf-guide-title{font-size:13.5px;font-weight:600;color:var(--text);margin-bottom:10px}
        .cf-guide-close{position:absolute;top:10px;right:10px;width:24px;height:24px;border:0;border-radius:999px;background:transparent;color:var(--muted);cursor:pointer;font-size:14px;line-height:1;display:grid;place-items:center;transition:.15s}
        .cf-guide-close:hover{color:var(--text);background:var(--surface2)}
        .cf-guide-steps{display:flex;flex-direction:column;gap:7px}
        .cf-guide-step{display:flex;align-items:baseline;gap:9px;font-size:13px;color:var(--dim);line-height:1.55}
        .cf-guide-step b{flex:none;width:18px;height:18px;border-radius:999px;background:rgba(139,92,246,.18);color:var(--teal);font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;transform:translateY(2px)}
        .cf-guide-foot{margin-top:10px;font-size:12px;color:var(--muted)}
        .cf-trends{max-width:720px;margin:26px auto 0;text-align:left;background:var(--surface);border:1px solid var(--bd);border-radius:16px;padding:14px 16px}
        .cf-trends-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
        .cf-trends-lbl{font-size:13px;font-weight:600;color:var(--dim);letter-spacing:.02em}
        .cf-trends-more{display:inline-flex;align-items:center;gap:5px;padding:5px 11px;border:1px solid var(--bd);border-radius:999px;background:transparent;color:var(--muted);font:inherit;font-size:12px;cursor:pointer;transition:.18s}
        .cf-trends-more:hover{color:var(--dim);border-color:var(--bd2)}
        .cf-trend-list{display:flex;flex-direction:column;margin:0 -8px}
        .cf-trow{display:flex;align-items:center;gap:10px;padding:7px 8px;border-radius:9px;transition:.15s}
        .cf-trow:hover{background:var(--surface2)}
        .cf-trow .trk{flex:none;width:18px;text-align:center;font-size:12px;font-style:normal;font-weight:700;color:var(--muted)}
        .cf-trow .trk.hot{color:#FDA4AF}
        .cf-trow .ttl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;background:none;border:0;color:var(--text);font:inherit;font-size:13.5px;cursor:pointer;padding:0}
        .cf-trow .ttl:hover{color:var(--teal)}
        .cf-trow .tv{flex:none;font-size:11px;color:var(--muted)}
        .cf-trow .tclone{flex:none;font-size:11.5px;color:var(--muted);text-decoration:none;padding:3px 9px;border:1px solid var(--bd);border-radius:999px;transition:.15s}
        .cf-trow .tclone:hover{color:var(--teal);border-color:rgba(139,92,246,.4)}
        .cf-trends-src{margin-top:9px;font-size:11.5px;color:var(--muted)}
        .cf-trends-cats{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:9px}
        .cf-cat{padding:4px 10px;border:1px solid transparent;border-radius:999px;background:transparent;color:var(--muted);font:inherit;font-size:12px;cursor:pointer;transition:.18s}
        .cf-cat:hover{color:var(--dim)}
        .cf-cat.on{border-color:rgba(139,92,246,.4);background:rgba(139,92,246,.08);color:var(--text)}
        .cf-daily{display:flex;align-items:center;gap:8px;margin-top:12px;padding-top:12px;border-top:1px solid var(--bd)}
        .cf-daily-lbl{font-size:12.5px;font-weight:600;color:var(--dim);flex:none}
        .cf-daily-input{flex:1;min-width:0;background:rgba(0,0,0,.25);border:1px solid var(--bd);border-radius:9px;color:var(--text);font:inherit;font-size:13px;padding:7px 11px;outline:none;transition:.18s}
        .cf-daily-input:focus{border-color:rgba(139,92,246,.45)}
        .cf-daily-btn{padding:7px 14px;border:0;border-radius:9px;background:var(--surface2);color:var(--text);font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;box-shadow:inset 0 0 0 1px var(--bd2);transition:.18s;flex:none}
        .cf-daily-btn:hover{box-shadow:inset 0 0 0 1px rgba(139,92,246,.45)}
        .cf-daily-msg{margin-top:8px;font-size:12px;color:var(--dim)}
        .cf-examples{margin-top:22px;font-size:13px;color:var(--muted);display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap}
        .cf-chip{padding:6px 12px;border:1px solid var(--bd);border-radius:999px;background:var(--surface);color:var(--dim);font:inherit;cursor:pointer;transition:.18s}
        .cf-chip:hover{border-color:rgba(139,92,246,.4);color:var(--text)}
        .cf-recent{max-width:720px;margin:22px auto 0;text-align:left}
        .cf-recent .lbl{font-size:12px;color:var(--muted);margin-bottom:8px;letter-spacing:.02em;display:flex;align-items:center;justify-content:space-between}
        .cf-recent .lbl-all{color:var(--muted);text-decoration:none;transition:.18s}
        .cf-recent .lbl-all:hover{color:var(--dim)}
        .cf-recent .row{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}
        .cf-pj{display:flex;align-items:center;gap:10px;padding:11px 13px;border:1px solid var(--bd);border-radius:12px;background:var(--surface);text-decoration:none;transition:.18s}
        .cf-pj:hover{border-color:var(--bd2);background:var(--surface2)}
        .cf-pj .dot{width:7px;height:7px;border-radius:999px;background:var(--teal);flex:none;box-shadow:0 0 8px var(--teal)}
        .cf-pj .col{min-width:0;display:flex;flex-direction:column;gap:2px}
        .cf-pj .nm{font-size:13px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .cf-pj-meta{font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      `}</style>

      <div className="cf-amb" />
      <div className="cf-grid" />
      <div className="cf-wrap">
        <section className="cf-hero">
          <div className="cf-eyebrow">{t("eyebrow")}</div>
          <h1 className="cf-h1">{t("h1Lead")}<span className="hl">{t("h1Highlight")}</span></h1>
          <p className="cf-sub">{t("sub")}</p>

        </section>

        <section className="cf-brief" ref={briefRef}>
          {/* 未配置模型时只给设置页引导（不再内联填 Key） */}
          {!llmReady && (
            <div className="cf-notice">
              <span>{t("llmNoticeText")}</span>
              <Link href="/settings?tab=llm">{t("llmNoticeCta")}</Link>
            </div>
          )}

          {busy ? (
            /* busy takeover: the whole create block becomes a live checklist so the
               20–60s creation wait reads as progress, not a frozen button */
            <div className="cf-card">
              <div className="cf-prog">
                <div className="cf-prog-title">
                  <span className="cf-spin" />
                  {t("progTitle")}
                </div>
                <div className="cf-prog-steps">
                  {busySteps.map((label, i) => (
                    <div key={label} className={`cf-prog-step${i < stageIdx ? " done" : i === stageIdx ? " on" : ""}`}>
                      <span className="ic">
                        {i < stageIdx ? (
                          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 13l5 5L20 6" /></svg>
                        ) : i === stageIdx ? (
                          <span className="cf-spin sm" />
                        ) : (
                          i + 1
                        )}
                      </span>
                      {label}
                    </div>
                  ))}
                </div>
                <div className="cf-prog-hint">{t("progHint")}</div>
              </div>
            </div>
          ) : (
            <>
              <CreationBriefForm
                submitLabel={t("ctaStart")}
                showAdvanced
                /* 本页用 onSubmitForm：建项目还需要商品名/图片/来源字段 */
                onSubmit={() => { /* 见 onSubmitForm */ }}
                onSubmitForm={runCreation}
                prefill={prefill}
                prefillKey={prefillKey}
                onImportLink={handleImportLink}
                importing={importing}
                importError={importError}
                linkImported={importedImages.length > 0}
              />

              {/* 需要用户显式选风格：不是错误，是继续生成所缺的一步 */}
              {stylePrompt && <StyleChoicePrompt requirement={stylePrompt} onPick={pickStyle} />}
              {error && <div className="cf-err">{error}</div>}
            </>
          )}

        </section>

        {showGuide && (
          <div className="cf-guide">
            <button type="button" className="cf-guide-close" onClick={dismissGuide} aria-label={t("guideClose")}>✕</button>
            <div className="cf-guide-title">{t("guideTitle")}</div>
            <div className="cf-guide-steps">
              <div className="cf-guide-step"><b>1</b>{t("guideStep1")}</div>
              <div className="cf-guide-step"><b>2</b>{t("guideStep2")}</div>
              <div className="cf-guide-step"><b>3</b>{t("guideStep3")}</div>
            </div>
            <div className="cf-guide-foot">{t("guideFoot")}</div>
          </div>
        )}

        {recent.length > 0 && (
          <div className="cf-recent">
            <div className="lbl">
              {t("recentLabel")}
              <Link href="/projects" className="lbl-all">{t("recentAll")} →</Link>
            </div>
            <div className="row">
              {recent.map((p) => {
                const rel = formatRelativeTime(p.updatedAt, locale);
                return (
                  <Link key={p.id} href={`/project/${p.id}/${stepFor(p.status)}`} className="cf-pj">
                    <span className="dot" />
                    <span className="col">
                      <span className="nm">{p.name || p.productName || t("untitledProject")}</span>
                      <span className="cf-pj-meta">{t(stageKeyFor(p.status))}{rel ? ` · ${rel}` : ""}</span>
                    </span>
                  </Link>
                );
              })}
            </div>
          </div>
        )}

        {trends.length > 0 && (
          <div className="cf-trends">
            <div className="cf-trends-head">
              <span className="cf-trends-lbl">{t("trendsLabel")}</span>
              <button type="button" className="cf-trends-more" onClick={() => setTrendsPage((p) => p + 1)}>
                {t("trendsRefresh")}
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" /></svg>
              </button>
            </div>
            {trendsCats.length > 1 && (
              <div className="cf-trends-cats">
                {(["all", ...trendsCats] as const).map((id) => (
                  <button
                    key={id}
                    type="button"
                    className={`cf-cat${trendsCat === id ? " on" : ""}`}
                    onClick={() => { setTrendsCat(id); setTrendsPage(0); }}
                  >
                    {id === "all" ? t("trendCatAll") : t(`trendCat_${id}`)}
                  </button>
                ))}
              </div>
            )}
            {/* ranked list rows: scan-friendly, one action per row (was a wall of glued pills) */}
            <div className="cf-trend-list">
              {trendsShown.map((tp, i) => (
                <div key={`${tp.source || "t"}-${tp.rank ?? tp.title}`} className="cf-trow">
                  <b className={`trk${typeof tp.rank === "number" && tp.rank <= 3 ? " hot" : ""}`}>
                    {typeof tp.rank === "number" ? tp.rank : i + 1}
                  </b>
                  <button
                    type="button"
                    className="ttl"
                    title={tp.context || tp.title}
                    onClick={() => pickTrend(tp)}
                  >
                    {tp.title}
                  </button>
                  {tp.traffic && <span className="tv">{tp.traffic}</span>}
                  <Link
                    href={`/project/clone?trend=${encodeURIComponent(tp.title)}`}
                    className="tclone"
                    title={t("trendCloneAria")}
                    aria-label={t("trendCloneAria")}
                  >
                    {t("trendCloneLabel")}
                  </Link>
                </div>
              ))}
            </div>
            <div className="cf-trends-src">{t("trendsSourceNote", { source: trendsSourceLabel })}</div>

            <div className="cf-daily">
              <span className="cf-daily-lbl">{t("dailyLabel")}</span>
              <input
                className="cf-daily-input"
                value={dailyPersona}
                onChange={(e) => onPersonaChange(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") runDailyPick(); }}
                placeholder={t("dailyPersonaPlaceholder")}
              />
              <button type="button" className="cf-daily-btn" onClick={runDailyPick}>{t("dailyPick")}</button>
            </div>
            {(dailyMsg || (dailyLast && dailyLast.date === localDateStamp())) && (
              <div className="cf-daily-msg">
                {dailyMsg || t("dailyDoneHint").replace("{topic}", dailyLast?.topic ?? "")}
              </div>
            )}
          </div>
        )}

        <div className="cf-examples">
          {t("examplesLabel")}
          {examples.slice(0, 3).map((ex) => (
            <button key={ex.id} type="button" className="cf-chip" onClick={() => void fillExample(ex)}>{ex.name} ¥{ex.price}</button>
          ))}
        </div>
      </div>
    </div>
  );
}

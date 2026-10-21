import { useCallback, useEffect, useRef, useState } from "react";

import { ClipForgeRequestError } from "../api/clipforgeClient";
import {
  createBatchJob,
  getBatchJob,
  updateBatchItem,
  updateBatchJob,
} from "../api/endpoints/batch";
import { composeVideo, getCompose } from "../api/endpoints/compose";
import { getHomogeneity } from "../api/endpoints/insights";
import { createProject } from "../api/endpoints/projects";
import { generateScript, judgeScript, patchScript } from "../api/endpoints/scripts";
import { fillStock } from "../api/endpoints/stock";
import type {
  BatchItemPatch,
  BatchItemStatus,
  BatchJobItem,
  HomogeneityResult,
  ProductItem,
  ScriptGenerationInput,
  ShotTextPatch,
} from "../api/types";
import {
  batchAutoComposeEnabled,
  batchItemTargetPath,
  buildBatchItemBrief,
  resolveBatchItemStyle,
} from "../lib/batch-creation-brief";
import {
  DURATION_OPTIONS,
  OUTPUT_STRATEGY_OPTIONS,
  SCRIPT_STYLE_OPTIONS,
  VIDEO_MODE_OPTIONS,
  scriptStyleLabel,
  type VideoModeId,
} from "../lib/creation-brief-defaults";
import { isOutputStrategy, type CreationBrief, type OutputStrategy } from "../lib/creation-brief";
import { parseStyleRequirement } from "../lib/script-style-requirement";
import { buildScriptRequest } from "../lib/build-script-request";
import { buildVariationPlan, describeSlot, type VariationSlot } from "../lib/variation-plan";
import { useProductLibraryStore } from "../state/productLibraryStore";
import { useSettingsStore } from "../state/settingsStore";

const CONCURRENCY = 3;
const COMPOSE_POLL_ATTEMPTS = 90;
const COMPOSE_POLL_INTERVAL_MS = 2500;
const DEFAULT_BATCH_STYLE = "scenario";

/** variation 摘要里的风格显示名（引擎 key 与 UI key 都覆盖）。 */
const STYLE_NAMES: Record<string, string> = {
  pain_point: "痛点式",
  scene: "场景种草",
  comparison: "对比实测",
  story: "剧情带货",
  ...Object.fromEntries(SCRIPT_STYLE_OPTIONS.map((option) => [option.id, option.label])),
};

const TASK_STATUS_LABEL: Record<BatchItemStatus, string> = {
  pending: "排队中",
  generating: "生成脚本",
  composing: "配画面/合成",
  done: "已完成",
  failed: "失败",
};

type BatchTask = {
  id: string;
  productName: string;
  status: BatchItemStatus;
  projectId?: string;
  error?: string;
  variation?: string;
};

type BatchSettings = { duration: string; scriptStyle: string; strategy: OutputStrategy };

type BatchCtx = {
  videoMode: VideoModeId;
  scriptStyle: string;
  duration: string;
  strategy: OutputStrategy;
  autoCompose: boolean;
  productCard: boolean;
  jobId?: string;
  itemIdByProduct: Map<string, string>;
};

type BatchWorkItem = {
  product: ProductItem;
  slot?: VariationSlot;
  brief: CreationBrief;
  appliedDurationOffset: number;
  resume?: BatchJobItem;
};

type BatchJobConfig = {
  videoMode?: string;
  scriptStyle?: string;
  duration?: string;
  strategy?: string;
  autoCompose?: boolean;
  productCard?: boolean;
  antiHomogeneity?: boolean;
  plan?: VariationSlot[];
};

type ResumableJob = {
  jobId: string;
  total: number;
  config: BatchJobConfig;
  items: BatchJobItem[];
};

function buildWorkItem(product: ProductItem, slot: VariationSlot | undefined, settings: BatchSettings): BatchWorkItem {
  const built = buildBatchItemBrief({
    style: resolveBatchItemStyle({ slotStyleType: slot?.styleType, chosenStyle: settings.scriptStyle }),
    baseDuration: parseInt(settings.duration, 10),
    durationOffset: slot?.durationOffset ?? 0,
    strategy: settings.strategy,
    audience: product.targetAudience,
  });
  return {
    product,
    brief: built.brief,
    appliedDurationOffset: built.appliedDurationOffset,
    ...(slot ? { slot } : {}),
  };
}

function variationLabelOf(item: BatchWorkItem): string | undefined {
  if (!item.slot) return undefined;
  return describeSlot({ ...item.slot, durationOffset: item.appliedDurationOffset }, STYLE_NAMES, "zh");
}

type BatchPageProps = {
  onNavigate: (path: string) => void;
};

/**
 * 任务中心：勾选商品 → 建批量任务 → 并发 3 出片。
 * 每个阶段都写回 /api/batch，刷新后可从 active 任务续跑；单项失败不影响其余。
 */
export function BatchPage({ onNavigate }: BatchPageProps) {
  const { products, incrementVideoCount } = useProductLibraryStore();
  const { llm } = useSettingsStore();

  const [selectedProducts, setSelectedProducts] = useState<Set<string>>(new Set());
  const [videoMode, setVideoMode] = useState<VideoModeId>("product_closeup");
  const [scriptStyle, setScriptStyle] = useState(DEFAULT_BATCH_STYLE);
  const [duration, setDuration] = useState("30");
  const [outputStrategy, setOutputStrategy] = useState<OutputStrategy>("draft");
  const [autoCompose, setAutoCompose] = useState(true);
  const [productCard, setProductCard] = useState(true);
  const [antiHomogeneity, setAntiHomogeneity] = useState(true);

  const [configError, setConfigError] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const [tasks, setTasks] = useState<BatchTask[]>([]);
  const [resumableJob, setResumableJob] = useState<ResumableJob | null>(null);
  const [homogeneity, setHomogeneity] = useState<HomogeneityResult | null>(null);
  const abortRef = useRef(false);
  // 页面卸载（刷新/关闭）时，在途请求会被中止；不要把这类中止写成「失败」，否则刷新后无法续跑。
  const unloadingRef = useRef(false);

  useEffect(() => {
    const markUnloading = () => {
      unloadingRef.current = true;
    };
    window.addEventListener("beforeunload", markUnloading);
    return () => window.removeEventListener("beforeunload", markUnloading);
  }, []);

  // 刷新后若存在 running 任务，说明上一个执行器随页面一起死了：提供继续/丢弃。
  useEffect(() => {
    let cancelled = false;
    void getBatchJob({ active: true })
      .then((snapshot) => {
        if (cancelled || !snapshot.job) return;
        setResumableJob({
          jobId: snapshot.job.id,
          total: snapshot.job.total,
          config: (snapshot.job.config ?? {}) as BatchJobConfig,
          items: snapshot.items,
        });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleProduct = useCallback((productId: string) => {
    setSelectedProducts((current) => {
      const next = new Set(current);
      if (next.has(productId)) next.delete(productId);
      else next.add(productId);
      return next;
    });
  }, []);

  const reportItem = useCallback((ctx: BatchCtx, productId: string, patch: BatchItemPatch) => {
    const itemId = ctx.itemIdByProduct.get(productId);
    if (!itemId) return;
    void updateBatchItem(itemId, patch).catch(() => undefined);
  }, []);

  const patchTask = useCallback((productId: string, patch: Partial<BatchTask>) => {
    setTasks((current) => current.map((task) => (task.id === productId ? { ...task, ...patch } : task)));
  }, []);

  const pollCompose = useCallback(async (projectId: string, compositionId?: string): Promise<boolean> => {
    for (let attempt = 0; attempt < COMPOSE_POLL_ATTEMPTS && !abortRef.current; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, COMPOSE_POLL_INTERVAL_MS));
      const snapshot = await getCompose(projectId, compositionId).catch(() => ({ composition: null }));
      const status = snapshot.composition?.status;
      if (status === "done") return true;
      if (status === "failed") throw new Error("合成失败，请到项目页查看原因并重试");
    }
    // 中途取消不算失败：进度已经逐条落库。
    return abortRef.current;
  }, []);

  const composeSubChain = useCallback(
    async (ctx: BatchCtx, product: ProductItem, projectId: string, slot?: VariationSlot) => {
      // 免费素材填充失败不致命：项目里可能已有商品图或素材。
      await fillStock(projectId, { source: "all", mediaType: "auto" }).catch(() => undefined);
      const accepted = await composeVideo(projectId, {
        freeTts: { enabled: true, ...(slot?.voice ? { voice: slot.voice } : {}) },
        ...(ctx.productCard ? { productCard: true } : {}),
        ...(slot?.bgm ? { freeBgm: true, ...(slot.bgmMood ? { bgmMood: slot.bgmMood } : {}) } : {}),
        ...(slot?.karaoke ? { karaoke: true } : {}),
      });
      reportItem(ctx, product.id, { compositionId: accepted.compositionId });
      const composed = await pollCompose(projectId, accepted.compositionId);
      if (!composed && !abortRef.current) throw new Error("合成失败，请到项目页重试");
    },
    [pollCompose, reportItem],
  );

  const processOne = useCallback(
    async (item: BatchWorkItem, ctx: BatchCtx) => {
      const { product, slot, brief, resume } = item;
      patchTask(product.id, { status: "generating", error: undefined });
      reportItem(ctx, product.id, { status: "generating" });
      try {
        // 断点续跑：已提交过合成就只查结果，失败再重渲。
        if (resume?.projectId && resume.compositionId) {
          patchTask(product.id, { status: "composing", projectId: resume.projectId });
          reportItem(ctx, product.id, { status: "composing" });
          try {
            const snapshot = await getCompose(resume.projectId, resume.compositionId);
            if (snapshot.composition?.status === "done") {
              incrementVideoCount(product.id);
              reportItem(ctx, product.id, { status: "done" });
              patchTask(product.id, { status: "done", projectId: resume.projectId });
              return;
            }
          } catch {
            // 查询失败按「需要重渲」处理
          }
          await composeSubChain(ctx, product, resume.projectId, slot);
          incrementVideoCount(product.id);
          reportItem(ctx, product.id, { status: "done" });
          patchTask(product.id, { status: "done", projectId: resume.projectId });
          return;
        }

        // 1) 建项目（已有项目则复用），创作简报来自共享构造器。
        let projectId = resume?.projectId ?? undefined;
        if (!projectId) {
          const project = await createProject({
            name: `批量 · ${product.name}`,
            productName: product.name,
            productCategory: product.category,
            productDescription: product.description ?? "",
            productImages: product.images,
            videoMode: ctx.videoMode,
            creationBrief: brief,
          });
          projectId = project.id;
          reportItem(ctx, product.id, { projectId });
        }

        // 2) 生成脚本：唯一请求构造器 + 批量自己的 hook 轮换通道。
        const script = await generateScript({
          ...buildScriptRequest({
            brief,
            projectId,
            productName: product.name,
            category: product.category,
            productDescription: product.description ?? "",
            productImages: product.images,
            videoMode: ctx.videoMode,
            llmConfig: {
              baseUrl: llm.baseUrl,
              apiKey: llm.apiKey,
              model: llm.model,
              visionModel: llm.visionModel,
            },
          }),
          ...(slot?.hookId ? { preferredHookId: slot.hookId } : {}),
        } as unknown as ScriptGenerationInput).catch((cause: unknown) => {
          if (cause instanceof ClipForgeRequestError) {
            const requirement = parseStyleRequirement(cause.status, cause.payload);
            if (requirement) {
              throw new Error(`需要先显式选择脚本风格：${requirement.candidates.map(scriptStyleLabel).join(" / ")}`);
            }
          }
          throw cause;
        });

        // 3) 只有显式「免费草稿」才自动配画面 + 合成；其它策略回项目页确认。
        if (batchAutoComposeEnabled(ctx.strategy, ctx.autoCompose) && !abortRef.current) {
          patchTask(product.id, { status: "composing", projectId });
          reportItem(ctx, product.id, { status: "composing" });

          // 判官团：best-effort，失败不影响批量项；只应用 invariant/default 级改写。
          const judgeScriptId = script.scripts?.[0]?.id;
          if (judgeScriptId) {
            try {
              const report = await judgeScript(projectId, {
                scriptId: judgeScriptId,
                llmConfig: { baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model },
              });
              if (!("skipped" in report)) {
                const shotTexts = new Map<number, ShotTextPatch>();
                for (const rewrite of report.rewrites) {
                  if (rewrite.tier === "taste") continue;
                  shotTexts.set(rewrite.shotId, { shotId: rewrite.shotId, voiceover: rewrite.voiceover });
                }
                for (const rewrite of report.descriptionRewrites) {
                  if (rewrite.tier === "taste") continue;
                  shotTexts.set(rewrite.shotId, {
                    ...(shotTexts.get(rewrite.shotId) ?? { shotId: rewrite.shotId }),
                    description: rewrite.description,
                  });
                }
                if (shotTexts.size > 0) {
                  await patchScript(projectId, { scriptId: judgeScriptId, shotTexts: [...shotTexts.values()] });
                }
              }
            } catch {
              // 质量增强失败不阻断出片
            }
          }

          await composeSubChain(ctx, product, projectId, slot);
        }

        incrementVideoCount(product.id);
        reportItem(ctx, product.id, { status: "done" });
        patchTask(product.id, { status: "done", projectId });
      } catch (cause) {
        // 刷新/关闭导致的请求中止不是业务失败：保持 running，让下次进入可续跑。
        if (unloadingRef.current) return;
        const message = cause instanceof Error ? cause.message : "生成失败，请重试";
        reportItem(ctx, product.id, { status: "failed", error: message });
        patchTask(product.id, { status: "failed", error: message });
      }
    },
    [composeSubChain, incrementVideoCount, llm, patchTask, reportItem],
  );

  const executeBatch = useCallback(
    async (workItems: BatchWorkItem[], ctx: BatchCtx) => {
      let cursor = 0;
      const worker = async () => {
        while (!abortRef.current) {
          const index = cursor;
          cursor += 1;
          if (index >= workItems.length) break;
          await processOne(workItems[index], ctx);
        }
      };
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, workItems.length) }, worker));

      if (ctx.jobId && !unloadingRef.current) {
        void updateBatchJob(ctx.jobId, abortRef.current ? "cancelled" : "done").catch(() => undefined);
      }
      if (!abortRef.current && !unloadingRef.current) {
        setIsComplete(true);
        if (workItems.length >= 2) {
          try {
            const report = await getHomogeneity(Math.min(20, workItems.length));
            if (report.message) setHomogeneity(report);
          } catch {
            // 自检只是建议，绝不影响批量结果
          }
        }
      }
      setIsGenerating(false);
    },
    [processOne],
  );

  const handleStart = useCallback(async () => {
    if (selectedProducts.size === 0 || isGenerating) return;
    if (!llm.apiKey.trim()) {
      setConfigError("请先在「设置」里填写脚本模型（LLM）的 API Key。");
      return;
    }
    setConfigError("");
    abortRef.current = false;
    setIsGenerating(true);
    setIsComplete(false);
    setResumableJob(null);
    setHomogeneity(null);

    const selected = products.filter((product) => selectedProducts.has(product.id));
    const plan = antiHomogeneity
      ? buildVariationPlan({
          count: selected.length,
          // 商品库的「其他」没有对应的 hook 品类，按家居通用池处理。
          category: (selected[0]?.category === "other" ? "home" : selected[0]?.category ?? "home") as never,
          styleType: scriptStyle,
          seed: Date.now() % 100000,
        })
      : [];
    const settings: BatchSettings = { duration, scriptStyle, strategy: outputStrategy };
    const work = selected.map((product, index) => buildWorkItem(product, plan[index], settings));
    setTasks(
      work.map((item) => ({
        id: item.product.id,
        productName: item.product.name,
        status: "pending" as BatchItemStatus,
        ...(variationLabelOf(item) ? { variation: variationLabelOf(item) } : {}),
      })),
    );

    const ctx: BatchCtx = {
      videoMode,
      scriptStyle,
      duration,
      strategy: outputStrategy,
      autoCompose,
      productCard,
      itemIdByProduct: new Map(),
    };
    try {
      const created = await createBatchJob({
        config: {
          videoMode,
          scriptStyle,
          duration,
          strategy: outputStrategy,
          autoCompose,
          productCard,
          antiHomogeneity,
          plan,
        },
        items: work.map((item) => ({
          productId: item.product.id,
          productName: item.product.name,
          ...(variationLabelOf(item) ? { variation: variationLabelOf(item) } : {}),
        })),
      });
      ctx.jobId = created.jobId;
      for (const row of created.items) ctx.itemIdByProduct.set(row.productId, row.id);
    } catch {
      // 落库只是升级项：失败时仍在内存里执行。
    }
    await executeBatch(work, ctx);
  }, [
    antiHomogeneity,
    autoCompose,
    duration,
    executeBatch,
    isGenerating,
    llm.apiKey,
    outputStrategy,
    productCard,
    products,
    scriptStyle,
    selectedProducts,
    videoMode,
  ]);

  async function handleResume() {
    if (!resumableJob || isGenerating) return;
    if (!llm.apiKey.trim()) {
      setConfigError("请先在「设置」里填写脚本模型（LLM）的 API Key。");
      return;
    }
    const { jobId, items, config } = resumableJob;
    setConfigError("");
    abortRef.current = false;
    setIsGenerating(true);
    setIsComplete(false);
    setResumableJob(null);
    setHomogeneity(null);

    const ctx: BatchCtx = {
      videoMode: (config.videoMode as VideoModeId | undefined) ?? videoMode,
      scriptStyle: config.scriptStyle ?? scriptStyle,
      duration: config.duration ?? duration,
      strategy: isOutputStrategy(config.strategy) ? config.strategy : "draft",
      autoCompose: config.autoCompose ?? true,
      productCard: config.productCard ?? true,
      jobId,
      itemIdByProduct: new Map(items.map((item) => [item.productId, item.id])),
    };
    const settings: BatchSettings = { duration: ctx.duration, scriptStyle: ctx.scriptStyle, strategy: ctx.strategy };
    const plan = Array.isArray(config.plan) ? config.plan : [];
    const byId = new Map(products.map((product) => [product.id, product]));

    setTasks(
      items.map((item) => ({
        id: item.productId,
        productName: item.productName,
        status: item.status === "done" ? "done" : "pending",
        ...(item.projectId ? { projectId: item.projectId } : {}),
        ...(item.variation ? { variation: item.variation } : {}),
      })),
    );

    const work: BatchWorkItem[] = [];
    for (const [index, item] of items.entries()) {
      if (item.status === "done") continue;
      const product = byId.get(item.productId);
      if (!product) {
        reportItem(ctx, item.productId, { status: "failed", error: "商品已从商品库删除" });
        patchTask(item.productId, { status: "failed", error: "商品已从商品库删除" });
        continue;
      }
      work.push({ ...buildWorkItem(product, plan[index], settings), resume: item });
    }
    await executeBatch(work, ctx);
  }

  function handleDiscard() {
    if (!resumableJob) return;
    void updateBatchJob(resumableJob.jobId, "cancelled").catch(() => undefined);
    setResumableJob(null);
  }

  const doneCount = tasks.filter((task) => task.status === "done").length;
  const failedCount = tasks.filter((task) => task.status === "failed").length;
  const allProductsSelected = products.length > 0 && selectedProducts.size === products.length;

  return (
    <section className="ai-video-page" aria-label="任务中心">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>任务中心</h2>
          <p>勾选商品批量出片：并发 3 条，每阶段落库，刷新后可继续未完成的任务。</p>
        </div>
        {tasks.length > 0 && (
          <span className={`ai-video-service-badge${failedCount > 0 ? " is-warning" : " is-ready"}`}>
            {doneCount}/{tasks.length} 完成{failedCount > 0 ? ` · ${failedCount} 失败` : ""}
          </span>
        )}
      </header>

      {resumableJob && !isGenerating && (
        <div className="ai-video-batch-resume" role="status">
          <div>
            <strong>发现未完成的批量任务</strong>
            <p>
              上次执行中断在 {resumableJob.total} 条中的第 {resumableJob.items.filter((item) => item.status === "done").length + 1} 条。
              继续会跳过已完成项；也可以丢弃后重新开始。
            </p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" onClick={() => void handleResume()}>继续未完成项</button>
            <button type="button" className="is-secondary" onClick={handleDiscard}>丢弃</button>
          </div>
        </div>
      )}

      {configError && <div className="ai-video-form-error" role="alert">{configError}</div>}

      {!isGenerating && tasks.length === 0 && (
        <>
          <section className="ai-video-form-card" aria-label="选择商品">
            <div className="ai-video-form-head">
              <h3>1. 选择商品</h3>
              <p>每条商品会独立建项目、生成脚本；商品图与卖点来自商品库。</p>
            </div>
            {products.length === 0 ? (
              <div className="ai-video-inline-empty">
                <span>商品库还是空的。</span>
                <button type="button" onClick={() => onNavigate("/products")}>去商品素材</button>
              </div>
            ) : (
              <>
                <div className="ai-video-settings-actions">
                  <button
                    type="button"
                    className="is-secondary"
                    onClick={() =>
                      setSelectedProducts(allProductsSelected ? new Set() : new Set(products.map((product) => product.id)))
                    }
                  >
                    {allProductsSelected ? "取消全选" : "全选"}
                  </button>
                  <span className="ai-video-settings-message">{selectedProducts.size} 条已选</span>
                </div>
                <div className="ai-video-batch-products">
                  {products.map((product) => (
                    <label key={product.id} className={`ai-video-batch-product${selectedProducts.has(product.id) ? " is-active" : ""}`}>
                      <input
                        type="checkbox"
                        checked={selectedProducts.has(product.id)}
                        onChange={() => toggleProduct(product.id)}
                      />
                      <span>
                        <strong>{product.name}</strong>
                        <em>{product.description || product.category}</em>
                      </span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </section>

          <section className="ai-video-form-card" aria-label="批量参数">
            <div className="ai-video-form-head">
              <h3>2. 批量参数</h3>
              <p>所有商品共用同一份参数；防同质化会给每条分配不同的钩子/风格/配音/字幕组合。</p>
            </div>
            <div className="ai-video-settings-grid">
              <div className="ai-video-field">
                <label htmlFor="ai-video-batch-style">脚本风格</label>
                <select
                  id="ai-video-batch-style"
                  value={scriptStyle}
                  onChange={(event) => setScriptStyle(event.target.value)}
                >
                  {SCRIPT_STYLE_OPTIONS.map((option) => (
                    <option key={option.id} value={option.id}>{option.label}</option>
                  ))}
                </select>
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-batch-duration">目标时长</label>
                <select
                  id="ai-video-batch-duration"
                  value={duration}
                  onChange={(event) => setDuration(event.target.value)}
                >
                  {DURATION_OPTIONS.map((option) => (
                    <option key={option.id} value={String(option.id)}>{option.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="ai-video-field">
              <label>画面模式</label>
              <div className="ai-video-chip-row">
                {VIDEO_MODE_OPTIONS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className={`ai-video-chip${videoMode === option.id ? " is-active" : ""}`}
                    aria-pressed={videoMode === option.id}
                    onClick={() => setVideoMode(option.id)}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="ai-video-field">
              <label>出片策略</label>
              <div className="ai-video-choice-grid">
                {OUTPUT_STRATEGY_OPTIONS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    className={`ai-video-choice-card${outputStrategy === option.id ? " is-active" : ""}`}
                    aria-pressed={outputStrategy === option.id}
                    onClick={() => setOutputStrategy(option.id)}
                  >
                    <strong>{option.label}</strong>
                    <span>{option.description}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="ai-video-batch-toggles">
              <label>
                <input
                  type="checkbox"
                  checked={antiHomogeneity}
                  onChange={(event) => setAntiHomogeneity(event.target.checked)}
                />
                防同质化（轮换钩子/风格/配音/字幕）
              </label>
              <label>
                <input type="checkbox" checked={productCard} onChange={(event) => setProductCard(event.target.checked)} />
                叠加商品卡
              </label>
              <label className={outputStrategy === "draft" ? "" : "is-disabled"}>
                <input
                  type="checkbox"
                  checked={autoCompose}
                  disabled={outputStrategy !== "draft"}
                  onChange={(event) => setAutoCompose(event.target.checked)}
                />
                脚本后自动配画面并合成（仅免费草稿）
              </label>
            </div>

            <button
              type="button"
              className="ai-video-submit"
              disabled={selectedProducts.size === 0}
              onClick={() => void handleStart()}
            >
              开始批量出片（{selectedProducts.size} 条，并发 {CONCURRENCY}）
            </button>
          </section>
        </>
      )}

      {isGenerating && (
        <div className="ai-video-progress" role="status">
          <strong>批量执行中：{doneCount}/{tasks.length} 完成</strong>
          <div className="ai-video-settings-actions">
            <button
              type="button"
              className="is-secondary"
              onClick={() => {
                abortRef.current = true;
              }}
            >
              中止批量
            </button>
            <span className="ai-video-settings-message">中止后已完成的条目会保留，未开始的条目不再执行。</span>
          </div>
        </div>
      )}

      {tasks.length > 0 && (
        <section className="ai-video-form-card" aria-label="批量任务列表">
          <div className="ai-video-form-head">
            <h3>任务列表</h3>
            <p>点击已完成的项目可以继续查看或修改。</p>
          </div>
          <ul className="ai-video-batch-tasks">
            {tasks.map((task) => (
              <li key={task.id} className={`is-${task.status}`}>
                <div>
                  <strong>{task.productName}</strong>
                  {task.variation && <em>{task.variation}</em>}
                  {task.error && <p>{task.error}</p>}
                </div>
                <span className={`ai-video-batch-status is-${task.status}`}>{TASK_STATUS_LABEL[task.status]}</span>
                {task.projectId && (
                  <button
                    type="button"
                    className="is-secondary"
                    onClick={() =>
                      onNavigate(batchItemTargetPath(task.projectId!, outputStrategy, autoCompose))
                    }
                  >
                    打开项目
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {isComplete && homogeneity && (
        <div className={`ai-video-batch-homogeneity is-${homogeneity.verdict}`} role="status">
          <strong>{homogeneity.verdict === "ok" ? "结构多样性良好" : "近期作品结构偏同质化"}</strong>
          <p>{homogeneity.message?.zh}</p>
        </div>
      )}

      {isComplete && !isGenerating && (
        <div className="ai-video-settings-actions">
          <button
            type="button"
            onClick={() => {
              setTasks([]);
              setIsComplete(false);
              setHomogeneity(null);
            }}
          >
            开始新的批量
          </button>
          <button type="button" className="is-secondary" onClick={() => onNavigate("/projects")}>去我的项目</button>
        </div>
      )}
    </section>
  );
}

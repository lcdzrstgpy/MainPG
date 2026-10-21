import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClipForgeRequestError } from "../api/clipforgeClient";
import { generateAiImage } from "../api/endpoints/aiImage";
import { listAiTasks, queryVideoTask, submitVideoTask } from "../api/endpoints/aiTasks";
import { listAssets, saveAsset, selectAsset } from "../api/endpoints/assets";
import { generateStoryboardGrid, previewFilm } from "../api/endpoints/film";
import { uploadMaterial } from "../api/endpoints/materials";
import { getProject } from "../api/endpoints/projects";
import { listScripts } from "../api/endpoints/scripts";
import { fillStock } from "../api/endpoints/stock";
import { uploadImages } from "../api/endpoints/upload";
import type { AiTask, Asset, FilmDryRun, Project, PublicLocalMaterial, Script } from "../api/types";
import { LocalMaterialLibrary } from "../components/LocalMaterialLibrary";
import { ProjectHeader } from "../components/ProjectHeader";
import { buildShotAssetRows, type ShotAssetRow } from "../lib/asset-rows";
import {
  buildImageOptions,
  buildVideoOptions,
  resolveDefaultModelTarget,
  type GenModelTarget,
} from "../lib/gen-params";
import { classifyMaterial } from "../lib/material-library";
import { aiVideoMediaUrl } from "../lib/media-url";
import { applyRetakePatch, RETAKE_SYMPTOMS, type RetakeSymptom } from "../lib/retake-patch";
import { useCharacterStore } from "../state/characterStore";
import { useSettingsStore } from "../state/settingsStore";

type Notice = { kind: "ok" | "error"; text: string } | null;

type BusyKind = "image" | "video" | "upload" | "lock";

type ProjectAssetsPageProps = {
  projectId: string;
  onNavigate: (path: string) => void;
};

/**
 * 素材页：逐镜生图 / 上传 / 商品图、锁定 take、以图生视频、免费素材自动填充、待恢复任务。
 * 锁定即 PATCH selected；服务端 stock-fill 与本地批量都会跳过已锁定镜头。
 */
export function ProjectAssetsPage({ projectId, onNavigate }: ProjectAssetsPageProps) {
  const {
    providers,
    imageParams,
    videoParams,
    defaultImageModel,
    defaultVideoModel,
    customModels,
    spendCapUsd,
  } = useSettingsStore();
  const { characters } = useCharacterStore();

  const [project, setProject] = useState<Project | null>(null);
  const [script, setScript] = useState<Script | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [tasks, setTasks] = useState<AiTask[]>([]);
  const [imageTarget, setImageTarget] = useState<GenModelTarget | null>(null);
  const [videoTarget, setVideoTarget] = useState<GenModelTarget | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [shotBusy, setShotBusy] = useState<Record<number, BusyKind>>({});
  const [shotErrors, setShotErrors] = useState<Record<number, string>>({});
  const [stockBusy, setStockBusy] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [pendingBusy, setPendingBusy] = useState<string | null>(null);
  const [retakes, setRetakes] = useState<Record<number, RetakeSymptom | "">>({});
  const [materialRevision, setMaterialRevision] = useState(0);
  const [filmBusy, setFilmBusy] = useState<"grid" | "preview" | null>(null);
  const [filmPreview, setFilmPreview] = useState<FilmDryRun | null>(null);

  const controllers = useRef(new Map<number, AbortController>());
  const uploadTarget = useRef<number | null>(null);
  const uploadRef = useRef<HTMLInputElement | null>(null);

  const rows = useMemo(() => buildShotAssetRows(script?.shots ?? [], assets), [script, assets]);
  const resumableTasks = tasks.filter((task) => task.mediaType === "video" && task.shotId != null);

  const reloadAssets = useCallback(async () => {
    const [nextAssets, activeTasks] = await Promise.all([
      listAssets(projectId),
      listAiTasks({ projectId, active: true }).catch(() => []),
    ]);
    setAssets(nextAssets);
    setTasks(activeTasks);
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    void (async () => {
      try {
        const [nextProject, scripts, nextAssets] = await Promise.all([
          getProject(projectId),
          listScripts(projectId),
          listAssets(projectId),
        ]);
        if (cancelled) return;
        setProject(nextProject);
        setScript(scripts.find((item) => item.selected) ?? scripts[0] ?? null);
        setAssets(nextAssets);

        const [image, video, activeTasks] = await Promise.all([
          resolveDefaultModelTarget(providers, defaultImageModel, customModels, "image").catch(() => null),
          resolveDefaultModelTarget(providers, defaultVideoModel, customModels, "video").catch(() => null),
          listAiTasks({ projectId, active: true }).catch(() => []),
        ]);
        if (cancelled) return;
        setImageTarget(image);
        setVideoTarget(video);
        setTasks(activeTasks);
      } catch (cause) {
        if (!cancelled) setLoadError(cause instanceof Error ? cause.message : "素材加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      for (const controller of controllers.current.values()) controller.abort();
      controllers.current.clear();
    };
  }, [customModels, defaultImageModel, defaultVideoModel, projectId, providers]);

  function setBusy(shotId: number, kind: BusyKind | null) {
    setShotBusy((current) => {
      const next = { ...current };
      if (kind) next[shotId] = kind;
      else delete next[shotId];
      return next;
    });
  }

  function setShotError(shotId: number, message: string) {
    setShotErrors((current) => ({ ...current, [shotId]: message }));
  }

  async function generateImageForRow(row: ShotAssetRow, batch = false) {
    const shotId = row.shot.shotId;
    setBusy(shotId, "image");
    setShotError(shotId, "");
    try {
      const productImage = project?.productImages[0];
      if (row.shot.visualSource === "product_image" && productImage) {
        await saveAsset(projectId, { shotId, sourceUrl: productImage, type: "product_image" });
      } else {
        if (!imageTarget) throw new Error("请先在设置中启用生图平台并选择默认生图模型。");
        const prompt = (row.shot.prompt?.trim() || row.shot.description).slice(0, 2000);
        const result = await generateAiImage({
          provider: imageTarget.provider,
          model: imageTarget.model,
          apiKey: imageTarget.apiKey,
          baseUrl: imageTarget.baseUrl,
          projectId,
          prompt,
          mode: "text-to-image",
          options: buildImageOptions(imageParams),
        });
        const url = result.imageUrls?.[0];
        if (!url) throw new Error("生图没有返回结果");
        await saveAsset(projectId, {
          shotId,
          sourceUrl: url,
          type: "ai_generated",
          provider: imageTarget.provider,
          model: result.modelId ?? imageTarget.model,
          prompt,
        });
      }
      if (!batch) {
        await reloadAssets();
        setNotice({ kind: "ok", text: `第 ${shotId} 镜画面已更新` });
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "生图失败";
      setShotError(shotId, message);
      if (!batch) setNotice({ kind: "error", text: message });
    } finally {
      setBusy(shotId, null);
    }
  }

  async function lockAsset(row: ShotAssetRow, asset: Asset) {
    const shotId = row.shot.shotId;
    setBusy(shotId, "lock");
    setShotError(shotId, "");
    // 先乐观更新，再以服务端结果为准。
    setAssets((current) =>
      current.map((item) => (item.shotId === asset.shotId ? { ...item, selected: item.id === asset.id } : item)),
    );
    try {
      await selectAsset(projectId, asset.id);
      await reloadAssets();
      setNotice({ kind: "ok", text: `第 ${shotId} 镜已锁定该 take，自动填充不会覆盖` });
    } catch (cause) {
      await reloadAssets().catch(() => undefined);
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "锁定失败" });
    } finally {
      setBusy(shotId, null);
    }
  }

  function triggerUpload(shotId: number) {
    uploadTarget.current = shotId;
    uploadRef.current?.click();
  }

  async function handleUpload(files: FileList | null) {
    const shotId = uploadTarget.current;
    uploadTarget.current = null;
    const file = files?.[0];
    if (!shotId || !file) return;
    setBusy(shotId, "upload");
    setShotError(shotId, "");
    try {
      // 视频/图片走素材库（≤80MB，可复用）；GIF/SVG 等仅图片格式仍走 /api/upload。
      if (classifyMaterial(file.name)) {
        const { material } = await uploadMaterial(projectId, file);
        await saveAsset(projectId, {
          shotId,
          sourceUrl: material.url,
          type: "user_upload",
          provider: "local",
          prompt: [material.originalName, ...material.tags].join(" "),
        });
        setMaterialRevision((value) => value + 1);
      } else {
        const uploaded = await uploadImages(projectId, [file]);
        const path = uploaded.paths?.[0];
        if (!path) throw new Error("上传没有返回文件路径");
        await saveAsset(projectId, { shotId, sourceUrl: path, type: "user_upload" });
      }
      await reloadAssets();
      setNotice({ kind: "ok", text: `第 ${shotId} 镜已换成本地素材` });
    } catch (cause) {
      setShotError(shotId, cause instanceof Error ? cause.message : "上传失败");
    } finally {
      setBusy(shotId, null);
    }
  }

  /** 素材库条目套用到镜头：与逐镜上传同一落库语义（user_upload）。 */
  async function applyMaterial(material: PublicLocalMaterial, shotId: number) {
    await saveAsset(projectId, {
      shotId,
      sourceUrl: material.url,
      type: "user_upload",
      provider: "local",
      prompt: [material.originalName, ...material.tags].join(" "),
    });
    await reloadAssets();
  }

  async function fillLocalMaterials() {
    await fillStock(projectId, { source: "local", mediaType: "auto" });
    await reloadAssets();
    setNotice({ kind: "ok", text: "已用本地素材填充未锁定镜头" });
  }

  async function generateVideoForRow(row: ShotAssetRow, retake?: RetakeSymptom) {
    const shotId = row.shot.shotId;
    if (!row.display || row.isVideo) {
      setShotError(shotId, "请先生成并锁定一张关键帧图，再以图生视频。");
      return;
    }
    if (!videoTarget) {
      setShotError(shotId, "请先在设置中启用视频平台并选择默认生视频模型。");
      return;
    }
    const keyframe = row.display.thumbnailPath ?? row.display.filePath;
    if (!keyframe) {
      setShotError(shotId, "关键帧没有可用的本地文件");
      return;
    }
    // 诊断重拍只改一个维度：症状 → 一条提示词补丁；补丁已存在时不重复提交付费任务。
    let prompt = (row.shot.prompt?.trim() || row.shot.description).slice(0, 2000);
    let retakeChange = "";
    if (retake) {
      const patched = applyRetakePatch(prompt, retake);
      if (!patched.applied) {
        setShotError(shotId, "该诊断补丁已经在这条提示词里，换一个症状再试。");
        return;
      }
      prompt = patched.prompt;
      retakeChange = patched.change.zh;
    }
    const controller = new AbortController();
    controllers.current.set(shotId, controller);
    setBusy(shotId, "video");
    setShotError(shotId, "");
    try {
      const result = await submitVideoTask(
        {
          provider: videoTarget.provider,
          model: videoTarget.model,
          apiKey: videoTarget.apiKey,
          baseUrl: videoTarget.baseUrl,
          projectId,
          shotId,
          mode: "image-to-video",
          prompt,
          imageUrl: keyframe,
          // 逐镜时长跟随脚本，模型支持区间内取整。
          options: {
            ...buildVideoOptions(videoParams),
            duration: Math.min(15, Math.max(4, Math.round(row.shot.duration || 5))),
          },
        },
        { signal: controller.signal },
      );
      if ("error" in result) throw new Error(result.error);
      const url = result.videoUrls?.[0];
      if (!url) throw new Error("生视频没有返回结果");
      await saveAsset(projectId, {
        shotId,
        sourceUrl: url,
        type: "ai_generated",
        provider: videoTarget.provider,
        model: result.modelId ?? videoTarget.model,
        prompt,
        thumbnailPath: keyframe,
      });
      await reloadAssets();
      setNotice({
        kind: "ok",
        text: retakeChange
          ? `第 ${shotId} 镜重拍完成（${retakeChange}）`
          : `第 ${shotId} 镜视频已生成并落库`,
      });
    } catch (cause) {
      if (controller.signal.aborted) {
        setNotice({
          kind: "ok",
          text: "已取消等待：付费任务可能仍在云端，请用下方「待恢复任务」查询，避免重复提交。",
        });
        await reloadAssets().catch(() => undefined);
        return;
      }
      const taskId =
        cause instanceof ClipForgeRequestError &&
        cause.payload &&
        typeof (cause.payload as Record<string, unknown>).taskId === "string"
          ? ((cause.payload as Record<string, unknown>).taskId as string)
          : undefined;
      if (taskId) {
        setNotice({
          kind: "error",
          text: `等待超时，但任务 ${taskId} 已提交。请勿重复提交，用下方「待恢复任务」查询结果。`,
        });
        await reloadAssets().catch(() => undefined);
        return;
      }
      setShotError(shotId, cause instanceof Error ? cause.message : "生视频失败");
    } finally {
      controllers.current.delete(shotId);
      setBusy(shotId, null);
    }
  }

  async function resumeTask(task: AiTask) {
    if (task.shotId == null || pendingBusy) return;
    const setting = providers[task.provider];
    if (!setting?.apiKey) {
      setNotice({ kind: "error", text: `任务平台 ${task.provider} 尚未配置 API Key` });
      return;
    }
    setPendingBusy(task.id);
    setNotice(null);
    try {
      const result = await queryVideoTask({
        provider: task.provider,
        apiKey: setting.apiKey,
        ...(setting.baseUrl ? { baseUrl: setting.baseUrl } : {}),
        taskId: task.taskId,
        wait: true,
      });
      if ("error" in result) throw new Error(result.error);
      const url = result.videoUrls?.[0];
      if (!url) throw new Error("任务仍未完成，请稍后再查");
      await saveAsset(projectId, {
        shotId: task.shotId,
        sourceUrl: url,
        type: "ai_generated",
        provider: task.provider,
        model: task.model,
        ...(task.prompt ? { prompt: task.prompt } : {}),
      });
      await reloadAssets();
      setNotice({ kind: "ok", text: `任务已恢复，第 ${task.shotId} 镜视频已落库` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "恢复任务失败" });
    } finally {
      setPendingBusy(null);
    }
  }

  async function handleStockFill() {
    if (stockBusy) return;
    setStockBusy(true);
    setNotice(null);
    try {
      const result = await fillStock(projectId, { source: "all", mediaType: "auto" });
      await reloadAssets();
      setNotice({
        kind: "ok",
        text: `已自动填充 ${result.filled}/${result.total} 个镜头${result.sameSourceHits ? `（${result.sameSourceHits} 个同源素材）` : ""}`,
      });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "自动填充失败" });
    } finally {
      setStockBusy(false);
    }
  }

  async function handleBatchGenerate() {
    if (batchBusy) return;
    // 已锁定的镜头不参与批量生成（锁定 = 用户确认过的 take）。
    const targets = rows.filter((row) => !row.locked);
    if (targets.length === 0) {
      setNotice({ kind: "ok", text: "所有镜头都已锁定素材，批量生成不会覆盖它们。" });
      return;
    }
    if (!window.confirm(`将为 ${targets.length} 个未锁定镜头生成付费画面（并发 2），继续？`)) return;
    setBatchBusy(true);
    setNotice(null);
    let cursor = 0;
    const worker = async () => {
      while (cursor < targets.length) {
        const index = cursor;
        cursor += 1;
        await generateImageForRow(targets[index], true);
      }
    };
    try {
      await Promise.all([worker(), worker()]);
      await reloadAssets();
      setNotice({ kind: "ok", text: `批量生成结束：${targets.length} 个镜头已尝试生成，请检查失败项后重试。` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "批量生成失败" });
    } finally {
      setBatchBusy(false);
    }
  }

  async function runStoryboardGrid() {
    if (!script || filmBusy) return;
    if (!imageTarget) {
      setNotice({ kind: "error", text: "请先在设置中启用生图平台并选择默认生图模型。" });
      return;
    }
    setFilmBusy("grid");
    setNotice(null);
    setFilmPreview(null);
    try {
      const result = await generateStoryboardGrid(projectId, {
        scriptId: script.id,
        provider: imageTarget.provider,
        model: imageTarget.model,
        apiKey: imageTarget.apiKey,
        ...(imageTarget.baseUrl ? { baseUrl: imageTarget.baseUrl } : {}),
        // 九宫格本身是 9:16，切出来的每格也正好是 9:16。
        options: buildImageOptions({ ...imageParams, aspectRatio: "9:16" }),
        ...(characterSheet ? { characterSheetUrl: characterSheet } : {}),
        ...(project?.productImages[0] ? { productImageUrl: project.productImages[0] } : {}),
      });
      await reloadAssets();
      setNotice({
        kind: "ok",
        text: result.reused ? `已复用 ${result.count} 张匹配当前脚本的关键帧` : `已生成 ${result.count} 张关键帧`,
      });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "九宫格分镜生成失败" });
    } finally {
      setFilmBusy(null);
    }
  }

  async function runFilmPreview() {
    if (!script || filmBusy) return;
    if (!videoTarget) {
      setNotice({ kind: "error", text: "请先在设置中启用视频平台并选择默认生视频模型。" });
      return;
    }
    setFilmBusy("preview");
    setNotice(null);
    setFilmPreview(null);
    try {
      const preview = await previewFilm(projectId, {
        scriptId: script.id,
        provider: videoTarget.provider,
        model: videoTarget.model,
        ...(videoTarget.baseUrl ? { baseUrl: videoTarget.baseUrl } : {}),
        spendCapUsd,
        ...(characterSheet ? { characterSheetUrl: characterSheet } : {}),
      });
      setFilmPreview(preview);
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "整片估算失败" });
    } finally {
      setFilmBusy(null);
    }
  }

  const characterSheet = project?.characterId
    ? characters.find((item) => item.id === project.characterId)?.referenceImages?.[0]
    : undefined;
  const strategy = project?.creationBrief?.outputStrategy;
  const strategyHint =
    strategy === "draft"
      ? "草稿策略：只用商品图 / 免费素材，可直接去「视频」页合成，本页可跳过。"
      : strategy === "native-film"
        ? "原生整片：在脚本页用九宫格关键帧，本页无需逐镜生视频。"
        : strategy === "controlled-motion"
          ? "逐镜动态：生图 → 锁定关键帧 → 以图生视频；锁定的 take 不会被自动填充覆盖。"
          : "先生成画面或自动配免费素材；锁定的 take 不会被自动填充覆盖。";

  if (loading) {
    return (
      <section className="ai-video-page" aria-label="项目素材">
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载素材…</strong>
        </div>
      </section>
    );
  }

  if (loadError || !project) {
    return (
      <section className="ai-video-page" aria-label="项目素材">
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError || "项目不存在"}</strong>
          <button type="button" onClick={() => onNavigate("/projects")}>返回我的项目</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ai-video-page" aria-label="项目素材">
      <ProjectHeader project={project} step="assets" onNavigate={onNavigate} />

      <div className="ai-video-notice is-strategy" role="status">
        <span>
          {strategyHint}
          {!imageTarget && " 未检测到默认生图模型。"}
          {!videoTarget && " 未检测到默认生视频模型。"}
        </span>
      </div>

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <input
        ref={uploadRef}
        type="file"
        accept="image/*,video/*"
        hidden
        onChange={(event) => {
          void handleUpload(event.target.files);
          event.target.value = "";
        }}
      />

      <div className="ai-video-assets-toolbar">
        <button type="button" className="is-secondary" disabled={stockBusy} onClick={() => void handleStockFill()}>
          {stockBusy ? "正在配免费素材…" : "自动配免费素材"}
        </button>
        <button type="button" disabled={batchBusy} onClick={() => void handleBatchGenerate()}>
          {batchBusy ? "批量生成中…" : "批量生成缺省画面"}
        </button>
        <span className="ai-video-settings-message">
          {rows.filter((row) => row.locked).length}/{rows.length} 镜已锁定
        </span>
      </div>

      <LocalMaterialLibrary
        projectId={projectId}
        shots={rows.map((row) => ({ shotId: row.shot.shotId, description: row.shot.description }))}
        refreshKey={materialRevision}
        disabled={loading || batchBusy}
        onUse={applyMaterial}
        onFill={fillLocalMaterials}
      />

      <section className="ai-video-form-card" aria-label="关键帧与整片">
        <div className="ai-video-form-head">
          <h3>关键帧与整片</h3>
          <p>九宫格一次生图切出逐镜关键帧；整片估算只生成提示词与报价，不提交付费任务。</p>
        </div>
        <div className="ai-video-settings-actions">
          <button type="button" disabled={filmBusy !== null} onClick={() => void runStoryboardGrid()}>
            {filmBusy === "grid" ? "正在生成…" : "生成九宫格关键帧（付费生图）"}
          </button>
          <button type="button" className="is-secondary" disabled={filmBusy !== null} onClick={() => void runFilmPreview()}>
            {filmBusy === "preview" ? "正在估算…" : "估算整片花费（不提交）"}
          </button>
        </div>
        {filmPreview && (
          <div className="ai-video-notice" role="status">
            <span>
              整片估算：{filmPreview.shotCount} 镜 / {filmPreview.seconds}s · {filmPreview.model}
              {filmPreview.estimate
                ? ` · $${filmPreview.estimate.minUsd.toFixed(2)}–$${filmPreview.estimate.maxUsd.toFixed(2)}`
                : ""}
              {filmPreview.durationOverflow ? " · 脚本时长超出模型上限" : ""}
              {!filmPreview.referenceQuota.ok
                ? ` · 参考图超配额（${filmPreview.referenceQuota.count}/${filmPreview.referenceQuota.limit ?? "?"}）`
                : ""}
            </span>
            <button type="button" aria-label="关闭估算" onClick={() => setFilmPreview(null)}>×</button>
          </div>
        )}
      </section>

      {resumableTasks.length > 0 && (
        <section className="ai-video-form-card" aria-label="待恢复任务">
          <div className="ai-video-form-head">
            <h3>待恢复的付费任务（{resumableTasks.length}）</h3>
            <p>提交成功但等待超时的生视频任务。查询不会重复计费。</p>
          </div>
          <ul className="ai-video-pending-tasks">
            {resumableTasks.map((task) => (
              <li key={task.id}>
                <span>
                  第 {task.shotId} 镜 · {task.provider} / {task.model} · {task.status}
                </span>
                <button
                  type="button"
                  className="is-secondary"
                  disabled={pendingBusy !== null}
                  onClick={() => void resumeTask(task)}
                >
                  {pendingBusy === task.id ? "查询中…" : "查询并恢复"}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="ai-video-assets-grid">
        {rows.map((row) => {
          const busy = shotBusy[row.shot.shotId];
          const thumbnail = aiVideoMediaUrl(row.thumbnail);
          const error = shotErrors[row.shot.shotId];
          return (
            <article key={row.shot.shotId} className={`ai-video-asset-card is-${row.status}`}>
              <div className="ai-video-asset-thumb">
                {thumbnail ? (
                  <img src={thumbnail} alt={`第 ${row.shot.shotId} 镜`} loading="lazy" />
                ) : (
                  <span>{row.isVideo ? "▶ 视频素材" : "暂无画面"}</span>
                )}
                <div className="ai-video-asset-badges">
                  <em>第 {row.shot.shotId} 镜</em>
                  {row.locked && <i className="is-locked">已锁定</i>}
                  {row.isVideo && <i>视频</i>}
                </div>
              </div>

              <div className="ai-video-asset-body">
                <p className="ai-video-asset-desc">{row.shot.description}</p>
                <span className="ai-video-asset-meta">
                  {row.shot.type} · {row.shot.duration}s · {row.shot.visualSource}
                </span>

                {row.candidates.length > 0 && (
                  <div className="ai-video-candidates" aria-label="候选素材">
                    {row.candidates.slice(0, 6).map((asset) => {
                      const candidateUrl = aiVideoMediaUrl(asset.thumbnailPath ?? asset.filePath);
                      return (
                        <button
                          key={asset.id}
                          type="button"
                          className={asset.selected ? "is-active" : ""}
                          disabled={busy !== undefined || asset.status !== "done"}
                          title={asset.selected ? "已锁定" : "点击锁定这个 take"}
                          onClick={() => void lockAsset(row, asset)}
                        >
                          {candidateUrl ? <img src={candidateUrl} alt="" loading="lazy" /> : <span>▶</span>}
                        </button>
                      );
                    })}
                  </div>
                )}

                {error && <p className="ai-video-field-error">{error}</p>}

                <div className="ai-video-asset-actions">
                  <button type="button" disabled={busy !== undefined || batchBusy} onClick={() => void generateImageForRow(row)}>
                    {busy === "image" ? "生成中…" : row.display ? "重新生图" : "AI 生图"}
                  </button>
                  <button type="button" className="is-secondary" disabled={busy !== undefined} onClick={() => triggerUpload(row.shot.shotId)}>
                    {busy === "upload" ? "上传中…" : "上传"}
                  </button>
                  <button
                    type="button"
                    className="is-secondary"
                    disabled={busy !== undefined || !row.display || row.isVideo}
                    onClick={() => void generateVideoForRow(row)}
                  >
                    {busy === "video" ? "生视频中…" : "以图生视频"}
                  </button>
                  {busy === "video" && (
                    <button type="button" className="is-secondary" onClick={() => controllers.current.get(row.shot.shotId)?.abort()}>
                      取消等待
                    </button>
                  )}
                </div>

                {row.display && !row.isVideo && (
                  <div className="ai-video-retake">
                    <select
                      aria-label={`第 ${row.shot.shotId} 镜重拍诊断`}
                      value={retakes[row.shot.shotId] ?? ""}
                      disabled={busy !== undefined}
                      onChange={(event) =>
                        setRetakes((current) => ({
                          ...current,
                          [row.shot.shotId]: event.target.value as RetakeSymptom | "",
                        }))
                      }
                    >
                      <option value="">重拍诊断（可选）</option>
                      {RETAKE_SYMPTOMS.map((symptom) => (
                        <option key={symptom.id} value={symptom.id}>
                          {symptom.label.zh}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="is-secondary"
                      disabled={busy !== undefined || !retakes[row.shot.shotId]}
                      onClick={() => {
                        const symptom = retakes[row.shot.shotId];
                        if (symptom) void generateVideoForRow(row, symptom);
                      }}
                    >
                      诊断重拍（付费）
                    </button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {rows.length === 0 && (
        <div className="ai-video-empty">
          <span className="ai-video-empty-icon">✎</span>
          <strong>当前脚本还没有分镜</strong>
          <button type="button" onClick={() => onNavigate(`/project/${projectId}/script`)}>去脚本页</button>
        </div>
      )}
    </section>
  );
}

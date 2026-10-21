import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  composeVideo,
  getCompose,
  getCompositionTimeline,
  listCompositions,
  uploadBgm,
} from "../api/endpoints/compose";
import { listAssets } from "../api/endpoints/assets";
import { getProject } from "../api/endpoints/projects";
import { listScripts } from "../api/endpoints/scripts";
import { synthesizeFreeTts } from "../api/endpoints/tts";
import type {
  AspectRatio,
  Asset,
  Composition,
  CompositionResolution,
  ComposeInput,
  Project,
  Script,
} from "../api/types";
import { ProjectHeader } from "../components/ProjectHeader";
import { CAPTION_PRESET_IDS, type CaptionPresetId } from "../lib/caption-presets";
import {
  DEFAULT_RENDER_PRESET,
  RENDER_PRESETS,
  recommendPreset,
  type RenderPreset,
} from "../lib/compose-presets";
import {
  compositionChoiceLabel,
  compositionChoices,
  pickCompositionId,
} from "../lib/composition-timeline-view";
import { buildHookVariants } from "../lib/hook-variants";
import { aiVideoMediaUrl } from "../lib/media-url";
import { summarizeVoiceReport, voiceSourceExplanation, type VoiceReportSummary } from "../lib/project-detail-view";
import type { ProductCategory } from "../lib/script-categories";
import { isPaidTTSReady, resolveTTSConfig } from "../lib/tts-presets";
import { resolveVoiceoverRequest } from "../lib/voiceover-strategy";
import { useSettingsStore } from "../state/settingsStore";

const POLL_INTERVAL_MS = 2500;
const POLL_BUDGET = 240;
const MATRIX_LIMIT = 6;

const BGM_OPTIONS = [
  { value: "none", label: "无背景音乐" },
  { value: "upbeat", label: "轻快" },
  { value: "chill", label: "舒缓" },
  { value: "energetic", label: "燃" },
  { value: "emotional", label: "情绪" },
];

const FREE_VOICES = [
  { value: "zh-CN-XiaoxiaoNeural", label: "晓晓（女声）" },
  { value: "zh-CN-XiaoyiNeural", label: "晓伊（女声）" },
  { value: "zh-CN-YunxiNeural", label: "云希（男声）" },
  { value: "zh-CN-YunyangNeural", label: "云扬（男声）" },
  { value: "zh-CN-YunjianNeural", label: "云健（男声）" },
];

const CAPTION_LABELS: Record<CaptionPresetId, string> = {
  standard: "标准字幕",
  bold: "大字冲击",
  minimal: "极简",
  karaoke: "逐字高亮",
};

type Notice = { kind: "ok" | "error"; text: string } | null;

type PendingCompose = { compositionId: string; startedAt: number; label?: string };

type ComposeConfig = {
  aspectRatio: AspectRatio;
  resolution: CompositionResolution;
  renderPreset: RenderPreset;
  captionPreset: CaptionPresetId;
  bgm: string;
  bgmVolume: number;
  bgmDuck: boolean;
  ctaEnabled: boolean;
  ctaText: string;
  productCard: boolean;
  voiceGround: boolean;
  freeVoice: string;
};

type MatrixRow = { label: string; status: "composing" | "done" | "failed"; url?: string };

type ProjectVideoPageProps = {
  projectId: string;
  onNavigate: (path: string) => void;
};

function pendingKey(projectId: string) {
  return `mainpg.ai-video.compose.${projectId}`;
}

function readPending(projectId: string): PendingCompose | null {
  try {
    const raw = window.localStorage.getItem(pendingKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PendingCompose>;
    return typeof parsed.compositionId === "string"
      ? {
          compositionId: parsed.compositionId,
          startedAt: typeof parsed.startedAt === "number" ? parsed.startedAt : Date.now(),
          ...(typeof parsed.label === "string" ? { label: parsed.label } : {}),
        }
      : null;
  } catch {
    return null;
  }
}

/**
 * 视频页：时间线 + 合成设置（画幅/分辨率/字幕/BGM/配音）+ 按 compositionId 精确轮询 +
 * 成片版本预览下载 + 变体矩阵（每个版本独立轮询，互不覆盖）。
 */
export function ProjectVideoPage({ projectId, onNavigate }: ProjectVideoPageProps) {
  const { providers, tts, defaultResolution, defaultAspectRatio } = useSettingsStore();

  const [project, setProject] = useState<Project | null>(null);
  const [script, setScript] = useState<Script | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);

  const [compositions, setCompositions] = useState<Composition[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Composition | null>(null);
  const [voiceReport, setVoiceReport] = useState<VoiceReportSummary | null>(null);
  const [pending, setPending] = useState<PendingCompose | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const [bgmPath, setBgmPath] = useState<{ path: string; name: string } | null>(null);
  const [bgmUploading, setBgmUploading] = useState(false);
  const [previewingVoice, setPreviewingVoice] = useState(false);
  const [ttsOverride, setTtsOverride] = useState<boolean | null>(null);

  const [matrixOpen, setMatrixOpen] = useState(false);
  const [matrixHooks, setMatrixHooks] = useState<string[]>(["base"]);
  const [matrixCaptions, setMatrixCaptions] = useState<CaptionPresetId[]>([]);
  const [matrixBgms, setMatrixBgms] = useState<string[]>([]);
  const [matrixRunning, setMatrixRunning] = useState(false);
  const [matrixRows, setMatrixRows] = useState<MatrixRow[]>([]);

  const alive = useRef(true);
  const bgmInput = useRef<HTMLInputElement | null>(null);
  const resumeGuard = useRef(false);

  const [config, setConfig] = useState<ComposeConfig>(() => ({
    aspectRatio: defaultAspectRatio,
    resolution: defaultResolution,
    renderPreset: DEFAULT_RENDER_PRESET,
    captionPreset: "standard",
    bgm: "upbeat",
    bgmVolume: 0.18,
    bgmDuck: true,
    ctaEnabled: false,
    ctaText: "点击下方链接购买",
    productCard: false,
    voiceGround: true,
    freeVoice: FREE_VOICES[0].value,
  }));

  const thumbs = useMemo(() => {
    const map: Record<number, string> = {};
    for (const asset of assets) {
      if (asset.status !== "done") continue;
      const url = asset.thumbnailPath ?? asset.filePath;
      if (url && map[asset.shotId] == null) map[asset.shotId] = url;
    }
    return map;
  }, [assets]);

  const shots = script?.shots ?? [];
  const totalDuration = shots.reduce((sum, shot) => sum + (shot.duration || 0), 0);
  const recommendation = useMemo(
    () =>
      recommendPreset({
        shotCount: shots.length,
        totalDuration,
      }),
    [shots.length, totalDuration],
  );

  const audioStrategy = project?.creationBrief?.audioStrategy ?? null;
  const paidTtsReady = isPaidTTSReady(tts, providers);
  const voiceover = resolveVoiceoverRequest({ audioStrategy, manualTtsEnabled: ttsOverride });
  const voiceSource = voiceSourceExplanation({
    audioStrategy: ttsOverride === null ? audioStrategy : null,
    ttsEnabled: voiceover.ttsEnabled,
    paidTtsReady,
  });

  const waitForComposition = useCallback(
    async (compositionId: string, onTick?: (attempt: number) => void): Promise<Composition> => {
      for (let attempt = 0; attempt < POLL_BUDGET; attempt += 1) {
        const { composition } = await getCompose(projectId, compositionId);
        if (composition?.status === "done") return composition;
        if (composition?.status === "failed") throw new Error("合成失败，请检查素材与配音设置后重试");
        onTick?.(attempt);
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        if (!alive.current) throw new Error("页面已关闭");
      }
      throw new Error("合成等待超时，可稍后在成片版本里查看结果");
    },
    [projectId],
  );

  const reloadCompositions = useCallback(
    async (preferredId?: string) => {
      const { compositions: list } = await listCompositions(projectId);
      setCompositions(list ?? []);
      const choices = compositionChoices(list);
      setSelectedId((current) => pickCompositionId(choices, preferredId ?? current));
      return list ?? [];
    },
    [projectId],
  );

  const finishCompose = useCallback(
    async (composition: Composition) => {
      window.localStorage.removeItem(pendingKey(projectId));
      setPending(null);
      await reloadCompositions(composition.id);
      setNotice({ kind: "ok", text: "合成完成，成片已就绪" });
    },
    [projectId, reloadCompositions],
  );

  const runCompose = useCallback(
    async (input: ComposeInput, label = "手动合成") => {
      const accepted = await composeVideo(projectId, input);
      const next: PendingCompose = {
        compositionId: accepted.compositionId,
        startedAt: Date.now(),
        label,
      };
      window.localStorage.setItem(pendingKey(projectId), JSON.stringify(next));
      setPending(next);
      setNotice({ kind: "ok", text: "已提交合成，正在按成片 ID 轮询（可离开页面，回来会继续）" });
      try {
        const composition = await waitForComposition(accepted.compositionId);
        if (alive.current) await finishCompose(composition);
      } catch (cause) {
        if (alive.current) {
          window.localStorage.removeItem(pendingKey(projectId));
          setPending(null);
          setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "合成失败" });
        }
      }
    },
    [finishCompose, projectId, waitForComposition],
  );

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    setLoadError("");
    void (async () => {
      try {
        const [nextProject, scripts, nextAssets, list] = await Promise.all([
          getProject(projectId),
          listScripts(projectId),
          listAssets(projectId),
          listCompositions(projectId).catch(() => ({ compositions: [] as Composition[] })),
        ]);
        if (!alive.current) return;
        setProject(nextProject);
        setScript(scripts.find((item) => item.selected) ?? scripts[0] ?? null);
        setAssets(nextAssets);
        setCompositions(list.compositions ?? []);
        setSelectedId(pickCompositionId(compositionChoices(list.compositions)));
      } catch (cause) {
        if (alive.current) setLoadError(cause instanceof Error ? cause.message : "视频页加载失败");
      } finally {
        if (alive.current) setLoading(false);
      }
    })();
    return () => {
      alive.current = false;
    };
  }, [projectId]);

  // 合成中离开页面：把 compositionId 落在 localStorage，回来继续按同一个 ID 轮询。
  useEffect(() => {
    const stored = readPending(projectId);
    if (!stored || resumeGuard.current) return;
    resumeGuard.current = true;
    setPending(stored);
    setNotice({ kind: "ok", text: "检测到未完成的合成，正在继续查询结果…" });
    void waitForComposition(stored.compositionId)
      .then((composition) => (alive.current ? finishCompose(composition) : undefined))
      .catch((cause) => {
        if (!alive.current) return;
        window.localStorage.removeItem(pendingKey(projectId));
        setPending(null);
        setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "合成恢复失败" });
      });
  }, [finishCompose, projectId, waitForComposition]);

  // 合成中计时，刷新不再是「猜进度」。
  useEffect(() => {
    if (!pending) {
      setElapsed(0);
      return;
    }
    const tick = () => setElapsed(Math.max(0, Math.round((Date.now() - pending.startedAt) / 1000)));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [pending]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      setVoiceReport(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const [{ composition }, timeline] = await Promise.all([
          getCompose(projectId, selectedId),
          getCompositionTimeline(projectId, selectedId).catch(() => null),
        ]);
        if (cancelled) return;
        setSelected(composition);
        setVoiceReport(summarizeVoiceReport(timeline?.timeline));
      } catch {
        if (!cancelled) setSelected(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, selectedId]);

  function patchConfig(patch: Partial<ComposeConfig>) {
    setConfig((current) => ({ ...current, ...patch }));
  }

  const ttsInput = useMemo(() => {
    if (!voiceover.ttsEnabled) return {};
    if (paidTtsReady) return { ttsConfig: resolveTTSConfig(tts, providers) as unknown as Record<string, unknown> };
    return { freeTts: { enabled: true, voice: config.freeVoice } };
  }, [config.freeVoice, paidTtsReady, providers, tts, voiceover.ttsEnabled]);

  function buildInput(overrides: Partial<ComposeInput> = {}): ComposeInput {
    return {
      renderPreset: config.renderPreset,
      captionPreset: config.captionPreset,
      resolution: config.resolution,
      aspectRatio: config.aspectRatio,
      bgmVolume: config.bgmVolume,
      bgmDuck: config.bgmDuck,
      voiceGround: config.voiceGround,
      ...(project?.productImages[0] ? { productCard: config.productCard } : {}),
      ...(config.ctaEnabled && config.ctaText.trim() ? { ctaText: config.ctaText.trim() } : {}),
      ...(bgmPath ? { bgmPath: bgmPath.path } : config.bgm !== "none" ? { freeBgm: true, bgmMood: config.bgm } : {}),
      ...ttsInput,
      ...overrides,
    };
  }

  async function handleCompose() {
    if (pending || loading) return;
    if (shots.length === 0) {
      setNotice({ kind: "error", text: "当前脚本还没有分镜，请先在脚本页生成或编辑。" });
      return;
    }
    setNotice(null);
    try {
      await runCompose(buildInput(), config.renderPreset);
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "提交合成失败" });
    }
  }

  async function previewVoice() {
    if (previewingVoice) return;
    setPreviewingVoice(true);
    try {
      const blob = await synthesizeFreeTts("这是一段配音试听。", { voice: config.freeVoice });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch {
      setNotice({ kind: "error", text: "试听失败，请检查本地服务" });
    } finally {
      setPreviewingVoice(false);
    }
  }

  async function handleBgm(file: File | undefined) {
    if (!file) return;
    setBgmUploading(true);
    try {
      const uploaded = await uploadBgm(projectId, file);
      setBgmPath(uploaded);
      setNotice({ kind: "ok", text: `背景音乐已上传：${uploaded.name}` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "BGM 上传失败" });
    } finally {
      setBgmUploading(false);
    }
  }

  const hookVariants = useMemo(() => {
    if (shots.length === 0) return [];
    return buildHookVariants(
      { ...(script?.title ? { title: script.title } : {}), shots },
      (project?.productCategory ?? "") as ProductCategory,
      2,
    );
  }, [project?.productCategory, script?.title, shots]);

  const matrixCombos = useMemo(() => {
    const hooks = matrixHooks.length > 0 ? matrixHooks : ["base"];
    const captions = matrixCaptions.length > 0 ? matrixCaptions : [config.captionPreset];
    const bgms = matrixBgms.length > 0 ? matrixBgms : [config.bgm];
    const rows: Array<{ hookKey: string; hookLabel: string; caption: CaptionPresetId; bgm: string; label: string }> = [];
    for (const hook of hooks) {
      const hookLabel = hook === "base" ? "原开头" : hookVariants.find((item) => item.hookId === hook)?.hookName ?? hook;
      for (const caption of captions) {
        for (const bgm of bgms) {
          rows.push({
            hookKey: hook,
            hookLabel,
            caption,
            bgm,
            label: `${hookLabel} · ${CAPTION_LABELS[caption]} · ${bgm}`,
          });
        }
      }
    }
    return rows.slice(0, MATRIX_LIMIT);
  }, [config.bgm, config.captionPreset, hookVariants, matrixBgms, matrixCaptions, matrixHooks]);

  function toggleMatrixHook(key: string) {
    setMatrixHooks((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }

  function toggleMatrixCaption(key: CaptionPresetId) {
    setMatrixCaptions((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }

  function toggleMatrixBgm(key: string) {
    setMatrixBgms((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }

  async function runMatrix() {
    if (matrixRunning || matrixCombos.length === 0) return;
    if (matrixBgms.length === 0) setMatrixBgms([config.bgm !== "none" ? config.bgm : "upbeat"]);
    setMatrixRunning(true);
    setMatrixRows(matrixCombos.map((combo) => ({ label: combo.label, status: "composing" })));
    try {
      for (let index = 0; index < matrixCombos.length; index += 1) {
        const combo = matrixCombos[index];
        const variant = combo.hookKey === "base" ? undefined : hookVariants.find((item) => item.hookId === combo.hookKey);
        const hookShot = variant?.script.shots[0];
        const overrides: Partial<ComposeInput> = {
          label: combo.label,
          captionPreset: combo.caption,
          // 上传过 BGM 时沿用文件；否则按组合切换免费配乐情绪。
          ...(bgmPath ? {} : { bgmMood: combo.bgm, freeBgm: true }),
          ...(hookShot && hookShot.voiceover
            ? { voiceoverOverrides: [{ shotId: hookShot.shotId, voiceover: hookShot.voiceover }] }
            : {}),
        };
        try {
          const accepted = await composeVideo(projectId, buildInput(overrides));
          // 每个变体只按自己的 compositionId 轮询，顺序执行也不会串版本。
          const composition = await waitForComposition(accepted.compositionId);
          if (!alive.current) return;
          setMatrixRows((rows) =>
            rows.map((row, i) =>
              i === index
                ? { ...row, status: "done", ...(composition.url ? { url: composition.url } : {}) }
                : row,
            ),
          );
        } catch {
          if (!alive.current) return;
          setMatrixRows((rows) => rows.map((row, i) => (i === index ? { ...row, status: "failed" } : row)));
        }
      }
      await reloadCompositions();
      setNotice({ kind: "ok", text: `变体矩阵完成：${matrixCombos.length} 个版本` });
    } finally {
      if (alive.current) setMatrixRunning(false);
    }
  }

  if (loading) {
    return (
      <section className="ai-video-page" aria-label="项目视频">
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载视频页…</strong>
        </div>
      </section>
    );
  }

  if (loadError || !project) {
    return (
      <section className="ai-video-page" aria-label="项目视频">
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError || "项目不存在"}</strong>
          <button type="button" onClick={() => onNavigate("/projects")}>返回我的项目</button>
        </div>
      </section>
    );
  }

  const previewUrl = aiVideoMediaUrl(selected?.url);

  return (
    <section className="ai-video-page" aria-label="项目视频">
      <ProjectHeader project={project} step="video" onNavigate={onNavigate} />

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <div className="ai-video-video-layout">
        <section className="ai-video-compose-preview" aria-label="成片预览">
          <div className="ai-video-form-head">
            <h3>成片预览</h3>
            <p>
              {shots.length} 镜 · 约 {Math.round(totalDuration)}s · {config.aspectRatio} / {config.resolution}
            </p>
          </div>

          {compositions.length > 0 && (
            <label className="ai-video-field">
              成片版本
              <select
                value={selectedId ?? ""}
                onChange={(event) => setSelectedId(event.target.value || null)}
              >
                {compositionChoices(compositions).map((choice, index) => (
                  <option key={choice.id} value={choice.id}>
                    {compositionChoiceLabel(choice, index)}
                  </option>
                ))}
              </select>
            </label>
          )}

          {pending ? (
            <div className="ai-video-compose-progress" role="status">
              <strong>正在合成…</strong>
              <span>已等待 {elapsed}s · 成片 ID {pending.compositionId.slice(0, 8)}</span>
              <div className="ai-video-progress-track">
                <i style={{ width: `${Math.min(95, Math.max(4, elapsed * 2))}%` }} />
              </div>
              <p>可以离开页面，回来会按同一个成片 ID 继续查询，不会重复提交。</p>
            </div>
          ) : previewUrl ? (
            <>
              <video className="ai-video-compose-video" src={previewUrl} controls playsInline preload="metadata" />
              <div className="ai-video-settings-actions">
                <a href={previewUrl} download={selected?.fileName ?? undefined}>
                  下载成片
                </a>
                {selected?.timelineUrl && (
                  <a href={aiVideoMediaUrl(selected.timelineUrl) ?? "#"} target="_blank" rel="noreferrer">
                    查看时间线文件
                  </a>
                )}
              </div>
            </>
          ) : (
            <div className="ai-video-empty">
              <span className="ai-video-empty-icon">▶</span>
              <strong>还没有可预览的成片</strong>
              <p>确认右侧设置后点击「开始合成」。</p>
            </div>
          )}

          <div className="ai-video-time-line" aria-label="时间线">
            {shots.map((shot) => {
              const thumb = aiVideoMediaUrl(thumbs[shot.shotId]);
              return (
                <article key={shot.shotId} className="ai-video-clip">
                  <div className="ai-video-clip-thumb">
                    {thumb ? <img src={thumb} alt={`第 ${shot.shotId} 镜`} loading="lazy" /> : <span>{shot.shotId}</span>}
                  </div>
                  <strong>第 {shot.shotId} 镜 · {shot.duration}s</strong>
                  <span>{shot.type} · {shot.transition}</span>
                  <p>{shot.voiceover || shot.description}</p>
                </article>
              );
            })}
          </div>

          {voiceReport && (
            <div className="ai-video-notice" role="status">
              <span>
                音频报告：火山 {voiceReport.counts.volcengine} · Edge {voiceReport.counts.edge} · 原生{" "}
                {voiceReport.counts.native} · 无 {voiceReport.counts.none}
                {voiceReport.hasFailures ? ` · ${voiceReport.failedShotIds.length} 镜降级` : ""}
              </span>
            </div>
          )}
        </section>

        <section className="ai-video-compose-settings" aria-label="合成设置">
          <div className="ai-video-form-card">
            <div className="ai-video-form-head">
              <h3>画幅与画质</h3>
              <p>推荐：{recommendation.reason}</p>
            </div>
            <label className="ai-video-field">
              画幅
              <select
                value={config.aspectRatio}
                onChange={(event) => patchConfig({ aspectRatio: event.target.value as AspectRatio })}
              >
                <option value="9:16">9:16 竖屏</option>
                <option value="16:9">16:9 横屏</option>
                <option value="1:1">1:1 方形</option>
              </select>
            </label>
            <label className="ai-video-field">
              渲染档位
              <select
                value={config.renderPreset}
                onChange={(event) => {
                  const preset = event.target.value as RenderPreset;
                  patchConfig({ renderPreset: preset, resolution: RENDER_PRESETS[preset].resolution });
                }}
              >
                <option value="fast">快速（720p）</option>
                <option value="standard">标准（1080p）</option>
                <option value="hd">高清（1080p 慢编码）</option>
              </select>
            </label>
            <label className="ai-video-field">
              分辨率
              <select
                value={config.resolution}
                onChange={(event) => patchConfig({ resolution: event.target.value as CompositionResolution })}
              >
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
              </select>
            </label>
            <label className="ai-video-field">
              字幕样式
              <select
                value={config.captionPreset}
                onChange={(event) => patchConfig({ captionPreset: event.target.value as CaptionPresetId })}
              >
                {CAPTION_PRESET_IDS.map((id) => (
                  <option key={id} value={id}>
                    {CAPTION_LABELS[id]}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="ai-video-form-card">
            <div className="ai-video-form-head">
              <h3>配音</h3>
              <p>{voiceSource.label}：{voiceSource.detail}</p>
            </div>
            <label className="ai-video-check">
              <input
                type="checkbox"
                checked={voiceover.ttsEnabled}
                onChange={(event) => setTtsOverride(event.target.checked)}
              />
              本次合成生成人声（{voiceover.source === "manual" ? "手动覆盖" : "跟随项目策略"}）
            </label>
            {!paidTtsReady && voiceover.ttsEnabled && (
              <label className="ai-video-field">
                免费音色
                <select
                  value={config.freeVoice}
                  onChange={(event) => patchConfig({ freeVoice: event.target.value })}
                >
                  {FREE_VOICES.map((voice) => (
                    <option key={voice.value} value={voice.value}>
                      {voice.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="ai-video-settings-actions">
              <button type="button" className="is-secondary" disabled={previewingVoice} onClick={() => void previewVoice()}>
                {previewingVoice ? "试听中…" : "试听免费音色"}
              </button>
            </div>
          </div>

          <div className="ai-video-form-card">
            <div className="ai-video-form-head">
              <h3>背景音乐</h3>
              <p>{bgmPath ? `已使用上传音乐：${bgmPath.name}` : "未上传时按情绪自动配乐。"}</p>
            </div>
            <label className="ai-video-field">
              情绪
              <select value={config.bgm} onChange={(event) => patchConfig({ bgm: event.target.value })}>
                {BGM_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="ai-video-field">
              音量 {Math.round(config.bgmVolume * 100)}%
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={config.bgmVolume}
                onChange={(event) => patchConfig({ bgmVolume: Number(event.target.value) })}
              />
            </label>
            <label className="ai-video-check">
              <input
                type="checkbox"
                checked={config.bgmDuck}
                onChange={(event) => patchConfig({ bgmDuck: event.target.checked })}
              />
              旁白时自动压低 BGM
            </label>
            <div className="ai-video-settings-actions">
              <button type="button" className="is-secondary" disabled={bgmUploading} onClick={() => bgmInput.current?.click()}>
                {bgmUploading ? "上传中…" : "上传 BGM"}
              </button>
              {bgmPath && (
                <button type="button" className="is-secondary" onClick={() => setBgmPath(null)}>
                  移除上传音乐
                </button>
              )}
              <input
                ref={bgmInput}
                type="file"
                accept="audio/*"
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  void handleBgm(file);
                }}
              />
            </div>
          </div>

          <div className="ai-video-form-card">
            <div className="ai-video-form-head">
              <h3>带货贴片</h3>
              <p>商品图存在时才写入商品卡；CTA 为空时不加片尾贴片。</p>
            </div>
            <label className="ai-video-check">
              <input
                type="checkbox"
                checked={config.ctaEnabled}
                onChange={(event) => patchConfig({ ctaEnabled: event.target.checked })}
              />
              片尾购买 CTA
            </label>
            {config.ctaEnabled && (
              <label className="ai-video-field">
                CTA 文案
                <input
                  value={config.ctaText}
                  maxLength={40}
                  onChange={(event) => patchConfig({ ctaText: event.target.value })}
                />
              </label>
            )}
            <label className="ai-video-check">
              <input
                type="checkbox"
                disabled={!project.productImages[0]}
                checked={config.productCard}
                onChange={(event) => patchConfig({ productCard: event.target.checked })}
              />
              左下角商品卡
            </label>
            <label className="ai-video-check">
              <input
                type="checkbox"
                checked={config.voiceGround}
                onChange={(event) => patchConfig({ voiceGround: event.target.checked })}
              />
              人声落地感（去播音腔 + 房间底噪）
            </label>
          </div>

          <div className="ai-video-settings-actions">
            <button type="button" disabled={Boolean(pending)} onClick={() => void handleCompose()}>
              {pending ? "合成中…" : "开始合成"}
            </button>
            {pending && (
              <button
                type="button"
                className="is-secondary"
                onClick={() => {
                  // 只停止本页等待；服务端任务与 pending 记录保留，回来继续查询。
                  setNotice({ kind: "ok", text: "已转为后台合成，稍后回到本页会继续查询结果。" });
                  setPending(null);
                }}
              >
                转到后台
              </button>
            )}
          </div>
        </section>
      </div>

      <section className="ai-video-form-card" aria-label="变体矩阵">
        <div className="ai-video-form-head">
          <h3>变体矩阵</h3>
          <p>同一批素材只换开头钩子 / 字幕 / BGM，一次生成多个可对比版本（最多 {MATRIX_LIMIT} 个，逐个合成、各自轮询）。</p>
        </div>
        <div className="ai-video-settings-actions">
          <button type="button" className="is-secondary" onClick={() => setMatrixOpen((value) => !value)}>
            {matrixOpen ? "收起变体矩阵" : "展开变体矩阵"}
          </button>
        </div>

        {matrixOpen && (
          <>
            <div className="ai-video-matrix-group">
              <strong>开头钩子</strong>
              <label className="ai-video-check">
                <input
                  type="checkbox"
                  checked={matrixHooks.includes("base")}
                  onChange={() => toggleMatrixHook("base")}
                />
                原开头
              </label>
              {hookVariants.map((variant) => (
                <label key={variant.hookId} className="ai-video-check">
                  <input
                    type="checkbox"
                    checked={matrixHooks.includes(variant.hookId)}
                    onChange={() => toggleMatrixHook(variant.hookId)}
                  />
                  {variant.hookName}
                </label>
              ))}
            </div>
            <div className="ai-video-matrix-group">
              <strong>字幕</strong>
              {CAPTION_PRESET_IDS.map((id) => (
                <label key={id} className="ai-video-check">
                  <input
                    type="checkbox"
                    checked={matrixCaptions.includes(id)}
                    onChange={() => toggleMatrixCaption(id)}
                  />
                  {CAPTION_LABELS[id]}
                </label>
              ))}
            </div>
            <div className="ai-video-matrix-group">
              <strong>BGM</strong>
              {BGM_OPTIONS.filter((option) => option.value !== "none").map((option) => (
                <label key={option.value} className="ai-video-check">
                  <input
                    type="checkbox"
                    checked={matrixBgms.includes(option.value)}
                    onChange={() => toggleMatrixBgm(option.value)}
                  />
                  {option.label}
                </label>
              ))}
            </div>
            <p className="ai-video-settings-message">
              当前组合 {matrixCombos.length} 个（超出上限会截断）。
            </p>
            <div className="ai-video-settings-actions">
              <button type="button" disabled={matrixRunning || matrixCombos.length === 0} onClick={() => void runMatrix()}>
                {matrixRunning ? "矩阵合成中…" : "开始矩阵合成"}
              </button>
            </div>
            {matrixRows.length > 0 && (
              <ul className="ai-video-pending-tasks">
                {matrixRows.map((row, index) => (
                  <li key={`${row.label}-${index}`}>
                    <span>{row.label}</span>
                    <span>
                      {row.status === "done" ? "已完成" : row.status === "failed" ? "失败" : "合成中…"}
                      {row.url && (
                        <>
                          {" · "}
                          <a href={aiVideoMediaUrl(row.url) ?? "#"} target="_blank" rel="noreferrer">
                            预览
                          </a>
                        </>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </section>
  );
}

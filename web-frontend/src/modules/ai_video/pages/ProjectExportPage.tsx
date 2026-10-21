import { useCallback, useEffect, useMemo, useState } from "react";

import { composeVideo, getCompose, listCompositions } from "../api/endpoints/compose";
import {
  downloadCreditsMarkdown,
  exportForPlatform,
  generateCarousel,
  generateContactSheet,
  generateCover,
  generateDub,
  generateEndCard,
  generateNativeFeel,
  generatePublishCopy,
  generateShopQr,
  getCredits,
  listMetrics,
  recordMetrics,
  runGate,
  runQc,
} from "../api/endpoints/export";
import { getProject } from "../api/endpoints/projects";
import { listScripts } from "../api/endpoints/scripts";
import type {
  Composition,
  CreditsManifest,
  GateResult,
  PlatformExportPlatform,
  Project,
  PublishMetric,
  QcReport,
  Script,
} from "../api/types";
import { ProjectHeader } from "../components/ProjectHeader";
import {
  OUTPUT_STRATEGY_GROUP_LABELS,
  compositionStrategyLabel,
  groupCompositionsByStrategy,
} from "../lib/project-detail-view";
import { aiVideoMediaUrl } from "../lib/media-url";
import { compositionChoiceLabel } from "../lib/composition-timeline-view";
import { buildAiDeclaration, buildPublishPack } from "../lib/publish-pack";
import { useBrandStore } from "../state/brandStore";
import { useSettingsStore } from "../state/settingsStore";

const PLATFORMS: Array<{ id: PlatformExportPlatform; label: string }> = [
  { id: "douyin", label: "抖音 9:16" },
  { id: "kuaishou", label: "快手 9:16" },
  { id: "xiaohongshu", label: "小红书 3:4" },
  { id: "shipinhao", label: "视频号 9:16" },
  { id: "tiktok", label: "TikTok Shop 9:16" },
  { id: "reels", label: "Instagram Reels 9:16" },
  { id: "shorts", label: "YouTube Shorts 9:16" },
];

const AB_PRESETS = [
  { key: "karaoke", label: "逐字字幕 · 轻快 BGM", compose: { karaoke: true, bgmMood: "upbeat" } },
  { key: "rapid", label: "标准字幕 · 燃 BGM", compose: { bgmMood: "energetic" } },
];

type Notice = { kind: "ok" | "error"; text: string } | null;

type ToolState = {
  busy?: boolean;
  error?: string;
  note?: string;
  images?: string[];
  video?: string;
  warning?: string;
  shopLink?: string;
  cuts?: number;
  proxy?: string;
};

type ExportPageProps = {
  projectId: string;
  onNavigate: (path: string) => void;
};

/** 导出页：成片分组、平台导出、发布文案、QC/门禁、授权清单、A/B 与更多产出。 */
export function ProjectExportPage({ projectId, onNavigate }: ExportPageProps) {
  const { llm } = useSettingsStore();
  const { brand } = useBrandStore();

  const [project, setProject] = useState<Project | null>(null);
  const [script, setScript] = useState<Script | null>(null);
  const [compositions, setCompositions] = useState<Composition[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);

  const [publishBusy, setPublishBusy] = useState(false);
  const [publish, setPublish] = useState<{
    titles: string[];
    hashtags: string[];
    caption: string;
    pinned?: string;
    objections?: Array<{ q?: string; a?: string }>;
    notice?: string;
    aiLine?: string;
  } | null>(null);

  const [platform, setPlatform] = useState<PlatformExportPlatform>("douyin");
  const [platformBusy, setPlatformBusy] = useState(false);
  const [platformResult, setPlatformResult] = useState<{ url: string; name: string; size: string } | null>(null);

  const [qcBusy, setQcBusy] = useState(false);
  const [qc, setQc] = useState<QcReport | null>(null);
  const [gateBusy, setGateBusy] = useState(false);
  const [gate, setGate] = useState<GateResult | null>(null);

  const [creditsBusy, setCreditsBusy] = useState(false);
  const [credits, setCredits] = useState<CreditsManifest | null>(null);

  const [abBusy, setAbBusy] = useState(false);
  const [abRows, setAbRows] = useState<Array<{ label: string; status: "running" | "done" | "failed"; url?: string }>>([]);

  const [tools, setTools] = useState<Record<string, ToolState>>({});
  const [coverTitle, setCoverTitle] = useState("");
  const [wantProxy, setWantProxy] = useState(false);
  const [feelStrength, setFeelStrength] = useState<"subtle" | "medium" | "strong">("subtle");
  const [feelHalation, setFeelHalation] = useState(false);
  const [feelPhoneCompress, setFeelPhoneCompress] = useState(false);
  const [dubLang, setDubLang] = useState("en");

  const [metricDraft, setMetricDraft] = useState({ views: "", likes: "", comments: "", shares: "", orders: "", note: "" });
  const [metricsBusy, setMetricsBusy] = useState(false);
  const [metrics, setMetrics] = useState<PublishMetric[]>([]);

  const setTool = useCallback((key: string, patch: Partial<ToolState>) => {
    setTools((current) => ({ ...current, [key]: { ...current[key], ...patch } }));
  }, []);

  const copyText = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setNotice({ kind: "ok", text: "已复制" });
      } catch {
        setNotice({ kind: "error", text: "复制失败，请手动选择文本" });
      }
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    void (async () => {
      try {
        const [nextProject, scripts, list, metricList] = await Promise.all([
          getProject(projectId),
          listScripts(projectId),
          listCompositions(projectId),
          listMetrics(projectId).catch(() => ({ metrics: [] as PublishMetric[] })),
        ]);
        if (cancelled) return;
        setProject(nextProject);
        setScript(scripts.find((item) => item.selected) ?? scripts[0] ?? null);
        setCompositions(list.compositions ?? []);
        setSelectedId(list.compositions?.[0]?.id ?? null);
        setMetrics(metricList.metrics ?? []);
        setCoverTitle(nextProject.productName ?? nextProject.name);
      } catch (cause) {
        if (!cancelled) setLoadError(cause instanceof Error ? cause.message : "导出页加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const selected = compositions.find((item) => item.id === selectedId) ?? compositions[0] ?? null;
  const strategy = project?.creationBrief?.outputStrategy ?? null;
  const groups = useMemo(
    () => groupCompositionsByStrategy(compositions, strategy),
    [compositions, strategy],
  );

  const mediaUrl = aiVideoMediaUrl(selected?.url ?? null);
  const absoluteMediaUrl = mediaUrl
    ? new URL(mediaUrl, window.location.origin).toString()
    : "";

  function applyTemplateCopy() {
    if (!project) return false;
    const pack = buildPublishPack({
      productName: project.productName || project.name,
      category: project.productCategory ?? "other",
      sellingPoints: project.productDescription ?? undefined,
      platform,
      locale: "zh",
    });
    setPublish({
      titles: pack.titles,
      hashtags: pack.hashtags,
      caption: pack.caption,
      pinned: pack.commentKit.pinned,
      objections: pack.commentKit.objections,
      notice: pack.commentKit.notice,
      aiLine: pack.aiDeclaration.line,
    });
    return true;
  }

  function useTemplateCopy() {
    if (!applyTemplateCopy()) return;
    setNotice({ kind: "ok", text: "已生成模板文案（不调用 LLM）" });
  }

  async function generatePublish() {
    if (!project) return;
    if (!llm.apiKey) {
      const fallback = applyTemplateCopy();
      setNotice({
        kind: fallback ? "ok" : "error",
        text: fallback
          ? "未配置 LLM，已生成模板文案；配置后可生成更贴合商品的版本。"
          : "请先在设置中配置 LLM（baseUrl / API Key / 模型）后生成发布文案。",
      });
      return;
    }
    setPublishBusy(true);
    try {
      const copy = await generatePublishCopy({
        productName: project.productName || project.name,
        category: project.productCategory,
        productDescription: project.productDescription,
        locale: "zh",
        llmConfig: { baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model },
      });
      setPublish({
        titles: copy.titles ?? [],
        hashtags: copy.hashtags ?? [],
        caption: copy.caption ?? "",
        ...(copy.commentKit?.pinned ? { pinned: copy.commentKit.pinned } : {}),
        ...(copy.commentKit?.objections ? { objections: copy.commentKit.objections } : {}),
        ...(copy.commentKit?.notice ? { notice: copy.commentKit.notice } : {}),
        aiLine: buildAiDeclaration("zh").line,
      });
      setNotice(null);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "发布文案生成失败";
      const fallback = applyTemplateCopy();
      setNotice({
        kind: fallback ? "ok" : "error",
        text: fallback ? `${message}；已切换为模板文案，可直接复制发布。` : message,
      });
    } finally {
      setPublishBusy(false);
    }
  }

  async function runPlatformExport() {
    if (!selected) {
      setNotice({ kind: "error", text: "还没有成片，请先在视频页合成。" });
      return;
    }
    setPlatformBusy(true);
    setPlatformResult(null);
    try {
      const result = await exportForPlatform(projectId, { compositionId: selected.id, platform });
      setPlatformResult({ url: result.url, name: result.platformName, size: result.size });
      setNotice({ kind: "ok", text: `${result.platformName} 导出完成（${result.size}）` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "平台导出失败" });
    } finally {
      setPlatformBusy(false);
    }
  }

  async function runQualityCheck() {
    if (!selected) return;
    setQcBusy(true);
    try {
      setQc(await runQc(projectId, selected.id));
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "质检失败" });
    } finally {
      setQcBusy(false);
    }
  }

  async function runReleaseGate() {
    if (!selected) return;
    setGateBusy(true);
    try {
      setGate(await runGate(projectId, selected.id));
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "门禁检查失败" });
    } finally {
      setGateBusy(false);
    }
  }

  async function loadCredits() {
    setCreditsBusy(true);
    try {
      setCredits(await getCredits(projectId));
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "授权清单读取失败" });
    } finally {
      setCreditsBusy(false);
    }
  }

  async function downloadCredits() {
    try {
      const blob = await downloadCreditsMarkdown(projectId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `credits-${projectId}.md`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "授权清单下载失败" });
    }
  }

  const pollComposition = useCallback(
    async (compositionId: string) => {
      for (let attempt = 0; attempt < 240; attempt += 1) {
        const { composition } = await getCompose(projectId, compositionId);
        if (composition?.status === "done" && composition.url) return composition;
        if (composition?.status === "failed") throw new Error("合成失败");
        await new Promise((resolve) => setTimeout(resolve, 2500));
      }
      throw new Error("合成等待超时");
    },
    [projectId],
  );

  async function runAb() {
    if (abBusy) return;
    setAbBusy(true);
    setAbRows(AB_PRESETS.map((preset) => ({ label: preset.label, status: "running" })));
    for (let index = 0; index < AB_PRESETS.length; index += 1) {
      const preset = AB_PRESETS[index];
      try {
        const accepted = await composeVideo(projectId, {
          resolution: selected?.resolution === "720p" ? "720p" : "1080p",
          aspectRatio: selected?.aspectRatio ?? "9:16",
          freeTts: { enabled: true },
          freeBgm: true,
          ...preset.compose,
        });
        const composition = await pollComposition(accepted.compositionId);
        setAbRows((rows) =>
          rows.map((row, i) => (i === index ? { ...row, status: "done", ...(composition.url ? { url: composition.url } : {}) } : row)),
        );
      } catch {
        setAbRows((rows) => rows.map((row, i) => (i === index ? { ...row, status: "failed" } : row)));
      }
    }
    setAbBusy(false);
    await listCompositions(projectId)
      .then((result) => setCompositions(result.compositions ?? []))
      .catch(() => undefined);
  }

  async function runCover() {
    setTool("cover", { busy: true, error: undefined, images: undefined });
    try {
      const result = await generateCover(projectId, coverTitle.trim() || project?.name || "封面");
      setTool("cover", { busy: false, images: [result.cover] });
    } catch (cause) {
      setTool("cover", { busy: false, error: cause instanceof Error ? cause.message : "封面生成失败" });
    }
  }

  async function runCarousel() {
    setTool("carousel", { busy: true, error: undefined, images: undefined });
    try {
      const result = await generateCarousel(projectId);
      setTool("carousel", { busy: false, images: result.cards ?? [] });
    } catch (cause) {
      setTool("carousel", { busy: false, error: cause instanceof Error ? cause.message : "图文卡片生成失败" });
    }
  }

  async function runShopQr() {
    setTool("qr", { busy: true, error: undefined, images: undefined });
    try {
      const result = await generateShopQr(projectId);
      setTool("qr", {
        busy: false,
        images: [result.qr],
        ...(result.shopLink ? { shopLink: result.shopLink } : {}),
        ...(result.warning ? { warning: result.warning.zh } : {}),
      });
    } catch (cause) {
      setTool("qr", { busy: false, error: cause instanceof Error ? cause.message : "二维码生成失败" });
    }
  }

  async function runEndCard() {
    setTool("endcard", { busy: true, error: undefined, video: undefined });
    try {
      const result = await generateEndCard(projectId);
      setTool("endcard", {
        busy: false,
        video: result.video,
        ...(result.shopLink ? { shopLink: result.shopLink } : {}),
        ...(result.warning ? { warning: result.warning.zh } : {}),
      });
    } catch (cause) {
      setTool("endcard", { busy: false, error: cause instanceof Error ? cause.message : "片尾卡生成失败" });
    }
  }

  async function runContactSheet() {
    setTool("sheet", { busy: true, error: undefined, images: undefined });
    try {
      const result = await generateContactSheet(projectId, wantProxy);
      setTool("sheet", {
        busy: false,
        images: [result.sheet],
        ...(result.proxy ? { proxy: result.proxy } : {}),
        cuts: result.cuts?.length ?? 0,
      });
    } catch (cause) {
      setTool("sheet", { busy: false, error: cause instanceof Error ? cause.message : "联系表生成失败" });
    }
  }

  async function runNativeFeel() {
    setTool("feel", { busy: true, error: undefined, video: undefined });
    try {
      const result = await generateNativeFeel(projectId, {
        strength: feelStrength,
        halation: feelHalation,
        phoneCompress: feelPhoneCompress,
      });
      setTool("feel", { busy: false, video: result.video });
    } catch (cause) {
      setTool("feel", { busy: false, error: cause instanceof Error ? cause.message : "原生感处理失败" });
    }
  }

  async function runDub() {
    if (!llm.baseUrl || !llm.model) {
      setTool("dub", { busy: false, error: "请先在设置中配置 LLM（baseUrl / 模型）" });
      return;
    }
    setTool("dub", { busy: true, error: undefined, note: undefined });
    try {
      const result = await generateDub(projectId, {
        targetLang: dubLang,
        llmConfig: { baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model },
      });
      setTool("dub", {
        busy: false,
        note: `已生成第 ${result.version} 版 ${result.targetLang} 脚本（推荐音色：${result.recommendedVoice}），到脚本页切换后重新合成。`,
      });
    } catch (cause) {
      setTool("dub", { busy: false, error: cause instanceof Error ? cause.message : "译制失败" });
    }
  }

  async function saveMetrics() {
    setMetricsBusy(true);
    try {
      const toNumber = (value: string) => (value.trim() ? Math.max(0, Math.floor(Number(value) || 0)) : undefined);
      const result = await recordMetrics(projectId, {
        platform,
        views: toNumber(metricDraft.views),
        likes: toNumber(metricDraft.likes),
        comments: toNumber(metricDraft.comments),
        shares: toNumber(metricDraft.shares),
        orders: toNumber(metricDraft.orders),
        ...(metricDraft.note.trim() ? { note: metricDraft.note.trim() } : {}),
      });
      setMetrics((current) => [result.metric, ...current]);
      setMetricDraft({ views: "", likes: "", comments: "", shares: "", orders: "", note: "" });
      setNotice({ kind: "ok", text: "数据已记录" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "数据记录失败" });
    } finally {
      setMetricsBusy(false);
    }
  }

  function renderComposition(composition: Composition, index: number) {
    const url = aiVideoMediaUrl(composition.url);
    return (
      <article key={composition.id} className={`ai-video-export-version${selected?.id === composition.id ? " is-active" : ""}`}>
        <div className="ai-video-export-version-head">
          <strong>
            {compositionChoiceLabel(
              {
                id: composition.id,
                fileName: composition.fileName ?? null,
                label: composition.label ?? null,
                createdAt: composition.createdAt,
              },
              index,
            )}
          </strong>
          <em>{compositionStrategyLabel(composition)}</em>
        </div>
        <span className="ai-video-asset-meta">
          {composition.resolution ?? "?"} · {composition.aspectRatio ?? "?"} ·{" "}
          {composition.duration ? `${Math.round(composition.duration / 1000)}s` : "时长未知"}
        </span>
        <div className="ai-video-settings-actions">
          <button type="button" className={selected?.id === composition.id ? "" : "is-secondary"} onClick={() => setSelectedId(composition.id)}>
            {selected?.id === composition.id ? "当前导出" : "选为导出"}
          </button>
          {url && (
            <a href={url} download={composition.fileName ?? undefined}>
              下载
            </a>
          )}
        </div>
      </article>
    );
  }

  if (loading) {
    return (
      <section className="ai-video-page" aria-label="项目导出">
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载导出页…</strong>
        </div>
      </section>
    );
  }

  if (loadError || !project) {
    return (
      <section className="ai-video-page" aria-label="项目导出">
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError || "项目不存在"}</strong>
          <button type="button" onClick={() => onNavigate("/projects")}>返回我的项目</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ai-video-page ai-video-export-page" aria-label="项目导出">
      <ProjectHeader project={project} step="export" onNavigate={onNavigate} />

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <div className="ai-video-export-layout">
        <section className="ai-video-form-card ai-video-export-hero" aria-label="成片版本">
          <div className="ai-video-form-head">
            <h3>成片版本</h3>
            <p>按项目出片策略分组；导出、质检与门禁都只作用于当前选中的版本。</p>
          </div>
          {compositions.length === 0 ? (
            <div className="ai-video-empty">
              <span className="ai-video-empty-icon">▶</span>
              <strong>还没有成片</strong>
              <button type="button" onClick={() => onNavigate(`/project/${projectId}/video`)}>去合成</button>
            </div>
          ) : (
            <>
              {groups.primary.length > 0 && strategy && (
                <div className="ai-video-export-group">
                  <h4>{OUTPUT_STRATEGY_GROUP_LABELS[strategy]}（主版本）</h4>
                  {groups.primary.map(renderComposition)}
                </div>
              )}
              {groups.others.length > 0 && (
                <div className="ai-video-export-group">
                  <h4>{groups.primary.length > 0 ? "其他版本" : "全部版本"}</h4>
                  {groups.others.map(renderComposition)}
                </div>
              )}
            </>
          )}

          {mediaUrl && (
            <>
              <video className="ai-video-compose-video" src={mediaUrl} controls playsInline preload="metadata" />
              <div className="ai-video-settings-actions">
                <a className="ai-video-action-link" href={mediaUrl} download={selected?.fileName ?? undefined}>下载成片</a>
                <button type="button" className="is-secondary" onClick={() => void copyText(absoluteMediaUrl)}>
                  复制链接
                </button>
              </div>
            </>
          )}
        </section>

        <div className="ai-video-export-row">
          <section className="ai-video-form-card" aria-label="发布文案">
          <div className="ai-video-form-head">
            <h3>发布文案</h3>
            <p>{llm.apiKey ? `LLM：${llm.model || "未选模型"}` : "未配置 LLM，生成按钮会提示先配置。"}</p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" disabled={publishBusy} onClick={() => void generatePublish()}>
              {publishBusy ? "生成中…" : "生成发布文案"}
            </button>
            <button type="button" className="is-secondary" disabled={publishBusy || !project} onClick={useTemplateCopy}>
              模板文案（不调用 LLM）
            </button>
          </div>
          {publish && (
            <div className="ai-video-publish-copy">
              <ul>
                {publish.titles.map((title) => (
                  <li key={title}>
                    <span>{title}</span>
                    <button type="button" className="is-secondary" onClick={() => void copyText(title)}>复制</button>
                  </li>
                ))}
              </ul>
              <p>{publish.hashtags.join(" ")}</p>
              <p>{publish.caption}</p>
              <div className="ai-video-settings-actions">
                <button type="button" className="is-secondary" onClick={() => void copyText(publish.hashtags.join(" "))}>
                  复制话题
                </button>
                <button type="button" className="is-secondary" onClick={() => void copyText(publish.caption)}>
                  复制正文
                </button>
              </div>
              {(publish.pinned || publish.objections?.length) && (
                <div className="ai-video-publish-comments">
                  {publish.pinned && <p>置顶：{publish.pinned}</p>}
                  {publish.objections?.map((item, index) => (
                    <p key={index}>Q：{item.q}　A：{item.a}</p>
                  ))}
                  {publish.notice && <p className="ai-video-settings-message">{publish.notice}</p>}
                  {publish.aiLine && <p className="ai-video-settings-message">AIGC 声明：{publish.aiLine}</p>}
                </div>
              )}
            </div>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="平台导出">
          <div className="ai-video-form-head">
            <h3>平台导出</h3>
            <p>按目标平台规格转码，避免上传后被二次压缩。</p>
          </div>
          <div className="ai-video-settings-actions">
            <select value={platform} onChange={(event) => setPlatform(event.target.value as PlatformExportPlatform)}>
              {PLATFORMS.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
            <button type="button" disabled={platformBusy || !selected} onClick={() => void runPlatformExport()}>
              {platformBusy ? "导出中…" : "导出到该平台"}
            </button>
          </div>
          {platformResult && (
            <div className="ai-video-publish-copy">
              <p>{platformResult.name} · {platformResult.size}</p>
              <div className="ai-video-settings-actions">
                <a className="ai-video-action-link" href={aiVideoMediaUrl(platformResult.url) ?? "#"} download>下载导出文件</a>
                <button
                  type="button"
                  className="is-secondary"
                  onClick={() => void copyText(new URL(aiVideoMediaUrl(platformResult.url) ?? "", window.location.origin).toString())}
                >
                  复制链接
                </button>
              </div>
            </div>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="品牌元素">
          <div className="ai-video-form-head">
            <h3>品牌元素</h3>
            <p>与设置页共用同一份品牌 store；当前 sidecar 合成不烧录水印，导出前可据此人工核对。</p>
          </div>
          <p className="ai-video-settings-message">
            {brand.name} ·{" "}
            {brand.watermark.enabled
              ? `水印已启用（${brand.watermark.position} · 不透明度 ${Math.round(brand.watermark.opacity * 100)}% · 缩放 ${Math.round(brand.watermark.scale * 100)}%）`
              : "水印未启用"}
            {brand.outroEnabled ? " · 片尾已启用" : ""}
          </p>
          <div className="ai-video-settings-actions">
            <button type="button" className="is-secondary" onClick={() => onNavigate("/settings")}>
              去设置品牌
            </button>
          </div>
        </section>
        </div>

        <div className="ai-video-export-row">
        <section className="ai-video-form-card" aria-label="质检与门禁">
          <div className="ai-video-form-head">
            <h3>质检与发布门禁</h3>
            <p>质检看成片本身（黑帧/静音/响度/流），门禁汇总脚本、质检与授权。</p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" className="is-secondary" disabled={qcBusy || !selected} onClick={() => void runQualityCheck()}>
              {qcBusy ? "质检中…" : "运行质检"}
            </button>
            <button type="button" disabled={gateBusy || !selected} onClick={() => void runReleaseGate()}>
              {gateBusy ? "检查中…" : "运行发布门禁"}
            </button>
          </div>
          {qc && (
            <div className={`ai-video-verdict is-${qc.status}`}>
              <strong>质检结论：{qc.status === "ok" ? "通过" : qc.status === "warn" ? "有警告" : "未通过"}</strong>
              <ul>
                {qc.checks.map((check) => (
                  <li key={check.id} className={`is-${check.level}`}>
                    {check.message.zh}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {gate && (
            <div className={`ai-video-verdict is-${gate.report.status}`}>
              <strong>{gate.report.verdict.zh}</strong>
              <ul>
                {gate.report.items.map((item) => (
                  <li key={item.id} className={`is-${item.status}`}>
                    {item.message.zh}
                    {item.problems.length > 0 && `：${item.problems.map((problem) => problem.zh).join("；")}`}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="授权清单">
          <div className="ai-video-form-head">
            <h3>授权清单</h3>
            <p>逐镜素材来源与商业风险；清单可下载为 Markdown。</p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" className="is-secondary" disabled={creditsBusy} onClick={() => void loadCredits()}>
              {creditsBusy ? "读取中…" : "查看授权清单"}
            </button>
            <button type="button" className="is-secondary" onClick={() => void downloadCredits()}>
              下载 Markdown
            </button>
          </div>
          {credits && (
            <div className="ai-video-publish-copy">
              <p>
                共 {credits.summary.total} 项 · 需署名 {credits.summary.needsAttribution} · 需复核{" "}
                {credits.summary.needsReview} ·{" "}
                {credits.summary.commercialSafe ? "商业可用" : "存在商业风险项"}
              </p>
              <ul>
                {[...credits.items, ...(credits.bgm ? [credits.bgm] : [])]
                  .filter((item) => item.attributionLine)
                  .map((item) => (
                    <li key={`${item.shotId}-${item.attributionLine}`}>
                      <span>{item.attributionLine}</span>
                      <button type="button" className="is-secondary" onClick={() => void copyText(item.attributionLine ?? "")}>
                        复制
                      </button>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="A/B 变体">
          <div className="ai-video-form-head">
            <h3>A/B 变体</h3>
            <p>同一成片参数下换字幕与 BGM，各生成一版做投放对比。</p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" disabled={abBusy} onClick={() => void runAb()}>
              {abBusy ? "生成变体中…" : "生成两组 A/B 变体"}
            </button>
          </div>
          {abRows.length > 0 && (
            <ul className="ai-video-pending-tasks">
              {abRows.map((row, index) => (
                <li key={index}>
                  <span>{row.label}</span>
                  <span>
                    {row.status === "done" ? "已完成" : row.status === "failed" ? "失败" : "合成中…"}
                    {row.url && (
                      <>
                        {" · "}
                        <a href={aiVideoMediaUrl(row.url) ?? "#"} target="_blank" rel="noreferrer">预览</a>
                      </>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
        </div>

        <section className="ai-video-form-card" aria-label="更多产出">
          <div className="ai-video-form-head">
            <h3>更多产出</h3>
            <p>封面、图文、二维码、片尾卡、联系表、原生感与译制；失败不影响主流程。</p>
          </div>

          <div className="ai-video-export-tools">
          <div className="ai-video-tool">
            <strong>封面图</strong>
            <div className="ai-video-settings-actions">
              <input value={coverTitle} maxLength={40} onChange={(event) => setCoverTitle(event.target.value)} aria-label="封面标题" />
              <button type="button" className="is-secondary" disabled={tools.cover?.busy} onClick={() => void runCover()}>
                {tools.cover?.busy ? "生成中…" : "生成封面"}
              </button>
            </div>
            {tools.cover?.error && <p className="ai-video-field-error">{tools.cover.error}</p>}
            {tools.cover?.images?.[0] && (
              <a href={aiVideoMediaUrl(tools.cover.images[0]) ?? "#"} target="_blank" rel="noreferrer">查看封面图</a>
            )}
          </div>

          <div className="ai-video-tool">
            <strong>图文卡片</strong>
            <button type="button" className="is-secondary" disabled={tools.carousel?.busy} onClick={() => void runCarousel()}>
              {tools.carousel?.busy ? "生成中…" : "生成图文卡片"}
            </button>
            {tools.carousel?.error && <p className="ai-video-field-error">{tools.carousel.error}</p>}
            {!!tools.carousel?.images?.length && (
              <div className="ai-video-settings-actions">
                {tools.carousel.images.map((card, index) => (
                  <a key={card} href={aiVideoMediaUrl(card) ?? "#"} target="_blank" rel="noreferrer">
                    卡片 {index + 1}
                  </a>
                ))}
              </div>
            )}
          </div>

          <div className="ai-video-tool">
            <strong>店铺二维码</strong>
            <button type="button" className="is-secondary" disabled={tools.qr?.busy} onClick={() => void runShopQr()}>
              {tools.qr?.busy ? "生成中…" : "生成二维码"}
            </button>
            {tools.qr?.error && <p className="ai-video-field-error">{tools.qr.error}</p>}
            {tools.qr?.warning && <p className="ai-video-field-error">{tools.qr.warning}</p>}
            {tools.qr?.images?.[0] && (
              <a href={aiVideoMediaUrl(tools.qr.images[0]) ?? "#"} target="_blank" rel="noreferrer">查看二维码</a>
            )}
            {tools.qr?.shopLink && <p className="ai-video-settings-message">店铺链接：{tools.qr.shopLink}</p>}
          </div>

          <div className="ai-video-tool">
            <strong>片尾卡视频</strong>
            <button type="button" className="is-secondary" disabled={tools.endcard?.busy} onClick={() => void runEndCard()}>
              {tools.endcard?.busy ? "生成中…" : "生成片尾卡"}
            </button>
            {tools.endcard?.error && <p className="ai-video-field-error">{tools.endcard.error}</p>}
            {tools.endcard?.warning && <p className="ai-video-field-error">{tools.endcard.warning}</p>}
            {tools.endcard?.video && (
              <a href={aiVideoMediaUrl(tools.endcard.video) ?? "#"} target="_blank" rel="noreferrer">查看片尾卡</a>
            )}
          </div>

          <div className="ai-video-tool">
            <strong>联系表</strong>
            <div className="ai-video-settings-actions">
              <label className="ai-video-check">
                <input type="checkbox" checked={wantProxy} onChange={(event) => setWantProxy(event.target.checked)} />
                带时间码审片代理
              </label>
              <button type="button" className="is-secondary" disabled={tools.sheet?.busy} onClick={() => void runContactSheet()}>
                {tools.sheet?.busy ? "生成中…" : "生成联系表"}
              </button>
            </div>
            {tools.sheet?.error && <p className="ai-video-field-error">{tools.sheet.error}</p>}
            {tools.sheet?.images?.[0] && (
              <a href={aiVideoMediaUrl(tools.sheet.images[0]) ?? "#"} target="_blank" rel="noreferrer">
                查看联系表{tools.sheet.cuts ? `（${tools.sheet.cuts} 个切点）` : ""}
              </a>
            )}
            {tools.sheet?.proxy && (
              <a href={aiVideoMediaUrl(tools.sheet.proxy) ?? "#"} target="_blank" rel="noreferrer">下载审片代理</a>
            )}
          </div>

          <div className="ai-video-tool">
            <strong>原生感处理</strong>
            <div className="ai-video-settings-actions">
              <select value={feelStrength} onChange={(event) => setFeelStrength(event.target.value as typeof feelStrength)}>
                <option value="subtle">轻微</option>
                <option value="medium">中等</option>
                <option value="strong">强</option>
              </select>
              <label className="ai-video-check">
                <input type="checkbox" checked={feelHalation} onChange={(event) => setFeelHalation(event.target.checked)} />
                光晕
              </label>
              <label className="ai-video-check">
                <input type="checkbox" checked={feelPhoneCompress} onChange={(event) => setFeelPhoneCompress(event.target.checked)} />
                手机压缩感
              </label>
              <button type="button" className="is-secondary" disabled={tools.feel?.busy} onClick={() => void runNativeFeel()}>
                {tools.feel?.busy ? "处理中…" : "生成原生感版本"}
              </button>
            </div>
            {tools.feel?.error && <p className="ai-video-field-error">{tools.feel.error}</p>}
            {tools.feel?.video && (
              <a href={aiVideoMediaUrl(tools.feel.video) ?? "#"} target="_blank" rel="noreferrer">查看原生感版本</a>
            )}
          </div>

          <div className="ai-video-tool">
            <strong>译制脚本</strong>
            <div className="ai-video-settings-actions">
              <select value={dubLang} onChange={(event) => setDubLang(event.target.value)} aria-label="目标语言">
                <option value="en">英语</option>
                <option value="ja">日语</option>
                <option value="ko">韩语</option>
                <option value="es">西班牙语</option>
              </select>
              <button type="button" className="is-secondary" disabled={tools.dub?.busy} onClick={() => void runDub()}>
                {tools.dub?.busy ? "译制中…" : "生成译制脚本"}
              </button>
            </div>
            {tools.dub?.error && <p className="ai-video-field-error">{tools.dub.error}</p>}
            {tools.dub?.note && <p className="ai-video-settings-message">{tools.dub.note}</p>}
          </div>
          </div>
        </section>

        <section className="ai-video-form-card" aria-label="发布数据">
          <div className="ai-video-form-head">
            <h3>发布数据回填</h3>
            <p>已记录 {metrics.length} 条；用于后续按风格/钩子复盘。</p>
          </div>
          <div className="ai-video-metrics-grid">
            {([
              ["views", "播放"],
              ["likes", "点赞"],
              ["comments", "评论"],
              ["shares", "转发"],
              ["orders", "订单"],
            ] as const).map(([key, label]) => (
              <label key={key} className="ai-video-field">
                {label}
                <input
                  inputMode="numeric"
                  value={metricDraft[key]}
                  onChange={(event) => setMetricDraft((current) => ({ ...current, [key]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <label className="ai-video-field">
            备注
            <input
              value={metricDraft.note}
              maxLength={120}
              onChange={(event) => setMetricDraft((current) => ({ ...current, note: event.target.value }))}
            />
          </label>
          <div className="ai-video-settings-actions">
            <button type="button" disabled={metricsBusy} onClick={() => void saveMetrics()}>
              {metricsBusy ? "保存中…" : "记录本次发布数据"}
            </button>
          </div>
        </section>
      </div>

      {script && (
        <p className="ai-video-settings-message">
          当前脚本：{script.title || `版本 ${script.version}`} · {script.shots.length} 镜
        </p>
      )}
    </section>
  );
}

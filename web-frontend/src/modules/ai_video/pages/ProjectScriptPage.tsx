import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClipForgeRequestError } from "../api/clipforgeClient";
import { generateCharacterSheet } from "../api/endpoints/characters";
import {
  generateFilm,
  generateStoryboardGrid,
  previewFilm,
} from "../api/endpoints/film";
import { getPipelineRun, startPipelineRun } from "../api/endpoints/pipeline";
import { getProject } from "../api/endpoints/projects";
import { generateScript, generateTopicScript, judgeScript, listScripts, patchScript } from "../api/endpoints/scripts";
import type {
  FilmDryRun,
  FilmResult,
  PipelineRun,
  Project,
  Script,
  ScriptGenerationInput,
  ScriptJudgeResult,
  ShotTextPatch,
} from "../api/types";
import { ProjectHeader } from "../components/ProjectHeader";
import { buildScriptRequest } from "../lib/build-script-request";
import { sanitizeCreationBrief, type CreationBrief } from "../lib/creation-brief";
import {
  SCRIPT_STYLE_OPTIONS,
  TOPIC_NARRATION_STYLE_OPTIONS,
  scriptStyleLabel,
  type VideoModeId,
} from "../lib/creation-brief-defaults";
import { topicNarrationStyleFor } from "../lib/creation-submit";
import { resolveDefaultModelTarget, type GenModelTarget } from "../lib/gen-params";
import { parseStyleRequirement } from "../lib/script-style-requirement";
import { useCharacterStore } from "../state/characterStore";
import { useSettingsStore } from "../state/settingsStore";

const PIPELINE_POLL_INTERVAL_MS = 2500;
const PIPELINE_POLL_BUDGET = 312;

const JUDGE_LABELS: Record<string, string> = {
  pace: "节奏官",
  voice: "口语官",
  idea: "创意官",
  structure: "结构官",
  visual: "画面官",
};

const STAGE_LABELS: Record<string, string> = {
  judge: "台词评审",
  stock_fill: "自动配画面",
  compose: "合成成片",
};

type Notice = { kind: "ok" | "error"; text: string } | null;

type ShotEdit = { voiceover?: string; description?: string; camera?: string };

type ProjectScriptPageProps = {
  projectId: string;
  onNavigate: (path: string) => void;
};

/** 项目脚本页：版本切换、逐镜编辑、判官改写、免费流水线、原生整片两阶段。 */
export function ProjectScriptPage({ projectId, onNavigate }: ProjectScriptPageProps) {
  const { llm, providers, defaultImageModel, defaultVideoModel, customModels, spendCapUsd } =
    useSettingsStore();
  const { characters, updateCharacter } = useCharacterStore();

  const [project, setProject] = useState<Project | null>(null);
  const [scripts, setScripts] = useState<Script[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);

  const [edits, setEdits] = useState<Record<number, ShotEdit>>({});
  const [saving, setSaving] = useState(false);
  const [styleDraft, setStyleDraft] = useState("scenario");
  const [regenerating, setRegenerating] = useState(false);

  const [judge, setJudge] = useState<ScriptJudgeResult | null>(null);
  const [judging, setJudging] = useState(false);

  const [pipeline, setPipeline] = useState<PipelineRun | null>(null);
  const [pipelineBusy, setPipelineBusy] = useState(false);
  const pollRef = useRef(false);

  const [filmBusy, setFilmBusy] = useState<"sheet" | "grid" | "preview" | "submit" | null>(null);
  const [filmPreview, setFilmPreview] = useState<FilmDryRun | null>(null);
  const [filmConfirmed, setFilmConfirmed] = useState(false);
  const [filmResult, setFilmResult] = useState<FilmResult | null>(null);
  const [filmNotice, setFilmNotice] = useState<Notice>(null);

  const selected = useMemo(
    () => scripts.find((script) => script.id === selectedId) ?? scripts.find((script) => script.selected) ?? scripts[0],
    [scripts, selectedId],
  );
  const topicProject = project?.contentType === "topic";
  const styleOptions = topicProject ? TOPIC_NARRATION_STYLE_OPTIONS : SCRIPT_STYLE_OPTIONS;
  const llmReady = Boolean(llm.baseUrl.trim() && llm.apiKey.trim() && llm.model.trim());
  const draftStrategy = !project?.creationBrief || project.creationBrief.outputStrategy === "draft";
  const filmStrategy = project?.creationBrief?.outputStrategy === "native-film";
  const character = project?.characterId
    ? characters.find((item) => item.id === project.characterId)
    : undefined;

  const llmConfig = useCallback(
    () => ({ baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model, visionModel: llm.visionModel }),
    [llm.baseUrl, llm.apiKey, llm.model, llm.visionModel],
  );

  const loadPipeline = useCallback(
    async (poll = true) => {
      try {
        const { run } = await getPipelineRun(projectId);
        setPipeline(run);
        if (poll && run?.status === "running" && !pollRef.current) {
          pollRef.current = true;
          let attempts = 0;
          const tick = async () => {
            if (!pollRef.current) return;
            try {
              const next = await getPipelineRun(projectId);
              setPipeline(next.run);
              if (next.run?.status === "running" && attempts < PIPELINE_POLL_BUDGET) {
                attempts += 1;
                window.setTimeout(tick, PIPELINE_POLL_INTERVAL_MS);
                return;
              }
            } catch {
              // 单次查询失败不终止轮询，由预算兜底
            }
            pollRef.current = false;
          };
          window.setTimeout(tick, PIPELINE_POLL_INTERVAL_MS);
        }
      } catch {
        // 流水线查询失败不影响脚本主流程
      }
    },
    [projectId],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    void Promise.all([getProject(projectId), listScripts(projectId)])
      .then(([nextProject, nextScripts]) => {
        if (cancelled) return;
        setProject(nextProject);
        setScripts(nextScripts);
        setSelectedId(nextScripts.find((script) => script.selected)?.id ?? nextScripts[0]?.id ?? null);
        setStyleDraft(
          nextProject.creationBrief?.styleType ||
            (nextProject.contentType === "topic" ? "knowledge" : "scenario"),
        );
      })
      .catch((cause) => {
        if (!cancelled) setLoadError(cause instanceof Error ? cause.message : "项目加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    void loadPipeline();
    return () => {
      cancelled = true;
      pollRef.current = false;
    };
  }, [loadPipeline, projectId]);

  async function selectVersion(script: Script) {
    if (!selected || script.id === selected.id) return;
    setSelectedId(script.id);
    setScripts((current) => current.map((item) => ({ ...item, selected: item.id === script.id })));
    setEdits({});
    setJudge(null);
    try {
      await patchScript(projectId, { selectedScriptId: script.id });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "切换版本失败" });
    }
  }

  function editShot(shotId: number, field: keyof ShotEdit, value: string) {
    setEdits((current) => ({ ...current, [shotId]: { ...current[shotId], [field]: value } }));
  }

  function shotValue(shot: Script["shots"][number], field: keyof ShotEdit): string {
    const edit = edits[shot.shotId];
    if (edit && edit[field] !== undefined) return edit[field] ?? "";
    return shot[field] ?? "";
  }

  async function saveEdits() {
    if (!selected || Object.keys(edits).length === 0 || saving) return;
    setSaving(true);
    setNotice(null);
    try {
      const shotTexts: ShotTextPatch[] = Object.entries(edits).map(([shotId, patch]) => ({
        shotId: Number(shotId),
        ...patch,
      }));
      const result = await patchScript(projectId, { scriptId: selected.id, shotTexts });
      if (result.script) {
        setScripts((current) => current.map((item) => (item.id === result.script!.id ? result.script! : item)));
      }
      setEdits({});
      setNotice({ kind: "ok", text: "逐镜内容已保存" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  async function regenerate() {
    if (!project || regenerating) return;
    if (!llmReady) {
      setNotice({ kind: "error", text: "请先在设置中填写脚本模型（LLM）的地址、Key 与模型。" });
      return;
    }
    setRegenerating(true);
    setNotice(null);
    try {
      const brief = sanitizeCreationBrief({
        ...(project.creationBrief ?? {}),
        inputMode: project.contentType === "topic" ? "topic" : project.creationBrief?.inputMode ?? "upload",
        styleType: styleDraft,
        styleSource: "explicit",
      });
      if (project.contentType === "topic") {
        await generateTopicScript({
          projectId,
          topic: project.topic ?? project.productName ?? project.name,
          targetDuration: brief.targetDuration,
          narrationStyle: topicNarrationStyleFor(styleDraft),
          llmConfig: llmConfig(),
        });
      } else {
        const body = buildScriptRequest({
          brief,
          projectId,
          productName: project.productName ?? project.name,
          category: project.productCategory ?? "",
          productDescription: project.productDescription ?? "",
          productImages: project.productImages,
          videoMode: (project.videoMode ?? "product_closeup") as VideoModeId,
          llmConfig: llmConfig(),
        });
        await generateScript(body as unknown as ScriptGenerationInput);
      }
      const fresh = await listScripts(projectId);
      setScripts(fresh);
      setSelectedId(fresh.find((script) => script.selected)?.id ?? fresh[0]?.id ?? null);
      setEdits({});
      setJudge(null);
      setNotice({ kind: "ok", text: "已生成新的脚本版本（旧版本仍保留）" });
    } catch (cause) {
      if (cause instanceof ClipForgeRequestError) {
        const requirement = parseStyleRequirement(cause.status, cause.payload);
        if (requirement) {
          setNotice({
            kind: "error",
            text: `需要先显式选择脚本风格：${requirement.candidates.map(scriptStyleLabel).join(" / ")}`,
          });
          return;
        }
      }
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "脚本生成失败" });
    } finally {
      setRegenerating(false);
    }
  }

  async function runJudge() {
    if (!selected || judging) return;
    if (!llmReady) {
      setNotice({ kind: "error", text: "请先在设置中填写脚本模型（LLM）。" });
      return;
    }
    setJudging(true);
    setNotice(null);
    try {
      const result = await judgeScript(projectId, {
        scriptId: selected.id,
        llmConfig: { baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model },
      });
      setJudge(result);
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "判官团评审失败" });
    } finally {
      setJudging(false);
    }
  }

  async function applyJudgeRewrites() {
    if (!selected || !judge || "skipped" in judge) return;
    const shotTexts = new Map<number, ShotTextPatch>();
    for (const rewrite of judge.rewrites) {
      if (rewrite.tier === "taste") continue;
      shotTexts.set(rewrite.shotId, { shotId: rewrite.shotId, voiceover: rewrite.voiceover });
    }
    for (const rewrite of judge.descriptionRewrites) {
      if (rewrite.tier === "taste") continue;
      shotTexts.set(rewrite.shotId, {
        ...(shotTexts.get(rewrite.shotId) ?? { shotId: rewrite.shotId }),
        description: rewrite.description,
      });
    }
    if (shotTexts.size === 0) {
      setNotice({ kind: "ok", text: "本次评审没有可自动应用的改写（口味级建议仅供查看）" });
      return;
    }
    try {
      const result = await patchScript(projectId, { scriptId: selected.id, shotTexts: [...shotTexts.values()] });
      if (result.script) {
        setScripts((current) => current.map((item) => (item.id === result.script!.id ? result.script! : item)));
      }
      setJudge(null);
      setEdits({});
      setNotice({ kind: "ok", text: `已应用 ${shotTexts.size} 条非口味级改写` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "应用改写失败" });
    }
  }

  async function startPipeline(resume: boolean) {
    if (pipelineBusy) return;
    setPipelineBusy(true);
    setNotice(null);
    try {
      await startPipelineRun(projectId, {
        ...(selected ? { scriptId: selected.id } : {}),
        llmConfig: { baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model },
        ...(resume ? { resume: true } : {}),
      });
      await loadPipeline();
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "启动流水线失败" });
    } finally {
      setPipelineBusy(false);
    }
  }

  async function resolveTarget(mediaType: "image" | "video"): Promise<GenModelTarget> {
    const target = await resolveDefaultModelTarget(
      providers,
      mediaType === "image" ? defaultImageModel : defaultVideoModel,
      customModels,
      mediaType,
    );
    if (!target) {
      throw new Error(
        mediaType === "image"
          ? "请先在设置中启用生图平台并选择默认生图模型。"
          : "请先在设置中启用视频平台并选择默认生视频模型。",
      );
    }
    return target;
  }

  async function generateSheet() {
    if (!character) return;
    if (!character.appearance?.trim()) {
      setFilmNotice({ kind: "error", text: "该角色还没有外观描述，请先去主播素材补全。" });
      return;
    }
    setFilmBusy("sheet");
    setFilmNotice(null);
    try {
      const target = await resolveTarget("image");
      const result = await generateCharacterSheet({
        appearance: character.appearance,
        name: character.name,
        provider: target.provider,
        model: target.model,
        apiKey: target.apiKey,
        baseUrl: target.baseUrl,
        options: { aspectRatio: "1:1", count: 1 },
      });
      updateCharacter(character.id, { referenceImages: [result.url, ...character.referenceImages.slice(1)] });
      setFilmNotice({ kind: "ok", text: "角色定妆照已更新" });
    } catch (cause) {
      setFilmNotice({ kind: "error", text: cause instanceof Error ? cause.message : "定妆照生成失败" });
    } finally {
      setFilmBusy(null);
    }
  }

  async function runStoryboardGrid() {
    if (!selected || filmBusy) return;
    setFilmBusy("grid");
    setFilmNotice(null);
    try {
      const target = await resolveTarget("image");
      const result = await generateStoryboardGrid(projectId, {
        scriptId: selected.id,
        provider: target.provider,
        model: target.model,
        apiKey: target.apiKey,
        baseUrl: target.baseUrl,
        options: { aspectRatio: "16:9", count: 1 },
        ...(character?.referenceImages[0] ? { characterSheetUrl: character.referenceImages[0] } : {}),
        ...(project?.productImages[0] ? { productImageUrl: project.productImages[0] } : {}),
      });
      setFilmNotice({
        kind: "ok",
        text: result.reused ? `已复用 ${result.count} 张匹配当前脚本的关键帧` : `已生成 ${result.count} 张关键帧`,
      });
    } catch (cause) {
      setFilmNotice({ kind: "error", text: cause instanceof Error ? cause.message : "九宫格分镜生成失败" });
    } finally {
      setFilmBusy(null);
    }
  }

  async function runFilmPreview() {
    if (!selected || filmBusy) return;
    setFilmBusy("preview");
    setFilmNotice(null);
    setFilmResult(null);
    try {
      const target = await resolveTarget("video");
      const preview = await previewFilm(projectId, {
        scriptId: selected.id,
        provider: target.provider,
        model: target.model,
        baseUrl: target.baseUrl,
        spendCapUsd,
        ...(character?.referenceImages[0] ? { characterSheetUrl: character.referenceImages[0] } : {}),
      });
      setFilmPreview(preview);
      setFilmConfirmed(false);
    } catch (cause) {
      setFilmNotice({ kind: "error", text: cause instanceof Error ? cause.message : "整片预览失败" });
    } finally {
      setFilmBusy(null);
    }
  }

  async function submitFilm() {
    if (!selected || !filmPreview || !filmConfirmed || filmBusy) return;
    setFilmBusy("submit");
    setFilmNotice(null);
    try {
      const target = await resolveTarget("video");
      const result = await generateFilm(projectId, {
        scriptId: selected.id,
        provider: target.provider,
        model: target.model,
        apiKey: target.apiKey,
        baseUrl: target.baseUrl,
        characterSheetUrl: character?.referenceImages[0],
        spendCapUsd,
        // 用户在确认弹层里显式勾选后才走到这里。
        acknowledgeOverCap: true,
      });
      setFilmResult(result);
      setFilmPreview(null);
      setFilmConfirmed(false);
      setFilmNotice({ kind: "ok", text: "整片生成完成，已写入成片列表" });
    } catch (cause) {
      setFilmNotice({ kind: "error", text: cause instanceof Error ? cause.message : "整片生成失败" });
    } finally {
      setFilmBusy(null);
    }
  }

  if (loading) {
    return (
      <section className="ai-video-page" aria-label="项目脚本">
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载项目…</strong>
        </div>
      </section>
    );
  }

  if (loadError || !project) {
    return (
      <section className="ai-video-page" aria-label="项目脚本">
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError || "项目不存在"}</strong>
          <button type="button" onClick={() => onNavigate("/projects")}>返回我的项目</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ai-video-page" aria-label="项目脚本">
      <ProjectHeader project={project} step="script" onNavigate={onNavigate} />

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <div className="ai-video-script-layout">
        <aside className="ai-video-script-versions" aria-label="脚本版本">
          <div className="ai-video-aside-head">
            <h3>版本（{scripts.length}）</h3>
          </div>
          {scripts.length === 0 ? (
            <p className="ai-video-aside-empty">还没有脚本：在右侧选择风格并生成。</p>
          ) : (
            <ul>
              {scripts.map((script, index) => (
                <li key={script.id}>
                  <button
                    type="button"
                    className={selected?.id === script.id ? "is-active" : ""}
                    onClick={() => void selectVersion(script)}
                  >
                    <strong>V{script.version || scripts.length - index}</strong>
                    <span>{script.title || "未命名脚本"}</span>
                    <em>
                      {scriptStyleLabel(script.styleType)} · 约 {script.totalDuration ?? "?"}s
                    </em>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="ai-video-field">
            <label htmlFor="ai-video-script-style">生成风格</label>
            <select
              id="ai-video-script-style"
              value={styleDraft}
              onChange={(event) => setStyleDraft(event.target.value)}
            >
              {styleOptions.map((option) => (
                <option key={option.id} value={option.id}>{option.label}</option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="ai-video-header-action"
            disabled={regenerating || !llmReady}
            onClick={() => void regenerate()}
          >
            {regenerating ? "正在生成…" : "重新生成脚本"}
          </button>
          {!llmReady && <p className="ai-video-field-hint">先在设置中配置脚本模型。</p>}
        </aside>

        <div className="ai-video-script-main">
          {!selected ? (
            <div className="ai-video-empty">
              <span className="ai-video-empty-icon">✎</span>
              <strong>还没有脚本</strong>
              <p>选择风格后点「重新生成脚本」，或回工作台重新创建项目。</p>
            </div>
          ) : (
            <>
              <section className="ai-video-form-card" aria-label="分镜脚本">
                <div className="ai-video-form-head">
                  <h3>{selected.title || "分镜脚本"}</h3>
                  <p>
                    共 {selected.shots.length} 镜 · {scriptStyleLabel(selected.styleType)} · 约{" "}
                    {selected.totalDuration ?? "?"}s。逐镜台词与画面描述可直接改，保存后落库。
                  </p>
                </div>
                <div className="ai-video-script-head-row">
                  <button type="button" disabled={judging || !llmReady} onClick={() => void runJudge()}>
                    {judging ? "判官团评审中…" : "判官团评审"}
                  </button>
                  <button
                    type="button"
                    className="is-secondary"
                    disabled={saving || Object.keys(edits).length === 0}
                    onClick={() => void saveEdits()}
                  >
                    {saving ? "正在保存…" : Object.keys(edits).length ? `保存修改（${Object.keys(edits).length} 镜）` : "保存修改"}
                  </button>
                </div>

                <ol className="ai-video-shots">
                  {selected.shots.map((shot) => (
                    <li key={shot.shotId}>
                      <div className="ai-video-shot-head">
                        <strong>第 {shot.shotId} 镜</strong>
                        <span>{shot.type}</span>
                        <em>{shot.duration}s</em>
                        {edits[shot.shotId] && <i>未保存</i>}
                      </div>
                      <div className="ai-video-field">
                        <label htmlFor={`shot-voice-${shot.shotId}`}>台词</label>
                        <textarea
                          id={`shot-voice-${shot.shotId}`}
                          rows={2}
                          value={shotValue(shot, "voiceover")}
                          onChange={(event) => editShot(shot.shotId, "voiceover", event.target.value)}
                        />
                      </div>
                      <div className="ai-video-settings-grid">
                        <div className="ai-video-field">
                          <label htmlFor={`shot-desc-${shot.shotId}`}>画面描述</label>
                          <textarea
                            id={`shot-desc-${shot.shotId}`}
                            rows={2}
                            value={shotValue(shot, "description")}
                            onChange={(event) => editShot(shot.shotId, "description", event.target.value)}
                          />
                        </div>
                        <div className="ai-video-field">
                          <label htmlFor={`shot-camera-${shot.shotId}`}>运镜</label>
                          <input
                            id={`shot-camera-${shot.shotId}`}
                            value={shotValue(shot, "camera")}
                            onChange={(event) => editShot(shot.shotId, "camera", event.target.value)}
                          />
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              </section>

              {judge && (
                <section className="ai-video-form-card" aria-label="判官团结果">
                  <div className="ai-video-form-head">
                    <h3>判官团结果</h3>
                    <p>只自动应用「铁律 / 默认」级改写；口味级建议仅供查看。</p>
                  </div>
                  {"skipped" in judge ? (
                    <p className="ai-video-field-hint">台词自上次评审后没有变化，已跳过（不重复消耗 LLM）。</p>
                  ) : (
                    <>
                      <div className="ai-video-judge-grid">
                        {judge.verdicts.map((verdict) => (
                          <div key={verdict.judge}>
                            <strong>{JUDGE_LABELS[verdict.judge] ?? verdict.judge}</strong>
                            {verdict.issues.length === 0 ? (
                              <span className="ai-video-settings-message is-ok">无问题</span>
                            ) : (
                              <ul>
                                {verdict.issues.map((issue, index) => (
                                  <li key={`${issue.shotId ?? "all"}-${index}`}>
                                    <em>{issue.tier === "taste" ? "口味" : issue.tier === "default" ? "默认" : "铁律"}</em>
                                    {issue.shotId ? `第 ${issue.shotId} 镜：` : ""}
                                    {issue.issue}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        ))}
                      </div>
                      {judge.summary && <p className="ai-video-field-hint">{judge.summary}</p>}
                      <div className="ai-video-settings-actions">
                        <button type="button" onClick={() => void applyJudgeRewrites()}>应用非口味级改写</button>
                        <span className="ai-video-settings-message">
                          可应用 {[...judge.rewrites, ...judge.descriptionRewrites].filter((item) => item.tier !== "taste").length} 条
                        </span>
                      </div>
                    </>
                  )}
                </section>
              )}

              {draftStrategy && (
                <section className="ai-video-form-card" aria-label="免费流水线">
                  <div className="ai-video-form-head">
                    <h3>免费出片流水线</h3>
                    <p>判官 → 自动配画面 → 合成，服务端执行；关掉页面也会继续，回来可续跑。</p>
                  </div>
                  {pipeline ? (
                    <div className="ai-video-pipeline">
                      <div className="ai-video-pipeline-stages">
                        {(["judge", "stock_fill", "compose"] as const).map((stage, index) => {
                          const order = ["judge", "stock_fill", "compose"];
                          const current = order.indexOf(pipeline.stage);
                          const state =
                            pipeline.status === "done"
                              ? "done"
                              : pipeline.status === "failed"
                                ? index <= current
                                  ? index === current
                                    ? "failed"
                                    : "done"
                                  : "pending"
                                : index < current
                                  ? "done"
                                  : index === current
                                    ? "active"
                                    : "pending";
                          return (
                            <span key={stage} className={`is-${state}`}>
                              {index + 1}. {STAGE_LABELS[stage]}
                            </span>
                          );
                        })}
                      </div>
                      <p>
                        状态：
                        {pipeline.status === "running"
                          ? "运行中"
                          : pipeline.status === "done"
                            ? "已完成"
                            : pipeline.interrupted
                              ? "已中断（可续跑）"
                              : "失败"}
                        {pipeline.error ? ` · ${pipeline.error}` : ""}
                      </p>
                      <div className="ai-video-settings-actions">
                        {pipeline.status === "running" && <span className="ai-video-settings-message">每 2.5 秒刷新进度…</span>}
                        {pipeline.status === "failed" && (
                          <button type="button" disabled={pipelineBusy} onClick={() => void startPipeline(true)}>
                            从断点续跑
                          </button>
                        )}
                        {pipeline.status === "done" && pipeline.compositionId && (
                          <button type="button" onClick={() => onNavigate(`/project/${projectId}/video`)}>查看成片</button>
                        )}
                      </div>
                    </div>
                  ) : (
                    <div className="ai-video-settings-actions">
                      <button type="button" disabled={pipelineBusy} onClick={() => void startPipeline(false)}>
                        {pipelineBusy ? "正在启动…" : "启动免费出片流水线"}
                      </button>
                    </div>
                  )}
                </section>
              )}

              {filmStrategy && (
                <section className="ai-video-form-card" aria-label="原生整片">
                  <div className="ai-video-form-head">
                    <h3>原生整片（两阶段）</h3>
                    <p>先预览镜头数、参考图配额与花费估算；确认后才提交付费生成。</p>
                  </div>
                  {filmNotice && (
                    <div className={`ai-video-notice${filmNotice.kind === "error" ? " is-error" : ""}`} role="status">
                      <span>{filmNotice.text}</span>
                      <button type="button" aria-label="关闭提示" onClick={() => setFilmNotice(null)}>×</button>
                    </div>
                  )}
                  {character && (
                    <div className="ai-video-film-character">
                      <span>
                        角色：{character.name}
                        {character.referenceImages[0] ? "（已有定妆照）" : "（还没有定妆照）"}
                      </span>
                      <button type="button" className="is-secondary" disabled={filmBusy !== null} onClick={() => void generateSheet()}>
                        {filmBusy === "sheet" ? "处理中…" : character.referenceImages[0] ? "重做定妆照" : "生成定妆照"}
                      </button>
                    </div>
                  )}
                  <div className="ai-video-settings-actions">
                    <button type="button" disabled={filmBusy !== null} onClick={() => void runStoryboardGrid()}>
                      {filmBusy === "grid" ? "正在生成…" : "生成九宫格分镜（付费生图）"}
                    </button>
                    <button type="button" className="is-secondary" disabled={filmBusy !== null} onClick={() => void runFilmPreview()}>
                      {filmBusy === "preview" ? "正在估算…" : "预览整片花费（不提交）"}
                    </button>
                  </div>
                  {filmResult && (
                    <div className="ai-video-notice" role="status">
                      <span>整片已生成：{filmResult.fileName}</span>
                      <button type="button" onClick={() => onNavigate(`/project/${projectId}/export`)}>查看成片</button>
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      </div>

      {filmPreview && (
        <div className="ai-video-modal-backdrop" role="presentation" onClick={() => setFilmPreview(null)}>
          <div
            className="ai-video-modal"
            role="dialog"
            aria-modal="true"
            aria-label="整片付费确认"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="ai-video-modal-head">
              <h3>确认生成原生整片</h3>
              <button type="button" aria-label="关闭" onClick={() => setFilmPreview(null)}>×</button>
            </div>
            <ul className="ai-video-film-summary">
              <li>镜头数：{filmPreview.shotCount}</li>
              <li>预计时长：{filmPreview.seconds}s（脚本 {filmPreview.scriptSeconds}s）</li>
              <li>模型：{filmPreview.model}</li>
              <li>参考图：{filmPreview.referenceImages} 张{filmPreview.referenceQuota.limit ? `（上限 ${filmPreview.referenceQuota.limit}）` : ""}</li>
              <li>
                预估花费：
                {filmPreview.estimate
                  ? `$${filmPreview.estimate.minUsd.toFixed(2)} – $${filmPreview.estimate.maxUsd.toFixed(2)}`
                  : "平台未公布单价，费用未知"}
              </li>
            </ul>
            {!filmPreview.referenceQuota.ok && (
              <p className="ai-video-field-error">参考图超过模型上限，请减少分镜或去掉定妆照。</p>
            )}
            {filmPreview.durationOverflow && (
              <p className="ai-video-field-error">
                脚本 {filmPreview.scriptSeconds}s 超过模型单次上限 {filmPreview.modelMaxSeconds}s，请缩短脚本或换模型。
              </p>
            )}
            {filmPreview.dialogueWarnings.length > 0 && (
              <p className="ai-video-field-hint">
                有 {filmPreview.dialogueWarnings.length} 个镜头台词偏密，可能出现口型不同步。
              </p>
            )}
            <label className="ai-video-switch">
              <input
                type="checkbox"
                checked={filmConfirmed}
                disabled={!filmPreview.referenceQuota.ok || filmPreview.durationOverflow}
                onChange={(event) => setFilmConfirmed(event.target.checked)}
              />
              <span>我已确认将提交付费生成，并承担上述预估费用</span>
            </label>
            <div className="ai-video-settings-actions">
              <button
                type="button"
                disabled={!filmConfirmed || filmBusy !== null || !filmPreview.referenceQuota.ok || filmPreview.durationOverflow}
                onClick={() => void submitFilm()}
              >
                {filmBusy === "submit" ? "正在生成整片（可能几分钟）…" : "确认并生成整片"}
              </button>
              <button type="button" className="is-secondary" disabled={filmBusy !== null} onClick={() => setFilmPreview(null)}>
                取消
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

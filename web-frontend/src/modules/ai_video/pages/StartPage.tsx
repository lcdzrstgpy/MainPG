import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ClipForgeRequestError } from "../api/clipforgeClient";
import { ingestProduct } from "../api/endpoints/products";
import { createProject, listProjects, patchProject } from "../api/endpoints/projects";
import { generateScript, generateTopicScript } from "../api/endpoints/scripts";
import { getTrends } from "../api/endpoints/trends";
import { uploadImages } from "../api/endpoints/upload";
import type { Project, ScriptGenerationInput, TrendTopic } from "../api/types";
import {
  CreationBriefForm,
  type CreationFormValues,
  type CreationProductOption,
} from "../components/CreationBriefForm";
import { StyleChoicePrompt } from "../components/StyleChoicePrompt";
import { buildScriptRequest } from "../lib/build-script-request";
import { DEFAULT_CREATION_BRIEF, sanitizeCreationBrief, type CreationBrief } from "../lib/creation-brief";
import {
  DEFAULT_VIDEO_MODE,
  MAX_SOURCE_IMAGES,
  coerceStyleTypeForInputMode,
  type VideoModeId,
} from "../lib/creation-brief-defaults";
import {
  clearCloneHandoff as clearCloneHandoffStorage,
  consumeCloneHandoff,
} from "../lib/clone-handoff";
import { topicNarrationStyleFor } from "../lib/creation-submit";
import { formatRelativeTime } from "../lib/relative-time";
import {
  missingStyleRequirement,
  parseStyleRequirement,
  type ScriptStyleRequirement,
} from "../lib/script-style-requirement";
import { consumeStartPrefill } from "../lib/start-prefill";
import { useCharacterStore } from "../state/characterStore";
import { readProductsFrom } from "../state/productLibraryData";
import { productLibraryActions } from "../state/productLibraryStore";
import { useSettingsStore } from "../state/settingsStore";

type PendingCreation =
  | { kind: "form"; values: CreationFormValues }
  | {
      kind: "script";
      projectId: string;
      brief: CreationBrief;
      productName: string;
      category: string;
      description: string;
      topic: string;
      productImages: string[];
      videoMode: VideoModeId;
      referenceStructure?: string;
    };

type StartPageProps = {
  /** 原生页面内的跳转（项目详情、设置、商品素材等仍由外层路由分发）。 */
  onNavigate: (path: string) => void;
};

function initialFormValues(): CreationFormValues {
  return {
    brief: sanitizeCreationBrief(DEFAULT_CREATION_BRIEF),
    productName: "",
    category: "",
    sellingPoints: "",
    images: [],
    linkUrl: "",
    topic: "",
    videoMode: DEFAULT_VIDEO_MODE,
    serverImages: [],
    // 默认「原生整片」就是主路线，与上游一致：无需额外点一次策略卡。
    strategyChosen: true,
  };
}

/** 远端商品图抓成本地 File，走正常上传链路；跨域抓不到时返回空数组。 */
async function fetchImagesAsFiles(urls: string[]): Promise<CreationFormValues["images"]> {
  const files: CreationFormValues["images"] = [];
  for (const [index, src] of urls.slice(0, MAX_SOURCE_IMAGES).entries()) {
    try {
      const response = await fetch(src);
      if (!response.ok) continue;
      const blob = await response.blob();
      files.push({
        id: crypto.randomUUID(),
        url: URL.createObjectURL(blob),
        file: new File([blob], `product-${index}.png`, { type: blob.type || "image/png" }),
      });
    } catch {
      // 跨域或被拦截：文本字段已填好，图片交给用户在表单里手工上传。
    }
  }
  return files;
}

function stepFor(status: Project["status"]): string {
  if (status === "done" || status === "composing" || status === "video") return "video";
  if (status === "assets") return "assets";
  return "script";
}

function statusLabel(status: Project["status"]): string {
  switch (status) {
    case "done":
      return "已出片";
    case "composing":
    case "video":
      return "合成中";
    case "assets":
      return "素材中";
    case "scripting":
      return "脚本中";
    default:
      return "草稿";
  }
}

/** 原生视频工作台：五种来源共用一份创作简报，提交后建项目 → 传图 → 生成脚本 → 跳脚本页。 */
export function StartPage({ onNavigate }: StartPageProps) {
  const { llm } = useSettingsStore();
  const { characters } = useCharacterStore();
  const llmReady = Boolean(llm.baseUrl.trim() && llm.apiKey.trim() && llm.model.trim());

  const [values, setValues] = useState<CreationFormValues>(initialFormValues);
  const [recent, setRecent] = useState<Project[]>([]);
  const [trends, setTrends] = useState<TrendTopic[]>([]);
  const [products, setProducts] = useState<CreationProductOption[]>([]);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [linkImported, setLinkImported] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(0);
  const [stylePrompt, setStylePrompt] = useState<ScriptStyleRequirement | null>(null);
  const pendingRef = useRef<PendingCreation | null>(null);
  const cloneRef = useRef<{ referenceStructure?: string; referenceVideoUrl?: string }>({});
  // 商品素材「做视频」交接过来的商品：创建成功后给它的 videoCount +1。
  const preselectedProductIdRef = useRef<string | null>(null);

  const busySteps = useMemo(
    () => (values.brief.inputMode === "topic" ? ["创建项目", "生成脚本"] : ["创建项目", "上传商品图", "生成脚本"]),
    [values.brief.inputMode],
  );

  // 最近项目 / 热点 / 商品库：进入页面各拉一次，失败静默（不影响主流程）。
  useEffect(() => {
    let cancelled = false;
    void listProjects()
      .then((projects) => {
        if (!cancelled) setRecent(projects.slice(0, 6));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void getTrends({ source: "cn", limit: 12 })
      .then((result) => {
        if (!cancelled) setTrends(result.topics.slice(0, 8));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setProducts(
      readProductsFrom(window.localStorage).map((product) => ({
        id: product.id,
        name: product.name,
        description: product.description ?? "",
        images: product.images,
      })),
    );
  }, []);

  // 商品素材「做视频」：读取一次性交接，把商品预填进商品库来源。
  useEffect(() => {
    const prefill = consumeStartPrefill(window.localStorage);
    if (!prefill?.productId) return;
    const product = readProductsFrom(window.localStorage).find((item) => item.id === prefill.productId);
    if (!product) return;
    preselectedProductIdRef.current = product.id;
    setValues((current) => ({
      ...current,
      productName: product.name,
      sellingPoints: product.description ?? "",
      category: product.category === "tech" ? "digital" : product.category === "other" ? "" : product.category,
      serverImages: product.images.slice(0, MAX_SOURCE_IMAGES),
      brief: sanitizeCreationBrief({
        ...current.brief,
        inputMode: "product-library",
        styleType: coerceStyleTypeForInputMode(current.brief.styleType, "product-library"),
      }),
    }));
  }, []);

  // 爆款复刻交接：只消费一次；创建成功后再清掉暂存，避免刷新即丢。
  useEffect(() => {
    const handoff = consumeCloneHandoff(window.localStorage);
    if (!handoff) return;
    cloneRef.current = {
      referenceStructure: handoff.referenceStructure,
      referenceVideoUrl: handoff.referenceVideoUrl,
    };
    setValues((current) => ({
      ...current,
      productName: handoff.productName || current.productName,
      sellingPoints: handoff.sellingPoints || current.sellingPoints,
      serverImages: handoff.productImages?.slice(0, MAX_SOURCE_IMAGES) ?? [],
      brief: sanitizeCreationBrief({ ...current.brief, ...handoff.brief, inputMode: "clone" }),
    }));
  }, []);

  const clearCloneHandoff = useCallback(() => {
    clearCloneHandoffStorage(window.localStorage);
  }, []);

  async function handleImportLink(url: string) {
    setImportError("");
    setImporting(true);
    try {
      const result = await ingestProduct({ url, createProject: false });
      const product = result.product;
      const remoteImages = (product.images ?? []).slice(0, MAX_SOURCE_IMAGES);
      const files = await fetchImagesAsFiles(remoteImages);
      setValues((current) => ({
        ...current,
        linkUrl: url,
        productName: product.title || current.productName,
        sellingPoints: product.description || current.sellingPoints,
        images: files.length ? files : current.images,
        serverImages: files.length ? current.serverImages : remoteImages,
        brief: sanitizeCreationBrief({ ...current.brief, inputMode: "link" }),
      }));
      setLinkImported(files.length > 0 || remoteImages.length > 0);
    } catch (cause) {
      setImportError(cause instanceof Error ? cause.message : "商品链接解析失败");
    } finally {
      setImporting(false);
    }
  }

  const llmConfig = useCallback(
    () => ({ baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model, visionModel: llm.visionModel }),
    [llm.baseUrl, llm.apiKey, llm.model, llm.visionModel],
  );

  /** 生成脚本；409 needs_explicit_style 不是失败，而是「让用户选一个风格再重试」。 */
  const requestScript = useCallback(
    async (pending: Extract<PendingCreation, { kind: "script" }>): Promise<"ok" | "needs-style"> => {
      try {
        if (pending.brief.inputMode === "topic") {
          await generateTopicScript({
            projectId: pending.projectId,
            topic: pending.topic,
            targetDuration: pending.brief.targetDuration,
            narrationStyle: topicNarrationStyleFor(pending.brief.styleType),
            llmConfig: llmConfig(),
          });
        } else {
          // buildScriptRequest 是唯一请求构造器，键集由它保证；接口类型只做静态约束。
          const character = pending.brief.characterId
            ? characters.find((item) => item.id === pending.brief.characterId)
            : undefined;
          const body = buildScriptRequest({
            brief: pending.brief,
            projectId: pending.projectId,
            productName: pending.productName,
            category: pending.category,
            productDescription: pending.description,
            productImages: pending.productImages,
            videoMode: pending.videoMode,
            llmConfig: llmConfig(),
            ...(character
              ? {
                  character: {
                    id: character.id,
                    name: character.name,
                    appearance: character.appearance ?? "",
                    ...(character.voiceProfile?.style
                      ? { voiceStyle: character.voiceProfile.style }
                      : {}),
                  },
                }
              : {}),
            ...(pending.referenceStructure ? { referenceStructure: pending.referenceStructure } : {}),
          });
          await generateScript(body as unknown as ScriptGenerationInput);
        }
        return "ok";
      } catch (cause) {
        if (cause instanceof ClipForgeRequestError) {
          const requirement = parseStyleRequirement(cause.status, cause.payload);
          if (requirement) {
            pendingRef.current = pending;
            setStylePrompt(requirement);
            return "needs-style";
          }
        }
        throw cause;
      }
    },
    [characters, llmConfig],
  );

  const runCreation = useCallback(
    async (form: CreationFormValues) => {
      if (busy) return;
      if (!llmReady) {
        setError("请先在「设置」里填写脚本模型（LLM）的接口地址、API Key 与模型。");
        return;
      }

      const brief = sanitizeCreationBrief(form.brief);
      // 没有显式风格就先让用户选：避免接口把空风格当作推荐失败，也避免白建一个项目。
      // 主题模式走旁白词表（空值由 topicNarrationStyleFor 落到 knowledge），不需要带货风格弹层。
      if (!brief.styleType && brief.inputMode !== "topic") {
        pendingRef.current = { kind: "form", values: form };
        setStylePrompt(missingStyleRequirement());
        return;
      }

      const topicMode = brief.inputMode === "topic";
      const topicText = form.topic.trim();
      const productName = topicMode ? topicText : form.productName.trim();
      const description = topicMode ? topicText : form.sellingPoints.trim();
      const initialImages = form.serverImages.slice(0, MAX_SOURCE_IMAGES);

      setBusy(true);
      setError(null);
      setStylePrompt(null);
      setStage(0);
      try {
        const project = await createProject({
          name: topicMode ? topicText : `AI 视频 · ${productName}`,
          productName,
          productCategory: form.category || undefined,
          productDescription: description,
          // 本地文件先留空，创建后再上传；服务端已有路径（商品库/链接/复刻）直接带上。
          productImages: form.images.length ? [] : initialImages,
          videoMode: topicMode ? undefined : form.videoMode,
          creationBrief: brief,
          ...(brief.characterId ? { characterId: brief.characterId } : {}),
          ...(topicMode ? { contentType: "topic" as const, topic: topicText } : {}),
          ...(cloneRef.current.referenceVideoUrl ? { sourceVideoUrl: cloneRef.current.referenceVideoUrl } : {}),
        });

        let productImages = initialImages;
        if (!topicMode && form.images.length) {
          setStage(1);
          const uploaded = await uploadImages(project.id, form.images.map((image) => image.file));
          // 本地新图 + 来源已有图（商品库/链接/复刻）都保留，最多 5 张。
          productImages = [...(uploaded.paths ?? []), ...initialImages].slice(0, MAX_SOURCE_IMAGES);
          if (productImages.length) await patchProject(project.id, { productImages });
        }

        setStage(busySteps.length - 1);
        const outcome = await requestScript({
          kind: "script",
          projectId: project.id,
          brief,
          productName,
          category: form.category,
          description,
          topic: topicText,
          productImages,
          videoMode: form.videoMode,
          ...(cloneRef.current.referenceStructure
            ? { referenceStructure: cloneRef.current.referenceStructure }
            : {}),
        });
        setBusy(false);
        if (outcome === "needs-style") return;
        clearCloneHandoff();
        if (preselectedProductIdRef.current) {
          productLibraryActions.incrementVideoCount(preselectedProductIdRef.current);
          preselectedProductIdRef.current = null;
        }
        onNavigate(`/project/${project.id}/script`);
      } catch (cause) {
        setBusy(false);
        setStage(0);
        setError(cause instanceof Error ? cause.message : "创建失败，请稍后重试");
      }
    },
    [busy, busySteps, clearCloneHandoff, llmReady, onNavigate, requestScript],
  );

  async function pickStyle(styleType: string) {
    const pending = pendingRef.current;
    if (!pending || busy) return;
    if (pending.kind === "form") {
      const next = {
        ...pending.values,
        brief: sanitizeCreationBrief({
          ...pending.values.brief,
          styleType,
          styleSource: "explicit" as const,
        }),
      };
      setValues(next);
      setStylePrompt(null);
      await runCreation(next);
      return;
    }

    const brief = sanitizeCreationBrief({ ...pending.brief, styleType, styleSource: "explicit" });
    setStylePrompt(null);
    setBusy(true);
    setStage(busySteps.length - 1);
    setError(null);
    try {
      await patchProject(pending.projectId, { creationBrief: brief });
      const outcome = await requestScript({ ...pending, brief });
      setBusy(false);
      if (outcome === "ok") {
        clearCloneHandoff();
        if (preselectedProductIdRef.current) {
          productLibraryActions.incrementVideoCount(preselectedProductIdRef.current);
          preselectedProductIdRef.current = null;
        }
        onNavigate(`/project/${pending.projectId}/script`);
      }
    } catch (cause) {
      setBusy(false);
      setError(cause instanceof Error ? cause.message : "脚本生成失败，请稍后重试");
    }
  }

  function pickTrend(topic: TrendTopic) {
    setValues((current) => ({
      ...current,
      topic: topic.title,
      brief: sanitizeCreationBrief({
        ...current.brief,
        inputMode: "topic",
        styleType: "knowledge",
        styleSource: "explicit",
      }),
    }));
  }

  return (
    <section className="ai-video-page" aria-label="AI 视频工作台">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>视频工作台</h2>
          <p>从商品素材到分镜、生成与导出，全部在界野工作台内完成。</p>
        </div>
        <span className={`ai-video-service-badge${llmReady ? " is-ready" : " is-warning"}`}>
          {llmReady ? "● 脚本模型已配置" : "● 待填写脚本模型 Key"}
        </span>
      </header>

      {!llmReady && (
        <div className="ai-video-settings-warning" role="status">
          <strong>还差一步：配置脚本模型</strong>
          <p>脚本生成需要一个 OpenAI 兼容的 LLM。填写后刷新即可在这里创建项目。</p>
          <div className="ai-video-settings-actions">
            <button type="button" onClick={() => onNavigate("/settings")}>去设置</button>
          </div>
        </div>
      )}

      <div className="ai-video-start-layout">
        <div className="ai-video-start-main">
          {busy && (
            <div className="ai-video-progress" role="status">
              <strong>正在创建「{values.brief.inputMode === "topic" ? values.topic || "主题视频" : values.productName || "新项目"}」</strong>
              <ol>
                {busySteps.map((label, index) => (
                  <li
                    key={label}
                    className={index < stage ? "is-done" : index === stage ? "is-active" : ""}
                  >
                    <span>{index < stage ? "✓" : index + 1}</span>
                    {label}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {error && (
            <div className="ai-video-settings-warning is-error" role="alert">
              <strong>提交失败</strong>
              <p>{error}</p>
            </div>
          )}

          <CreationBriefForm
            values={values}
            onChange={setValues}
            products={products}
            characters={characters.map((character) => ({ id: character.id, name: character.name }))}
            busy={busy}
            onImportLink={handleImportLink}
            importing={importing}
            importError={importError}
            linkImported={linkImported}
            onSubmit={() => void runCreation(values)}
            onGoProducts={() => onNavigate("/products")}
            onGoClone={() => onNavigate("/project/clone")}
          />
        </div>

        <aside className="ai-video-start-aside">
          <section className="ai-video-aside-card" aria-label="最近项目">
            <div className="ai-video-aside-head">
              <h3>最近项目</h3>
              <button type="button" onClick={() => onNavigate("/projects")}>全部</button>
            </div>
            {recent.length === 0 ? (
              <p className="ai-video-aside-empty">还没有项目，左侧创建一个。</p>
            ) : (
              <ul className="ai-video-recent-list">
                {recent.map((project) => (
                  <li key={project.id}>
                    <button type="button" onClick={() => onNavigate(`/project/${project.id}/${stepFor(project.status)}`)}>
                      <span className="ai-video-recent-name">{project.name}</span>
                      <span className="ai-video-recent-meta">
                        {statusLabel(project.status)}
                        <i>{formatRelativeTime(project.updatedAt, "zh")}</i>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="ai-video-aside-card" aria-label="今日热点">
            <div className="ai-video-aside-head">
              <h3>今日热点</h3>
              <span>点一下填入主题</span>
            </div>
            {trends.length === 0 ? (
              <p className="ai-video-aside-empty">暂时没有取到热点，直接手写主题也可以。</p>
            ) : (
              <div className="ai-video-trend-list">
                {trends.map((topic, index) => (
                  <button key={`${topic.title}-${index}`} type="button" onClick={() => pickTrend(topic)}>
                    <span>{index + 1}</span>
                    <strong>{topic.title}</strong>
                    {topic.traffic && <i>{topic.traffic}</i>}
                  </button>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>

      {stylePrompt && (
        <StyleChoicePrompt
          requirement={stylePrompt}
          busy={busy}
          onPick={(styleType) => void pickStyle(styleType)}
          onClose={() => setStylePrompt(null)}
        />
      )}
    </section>
  );
}

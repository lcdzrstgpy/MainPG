import { useState } from "react";

import { getSidecarHealth, listAiModels, testAiProvider } from "../api/endpoints/ai";
import { listLlmModels, testLlm } from "../api/endpoints/settings";
import { synthesizeFreeTts, synthesizePaidTts } from "../api/endpoints/tts";
import type { AiModel, AiProviderCatalogStatus } from "../api/types";
import { PresenterManager } from "../components/PresenterManager";
import { TTS_PROVIDERS, getTTSProviderMeta, isPaidTTSReady, resolveTTSConfig, type TTSProvider } from "../lib/tts-presets";
import { useBrandStore } from "../state/brandStore";
import { useSettingsStore } from "../state/settingsStore";

/**
 * 原生设置页（T0.4 起步，T1.7 补全）：
 * 平台 / 脚本模型 / 模型目录 / 生成参数 / 配音 / 角色 / 品牌 / 诊断。
 */

const MEDIA_PROVIDERS = [
  { key: "volcengine", name: "火山引擎", desc: "豆包图像 / 视频模型，脚本之外的主要生成能力。" },
  { key: "suchuang", name: "速创", desc: "图像 / 视频生成平台，按次计费。" },
] as const;

const MEDIA_PROVIDER_KEYS = ["volcengine", "suchuang"] as const;

const TABS = [
  { id: "providers", label: "AI 平台" },
  { id: "llm", label: "脚本模型" },
  { id: "models", label: "模型目录" },
  { id: "generation", label: "生成参数" },
  { id: "tts", label: "配音" },
  { id: "characters", label: "角色" },
  { id: "brand", label: "品牌" },
  { id: "diagnostics", label: "诊断" },
] as const;

type SettingsTab = (typeof TABS)[number]["id"];

type TestState = { status: "idle" | "testing" | "ok" | "invalid" | "unknown"; message: string };

const IDLE_TEST: TestState = { status: "idle", message: "" };

function parseOptionalNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function SettingsPage() {
  const {
    providers,
    llm,
    tts,
    defaultImageModel,
    defaultVideoModel,
    defaultResolution,
    defaultAspectRatio,
    imageParams,
    videoParams,
    setProvider,
    setLLM,
    setTTS,
    setDefaultImageModel,
    setDefaultVideoModel,
    setDefaultResolution,
    setDefaultAspectRatio,
    setImageParams,
    setVideoParams,
  } = useSettingsStore();
  const { brand, updateBrand, updateWatermark } = useBrandStore();

  const [tab, setTab] = useState<SettingsTab>("providers");
  const [llmTest, setLlmTest] = useState<TestState>(IDLE_TEST);
  const [llmModels, setLlmModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState("");
  const [loadingModels, setLoadingModels] = useState(false);

  const [providerTests, setProviderTests] = useState<Record<string, TestState>>({});
  const [catalog, setCatalog] = useState<{ models: AiModel[]; providers: AiProviderCatalogStatus[] } | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");

  const [ttsPreview, setTtsPreview] = useState<TestState>(IDLE_TEST);
  const [ttsUsingFree, setTtsUsingFree] = useState(false);

  const [diagnostics, setDiagnostics] = useState<{ status: number; text: string } | null>(null);
  const [diagnosticsMessage, setDiagnosticsMessage] = useState("");

  const hasMediaProvider = MEDIA_PROVIDERS.some(({ key }) => {
    const provider = providers[key];
    return Boolean(provider?.enabled && provider.apiKey.trim());
  });
  const fullyConfigured = hasMediaProvider && Boolean(llm.apiKey.trim());
  const enabledProviders = MEDIA_PROVIDER_KEYS.flatMap((key) => {
    const setting = providers[key];
    return setting?.enabled && setting.apiKey.trim()
      ? [{ name: key, apiKey: setting.apiKey, ...(setting.baseUrl ? { baseUrl: setting.baseUrl } : {}) }]
      : [];
  });

  async function handleTestLlm() {
    setLlmTest({ status: "testing", message: "正在测试连接…" });
    try {
      const result = await testLlm({ baseUrl: llm.baseUrl, apiKey: llm.apiKey, model: llm.model });
      setLlmTest(
        result.ok
          ? { status: "ok", message: result.warning ?? "连接正常，可以生成脚本。" }
          : { status: "invalid", message: result.error ?? "连接失败，请检查地址与 Key。" },
      );
    } catch (error) {
      setLlmTest({ status: "invalid", message: error instanceof Error ? error.message : "连接失败" });
    }
  }

  async function handleLoadLlmModels() {
    setLoadingModels(true);
    setModelsError("");
    try {
      const result = await listLlmModels({ baseUrl: llm.baseUrl, apiKey: llm.apiKey });
      setLlmModels(result.models ?? []);
      if (!result.ok || !result.models?.length) {
        setModelsError(result.error ?? "没有读到模型列表，请检查地址与 Key。");
      }
    } catch (error) {
      setLlmModels([]);
      setModelsError(error instanceof Error ? error.message : "读取模型列表失败");
    } finally {
      setLoadingModels(false);
    }
  }

  async function handleTestProvider(key: string) {
    const setting = providers[key];
    if (!setting?.apiKey.trim()) return;
    setProviderTests((current) => ({ ...current, [key]: { status: "testing", message: "正在测试…" } }));
    try {
      const result = await testAiProvider({
        name: key,
        apiKey: setting.apiKey,
        ...(setting.baseUrl ? { baseUrl: setting.baseUrl } : {}),
      });
      setProviderTests((current) => ({
        ...current,
        [key]: {
          status: result.status,
          message: result.message,
        },
      }));
    } catch (error) {
      setProviderTests((current) => ({
        ...current,
        [key]: { status: "unknown", message: error instanceof Error ? error.message : "测试失败" },
      }));
    }
  }

  async function handleRefreshCatalog(forceRefresh: boolean) {
    if (enabledProviders.length === 0) {
      setCatalogError("请先在「AI 平台」启用至少一个平台并填写 API Key。");
      return;
    }
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const result = await listAiModels({ providers: enabledProviders, refresh: forceRefresh });
      setCatalog(result);
      if (result.models.length === 0) setCatalogError("没有读到可用模型，请检查平台 Key 或稍后重试。");
    } catch (error) {
      setCatalogError(error instanceof Error ? error.message : "模型目录读取失败");
    } finally {
      setCatalogLoading(false);
    }
  }

  function changeTtsProvider(provider: TTSProvider) {
    const meta = getTTSProviderMeta(provider);
    setTTS({
      ...tts,
      provider,
      baseUrl: meta.baseUrl,
      model: meta.defaultModel,
      voice: meta.defaultVoice,
      ...(provider === "volcengine-speech" ? { resourceId: meta.defaultResourceId, speechRate: 10 } : {}),
    });
  }

  async function handleTtsPreview() {
    setTtsPreview({ status: "testing", message: "正在生成试听…" });
    try {
      const paidReady = isPaidTTSReady(tts, providers);
      setTtsUsingFree(!paidReady);
      const text = "你好，这是一段配音试听。";
      const blob = paidReady
        ? await synthesizePaidTts(text, resolveTTSConfig(tts, providers) as unknown as Record<string, unknown>)
        : await synthesizeFreeTts(text);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
      setTtsPreview({ status: "ok", message: paidReady ? "正在播放付费音色试听" : "正在播放免费音色试听" });
    } catch (error) {
      setTtsPreview({ status: "invalid", message: error instanceof Error ? error.message : "试听失败" });
    }
  }

  async function handleLoadDiagnostics() {
    setDiagnosticsMessage("正在读取…");
    try {
      const snapshot = await getSidecarHealth();
      setDiagnostics({
        status: snapshot.httpStatus,
        text: JSON.stringify(snapshot.payload, null, 2),
      });
      setDiagnosticsMessage(snapshot.httpStatus === 200 ? "服务健康" : `服务异常（HTTP ${snapshot.httpStatus}）`);
    } catch (error) {
      setDiagnostics(null);
      setDiagnosticsMessage(error instanceof Error ? error.message : "诊断读取失败");
    }
  }

  async function handleCopyDiagnostics() {
    if (!diagnostics) return;
    try {
      await navigator.clipboard.writeText(diagnostics.text);
      setDiagnosticsMessage("已复制到剪贴板");
    } catch {
      setDiagnosticsMessage("复制失败，请手动选择下方文本");
    }
  }

  const ttsMeta = getTTSProviderMeta(tts.provider);
  const imageModels = catalog?.models.filter((model) => model.mediaType === "image") ?? [];
  const videoModels = catalog?.models.filter((model) => model.mediaType === "video") ?? [];

  return (
    <section className="ai-video-page" aria-label="AI 视频设置">
      <header className="ai-video-module-header">
        <div>
          <p className="ai-video-eyebrow">AI 创作工具</p>
          <h2>AI 视频设置</h2>
          <p>平台 Key、脚本模型、生成参数、配音与品牌。所有修改即时保存在本机，不需要额外保存。</p>
        </div>
        <span className={`ai-video-service-badge${fullyConfigured ? " is-ready" : " is-warning"}`}>
          {fullyConfigured ? "● 基础配置已完成" : "● 待补充基础配置"}
        </span>
      </header>

      {!fullyConfigured && (
        <div className="ai-video-settings-warning" role="status">
          <strong>需要重新填写 Key</strong>
          <p>
            旧内嵌页面的设置存在 sidecar 的随机端口下，无法自动迁移；此处填写的 Key 会保存在 MainPG 本地。
            未配置时仍可浏览项目，但生成脚本 / 出片会失败。
          </p>
          <ul>
            {!llm.apiKey.trim() && <li>尚未填写脚本模型（LLM）API Key</li>}
            {!hasMediaProvider && <li>尚未启用任何生成平台并填写 Key</li>}
          </ul>
        </div>
      )}

      <nav className="ai-video-settings-tabs" aria-label="设置分区">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={tab === item.id ? "is-active" : ""}
            aria-current={tab === item.id ? "page" : undefined}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {tab === "providers" && (
        <section className="ai-video-settings-card" aria-label="生成平台">
          <div className="ai-video-settings-card-head">
            <h3>生成平台</h3>
            <p>用于图像 / 视频生成与素材处理，启用后才会出现在模型列表里。</p>
          </div>
          <div className="ai-video-provider-list">
            {MEDIA_PROVIDERS.map(({ key, name, desc }) => {
              const provider = providers[key] ?? { enabled: false, apiKey: "" };
              const probe = providerTests[key] ?? IDLE_TEST;
              return (
                <article key={key} className="ai-video-provider-card">
                  <div className="ai-video-provider-head">
                    <div>
                      <h4>{name}</h4>
                      <p>{desc}</p>
                    </div>
                    <label className="ai-video-switch">
                      <input
                        type="checkbox"
                        checked={provider.enabled}
                        onChange={(event) => setProvider(key, { ...provider, enabled: event.target.checked })}
                      />
                      <span>{provider.enabled ? "已启用" : "未启用"}</span>
                    </label>
                  </div>
                  <div className="ai-video-settings-grid">
                    <div className="ai-video-field">
                      <label htmlFor={`ai-video-key-${key}`}>API Key</label>
                      <input
                        id={`ai-video-key-${key}`}
                        type="password"
                        autoComplete="off"
                        spellCheck={false}
                        value={provider.apiKey}
                        placeholder="填入平台 API Key"
                        onChange={(event) =>
                          setProvider(key, {
                            ...provider,
                            enabled: event.target.value.trim() ? true : provider.enabled,
                            apiKey: event.target.value,
                          })
                        }
                      />
                    </div>
                    <div className="ai-video-field">
                      <label htmlFor={`ai-video-base-${key}`}>自定义地址（可选）</label>
                      <input
                        id={`ai-video-base-${key}`}
                        type="url"
                        spellCheck={false}
                        value={provider.baseUrl ?? ""}
                        placeholder="留空使用平台默认地址"
                        onChange={(event) =>
                          setProvider(key, {
                            ...provider,
                            baseUrl: event.target.value.trim() ? event.target.value : undefined,
                          })
                        }
                      />
                    </div>
                  </div>
                  <div className="ai-video-settings-actions">
                    <button
                      type="button"
                      className="is-secondary"
                      disabled={!provider.apiKey.trim() || probe.status === "testing"}
                      onClick={() => void handleTestProvider(key)}
                    >
                      {probe.status === "testing" ? "测试中…" : "测试 Key"}
                    </button>
                    {probe.message && (
                      <span className={`ai-video-settings-message is-${probe.status === "ok" ? "ok" : probe.status === "testing" ? "testing" : "error"}`}>
                        {probe.message}
                      </span>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      {tab === "llm" && (
        <section className="ai-video-settings-card" aria-label="脚本模型">
          <div className="ai-video-settings-card-head">
            <h3>脚本模型（LLM）</h3>
            <p>OpenAI 兼容接口：脚本生成、商品图分析与判官团都走这里。</p>
          </div>
          <div className="ai-video-settings-grid">
            <div className="ai-video-field">
              <label htmlFor="ai-video-llm-provider">服务名称（备注）</label>
              <input
                id="ai-video-llm-provider"
                value={llm.provider}
                placeholder="例如 DeepSeek / Kimi / 本地 Ollama"
                onChange={(event) => setLLM({ ...llm, provider: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-llm-base">接口地址</label>
              <input
                id="ai-video-llm-base"
                type="url"
                spellCheck={false}
                value={llm.baseUrl}
                placeholder="https://api.deepseek.com"
                onChange={(event) => setLLM({ ...llm, baseUrl: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-llm-key">API Key</label>
              <input
                id="ai-video-llm-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={llm.apiKey}
                placeholder="sk-..."
                onChange={(event) => setLLM({ ...llm, apiKey: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-llm-model">模型</label>
              <input
                id="ai-video-llm-model"
                spellCheck={false}
                list="ai-video-llm-models"
                value={llm.model}
                placeholder="deepseek-v4-flash"
                onChange={(event) => setLLM({ ...llm, model: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-llm-vision">看图模型（可选）</label>
              <input
                id="ai-video-llm-vision"
                spellCheck={false}
                list="ai-video-llm-models"
                value={llm.visionModel ?? ""}
                placeholder="留空则复用上方模型"
                onChange={(event) => setLLM({ ...llm, visionModel: event.target.value })}
              />
            </div>
          </div>
          <datalist id="ai-video-llm-models">
            {llmModels.map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
          <div className="ai-video-settings-actions">
            <button type="button" onClick={() => void handleTestLlm()} disabled={llmTest.status === "testing"}>
              {llmTest.status === "testing" ? "正在测试…" : "测试连接"}
            </button>
            <button type="button" className="is-secondary" onClick={() => void handleLoadLlmModels()} disabled={loadingModels}>
              {loadingModels ? "正在读取…" : "拉取模型列表"}
            </button>
            {llmTest.message && (
              <span className={`ai-video-settings-message is-${llmTest.status === "ok" ? "ok" : llmTest.status === "testing" ? "testing" : "error"}`}>
                {llmTest.message}
              </span>
            )}
            {modelsError && <span className="ai-video-settings-message is-error">{modelsError}</span>}
          </div>
        </section>
      )}

      {tab === "models" && (
        <section className="ai-video-settings-card" aria-label="模型目录">
          <div className="ai-video-settings-card-head">
            <h3>模型目录与默认模型</h3>
            <p>读取已启用平台暴露的模型；点模型名即可设为默认生图 / 生视频模型。</p>
          </div>
          <div className="ai-video-settings-grid">
            <div className="ai-video-field">
              <label htmlFor="ai-video-default-image">默认生图模型</label>
              <input
                id="ai-video-default-image"
                spellCheck={false}
                value={defaultImageModel}
                placeholder="image_gpt"
                onChange={(event) => setDefaultImageModel(event.target.value)}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-default-video">默认生视频模型</label>
              <input
                id="ai-video-default-video"
                spellCheck={false}
                value={defaultVideoModel}
                placeholder="video_minimax_h3"
                onChange={(event) => setDefaultVideoModel(event.target.value)}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-resolution">默认分辨率</label>
              <select
                id="ai-video-resolution"
                value={defaultResolution}
                onChange={(event) => setDefaultResolution(event.target.value === "1080p" ? "1080p" : "720p")}
              >
                <option value="720p">720p</option>
                <option value="1080p">1080p</option>
              </select>
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-aspect">默认画幅</label>
              <select
                id="ai-video-aspect"
                value={defaultAspectRatio}
                onChange={(event) =>
                  setDefaultAspectRatio(event.target.value === "16:9" || event.target.value === "1:1" ? event.target.value : "9:16")
                }
              >
                <option value="9:16">9:16 竖屏</option>
                <option value="16:9">16:9 横屏</option>
                <option value="1:1">1:1 方形</option>
              </select>
            </div>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" onClick={() => void handleRefreshCatalog(false)} disabled={catalogLoading}>
              {catalogLoading ? "正在读取…" : "刷新模型目录"}
            </button>
            <button type="button" className="is-secondary" onClick={() => void handleRefreshCatalog(true)} disabled={catalogLoading}>
              强制刷新上游
            </button>
            {catalogError && <span className="ai-video-settings-message is-error">{catalogError}</span>}
          </div>
          {catalog && (
            <div className="ai-video-model-catalog">
              <div>
                <h4>生图模型（{imageModels.length}）</h4>
                <div className="ai-video-chip-row">
                  {imageModels.map((model) => (
                    <button
                      key={`${model.provider}-${model.id}`}
                      type="button"
                      className={`ai-video-chip${defaultImageModel === model.id ? " is-active" : ""}`}
                      onClick={() => setDefaultImageModel(model.id)}
                      title={`${model.provider} · ${model.id}`}
                    >
                      {model.name || model.id}
                    </button>
                  ))}
                  {imageModels.length === 0 && <span className="ai-video-field-hint">没有生图模型</span>}
                </div>
              </div>
              <div>
                <h4>生视频模型（{videoModels.length}）</h4>
                <div className="ai-video-chip-row">
                  {videoModels.map((model) => (
                    <button
                      key={`${model.provider}-${model.id}`}
                      type="button"
                      className={`ai-video-chip${defaultVideoModel === model.id ? " is-active" : ""}`}
                      onClick={() => setDefaultVideoModel(model.id)}
                      title={`${model.provider} · ${model.id}`}
                    >
                      {model.name || model.id}
                    </button>
                  ))}
                  {videoModels.length === 0 && <span className="ai-video-field-hint">没有生视频模型</span>}
                </div>
              </div>
              <div className="ai-video-catalog-status">
                {catalog.providers.map((status) => (
                  <span key={`${status.provider}-${status.mediaType}`} className={`is-${status.status}`}>
                    {status.provider} · {status.mediaType === "image" ? "生图" : "生视频"}：{status.count} 个
                    {status.errorCode ? `（${status.errorCode}）` : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {tab === "generation" && (
        <>
          <section className="ai-video-settings-card" aria-label="生图参数">
            <div className="ai-video-settings-card-head">
              <h3>生图默认参数</h3>
              <p>留空表示使用平台默认值；这些参数会作为每次生成的初始值。</p>
            </div>
            <div className="ai-video-settings-grid">
              <div className="ai-video-field">
                <label htmlFor="ai-video-img-aspect">画幅</label>
                <select
                  id="ai-video-img-aspect"
                  value={imageParams.aspectRatio}
                  onChange={(event) =>
                    setImageParams({
                      ...imageParams,
                      aspectRatio: event.target.value === "16:9" || event.target.value === "1:1" ? event.target.value : "9:16",
                    })
                  }
                >
                  <option value="9:16">9:16 竖屏</option>
                  <option value="16:9">16:9 横屏</option>
                  <option value="1:1">1:1 方形</option>
                </select>
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-img-count">生成张数</label>
                <input
                  id="ai-video-img-count"
                  type="number"
                  min={1}
                  value={imageParams.count}
                  onChange={(event) => setImageParams({ ...imageParams, count: parseOptionalNumber(event.target.value) ?? 1 })}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-img-steps">步数（可选）</label>
                <input
                  id="ai-video-img-steps"
                  type="number"
                  value={imageParams.steps ?? ""}
                  placeholder="平台默认"
                  onChange={(event) => setImageParams({ ...imageParams, steps: parseOptionalNumber(event.target.value) })}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-img-guidance">引导强度（可选）</label>
                <input
                  id="ai-video-img-guidance"
                  type="number"
                  step="0.1"
                  value={imageParams.guidanceScale ?? ""}
                  placeholder="平台默认"
                  onChange={(event) =>
                    setImageParams({ ...imageParams, guidanceScale: parseOptionalNumber(event.target.value) })
                  }
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-img-seed">随机种子（可选）</label>
                <input
                  id="ai-video-img-seed"
                  type="number"
                  value={imageParams.seed ?? ""}
                  placeholder="留空每次随机"
                  onChange={(event) => setImageParams({ ...imageParams, seed: parseOptionalNumber(event.target.value) })}
                />
              </div>
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-img-negative">反向提示词</label>
              <textarea
                id="ai-video-img-negative"
                rows={2}
                value={imageParams.negativePrompt ?? ""}
                onChange={(event) =>
                  setImageParams({ ...imageParams, negativePrompt: event.target.value || undefined })
                }
              />
            </div>
          </section>

          <section className="ai-video-settings-card" aria-label="生视频参数">
            <div className="ai-video-settings-card-head">
              <h3>生视频默认参数</h3>
              <p>逐镜生视频时使用；单个项目里仍可覆盖。</p>
            </div>
            <div className="ai-video-settings-grid">
              <div className="ai-video-field">
                <label htmlFor="ai-video-vid-aspect">画幅</label>
                <select
                  id="ai-video-vid-aspect"
                  value={videoParams.aspectRatio}
                  onChange={(event) =>
                    setVideoParams({
                      ...videoParams,
                      aspectRatio: event.target.value === "16:9" || event.target.value === "1:1" ? event.target.value : "9:16",
                    })
                  }
                >
                  <option value="9:16">9:16 竖屏</option>
                  <option value="16:9">16:9 横屏</option>
                  <option value="1:1">1:1 方形</option>
                </select>
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-vid-resolution">分辨率</label>
                <select
                  id="ai-video-vid-resolution"
                  value={videoParams.resolution}
                  onChange={(event) =>
                    setVideoParams({ ...videoParams, resolution: event.target.value === "1080p" ? "1080p" : "720p" })
                  }
                >
                  <option value="720p">720p</option>
                  <option value="1080p">1080p</option>
                </select>
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-vid-duration">时长（秒）</label>
                <input
                  id="ai-video-vid-duration"
                  type="number"
                  min={1}
                  value={videoParams.duration ?? ""}
                  onChange={(event) => setVideoParams({ ...videoParams, duration: parseOptionalNumber(event.target.value) })}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-vid-fps">帧率（可选）</label>
                <input
                  id="ai-video-vid-fps"
                  type="number"
                  value={videoParams.fps ?? ""}
                  placeholder="平台默认"
                  onChange={(event) => setVideoParams({ ...videoParams, fps: parseOptionalNumber(event.target.value) })}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-vid-motion">运动强度（可选）</label>
                <input
                  id="ai-video-vid-motion"
                  type="number"
                  step="0.1"
                  value={videoParams.motionStrength ?? ""}
                  placeholder="平台默认"
                  onChange={(event) =>
                    setVideoParams({ ...videoParams, motionStrength: parseOptionalNumber(event.target.value) })
                  }
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-vid-seed">随机种子（可选）</label>
                <input
                  id="ai-video-vid-seed"
                  type="number"
                  value={videoParams.seed ?? ""}
                  placeholder="留空每次随机"
                  onChange={(event) => setVideoParams({ ...videoParams, seed: parseOptionalNumber(event.target.value) })}
                />
              </div>
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-vid-negative">反向提示词</label>
              <textarea
                id="ai-video-vid-negative"
                rows={2}
                value={videoParams.negativePrompt ?? ""}
                onChange={(event) =>
                  setVideoParams({ ...videoParams, negativePrompt: event.target.value || undefined })
                }
              />
            </div>
          </section>
        </>
      )}

      {tab === "tts" && (
        <section className="ai-video-settings-card" aria-label="配音">
          <div className="ai-video-settings-card-head">
            <h3>配音（TTS）</h3>
            <p>未配置付费音色时，试听会自动使用免费 Edge 音色。</p>
          </div>
          <label className="ai-video-switch">
            <input type="checkbox" checked={tts.enabled} onChange={(event) => setTTS({ ...tts, enabled: event.target.checked })} />
            <span>{tts.enabled ? "启用配音" : "未启用配音"}</span>
          </label>
          <div className="ai-video-settings-grid">
            <div className="ai-video-field">
              <label htmlFor="ai-video-tts-provider">平台</label>
              <select
                id="ai-video-tts-provider"
                value={tts.provider ?? "volcengine-speech"}
                onChange={(event) => changeTtsProvider(event.target.value as TTSProvider)}
              >
                {TTS_PROVIDERS.map((provider) => (
                  <option key={provider.value} value={provider.value}>{provider.label}</option>
                ))}
              </select>
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-tts-model">模型</label>
              {ttsMeta.models.length > 0 ? (
                <select
                  id="ai-video-tts-model"
                  value={tts.model}
                  onChange={(event) => setTTS({ ...tts, model: event.target.value })}
                >
                  <option value="">请选择模型</option>
                  {ttsMeta.models.map((model) => (
                    <option key={model.value} value={model.value}>{model.label}</option>
                  ))}
                </select>
              ) : (
                <input
                  id="ai-video-tts-model"
                  value={tts.model}
                  onChange={(event) => setTTS({ ...tts, model: event.target.value })}
                />
              )}
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-tts-key">API Key</label>
              <input
                id="ai-video-tts-key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={tts.apiKey}
                onChange={(event) => setTTS({ ...tts, apiKey: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-tts-voice">音色 ID</label>
              <input
                id="ai-video-tts-voice"
                value={tts.voice}
                placeholder={ttsMeta.voices[0]?.value ?? "粘贴已授权的音色 ID"}
                onChange={(event) => setTTS({ ...tts, voice: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-tts-base">接口地址</label>
              <input
                id="ai-video-tts-base"
                type="url"
                spellCheck={false}
                value={tts.baseUrl}
                onChange={(event) => setTTS({ ...tts, baseUrl: event.target.value })}
              />
            </div>
            {ttsMeta.value === "volcengine-speech" && (
              <>
                <div className="ai-video-field">
                  <label htmlFor="ai-video-tts-resource">资源 ID</label>
                  <input
                    id="ai-video-tts-resource"
                    value={tts.resourceId ?? ""}
                    onChange={(event) => setTTS({ ...tts, resourceId: event.target.value })}
                  />
                </div>
                <div className="ai-video-field">
                  <label htmlFor="ai-video-tts-rate">语速（-50–100）</label>
                  <input
                    id="ai-video-tts-rate"
                    type="number"
                    value={tts.speechRate ?? 10}
                    onChange={(event) => setTTS({ ...tts, speechRate: parseOptionalNumber(event.target.value) })}
                  />
                </div>
              </>
            )}
            {ttsMeta.needsGroupId && (
              <div className="ai-video-field">
                <label htmlFor="ai-video-tts-group">GroupId</label>
                <input
                  id="ai-video-tts-group"
                  value={tts.groupId ?? ""}
                  onChange={(event) => setTTS({ ...tts, groupId: event.target.value })}
                />
              </div>
            )}
          </div>
          {ttsMeta.hint && <p className="ai-video-field-hint">{ttsMeta.hint}</p>}
          <div className="ai-video-settings-actions">
            <button type="button" onClick={() => void handleTtsPreview()} disabled={ttsPreview.status === "testing"}>
              {ttsPreview.status === "testing" ? "正在生成…" : "试听"}
            </button>
            {ttsPreview.message && (
              <span className={`ai-video-settings-message is-${ttsPreview.status === "ok" ? "ok" : ttsPreview.status === "testing" ? "testing" : "error"}`}>
                {ttsPreview.message}
                {ttsPreview.status === "ok" && (ttsUsingFree ? "（免费 Edge 音色）" : "")}
              </span>
            )}
          </div>
        </section>
      )}

      {tab === "characters" && (
        <section className="ai-video-settings-card" aria-label="角色">
          <div className="ai-video-settings-card-head">
            <h3>出镜角色</h3>
            <p>与「主播素材」共用同一份角色库；在这里也能直接管理。</p>
          </div>
          <PresenterManager />
        </section>
      )}

      {tab === "brand" && (
        <section className="ai-video-settings-card" aria-label="品牌">
          <div className="ai-video-settings-card-head">
            <h3>品牌设置</h3>
            <p>导出页会读取这份配置渲染水印、片头片尾等品牌元素（导出页 T2.4 接入）。</p>
          </div>
          <div className="ai-video-settings-grid">
            <div className="ai-video-field">
              <label htmlFor="ai-video-brand-name">店铺 / 品牌名</label>
              <input
                id="ai-video-brand-name"
                value={brand.name}
                onChange={(event) => updateBrand({ name: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-brand-font">字体</label>
              <input
                id="ai-video-brand-font"
                value={brand.fontFamily}
                onChange={(event) => updateBrand({ fontFamily: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-brand-primary">主色</label>
              <input
                id="ai-video-brand-primary"
                type="color"
                value={brand.primaryColor}
                onChange={(event) => updateBrand({ primaryColor: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-brand-secondary">辅色</label>
              <input
                id="ai-video-brand-secondary"
                type="color"
                value={brand.secondaryColor}
                onChange={(event) => updateBrand({ secondaryColor: event.target.value })}
              />
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-brand-logo">Logo 地址（可选）</label>
              <input
                id="ai-video-brand-logo"
                type="url"
                spellCheck={false}
                value={brand.logoUrl ?? ""}
                onChange={(event) => updateBrand({ logoUrl: event.target.value || undefined })}
              />
            </div>
          </div>

          <div className="ai-video-field">
            <label>水印</label>
            <div className="ai-video-settings-actions">
              <label className="ai-video-switch">
                <input
                  type="checkbox"
                  checked={brand.watermark.enabled}
                  onChange={(event) => updateWatermark({ enabled: event.target.checked })}
                />
                <span>{brand.watermark.enabled ? "显示水印" : "不显示水印"}</span>
              </label>
            </div>
            <div className="ai-video-settings-grid">
              <div className="ai-video-field">
                <label htmlFor="ai-video-watermark-position">位置</label>
                <select
                  id="ai-video-watermark-position"
                  value={brand.watermark.position}
                  onChange={(event) => updateWatermark({ position: event.target.value as typeof brand.watermark.position })}
                >
                  <option value="top-left">左上</option>
                  <option value="top-right">右上</option>
                  <option value="bottom-left">左下</option>
                  <option value="bottom-right">右下</option>
                </select>
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-watermark-opacity">不透明度：{Math.round(brand.watermark.opacity * 100)}%</label>
                <input
                  id="ai-video-watermark-opacity"
                  type="range"
                  min={0.1}
                  max={1}
                  step={0.05}
                  value={brand.watermark.opacity}
                  onChange={(event) => updateWatermark({ opacity: Number(event.target.value) })}
                />
              </div>
              <div className="ai-video-field">
                <label htmlFor="ai-video-watermark-scale">大小：{Math.round(brand.watermark.scale * 100)}%</label>
                <input
                  id="ai-video-watermark-scale"
                  type="range"
                  min={0.1}
                  max={0.5}
                  step={0.05}
                  value={brand.watermark.scale}
                  onChange={(event) => updateWatermark({ scale: Number(event.target.value) })}
                />
              </div>
            </div>
          </div>

          <div className="ai-video-batch-toggles">
            <label>
              <input type="checkbox" checked={brand.introEnabled} onChange={(event) => updateBrand({ introEnabled: event.target.checked })} />
              片头卡片
            </label>
            <label>
              <input type="checkbox" checked={brand.outroEnabled} onChange={(event) => updateBrand({ outroEnabled: event.target.checked })} />
              片尾卡片
            </label>
          </div>
          {brand.outroEnabled && (
            <div className="ai-video-field">
              <label htmlFor="ai-video-brand-outro">片尾文案</label>
              <input
                id="ai-video-brand-outro"
                value={brand.outroText ?? ""}
                placeholder="例如：关注我们获取更多好物推荐"
                onChange={(event) => updateBrand({ outroText: event.target.value || undefined })}
              />
            </div>
          )}
        </section>
      )}

      {tab === "diagnostics" && (
        <section className="ai-video-settings-card" aria-label="诊断">
          <div className="ai-video-settings-card-head">
            <h3>系统诊断</h3>
            <p>读取 sidecar 的 /api/health 快照；报障时可直接复制这段文本。</p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" onClick={() => void handleLoadDiagnostics()}>读取诊断</button>
            <button type="button" className="is-secondary" disabled={!diagnostics} onClick={() => void handleCopyDiagnostics()}>
              复制
            </button>
            {diagnosticsMessage && <span className="ai-video-settings-message">{diagnosticsMessage}</span>}
          </div>
          {diagnostics && <pre className="ai-video-diagnostics">{diagnostics.text}</pre>}
        </section>
      )}

      <p className="ai-video-settings-note">所有设置保存在本机 localStorage；换机器或清理浏览器数据后需要重新填写。</p>
    </section>
  );
}

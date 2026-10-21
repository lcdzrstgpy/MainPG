import { useRef, useState } from "react";

import { sanitizeCreationBrief, type CreationBrief, type InputMode, type OutputStrategy } from "../lib/creation-brief";
import {
  CATEGORY_OPTIONS,
  DURATION_OPTIONS,
  INPUT_MODE_OPTIONS,
  MAX_SOURCE_IMAGES,
  OUTPUT_STRATEGY_OPTIONS,
  PLATFORM_OPTIONS,
  SCRIPT_STYLE_OPTIONS,
  TOPIC_NARRATION_STYLE_OPTIONS,
  VIDEO_MODE_OPTIONS,
  coerceStyleTypeForInputMode,
  defaultAudioStrategyFor,
  resolveStyleSource,
  validateCreationBriefForm,
  type VideoModeId,
} from "../lib/creation-brief-defaults";

export interface CreationFormImage {
  id: string;
  url: string;
  file: File;
}

/** 表单收集到的全部内容：简报 + 建项目需要的来源字段。表单自己不发任何请求。 */
export interface CreationFormValues {
  brief: CreationBrief;
  productName: string;
  category: string;
  sellingPoints: string;
  images: CreationFormImage[];
  linkUrl: string;
  topic: string;
  videoMode: VideoModeId;
  /** 服务端已落盘的商品图（链接导入 / 商品库 / 复刻交接），创建时直接写进 projectImages */
  serverImages: string[];
  strategyChosen: boolean;
}

export interface CreationProductOption {
  id: string;
  name: string;
  description?: string;
  images: string[];
}

type CreationBriefFormProps = {
  values: CreationFormValues;
  onChange: (next: CreationFormValues) => void;
  products: CreationProductOption[];
  /** 可绑定的出镜角色（真人出镜模式使用）。 */
  characters: ReadonlyArray<{ id: string; name: string }>;
  busy?: boolean;
  onImportLink: (url: string) => void;
  importing?: boolean;
  importError?: string;
  linkImported?: boolean;
  onSubmit: () => void;
  onGoProducts: () => void;
  onGoClone: () => void;
};

/** 统一创作简报表单：来源面板 → 商品信息 → 投放设置 → 策略 → 提交。 */
export function CreationBriefForm({
  values,
  onChange,
  products,
  characters,
  busy,
  onImportLink,
  importing,
  importError,
  linkImported,
  onSubmit,
  onGoProducts,
  onGoClone,
}: CreationBriefFormProps) {
  const [showErrors, setShowErrors] = useState(false);
  const [selectedProductId, setSelectedProductId] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const topicMode = values.brief.inputMode === "topic";
  const blocked = busy === true;

  const patchBrief = (partial: Partial<CreationBrief>) =>
    onChange({ ...values, brief: sanitizeCreationBrief({ ...values.brief, ...partial }) });

  const changeInputMode = (inputMode: InputMode) => {
    const styleType = coerceStyleTypeForInputMode(values.brief.styleType, inputMode);
    onChange({
      ...values,
      // 服务端已有商品图只属于「商品库 / 复刻」来源；切到其它来源时清掉，避免带错图建项目。
      serverImages: inputMode === "product-library" || inputMode === "clone" ? values.serverImages : [],
      brief: sanitizeCreationBrief({
        ...values.brief,
        inputMode,
        styleType,
        styleSource: resolveStyleSource({ styleType, templateId: values.brief.templateId }),
      }),
    });
  };

  const chooseStyle = (styleType: string) => {
    onChange({
      ...values,
      brief: sanitizeCreationBrief({
        ...values.brief,
        styleType,
        styleSource: resolveStyleSource({ styleType, templateId: values.brief.templateId }),
      }),
    });
  };

  const chooseStrategy = (outputStrategy: OutputStrategy) => {
    onChange({
      ...values,
      strategyChosen: true,
      brief: sanitizeCreationBrief({
        ...values.brief,
        outputStrategy,
        audioStrategy: defaultAudioStrategyFor(outputStrategy),
      }),
    });
  };

  const togglePlatform = (platform: string) => {
    if (values.brief.platforms.length === 1 && values.brief.platforms.includes(platform)) return;
    const platforms = values.brief.platforms.includes(platform)
      ? values.brief.platforms.filter((item) => item !== platform)
      : [...values.brief.platforms, platform];
    patchBrief({ platforms });
  };

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const remaining = MAX_SOURCE_IMAGES - values.images.length - values.serverImages.length;
    if (remaining <= 0) return;
    const added = Array.from(files)
      .slice(0, remaining)
      .filter((file) => file.type.startsWith("image/"))
      .map((file) => ({ id: crypto.randomUUID(), url: URL.createObjectURL(file), file }));
    if (added.length) onChange({ ...values, images: [...values.images, ...added] });
  };

  const removeImage = (id: string) => {
    const target = values.images.find((image) => image.id === id);
    if (target) URL.revokeObjectURL(target.url);
    onChange({ ...values, images: values.images.filter((image) => image.id !== id) });
  };

  const pickProduct = (productId: string) => {
    setSelectedProductId(productId);
    const product = products.find((item) => item.id === productId);
    if (!product) return;
    onChange({
      ...values,
      productName: product.name,
      sellingPoints: product.description ?? "",
      serverImages: product.images.slice(0, MAX_SOURCE_IMAGES),
      brief: sanitizeCreationBrief({
        ...values.brief,
        inputMode: "product-library",
        styleType: coerceStyleTypeForInputMode(values.brief.styleType, "product-library"),
      }),
    });
  };

  const validation = validateCreationBriefForm({
    productName: values.productName,
    // 本地文件与服务端已有商品图（商品库 / 链接 / 复刻）都算「有图」。
    images: [...values.images, ...values.serverImages],
    topic: values.topic,
    inputMode: values.brief.inputMode,
    linkImported,
    strategyChosen: values.strategyChosen,
  });

  const handleSubmit = () => {
    if (blocked || !validation.valid) {
      setShowErrors(true);
      return;
    }
    onSubmit();
  };

  const styleOptions = topicMode ? TOPIC_NARRATION_STYLE_OPTIONS : SCRIPT_STYLE_OPTIONS;

  return (
    <div className="ai-video-form">
      <section className="ai-video-form-card" aria-label="素材来源">
        <div className="ai-video-form-head">
          <h3>素材来源</h3>
          <p>五种入口共用同一份创作简报；提交后统一建项目、生成脚本。</p>
        </div>
        <div className="ai-video-source-tabs" role="tablist" aria-label="素材来源">
          {INPUT_MODE_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={values.brief.inputMode === option.id}
              className={`ai-video-source-tab${values.brief.inputMode === option.id ? " is-active" : ""}`}
              disabled={blocked}
              onClick={() => changeInputMode(option.id)}
            >
              <strong>{option.label}</strong>
              <span>{option.description}</span>
            </button>
          ))}
        </div>

        {values.brief.inputMode === "link" && (
          <div className="ai-video-link-row">
            <input
              type="url"
              value={values.linkUrl}
              disabled={blocked}
              placeholder="粘贴商品链接（https://...）"
              onChange={(event) => onChange({ ...values, linkUrl: event.target.value })}
            />
            <button
              type="button"
              className="is-secondary"
              disabled={blocked || importing || !values.linkUrl.trim()}
              onClick={() => onImportLink(values.linkUrl.trim())}
            >
              {importing ? "解析中…" : "抓取商品信息"}
            </button>
            {linkImported && <span className="ai-video-settings-message is-ok">已抓取，可在下方修改后提交</span>}
            {importError && <span className="ai-video-settings-message is-error">{importError}</span>}
          </div>
        )}

        {values.brief.inputMode === "topic" && (
          <div className="ai-video-field">
            <label htmlFor="ai-video-topic">一句话主题</label>
            <textarea
              id="ai-video-topic"
              rows={3}
              value={values.topic}
              disabled={blocked}
              placeholder="例如：在家如何泡一杯手冲咖啡"
              onChange={(event) => onChange({ ...values, topic: event.target.value })}
            />
            <span className="ai-video-field-hint">主题模式不使用商品图，脚本会按旁白风格生成，素材由免费素材库补足。</span>
          </div>
        )}

        {values.brief.inputMode === "product-library" && (
          <div className="ai-video-field">
            <label htmlFor="ai-video-library-product">选择商品</label>
            {products.length === 0 ? (
              <div className="ai-video-inline-empty">
                <span>商品库还是空的。</span>
                <button type="button" className="is-secondary" onClick={onGoProducts}>去商品素材</button>
              </div>
            ) : (
              <select
                id="ai-video-library-product"
                value={selectedProductId}
                disabled={blocked}
                onChange={(event) => pickProduct(event.target.value)}
              >
                <option value="">请选择商品</option>
                {products.map((product) => (
                  <option key={product.id} value={product.id}>{product.name}</option>
                ))}
              </select>
            )}
          </div>
        )}

        {values.brief.inputMode === "clone" && (
          <div className="ai-video-inline-empty">
            <span>
              {values.productName
                ? `已带入复刻商品「${values.productName}」，可修改后提交。`
                : "还没有复刻交接内容。"}
            </span>
            <button type="button" className="is-secondary" onClick={onGoClone}>去爆款复刻</button>
          </div>
        )}

        {!topicMode && (
          <div className="ai-video-upload">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={(event) => {
                addFiles(event.target.files);
                event.target.value = "";
              }}
            />
            <button
              type="button"
              className="ai-video-upload-drop"
              disabled={blocked || values.images.length + values.serverImages.length >= MAX_SOURCE_IMAGES}
              onClick={() => fileInputRef.current?.click()}
            >
              <strong>商品图（{values.images.length + values.serverImages.length}/{MAX_SOURCE_IMAGES}）</strong>
              <span>可手工补图；单张不超过 20MB</span>
            </button>
            {values.images.length > 0 && (
              <div className="ai-video-thumbs">
                {values.images.map((image) => (
                  <figure key={image.id} className="ai-video-thumb">
                    <img src={image.url} alt="" />
                    <button type="button" aria-label="移除图片" disabled={blocked} onClick={() => removeImage(image.id)}>
                      ×
                    </button>
                  </figure>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      {!topicMode && (
        <section className="ai-video-form-card" aria-label="商品信息">
          <div className="ai-video-form-head">
            <h3>商品信息</h3>
            <p>名称会写进项目与脚本提示词；卖点越具体，脚本越贴近真实卖点。</p>
          </div>
          <div className="ai-video-settings-grid">
            <div className="ai-video-field">
              <label htmlFor="ai-video-product-name">商品名称 *</label>
              <input
                id="ai-video-product-name"
                value={values.productName}
                disabled={blocked}
                placeholder="例如：桂花乌龙茶"
                onChange={(event) => onChange({ ...values, productName: event.target.value })}
              />
              {showErrors && validation.errors.productName && (
                <span className="ai-video-field-error">{validation.errors.productName}</span>
              )}
            </div>
            <div className="ai-video-field">
              <label htmlFor="ai-video-product-category">商品品类</label>
              <select
                id="ai-video-product-category"
                value={values.category}
                disabled={blocked}
                onChange={(event) => onChange({ ...values, category: event.target.value })}
              >
                <option value="">不指定</option>
                {CATEGORY_OPTIONS.map((option) => (
                  <option key={option.id} value={option.id}>{option.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="ai-video-field">
            <label htmlFor="ai-video-selling-points">卖点描述</label>
            <textarea
              id="ai-video-selling-points"
              rows={3}
              value={values.sellingPoints}
              disabled={blocked}
              placeholder="例如：0 糖 0 卡，3 秒出泡，冷热皆可"
              onChange={(event) => onChange({ ...values, sellingPoints: event.target.value })}
            />
          </div>
          {showErrors && validation.errors.images && (
            <span className="ai-video-field-error">{validation.errors.images}</span>
          )}
        </section>
      )}

      <section className="ai-video-form-card" aria-label="投放设置">
        <div className="ai-video-form-head">
          <h3>投放设置</h3>
          <p>风格、时长与平台都会写进创作简报，后续脚本与出片按它执行。</p>
        </div>

        <div className="ai-video-field">
          <label>目标时长</label>
          <div className="ai-video-chip-row">
            {DURATION_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`ai-video-chip${values.brief.targetDuration === option.id ? " is-active" : ""}`}
                aria-pressed={values.brief.targetDuration === option.id}
                disabled={blocked}
                onClick={() => patchBrief({ targetDuration: option.id })}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="ai-video-field">
          <label>{topicMode ? "主题旁白风格" : "脚本风格"}</label>
          <div className="ai-video-chip-row">
            {styleOptions.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`ai-video-chip${values.brief.styleType === option.id ? " is-active" : ""}`}
                aria-pressed={values.brief.styleType === option.id}
                title={option.description}
                disabled={blocked}
                onClick={() => chooseStyle(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {!topicMode && (
          <div className="ai-video-field">
            <label>画面模式</label>
            <div className="ai-video-choice-grid">
              {VIDEO_MODE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  className={`ai-video-choice-card${values.videoMode === option.id ? " is-active" : ""}`}
                  aria-pressed={values.videoMode === option.id}
                  disabled={blocked}
                  onClick={() =>
                    onChange({
                      ...values,
                      videoMode: option.id,
                      // 角色只对真人出镜有意义：切到其它模式时解绑，避免留下无效引用。
                      brief:
                        option.id === "live_presenter"
                          ? values.brief
                          : sanitizeCreationBrief({ ...values.brief, characterId: undefined }),
                    })
                  }
                >
                  <strong>{option.label}</strong>
                  <span>{option.description}</span>
                </button>
              ))}
            </div>
            {values.videoMode === "live_presenter" && (
              <div className="ai-video-field">
                <label htmlFor="ai-video-character-select">出镜角色</label>
                <select
                  id="ai-video-character-select"
                  value={values.brief.characterId ?? ""}
                  disabled={blocked}
                  onChange={(event) => patchBrief({ characterId: event.target.value || undefined })}
                >
                  <option value="">不绑定角色</option>
                  {characters.map((character) => (
                    <option key={character.id} value={character.id}>{character.name}</option>
                  ))}
                </select>
                {characters.length === 0 && (
                  <span className="ai-video-field-hint">还没有角色，可先去「主播素材」创建并生成定妆照。</span>
                )}
              </div>
            )}
          </div>
        )}

        <div className="ai-video-field">
          <label>投放平台</label>
          <div className="ai-video-chip-row">
            {PLATFORM_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`ai-video-chip${values.brief.platforms.includes(option.id) ? " is-active" : ""}`}
                aria-pressed={values.brief.platforms.includes(option.id)}
                disabled={blocked}
                onClick={() => togglePlatform(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="ai-video-form-card" aria-label="出片策略">
        <div className="ai-video-form-head">
          <h3>出片策略</h3>
          <p>决定是否花钱、怎么花。默认「原生整片」，可改选其它策略。</p>
        </div>
        <div className="ai-video-choice-grid">
          {OUTPUT_STRATEGY_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              className={`ai-video-choice-card${values.brief.outputStrategy === option.id ? " is-active" : ""}`}
              aria-pressed={values.brief.outputStrategy === option.id}
              disabled={blocked}
              onClick={() => chooseStrategy(option.id)}
            >
              <strong>{option.label}</strong>
              <span>{option.description}</span>
            </button>
          ))}
        </div>
        {showErrors && validation.errors.outputStrategy && (
          <span className="ai-video-field-error">{validation.errors.outputStrategy}</span>
        )}
      </section>

      {showErrors && !validation.valid && (
        <div className="ai-video-form-error" role="alert">
          {Object.values(validation.errors).filter(Boolean).join("；")}
        </div>
      )}

      <button type="button" className="ai-video-submit" disabled={blocked} onClick={handleSubmit}>
        {blocked ? "正在创建…" : "创建项目并生成脚本"}
      </button>
    </div>
  );
}

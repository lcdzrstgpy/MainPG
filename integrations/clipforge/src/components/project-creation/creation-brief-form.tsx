"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LuCircleAlert, LuZap } from "react-icons/lu";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { sanitizeCreationBrief } from "@/lib/creation-brief";
import { OUTPUT_SCHEMES } from "@/lib/output-schemes";
import { sanitizeCreativeIntent, type CreativeIntent, type VisualBible } from "@/lib/production-system";
import { useCharacterStore } from "@/lib/stores/project-store";
import { useTemplateStore } from "@/lib/stores/template-store";
import { InputSourcePanel, type InputSourceImage } from "./input-source-panel";
import { NarrativePanel } from "./narrative-panel";
import { OutputSchemePanel } from "./output-scheme-panel";
import {
  EMPTY_VISUAL_CONSTRAINTS,
  VisualControlPanel,
  buildCreativeIntentFields,
  type VisualConstraintValues,
} from "./visual-control-panel";
import {
  AUDIENCE_OPTIONS,
  CATEGORY_OPTIONS,
  DEFAULT_VIDEO_MODE,
  DURATION_OPTIONS,
  MAX_SOURCE_IMAGES,
  PLATFORM_OPTIONS,
  PRICE_RANGE_OPTIONS,
  SCRIPT_STYLE_OPTIONS,
  TOPIC_NARRATION_STYLE_OPTIONS,
  coerceStyleTypeForInputMode,
  resolveStyleSource,
  validateCreationBriefForm,
  type VideoModeId,
} from "./creation-brief-defaults";
import type { CreationBrief, InputMode } from "./creation-brief-types";
import type { OutputSchemeSnapshot } from "@/lib/output-schemes";

/**
 * 表单收集到的全部内容：简报本身 + 建项目需要的来源字段（商品名/图片/来源）。
 * 入口页据此构造创建 DTO，表单自己不做任何请求。
 */
export interface CreationBriefFormValues {
  brief: CreationBrief;
  productName: string;
  category: string;
  sellingPoints: string;
  /** 本地商品图（含 File），入口页用它们走上传接口 */
  images: InputSourceImage[];
  linkUrl: string;
  topic: string;
  videoMode: VideoModeId;
  /** 由「场景与画面约束」映射并经 sanitize；始终存在（约束为空时是空 subject 的完整对象） */
  creativeIntent: CreativeIntent;
  /** 仅当画面约束确实收集到「画面禁忌」时产出，不凭空编造锚点 */
  visualBible?: VisualBible;
}

/** 入口页在挂载后推给表单的预填（商品库、热点、模板、示例商品等）。 */
export interface CreationBriefFormPrefill {
  brief?: Partial<CreationBrief>;
  productName?: string;
  category?: string;
  sellingPoints?: string;
  images?: InputSourceImage[];
  linkUrl?: string;
  topic?: string;
  videoMode?: VideoModeId;
}

export interface CreationBriefFormProps {
  initial?: Partial<CreationBrief>;
  submitLabel: string;
  showAdvanced?: boolean;
  /** 只要简报的调用方（向后兼容的原始契约） */
  onSubmit: (brief: CreationBrief) => void;
  /**
   * 需要来源字段（商品名/图片/来源）才能建项目的入口页走这个通道；
   * 提供它时表单只调它，避免同一次提交触发两条创建路径。
   */
  onSubmitForm?: (values: CreationBriefFormValues) => void;
  /** 实时快照：入口页据此驱动模板推荐等只读用途，不允许反向改表单状态 */
  onValuesChange?: (values: CreationBriefFormValues) => void;
  /** 外部预填内容，配合 `prefillKey` 在 key 变化时应用一次 */
  prefill?: CreationBriefFormPrefill;
  prefillKey?: string;
  /** 链接导入由入口页执行（它会发请求），表单只回传输入 */
  onImportLink?: (url: string) => void;
  importing?: boolean;
  importError?: string;
  /** 链接已导入成功：此时链接来源已有商品图，不再强制本地图片 */
  linkImported?: boolean;
  disabled?: boolean;
}

const CHIP_CLS = "px-3 py-1.5 rounded-full border text-xs font-medium transition-all disabled:opacity-40";
const CARD_CLS = "flex items-center justify-center h-11 rounded-lg border text-sm font-medium transition-all disabled:opacity-40";

/**
 * The only stateful container of the creation brief.
 *
 * It collects and validates; it never calls a model, never composes media, and never issues a
 * request of its own. Whatever needs to happen after submission belongs to the hosting page.
 */
export function CreationBriefForm({
  initial,
  submitLabel,
  showAdvanced,
  onSubmit,
  onSubmitForm,
  onValuesChange,
  prefill,
  prefillKey,
  onImportLink,
  importing,
  importError,
  linkImported,
  disabled,
}: CreationBriefFormProps) {
  const templates = useTemplateStore((state) => state.templates);
  const characters = useCharacterStore((state) => state.characters);

  const [brief, setBrief] = useState<CreationBrief>(() => sanitizeCreationBrief(initial));
  const [productName, setProductName] = useState("");
  const [category, setCategory] = useState("");
  const [sellingPoints, setSellingPoints] = useState("");
  const [images, setImages] = useState<InputSourceImage[]>([]);
  const [linkUrl, setLinkUrl] = useState("");
  const [topic, setTopic] = useState("");
  const [videoMode, setVideoMode] = useState<VideoModeId>(DEFAULT_VIDEO_MODE);
  const [constraints, setConstraints] = useState<VisualConstraintValues>(EMPTY_VISUAL_CONSTRAINTS);
  const [showErrors, setShowErrors] = useState(false);

  // every mutation runs through the shared sanitizer, so no field can drift out of contract
  const patchBrief = useCallback((partial: Partial<CreationBrief>) => {
    setBrief((prev) => sanitizeCreationBrief({ ...prev, ...partial }));
  }, []);

  // 外部预填（商品库/热点/模板/示例商品）：只在 prefillKey 变化时应用一次。
  // prefill 对象通常每次渲染都是新引用，所以经 ref 取最新值，绝不以它作为依赖。
  const latestPrefill = useRef<CreationBriefFormPrefill | undefined>(prefill);
  useEffect(() => {
    latestPrefill.current = prefill;
  }, [prefill]);
  useEffect(() => {
    const incoming = latestPrefill.current;
    if (!incoming) return;
    if (incoming.brief) patchBrief(incoming.brief);
    if (incoming.productName !== undefined) setProductName(incoming.productName);
    if (incoming.category !== undefined) setCategory(incoming.category);
    if (incoming.sellingPoints !== undefined) setSellingPoints(incoming.sellingPoints);
    if (incoming.linkUrl !== undefined) setLinkUrl(incoming.linkUrl);
    if (incoming.topic !== undefined) setTopic(incoming.topic);
    if (incoming.videoMode) setVideoMode(incoming.videoMode);
    if (incoming.images) {
      const next = incoming.images;
      setImages((prev) => {
        prev.forEach((image) => URL.revokeObjectURL(image.url));
        return next;
      });
    }
  }, [prefillKey, patchBrief]);

  // 画面约束是唯一来源：映射出的字段经共享 sanitizer 归一化为完整的 CreativeIntent（空 subject 也保留）
  const creativeIntent = sanitizeCreativeIntent(buildCreativeIntentFields(constraints));
  // 只映射确实收集到的「画面禁忌」；收集不到就不产出 visualBible，绝不编造其它锚点
  const forbiddenChanges = creativeIntent.negative ?? [];
  const visualBible: VisualBible | undefined = forbiddenChanges.length
    ? { characterAnchors: [], productAnchors: [], wardrobeAnchors: [], environmentAnchors: [], lightingAnchors: [], forbiddenChanges }
    : undefined;

  // 实时快照：只在真正收集到的字段变化时上报，避免每次渲染都推送新对象
  const valuesKey = JSON.stringify({ brief, productName, category, sellingPoints, linkUrl, topic, videoMode, constraints, imageCount: images.length });
  useEffect(() => {
    if (!onValuesChange) return;
    onValuesChange({ brief: sanitizeCreationBrief(brief), productName, category, sellingPoints, images, linkUrl, topic, videoMode, creativeIntent, visualBible });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- valuesKey 已经是所有被收集字段的值指纹
  }, [valuesKey]);

  const handleInputModeChange = useCallback(
    (inputMode: InputMode) => {
      // 两个来源各有自己的风格词表：切到主题时残留的带货风格会被引擎静默降级，所以显式归一化
      const styleType = coerceStyleTypeForInputMode(brief.styleType, inputMode);
      if (styleType === brief.styleType) {
        patchBrief({ inputMode });
        return;
      }
      patchBrief({ inputMode, styleType, styleSource: resolveStyleSource({ styleType, templateId: brief.templateId }) });
    },
    [brief.styleType, brief.templateId, patchBrief]
  );

  const handleStyleTypeChange = useCallback(
    (styleType: string) => patchBrief({ styleType, styleSource: resolveStyleSource({ styleType, templateId: brief.templateId }) }),
    [brief.templateId, patchBrief]
  );

  const handleTemplateIdChange = useCallback(
    (templateId?: string) => {
      const template = templateId ? templates.find((item) => item.id === templateId) : undefined;
      const styleType = template?.styleType || brief.styleType;
      patchBrief({ templateId, styleType, styleSource: resolveStyleSource({ styleType, templateId }) });
    },
    [brief.styleType, patchBrief, templates]
  );

  const handleVideoModeChange = useCallback(
    (mode: VideoModeId) => {
      setVideoMode(mode);
      // a character only makes sense while the presenter mode is active
      if (mode !== "live_presenter") patchBrief({ characterId: undefined });
    },
    [patchBrief]
  );

  const handleOutputSchemeChange = useCallback((value: OutputSchemeSnapshot) => {
    const scheme = value.id === "native-film" ? { ...OUTPUT_SCHEMES["native-film"] } : value;
    patchBrief({ outputScheme: scheme, outputStrategy: scheme.outputStrategy, audioStrategy: scheme.audioStrategy });
  }, [patchBrief]);

  const handleFilesSelected = useCallback((files: FileList | null) => {
    if (!files) return;
    setImages((prev) => {
      const remaining = MAX_SOURCE_IMAGES - prev.length;
      if (remaining <= 0) return prev;
      const added = Array.from(files)
        .slice(0, remaining)
        .filter((file) => file.type.startsWith("image/"))
        .map((file) => ({ id: crypto.randomUUID(), url: URL.createObjectURL(file), file }));
      return [...prev, ...added];
    });
  }, []);

  const handleRemoveImage = useCallback((id: string) => {
    setImages((prev) => {
      const target = prev.find((image) => image.id === id);
      if (target) URL.revokeObjectURL(target.url);
      return prev.filter((image) => image.id !== id);
    });
  }, []);

  const toggleAudience = (tag: string) => {
    const next = brief.targetAudience.includes(tag)
      ? brief.targetAudience.filter((item) => item !== tag)
      : [...brief.targetAudience, tag];
    patchBrief({ targetAudience: next });
  };

  const togglePlatform = (platform: string) => {
    // a brief always publishes somewhere, so the last selected platform cannot be removed
    if (brief.platforms.length === 1 && brief.platforms.includes(platform)) return;
    const next = brief.platforms.includes(platform)
      ? brief.platforms.filter((item) => item !== platform)
      : [...brief.platforms, platform];
    patchBrief({ platforms: next });
  };

  const validation = validateCreationBriefForm({ productName, images, topic, inputMode: brief.inputMode, linkImported });
  const blocked = disabled === true;
  // 一句话主题走的是主题引擎的旁白风格词表，与带货脚本风格不通用
  const topicMode = brief.inputMode === "topic";

  const handleSubmit = () => {
    if (!validation.valid || blocked) {
      setShowErrors(true);
      return;
    }
    const submitted = sanitizeCreationBrief(brief);
    if (onSubmitForm) {
      // 入口页需要来源字段（商品名/图片/来源）才能建项目：只走这一条提交路径
      onSubmitForm({ brief: submitted, productName, category, sellingPoints, images, linkUrl, topic, videoMode, creativeIntent, visualBible });
      return;
    }
    onSubmit(submitted);
  };

  return (
    <div className="space-y-6">
      <InputSourcePanel
        inputMode={brief.inputMode}
        onInputModeChange={handleInputModeChange}
        images={images}
        onFilesSelected={handleFilesSelected}
        onRemoveImage={handleRemoveImage}
        linkUrl={linkUrl}
        onLinkUrlChange={setLinkUrl}
        onImportLink={onImportLink}
        importing={importing}
        importError={importError}
        topic={topic}
        onTopicChange={setTopic}
        disabled={blocked}
      />

      <Card className="glass-card">
        <CardContent className="p-5 space-y-5">
          <span className="text-sm font-semibold">商品信息</span>
          <div className="space-y-2">
            <Label className="text-sm font-medium">
              商品名称
              <span className="text-destructive ml-0.5">*</span>
            </Label>
            <Input
              aria-label="商品名称"
              value={productName}
              onChange={(event) => setProductName(event.target.value)}
              placeholder="例如：桂花乌龙茶"
              disabled={blocked}
            />
            {showErrors && validation.errors.productName && (
              <p className="text-xs text-destructive">{validation.errors.productName}</p>
            )}
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">卖点描述</Label>
              <span className="text-xs text-muted-foreground">可选</span>
            </div>
            <Textarea
              value={sellingPoints}
              onChange={(event) => setSellingPoints(event.target.value)}
              rows={3}
              placeholder="例如：0 糖 0 卡，3 秒出泡，冷热皆可"
              disabled={blocked}
            />
          </div>
          {showAdvanced && (
            <div className="space-y-2 pt-4 border-t border-border/40">
              <Label className="text-sm font-medium">商品品类</Label>
              <div className="flex flex-wrap gap-2">
                {CATEGORY_OPTIONS.map((option) => {
                  const active = option.id === category;
                  return (
                    <button
                      key={option.id}
                      type="button"
                      aria-pressed={active}
                      disabled={blocked}
                      onClick={() => setCategory(active ? "" : option.id)}
                      className={`${CHIP_CLS} ${
                        active
                          ? "bg-primary/15 text-primary border-primary/30"
                          : "bg-muted/20 text-muted-foreground border-border/50 hover:border-primary/30"
                      }`}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <NarrativePanel
        styleType={brief.styleType}
        onStyleTypeChange={handleStyleTypeChange}
        styleSource={brief.styleSource}
        narrative={brief.narrative}
        onNarrativeChange={(narrative) => patchBrief({ narrative })}
        styleOptions={topicMode ? TOPIC_NARRATION_STYLE_OPTIONS : SCRIPT_STYLE_OPTIONS}
        styleLabel={topicMode ? "主题旁白风格" : "脚本风格"}
        disabled={blocked}
      />

      <section aria-label="投放设置">
      <Card className="glass-card">
        <CardContent className="p-5 space-y-5">
          <span className="text-sm font-semibold">投放设置</span>
          <div className="space-y-2">
            <Label className="text-sm font-medium">目标时长</Label>
            <div className="grid grid-cols-3 gap-3">
              {DURATION_OPTIONS.map((option) => {
                const active = option.id === brief.targetDuration;
                return (
                  <button
                    key={option.id}
                    type="button"
                    aria-pressed={active}
                    disabled={blocked}
                    onClick={() => patchBrief({ targetDuration: option.id })}
                    className={`${CARD_CLS} ${
                      active
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          {showAdvanced && (
            <div className="space-y-5 pt-4 border-t border-border/40">
              <div className="space-y-2">
                <Label className="text-sm font-medium">价格定位</Label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {PRICE_RANGE_OPTIONS.map((option) => {
                    const active = option.id === brief.priceRange;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        aria-pressed={active}
                        disabled={blocked}
                        onClick={() => patchBrief({ priceRange: active ? "" : option.id })}
                        className={`${CARD_CLS} ${
                          active
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40"
                        }`}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium">目标人群</Label>
                <div className="flex flex-wrap gap-2">
                  {AUDIENCE_OPTIONS.map((option) => {
                    const active = brief.targetAudience.includes(option.id);
                    return (
                      <button
                        key={option.id}
                        type="button"
                        aria-pressed={active}
                        disabled={blocked}
                        onClick={() => toggleAudience(option.id)}
                        className={`${CHIP_CLS} ${
                          active
                            ? "bg-primary/15 text-primary border-primary/30"
                            : "bg-muted/20 text-muted-foreground border-border/50 hover:border-primary/30"
                        }`}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <Label className="text-sm font-medium">投放平台</Label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {PLATFORM_OPTIONS.map((option) => {
                    const active = brief.platforms.includes(option.id);
                    return (
                      <button
                        key={option.id}
                        type="button"
                        aria-pressed={active}
                        disabled={blocked}
                        onClick={() => togglePlatform(option.id)}
                        className={`${CARD_CLS} ${
                          active
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border/50 bg-muted/20 text-muted-foreground hover:border-primary/40"
                        }`}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm font-medium">用法与优势</Label>
                  <span className="text-xs text-muted-foreground">可选</span>
                </div>
                <Textarea
                  value={brief.usageAdvantage ?? ""}
                  onChange={(event) => patchBrief({ usageAdvantage: event.target.value })}
                  rows={3}
                  placeholder="例如：早上 3 分钟就能喝上，办公室常备"
                  disabled={blocked}
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>
      </section>

      <VisualControlPanel
        videoMode={videoMode}
        onVideoModeChange={handleVideoModeChange}
        templates={templates.map((template) => ({
          id: template.id,
          name: template.name,
          meta: template.category || template.styleType || undefined,
        }))}
        templateId={brief.templateId}
        onTemplateIdChange={handleTemplateIdChange}
        characters={characters.map((character) => ({
          id: character.id,
          name: character.name,
          description: character.description,
        }))}
        characterId={brief.characterId}
        onCharacterIdChange={(characterId) => patchBrief({ characterId })}
        constraints={constraints}
        onConstraintsChange={setConstraints}
        disabled={blocked}
      />

      <OutputSchemePanel
        value={brief.outputScheme}
        onChange={handleOutputSchemeChange}
        disabled={blocked}
      />

      <div className="pt-2">
        {showErrors && !validation.valid && (
          <div className="mb-4 p-3 rounded-lg bg-destructive/10 border border-destructive/20">
            <p className="text-sm text-destructive flex items-center gap-2">
              <LuCircleAlert className="w-4 h-4 shrink-0" />
              {Object.values(validation.errors).filter(Boolean).join("；")}
            </p>
          </div>
        )}
        <Button type="button" data-submit-brief onClick={handleSubmit} disabled={blocked} className="w-full h-12 brand-gradient text-white font-semibold text-base">
          <LuZap className="w-5 h-5 mr-2" />
          {submitLabel}
        </Button>
        <p className="text-xs text-muted-foreground text-center mt-3">
          {disabled ? "当前不可提交" : "提交后由入口页负责创建项目与生成脚本"}
        </p>
      </div>
    </div>
  );
}

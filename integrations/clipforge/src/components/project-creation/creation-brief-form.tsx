"use client";

import { useCallback, useState } from "react";
import { LuCircleAlert, LuZap } from "react-icons/lu";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { sanitizeCreationBrief } from "@/lib/creation-brief";
import { useCharacterStore } from "@/lib/stores/project-store";
import { useTemplateStore } from "@/lib/stores/template-store";
import { InputSourcePanel, type InputSourceImage } from "./input-source-panel";
import { NarrativePanel } from "./narrative-panel";
import { OutputStrategyPanel } from "./output-strategy-panel";
import { VisualControlPanel } from "./visual-control-panel";
import {
  AUDIENCE_OPTIONS,
  CATEGORY_OPTIONS,
  DEFAULT_VIDEO_MODE,
  DURATION_OPTIONS,
  MAX_SOURCE_IMAGES,
  PLATFORM_OPTIONS,
  PRICE_RANGE_OPTIONS,
  defaultAudioStrategyFor,
  resolveStyleSource,
  validateCreationBriefForm,
  type VideoModeId,
} from "./creation-brief-defaults";
import type { AudioStrategy, CreationBrief, InputMode, OutputStrategy } from "./creation-brief-types";

export interface CreationBriefFormProps {
  initial?: Partial<CreationBrief>;
  submitLabel: string;
  showAdvanced?: boolean;
  onSubmit: (brief: CreationBrief) => void;
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
export function CreationBriefForm({ initial, submitLabel, showAdvanced, onSubmit, disabled }: CreationBriefFormProps) {
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
  const [showErrors, setShowErrors] = useState(false);

  // every mutation runs through the shared sanitizer, so no field can drift out of contract
  const patchBrief = useCallback((partial: Partial<CreationBrief>) => {
    setBrief((prev) => sanitizeCreationBrief({ ...prev, ...partial }));
  }, []);

  const handleInputModeChange = useCallback((inputMode: InputMode) => patchBrief({ inputMode }), [patchBrief]);

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

  const handleOutputStrategyChange = useCallback(
    (outputStrategy: OutputStrategy) => patchBrief({ outputStrategy, audioStrategy: defaultAudioStrategyFor(outputStrategy) }),
    [patchBrief]
  );

  const handleAudioStrategyChange = useCallback((audioStrategy: AudioStrategy) => patchBrief({ audioStrategy }), [patchBrief]);

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

  const validation = validateCreationBriefForm({ productName, images, topic, inputMode: brief.inputMode });
  const blocked = disabled === true;

  const handleSubmit = () => {
    if (!validation.valid || blocked) {
      setShowErrors(true);
      return;
    }
    onSubmit(sanitizeCreationBrief(brief));
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
        disabled={blocked}
      />

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
        disabled={blocked}
      />

      <OutputStrategyPanel
        outputStrategy={brief.outputStrategy}
        onOutputStrategyChange={handleOutputStrategyChange}
        audioStrategy={brief.audioStrategy}
        onAudioStrategyChange={handleAudioStrategyChange}
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
        <Button type="button" onClick={handleSubmit} disabled={blocked} className="w-full h-12 brand-gradient text-white font-semibold text-base">
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

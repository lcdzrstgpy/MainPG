"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { CreativeIntent } from "@/lib/production-system";
import { VIDEO_MODE_OPTIONS, type VideoModeId } from "./creation-brief-defaults";
import { optionCardClass, SelectionCheck } from "./option-state-styles";

/** Minimal description of a pickable script template — the store object is mapped by the caller. */
export interface TemplateOption {
  id: string;
  name: string;
  meta?: string;
}

export interface CharacterOption {
  id: string;
  name: string;
  description?: string;
}

/**
 * Scene / visual constraints. Every field is named after the `CreativeIntent` field it feeds, so the
 * create flow can initialize the project's creative intent without renaming anything.
 */
export interface VisualConstraintValues {
  environment: string;
  action: string;
  camera: string;
  lighting: string;
  palette: string;
  productConstraints: string;
  negative: string;
}

export const EMPTY_VISUAL_CONSTRAINTS: VisualConstraintValues = {
  environment: "",
  action: "",
  camera: "",
  lighting: "",
  palette: "",
  productConstraints: "",
  negative: "",
};

export interface VisualConstraintField {
  id: keyof VisualConstraintValues;
  intentField: keyof CreativeIntent;
  label: string;
  placeholder: string;
}

export const VISUAL_CONSTRAINT_FIELDS: ReadonlyArray<VisualConstraintField> = [
  { id: "environment", intentField: "environment", label: "场景", placeholder: "例如：深夜书桌、通勤地铁" },
  { id: "action", intentField: "action", label: "动作", placeholder: "例如：双手拆开包装并倒水" },
  { id: "camera", intentField: "camera", label: "镜头偏好", placeholder: "例如：手持特写 + 快速推近" },
  { id: "lighting", intentField: "lighting", label: "灯光", placeholder: "例如：暖色柔光" },
  { id: "palette", intentField: "palette", label: "色调", placeholder: "例如：奶油白 + 暖橙" },
  { id: "productConstraints", intentField: "productConstraints", label: "商品约束", placeholder: "用顿号或换行分隔，例如：包装盒不得变形、Logo 必须清晰" },
  { id: "negative", intentField: "negative", label: "画面禁忌", placeholder: "用顿号或换行分隔，例如：无人物入镜、禁用英文" },
];

const LIST_SEPARATOR = /[、,，;；\n]+/;

function splitList(value: string): string[] {
  return value
    .split(LIST_SEPARATOR)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** Compiles the constraint draft into `CreativeIntent` fields, dropping everything left empty. */
export function buildCreativeIntentFields(values: VisualConstraintValues): Partial<CreativeIntent> {
  const fields: Partial<CreativeIntent> = {};
  const environment = values.environment.trim();
  if (environment) fields.environment = environment;
  const action = values.action.trim();
  if (action) fields.action = action;
  const camera = values.camera.trim();
  if (camera) fields.camera = camera;
  const lighting = values.lighting.trim();
  if (lighting) fields.lighting = lighting;
  const palette = values.palette.trim();
  if (palette) fields.palette = palette;
  const productConstraints = splitList(values.productConstraints);
  if (productConstraints.length) fields.productConstraints = productConstraints;
  const negative = splitList(values.negative);
  if (negative.length) fields.negative = negative;
  return fields;
}

export interface VisualControlPanelProps {
  videoMode: VideoModeId;
  onVideoModeChange: (mode: VideoModeId) => void;
  templates?: ReadonlyArray<TemplateOption>;
  templateId?: string;
  onTemplateIdChange: (templateId?: string) => void;
  characters?: ReadonlyArray<CharacterOption>;
  characterId?: string;
  onCharacterIdChange: (characterId?: string) => void;
  /** Supplied by the advanced entry points; the plain create form only collects brief-backed fields. */
  constraints?: VisualConstraintValues;
  onConstraintsChange?: (next: VisualConstraintValues) => void;
  disabled?: boolean;
}

export function VisualControlPanel({
  videoMode,
  onVideoModeChange,
  templates,
  templateId,
  onTemplateIdChange,
  characters,
  characterId,
  onCharacterIdChange,
  constraints,
  onConstraintsChange,
  disabled,
}: VisualControlPanelProps) {
  const presenterMode = videoMode === "live_presenter";
  const editingConstraints = constraints !== undefined && onConstraintsChange !== undefined;

  return (
    <Card className="glass-card">
      <CardContent className="p-5 space-y-5">
        <div>
          <Label className="text-sm font-medium mb-3 block">画面形态</Label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" role="radiogroup" aria-label="画面形态">
            {VIDEO_MODE_OPTIONS.map((option) => {
              const active = option.id === videoMode;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-video-mode={option.id}
                  disabled={disabled}
                  onClick={() => onVideoModeChange(option.id)}
                  className={`flex flex-col items-start rounded-lg p-3.5 text-left ${optionCardClass(active)}`}
                >
                  {active && <SelectionCheck className="absolute right-3 top-3" />}
                  <span className="text-sm font-semibold">{option.label}</span>
                  <span className={`mt-0.5 text-xs ${active ? "text-primary-foreground/80" : "text-muted-foreground"}`}>{option.description}</span>
                </button>
              );
            })}
          </div>
        </div>

        {templates && templates.length > 0 && (
          <div className="pt-4 border-t border-border/40">
            <div className="flex items-center justify-between mb-3">
              <Label className="text-sm font-medium">脚本模板</Label>
              <span className="text-xs text-muted-foreground">可选</span>
            </div>
            <div className="flex gap-3 overflow-x-auto pb-2">
              <button
                type="button"
                aria-pressed={!templateId}
                disabled={disabled}
                onClick={() => onTemplateIdChange(undefined)}
                className={`shrink-0 flex min-w-[140px] flex-col items-start rounded-lg p-3 text-left ${optionCardClass(!templateId)}`}
              >
                {!templateId && <SelectionCheck className="absolute right-2 top-2" />}
                <span className="pr-5 text-sm font-semibold">不使用模板</span>
                <span className={`mt-0.5 text-[11px] ${!templateId ? "text-primary-foreground/80" : "text-muted-foreground"}`}>完全由风格与创作要求决定</span>
              </button>
              {templates.map((option) => {
                const active = option.id === templateId;
                return (
                  <button
                    key={option.id}
                    type="button"
                    aria-pressed={active}
                    disabled={disabled}
                    onClick={() => onTemplateIdChange(option.id)}
                    className={`shrink-0 flex min-w-[140px] flex-col items-start rounded-lg p-3 text-left ${optionCardClass(active)}`}
                  >
                    {active && <SelectionCheck className="absolute right-2 top-2" />}
                    <span className="max-w-[160px] truncate pr-5 text-sm font-semibold">
                      {option.name}
                    </span>
                    {option.meta && <span className={`mt-0.5 text-[11px] ${active ? "text-primary-foreground/80" : "text-muted-foreground"}`}>{option.meta}</span>}
                  </button>
                );
              })}
            </div>
            <p className="text-[11px] text-muted-foreground">套用模板会同时改写脚本风格，风格来源会标记为「来自模板」。</p>
          </div>
        )}

        {presenterMode && characters && characters.length > 0 && (
          <div className="pt-4 border-t border-border/40">
            <div className="flex items-center justify-between mb-3">
              <Label className="text-sm font-medium">出镜角色</Label>
              <span className="text-xs text-muted-foreground">可选</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3" role="radiogroup" aria-label="出镜角色">
              <button
                type="button"
                role="radio"
                aria-checked={!characterId}
                disabled={disabled}
                onClick={() => onCharacterIdChange(undefined)}
                className={`flex flex-col items-start rounded-lg p-3 text-left ${optionCardClass(!characterId)}`}
              >
                {!characterId && <SelectionCheck className="absolute right-2 top-2" />}
                <span className="text-sm font-medium">不出镜</span>
                <span className={`text-[11px] ${!characterId ? "text-primary-foreground/80" : "text-muted-foreground"}`}>用商品与场景画面完成讲解</span>
              </button>
              {characters.map((option) => {
                const active = option.id === characterId;
                return (
                  <button
                    key={option.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={disabled}
                    onClick={() => onCharacterIdChange(option.id)}
                    className={`flex flex-col items-start rounded-lg p-3 text-left ${optionCardClass(active)}`}
                  >
                    {active && <SelectionCheck className="absolute right-2 top-2" />}
                    <span className="text-sm font-medium truncate max-w-[140px]">{option.name}</span>
                    {option.description && <span className={`max-w-[140px] truncate text-[11px] ${active ? "text-primary-foreground/80" : "text-muted-foreground"}`}>{option.description}</span>}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {editingConstraints && (
          <div className="pt-4 border-t border-border/40 space-y-3">
            <div>
              <Label className="text-sm font-medium">场景与画面约束</Label>
              <p className="text-xs text-muted-foreground mt-1">这些字段会写入项目的创作意图（CreativeIntent），逐镜生成时复用。</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {VISUAL_CONSTRAINT_FIELDS.map((field) => (
                <label key={field.id} className="block">
                  <span className="text-xs text-muted-foreground">{field.label}</span>
                  <Textarea
                    value={constraints[field.id]}
                    onChange={(event) => onConstraintsChange({ ...constraints, [field.id]: event.target.value })}
                    rows={2}
                    placeholder={field.placeholder}
                    disabled={disabled}
                    className="mt-1"
                  />
                </label>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { LANGUAGE_OPTIONS, SCRIPT_STYLE_OPTIONS, STYLE_SOURCE_LABELS, TONE_OPTIONS, type DescribedBriefOption } from "./creation-brief-defaults";
import type { CreationBrief, StyleSource } from "./creation-brief-types";

export interface NarrativePanelProps {
  styleType: string;
  onStyleTypeChange: (styleType: string) => void;
  styleSource: StyleSource;
  narrative: CreationBrief["narrative"];
  onNarrativeChange: (narrative: CreationBrief["narrative"]) => void;
  /**
   * 风格选项集按输入来源切换：缺省仍是带货脚本风格，一句话主题链路传自己的旁白风格词表，
   * 否则用户点到的风格不在引擎白名单里会被静默降级。
   */
  styleOptions?: ReadonlyArray<DescribedBriefOption<string>>;
  /** 风格区块标题，缺省沿用「脚本风格」。 */
  styleLabel?: string;
  disabled?: boolean;
}

export function NarrativePanel({
  styleType,
  onStyleTypeChange,
  styleSource,
  narrative,
  onNarrativeChange,
  styleOptions = SCRIPT_STYLE_OPTIONS,
  styleLabel = "脚本风格",
  disabled,
}: NarrativePanelProps) {
  const current = narrative ?? {};
  const patch = (partial: NonNullable<CreationBrief["narrative"]>) => onNarrativeChange({ ...current, ...partial });

  return (
    <Card className="glass-card">
      <CardContent className="p-5 space-y-5">
        <div>
          <div className="flex items-center justify-between mb-3">
            <Label className="text-sm font-medium">{styleLabel}</Label>
            {/* the source is always visible: a recommended or template-inherited style is never implied to be hand-picked */}
            <span className="text-xs text-muted-foreground">风格来源：{STYLE_SOURCE_LABELS[styleSource]}</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3" role="radiogroup" aria-label={styleLabel}>
            {styleOptions.map((option) => {
              const active = option.id === styleType;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-style={option.id}
                  disabled={disabled}
                  onClick={() => onStyleTypeChange(option.id)}
                  className={`relative flex flex-col items-start p-3.5 rounded-lg border text-left transition-all disabled:opacity-40 ${
                    active ? "border-primary bg-primary/10" : "border-border/50 bg-muted/20 hover:border-primary/40"
                  }`}
                >
                  <span className={`text-sm font-medium ${active ? "text-primary" : "text-foreground"}`}>{option.label}</span>
                  <span className="text-xs text-muted-foreground mt-0.5">{option.description}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="space-y-2 pt-4 border-t border-border/40">
          <Label className="text-sm font-medium">人物与处境</Label>
          <Textarea
            value={narrative?.situation ?? ""}
            onChange={(event) => patch({ situation: event.target.value })}
            rows={2}
            placeholder="谁在什么处境里看到这个问题，例如：加班到深夜的上班族，回到家只想快点喝上热的"
            disabled={disabled}
          />
          <p className="text-xs text-muted-foreground">写清人物和处境，脚本会以此为叙事起点。</p>
        </div>

        <div className="space-y-2">
          <Label className="text-sm font-medium">语言</Label>
          <div className="flex flex-wrap gap-2">
            {LANGUAGE_OPTIONS.map((option) => {
              const active = narrative?.language === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={active}
                  data-language={option.id}
                  disabled={disabled}
                  onClick={() => patch({ language: active ? undefined : option.id })}
                  className={`px-3 py-1.5 rounded-full border text-xs font-medium transition-all disabled:opacity-40 ${
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
          <Label className="text-sm font-medium">语气</Label>
          <div className="flex flex-wrap gap-2">
            {TONE_OPTIONS.map((option) => {
              const active = narrative?.tone === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  aria-pressed={active}
                  data-tone={option.id}
                  disabled={disabled}
                  onClick={() => patch({ tone: active ? undefined : option.id })}
                  className={`px-3 py-1.5 rounded-full border text-xs font-medium transition-all disabled:opacity-40 ${
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
      </CardContent>
    </Card>
  );
}

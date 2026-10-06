"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { AUDIO_STRATEGY_OPTIONS, OUTPUT_STRATEGY_OPTIONS } from "./creation-brief-defaults";
import type { AudioStrategy, OutputStrategy } from "./creation-brief-types";
import { optionCardClass, optionChipClass, SelectionCheck } from "./option-state-styles";

export interface OutputStrategyPanelProps {
  outputStrategy: OutputStrategy;
  onOutputStrategyChange: (strategy: OutputStrategy) => void;
  audioStrategy: AudioStrategy;
  onAudioStrategyChange: (strategy: AudioStrategy) => void;
  disabled?: boolean;
}

export function OutputStrategyPanel({
  outputStrategy,
  onOutputStrategyChange,
  audioStrategy,
  onAudioStrategyChange,
  disabled,
}: OutputStrategyPanelProps) {
  return (
    <Card className="glass-card">
      <CardContent className="p-5 space-y-5">
        <div>
          <div className="flex items-center justify-between mb-3">
            <Label className="text-sm font-medium">
              出片策略
              <span className="text-destructive ml-0.5">*</span>
            </Label>
            <span className="text-xs text-muted-foreground">选择即决定后续阶段、素材类型与计费</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3" role="radiogroup" aria-label="出片策略">
            {OUTPUT_STRATEGY_OPTIONS.map((option) => {
              const active = option.id === outputStrategy;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-strategy={option.id}
                  disabled={disabled}
                  onClick={() => onOutputStrategyChange(option.id)}
                  className={`flex flex-col items-start rounded-lg p-3.5 pr-10 text-left ${optionCardClass(active)}`}
                >
                  {active && <SelectionCheck className="absolute right-3 top-3" />}
                  <span className="text-sm font-semibold">{option.label}</span>
                  <span className={`mt-1 text-xs ${active ? "text-primary-foreground/90" : "text-muted-foreground"}`}>
                    {option.description}
                  </span>
                  <span className={`mt-2.5 space-y-0.5 text-[11px] ${active ? "text-primary-foreground/75" : "text-muted-foreground"}`}>
                    <span className="block">允许素材：{option.allowedMedia}</span>
                    <span className="block">默认音频来源：{option.defaultAudioSource}</span>
                    <span className="block">主成片形态：{option.mainOutput}</span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="pt-4 border-t border-border/40">
          <div className="flex items-center justify-between mb-3">
            <Label className="text-sm font-medium">音频策略</Label>
            <span className="text-xs text-muted-foreground">失败时不会被静默跳过</span>
          </div>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="音频策略">
            {AUDIO_STRATEGY_OPTIONS.map((option) => {
              const active = option.id === audioStrategy;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  data-audio-strategy={option.id}
                  disabled={disabled}
                  onClick={() => onAudioStrategyChange(option.id)}
                  className={optionChipClass(active)}
                >
                  {active && <SelectionCheck />}
                  {option.label}
                </button>
              );
            })}
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">
            {AUDIO_STRATEGY_OPTIONS.find((option) => option.id === audioStrategy)?.description ?? ""}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

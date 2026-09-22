"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { AUDIO_STRATEGY_OPTIONS, OUTPUT_STRATEGY_OPTIONS } from "./creation-brief-defaults";
import type { AudioStrategy, OutputStrategy } from "./creation-brief-types";

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
                  className={`flex flex-col items-start p-3.5 rounded-lg border text-left transition-all disabled:opacity-40 ${
                    active ? "border-primary bg-primary/10" : "border-border/50 bg-muted/20 hover:border-primary/40"
                  }`}
                >
                  <span className={`text-sm font-medium ${active ? "text-primary" : "text-foreground"}`}>{option.label}</span>
                  <span className="text-xs text-muted-foreground mt-1">{option.description}</span>
                  <span className="mt-2.5 space-y-0.5 text-[11px] text-muted-foreground">
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
          <p className="text-[11px] text-muted-foreground mt-2">
            {AUDIO_STRATEGY_OPTIONS.find((option) => option.id === audioStrategy)?.description ?? ""}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

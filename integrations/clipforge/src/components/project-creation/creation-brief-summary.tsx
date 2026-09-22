"use client";

import { Card, CardContent } from "@/components/ui/card";
import { sanitizeCreationBrief } from "@/lib/creation-brief";
import {
  PLATFORM_OPTIONS,
  STYLE_SOURCE_LABELS,
  audioStrategyLabel,
  durationLabel,
  inputModeLabel,
  outputStrategyLabel,
  scriptStyleLabel,
} from "./creation-brief-defaults";
import type { CreationBrief } from "./creation-brief-types";

export interface CreationBriefSummaryProps {
  brief: CreationBrief;
  title?: string;
  className?: string;
}

const UNSET = "未设置";

/**
 * Read-only projection of the creation brief, shared by the script / assets / video / export pages.
 * It normalizes whatever it is handed, so a legacy project (whose stored brief is null or partial)
 * still renders a readable summary instead of empty cells.
 */
export function CreationBriefSummary({ brief, title = "创作简报", className }: CreationBriefSummaryProps) {
  const safe = sanitizeCreationBrief(brief);
  const narrative = safe.narrative ?? {};
  const platforms = safe.platforms.map((id) => PLATFORM_OPTIONS.find((option) => option.id === id)?.label ?? id);
  const rows: Array<{ key: string; label: string; value: string }> = [
    { key: "input", label: "创作来源", value: inputModeLabel(safe.inputMode) },
    {
      key: "style",
      label: "脚本风格",
      value: safe.styleType
        ? `${scriptStyleLabel(safe.styleType)}（${STYLE_SOURCE_LABELS[safe.styleSource]}）`
        : `未选风格（${STYLE_SOURCE_LABELS[safe.styleSource]}）`,
    },
    { key: "duration", label: "目标时长", value: durationLabel(safe.targetDuration) },
    { key: "audience", label: "目标人群", value: safe.targetAudience.join("、") || UNSET },
    { key: "platforms", label: "投放平台", value: platforms.join("、") || UNSET },
    { key: "situation", label: "人物与处境", value: narrative.situation ?? UNSET },
    { key: "voice", label: "语言与语气", value: `${narrative.language ?? UNSET} · ${narrative.tone ?? UNSET}` },
    { key: "strategy", label: "出片策略", value: outputStrategyLabel(safe.outputStrategy) },
    { key: "audio", label: "音频策略", value: audioStrategyLabel(safe.audioStrategy) },
  ];
  // optional rows only appear when they carry a value — no placeholder noise
  if (safe.priceRange) rows.push({ key: "price", label: "价格定位", value: safe.priceRange });
  if (safe.usageAdvantage) rows.push({ key: "usage", label: "用法与优势", value: safe.usageAdvantage });
  if (safe.templateId) rows.push({ key: "template", label: "脚本模板", value: safe.templateId });
  if (safe.characterId) rows.push({ key: "character", label: "出镜角色", value: safe.characterId });

  return (
    <Card className={className ? `glass-card ${className}` : "glass-card"}>
      <CardContent className="p-5">
        <p className="text-sm font-semibold mb-3">{title}</p>
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
          {rows.map((row) => (
            <div key={row.key} className="min-w-0">
              <dt className="text-xs text-muted-foreground">{row.label}</dt>
              <dd className="text-sm text-foreground mt-0.5 break-words">{row.value}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

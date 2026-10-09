"use client";

import { LuSparkles } from "react-icons/lu";
import { Card, CardContent } from "@/components/ui/card";
import { scriptStyleLabel } from "./creation-brief-defaults";
import type { ScriptStyleRequirement } from "./script-style-requirement";

export interface StyleChoicePromptProps {
  requirement: ScriptStyleRequirement;
  /** 用户点选后由入口页带着该风格重试同一个脚本请求 */
  onPick: (styleType: string) => void;
  busy?: boolean;
  className?: string;
}

/**
 * 「需要用户显式选风格」时的公共提示：候选直接来自接口返回的 candidates（无数据兜底为完整可选风格表）。
 * 这不是错误提示——是继续生成所缺的那一步，所以不渲染成失败态。
 */
export function StyleChoicePrompt({ requirement, onPick, busy, className }: StyleChoicePromptProps) {
  const noData = requirement.reason === "no-data";
  return (
    <Card className={className ? `glass-card ${className}` : "glass-card"}>
      <CardContent className="p-4 space-y-3">
        <p className="text-sm font-medium flex items-center gap-2">
          <LuSparkles className="w-4 h-4 shrink-0 text-primary" />
          {noData ? "暂无足够数据推荐，请选择一个风格" : "请先选择一个脚本风格"}
        </p>
        <p className="text-xs text-muted-foreground">
          {noData
            ? "历史投放数据还不够，系统不会替你猜一个风格；选定后立即用这个风格重新生成脚本。"
            : "脚本接口要求显式风格，选定后立即生成脚本。"}
        </p>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="脚本风格选择">
          {requirement.candidates.map((styleType) => (
            <button
              key={styleType}
              type="button"
              role="radio"
              aria-checked={false}
              data-style={styleType}
              disabled={busy}
              onClick={() => onPick(styleType)}
              className="px-3 py-1.5 rounded-full border text-xs font-medium transition-all disabled:opacity-40 bg-muted/20 text-muted-foreground border-border/50 hover:border-primary/40 hover:text-foreground"
            >
              {scriptStyleLabel(styleType)}
            </button>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import type { ScriptFlowPolicy } from "@/lib/script-flow-policy";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

interface SimpleModeActionsProps {
  /** 由 script-flow-policy 解析出的策略门控（界面模式不影响它）。 */
  policy: ScriptFlowPolicy;
  id: string;
  t: Translate;
  autoFinish: () => void;
  runAiFilm: () => void;
  isGenerating: boolean;
  autoFinishing: boolean;
  aiFilming: boolean;
  autoFinishStage: string;
  hasScript: boolean;
  onRegenerate: () => void;
  onGoPro: () => void;
}

/**
 * 小白模式的动作区：每个出片策略只留一个主操作，其余交给导演模式。
 * 只接收 flags/handlers，自身绝不发起任何 fetch/API 调用（策略与服务端行为不变）。
 */
export function SimpleModeActions({
  policy,
  id,
  t,
  autoFinish,
  runAiFilm,
  isGenerating,
  autoFinishing,
  aiFilming,
  autoFinishStage,
  hasScript,
  onRegenerate,
  onGoPro,
}: SimpleModeActionsProps) {
  const busy = autoFinishing || aiFilming || !hasScript;

  return (
    <div className="flex flex-col items-center gap-3">
      {policy.strategy === "draft" && policy.showDraftAction && (
        <>
          <Button size="lg" className="brand-gradient text-white w-full" disabled={busy} onClick={autoFinish}>
            {autoFinishing ? (autoFinishStage || t("autoFinish")) : `⚡ ${t("autoFinish")}`}
          </Button>
          <p className="text-center text-xs text-muted-foreground">{t("autoFinishHint")}</p>
        </>
      )}

      {policy.strategy === "controlled-motion" && policy.showControlledMotionAction && (
        <Link href={`/project/${id}/assets`} className="w-full">
          <Button size="lg" className="brand-gradient text-white w-full" disabled={autoFinishing || aiFilming}>
            进入素材页生成逐镜动态
          </Button>
        </Link>
      )}

      {policy.strategy === "native-film" && policy.showNativeFilmAction && (
        <>
          <Button size="lg" className="brand-gradient text-white w-full" disabled={busy} onClick={runAiFilm}>
            {t("aiFilmPreviewTitle")}
          </Button>
          <p className="text-center text-xs text-muted-foreground/80">{t("aiFilmCostNote")}</p>
        </>
      )}

      {policy.strategy === "legacy" && (policy.showDraftAction || policy.showNativeFilmAction) && (
        <>
          <div className="grid w-full gap-2 sm:grid-cols-2">
            {policy.showDraftAction && (
              <Button size="lg" className="brand-gradient text-white w-full" disabled={busy} onClick={autoFinish}>
                {autoFinishing ? (autoFinishStage || t("autoFinish")) : `⚡ ${t("autoFinish")}`}
              </Button>
            )}
            {policy.showNativeFilmAction && (
              <Button size="lg" variant="outline" className="w-full" disabled={busy} onClick={runAiFilm}>
                {`✨ ${t("aiFilmCta")}`}
              </Button>
            )}
          </div>
          <p className="text-center text-xs text-muted-foreground">{t("autoFinishHint")}</p>
          <p className="text-center text-xs text-muted-foreground">
            出片策略未记录（旧项目）：保留原有双入口与断点恢复行为
          </p>
          <p className="text-center text-xs text-muted-foreground/80">{t("aiFilmCostNote")}</p>
        </>
      )}

      {/* 简报读取失败/未读定（legacy 但全部入口关闭）：不给任何可执行动作，避免误触发 */}
      {policy.strategy === "legacy" && !policy.showDraftAction && !policy.showNativeFilmAction && (
        <div className="w-full rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-center">
          <p className="text-sm font-medium text-amber-500">{t("strategyUnknown")}</p>
          <p className="mt-1 text-xs text-muted-foreground">{t("strategyUnknownHint")}</p>
        </div>
      )}

      {/* quality reassurance: both paths run the judge panel automatically — Easy mode
          hides the operation, never the quality features */}
      <p className="text-center text-xs text-muted-foreground/80">⚖️ {t("autoJudgeNote")}</p>
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" className="text-xs" disabled={isGenerating} onClick={onRegenerate}>
          {t("regenerate")}
        </Button>
        <Button variant="ghost" size="sm" className="text-xs text-muted-foreground" onClick={onGoPro}>
          {t("simpleGoPro")}
        </Button>
      </div>
    </div>
  );
}
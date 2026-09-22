"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { LuSparkles, LuCircleAlert, LuWandSparkles } from "react-icons/lu";
import { useSettingsStore } from "@/lib/stores/settings-store";
import { useT } from "@/lib/i18n";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toPrefillParams } from "@/lib/creation-entry-prefill";

/**
 * 一句话主题入口（/project/topic）。
 *
 * 设计 §8 阶段 5：这个入口只负责把「一句话主题」预填进主入口 /start 的创作简报，不再自己创建
 * 项目、也不再调用 /api/topic/script 形成第二条创建链。脚本风格、目标时长与出片策略都在创作
 * 简报里由用户显式选择，因此本页不再重复提供这两项选择器。
 */

// topic inspiration examples (zero-barrier trial for beginners); copy resolved per locale; key order matches the render below
const exampleTopicKeys = ["exampleTopic1", "exampleTopic2", "exampleTopic3", "exampleTopic4", "exampleTopic5"];

export default function TopicProjectPage() {
  const t = useT("topic");
  const router = useRouter();
  const { llm } = useSettingsStore();
  const isLLMConfigured = llm.apiKey.length > 0;

  const [topic, setTopic] = useState("");
  const [error, setError] = useState<string | null>(null);

  const isValid = topic.trim().length >= 2;

  /** 只做交接：主题随 query 带进主入口，由那里创建项目与生成脚本。 */
  const handleContinue = () => {
    if (!isValid) return;
    if (!isLLMConfigured) {
      setError(t("errorNoLlm"));
      return;
    }
    setError(null);
    router.push(toPrefillParams({ kind: "topic", topic }).href);
  };

  return (
    <div className="min-h-screen grid-bg">
      <main className="mx-auto max-w-2xl px-6 py-10">
        {/* page title */}
        <div className="mb-8">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-violet-500/10 px-3 py-1 text-xs font-medium text-violet-500">
            <LuSparkles className="w-3.5 h-3.5" />
            {t("heroBadge")}
          </div>
          <h1 className="text-2xl font-bold tracking-tight mb-2">{t("heroTitle")}</h1>
          <p className="text-muted-foreground text-sm leading-relaxed">
            {t("heroSubtitle")}
          </p>
        </div>

        {/* LLM not configured guidance */}
        {!isLLMConfigured && (
          <Link href="/settings?tab=llm">
            <div className="mb-6 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3 cursor-pointer hover:bg-amber-500/15 transition-colors">
              <LuCircleAlert className="w-5 h-5 shrink-0 text-amber-600 mt-0.5" />
              <div>
                <h3 className="font-semibold text-amber-200 text-sm">{t("llmBannerTitle")}</h3>
                <p className="text-xs text-amber-300/80 mt-0.5">
                  {t("llmBannerDesc")}
                  <span className="underline ml-1">{t("llmBannerCta")}</span>
                </p>
              </div>
            </div>
          </Link>
        )}

        <Card className="glass-card">
          <CardContent className="p-6 space-y-6">
            {/* topic input */}
            <div className="space-y-2">
              <Label htmlFor="topic" className="text-sm font-medium">
                {t("topicLabel")} <span className="text-destructive">*</span>
              </Label>
              <Textarea
                id="topic"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder={t("topicPlaceholder")}
                rows={3}
                className="resize-none"
              />
              {/* inspiration examples */}
              <div className="flex flex-wrap gap-1.5 pt-1">
                <span className="text-xs text-muted-foreground self-center">{t("tryLabel")}</span>
                {exampleTopicKeys.map((key) => {
                  const text = t(key);
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setTopic(text)}
                      className="rounded-full border border-border/60 bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground transition-colors"
                    >
                      {text}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* handoff note: style / duration / output strategy now live in the creation brief */}
            <p className="rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs text-muted-foreground leading-relaxed">
              {t("entryNote")}
            </p>

            {/* error message */}
            {error && (
              <div className="flex items-start gap-2 rounded-lg bg-destructive/10 border border-destructive/20 p-3 text-sm text-destructive">
                <LuCircleAlert className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            {/* continue to the single creation entry */}
            <Button
              onClick={handleContinue}
              disabled={!isValid}
              className="w-full brand-gradient text-white"
              size="lg"
            >
              <LuWandSparkles className="w-4 h-4" />
              <span className="ml-1.5">{t("ctaContinue")}</span>
            </Button>

            {/* workflow hints */}
            <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground pt-1">
              <Badge variant="secondary" className="text-[10px]">{t("flowStep1")}</Badge>
              <span className="text-border">→</span>
              <Badge variant="secondary" className="text-[10px]">{t("flowStep2")}</Badge>
              <span className="text-border">→</span>
              <Badge variant="secondary" className="text-[10px]">{t("flowStep3")}</Badge>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}

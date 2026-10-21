/**
 * 复制自 integrations/clipforge/src/lib/script-engine/hook-variants.ts。
 * 只读同步：逻辑以 ClipForge 上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 */

/**
 * Hook A/B variants: keep all subsequent shots of a base script fixed, rewrite only
 * shot 1 (the hook) using different patterns to produce N "same-body, different-opening"
 * comparable variants; each is tagged with hookId for post-campaign winner selection.
 *
 * Pure functions, zero LLM / zero API key.
 */
import type { Shot } from "../api/types";
import { selectHookPatterns, type HookPattern } from "./hook-patterns";
import type { ProductCategory } from "./script-categories";

export interface ScriptLike {
  title?: string;
  styleType?: string;
  totalDuration?: number;
  shots: Shot[];
}

export interface HookVariant {
  /** id of the hook pattern used (= HookPattern.id); performance data is aggregated by this key */
  hookId: string;
  hookName: string;
  script: ScriptLike;
}

/** Rewrites shot 1 using the given pattern: replaces voiceover and prepends the pattern's "stop" method. */
function rewriteHookShot(shot: Shot, p: HookPattern): Shot {
  return {
    ...shot,
    type: "hook",
    voiceover: p.example,
    description: shot.description ? `${p.stop}（沿用原画面意图：${shot.description}）` : p.stop,
  };
}

/**
 * Generates N hook variants from a base script (only shot 1 is changed; shots 2..N are kept as-is).
 */
export function buildHookVariants(
  base: ScriptLike,
  category: ProductCategory,
  n = 3,
  patterns?: HookPattern[],
): HookVariant[] {
  if (!base.shots.length) return [];
  const picks = (patterns ?? selectHookPatterns(category, n)).slice(0, n);
  return picks.map((p) => ({
    hookId: p.id,
    hookName: p.name,
    script: {
      ...base,
      shots: base.shots.map((s, i) => (i === 0 ? rewriteHookShot(s, p) : s)),
    },
  }));
}

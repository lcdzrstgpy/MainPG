import { STYLE_VALUES } from "@/lib/ad-templates";
import type { StyleSource } from "@/lib/creation-brief";

/**
 * The styles a creator can actually pick. Derived from the single ad-template whitelist so the two
 * never drift; "auto" is deliberately excluded because it means "let the system recommend", not a
 * concrete script style (see `resolveScriptStyle`).
 */
export const SCRIPT_STYLE_VALUES: readonly string[] = Object.freeze(
  [...STYLE_VALUES].filter((value) => value !== "auto")
);

/** Sample count below which historical conversion data is treated as too thin to recommend from. */
export const DEFAULT_MIN_SAMPLE_SIZE = 3;

/**
 * Controlled engine → UI style aliases (never widens the selectable whitelist, never fuzzy matches).
 *
 * The history read (`insights.topStyle`, i.e. `publish_metrics.style`) snapshots `scripts.style_type`,
 * which stores the *engine* vocabulary, while the creation whitelist (`STYLE_VALUES`) uses the
 * hyphenated UI vocabulary. This table rewrites only the engine keys whose spelling actually differs,
 * so real historical recommendations become usable again.
 *
 * Sources (verified against the real call sites):
 * - `pain_point` — engine key for pain-point: `ScriptStyleType` / `styleNameMap` in
 *   `src/lib/script-engine/prompts.ts:44-70`, the `scripts.style_type` enum in
 *   `src/lib/db/schema.ts:76-78`, and `publish_metrics.style` when the metrics route freezes the
 *   latest script's engine style (`src/app/api/project/[id]/metrics/route.ts:44-53`).
 * - `scene` — engine key for scenario, same engine sources; callers that already speak engine
 *   vocabulary send it too (`src/app/batch/page.tsx:66-78` maps UI `scenario` → engine `scene`).
 *
 * Deliberately absent:
 * - the other engine keys (drama/reversal/interview/story/unboxing/product_pov/comparison/
 *   talking_head) are spelled identically in both vocabularies, so they need no alias;
 * - `custom` is an engine-only fallback with no selectable UI counterpart, so it must keep falling
 *   through to "ask the creator" instead of being silently rewritten to a concrete style.
 */
export const SCRIPT_STYLE_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  // keys are lower-case; callers normalize before lookup
  pain_point: "pain-point",
  scene: "scenario",
});

export interface StyleResolutionInput {
  /** 调用方请求的风格；空串或 "auto" 视为「未显式选择」 */
  requestedStyle?: unknown;
  /** 请求来源：用户手选 / 模板覆盖 / 空（未知） */
  styleSource?: StyleSource | null;
  /** 历史转化洞察：仅当确实有足够样本时才有 topStyle */
  insights?: { topStyle?: string | null; sampleSize?: number | null } | null;
  /** 认为「样本足够、可用于推荐」的最小样本量，默认 3 */
  minSampleSize?: number;
}

export type StyleResolution =
  | { kind: "resolved"; styleType: string; styleSource: StyleSource }
  | { kind: "needs-explicit-choice"; reason: "no-data" | "unknown-style"; candidates: string[] };

const normalized = (value: unknown): string => (typeof value === "string" ? value.trim().toLowerCase() : "");

/**
 * Case-insensitive lookup of the canonical whitelist entry; returns undefined when not selectable.
 * A known engine key is normalized through `SCRIPT_STYLE_ALIASES` first (see that table), so history
 * written in engine vocabulary maps onto a selectable style instead of being rejected.
 */
const canonicalStyle = (value: string): string | undefined => {
  if (!value) return undefined;
  const direct = SCRIPT_STYLE_VALUES.find((style) => style.toLowerCase() === value);
  if (direct) return direct;
  const aliased = SCRIPT_STYLE_ALIASES[value];
  return aliased ? SCRIPT_STYLE_VALUES.find((style) => style === aliased) : undefined;
};

const count = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);

/**
 * Decide which script style a request actually resolves to, without ever guessing a style the
 * creator did not choose. Pure: no DB, no network, never throws — hostile input falls into one of
 * the resolution branches instead of raising.
 *
 * - explicit (and selectable) `requestedStyle` → resolved, source `template` when a template
 *   supplied it, otherwise `explicit`; historical insights never override a real choice. A known
 *   engine alias (`pain_point`/`scene`) resolves to its UI counterpart.
 * - `auto` / empty → resolved only when there is a selectable `topStyle` backed by >= `minSampleSize`
 *   samples (`performance-recommendation`); otherwise the caller must ask the creator to choose.
 *   It must never silently fall back to pain-point.
 * - unknown `requestedStyle` → the caller must ask for a valid choice.
 */
export function resolveScriptStyle(input: StyleResolutionInput = {}): StyleResolution {
  const minSampleSize = typeof input.minSampleSize === "number" && Number.isFinite(input.minSampleSize)
    ? input.minSampleSize
    : DEFAULT_MIN_SAMPLE_SIZE;

  const requested = normalized(input.requestedStyle);
  const isAuto = requested === "" || requested === "auto";

  if (!isAuto) {
    const styleType = canonicalStyle(requested);
    if (styleType) {
      return {
        kind: "resolved",
        styleType,
        styleSource: input.styleSource === "template" ? "template" : "explicit",
      };
    }
    return { kind: "needs-explicit-choice", reason: "unknown-style", candidates: [...SCRIPT_STYLE_VALUES] };
  }

  const recommended = canonicalStyle(normalized(input.insights?.topStyle));
  if (recommended && count(input.insights?.sampleSize) >= minSampleSize) {
    return { kind: "resolved", styleType: recommended, styleSource: "performance-recommendation" };
  }
  return { kind: "needs-explicit-choice", reason: "no-data", candidates: [...SCRIPT_STYLE_VALUES] };
}

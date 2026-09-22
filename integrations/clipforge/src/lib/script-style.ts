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

/** Case-insensitive lookup of the canonical whitelist entry; returns undefined when not selectable. */
const canonicalStyle = (value: string): string | undefined =>
  value ? SCRIPT_STYLE_VALUES.find((style) => style.toLowerCase() === value) : undefined;

const count = (value: unknown): number => (typeof value === "number" && Number.isFinite(value) ? value : 0);

/**
 * Decide which script style a request actually resolves to, without ever guessing a style the
 * creator did not choose. Pure: no DB, no network, never throws — hostile input falls into one of
 * the resolution branches instead of raising.
 *
 * - explicit (and selectable) `requestedStyle` → resolved, source `template` when a template
 *   supplied it, otherwise `explicit`; historical insights never override a real choice.
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

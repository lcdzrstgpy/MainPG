/**
 * Component-layer view of the creation brief contract.
 *
 * The authoritative definition lives in `src/lib/creation-brief.ts`, because the database schema
 * and the API routes need it too. This module only re-exports it, so the component layer never
 * forks or redefines the contract (no inverted dependency on the library layer).
 */
export type {
  AudioStrategy,
  CreationBrief,
  InputMode,
  OutputStrategy,
  StyleSource,
} from "@/lib/creation-brief";

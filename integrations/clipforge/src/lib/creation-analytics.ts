import { getDb } from "@/lib/db";
import { projectEvents } from "@/lib/db/schema";

export type CreationEventKind =
  | "project_created"
  | "script_generated"
  | "strategy_selected"
  | "style_resolved"
  | "video_task_submitted"
  | "compose_finished";

/**
 * Append-only creation telemetry. Observability must never break the creation flow,
 * so a failed write is reported and swallowed instead of propagating to the caller.
 */
export function recordCreationEvent(input: { projectId: string; kind: CreationEventKind; payload?: Record<string, unknown> }): void {
  try {
    getDb().insert(projectEvents).values({
      projectId: input.projectId,
      kind: input.kind,
      payload: input.payload ?? {},
    }).run();
  } catch (error) {
    console.warn(`creation event not recorded (${input.kind}):`, error);
  }
}

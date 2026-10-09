// @vitest-environment node
import Database from "better-sqlite3";
import { NextRequest } from "next/server";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { eq } from "drizzle-orm";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import { DEFAULT_TRANSCRIPT_EDIT_PLAN, type TranscriptDocument } from "@/lib/transcript-editor";
import { createTranscriptEditProposal } from "@/lib/transcript-edit-protocol";
let db: ReturnType<typeof drizzle<typeof schema>>;
let sqlite: Database.Database;
vi.mock("@/lib/db", () => ({ getDb: () => db }));
vi.mock("@/lib/paths", () => ({ getOutputDir: () => "/private/tmp/clipforge-job-tests" }));
const { pending } = vi.hoisted(() => ({ pending: new Map<string, { resolve: () => void; signal: AbortSignal; transcript: TranscriptDocument }>() }));
vi.mock("@/lib/transcript-render", () => ({ renderTranscriptEdit: (input: { outputPath: string; signal: AbortSignal; transcript: TranscriptDocument }) => new Promise<void>((resolve, reject) => {
  pending.set(input.outputPath, { resolve, signal: input.signal, transcript: input.transcript });
  if (input.signal.aborted) reject(input.signal.reason);
  else input.signal.addEventListener("abort", () => reject(input.signal.reason), { once: true });
}) }));
vi.mock("@/lib/video-composer/frame-extract", () => ({ extractFirstFrame: async () => null }));
import { enqueueTranscriptEdits } from "@/lib/transcript-edit-jobs";
import { cancelTranscriptRender, publicTranscriptEdit, reconcileTranscriptRenders, retryTranscriptRender } from "@/lib/transcript-render-runner";
const transcript: TranscriptDocument = { version: 1, language: "en", text: "hello", duration: 5, model: "test", device: "wasm", createdAt: "test", words: [{ id: "a", text: "hello", start: 1, end: 3 }], segments: [], silenceRanges: [] };
function source() { return db.select().from(schema.mediaSources).get()!; }
function proposal(index = 0) { return createTranscriptEditProposal({ document: transcript, latestRevision: 0, fallbackOperationId: `operation-${index}`, value: { plan: DEFAULT_TRANSCRIPT_EDIT_PLAN, baseRevision: 0 } }); }
beforeEach(() => {
  pending.clear(); sqlite = new Database(":memory:"); sqlite.pragma("foreign_keys = ON"); db = drizzle(sqlite, { schema }); migrate(db, { migrationsFolder: "drizzle" });
  db.insert(schema.projects).values({ id: "project", name: "Test" }).run();
  db.insert(schema.mediaSources).values({ id: "source", projectId: "project", originalName: "test.mp4", filePath: "/tmp/test.mp4", mimeType: "video/mp4", sizeBytes: 100, duration: 5000, transcript, status: "ready" }).run();
});
afterEach(async () => { for (const edit of db.select().from(schema.mediaEdits).all()) cancelTranscriptRender(edit.id); await new Promise((resolve) => setTimeout(resolve, 10)); sqlite.close(); });
describe("durable transcript jobs", () => {
  it("inspect defaults to 500 words instead of treating an absent limit as zero", async () => {
    const words = Array.from({ length: 600 }, (_, index) => ({ id: `word-${index}`, text: "test", start: index / 150, end: (index + 1) / 150 }));
    db.update(schema.mediaSources).set({ transcript: { ...transcript, words } }).run();
    const { GET } = await import("@/app/api/project/[id]/media/[mediaId]/edit/route");
    const response = await GET(new NextRequest("http://localhost/api/project/project/media/source/edit"), { params: Promise.resolve({ id: "project", mediaId: "source" }) });
    const result = await response.json();
    expect(result.transcript.words).toHaveLength(500);
    expect(result.transcript).toMatchObject({ wordOffset: 0, wordLimit: 500, hasMore: true, totalWords: 600 });
  });
  it("reserves a batch atomically and refuses a second batch while any item is active", () => {
    const created = enqueueTranscriptEdits(source(), transcript, [{ proposal: proposal(), label: "One" }, { proposal: proposal(1), label: "Two" }], "batch-one");
    expect(created.map(({ edit }) => edit.revision)).toEqual([1, 2]);
    expect(created.every(({ edit }) => edit.status === "queued" && edit.transcriptSnapshot?.text === "hello")).toBe(true);
    expect(publicTranscriptEdit(created[0].edit)).not.toHaveProperty("transcriptSnapshot");
    expect(() => enqueueTranscriptEdits(source(), transcript, [{ proposal: { ...proposal(2), latestRevision: 2 } }])).toThrow("EDIT_BUSY");
  });
  it("rolls back every item if one plan is too short", () => {
    expect(() => enqueueTranscriptEdits(source(), transcript, [{ proposal: proposal() }, { proposal: { ...proposal(1), summary: { ...proposal(1).summary, outputDuration: 0.1 } } }])).toThrow("EDIT_TOO_SHORT");
    expect(db.select().from(schema.mediaEdits).all()).toEqual([]);
    expect(db.select().from(schema.compositions).all()).toEqual([]);
  });
  it("cancel then immediate retry protects the new attempt from the old completion", async () => {
    const [{ edit }] = enqueueTranscriptEdits(source(), transcript, [{ proposal: proposal() }]);
    const first = [...pending.values()][0];
    cancelTranscriptRender(edit.id);
    const retried = retryTranscriptRender(edit.id);
    expect(first.signal.aborted).toBe(true);
    expect(retried.attemptId).not.toBe(edit.attemptId);
    db.update(schema.mediaSources).set({ transcript: { ...transcript, text: "changed" } }).run();
    const second = [...pending.values()][1];
    expect(second.transcript.text).toBe("hello");
    first.resolve(); second.resolve();
    await vi.waitFor(() => expect(db.select().from(schema.mediaEdits).where(eq(schema.mediaEdits.id, edit.id)).get()?.status).toBe("done"));
    expect(db.select().from(schema.compositions).get()?.outputPath).toContain(retried.attemptId);
    expect(() => retryTranscriptRender(edit.id)).toThrow("EDIT_NOT_RETRYABLE");
  });
  it("recovers expired heartbeats while preserving fresh and other-project work", () => {
    const [{ edit }] = enqueueTranscriptEdits(source(), transcript, [{ proposal: proposal() }]);
    reconcileTranscriptRenders("project");
    expect(db.select().from(schema.mediaEdits).get()?.status).toBe("queued");
    db.update(schema.mediaEdits).set({ heartbeatAt: new Date(0) }).where(eq(schema.mediaEdits.id, edit.id)).run();
    reconcileTranscriptRenders("different");
    expect(db.select().from(schema.mediaEdits).get()?.status).toBe("queued");
    reconcileTranscriptRenders("project");
    expect(db.select().from(schema.mediaEdits).get()).toMatchObject({ status: "failed", error: "RENDER_INTERRUPTED" });
    expect(db.select().from(schema.compositions).get()?.status).toBe("failed");
  });
});

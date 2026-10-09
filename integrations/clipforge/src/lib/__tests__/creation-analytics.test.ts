// @vitest-environment node
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { join } from "path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

let db: ReturnType<typeof drizzle<typeof schema>>;
let sqlite: Database.Database;
const state = vi.hoisted(() => ({ fail: false }));
vi.mock("@/lib/db", () => ({
  getDb: () => {
    if (state.fail) throw new Error("database unavailable");
    return db;
  },
}));
import { recordCreationEvent } from "@/lib/creation-analytics";

beforeEach(() => {
  state.fail = false;
  sqlite = new Database(":memory:");
  sqlite.pragma("foreign_keys = ON");
  db = drizzle(sqlite, { schema });
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
});
afterEach(() => sqlite.close());

describe("creation event recording", () => {
  it("persists an event that can be read back", () => {
    recordCreationEvent({ projectId: "project-1", kind: "strategy_selected", payload: { outputStrategy: "draft" } });
    const rows = db.select().from(schema.projectEvents).all();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      projectId: "project-1",
      kind: "strategy_selected",
      payload: { outputStrategy: "draft" },
    });
    expect(rows[0].id).toBeTruthy();
    expect(rows[0].createdAt).toBeInstanceOf(Date);
  });

  it("stores an empty payload when the caller has no extra data", () => {
    recordCreationEvent({ projectId: "project-1", kind: "project_created" });
    expect(db.select().from(schema.projectEvents).all()[0]?.payload).toEqual({});
  });

  it("does not throw for an unrelated or empty project id", () => {
    expect(() => recordCreationEvent({ projectId: "missing-project", kind: "compose_finished" })).not.toThrow();
    expect(() => recordCreationEvent({ projectId: "", kind: "compose_finished" })).not.toThrow();
    expect(db.select().from(schema.projectEvents).all()).toHaveLength(2);
  });

  it("warns instead of throwing when the database is unavailable", () => {
    state.fail = true;
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(() => recordCreationEvent({ projectId: "project-1", kind: "video_task_submitted" })).not.toThrow();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

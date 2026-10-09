// @vitest-environment node
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { eq } from "drizzle-orm";
import { join } from "path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import * as schema from "@/lib/db/schema";
import { DEFAULT_CREATION_BRIEF, type CreationBrief } from "@/lib/creation-brief";

let db: ReturnType<typeof drizzle<typeof schema>>;
let sqlite: Database.Database;

beforeEach(() => {
  sqlite = new Database(":memory:");
  sqlite.pragma("foreign_keys = ON");
  db = drizzle(sqlite, { schema });
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
});
afterEach(() => sqlite.close());

describe("creation brief migration", () => {
  it("stores and reads back projects.creation_brief", () => {
    const brief: CreationBrief = {
      ...DEFAULT_CREATION_BRIEF,
      inputMode: "link",
      styleType: "pain_point",
      targetAudience: ["宝妈"],
      outputStrategy: "controlled-motion",
      narrative: { situation: "夏天出汗", tone: "共情" },
    };
    db.insert(schema.projects).values({ id: "with-brief", name: "With brief", creationBrief: brief }).run();
    expect(db.select().from(schema.projects).where(eq(schema.projects.id, "with-brief")).get()?.creationBrief).toEqual(brief);
  });

  it("leaves legacy projects with a null creation_brief", () => {
    db.insert(schema.projects).values({ id: "legacy", name: "Legacy" }).run();
    const row = db.select().from(schema.projects).where(eq(schema.projects.id, "legacy")).get();
    expect(row?.creationBrief).toBeNull();
    expect(row?.creativeIntent).toBeNull();
  });

  it("appends and reads back project events", () => {
    db.insert(schema.projectEvents)
      .values({ id: "event-1", projectId: "legacy", kind: "project_created", payload: { entry: "start" } })
      .run();
    const row = db.select().from(schema.projectEvents).where(eq(schema.projectEvents.id, "event-1")).get();
    expect(row).toMatchObject({ id: "event-1", projectId: "legacy", kind: "project_created", payload: { entry: "start" } });
    expect(row?.createdAt).toBeInstanceOf(Date);
  });
});

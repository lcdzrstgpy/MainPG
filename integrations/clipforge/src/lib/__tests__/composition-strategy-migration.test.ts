// @vitest-environment node
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { eq } from "drizzle-orm";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import * as schema from "@/lib/db/schema";

let db: ReturnType<typeof drizzle<typeof schema>>;
let sqlite: Database.Database;

beforeEach(() => {
  sqlite = new Database(":memory:");
  sqlite.pragma("foreign_keys = ON");
  db = drizzle(sqlite, { schema });
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
});
afterEach(() => sqlite.close());

const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

describe("compositions.strategy 迁移", () => {
  it("迁移后的表有 strategy 列（text），可写可读", () => {
    const columns = sqlite.prepare("PRAGMA table_info(compositions)").all() as Array<{ name: string; type: string }>;
    const strategy = columns.find((c) => c.name === "strategy");
    expect(strategy).toBeDefined();
    expect(strategy?.type.toLowerCase()).toBe("text");

    db.insert(schema.projects).values({ id: "p1", name: "P1" }).run();
    db.insert(schema.compositions)
      .values({ id: "c1", projectId: "p1", outputPath: "/tmp/a.mp4", strategy: "controlled-motion", status: "done" })
      .run();
    expect(db.select().from(schema.compositions).where(eq(schema.compositions.id, "c1")).get()?.strategy)
      .toBe("controlled-motion");
  });

  it("历史成片（未写 strategy）该列为 null", () => {
    db.insert(schema.projects).values({ id: "p2", name: "P2" }).run();
    db.insert(schema.compositions)
      .values({ id: "c2", projectId: "p2", outputPath: "/tmp/b.mp4", label: "免费草稿 · 静态合成", status: "done" })
      .run();
    const row = db.select().from(schema.compositions).where(eq(schema.compositions.id, "c2")).get();
    expect(row?.strategy).toBeNull();
    expect(row?.label).toBe("免费草稿 · 静态合成");
  });
});

describe("0021 迁移三件套齐备", () => {
  it("迁移 SQL 只给 compositions 增加可空 strategy 列", () => {
    const sql = read("drizzle/0021_add_composition_strategy.sql");
    expect(sql).toMatch(/ALTER TABLE `compositions` ADD `strategy` text;/);
  });

  it("journal 追加 idx=21，snapshot 的 prevId 指向 0020", () => {
    const journal = JSON.parse(read("drizzle/meta/_journal.json")) as {
      entries: Array<{ idx: number; tag: string }>;
    };
    const last = journal.entries[journal.entries.length - 1];
    expect(last).toMatchObject({ idx: 21, tag: "0021_add_composition_strategy" });

    const snapshot0020 = JSON.parse(read("drizzle/meta/0020_snapshot.json")) as { id: string };
    const snapshot0021 = JSON.parse(read("drizzle/meta/0021_snapshot.json")) as { prevId: string };
    expect(snapshot0021.prevId).toBe(snapshot0020.id);
  });

  it("snapshot 里 compositions 记录了 strategy 列", () => {
    const snapshot = JSON.parse(read("drizzle/meta/0021_snapshot.json")) as {
      tables: Record<string, { columns: Record<string, { name: string; type: string; notNull: boolean }> }>;
    };
    const column = snapshot.tables.compositions?.columns.strategy;
    expect(column).toMatchObject({ name: "strategy", type: "text", notNull: false });
  });
});

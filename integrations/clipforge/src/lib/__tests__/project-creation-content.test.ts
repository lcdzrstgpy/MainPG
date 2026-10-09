// @vitest-environment node
/**
 * Route-level coverage for the creation contract's content fields (C3):
 * POST /api/project must accept `characterId` (角色落库到项目), `contentType` ("product" | "topic",
 * 非法值忽略) and `topic` (只在 content-type 为 topic 的请求里写入)。
 *
 * Real in-memory SQLite + real migrations + mocked getDb, same setup as
 * project-creation-contract.test.ts.
 */
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { join } from "path";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

const state = vi.hoisted(() => ({ db: null as unknown }));
vi.mock("@/lib/db", () => ({ getDb: () => state.db }));

import { POST } from "@/app/api/project/route";
import { buildWorkflowPlanForStrategy } from "@/lib/creation-brief";

const sqlite = new Database(":memory:");
const db = drizzle(sqlite, { schema });
beforeAll(() => {
  state.db = db;
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
});
beforeEach(() => {
  db.delete(schema.projectEvents).run();
  db.delete(schema.projects).run();
});
afterAll(() => sqlite.close());

const storedProjects = () => db.select().from(schema.projects).all();

function post(body: unknown) {
  return POST(
    new NextRequest("http://localhost/api/project", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
  );
}

describe("POST /api/project 内容类型与角色落库", () => {
  it("带 characterId / contentType:'topic' / topic → 三项都写进项目行", async () => {
    const response = await post({
      name: "一句话主题项目",
      characterId: "char-001",
      contentType: "topic",
      topic: "在家如何泡一杯手冲咖啡",
    });
    expect(response.status).toBe(201);
    const row = await response.json();
    expect(row).toMatchObject({
      name: "一句话主题项目",
      characterId: "char-001",
      contentType: "topic",
      topic: "在家如何泡一杯手冲咖啡",
    });
    const [stored] = storedProjects();
    expect(stored).toMatchObject({ characterId: "char-001", contentType: "topic", topic: "在家如何泡一杯手冲咖啡" });
  });

  it("contentType 非法值被忽略（保持 product 语义），且 topic 不写入", async () => {
    const response = await post({ name: "非法内容类型", contentType: "hologram", topic: "海边日出" });
    expect(response.status).toBe(201);
    const row = await response.json();
    expect(row.contentType).toBe("product");
    expect(row.topic).toBeNull();
    expect(storedProjects()[0].topic).toBeNull();
  });

  it("contentType 为 product（未传或显式）时 topic 不写入，characterId 照常写入", async () => {
    const omitted = await (await post({ name: "带货项目", topic: "海边日出", characterId: "char-002" })).json();
    expect(omitted.contentType).toBe("product");
    expect(omitted.topic).toBeNull();
    expect(omitted.characterId).toBe("char-002");

    const explicit = await (await post({ name: "带货项目", contentType: "product", topic: "海边日出" })).json();
    expect(explicit.contentType).toBe("product");
    expect(explicit.topic).toBeNull();
  });

  it("空串 / 非字符串的新字段被视为未传（不写坏列）", async () => {
    const row = await (await post({ name: "空字段", characterId: "   ", topic: 42, contentType: 7 })).json();
    expect(row).toMatchObject({ characterId: null, contentType: "product", topic: null });
  });

  it("不传新字段 → 既有行为不变（null/product、无简报时工作流仍为 null、有简报时写入策略默认工作流）", async () => {
    const legacy = await (await post({ name: "旧调用" })).json();
    expect(legacy).toMatchObject({
      name: "旧调用",
      characterId: null,
      contentType: "product",
      topic: null,
      creationBrief: null,
      productionWorkflow: null,
    });

    const briefed = await (await post({ name: "简报项目", creationBrief: { outputStrategy: "controlled-motion" } })).json();
    expect(briefed.productionWorkflow).toEqual(buildWorkflowPlanForStrategy("controlled-motion"));
  });
});

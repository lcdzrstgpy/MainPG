// @vitest-environment node
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { join } from "path";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

// Both routes read the database through getDb(); point that at an in-memory SQLite database
// migrated with the real drizzle migrations (same pattern as task-feed-route.test.ts /
// platform-export-route.test.ts).
const state = vi.hoisted(() => ({ db: null as unknown }));
vi.mock("@/lib/db", () => ({ getDb: () => state.db }));

import { POST } from "@/app/api/project/route";
import { GET, PATCH } from "@/app/api/project/[id]/route";
import {
  assertStrategyWorkflowConsistency,
  buildWorkflowPlanForStrategy,
  sanitizeCreationBrief,
  type OutputStrategy,
} from "@/lib/creation-brief";

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
  return POST(new NextRequest("http://localhost/api/project", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
}
function context(id: string) {
  return { params: Promise.resolve({ id }) };
}
function patch(id: string, body: unknown) {
  return PATCH(new NextRequest(`http://localhost/api/project/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }), context(id));
}
function get(id: string) {
  return GET(new NextRequest(`http://localhost/api/project/${id}`), context(id));
}
/** The documented plan for the strategy, with one stage explicitly turned off. */
const withDisabledStage = (strategy: OutputStrategy, id: "motion" | "compose") =>
  buildWorkflowPlanForStrategy(strategy).map((stage) => (stage.id === id ? { ...stage, enabled: false } : stage));

describe("创建项目写入创作简报", () => {
  it("完整简报经归一化写入，响应体仍是新建项目的整行对象", async () => {
    const brief = {
      version: 1,
      inputMode: "clone",
      targetDuration: 15,
      styleType: "comparison",
      styleSource: "template",
      targetAudience: ["宝妈", " 宝妈 ", 42],
      platforms: ["douyin", "tiktok"],
      usageAdvantage: " 一杯顶三杯 ",
      narrative: { situation: "通勤路上", tone: "轻快" },
      outputStrategy: "native-film",
      audioStrategy: "native-audio",
      characterId: "character-1",
    };
    const response = await post({
      name: "原生整片项目",
      productName: "咖啡液",
      videoMode: "scene_demo",
      productionWorkflow: buildWorkflowPlanForStrategy("native-film"),
      creationBrief: brief,
    });
    expect(response.status).toBe(201);
    const row = await response.json();
    expect(row).toMatchObject({ name: "原生整片项目", productName: "咖啡液", videoMode: "scene_demo", status: "draft" });
    expect(row.creationBrief).toEqual(sanitizeCreationBrief(brief));
    expect(row.creationBrief.usageAdvantage).toBe("一杯顶三杯");
    expect(row.creationBrief.targetAudience).toEqual(["宝妈"]);
    const [stored] = storedProjects();
    expect(stored.id).toBe(row.id);
    expect(stored.creationBrief).toEqual(sanitizeCreationBrief(brief));
    expect(stored.productionWorkflow).toEqual(buildWorkflowPlanForStrategy("native-film"));
  });

  it("不传简报时仍成功创建，列保持 null，既有字段行为不变", async () => {
    const response = await post({ name: "旧调用", sourceType: "clone", sourceVideoUrl: "https://example.com/v.mp4" });
    expect(response.status).toBe(201);
    const row = await response.json();
    expect(row).toMatchObject({ name: "旧调用", sourceType: "clone", sourceVideoUrl: "https://example.com/v.mp4", creationBrief: null });
    // Without a brief the workflow column stays null too: legacy callers must not silently inherit
    // a `draft` plan (that would flip the assets page auto-motion toggle off for them).
    expect(row.productionWorkflow).toBeNull();
    expect(storedProjects()[0].creationBrief).toBeNull();
    expect((await (await post({ name: "" })).json()).name).toBe("未命名项目");
    expect((await (await post({ videoMode: "telepathy" })).json()).videoMode).toBe("product_closeup");
  });

  it("非法策略归一化为 native-film 而不是 500，并写入该策略的默认工作流", async () => {
    const response = await post({ name: "非法策略", creationBrief: { outputStrategy: "hologram", inputMode: "telepathy", targetDuration: 42 } });
    expect(response.status).toBe(201);
    const row = await response.json();
    expect(row.creationBrief).toMatchObject({ outputStrategy: "native-film", inputMode: "upload", targetDuration: 30 });
    expect(row.productionWorkflow).toEqual(buildWorkflowPlanForStrategy("native-film"));
  });

  it("有简报时忽略提交的冲突工作流，按归一化策略生成", async () => {
    const motion = await post({
      name: "冲突",
      creationBrief: { outputStrategy: "controlled-motion" },
      productionWorkflow: withDisabledStage("controlled-motion", "motion"),
    });
    expect(motion.status).toBe(201);
    expect((await motion.json()).productionWorkflow).toEqual(buildWorkflowPlanForStrategy("controlled-motion"));

    const compose = await post({
      name: "冲突",
      creationBrief: { outputStrategy: "native-film" },
      productionWorkflow: withDisabledStage("native-film", "compose"),
    });
    expect(compose.status).toBe(201);
    expect((await compose.json()).productionWorkflow).toEqual(buildWorkflowPlanForStrategy("native-film"));
    expect(storedProjects()).toHaveLength(2);
  });

  it("有简报时生成对应工作流，无简报时保留既有非法工作流处理", async () => {
    const consistent = await post({
      name: "动态项目",
      creationBrief: { outputStrategy: "controlled-motion" },
      productionWorkflow: buildWorkflowPlanForStrategy("controlled-motion"),
    });
    expect(consistent.status).toBe(201);
    expect((await consistent.json()).productionWorkflow).toEqual(buildWorkflowPlanForStrategy("controlled-motion"));

    const legacyWorkflow = buildWorkflowPlanForStrategy("draft");
    const legacy = await post({ name: "旧显式工作流", productionWorkflow: legacyWorkflow });
    expect(legacy.status).toBe(201);
    expect((await legacy.json()).productionWorkflow).toEqual(legacyWorkflow);

    const broken = await post({ name: "坏工作流", productionWorkflow: "not-an-array" });
    expect(broken.status).toBe(201);
    expect((await broken.json()).productionWorkflow).toBeNull();
  });

  it("创意意图与视觉圣经经服务端归一化后写入，未传则为 null", async () => {
    const response = await post({
      name: "生产约束",
      creativeIntent: { subject: " 咖啡杯 ", continuity: [" 白色桌面 ", "白色桌面"], negative: "nope" },
      visualBible: { characterAnchors: [" 短发女生 "], forbiddenChanges: [42] },
    });
    expect(response.status).toBe(201);
    const row = await response.json();
    expect(row.creativeIntent).toEqual({ subject: "咖啡杯", continuity: ["白色桌面"] });
    expect(row.visualBible).toEqual({
      characterAnchors: ["短发女生"],
      productAnchors: [],
      wardrobeAnchors: [],
      environmentAnchors: [],
      lightingAnchors: [],
      forbiddenChanges: [],
    });

    const plain = await (await post({ name: "无约束" })).json();
    expect(plain.creativeIntent).toBeNull();
    expect(plain.visualBible).toBeNull();
  });

  it("创建成功后记录一条 project_created 事件", async () => {
    const row = await (await post({
      name: "事件项目",
      creationBrief: { inputMode: "link", outputStrategy: "controlled-motion", styleType: "scene", styleSource: "template" },
    })).json();
    const events = db.select().from(schema.projectEvents).all();
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ projectId: row.id, kind: "project_created" });
    expect(events[0].payload).toMatchObject({ inputMode: "link", outputStrategy: "controlled-motion", styleType: "scene", styleSource: "template" });
  });
});

describe("PATCH 更新创作简报", () => {
  it("白名单允许 creationBrief，取值同样经过归一化", async () => {
    const created = await (await post({ name: "待更新" })).json();
    const updated = await patch(created.id, {
      creationBrief: { inputMode: "topic", targetDuration: 60, outputStrategy: "native-film", targetAudience: [" 学生党 "] },
    });
    expect(updated.status).toBe(200);
    expect((await updated.json()).creationBrief).toEqual(
      sanitizeCreationBrief({ inputMode: "topic", targetDuration: 60, outputStrategy: "native-film", targetAudience: ["学生党"] })
    );
    expect((await (await get(created.id)).json()).productionWorkflow).toEqual(buildWorkflowPlanForStrategy("native-film"));
    expect((await (await get(created.id)).json()).creationBrief.outputStrategy).toBe("native-film");

    const invalid = await patch(created.id, { creationBrief: { inputMode: "telepathy", outputStrategy: "hologram" } });
    expect(invalid.status).toBe(200);
    expect((await invalid.json()).creationBrief).toEqual(sanitizeCreationBrief({}));
    expect((await (await get(created.id)).json()).creationBrief.outputStrategy).toBe("native-film");
  });

  it("其余 sanitize 列仍只由 production 路由写入，既有字段照常更新", async () => {
    const created = await (await post({ name: "待更新" })).json();
    const rejected = await patch(created.id, {
      creativeIntent: { subject: "raw" },
      visualBible: { productAnchors: ["raw"] },
      productionWorkflow: buildWorkflowPlanForStrategy("draft"),
    });
    expect(rejected.status).toBe(400);
    expect((await (await get(created.id)).json()).creativeIntent).toBeNull();
    expect((await (await patch(created.id, { name: "改名" })).json()).name).toBe("改名");
  });
});

describe("策略与工作流一致性判定", () => {
  it("只拒绝明确关闭关键阶段的组合", () => {
    const motion = sanitizeCreationBrief({ outputStrategy: "controlled-motion" });
    expect(assertStrategyWorkflowConsistency(motion, buildWorkflowPlanForStrategy("controlled-motion"))).toBeNull();
    expect(assertStrategyWorkflowConsistency(motion, withDisabledStage("controlled-motion", "motion"))).toContain("motion");

    const film = sanitizeCreationBrief({ outputStrategy: "native-film" });
    expect(assertStrategyWorkflowConsistency(film, buildWorkflowPlanForStrategy("native-film"))).toBeNull();
    expect(assertStrategyWorkflowConsistency(film, withDisabledStage("native-film", "compose"))).toContain("compose");

    // draft is defined as the still-output plan: a disabled motion stage is expected, not a conflict
    const draft = sanitizeCreationBrief(undefined);
    expect(assertStrategyWorkflowConsistency(draft, buildWorkflowPlanForStrategy("draft"))).toBeNull();
    expect(assertStrategyWorkflowConsistency(draft, [])).toBeNull();
  });
});

describe("观测失败不阻断创建", () => {
  it("事件表不可用时仍然返回 201", async () => {
    sqlite.exec("ALTER TABLE project_events RENAME TO project_events_backup");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const response = await post({ name: "容错" });
      expect(response.status).toBe(201);
      expect(warn).toHaveBeenCalled();
    } finally {
      warn.mockRestore();
      sqlite.exec("ALTER TABLE project_events_backup RENAME TO project_events");
    }
  });
});

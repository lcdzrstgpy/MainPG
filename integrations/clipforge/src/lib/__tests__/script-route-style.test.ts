// @vitest-environment node
/**
 * Route-level coverage for Task 4.1: POST /api/llm/script must resolve the script style without ever
 * silently falling back to pain-point (design §5.3 / plan Task 4.1), and must read the project's
 * creation brief as a default source for style / audience / platforms (design §7.3).
 *
 * Real in-memory SQLite + real migrations + mocked getDb (same setup as task-feed-route.test.ts);
 * the LLM generator is mocked so no network call happens. The mock mirrors the real engine contract:
 * `parseScriptResponse` stamps the requested style onto the generated scripts.
 */
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { eq } from "drizzle-orm";
import { join } from "path";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import type { CreationBrief } from "@/lib/creation-brief";

const state = vi.hoisted(() => ({
  db: null as unknown,
  lastInput: null as { styleType?: string; targetAudience?: string; platforms?: string } | null,
}));

vi.mock("@/lib/db", () => ({ getDb: () => state.db }));
vi.mock("@/lib/script-engine/generator", () => ({
  analyzeProduct: vi.fn(async () => "商品分析"),
  generateScript: vi.fn(async (input: { styleType: string }) => {
    state.lastInput = input;
    return [
      {
        title: "测试脚本",
        styleType: input.styleType, // real parseScriptResponse falls back to the requested style
        totalDuration: 20,
        shots: [
          {
            shotId: 1,
            type: "hook",
            duration: 3,
            description: "开场",
            camera: "static",
            visualSource: "ai_generate",
            transition: "direct_concat",
            voiceover: "开场白",
          },
        ],
      },
    ];
  }),
}));

import { POST } from "@/app/api/llm/script/route";

const sqlite = new Database(":memory:");
const db = drizzle(sqlite, { schema });
beforeAll(() => {
  state.db = db;
  migrate(db, { migrationsFolder: join(process.cwd(), "drizzle") });
});
beforeEach(() => {
  db.delete(schema.scripts).run();
  db.delete(schema.publishMetrics).run();
  db.delete(schema.projects).run();
  state.lastInput = null;
  db.insert(schema.projects).values({ id: "project-1", name: "测试项目" }).run();
});
afterAll(() => sqlite.close());

const LLM_CONFIG = { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" };

const post = (body: Record<string, unknown>) =>
  POST(
    new NextRequest("http://localhost/api/llm/script", {
      method: "POST",
      body: JSON.stringify({ projectId: "project-1", productName: "桂花乌龙茶", llmConfig: LLM_CONFIG, ...body }),
    })
  );

const scriptStyles = () => db.select().from(schema.scripts).all().map((row) => row.styleType);

/** Enough samples of one engine style for it to win the conversion ranking. */
const seedHistory = (style: string, samples = 3) => {
  db.insert(schema.publishMetrics)
    .values(
      Array.from({ length: samples }, (_, i) => ({
        projectId: "project-1",
        style,
        category: "beauty",
        views: 10000 + i * 1000,
        orders: 50 + i * 10,
      }))
    )
    .run();
};

const setBrief = (brief: CreationBrief) => {
  db.update(schema.projects).set({ creationBrief: brief }).where(eq(schema.projects.id, "project-1")).run();
};

describe("POST /api/llm/script 风格解析契约", () => {
  it("显式选择 UI 风格 pain-point → 200，用该风格生成，落库为引擎词 pain_point", async () => {
    const res = await post({ styleType: "pain-point" });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.styleType).toBe("pain-point");
    expect(data.styleSource).toBe("explicit");
    expect(state.lastInput?.styleType).toBe("pain_point");
    expect(scriptStyles()).toEqual(["pain_point"]);
  });

  it("显式选择 scenario → 200 且落库为引擎词 scene", async () => {
    const res = await post({ styleType: "scenario" });
    expect(res.status).toBe(200);
    expect((await res.json()).styleType).toBe("scenario");
    expect(state.lastInput?.styleType).toBe("scene");
    expect(scriptStyles()).toEqual(["scene"]);
  });

  it("请求体带模板 id → styleSource 记为 template 并写回项目简报", async () => {
    setBrief({
      version: 1,
      inputMode: "upload",
      targetDuration: 30,
      styleType: "",
      styleSource: "explicit",
      targetAudience: [],
      platforms: ["douyin"],
      outputStrategy: "draft",
      audioStrategy: "volcengine-tts",
    });
    const res = await post({ styleType: "drama", templateId: "template-1" });
    expect(res.status).toBe(200);
    expect((await res.json()).styleSource).toBe("template");
    const [project] = db.select().from(schema.projects).all();
    expect(project.creationBrief).toMatchObject({ styleType: "drama", styleSource: "template" });
  });

  it("auto 且无历史数据 → 409 needs_explicit_style，且绝不落 pain_point", async () => {
    const res = await post({ styleType: "auto" });
    expect(res.status).toBe(409);
    const data = await res.json();
    expect(data.code).toBe("needs_explicit_style");
    expect(data.reason).toBe("no-data");
    expect(typeof data.error).toBe("string");
    expect(data.error.length).toBeGreaterThan(0);
    expect(Array.isArray(data.candidates)).toBe(true);
    expect(data.candidates.length).toBeGreaterThan(0);
    expect(scriptStyles()).not.toContain("pain_point");
    expect(scriptStyles()).toHaveLength(0);
  });

  it("auto + 足够历史（topStyle 为引擎词 pain_point）→ 200，最终风格 pain-point", async () => {
    seedHistory("pain_point");
    const res = await post({ styleType: "auto", category: "beauty" });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.styleType).toBe("pain-point");
    expect(data.styleSource).toBe("performance-recommendation");
    expect(state.lastInput?.styleType).toBe("pain_point");
    expect(scriptStyles()).toEqual(["pain_point"]);
  });

  it("auto + 历史样本不足 → 409 no-data", async () => {
    seedHistory("pain_point", 2);
    const res = await post({ styleType: "auto", category: "beauty" });
    expect(res.status).toBe(409);
    expect((await res.json()).reason).toBe("no-data");
    expect(scriptStyles()).toHaveLength(0);
  });

  it("未知风格 → 409 unknown-style，且不落库", async () => {
    const res = await post({ styleType: "banana-style" });
    expect(res.status).toBe(409);
    const data = await res.json();
    expect(data.code).toBe("needs_explicit_style");
    expect(data.reason).toBe("unknown-style");
    expect(data.candidates).toContain("pain-point");
    expect(scriptStyles()).toHaveLength(0);
  });

  it("请求体未给风格/受众/平台 → 用项目 creationBrief 兜底", async () => {
    setBrief({
      version: 1,
      inputMode: "upload",
      targetDuration: 30,
      styleType: "drama",
      styleSource: "explicit",
      targetAudience: ["宝妈"],
      platforms: ["douyin", "tiktok"],
      outputStrategy: "draft",
      audioStrategy: "volcengine-tts",
    });
    const res = await post({});
    expect(res.status).toBe(200);
    expect(state.lastInput).toMatchObject({
      styleType: "drama",
      targetAudience: "宝妈",
      platforms: "douyin,tiktok",
    });
    expect((await res.json()).styleType).toBe("drama");
  });

  it("请求体显式字段优先于简报默认值", async () => {
    setBrief({
      version: 1,
      inputMode: "upload",
      targetDuration: 60,
      styleType: "drama",
      styleSource: "explicit",
      targetAudience: ["宝妈"],
      platforms: ["douyin"],
      outputStrategy: "draft",
      audioStrategy: "volcengine-tts",
    });
    const res = await post({ styleType: "story", platforms: "tiktok", targetAudience: "学生党" });
    expect(res.status).toBe(200);
    expect(state.lastInput).toMatchObject({
      styleType: "story",
      targetAudience: "学生党",
      platforms: "tiktok",
    });
  });
});

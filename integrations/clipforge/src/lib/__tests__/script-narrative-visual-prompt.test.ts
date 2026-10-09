// @vitest-environment node
/**
 * Coverage for C4/C5: 叙事要求（人物与处境/语言/语气）与视觉约束（creativeIntent / visualBible）必须真正
 * 进入送给 LLM 的脚本 prompt。
 *
 * Route level: real in-memory SQLite + real migrations + mocked getDb; the generator is mocked but calls
 * the REAL buildBatchPrompt, so `state.lastPrompt` is the exact text the engine would send to the LLM.
 * Prompt level: buildUserPrompt must be byte-identical when the new inputs are absent (改造前一致).
 */
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { eq } from "drizzle-orm";
import { join } from "path";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import { sanitizeCreativeIntent, sanitizeVisualBible } from "@/lib/production-system";
import { DEFAULT_CREATION_BRIEF } from "@/lib/creation-brief";
import { buildUserPrompt, buildBatchPrompt, type ScriptGenerationInput } from "@/lib/script-engine/prompts";

const state = vi.hoisted(() => ({
  db: null as unknown,
  lastInput: null as Record<string, unknown> | null,
  lastPrompt: null as string | null,
}));

vi.mock("@/lib/db", () => ({ getDb: () => state.db }));

vi.mock("@/lib/script-engine/generator", async () => {
  const { buildBatchPrompt: build } = await import("@/lib/script-engine/prompts");
  return {
    analyzeProduct: vi.fn(async () => "商品分析"),
    generateScript: vi.fn(async (input: ScriptGenerationInput) => {
      state.lastInput = input as unknown as Record<string, unknown>;
      // non-Pollinations endpoints keep the requested batch count (batchCountFor), so 3 mirrors the engine
      state.lastPrompt = build(input, 3);
      return [
        {
          title: "测试脚本",
          styleType: input.styleType,
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
  };
});

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
  db.insert(schema.projects).values({ id: "project-1", name: "测试项目" }).run();
  state.lastInput = null;
  state.lastPrompt = null;
});
afterAll(() => sqlite.close());

const LLM_CONFIG = { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" };

/** Explicit style keeps the 409 needs_explicit_style contract out of the way of these assertions. */
const post = (body: Record<string, unknown> = {}) =>
  POST(
    new NextRequest("http://localhost/api/llm/script", {
      method: "POST",
      body: JSON.stringify({ projectId: "project-1", productName: "桂花乌龙茶", styleType: "pain-point", llmConfig: LLM_CONFIG, ...body }),
    })
  );

const updateProject = (values: Partial<typeof schema.projects.$inferInsert>) =>
  db.update(schema.projects).set(values).where(eq(schema.projects.id, "project-1")).run();

describe("POST /api/llm/script × 叙事要求", () => {
  it("请求体带 narrative → 生成器收到的 prompt 里出现叙事段全文", async () => {
    const res = await post({ narrative: { situation: "通勤地铁上，女主刚下班", language: "口语化中文短句", tone: "轻快自嘲" } });
    expect(res.status).toBe(200);
    expect(state.lastPrompt).toContain("【叙事要求】");
    expect(state.lastPrompt).toContain("人物与处境：通勤地铁上，女主刚下班");
    expect(state.lastPrompt).toContain("语言：口语化中文短句");
    expect(state.lastPrompt).toContain("语气：轻快自嘲");
  });

  it("请求体不带 narrative，但项目 creationBrief.narrative 有值 → 同样进入 prompt", async () => {
    updateProject({ creationBrief: { ...DEFAULT_CREATION_BRIEF, narrative: { situation: "周末宅家的清晨", tone: "温柔" } } });
    const res = await post();
    expect(res.status).toBe(200);
    expect(state.lastPrompt).toContain("【叙事要求】");
    expect(state.lastPrompt).toContain("周末宅家的清晨");
    expect(state.lastPrompt).toContain("语气：温柔");
  });

  it("请求体字段逐项优先于项目简报，简报补全请求体没给的字段", async () => {
    updateProject({
      creationBrief: { ...DEFAULT_CREATION_BRIEF, narrative: { situation: "旧处境", language: "旧语言", tone: "旧语气" } },
    });
    await post({ narrative: { tone: "新语气" } });
    expect(state.lastPrompt).toContain("新语气");
    expect(state.lastPrompt).toContain("旧处境");
    expect(state.lastPrompt).toContain("旧语言");
    expect(state.lastPrompt).not.toContain("旧语气");
  });

  it("请求体 narrative 全为空串 → 视为未传（不出现叙事段）", async () => {
    await post({ narrative: { situation: "  ", language: "", tone: "" } });
    expect(state.lastPrompt).not.toContain("【叙事要求】");
  });
});

describe("POST /api/llm/script × 视觉约束", () => {
  it("项目有 creativeIntent → prompt 含视觉约束段（主体/光线/负向等字段进入 prompt）", async () => {
    updateProject({
      creativeIntent: sanitizeCreativeIntent({
        subject: "白色陶瓷咖啡杯",
        action: "手冲注水",
        environment: "木质厨房台面",
        lighting: "清晨侧光",
        palette: "暖白",
        composition: "中心构图",
        camera: "固定机位",
        motion: "缓慢推近",
        continuity: ["白色台面"],
        productConstraints: ["杯身logo不可变"],
        negative: ["出现人脸"],
      }),
    });
    const res = await post();
    expect(res.status).toBe(200);
    expect(state.lastPrompt).toContain("【视觉约束（必须遵守）】");
    expect(state.lastPrompt).toContain("白色陶瓷咖啡杯");
    expect(state.lastPrompt).toContain("清晨侧光");
    expect(state.lastPrompt).toContain("杯身logo不可变");
    expect(state.lastPrompt).toContain("出现人脸");
  });

  it("项目有 visualBible → 锚点与禁止改变进入 prompt", async () => {
    updateProject({
      visualBible: sanitizeVisualBible({
        characterAnchors: ["短发女生，浅蓝衬衫"],
        productAnchors: ["磨砂白瓶身"],
        forbiddenChanges: ["改变发色", "更换包装颜色"],
      }),
    });
    await post();
    expect(state.lastPrompt).toContain("【视觉约束（必须遵守）】");
    expect(state.lastPrompt).toContain("短发女生，浅蓝衬衫");
    expect(state.lastPrompt).toContain("磨砂白瓶身");
    expect(state.lastPrompt).toContain("禁止改变：改变发色；更换包装颜色");
  });

  it("项目既无 creativeIntent 也无 visualBible → 不出现视觉约束段", async () => {
    await post();
    expect(state.lastPrompt).not.toContain("【视觉约束");
  });
});

describe("POST /api/llm/script × 三者都空（与改造前一致）", () => {
  it("两段都不出现，且生成器输入里的三个新字段为 undefined；去掉它们后 prompt 完全一致", async () => {
    const res = await post();
    expect(res.status).toBe(200);
    const input = state.lastInput ?? {};
    expect(input.narrative).toBeUndefined();
    expect(input.creativeIntent).toBeUndefined();
    expect(input.visualBible).toBeUndefined();
    expect(state.lastPrompt).not.toContain("【叙事要求】");
    expect(state.lastPrompt).not.toContain("【视觉约束");

    // Same generation input minus the new fields → byte-identical prompt (no output change for legacy callers)
    const legacyInput = { ...input };
    delete legacyInput.narrative;
    delete legacyInput.creativeIntent;
    delete legacyInput.visualBible;
    expect(state.lastPrompt).toBe(buildBatchPrompt(legacyInput as unknown as ScriptGenerationInput, 3));
  });
});

describe("POST /api/llm/script × 项目设定读取容错", () => {
  it("项目设定读库失败 → 只告警不 500，仍按请求体生成", async () => {
    const real = state.db as typeof db;
    const realSelect = real.select.bind(real) as (...args: unknown[]) => unknown;
    state.db = new Proxy(real as object, {
      get(target, prop) {
        const value = Reflect.get(target, prop, target);
        if (prop !== "select") return typeof value === "function" ? value.bind(target) : value;
        return (fields?: unknown) => {
          if (fields && typeof fields === "object" && "creationBrief" in (fields as object)) throw new Error("项目读取失败");
          return realSelect(fields);
        };
      },
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const res = await post({ narrative: { tone: "轻快" } });
      expect(res.status).toBe(200);
      expect(warn).toHaveBeenCalled();
      expect(state.lastPrompt).toContain("语气：轻快");
    } finally {
      warn.mockRestore();
      state.db = real;
    }
  });
});

describe("buildUserPrompt × 叙事/视觉两段（提示词层）", () => {
  const base = { productName: "云柔纸巾", category: "home" as const, styleType: "pain_point" as const };

  it("填了叙事/视觉约束 → 两段都出现，且在风格指令之后、钩子指引之前", () => {
    const p = buildUserPrompt({
      ...base,
      narrative: { situation: "加班回家", tone: "吐槽" },
      creativeIntent: sanitizeCreativeIntent({ subject: "云柔抽纸", lighting: "暖光" }),
      visualBible: sanitizeVisualBible({ characterAnchors: ["短发女生"], forbiddenChanges: ["改变发色"] }),
    });
    expect(p).toContain("【叙事要求】");
    expect(p).toContain("【视觉约束（必须遵守）】");
    expect(p).toContain("短发女生");
    expect(p.indexOf("【脚本风格：痛点种草型】")).toBeLessThan(p.indexOf("【叙事要求】"));
    expect(p.indexOf("【叙事要求】")).toBeLessThan(p.indexOf("【视觉约束（必须遵守）】"));
    expect(p.indexOf("【视觉约束（必须遵守）】")).toBeLessThan(p.indexOf("黄金3秒钩子"));
  });

  it("不填 → 两段都不出现，且与传空对象时输出完全一致", () => {
    const bare = buildUserPrompt(base);
    expect(bare).not.toContain("【叙事要求】");
    expect(bare).not.toContain("【视觉约束");
    const empty = buildUserPrompt({
      ...base,
      narrative: {},
      creativeIntent: sanitizeCreativeIntent({}),
      visualBible: sanitizeVisualBible({}),
    });
    expect(empty).toBe(bare);
  });
});

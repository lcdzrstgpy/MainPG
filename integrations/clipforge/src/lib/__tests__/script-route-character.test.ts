// @vitest-environment node
/**
 * 断链修复覆盖：POST /api/llm/script 必须把请求体里的 `character`（出镜人物）服务端清洗后传给
 * generateScript，使 buildUserPrompt 的【出镜人物】块（人物名称 / 外貌锚点 / shot.characterId 约束）
 * 真正进入送给 LLM 的 prompt（此前 route 未透传 character，首次生成完全拿不到角色信息）。
 *
 * 路由层：真实内存 SQLite + 真实 migrate + mock getDb；generator 被 mock 但调用真实 buildBatchPrompt,
 * 因此 state.lastPrompt 就是引擎实际会发给 LLM 的文本（搭法同 script-narrative-visual-prompt.test.ts）。
 */
import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { join } from "path";
import { NextRequest } from "next/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import type { ScriptGenerationInput } from "@/lib/script-engine/prompts";

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

/**
 * The exact generateScript key set before this change (route's object literal, sorted). The
 * no-character path must still hand over precisely this set — no `character` key added.
 */
const LEGACY_INPUT_KEYS = [
  "category",
  "creativeIntent",
  "customRequirements",
  "llmConfig",
  "narrative",
  "performanceHint",
  "platforms",
  "preferredHookId",
  "priceRange",
  "productAnalysis",
  "productDescription",
  "productName",
  "referenceStructure",
  "styleType",
  "targetAudience",
  "targetDuration",
  "usageAdvantage",
  "videoMode",
  "visualBible",
];

describe("POST /api/llm/script × 出镜人物 character", () => {
  it("请求带合法 character → generateScript 收到清洗后的 character，prompt 含人物块与 characterId 约束", async () => {
    const res = await post({
      videoMode: "live_presenter",
      character: {
        id: "char_a",
        name: "小美",
        appearance: "32岁有亲和力，松散低马尾，浅色居家服，左手腕套着旧发圈",
        voiceStyle: "轻快女声",
      },
    });
    expect(res.status).toBe(200);

    // 断言清洗后的字段与值（而不是原样对象）
    expect(state.lastInput?.character).toEqual({
      id: "char_a",
      name: "小美",
      appearance: "32岁有亲和力，松散低马尾，浅色居家服，左手腕套着旧发圈",
      voiceStyle: "轻快女声",
    });

    // 提示词真实包含人物名称、外貌锚点与 characterId 约束
    expect(state.lastPrompt).toContain("【出镜人物】");
    expect(state.lastPrompt).toContain("人物名称：小美");
    expect(state.lastPrompt).toContain("外貌特征：32岁有亲和力，松散低马尾，浅色居家服，左手腕套着旧发圈");
    expect(state.lastPrompt).toContain("声音风格：轻快女声");
    expect(state.lastPrompt).toContain('characterId 字段填入 "char_a"');
  });

  it("清洗：trim、按上限截断（id/voiceStyle≤80、name≤60、appearance≤300）、丢弃未知键", async () => {
    await post({
      character: {
        id: `  ${"i".repeat(100)}  `,
        name: `  ${"n".repeat(100)}  `,
        appearance: "a".repeat(400),
        voiceStyle: "v".repeat(100),
        gender: "male",
        persona: "毒舌闺蜜",
        unknown: "should-be-dropped",
      },
    });

    const character = state.lastInput?.character as Record<string, string>;
    expect(Object.keys(character).sort()).toEqual(["appearance", "id", "name", "voiceStyle"]);
    expect(character.id).toBe("i".repeat(80));
    expect(character.name).toBe("n".repeat(60));
    expect(character.appearance).toBe("a".repeat(300));
    expect(character.voiceStyle).toBe("v".repeat(80));
  });

  it("请求不带 character → prompt 无人物块，且 generateScript 参数键与改造前逐键一致", async () => {
    const res = await post();
    expect(res.status).toBe(200);

    const input = state.lastInput ?? {};
    expect("character" in input).toBe(false);
    expect(input.character).toBeUndefined();
    expect(Object.keys(input).sort()).toEqual(LEGACY_INPUT_KEYS);
    expect(state.lastPrompt).not.toContain("【出镜人物】");
  });
});

describe("POST /api/llm/script × character 脏数据（安全忽略 / 按上限截断）", () => {
  const expectIgnored = async (character: unknown) => {
    const res = await post({ character });
    expect(res.status).toBe(200);
    expect(state.lastInput?.character).toBeUndefined();
    expect("character" in (state.lastInput ?? {})).toBe(false);
    expect(state.lastPrompt).not.toContain("【出镜人物】");
  };

  it("id 为空白 → 忽略", async () => {
    await expectIgnored({ id: "   ", name: "小美", appearance: "长发" });
  });

  it("name 缺失 → 忽略", async () => {
    await expectIgnored({ id: "char_a", appearance: "长发" });
  });

  it("name 非字符串（数字）→ 忽略", async () => {
    await expectIgnored({ id: "char_a", name: 42, appearance: "长发" });
  });

  it("空对象 → 忽略", async () => {
    await expectIgnored({});
  });

  it("character 为字符串 / 数字 / 数组 / null → 一律安全忽略，不 500", async () => {
    await expectIgnored("char_a");
    await expectIgnored(123);
    await expectIgnored(["char_a", "小美"]);
    await expectIgnored(null);
  });

  it("缺 appearance → 仍然接受（appearance 为空串），仅保留 id/name", async () => {
    const res = await post({ character: { id: "char_a", name: "小美" } });
    expect(res.status).toBe(200);
    expect(state.lastInput?.character).toEqual({ id: "char_a", name: "小美", appearance: "" });
    expect(state.lastPrompt).toContain("人物名称：小美");
    expect(state.lastPrompt).toContain('characterId 字段填入 "char_a"');
    expect(state.lastPrompt).not.toContain("声音风格：");
  });

  it("voiceStyle 为空白串 → 不写入（保持可选语义）", async () => {
    await post({ character: { id: "char_a", name: "小美", appearance: "长发", voiceStyle: "   " } });
    expect(state.lastInput?.character).toEqual({ id: "char_a", name: "小美", appearance: "长发" });
    expect(state.lastPrompt).not.toContain("声音风格：");
  });
});

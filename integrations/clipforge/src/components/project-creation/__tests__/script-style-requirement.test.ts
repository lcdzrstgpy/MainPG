import { describe, expect, it } from "vitest";
import { SCRIPT_STYLE_VALUES } from "@/lib/script-style";
import {
  NEEDS_EXPLICIT_STYLE_CODE,
  missingStyleRequirement,
  parseStyleRequirement,
} from "@/components/project-creation/script-style-requirement";

/**
 * 脚本接口在无足够历史数据时不再静默回退痛点种草，而是返回 409 + needs_explicit_style。
 * 两个创建入口必须把这次响应翻译成「请用户显式选风格」，而不是当成致命错误。
 */
describe("parseStyleRequirement", () => {
  it("只把 409 + needs_explicit_style 当作「需要用户显式选风格」", () => {
    expect(NEEDS_EXPLICIT_STYLE_CODE).toBe("needs_explicit_style");
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: ["drama"] })).toEqual({
      reason: "no-data",
      candidates: ["drama"],
    });
    // 其它状态码/其它 code 都不是这个分支，调用方必须按普通错误处理
    expect(parseStyleRequirement(200, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: ["drama"] })).toBeNull();
    expect(parseStyleRequirement(409, { code: "topic_project" })).toBeNull();
    expect(parseStyleRequirement(409, null)).toBeNull();
    expect(parseStyleRequirement(500, { error: "boom" })).toBeNull();
  });

  it("消费 candidates：过滤非法值，缺失时回退到完整可选风格表", () => {
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: ["drama", "", 3, null, "unboxing"] }))
      .toEqual({ reason: "no-data", candidates: ["drama", "unboxing"] });
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE })?.candidates)
      .toEqual([...SCRIPT_STYLE_VALUES]);
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: [] })?.candidates)
      .toEqual([...SCRIPT_STYLE_VALUES]);
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: "drama" })?.candidates)
      .toEqual([...SCRIPT_STYLE_VALUES]);
  });

  it("本地就能判定的「还没选风格」不需要请求接口", () => {
    expect(missingStyleRequirement()).toEqual({ reason: "missing", candidates: [...SCRIPT_STYLE_VALUES] });
    // 候选必须是可选风格（不含 auto），否则用户会选到一个仍然需要推荐的风格
    expect(missingStyleRequirement().candidates).not.toContain("auto");
  });
});

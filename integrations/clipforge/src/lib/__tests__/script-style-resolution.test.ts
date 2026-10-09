import { describe, it, expect } from "vitest";
import {
  resolveScriptStyle,
  SCRIPT_STYLE_VALUES,
  SCRIPT_STYLE_ALIASES,
  DEFAULT_MIN_SAMPLE_SIZE,
} from "@/lib/script-style";
import { STYLE_VALUES } from "@/lib/ad-templates";

const expectNeedsChoice = (result: ReturnType<typeof resolveScriptStyle>, reason: "no-data" | "unknown-style") => {
  expect(result.kind).toBe("needs-explicit-choice");
  if (result.kind === "needs-explicit-choice") {
    expect(result.reason).toBe(reason);
    expect(result.candidates).toEqual([...SCRIPT_STYLE_VALUES]);
  }
};

describe("SCRIPT_STYLE_VALUES 风格白名单", () => {
  it("从 ad-templates 白名单派生（去掉 auto），不复制第二份字面量", () => {
    expect(SCRIPT_STYLE_VALUES).toEqual([...STYLE_VALUES].filter((v) => v !== "auto"));
  });

  it("痛点种草写作 pain-point，且不包含 auto", () => {
    expect(SCRIPT_STYLE_VALUES).toContain("pain-point");
    expect(SCRIPT_STYLE_VALUES).toContain("scenario");
    expect(SCRIPT_STYLE_VALUES).not.toContain("auto");
    expect(DEFAULT_MIN_SAMPLE_SIZE).toBe(3);
  });
});

describe("resolveScriptStyle 契约 1：显式且合法的风格", () => {
  it("合法非 auto 风格 → resolved，来源 explicit", () => {
    expect(resolveScriptStyle({ requestedStyle: "drama" })).toEqual({
      kind: "resolved",
      styleType: "drama",
      styleSource: "explicit",
    });
  });

  it("模板覆盖 → styleSource 为 template", () => {
    expect(resolveScriptStyle({ requestedStyle: "comparison", styleSource: "template" })).toEqual({
      kind: "resolved",
      styleType: "comparison",
      styleSource: "template",
    });
  });

  it("显式选择 pain-point 仍然可用（用户主动选痛点种草不能被禁止）", () => {
    expect(resolveScriptStyle({ requestedStyle: "pain-point" })).toEqual({
      kind: "resolved",
      styleType: "pain-point",
      styleSource: "explicit",
    });
  });

  it("历史洞察不得覆盖用户的显式选择", () => {
    const result = resolveScriptStyle({
      requestedStyle: "story",
      insights: { topStyle: "drama", sampleSize: 999 },
    });
    expect(result).toEqual({ kind: "resolved", styleType: "story", styleSource: "explicit" });
  });
});

describe("resolveScriptStyle 契约 2：auto / 空 → 智能推荐", () => {
  it("回归：auto + 无数据不会返回 pain-point（本次要修的核心 bug）", () => {
    const result = resolveScriptStyle({ requestedStyle: "auto", insights: null });
    expect(result.kind).not.toBe("resolved");
    expectNeedsChoice(result, "no-data");
    expect(result).not.toEqual({ kind: "resolved", styleType: "pain-point", styleSource: "explicit" });
    expect(JSON.stringify(result)).not.toContain('"styleType"');
  });

  it("空串 / undefined 同样视为未显式选择", () => {
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "" }), "no-data");
    expectNeedsChoice(resolveScriptStyle({}), "no-data");
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: null, sampleSize: null } }), "no-data");
  });

  it("auto + 合法 topStyle + 足够样本 → resolved 且来源为 performance-recommendation", () => {
    expect(resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "drama", sampleSize: 3 } })).toEqual({
      kind: "resolved",
      styleType: "drama",
      styleSource: "performance-recommendation",
    });
  });

  it("auto + 样本量不足（1 < 默认 3）→ needs-explicit-choice", () => {
    expectNeedsChoice(
      resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "drama", sampleSize: 1 } }),
      "no-data"
    );
  });

  it("auto + sampleSize 缺失按 0 处理 → needs-explicit-choice", () => {
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "drama" } }), "no-data");
  });

  it("minSampleSize 可覆盖阈值", () => {
    expect(resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "story", sampleSize: 1 }, minSampleSize: 1 })).toEqual({
      kind: "resolved",
      styleType: "story",
      styleSource: "performance-recommendation",
    });
  });

  it("auto + topStyle 非法（不在白名单）→ no-data，而不是 unknown-style", () => {
    expectNeedsChoice(
      resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "banana-style", sampleSize: 10 } }),
      "no-data"
    );
  });
});

describe("resolveScriptStyle 契约 3：非法风格", () => {
  it("请求一个不存在的风格 → unknown-style + 候选列表", () => {
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "banana-style" }), "unknown-style");
  });

  it("候选列表只包含可直接生成的合法风格", () => {
    const result = resolveScriptStyle({ requestedStyle: "" });
    if (result.kind === "needs-explicit-choice") {
      for (const candidate of result.candidates) {
        expect(SCRIPT_STYLE_VALUES).toContain(candidate);
        expect(candidate).not.toBe("auto");
      }
    }
  });
});

describe("resolveScriptStyle 契约 4：纯函数，非法输入不抛错", () => {
  it("对 null / 数字 / 对象 / 数组等非法输入一律走分支而非异常", () => {
    const hostileInputs: unknown[] = [null, 42, true, {}, [], { requestedStyle: {} }, { insights: "nope" }];
    for (const value of hostileInputs) {
      expect(() => resolveScriptStyle({ requestedStyle: value })).not.toThrow();
      const result = resolveScriptStyle({ requestedStyle: value });
      expect(["resolved", "needs-explicit-choice"]).toContain(result.kind);
    }
  });
});

describe("SCRIPT_STYLE_ALIASES 受控别名表（引擎词 → UI 白名单）", () => {
  it("只登记真正拼写不同的引擎词，不改变公开白名单取值集合", () => {
    expect(SCRIPT_STYLE_ALIASES).toEqual({ pain_point: "pain-point", scene: "scenario" });
    // 别名只影响归一化读取，SCRIPT_STYLE_VALUES 仍是纯 UI 白名单
    expect(SCRIPT_STYLE_VALUES).not.toContain("pain_point");
    expect(SCRIPT_STYLE_VALUES).not.toContain("scene");
    for (const target of Object.values(SCRIPT_STYLE_ALIASES)) {
      expect(SCRIPT_STYLE_VALUES).toContain(target);
    }
  });

  it("回归：auto + 历史 topStyle 为引擎词 pain_point → 可推荐为 pain-point", () => {
    expect(
      resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "pain_point", sampleSize: 3 } })
    ).toEqual({ kind: "resolved", styleType: "pain-point", styleSource: "performance-recommendation" });
  });

  it("回归：auto + 历史 topStyle 为引擎词 scene → 可推荐为 scenario", () => {
    expect(
      resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "scene", sampleSize: 5 } })
    ).toEqual({ kind: "resolved", styleType: "scenario", styleSource: "performance-recommendation" });
  });

  it("调用方直接传引擎词时同样归一到 UI 值", () => {
    expect(resolveScriptStyle({ requestedStyle: "pain_point" })).toEqual({
      kind: "resolved",
      styleType: "pain-point",
      styleSource: "explicit",
    });
    expect(resolveScriptStyle({ requestedStyle: "SCENE" })).toEqual({
      kind: "resolved",
      styleType: "scenario",
      styleSource: "explicit",
    });
  });

  it("未列入别名表的非法值仍不被接受（不静默吞掉）", () => {
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "custom" }), "unknown-style");
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "painpoint" }), "unknown-style");
    expectNeedsChoice(resolveScriptStyle({ requestedStyle: "scenes" }), "unknown-style");
    expectNeedsChoice(
      resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "custom", sampleSize: 99 } }),
      "no-data"
    );
    expectNeedsChoice(
      resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "painpoint", sampleSize: 99 } }),
      "no-data"
    );
  });
});

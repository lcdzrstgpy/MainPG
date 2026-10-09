import { describe, expect, it } from "vitest";
import { resolveScriptFlowPolicy } from "@/lib/script-flow-policy";
import type { OutputStrategy } from "@/lib/creation-brief";

describe("resolveScriptFlowPolicy：出片策略 → 脚本页主操作与自动启动授权", () => {
  it("draft：主操作是免费草稿自动成片，允许自动启动且允许免费链", () => {
    const p = resolveScriptFlowPolicy({ outputStrategy: "draft", autoMode: true });
    expect(p.strategy).toBe("draft");
    expect(p.primaryAction).toBe("draft-auto-finish");
    expect(p.allowAutoStart).toBe(true);
    expect(p.allowFreeChain).toBe(true);
    expect(p.showDraftAction).toBe(true);
    expect(p.showControlledMotionAction).toBe(false);
    expect(p.showNativeFilmAction).toBe(false);
  });

  it("draft 无 auto 参数时不允许自动启动，但仍允许免费链", () => {
    const p = resolveScriptFlowPolicy({ outputStrategy: "draft" });
    expect(p.allowAutoStart).toBe(false);
    expect(p.allowFreeChain).toBe(true);
  });

  it("controlled-motion：逐镜动态是主操作，?auto=1 绝不降级成免费草稿", () => {
    const p = resolveScriptFlowPolicy({ outputStrategy: "controlled-motion", autoMode: true });
    expect(p.strategy).toBe("controlled-motion");
    expect(p.primaryAction).toBe("controlled-motion-assets");
    expect(p.allowAutoStart).toBe(false);
    expect(p.allowFreeChain).toBe(false);
    expect(p.showDraftAction).toBe(false);
    expect(p.showControlledMotionAction).toBe(true);
    expect(p.showNativeFilmAction).toBe(false);
  });

  it("native-film：整片预览是主操作，?auto=1 绝不降级成免费草稿", () => {
    const p = resolveScriptFlowPolicy({ outputStrategy: "native-film", autoMode: true });
    expect(p.strategy).toBe("native-film");
    expect(p.primaryAction).toBe("native-film-preview");
    expect(p.allowAutoStart).toBe(false);
    expect(p.allowFreeChain).toBe(false);
    expect(p.showDraftAction).toBe(false);
    expect(p.showControlledMotionAction).toBe(false);
    expect(p.showNativeFilmAction).toBe(true);
  });

  it("旧项目（null）：保留原有双入口与断点恢复行为", () => {
    const p = resolveScriptFlowPolicy({ outputStrategy: null, autoMode: true });
    expect(p.strategy).toBe("legacy");
    expect(p.primaryAction).toBe("legacy");
    expect(p.allowAutoStart).toBe(true);
    expect(p.allowFreeChain).toBe(true);
    expect(p.showDraftAction).toBe(true);
    expect(p.showNativeFilmAction).toBe(true);
    expect(p.showControlledMotionAction).toBe(false);
  });

  it("uiMode 只是界面模式，绝不改判出片策略（simple 与 pro 产出完全一致）", () => {
    const strategies: readonly (OutputStrategy | null)[] = ["draft", "controlled-motion", "native-film", null];
    for (const outputStrategy of strategies) {
      const simple = resolveScriptFlowPolicy({ outputStrategy, uiMode: "simple", autoMode: true });
      const pro = resolveScriptFlowPolicy({ outputStrategy, uiMode: "pro", autoMode: true });
      expect(simple).toEqual(pro);
    }
  });

  it("未知字符串策略安全回退到 legacy（不抛错、不误判付费策略）", () => {
    const p = resolveScriptFlowPolicy({ outputStrategy: "hologram" as OutputStrategy, autoMode: true });
    expect(p.strategy).toBe("legacy");
    expect(p.allowAutoStart).toBe(true);
    expect(p.allowFreeChain).toBe(true);
  });
});

describe("resolveScriptFlowPolicy：简报读取状态（briefState）", () => {
  it("简报 loading/failed 时关闭全部生成动作（自动启动/免费链/整片入口，draft 与 legacy 同样被锁）", () => {
    for (const briefState of ["loading", "failed"] as const) {
      const draft = resolveScriptFlowPolicy({ outputStrategy: "draft", autoMode: true, briefState });
      expect(draft.allowAutoStart).toBe(false);
      expect(draft.allowFreeChain).toBe(false);
      expect(draft.showDraftAction).toBe(false);
      const legacy = resolveScriptFlowPolicy({ outputStrategy: null, autoMode: true, briefState });
      expect(legacy.allowAutoStart).toBe(false);
      expect(legacy.allowFreeChain).toBe(false);
      expect(legacy.showDraftAction).toBe(false);
      expect(legacy.showNativeFilmAction).toBe(false);
    }
  });

  it("只有成功读定简报（含读到 null 的旧项目）才放行动作（briefState 缺省即 loaded）", () => {
    const legacy = resolveScriptFlowPolicy({ outputStrategy: null, autoMode: true });
    expect(legacy.allowAutoStart).toBe(true);
    expect(legacy.allowFreeChain).toBe(true);
    expect(legacy.showDraftAction).toBe(true);
    expect(legacy.showNativeFilmAction).toBe(true);
    expect(resolveScriptFlowPolicy({ outputStrategy: "draft", autoMode: true }).allowAutoStart).toBe(true);
  });

  it("briefState 失败只锁动作，不改付费策略归属（策略字段保持不变）", () => {
    const failedNative = resolveScriptFlowPolicy({ outputStrategy: "native-film", autoMode: true, briefState: "failed" });
    expect(failedNative.strategy).toBe("native-film");
    expect(failedNative.showNativeFilmAction).toBe(false);
    expect(resolveScriptFlowPolicy({ outputStrategy: "native-film", autoMode: true }).showNativeFilmAction).toBe(true);
    expect(resolveScriptFlowPolicy({ outputStrategy: "controlled-motion", briefState: "failed" }).allowFreeChain).toBe(false);
  });
});
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  LEGACY_BRIEF_NOTICE,
  OUTPUT_STRATEGY_GROUP_LABELS,
  assetsStageGuide,
  compositionStrategyOf,
  groupCompositionsByStrategy,
  summarizeVoiceReport,
  voiceSourceExplanation,
} from "@/lib/project-detail-view";

/**
 * 项目详情三页（assets / video / export）消费创作简报的契约。
 *
 * 仓库没有 @testing-library/react，沿用既有「源码契约 + 纯逻辑」风格
 * （见 src/components/project-creation/__tests__/creation-brief-components.test.ts）：
 * 页面里能抽成纯函数的判定一律抽出来直接单测，页面本身只断言关键接线不会消失。
 */
const page = (file: string) => readFileSync(resolve(process.cwd(), "src/app/project/[id]", file), "utf8");

const assetsPage = page("assets/page.tsx");
const videoPage = page("video/page.tsx");
const exportPage = page("export/page.tsx");

const SUMMARY_IMPORT = /import \{ CreationBriefSummary \} from "@\/components\/project-creation\/creation-brief-summary"/;

describe("项目详情页创作简报接线（源码契约）", () => {
  it("三个页面都渲染只读简报摘要组件", () => {
    for (const source of [assetsPage, videoPage, exportPage]) {
      expect(source).toMatch(SUMMARY_IMPORT);
      expect(source).toMatch(/<CreationBriefSummary/);
    }
  });

  it("三个页面都对 null 简报的旧项目给出兼容提示，而不是抛错或空渲染", () => {
    for (const source of [assetsPage, videoPage, exportPage]) {
      expect(source).toMatch(/LEGACY_BRIEF_NOTICE/);
    }
  });

  it("导出页按策略分组，并把非主策略版本放进「其他版本」", () => {
    expect(exportPage).toMatch(/groupCompositionsByStrategy\(/);
    expect(exportPage).toMatch(/OUTPUT_STRATEGY_GROUP_LABELS/);
    expect(exportPage).toMatch(/data-composition-group="primary"/);
    expect(exportPage).toMatch(/data-composition-group="others"/);
    expect(exportPage).toMatch(/其他版本/);
  });

  it("导出页默认选中项目 outputStrategy 对应的主版本，读不到时退回最新一条（旧项目行为）", () => {
    expect(exportPage).toMatch(/creationBrief/);
    expect(exportPage).toMatch(/primary\[0\] \?\? list\[0\]/);
  });

  it("素材页按策略标注主操作，不改变既有按钮行为", () => {
    expect(assetsPage).toMatch(/assetsStageGuide\(/);
    expect(assetsPage).toMatch(/data-strategy-primary/);
    // 逐镜 I2V 按钮仍是原来的 generateMotion 调用，只是多了策略标注
    expect(assetsPage).toMatch(/onClick=\{\(\) => generateMotion\(asset\.shotId\)\}/);
  });

  it("视频页读取 sidecar 的 voiceReport，读不到时显示「暂无音频报告」", () => {
    expect(videoPage).toMatch(/summarizeVoiceReport\(/);
    expect(videoPage).toMatch(/timelineUrl/);
    expect(videoPage).toMatch(/暂无音频报告/);
    expect(videoPage).toMatch(/voiceSourceExplanation\(/);
  });
});

describe("groupCompositionsByStrategy", () => {
  const draft = { id: "a", label: "免费草稿 · 静态合成" };
  const motion = { id: "b", strategy: "controlled-motion" as const, label: "逐镜动态合成" };
  const film = { id: "c", label: "九宫格整片 · Seedance 2.5" };
  const unlabeled = { id: "d", label: null };

  it("按项目策略挑出主版本，其余进「其他版本」并保持传入顺序", () => {
    const groups = groupCompositionsByStrategy([film, motion, unlabeled, draft], "controlled-motion");
    expect(groups.primary.map((c) => c.id)).toEqual(["b"]);
    expect(groups.others.map((c) => c.id)).toEqual(["c", "d", "a"]);
  });

  it("原生整片按标签识别，免费草稿按标签识别", () => {
    expect(compositionStrategyOf(film)).toBe("native-film");
    expect(compositionStrategyOf(draft)).toBe("draft");
    expect(compositionStrategyOf(unlabeled)).toBeNull();
    expect(compositionStrategyOf({ strategy: "native-film", label: null })).toBe("native-film");
  });

  it("creationBrief 为 null（outputStrategy 未知）时返回空主版本且不抛错", () => {
    for (const strategy of [null, undefined]) {
      const groups = groupCompositionsByStrategy([film, motion], strategy);
      expect(groups.primary).toEqual([]);
      expect(groups.others.map((c) => c.id)).toEqual(["c", "b"]);
    }
    expect(groupCompositionsByStrategy(null, "draft")).toEqual({ primary: [], others: [] });
    expect(groupCompositionsByStrategy(undefined, "draft")).toEqual({ primary: [], others: [] });
  });

  it("没有版本命中主策略时主版本为空，页面据此退回最新一条", () => {
    const groups = groupCompositionsByStrategy([unlabeled], "native-film");
    expect(groups.primary).toEqual([]);
    expect(groups.others.map((c) => c.id)).toEqual(["d"]);
  });
});

describe("summarizeVoiceReport", () => {
  it("归约 counts 与 failedShotIds，并保留逐镜降级原因", () => {
    const summary = summarizeVoiceReport({
      version: 1,
      shots: [
        { shotId: 1, source: "volcengine" },
        { shotId: 2, source: "edge", reason: "付费语音合成失败，已回退免费 Edge 音色：503" },
        { shotId: 3, source: "native" },
      ],
      counts: { volcengine: 1, edge: 1, native: 1, none: 0 },
      hasFailures: true,
      failedShotIds: [2],
    });

    expect(summary).not.toBeNull();
    expect(summary?.counts).toEqual({ volcengine: 1, edge: 1, native: 1, none: 0 });
    expect(summary?.failedShotIds).toEqual([2]);
    expect(summary?.hasFailures).toBe(true);
    expect(summary?.degradations).toEqual([
      { shotId: 2, source: "edge", reason: "付费语音合成失败，已回退免费 Edge 音色：503" },
    ]);
  });

  it("sidecar 缺失或结构异常时返回 null（页面显示「暂无音频报告」），绝不抛错", () => {
    expect(summarizeVoiceReport(null)).toBeNull();
    expect(summarizeVoiceReport(undefined)).toBeNull();
    expect(summarizeVoiceReport("not json")).toBeNull();
    expect(summarizeVoiceReport({ boundaries: [1, 2] })).toBeNull();
    expect(summarizeVoiceReport({ counts: "oops" })).toBeNull();
  });

  it("只有 counts 的旧 sidecar 也能读，缺省字段回退为空", () => {
    const summary = summarizeVoiceReport({ counts: { volcengine: 2, edge: 0, native: 0, none: 1 } });
    expect(summary?.counts).toEqual({ volcengine: 2, edge: 0, native: 0, none: 1 });
    expect(summary?.failedShotIds).toEqual([]);
    expect(summary?.hasFailures).toBe(false);
    expect(summary?.degradations).toEqual([]);
  });
});

describe("voiceSourceExplanation", () => {
  it("简报为 native-audio 时说明是模型原生音频", () => {
    const e = voiceSourceExplanation({ audioStrategy: "native-audio", ttsEnabled: true, paidTtsReady: true });
    expect(e.kind).toBe("native");
    expect(e.label).toContain("模型原生音频");
  });

  it("简报为 mute 时说明静音", () => {
    expect(voiceSourceExplanation({ audioStrategy: "mute", ttsEnabled: true, paidTtsReady: true }).kind).toBe("mute");
  });

  it("关了配音开关时说明本次不生成人声", () => {
    expect(voiceSourceExplanation({ audioStrategy: "volcengine-tts", ttsEnabled: false, paidTtsReady: true }).kind).toBe("off");
  });

  it("付费火山语音就绪时说清付费来源，否则说明免费 Edge 回退", () => {
    const paid = voiceSourceExplanation({ audioStrategy: "volcengine-tts", ttsEnabled: true, paidTtsReady: true });
    expect(paid.kind).toBe("volcengine");
    expect(paid.label).toContain("火山语音");

    const free = voiceSourceExplanation({ audioStrategy: "volcengine-tts", ttsEnabled: true, paidTtsReady: false });
    expect(free.kind).toBe("edge");
    expect(free.label).toContain("Edge");
    expect(free.detail).toContain("回退");
  });

  it("旧项目没有简报时按开关与配置现状解释", () => {
    expect(voiceSourceExplanation({ audioStrategy: null, ttsEnabled: true, paidTtsReady: false }).kind).toBe("edge");
  });
});

describe("assetsStageGuide", () => {
  it("draft 明确标注免费草稿是静态素材，非 AI 动态视频", () => {
    const guide = assetsStageGuide("draft");
    expect(guide.legacy).toBe(false);
    expect(guide.primaryAction).toBe("stock-fill");
    expect(guide.title).toBe("免费草稿：静态素材，非 AI 动态视频");
    expect(guide.detail).toContain("不会提交生视频任务");
  });

  it("只有 controlled-motion 把逐镜 I2V 作为主操作", () => {
    expect(assetsStageGuide("controlled-motion").primaryAction).toBe("per-shot-motion");
    expect(assetsStageGuide("draft").primaryAction).not.toBe("per-shot-motion");
    expect(assetsStageGuide("native-film").primaryAction).not.toBe("per-shot-motion");
  });

  it("native-film 引导到整片流程", () => {
    const guide = assetsStageGuide("native-film");
    expect(guide.primaryAction).toBe("storyboard-film");
    expect(guide.detail).toContain("整片");
  });

  it("旧项目（简报为 null）保持原素材流程并给出兼容提示", () => {
    const guide = assetsStageGuide(null);
    expect(guide.legacy).toBe(true);
    expect(guide.primaryAction).toBeNull();
    expect(guide.title).toBe(LEGACY_BRIEF_NOTICE);
    expect(assetsStageGuide(undefined).legacy).toBe(true);
  });

  it("三种策略的分组标题都不为空", () => {
    expect(Object.values(OUTPUT_STRATEGY_GROUP_LABELS)).toEqual(["免费草稿", "受控动态成片", "原生整片"]);
  });
});

import { describe, expect, it } from "vitest";
import { buildVoiceReport } from "@/lib/voice-report";

describe("buildVoiceReport 逐镜音源归约", () => {
  it("全付费成功：volcengine 计数正确且无失败", () => {
    const report = buildVoiceReport({
      shots: [
        { shotId: 1, source: "volcengine" },
        { shotId: 2, source: "volcengine" },
        { shotId: 3, source: "volcengine" },
      ],
    });

    expect(report.counts).toEqual({ volcengine: 3, edge: 0, native: 0, none: 0 });
    expect(report.hasFailures).toBe(false);
    expect(report.failedShotIds).toEqual([]);
    expect(report.shots).toHaveLength(3);
    expect(report.shots.every((s) => s.warning === undefined && s.reason === undefined)).toBe(true);
  });

  it("付费失败回退免费 Edge：该镜音源为 edge，保留失败原因并标记失败", () => {
    const report = buildVoiceReport({
      shots: [
        { shotId: 1, source: "volcengine" },
        { shotId: 2, source: "edge", reason: "付费语音合成失败，已回退免费 Edge 音色：503 Service Unavailable" },
      ],
      warnings: [{ code: "tts_fallback_free", shotId: 2 }],
    });

    expect(report.counts).toEqual({ volcengine: 1, edge: 1, native: 0, none: 0 });
    expect(report.hasFailures).toBe(true);
    expect(report.failedShotIds).toEqual([2]);

    const fallback = report.shots.find((s) => s.shotId === 2);
    expect(fallback?.source).toBe("edge");
    expect(fallback?.reason).toContain("回退");
    expect(fallback?.warning).toBe("tts_fallback_free");
  });

  it("免费与原生音频混合：native 与 edge 计数正确且不算失败", () => {
    const report = buildVoiceReport({
      shots: [
        { shotId: 1, source: "edge" },
        { shotId: 2, source: "native" },
        { shotId: 3, source: "native" },
      ],
    });

    expect(report.counts).toEqual({ volcengine: 0, edge: 1, native: 2, none: 0 });
    expect(report.hasFailures).toBe(false);
    expect(report.failedShotIds).toEqual([]);
    expect(report.shots.map((s) => s.source)).toEqual(["edge", "native", "native"]);
  });

  it("全部失败：failedShotIds 与 none 计数正确", () => {
    const report = buildVoiceReport({
      shots: [
        { shotId: 1, source: "none", reason: "语音合成失败，该镜无旁白：socket hang up" },
        { shotId: 2, source: "none", reason: "语音合成失败，该镜无旁白：socket hang up" },
      ],
      warnings: [
        { code: "tts_failed", shotId: 1 },
        { code: "tts_failed", shotId: 2 },
      ],
    });

    expect(report.counts).toEqual({ volcengine: 0, edge: 0, native: 0, none: 2 });
    expect(report.hasFailures).toBe(true);
    expect(report.failedShotIds).toEqual([1, 2]);
    expect(report.shots.every((s) => s.warning === "tts_failed")).toBe(true);
  });

  it("无音源且无警告（未开启配音）不算失败，但仍计入 none", () => {
    const report = buildVoiceReport({
      shots: [
        { shotId: 1, source: "none" },
        { shotId: 2, source: "volcengine" },
      ],
      warnings: [],
    });

    expect(report.counts).toEqual({ volcengine: 1, edge: 0, native: 0, none: 1 });
    expect(report.hasFailures).toBe(false);
    expect(report.failedShotIds).toEqual([]);
  });

  it("重复警告的 shotId 去重且保留出现顺序，sidecar 结构稳定", () => {
    const report = buildVoiceReport({
      shots: [{ shotId: 9, source: "none" }],
      warnings: [
        { code: "tts_fallback_free", shotId: 5 },
        { code: "tts_failed", shotId: 9 },
        { code: "tts_failed", shotId: 9 },
      ],
    });

    expect(report.version).toBe(1);
    expect(Object.keys(report)).toEqual(["version", "shots", "counts", "hasFailures", "failedShotIds"]);
    expect(report.failedShotIds).toEqual([5, 9]);
    expect(report.shots[0].warning).toBe("tts_failed");
  });
});

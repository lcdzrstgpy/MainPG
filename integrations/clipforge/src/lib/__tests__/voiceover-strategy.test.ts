import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AUDIO_STRATEGY_LABELS, resolveVoiceoverRequest } from "@/lib/voiceover-strategy";

/**
 * 审阅 P2：`creationBrief.audioStrategy = "mute"` 必须真正控制合成请求。
 *
 * 仓库没有 @testing-library/react，沿用既有「纯函数单测 + 源码契约」风格
 * （见 src/lib/__tests__/project-detail-brief.test.ts）：判定抽成纯函数直接单测，
 * 页面本身只断言关键接线不会消失。
 */
const videoPage = readFileSync(resolve(process.cwd(), "src/app/project/[id]/video/page.tsx"), "utf8");

describe("resolveVoiceoverRequest：策略 → 是否启用 TTS", () => {
  it("mute：不启用 TTS（合成请求不携带 ttsConfig / freeTts）", () => {
    expect(resolveVoiceoverRequest({ audioStrategy: "mute", manualTtsEnabled: null })).toEqual({
      ttsEnabled: false,
      source: "strategy",
    });
  });

  it("native-audio：不生成 TTS，人声由模型整片产出", () => {
    expect(resolveVoiceoverRequest({ audioStrategy: "native-audio", manualTtsEnabled: null })).toEqual({
      ttsEnabled: false,
      source: "strategy",
    });
  });

  it("volcengine-tts：保持既有行为（启用 TTS）", () => {
    expect(resolveVoiceoverRequest({ audioStrategy: "volcengine-tts", manualTtsEnabled: null })).toEqual({
      ttsEnabled: true,
      source: "strategy",
    });
  });

  it("旧项目（简报为 null）走既有默认：无 voice 阶段记录时为 true", () => {
    expect(resolveVoiceoverRequest({ audioStrategy: null, manualTtsEnabled: null })).toEqual({
      ttsEnabled: true,
      source: "legacy",
    });
    expect(
      resolveVoiceoverRequest({ audioStrategy: null, manualTtsEnabled: null, legacyWorkflowVoiceEnabled: null })
    ).toEqual({ ttsEnabled: true, source: "legacy" });
  });

  it("旧项目沿用 productionWorkflow.voice 阶段的既有行为，不回归", () => {
    expect(
      resolveVoiceoverRequest({ audioStrategy: null, manualTtsEnabled: null, legacyWorkflowVoiceEnabled: false })
    ).toEqual({ ttsEnabled: false, source: "legacy" });
    expect(
      resolveVoiceoverRequest({ audioStrategy: null, manualTtsEnabled: null, legacyWorkflowVoiceEnabled: true })
    ).toEqual({ ttsEnabled: true, source: "legacy" });
  });

  it("手动覆盖优先于策略默认，且来源标记为 manual", () => {
    // 静音项目手动开启配音
    expect(resolveVoiceoverRequest({ audioStrategy: "mute", manualTtsEnabled: true })).toEqual({
      ttsEnabled: true,
      source: "manual",
    });
    // 火山 TTS 项目手动关闭配音
    expect(resolveVoiceoverRequest({ audioStrategy: "volcengine-tts", manualTtsEnabled: false })).toEqual({
      ttsEnabled: false,
      source: "manual",
    });
    // 旧项目手动关闭配音
    expect(
      resolveVoiceoverRequest({ audioStrategy: null, manualTtsEnabled: false, legacyWorkflowVoiceEnabled: true })
    ).toEqual({ ttsEnabled: false, source: "manual" });
  });

  it("三种策略都有界面文案", () => {
    expect(AUDIO_STRATEGY_LABELS.mute).toBe("静音");
    expect(AUDIO_STRATEGY_LABELS["native-audio"]).toBe("模型原生音频");
    expect(AUDIO_STRATEGY_LABELS["volcengine-tts"]).toContain("火山");
  });
});

describe("视频页接线（源码契约）", () => {
  it("读 creationBrief.audioStrategy 并交给纯判定，而不是只看 productionWorkflow.voice", () => {
    expect(videoPage).toMatch(/from "@\/lib\/voiceover-strategy"/);
    expect(videoPage).toMatch(/creationBrief\?\.audioStrategy/);
    expect(videoPage).toMatch(
      /resolveVoiceoverRequest\(\{[\s\S]{0,200}?audioStrategy,[\s\S]{0,200}?manualTtsEnabled: ttsOverride/
    );
  });

  it("合成请求按判定结果决定是否携带 TTS 配置，不再直接看 config.ttsEnabled", () => {
    expect(videoPage).not.toMatch(/config\.ttsEnabled/);
    // 矩阵批量合成 + 单次合成两处付费 TTS 都要被判定门控
    expect(videoPage.match(/\.\.\.\(ttsEnabled && paidTtsReady && \{/g) ?? []).toHaveLength(2);
    // 两处免费 Edge 回退同理
    expect(videoPage.match(/\.\.\.\(ttsEnabled && !paidTtsReady && \{/g) ?? []).toHaveLength(2);
  });

  it("界面说明本次音频策略，并区分策略默认与手动覆盖", () => {
    expect(videoPage).toMatch(/本次音频策略：/);
    expect(videoPage).toMatch(/AUDIO_STRATEGY_LABELS/);
    expect(videoPage).toMatch(/data-voiceover-source=\{voiceover\.source\}/);
    expect(videoPage).toMatch(/setTtsOverride\(null\)/);
  });
});

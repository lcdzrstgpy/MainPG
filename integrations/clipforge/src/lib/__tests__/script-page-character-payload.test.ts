// @vitest-environment node
/**
 * 脚本页「重新生成脚本」的载荷回归：
 *
 * - 带货分支必须把已绑定主播（同一份 presenter 解析）作为 character 发出；
 * - 页面只能有一处角色解析来源（useCharacterStore / resolveScriptCharacter / presenterLib.find 各 1 处）；
 * - topic 分支仍只经 buildTopicScriptRequest，payload 是五键契约且不含 character（本次不改主题链路）。
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildTopicScriptRequest } from "@/lib/creation-submit";
import { DEFAULT_CREATION_BRIEF } from "@/lib/creation-brief";

const page = readFileSync(new URL("../../app/project/[id]/script/page.tsx", import.meta.url), "utf8");

/** 取 handleGenerate 内部「构造 payload → 发请求」之间的那段源码。 */
const payloadRegion = (() => {
  const start = page.indexOf("const payload = isTopic");
  const end = page.indexOf("const res = await fetch(endpoint", start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  return page.slice(start, end);
})();

describe("脚本页 × 带货分支 character", () => {
  it("复用同一个 presenter 解析结果构造 character，并在带货分支条件展开", () => {
    expect(page).toContain('import { scriptCharacterFrom } from "@/lib/script-character"');
    expect(page).toContain("const character = scriptCharacterFrom(presenter);");
    expect(payloadRegion).toContain("...(character && { character })");
  });

  it("页面只有一处角色解析来源（项目记录 → 创作简报 → URL 兜底）", () => {
    const count = (pattern: RegExp) => page.match(pattern)?.length ?? 0;
    expect(count(/useCharacterStore\(\)/g)).toBe(1);
    expect(count(/=\s*resolveScriptCharacter\(/g)).toBe(1);
    expect(count(/presenterLib\.find\(/g)).toBe(1);
  });

  it("character 只出现在带货分支，topic 分支不受影响", () => {
    const topicIdx = payloadRegion.indexOf("buildTopicScriptRequest({");
    const commerceIdx = payloadRegion.search(/^\s*: \{$/m);
    const characterIdx = payloadRegion.indexOf("...(character && { character })");

    expect(topicIdx).toBeGreaterThan(-1);
    expect(commerceIdx).toBeGreaterThan(topicIdx);
    expect(characterIdx).toBeGreaterThan(commerceIdx);
  });
});

describe("脚本页 × topic 分支载荷契约", () => {
  it("buildTopicScriptRequest 输出仍是五键且不含 character", () => {
    const payload = buildTopicScriptRequest({
      projectId: "project-1",
      topic: "加班到深夜的上班族",
      brief: { ...DEFAULT_CREATION_BRIEF, inputMode: "topic", targetDuration: 60, styleType: "lifestyle" },
      llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
    });
    expect(Object.keys(payload).sort()).toEqual(["llmConfig", "narrationStyle", "projectId", "targetDuration", "topic"]);
    expect("character" in payload).toBe(false);
    expect(payload.targetDuration).toBe(60);
    expect(payload.narrationStyle).toBe("lifestyle");
  });

  it("topic 分支的 payload 直接来自构造器（页面不放 character 常量）", () => {
    const topicBranch = payloadRegion.slice(0, payloadRegion.search(/^\s*: \{$/m));
    expect(topicBranch).toContain("buildTopicScriptRequest({");
    expect(topicBranch).not.toContain("character");
  });
});

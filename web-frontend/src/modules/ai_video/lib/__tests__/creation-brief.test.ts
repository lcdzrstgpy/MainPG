import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_CREATION_BRIEF,
  assertStrategyWorkflowConsistency,
  buildWorkflowPlanForStrategy,
  isOutputStrategy,
  sanitizeCreationBrief,
  type OutputStrategy,
} from "../creation-brief.ts";

const stage = (strategy: OutputStrategy, id: ReturnType<typeof buildWorkflowPlanForStrategy>[number]["id"]) =>
  buildWorkflowPlanForStrategy(strategy).find((row) => row.id === id);

test("sanitizeCreationBrief fills every missing field with the documented default", () => {
  assert.deepEqual(sanitizeCreationBrief(undefined), DEFAULT_CREATION_BRIEF);
  assert.deepEqual(sanitizeCreationBrief(null), DEFAULT_CREATION_BRIEF);
  assert.deepEqual(sanitizeCreationBrief({}), DEFAULT_CREATION_BRIEF);
  assert.deepEqual(DEFAULT_CREATION_BRIEF, {
    version: 1,
    inputMode: "upload",
    targetDuration: 30,
    styleType: "",
    styleSource: "explicit",
    targetAudience: [],
    platforms: ["douyin"],
    outputStrategy: "native-film",
    audioStrategy: "native-audio",
  });
});

test("out-of-contract input falls back to safe enum values instead of throwing", () => {
  const brief = sanitizeCreationBrief({
    version: 99,
    inputMode: "telepathy",
    targetDuration: 42,
    styleSource: "guesswork",
    outputStrategy: "hologram",
    audioStrategy: "scream",
    platforms: "douyin",
    narrative: "not-an-object",
  });
  assert.deepEqual(brief, DEFAULT_CREATION_BRIEF);
});

test("narrative is optional and only keeps keys that carry content", () => {
  assert.equal(sanitizeCreationBrief({ styleType: "scene" }).narrative, undefined);
  assert.equal(sanitizeCreationBrief({ narrative: { tone: "  " } }).narrative, undefined);
  assert.deepEqual(sanitizeCreationBrief({ narrative: { situation: "  加班到深夜  ", tone: "共情" } }).narrative, {
    situation: "加班到深夜",
    tone: "共情",
  });
});

test("list fields are cleaned and de-duplicated, and platforms never end up empty", () => {
  const brief = sanitizeCreationBrief({
    targetAudience: ["宝妈", " 宝妈 ", 42, "", "学生党", null],
    platforms: ["douyin", "douyin", "  ", "xiaohongshu"],
    priceRange: "  ¥39.9 ",
    usageAdvantage: "3 秒出泡",
  });
  assert.deepEqual(brief.targetAudience, ["宝妈", "学生党"]);
  assert.deepEqual(brief.platforms, ["douyin", "xiaohongshu"]);
  assert.equal(brief.priceRange, "¥39.9");
  assert.equal(brief.usageAdvantage, "3 秒出泡");
  assert.deepEqual(sanitizeCreationBrief({ platforms: [] }).platforms, ["douyin"]);
});

test("a missing audio strategy follows the normalized output strategy", () => {
  // 原生整片 = 模型自带音轨；其余策略 = 火山语音
  assert.equal(sanitizeCreationBrief({ outputStrategy: "native-film" }).audioStrategy, "native-audio");
  assert.equal(sanitizeCreationBrief({ outputStrategy: "draft" }).audioStrategy, "volcengine-tts");
  assert.equal(
    sanitizeCreationBrief({ outputStrategy: "controlled-motion" }).audioStrategy,
    "volcengine-tts"
  );
  // 显式音源不被默认表覆盖
  assert.equal(
    sanitizeCreationBrief({ outputStrategy: "draft", audioStrategy: "mute" }).audioStrategy,
    "mute"
  );
});

test("only the three output strategies are recognised", () => {
  assert.equal(isOutputStrategy("draft"), true);
  assert.equal(isOutputStrategy("controlled-motion"), true);
  assert.equal(isOutputStrategy("native-film"), true);
  assert.equal(isOutputStrategy("auto"), false);
  assert.equal(isOutputStrategy(undefined), false);
});

test("each strategy maps onto the expected workflow stages", () => {
  assert.equal(stage("draft", "motion")?.enabled, false);
  assert.equal(stage("draft", "keyframes")?.billing, "free");
  assert.equal(stage("draft", "voice")?.enabled, true);
  assert.equal(stage("draft", "compose")?.enabled, true);

  assert.deepEqual(stage("controlled-motion", "keyframes"), {
    id: "keyframes",
    enabled: true,
    execution: "client",
    billing: "paid",
    dependsOn: ["judge"],
  });
  assert.deepEqual(stage("controlled-motion", "motion"), {
    id: "motion",
    enabled: true,
    execution: "client",
    billing: "paid",
    dependsOn: ["keyframes"],
  });

  assert.equal(stage("native-film", "motion")?.enabled, true);
  assert.equal(stage("native-film", "voice")?.enabled, false);
  assert.equal(stage("native-film", "voice")?.reason, "native-audio");
  assert.equal(stage("native-film", "compose")?.enabled, true);
});

test("strategy and workflow consistency accepts the canonical plans", () => {
  for (const strategy of ["draft", "controlled-motion", "native-film"] as const) {
    const brief = sanitizeCreationBrief({ outputStrategy: strategy });
    assert.equal(assertStrategyWorkflowConsistency(brief, buildWorkflowPlanForStrategy(strategy)), null);
  }
});

test("strategy and workflow consistency rejects a downgraded paid path", () => {
  const controlled = sanitizeCreationBrief({ outputStrategy: "controlled-motion" });
  const noMotion = buildWorkflowPlanForStrategy("controlled-motion").map((row) =>
    row.id === "motion" ? { ...row, enabled: false } : row
  );
  assert.equal(
    assertStrategyWorkflowConsistency(controlled, noMotion),
    "出片策略 controlled-motion 要求启用 motion 阶段，但提交的工作流把 motion 设为 disabled"
  );

  const film = sanitizeCreationBrief({ outputStrategy: "native-film" });
  const noCompose = buildWorkflowPlanForStrategy("native-film").map((row) =>
    row.id === "compose" ? { ...row, enabled: false } : row
  );
  assert.equal(
    assertStrategyWorkflowConsistency(film, noCompose),
    "出片策略 native-film 要求启用 compose 阶段，但提交的工作流把 compose 设为 disabled"
  );
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_MIN_SAMPLE_SIZE,
  SCRIPT_STYLE_ALIASES,
  SCRIPT_STYLE_VALUES,
  resolveScriptStyle,
} from "../script-style.ts";
import { STYLE_VALUES } from "../style-values.ts";

test("script style whitelist derives from the ad-template whitelist without auto", () => {
  assert.deepEqual(SCRIPT_STYLE_VALUES, [...STYLE_VALUES].filter((value) => value !== "auto"));
  assert.ok(SCRIPT_STYLE_VALUES.includes("pain-point"));
  assert.ok(SCRIPT_STYLE_VALUES.includes("scenario"));
  assert.ok(!SCRIPT_STYLE_VALUES.includes("auto"));
  assert.equal(DEFAULT_MIN_SAMPLE_SIZE, 3);
});

test("an explicit selectable style resolves as explicit", () => {
  assert.deepEqual(resolveScriptStyle({ requestedStyle: "drama" }), {
    kind: "resolved",
    styleType: "drama",
    styleSource: "explicit",
  });
});

test("a template-supplied style keeps the template source", () => {
  assert.deepEqual(resolveScriptStyle({ requestedStyle: "comparison", styleSource: "template" }), {
    kind: "resolved",
    styleType: "comparison",
    styleSource: "template",
  });
});

test("historical insights never override an explicit choice", () => {
  assert.deepEqual(
    resolveScriptStyle({ requestedStyle: "story", insights: { topStyle: "drama", sampleSize: 999 } }),
    { kind: "resolved", styleType: "story", styleSource: "explicit" }
  );
});

test("auto without data asks the creator to choose instead of guessing", () => {
  for (const requestedStyle of ["auto", "", undefined]) {
    const result = resolveScriptStyle({ requestedStyle, insights: null });
    assert.equal(result.kind, "needs-explicit-choice");
    if (result.kind === "needs-explicit-choice") {
      assert.equal(result.reason, "no-data");
      assert.deepEqual(result.candidates, [...SCRIPT_STYLE_VALUES]);
    }
  }
});

test("an unknown style reports unknown-style with the candidate whitelist", () => {
  const result = resolveScriptStyle({ requestedStyle: "banana-style" });
  assert.equal(result.kind, "needs-explicit-choice");
  if (result.kind === "needs-explicit-choice") {
    assert.equal(result.reason, "unknown-style");
    assert.deepEqual(result.candidates, [...SCRIPT_STYLE_VALUES]);
  }
});

test("auto plus a valid topStyle with enough samples resolves as performance recommendation", () => {
  assert.deepEqual(
    resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "drama", sampleSize: 3 } }),
    { kind: "resolved", styleType: "drama", styleSource: "performance-recommendation" }
  );
});

test("auto with too few samples still asks for an explicit choice", () => {
  for (const insights of [
    { topStyle: "drama", sampleSize: 1 },
    { topStyle: "drama" },
    { topStyle: null, sampleSize: null },
  ]) {
    const result = resolveScriptStyle({ requestedStyle: "auto", insights });
    assert.equal(result.kind, "needs-explicit-choice");
    if (result.kind === "needs-explicit-choice") assert.equal(result.reason, "no-data");
  }
});

test("minSampleSize can lower the recommendation threshold", () => {
  assert.deepEqual(
    resolveScriptStyle({
      requestedStyle: "auto",
      insights: { topStyle: "story", sampleSize: 1 },
      minSampleSize: 1,
    }),
    { kind: "resolved", styleType: "story", styleSource: "performance-recommendation" }
  );
});

test("engine-vocabulary history aliases map onto selectable UI styles", () => {
  assert.deepEqual(SCRIPT_STYLE_ALIASES, { pain_point: "pain-point", scene: "scenario" });
  assert.deepEqual(
    resolveScriptStyle({ requestedStyle: "auto", insights: { topStyle: "pain_point", sampleSize: 3 } }),
    { kind: "resolved", styleType: "pain-point", styleSource: "performance-recommendation" }
  );
  assert.deepEqual(resolveScriptStyle({ requestedStyle: "SCENE" }), {
    kind: "resolved",
    styleType: "scenario",
    styleSource: "explicit",
  });
});

test("hostile input never throws and always lands in a documented branch", () => {
  const hostileInputs: unknown[] = [null, 42, true, {}, [], { requestedStyle: {} }, { insights: "nope" }];
  for (const requestedStyle of hostileInputs) {
    const result = resolveScriptStyle({ requestedStyle });
    assert.ok(["resolved", "needs-explicit-choice"].includes(result.kind));
  }
});

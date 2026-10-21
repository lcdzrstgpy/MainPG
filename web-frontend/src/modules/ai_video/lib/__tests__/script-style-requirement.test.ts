import assert from "node:assert/strict";
import test from "node:test";

import {
  NEEDS_EXPLICIT_STYLE_CODE,
  missingStyleRequirement,
  parseStyleRequirement,
} from "../script-style-requirement.ts";

test("只认 409 + needs_explicit_style，其它状态与错误码都不接管", () => {
  assert.equal(parseStyleRequirement(500, { code: NEEDS_EXPLICIT_STYLE_CODE }), null);
  assert.equal(parseStyleRequirement(409, { code: "other" }), null);
  assert.equal(parseStyleRequirement(409, null), null);
});

test("候选风格来自响应体并去重；没有候选时回落到完整白名单", () => {
  assert.deepEqual(parseStyleRequirement(409, {
    code: NEEDS_EXPLICIT_STYLE_CODE,
    candidates: ["drama", "drama", "scenario"],
  }), { reason: "no-data", candidates: ["drama", "scenario"] });

  const fallback = parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: [] });
  assert.equal(fallback?.reason, "no-data");
  assert.ok((fallback?.candidates.length ?? 0) > 0);
});

test("本地未选风格时给出 missing 与完整候选", () => {
  const requirement = missingStyleRequirement();
  assert.equal(requirement.reason, "missing");
  assert.ok(requirement.candidates.length > 0);
  assert.ok(requirement.candidates.includes("drama"));
  assert.ok(!requirement.candidates.includes("auto"));
});

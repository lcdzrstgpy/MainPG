# VolcEngine Frame/Reference Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Submit valid VolcEngine Seedance image-to-video requests by never mixing native first/last frames with reference media.

**Architecture:** `buildVideoControlPlan` is the single policy layer that chooses native frame fields and optional reference inputs before the request reaches the provider adapter. Change only this policy: a VolcEngine request with either frame keeps the native frame fields and drops the full reference pack, recording an explicit warning. The provider adapter remains unchanged.

**Tech Stack:** TypeScript, Vitest, Next.js 16 application library code.

## Global Constraints

- Do not make model API calls or alter user API settings.
- Preserve the existing Atlas policy and VolcEngine reference-only behavior.
- When a VolcEngine first or last frame exists, omit image, video, and audio reference media.
- Persist the explicit `reference-pack-deferred-for-frames` warning in the sanitized control summary.

---

### Task 1: Encode and verify VolcEngine's mutually exclusive input modes

**Files:**
- Modify: `src/lib/__tests__/video-control-plan.test.ts:72-106`
- Modify: `src/lib/video-control-plan.ts:13-16,120-152,208-211`

**Interfaces:**
- Consumes: `buildVideoControlPlan(input)` from `src/lib/video-control-plan.ts`.
- Produces: a plan that retains `firstFrameUrl`/`lastFrameUrl`, contains no `referenceInputs`, and includes the `reference-pack-deferred-for-frames` warning whenever VolcEngine receives a native frame plus a reference pack.

- [ ] **Step 1: Write the failing test**

Replace the existing VolcEngine frame/reference test with this expectation and add a summary-sanitization assertion:

```ts
it("defers all VolcEngine reference media when native frames are present", () => {
  const plan = buildVideoControlPlan({
    provider: "volcengine",
    modelId: "doubao-seedance-2-0-pro-250528",
    supportsAudio: true,
    firstFrameUrl: "https://e.com/key.png",
    continuityReferenceUrl: "https://e.com/tail.png",
    audioReferenceUrl: "https://e.com/voice.wav",
    locale: "en",
  });

  expect(plan).toMatchObject({
    strategy: "keyframe",
    firstFrameUrl: "https://e.com/key.png",
    referenceInputs: [],
    referenceCount: 1,
    warnings: ["reference-pack-deferred-for-frames"],
  });
});
```

Add a sanitization case that keeps `reference-pack-deferred-for-frames` and still drops an unknown warning.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pnpm test src/lib/__tests__/video-control-plan.test.ts`

Expected: FAIL because the current plan contains VolcEngine reference inputs and labels the request as `reference-pack`.

- [ ] **Step 3: Implement the minimal policy change**

In `src/lib/video-control-plan.ts`, add `reference-pack-deferred-for-frames` to `VideoControlWarning`. Replace the VolcEngine `canAttachAlongsideFrames` branch with a `hasNativeFrame` guard:

```ts
const hasNativeFrame = isNonEmpty(input.firstFrameUrl) || isNonEmpty(input.lastFrameUrl);
const mustDeferVolcReferences = input.provider === "volcengine" && hasNativeFrame && hasVisualPack;

if (mustDeferVolcReferences) warnings.push("reference-pack-deferred-for-frames");
```

Do not append optional visual or audio references when `mustDeferVolcReferences` is true. Keep `firstFrameUrl` and `lastFrameUrl` in the returned plan. Include the new warning in `sanitizeVideoControlSummary`'s allowlist.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pnpm test src/lib/__tests__/video-control-plan.test.ts`

Expected: PASS; Atlas cases remain unchanged, a frame-only VolcEngine request remains `keyframe`, and the new VolcEngine frame/reference case has no reference inputs.

- [ ] **Step 5: Run regression and type checks**

Run: `pnpm test src/lib/__tests__/model-capabilities.test.ts src/lib/__tests__/video-control-plan.test.ts && pnpm lint`

Expected: all selected tests pass and ESLint reports no errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/video-control-plan.ts src/lib/__tests__/video-control-plan.test.ts
git commit -m "fix: defer VolcEngine references for native frames"
```


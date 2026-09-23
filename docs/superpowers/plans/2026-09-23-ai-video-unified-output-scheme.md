# AI 视频统一出片方案 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前重叠的“智能生产方案 / 出片策略”合并为五种项目级出片方案；新建默认原生整片，脚本页只留查看脚本与一键出片。

**Architecture:** 在既有 `projects.creationBrief` JSON 中增加经清洗的 `outputScheme` 完整快照。创建表单只选择该方案；生成页以项目快照覆盖全局渲染参数，旧项目才回退旧策略和全局设置。

**Tech Stack:** Next.js App Router、TypeScript、React、Zustand、Vitest、Drizzle/SQLite、FFmpeg。

## Global Constraints

- 方案固定为 `draft`、`controlled-rapid`、`controlled-balanced`、`controlled-cinematic`、`native-film`；新项目默认 `native-film`。
- 原生整片强制模型原生音频；不得调用火山 TTS 或逐镜 I2V。
- 火山 TTS 保留，仅允许免费草稿和导演动态选择。
- 项目快照优先于全局生成设置；不存在简报的历史项目才允许回退全局设置。
- 开始生成只创建项目并写脚本；一键出片才提交对应媒体任务。
- 不批量重写历史项目；所有新增行为先写失败测试。

---

## File Structure

- `integrations/clipforge/src/lib/output-schemes.ts`（新建）：五个方案、快照清洗、策略/音频派生、项目级生成参数解析。
- `integrations/clipforge/src/lib/creation-brief.ts`：在简报中保存快照并兼容旧策略。
- `integrations/clipforge/src/components/project-creation/output-scheme-panel.tsx`（新建）：创建页内受控五卡选择器。
- `integrations/clipforge/src/components/project-creation/creation-brief-form.tsx`：严格编排表单顺序和方案/音频状态。
- `integrations/clipforge/src/app/api/project/route.ts`、`[id]/route.ts`：持久化/校验快照。
- `integrations/clipforge/src/app/project/[id]/script/page.tsx`：唯一出片分发器与两个动作。
- `integrations/clipforge/src/app/project/[id]/assets/page.tsx`：导演动态读取项目快照。

### Task 1: 定义五种方案与项目快照

**Files:**
- Create: `integrations/clipforge/src/lib/output-schemes.ts`
- Modify: `integrations/clipforge/src/lib/creation-brief.ts`
- Create: `integrations/clipforge/src/lib/__tests__/output-schemes.test.ts`
- Modify: `integrations/clipforge/src/lib/__tests__/creation-brief.test.ts`

**Interfaces:**

```ts
export type OutputSchemeId =
  | "draft" | "controlled-rapid" | "controlled-balanced"
  | "controlled-cinematic" | "native-film";

export interface OutputSchemeSnapshot {
  id: OutputSchemeId;
  outputStrategy: "draft" | "controlled-motion" | "native-film";
  audioStrategy: "volcengine-tts" | "native-audio" | "mute";
  resolution: "720p" | "1080p";
  shotDuration: number;
  motionStrength: number;
  motionIntensity: "subtle" | "normal" | "strong";
  motionRealism: "constraints" | "auto" | "off";
  chainMode: "pin" | "tail" | "off";
  visualLook: string;
}
export function sanitizeOutputSchemeSnapshot(value: unknown): OutputSchemeSnapshot;
export function projectGenerationSettings(brief: CreationBrief | null, global: ProjectGenerationGlobals): ProjectGenerationSettings;
```

- [ ] **Step 1: Write the failing tests**

```ts
it("defaults to native film with native audio", () => {
  expect(sanitizeCreationBrief({})).toMatchObject({
    outputScheme: { id: "native-film", audioStrategy: "native-audio" },
    outputStrategy: "native-film", audioStrategy: "native-audio",
  });
});
it("sanitizes native film to native audio and removes unknown fields", () => {
  const snapshot = sanitizeOutputSchemeSnapshot({ id: "native-film", audioStrategy: "volcengine-tts", apiKey: "x" });
  expect(snapshot).toMatchObject({ id: "native-film", audioStrategy: "native-audio" });
  expect(snapshot).not.toHaveProperty("apiKey");
});
it.each(["draft", "controlled-rapid", "controlled-balanced", "controlled-cinematic", "native-film"] as const)(
  "has a valid snapshot for %s", (id) => expect(sanitizeOutputSchemeSnapshot({ id })).toMatchObject({ id }),
);
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/output-schemes.test.ts src/lib/__tests__/creation-brief.test.ts`  
Expected: FAIL because the output-scheme module/field does not exist and the default is `draft`.

- [ ] **Step 3: Implement the source of truth**

Create `OUTPUT_SCHEMES` as five immutable snapshots. Values are: draft has no AI motion; rapid is 720p/4s/subtle/off/none; balanced is 720p/5s/normal/pin/daylight_clean; cinematic is 1080p/8s/strong/tail/studio_product; native-film derives native audio and does not permit TTS. Add `outputScheme` to `CreationBrief`, set `DEFAULT_CREATION_BRIEF` to native-film, and derive its legacy fields from the sanitized snapshot. When reading legacy records without a snapshot, infer `draft`, `controlled-balanced`, or `native-film` from the old strategy/audio pair.

- [ ] **Step 4: Verify tests pass**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/output-schemes.test.ts src/lib/__tests__/creation-brief.test.ts src/lib/__tests__/creation-brief-migration.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add integrations/clipforge/src/lib/output-schemes.ts integrations/clipforge/src/lib/creation-brief.ts integrations/clipforge/src/lib/__tests__/output-schemes.test.ts integrations/clipforge/src/lib/__tests__/creation-brief.test.ts
git commit -m "feat(ai-video): add project output scheme snapshots"
```

### Task 2: 在 API 中保存方案快照与派生工作流

**Files:**
- Modify: `integrations/clipforge/src/app/api/project/route.ts`
- Modify: `integrations/clipforge/src/app/api/project/[id]/route.ts`
- Create: `integrations/clipforge/src/lib/__tests__/project-output-scheme-api.test.ts`
- Modify: `integrations/clipforge/src/lib/__tests__/project-creation-contract.test.ts`

**Interfaces:** `POST/PATCH /api/project` 永远从 `sanitizeCreationBrief` 取得快照；workflow 只由快照派生的 `outputStrategy` 建立。

- [ ] **Step 1: Write the failing API tests**

```ts
it("persists a sanitized native-film snapshot", async () => {
  const body = await createProject({ creationBrief: { outputScheme: { id: "native-film", audioStrategy: "volcengine-tts", unsafe: true } } });
  expect(body.creationBrief).toMatchObject({ outputScheme: { id: "native-film", audioStrategy: "native-audio" } });
  expect(body.creationBrief.outputScheme).not.toHaveProperty("unsafe");
});
it("derives a motion workflow for controlled-balanced", async () => {
  const body = await createProject({ creationBrief: { outputScheme: { id: "controlled-balanced" } } });
  expect(body.productionWorkflow.find((stage: { id: string }) => stage.id === "motion")).toMatchObject({ enabled: true });
});
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/project-output-scheme-api.test.ts src/lib/__tests__/project-creation-contract.test.ts`  
Expected: FAIL because raw `outputScheme` is not persisted.

- [ ] **Step 3: Implement API sanitization**

Continue using the existing `creationBrief` JSON column; do not add a migration. In POST and PATCH, sanitize once, store that value, build or validate `productionWorkflow` using `creationBrief.outputStrategy`, and only log fields from the sanitized result.

- [ ] **Step 4: Verify the tests pass**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/project-output-scheme-api.test.ts src/lib/__tests__/project-creation-contract.test.ts src/lib/__tests__/creation-brief-migration.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add integrations/clipforge/src/app/api/project/route.ts integrations/clipforge/src/app/api/project/'[id]'/route.ts integrations/clipforge/src/lib/__tests__/project-output-scheme-api.test.ts integrations/clipforge/src/lib/__tests__/project-creation-contract.test.ts
git commit -m "feat(ai-video): persist output scheme per project"
```

### Task 3: 创建表单改为单一五卡方案

**Files:**
- Create: `integrations/clipforge/src/components/project-creation/output-scheme-panel.tsx`
- Modify: `integrations/clipforge/src/components/project-creation/creation-brief-form.tsx`
- Modify: `integrations/clipforge/src/components/project-creation/creation-brief-defaults.ts`
- Modify: `integrations/clipforge/src/app/start/page.tsx`
- Modify: `integrations/clipforge/src/components/project-creation/__tests__/creation-brief-form.test.ts`
- Modify: `integrations/clipforge/src/components/project-creation/__tests__/creation-brief-components.test.ts`
- Create: `integrations/clipforge/src/components/project-creation/__tests__/output-scheme-panel.test.ts`

**Interfaces:**

```tsx
export function OutputSchemePanel(props: {
  value: OutputSchemeSnapshot;
  onChange: (value: OutputSchemeSnapshot) => void;
  disabled?: boolean;
}): JSX.Element;
```

- [ ] **Step 1: Write the failing UI tests**

```ts
it("does not require an extra strategy click for a default native project", () => {
  expect(validateCreationBriefForm({ productName: "杯子", images: [image], inputMode: "upload" })).toEqual([]);
});
it("orders the controlled scheme panel after visual controls and before audio", () => {
  const source = readFileSync(FORM_PATH, "utf8");
  expect(source.indexOf("<VisualControlPanel")).toBeLessThan(source.indexOf("<OutputSchemePanel"));
  expect(source.indexOf("<OutputSchemePanel")).toBeLessThan(source.indexOf("音频策略"));
});
it("locks native film to model-native audio", () => {
  render(<OutputSchemePanel value={nativeScheme} onChange={vi.fn()} />);
  expect(screen.getByText("模型原生音频")).toBeVisible();
  expect(screen.queryByText("火山语音配音（TTS）")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/components/project-creation/__tests__/output-scheme-panel.test.ts src/components/project-creation/__tests__/creation-brief-form.test.ts src/components/project-creation/__tests__/creation-brief-components.test.ts`  
Expected: FAIL because the old output strategy/`strategyChosen` implementation remains.

- [ ] **Step 3: Implement strict form order**

Keep the form order as input source, product info, narrative, delivery/visual constraints, then `OutputSchemePanel`, audio and submit. Remove `strategyChosen` from validation/state/form DTO. `OutputSchemePanel` must have exactly five cards and explain exact chains. `native-film` shows a locked “模型原生音频” summary; draft/director shows selectable TTS or mute. Remove the external `ProductionProfilePicker` rendering from `/start`; it must not call `applyProductionProfile` during creation.

- [ ] **Step 4: Verify the tests pass**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/components/project-creation/__tests__/output-scheme-panel.test.ts src/components/project-creation/__tests__/creation-brief-form.test.ts src/components/project-creation/__tests__/creation-brief-components.test.ts src/components/project-creation/__tests__/creation-brief-defaults.test.ts src/lib/__tests__/creation-entry-prefill.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add integrations/clipforge/src/components/project-creation integrations/clipforge/src/app/start/page.tsx
git commit -m "feat(ai-video): unify creation output scheme selection"
```

### Task 4: 生成请求优先使用项目快照

**Files:**
- Modify: `integrations/clipforge/src/lib/output-schemes.ts`
- Modify: `integrations/clipforge/src/app/project/[id]/script/page.tsx`
- Modify: `integrations/clipforge/src/app/project/[id]/assets/page.tsx`
- Create: `integrations/clipforge/src/lib/__tests__/project-generation-settings.test.ts`
- Modify: `integrations/clipforge/src/lib/__tests__/script-page-strategy.test.ts`
- Modify: `integrations/clipforge/src/lib/__tests__/assets-view.test.ts`

**Interfaces:** `projectGenerationSettings(brief, global)` returns image/video parameters, motion intensity/realism, chain mode and visual look. Provider, model IDs and credentials remain current authorized settings and are not persisted in snapshots.

- [ ] **Step 1: Write the failing precedence tests**

```ts
it("keeps a cinematic project stable after global settings change", () => {
  const brief = sanitizeCreationBrief({ outputScheme: { id: "controlled-cinematic" } });
  expect(projectGenerationSettings(brief, rapidGlobals)).toEqual(projectGenerationSettings(brief, balancedGlobals));
  expect(projectGenerationSettings(brief, rapidGlobals).videoParams).toMatchObject({ resolution: "1080p", duration: 8, motionStrength: 0.72 });
});
it("uses global parameters only for legacy projects without a brief", () => {
  expect(projectGenerationSettings(null, rapidGlobals)).toEqual(rapidGlobals);
});
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/project-generation-settings.test.ts src/lib/__tests__/script-page-strategy.test.ts src/lib/__tests__/assets-view.test.ts`  
Expected: FAIL because execution pages still read raw global `imageParams`, `videoParams`, `chainMode`, and `visualLook`.

- [ ] **Step 3: Implement project-first resolution**

Make the resolver merge global aspect/model-neutral defaults with the project snapshot’s resolution, duration, strength, intensity, realism, chain mode and look. In `script/page.tsx`, use it for character sheet, storyboard-grid and storyboard-film request options. In `assets/page.tsx`, use it for keyframes, I2V, preflight and look/chain decisions. A no-brief legacy project retains exact current global behavior.

- [ ] **Step 4: Verify the tests pass**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/project-generation-settings.test.ts src/lib/__tests__/script-page-strategy.test.ts src/lib/__tests__/assets-view.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add integrations/clipforge/src/lib/output-schemes.ts integrations/clipforge/src/app/project/'[id]'/script/page.tsx integrations/clipforge/src/app/project/'[id]'/assets/page.tsx integrations/clipforge/src/lib/__tests__/project-generation-settings.test.ts integrations/clipforge/src/lib/__tests__/script-page-strategy.test.ts integrations/clipforge/src/lib/__tests__/assets-view.test.ts
git commit -m "fix(ai-video): honor project output scheme during generation"
```

### Task 5: 脚本页只保留查看脚本与一键出片

**Files:**
- Modify: `integrations/clipforge/src/app/project/[id]/script/page.tsx`
- Create: `integrations/clipforge/src/lib/__tests__/script-page-output-scheme.test.ts`
- Modify: `integrations/clipforge/src/lib/__tests__/script-page-strategy.test.ts`

**Interfaces:** `startOutputScheme(): Promise<void>` reads only `creationBrief.outputScheme.id`; it dispatches `draft` to `startPipeline`, each `controlled-*` to `/assets`, and `native-film` to the existing native preflight/confirmation.

- [ ] **Step 1: Write the failing dispatcher tests**

```ts
it.each([
  ["draft", "startPipeline"],
  ["controlled-balanced", "router.push(`/project/${id}/assets`)"],
  ["native-film", "runAiFilm"],
] as const)("has one output path for %s", (scheme, call) => expect(dispatchSource(scheme)).toContain(call));
it("does not render competing completion CTAs", () => {
  const source = readFileSync(SCRIPT_PAGE, "utf8");
  expect(source).not.toContain("免费快剪成片");
  expect(source).not.toContain("AI 生成成片");
  expect(source).not.toContain("出片策略：原生整片");
  expect(source).toContain("一键出片");
});
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/script-page-output-scheme.test.ts src/lib/__tests__/script-page-strategy.test.ts`  
Expected: FAIL because the old strategy cards and paired `autoFinish`/`runAiFilm` CTAs remain.

- [ ] **Step 3: Implement one dispatcher**

Replace strategy panels and competing buttons with “查看脚本” and “一键出片”. Keep native cost preview as the confirmation stage inside `runAiFilm`, not another strategy entry. New projects never auto-start draft via `?auto=1`; only old projects without `creationBrief` retain resume compatibility. Display the selected scheme and live stage/model/cost messaging while the single action runs.

- [ ] **Step 4: Verify the tests pass**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/script-page-output-scheme.test.ts src/lib/__tests__/script-page-strategy.test.ts src/lib/__tests__/script-page-topic-regen.test.ts src/lib/__tests__/script-page-character-payload.test.ts`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add integrations/clipforge/src/app/project/'[id]'/script/page.tsx integrations/clipforge/src/lib/__tests__/script-page-output-scheme.test.ts integrations/clipforge/src/lib/__tests__/script-page-strategy.test.ts
git commit -m "feat(ai-video): dispatch one output scheme from script"
```

### Task 6: 收敛导演入口、文档与全量验证

**Files:**
- Modify: `integrations/clipforge/src/app/project/[id]/assets/page.tsx`
- Modify: `integrations/clipforge/src/app/project/[id]/production/page.tsx`
- Modify: `integrations/clipforge/src/lib/__tests__/assets-view.test.ts`
- Modify: `docs/操作答疑FAQ清单.md`

- [ ] **Step 1: Write failing visibility tests**

```ts
it("makes per-shot generation the primary action only for controlled schemes", () => {
  expect(assetPageMode("controlled-balanced").primaryAction).toBe("generate-shots");
  expect(assetPageMode("draft").primaryAction).not.toBe("generate-shots");
  expect(assetPageMode("native-film").primaryAction).not.toBe("generate-shots");
});
```

- [ ] **Step 2: Verify the tests fail**

Run: `cd integrations/clipforge && ./node_modules/.bin/vitest run src/lib/__tests__/assets-view.test.ts`  
Expected: FAIL because asset controls do not yet reflect the selected scheme.

- [ ] **Step 3: Implement final guards and documentation**

Render director dynamic controls as the primary assets action only for `controlled-*`. Do not surface those controls as default paths for draft/native. Update the FAQ with five schemes and this exact rule: “原生整片使用模型原生音频，不使用火山 TTS；火山 TTS 仅在免费草稿和导演动态的 TTS 策略中调用。”

- [ ] **Step 4: Run focused and complete verification**

Run:

```bash
cd integrations/clipforge
FFMPEG_PATH="$(pwd)/node_modules/ffmpeg-static/ffmpeg" FFPROBE_PATH="$(pwd)/node_modules/ffprobe-static/bin/darwin/arm64/ffprobe" ./node_modules/.bin/vitest run
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/next build
```

Expected: all test files pass; `tsc` exits 0; production build compiles successfully.

- [ ] **Step 5: Commit**

```bash
git add integrations/clipforge/src/app/project/'[id]'/assets/page.tsx integrations/clipforge/src/app/project/'[id]'/production/page.tsx integrations/clipforge/src/lib/__tests__/assets-view.test.ts docs/操作答疑FAQ清单.md
git commit -m "feat(ai-video): complete unified output scheme flow"
```

## Plan Self-Review

- Spec coverage: Tasks 1–2 create and persist a safe snapshot; Task 3 implements the exact requested form order and single selection; Task 4 prevents global changes from altering projects; Task 5 removes repeated decision UI and dispatches the three actual technical paths; Task 6 constrains the director entry and verifies/document the audio rules.
- Type consistency: `OutputSchemeSnapshot` is defined in Task 1 and is the source passed by the form, API, and generation resolver in all later tasks.
- Compatibility: no table migration or bulk update occurs because `projects.creationBrief` already stores JSON; missing old briefs deliberately retain prior behavior.

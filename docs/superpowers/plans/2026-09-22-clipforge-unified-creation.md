# ClipForge 统一创作入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `integrations/clipforge` 的两套项目创建入口合并为一份「创作简报」，让出片策略显式可选、可解释，并清理用户面的 Atlas 入口。

**Architecture:** 新增项目级 `creationBrief` 数据合同与共享组件目录 `src/components/project-creation/`；工作台 `/start` 成为唯一主创建入口并渲染同一份表单；脚本、素材、视频、导出页读取同一份简报；免费流水线只服务于显式 `draft` 策略。

**Tech Stack:** Next.js 16 + React 19 + TypeScript 5（strict）、Drizzle ORM + SQLite、Vitest。

## 依据

- 现状盘点：`docs/2026-09-22-ClipForge功能入口与流程盘点.md`
- 迁移设计：`docs/superpowers/specs/2026-09-22-clipforge-unified-creation-design.md`

## Global Constraints

- 只改 `integrations/clipforge`（以及必要的 `docs/`）；不改 MainPG 其他业务模块。
- 不重写模型适配器、FFmpeg 合成器；不批量重写历史项目数据。
- 用户界面、默认值、引导、错误文案只出现火山/豆包与速创。
- 不引入新的第三方依赖。
- 每个任务先写失败测试再实现；提交前 `pnpm exec vitest run <相关文件>` 必须全绿。
- 保持既有代码风格：2 空格缩进、strict TS、不使用 `any`（除既有模式）、不顺手重构无关代码。
- 既有测试不得因本次改动而放宽断言。

## 已核实的实现事实（实施时以此为准）

| 事实 | 位置 |
|---|---|
| `auto` 无数据时静默归一化为 `pain_point` | `src/app/api/llm/script/route.ts:87`、`:89`；auto 识别 `:144-146`；历史覆盖 `:177-180` |
| 免费流水线仅三阶段，无生视频 | `src/lib/pipeline-stages.ts:9`；`src/lib/pipeline-runner.ts:97-136` |
| 脚本页靠 URL `?auto=1` 触发免费链 | `src/app/project/[id]/script/page.tsx:347,493-499` |
| `/start` 硬编码 category=`other`、duration=30、images=[]、无条件 `?auto=1` | `src/app/start/page.tsx:379,409,411,423,447,449,457` |
| `/start` 默认 `genMode="free"`，仅 4 个预设 | `src/app/start/page.tsx:74,40-45` |
| `/project/new` 才有受众/平台/价格带/模板/角色 | `src/app/project/new/page.tsx:114-127,91-103` |
| `projects` 已有 `creative_intent`/`visual_bible`/`production_workflow`，无 `creation_brief` | `src/lib/db/schema.ts:48-51` |
| 创建 API 只写 5 个字段且几乎不校验 | `src/app/api/project/route.ts:22-54` |
| 合成不阻断 TTS 失败，警告只进 timeline sidecar | `src/app/api/project/[id]/compose/route.ts:266-286,568-585` |
| Provider 注册表只保留 volcengine / suchuang | `src/lib/providers/index.ts:13-37` |
| Atlas 用户面入口仍在（落地页内联面板等） | `src/app/start/page.tsx:19,66,476-519,801-834` |
| 最新迁移为 0019，下一个编号 0020 | `drizzle/0019_calm_tigra.sql`、`drizzle/meta/_journal.json` |

## 分层决策（对设计文档的少量偏离，已记录理由）

设计文档 §6.1 把 `CreationBrief` 类型放在 `src/components/project-creation/creation-brief-types.ts`。但 `src/lib/db/schema.ts` 与 API 路由也需要该类型，从组件目录反向依赖 `lib` 会造成分层倒置。因此：

- **权威定义放在 `src/lib/creation-brief.ts`**（类型 + 默认值 + 校验 + 归一化）；
- `src/components/project-creation/creation-brief-types.ts` 只做 re-export，供组件层使用。

---

## 阶段 0：观测基线

### Task 0.1 数据合同：`creation_brief` 列 + `project_events` 表 + 简报类型

**Files:**
- Create: `src/lib/creation-brief.ts`
- Modify: `src/lib/db/schema.ts`
- Create: `drizzle/0020_add_creation_brief_and_events.sql`
- Create: `drizzle/meta/0020_snapshot.json`、Modify `drizzle/meta/_journal.json`
- Test: `src/lib/__tests__/creation-brief.test.ts`、`src/lib/__tests__/creation-brief-migration.test.ts`

**Interfaces:**
- `type InputMode = "upload" | "link" | "topic" | "product-library" | "clone"`
- `type OutputStrategy = "draft" | "controlled-motion" | "native-film"`
- `type AudioStrategy = "volcengine-tts" | "native-audio" | "mute"`
- `type StyleSource = "explicit" | "template" | "performance-recommendation"`
- `sanitizeCreationBrief(value: unknown): CreationBrief`（缺失字段补默认值，非法枚举回退到安全值，不抛错）
- `buildWorkflowPlanForStrategy(strategy: OutputStrategy): WorkflowStagePlan[]`（映射设计文档 §5.2 表格）
- 表 `projectEvents`：`id`、`project_id`、`kind`、`payload`(json)、`created_at`

- [ ] 先写 `creation-brief.test.ts`：默认值、非法枚举回退、三种策略的 workflow 映射（`draft` 不含 `motion`，`controlled-motion` 必含 `motion`，`native-film` 不含 `voice`）
- [ ] 运行测试确认失败
- [ ] 实现 `src/lib/creation-brief.ts`
- [ ] 写 `creation-brief-migration.test.ts`：内存 SQLite 跑 `migrate(db, { migrationsFolder: "drizzle" })`，断言新列与新表存在、旧项目该列为 null
- [ ] 生成迁移（`pnpm exec drizzle-kit generate` 或按 0019 格式手工补齐三件套）
- [ ] 运行两个测试文件，确认通过

### Task 0.2 创建事件记录

**Files:**
- Create: `src/lib/creation-analytics.ts`
- Test: `src/lib/__tests__/creation-analytics.test.ts`

**Interfaces:**
- `recordCreationEvent(input: { projectId: string; kind: CreationEventKind; payload?: Record<string, unknown> }): void`（写库失败只记录日志，绝不抛错中断创建）

- [ ] 先写失败测试：写入后可读出、失败不抛错
- [ ] 实现
- [ ] 运行测试

---

## 阶段 1：抽取共享创作简报（行为等价）

### Task 1.1 共享表单与面板

**Files:**
- Create: `src/components/project-creation/creation-brief-types.ts`（re-export）
- Create: `creation-brief-defaults.ts`（无隐式降级的默认值与选项）
- Create: `input-source-panel.tsx`、`narrative-panel.tsx`、`visual-control-panel.tsx`、`output-strategy-panel.tsx`
- Create: `creation-brief-form.tsx`、`creation-brief-summary.tsx`
- Test: `src/components/project-creation/__tests__/creation-brief-form.test.ts`

**Interfaces:**
- `CreationBriefForm({ initial, onSubmit, submitLabel, showAdvanced }: Props)`；`onSubmit(brief: CreationBrief)` 只回传简报，不直接调用模型
- `buildScriptRequest(brief, extra)`：唯一脚本请求体构造器

- [ ] 先写失败测试：从 `/project/new` 的字段集合构造简报，断言 `buildScriptRequest` 与改造前 `/project/new` 的请求体**逐键等价**（含 `priceRange`/`targetAudience`/`platforms`/`usageAdvantage`/`templateId`/`referenceStructure`/`customRequirements`/`character`）
- [ ] 实现组件与请求构造器
- [ ] 运行测试

### Task 1.2 `/project/new` 改用共享组件（行为等价）

**Files:**
- Modify: `src/app/project/new/page.tsx`
- Test: 复用 Task 1.1 的等价性测试

- [ ] 用 `CreationBriefForm` 替换页内表单；保持创建 DTO 与脚本 DTO 与改造前一致
- [ ] e2e `e2e/smoke.spec.ts` 的 `/project/new` 断言仍通过
- [ ] 运行相关测试

---

## 阶段 2：工作台接入共享表单

### Task 2.1 `/start` 成为唯一主创建入口

**Files:**
- Modify: `src/app/start/page.tsx`
- Test: `src/components/project-creation/__tests__/start-entry.test.ts`

- [ ] 删除 `FORM_PRESETS`、`genMode`、`?auto=1` 拼接、`applyAtlasOneKey` 一键面板与本页 Atlas 文案
- [ ] 商品图/链接/主题映射为 `InputMode`；显式选择 `OutputStrategy`
- [ ] 先写失败测试：页面源码不再含 `FORM_PRESETS`/`applyAtlasOneKey`/`auto=1`，且渲染共享表单
- [ ] 实现并运行测试

### Task 2.2 脚本页按策略决定是否自动出片

**Files:**
- Modify: `src/app/project/[id]/script/page.tsx`
- Test: `src/lib/__tests__/pipeline-entry.test.ts`

- [ ] 自动免费链仅当 `creationBrief.outputStrategy === "draft"`；不再读取 URL `auto` 参数启动新项目
- [ ] 旧项目（`creationBrief` 为 null）保留原 `?auto=1` 行为以支持断点恢复
- [ ] 运行测试

---

## 阶段 3：项目详情消费简报

### Task 3.1 简报摘要接入四个页面

**Files:**
- Modify: `src/app/project/[id]/script/page.tsx`、`assets/page.tsx`、`video/page.tsx`、`export/page.tsx`
- Test: `src/components/project-creation/__tests__/creation-brief-summary.test.ts`

- [ ] 显示风格/来源、受众、平台、人物处境、语言语气、出片策略、音频策略
- [ ] 运行测试

---

## 阶段 4：默认值与降级收口

### Task 4.1 移除 `auto → pain_point` 静默回退

**Files:**
- Modify: `src/app/api/llm/script/route.ts`
- Test: `src/lib/__tests__/script-style-resolution.test.ts`

- [ ] 无历史数据时返回“无法推荐”，要求显式风格；`styleSource` 记录 `explicit` / `template` / `performance-recommendation`
- [ ] 运行测试

### Task 4.2 合成逐镜音源与失败可见

**Files:**
- Modify: `src/app/api/project/[id]/compose/route.ts`
- Test: `src/lib/__tests__/compose-voice-report.test.ts`

- [ ] 逐镜记录实际音源（`volcengine` / `edge` / `native` / `none`）与失败原因，写入 sidecar 与合成记录可读字段
- [ ] 默认不静默跳过语音：存在 `tts_failed` 时任务标记待处理
- [ ] 运行测试

---

## 阶段 5：次级入口迁移

### Task 5.1 商品库/主播库/爆款复刻/主题/批量只预填简报

- [ ] 各入口输出预填 `CreationBrief`，不再创建独立语义项目
- [ ] 批量出片复用同一策略与执行器

---

## 阶段 6：清理 Atlas 用户面

### Task 6.1 落地页移除一键接入（随 Task 2.1）
### Task 6.2 设置页与 i18n 去 Atlas
- [ ] `src/app/settings/page.tsx` 移除 Atlas 平台卡与相关文案 key；清理 `start.ts`/`settings.ts` 中 atlas/oneKey 文案
### Task 6.3 错误文案与 CLI/MCP 提示改为火山/速创
- [ ] `script/page.tsx:599`、`clone/page.tsx:748`、`bin/clipforge.mjs`、`mcp/clipforge-mcp.mjs`
### Task 6.4 文档去 Atlas
- [ ] `README.md`/`README.en.md`/`TUTORIAL.md`/`TUTORIAL.en.md`/`docs/*.html`/`mcp/README.md`

---

## 进度与未决事项

### 已完成批次

- **批次 1（77e2a560）**：`creationBrief` 合同与 `sanitizeCreationBrief`、`creation_brief` 列 + `project_events` 表（迁移 0020）、`recordCreationEvent`、逐镜 `voiceReport`（写入合成 sidecar）、设置页/错误文案/CLI/MCP/LLM 预设去 Atlas。
- **批次 2**：`src/components/project-creation/` 共享表单与 `buildScriptRequest`；`src/lib/script-style.ts` 风格解析器（无数据不再回退 `pain_point`）；`POST /api/project` 接受并校验 `creationBrief`/`creativeIntent`/`visualBible`/`productionWorkflow`，`PATCH` 白名单加入 `creationBrief`。

### 未决事项（下一批次必须先决定）

1. **风格词表别名**：`insights.topStyle` 落库的是引擎风格词（`pain_point`、`scene`），而 UI 白名单是 `pain-point`、`scenario`。当前 `resolveScriptStyle` 采严格口径，真实历史数据里这两种风格不会被推荐。接线批次需显式决定归一策略（建议在 `script-style.ts` 内维护一张受控别名表，而不是放宽白名单）。
2. **`/api/llm/script` 接线时机**：`resolveScriptStyle` 已就绪但未接入路由，原因是 `/start`、`/project/new`、批量出片、爆款复刻、MCP/CLI 仍会发送 `auto`；必须先完成 Task 2.1（工作台显式选择风格）与 Task 1.2（新建页接入共享表单），再切换路由，否则默认流程会报错。
3. **创建 API 的兼容边界（已实现）**：只有请求体带 `creationBrief` 时才会写入默认 `productionWorkflow`；无简报的旧调用方保持该列 null，避免素材页「自动生视频」开关被静默改变。
4. **文档去 Atlas（Task 6.4）暂缓**：`README*.md` / `TUTORIAL*.md` / `docs/*.html` 属上游 vendored 内容，改动会影响 AGPL 溯源与后续上游对比，需与维护者确认后再动。

### 批次 3 之后新增的未决事项

5. **`compositions` 表没有策略列**：导出页的「免费草稿 / 受控动态成片 / 原生整片」分组与默认选中因此对 `draft` 与 `controlled-motion` 无法严格成立（只能靠 `label` 粗判，缺失时回退到「最新一条」）。要么给 `compositions` 增加 `strategy` 列，要么在 compose 写入时约定 `label` 前缀。
6. **video 页音频报告取的是「最新一条」合成记录**，不是导出页选中的版本；按 `compositionId` 读取对应 sidecar 需要新增读接口（属 `src/app/api/**`）。
7. **脚本版本保留只有本页会话内快照**：`/api/llm/script` 是先删后插整组替换，跨会话版本需要 API 侧支持（本次未改 API）。
8. **新文案未接 i18n**：`creation-brief-summary` / `style-choice-prompt` / 脚本页新分支等使用硬编码中文，英文界面下仍显示中文。
9. **Task 5.1 次级入口尚未统一**：商品库/主播库/爆款复刻/主题/批量目前只做了「风格不再静默降级」的兼容（clone 改为显式 `scenario`、CLI/MCP/Canvas 有默认值与 409 提示），仍未改为「只预填一份 `CreationBrief`」。
10. **Task 4.2 剩余两项**：`draft` 的付费升级缺二次确认；`tts_failed` 目前只暴露数据（`voiceReport`），尚未默认把任务标记为待处理。

### 批次 3 的真机验证记录（主工作区新构建）

以 `integrations/clipforge/.next/standalone` 起本地服务（127.0.0.1:3210，独立数据目录），浏览器实测：

- `/start?embed=mainpg`：出现「出片策略（创建时选定）」三项，`draft` 文案含「非 AI 动态视频，不计费」；来源含 商品图/商品链接/一句话主题/商品库预填/爆款复刻；页面无任何 Atlas 文案；未配 LLM 时引导「前往设置」而非内联填 Key。
- `/project/new?embed=mainpg`：与 `/start` 同一份表单，步骤为 1 商品图片 / 2 商品名称 / 3 视频模式 / 4 出片策略；无 Atlas 文案。

### 批次 4（功能完整性）与遗留决策

已实现：`compositions.strategy` 列（迁移 0021）与导出页严格分组；按 `compositionId` 读取成片音频报告的读接口（含跨项目/非法 id/路径穿越防护）；次级入口（topic / clone / products / batch）收敛为「只预填一份 `CreationBrief`」，批量出片复用共享脚本请求构造器与显式策略；`/start` 与 `/project/new` 均消费同一套预填参数（`parseStartPrefill` / `parseClonePrefill`）。

维护者已作出的取舍（不再视为未决）：

- **clone 的付费「模型级一键成片复刻」保持移除**：它与「次级入口不创建项目」直接冲突。若产品上仍需该付费能力，应在项目详情（assets / production）内提供，而不是回到 clone 页——需要产品确认后再开任务。
- **批量出片的秒级时长抖动收窄到 15/30/60**：`CreationBrief.targetDuration` 只有三档，抖动被归位到合法档位，变量标签回填的是真实生效值（不显示不会发生的抖动）。
- **topic 页移除「旁白风格 / 目标时长」选择器**：这两个选项无法映射进白名单（旁白风格与脚本风格不是同一套枚举，15/25/40 不在 {15,30,60}），改由主入口简报显式选择。
- **`/products` 的「做视频」统一指向 `/start`**：小白与导演模式同址，单一创建入口。

仍待处理（低优先）：

- `/project/new?entry=topic` 的提交语义未与 `/start` 对齐（主题文本不会映射为商品名/描述）。仓库内已无该深链接的生产者，仅为旧链接兼容。
- 批量出片的 `preferredHookId` 仍是共享构造器之外追加的批专用字段；彻底收口需要给 `buildScriptRequest` 增加该入参。
- `/api/replicate/analyze` 返回的 `modelTierEligible` 已无消费者，可清理。
- `/products` 的「去批量出片」不带商品预选；如需带参需要 `/batch` 接受 `productIds`。

### 环境风险与操作约定（重要）

本轮出现**两次 git 索引被清空**（`git ls-files` 归零、全仓文件被标记为已暂存删除；HEAD 与工作区文件均完好）。触发时机都在多个并行代理同时执行 `drizzle-kit generate` / `vitest` / `tsc` 等命令之后，根因未定位。

- 恢复方式（只动索引、不碰工作区，已验证有效）：`git reset --mixed HEAD`。
- 强制约定：**任何提交前必须先校验**「`git diff --cached --name-status | grep -c '^D'` 必须为 0」且 `git ls-files` 数量正常；本轮的每个批次都是靠这条校验拦住的。
- 建议后续排查：并行代理是否共用同一个 `.git` 索引，或某个工具在写入 `.git/index`。





```bash
cd integrations/clipforge
pnpm exec vitest run                                   # 全量单测
pnpm build                                             # 生产构建
node scripts/prepare-mainpg-sidecar.mjs                # sidecar 准备
```

- [ ] 从 `/start` 与 `/project/new` 创建同一份简报时，创建 DTO 与脚本 DTO 相同
- [ ] 新项目记录同时含 `creationBrief`、`creativeIntent`、`visualBible`、`productionWorkflow`
- [ ] `draft` 不出现「AI 生视频」措辞；`controlled-motion` 无视频素材时不进入主合成
- [ ] 「智能推荐」无历史数据时不产出痛点种草脚本
- [ ] 用户界面与文案不再出现 Atlas
- [ ] 旧项目可打开、导出、断点续跑；sidecar 启动与自动迁移不失败

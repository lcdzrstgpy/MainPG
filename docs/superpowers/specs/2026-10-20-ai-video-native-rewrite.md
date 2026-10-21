# AI 视频模块原生化重构（去掉 iframe）

> 状态：代码完成；自动化回归已完成，剩余手动项交用户执行（上传商品图、参考视频文件选择、付费生视频、ASR）
> 日期：2026-10-20
> 目标目录：`web-frontend/src/modules/ai_video/`（新前端）+ `local-runtime/wh_local/modules/clipforge/`（代理层）
> 执行方式：按本文任务编号逐个开新会话执行；每个任务都自带「开工前必读 / 改动清单 / 接口契约 / 验收标准」，不依赖前序会话的上下文。

---

## 1. 背景与目标

现状：AI 视频模块把整个 ClipForge（Next.js 应用）以 iframe 内嵌在 MainPG 工作台里。
问题：视觉/滚动/交互都是两套体系，标题栏固定、滚动割裂、主题不可控，后续也无法复用 MainPG 的组件与状态。

目标：

1. **彻底移除 iframe**，AI 视频的页面由 `web-frontend` 自己渲染，直接调用 ClipForge sidecar 的 HTTP API。
2. **保留 ClipForge 作为后端**（Node 专属能力不迁）：SQLite、文件落盘、ffmpeg/ffprobe、provider 调用、长任务执行器都留在 sidecar。
3. 视觉与交互统一到 MainPG 现有模块规范（模块标题卡、胶囊输入、主题变量、页签/滚动/返回顶部行为）。
4. 设置与凭据改由 MainPG origin 的 localStorage 承载，解决 sidecar 随机端口导致设置丢失的问题。
5. 渐进替换：每完成一页就切一页，未完成的页面暂时继续走 iframe，任何阶段都可回退。

**非目标（本次明确不做）**

- 不改 ClipForge 的数据库结构与生成/合成算法。
- 不把 ClipForge 接入 MainPG 的积分计费（两套计费目前独立；本重构不新增扣费点）。
- 不重写 `media-lab`、`examples/showcase` 等未挂到 AI 视频侧栏的实验页。
- 不改动 POD 等其他模块。

---

## 2. 关键事实（已盘点，执行时直接采信）

### 2.1 后端

| 项 | 事实 |
| --- | --- |
| 运行方式 | `node <artifact>/server.js`，端口随机，URL 由 `GET /api/clipforge/status` 返回 |
| 存储 | SQLite：`<data_dir>/clipforge/data/sqlite.db`；上传 `<data>/uploads`；成片 `<data>/output` |
| 鉴权 | **无鉴权**，仅 loopback + 随机端口；保护依赖本机边界 |
| CORS | `src/proxy.ts` 放行 `localhost/127.0.0.1/[::1]` 任意端口；仍建议走同源代理（见 3.2） |
| 凭据 | 全部随请求 body 传入（`llmConfig` / `provider+apiKey+baseUrl` / `ttsConfig` / `apiKeys`），服务端不存用户设置 |
| 默认设置 | `clipforge.local.json`（运维下发）由 sidecar 首屏注入；原生前端需要一个新的读取接口 |
| 媒体 | `/api/files/*`（上传）、`/api/output/*`（成片），支持 Range；接口返回相对 URL 为主，个别行含绝对磁盘路径（禁止展示） |
| 长任务 | 见 2.3 轮询矩阵；没有任何 SSE |
| 错误 | 统一 `{ error: string }`，按 `Accept-Language` 本地化 |

### 2.2 前端页面规模（重写对象）

| 页面 | 行数 | 复杂度 |
| --- | --- | --- |
| `/start` 视频工作台 | 965 | 高：共享创作简报表单 + 多来源 + 409 选风格 |
| `/projects` 我的项目 | 316 | 低 |
| `/products` 商品素材 | 779 | 中 |
| `/presenters` 主播素材 | 25 + 组件 | 中 |
| `/project/clone` 爆款复刻 | 658 | 中 |
| `/batch` 任务中心 | 1108 | 高：批量执行器 + 断点续跑 |
| `/settings` 设置 | 1408 | 高：Provider/LLM/TTS/模型/角色/品牌 |
| `/project/[id]/script` | 1833 | 很高 |
| `/project/[id]/assets` | 1766 | 很高 |
| `/project/[id]/video` | 1404 | 高 |
| `/project/[id]/export` | 1083 | 高 |
| `/project/[id]/production` | 548 | 中高 |
| `/project/[id]/transcript` | 919 | 高：浏览器本地 ASR Worker |

### 2.3 轮询矩阵（原样保留）

| 任务 | 触发 | 轮询接口 | 间隔 / 预算 |
| --- | --- | --- | --- |
| 全局任务中心 | 页面常驻 | `GET /api/tasks` | 有活跃 15s / 空闲 30s，单次超时 10s |
| 成片合成 | `POST /api/project/:id/compose` | `GET /api/project/:id/compose?compositionId=` | 3s，页面侧 300s |
| 服务端流水线 | `POST /api/project/:id/pipeline` | `GET /api/project/:id/pipeline` | 2.5s × 312（约 13 分钟） |
| 转写/剪辑渲染 | `POST .../media/:sid/edit` | `GET /api/project/:id/media` | 2.5s |
| 付费视频任务 | `POST /api/ai/video`（超时返回 `taskId+recoverable`） | `GET /api/ai/tasks?projectId&active=1`、`POST /api/ai/video/task` | 5s；只能恢复，禁止重复提交 |
| 批处理 | 页面内执行器 | `GET /api/batch?jobId=` / `?active=1` | 由执行器节奏决定 |

### 2.4 必须留在 sidecar 的能力

better-sqlite3 全部读写、`fs` 落盘与 Range 流、ffmpeg/ffprobe（合成/切帧/九宫格/质检/母版/封面/逐字稿渲染）、provider 网络调用、进程内 pipeline 执行器与转写渲染队列。

浏览器侧**没有** ffmpeg、没有 IndexedDB；唯一 Worker 是 `src/workers/asr.worker.ts`（`@huggingface/transformers`，WebGPU/WASM）。

---

## 3. 目标架构

### 3.1 总览

```
┌──────────────────────── MainPG 工作台（Vite + React, 127.0.0.1:8010）────────────────────────┐
│  modules/ai_video/                                                                          │
│    pages/*            原生页面（工作台/项目/素材/设置/项目流水线…）                          │
│    api/clipforgeClient.ts  统一请求封装（同源代理、错误、二进制、类型）                      │
│    state/*            settings / product-library / characters / templates / brand           │
│    lib/*              从 integrations/clipforge/src/lib 复制的纯 TS 逻辑                     │
└───────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                            │ 同源 HTTP：/api/clipforge/proxy/api/...
┌───────────────────────────────────────────▼─────────────────────────────────────────────────┐
│ MainPG 后端（FastAPI :8010）                                                                 │
│   /api/clipforge/status | start          现有生命周期接口（保留）                             │
│   /api/clipforge/proxy/{path}            新增：流式反代到当前 sidecar（Range/二进制/超时）     │
│   /api/clipforge/defaults                新增：读取 clipforge.local.json 的运维默认值          │
└───────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                            │ http://127.0.0.1:<随机端口>
┌───────────────────────────────────────────▼─────────────────────────────────────────────────┐
│ ClipForge sidecar（Node/Next，仅作为 API 服务）                                              │
│   /api/project/** /api/ai/** /api/llm/** /api/files/** /api/output/** …                      │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 为什么仍然要加一层同源代理

即使 sidecar 的 CORS 已放行 loopback 任意端口，也统一走 `/api/clipforge/proxy`：

1. sidecar 端口每次随机，前端只需知道固定路径，媒体 URL 也能稳定拼接；
2. 代理层可统一处理：sidecar 未就绪 → `503 {code:"CLIPFORGE_NOT_READY"}`，上游断连 → 中文错误；
3. 可统一注入 `Accept-Language: zh-CN`，保证错误文案语言一致；
4. 未来若打包形态变化（自定义协议、`file://`、Origin 为 null）不会被 CORS 卡死；
5. 媒体 `<video>/<img>` 的 Range 请求也走同一入口，避免把随机端口散落到 DOM。

代理规则：
- 前端路径：`/api/clipforge/proxy/<rest>`，其中 `<rest>` 以 `api/` 开头，例如 `/api/clipforge/proxy/api/project`。
- 后端转发到：`<status.url>/<rest>`，保留 method/query/body/`Range`/`Content-Type`，回传 `status`、`Content-Type`、`Content-Length`、`Content-Range`、`Accept-Ranges`，流式输出（不要整体读入内存）。
- 不转发 `Authorization`；转发 `Accept-Language`，缺省补 `zh-CN`。
- 超时：连接 5s、读 120s（付费视频任务可能同步阻塞几分钟，前端应优先用 202 + 轮询的接口；确实需要长阻塞的接口单独放宽到 600s）。
- 安全：只允许 `GET/POST/PUT/PATCH/DELETE/HEAD`；`rest` 必须匹配 `^api/`，禁止 `..`；只连 `127.0.0.1` 的当前实例 URL。

### 3.3 设置与凭据

- 新增 `modules/ai_video/state/settingsStore.ts`，字段形状照搬 ClipForge `settings-store`（`providers/llm/tts/defaultImageModel/defaultVideoModel/defaultResolution/defaultAspectRatio/customModels/imageParams/videoParams/motionIntensity/motionRealism/chainMode/visualLook/uiMode/spendCapUsd/activeProductionProfile`）。
- 存储键沿用 `daihuo-jianshou-settings`，但**写在 MainPG origin 的 localStorage**：不再随 sidecar 随机端口变化而丢失；但仍绑定浏览器 profile 与 MainPG 的 origin（协议+主机+端口），换浏览器、换开发端口（如 5173↔5174）或清理站点数据后需要重新填写。`clipforge.local.json` 只作为运维默认值，不保存用户手填的 Key。
- 首次进入：`GET /api/clipforge/defaults` 取运维默认值，只补空字段（与 `configured-defaults.ts` 的语义一致）。
- 每次调用按接口契约注入凭据；不允许把 Key 放进 URL。
- `uiMode`（小白/导演）沿用现有 `mainpg.aiVideo.uiMode`，与设置 store 双向同步（以现有父页面逻辑为准）。
- 商品库/角色/模板/品牌 store 同理迁移到 MainPG origin，键名保持 `daihuo-jianshou-products|characters|templates|brand`。

### 3.4 前端目录规划

```
web-frontend/src/modules/ai_video/
  api/
    clipforgeClient.ts        # 请求封装、cfUrl()、错误映射、AbortSignal、二进制
    endpoints/                # 按域拆分的薄封装（projects/scripts/assets/compose/ai/tasks/…）
    types.ts                  # Project/Script/Asset/Composition/AiTask/BatchJob/…
  components/                 # 模块内共享组件（见 3.5）
  hooks/
    useClipForgeService.ts    # 保留（生命周期轮询）
    useAiVideoTaskFeed.ts     # 全局任务轮询（15s/30s）
    usePolling.ts             # 通用轮询（支持 stop 条件、页面隐藏暂停）
  lib/                        # 从 ClipForge 复制的纯 TS（见 3.6）
  pages/
    StartPage.tsx
    ProjectsPage.tsx
    ProductsPage.tsx
    PresentersPage.tsx
    ClonePage.tsx
    BatchPage.tsx
    SettingsPage.tsx
    ProjectScriptPage.tsx
    ProjectAssetsPage.tsx
    ProjectVideoPage.tsx
    ProjectExportPage.tsx
    ProjectProductionPage.tsx
    ProjectTranscriptPage.tsx
  state/
    settingsStore.ts
    productLibraryStore.ts
    characterStore.ts
    templateStore.ts
    brandStore.ts
  styles/
    aiVideoPage.css           # 现有文件；按页面追加分区注释
  nav.ts                      # 保留（侧栏子导航）
```

### 3.5 组件与 UI 规范

- **不复用 ClipForge 的 Tailwind/shadcn**（MainPG 是纯 CSS + 主题变量体系）。新页面一律用 MainPG 的类名与 `--theme-*` 变量，交互配方参考 `modules/pod_customization`。
- 可复制的纯逻辑（无 JSX）直接搬；带 JSX 的组件按 MainPG 风格重写。
- 需要新建的共享组件（放在 `modules/ai_video/components/`）：
  - `CreationBriefForm`（创作简报：来源/叙事/画面约束/出片策略/时长平台）
  - `ProjectHeader` + `ProjectStepper`（项目流水线上下文条与步骤胶囊）
  - `TaskCenterButton`（任务中心入口，复用 `useAiVideoTaskFeed`）
  - `ModelPicker` / `ModelCatalogStatus`
  - `PresenterManager`（主播素材页与设置的角色页共用）
  - `StyleChoicePrompt`（409 `needs_explicit_style` 专用弹层）
  - `EmptyState` / `LoadingBlock` / `ErrorNotice`
- 所有页面遵循：标题卡 → 内容卡 → 右侧/底部操作；滚动交给 `.content-card`（不要自建滚动容器）。

### 3.6 复制的纯逻辑（第一批）

| 目录/文件 | 用途 |
| --- | --- |
| `creation-brief.ts`、`creation-submit.ts`、`creation-entry-prefill.ts`、`batch-creation-brief.ts`、`variation-plan.ts` | 创作简报与提交参数 |
| `script-style.ts`、`style-packs.ts`、`look-presets.ts`、`camera-presets.ts`、`caption-presets.ts`、`compose-presets.ts`、`production-profiles.ts`、`llm-presets.ts`、`tts-presets.ts` | 预设与选项 |
| `gen-params.ts`、`model-capabilities.ts` | 生成参数与能力判断 |
| `script-flow-policy.ts`、`stepper-flow-policy.ts`、`pipeline-stages.ts`、`voiceover-strategy.ts`、`composition-timeline-view.ts`、`project-detail-view.ts` | 流程门控与视图模型 |
| `motion-prompt.ts`、`prompt-lint.ts`、`retake-patch.ts`、`video-control-plan.ts` | 提示词与镜头控制 |
| `assets-view.ts`、`real-mix.ts`、`script-character.ts`、`script-judge.ts`、`ad-compliance.ts`、`publish-readiness.ts`、`presenters.ts` | 素材/脚本视图与自检 |
| `publish-pack.ts`、`shop-link.ts`、`platform-specs.ts`、`video-framing.ts`、`subtitle-export.ts`、`transcript-editor.ts`、`transcript-checkpoint.ts`、`transcript-edit-protocol.ts`、`local-asr.ts` | 发布/导出/逐字稿 |
| `trends.ts`、`examples.ts`、`relative-time.ts`、`friendly-error.ts`、`utils.ts` | 通用 |
| `ad-templates.ts` | 模板工坊（`/project/new` 如要保留才搬；第一批可不搬） |

复制规则：
- 去掉 `@/lib/db/schema` 类型依赖，改为 `api/types.ts` 里的本地类型。
- 去掉 i18n hook，中文文案直接写死（MainPG 目前只有中文）；字典类数据（风格名、预设名）保留中文值。
- 每个复制文件在文件头注明来源路径与「只读同步」约定，避免后续两边漂移。

### 3.7 模块内路由与页签

- 现有侧栏子导航（`nav.ts`）已经是 7 个入口 + 项目详情归属「我的项目」。
- 新增模块内路径状态：`"/start" | "/projects" | "/products" | "/presenters" | "/project/clone" | "/batch" | "/settings" | "/project/<id>/<step>"`。
- `WorkspaceTab` 增加可选字段 `aiVideoPath?: string`，并同步更新 `workspaceTabsPersistence.ts` 的清洗白名单；切页签/刷新后保持所在页面。
- 页面切换不重建模块（与现在 iframe 常驻一致）；切到项目详情时保留返回「我的项目」的入口。
- 未原生化完成的路径：继续渲染现有 iframe（见 3.8），保证任何阶段功能不断。

### 3.8 渐进替换开关

在 `pages/AiVideoPage.tsx` 保留一个内部常量：

```ts
const NATIVE_ROUTES = new Set<AiVideoPath>(["/start"]);
```

- 命中 `NATIVE_ROUTES` → 渲染原生页面；
- 未命中 → 渲染现有 iframe（以及现有的高度同步/滚轮转发逻辑）；
- 每完成一个任务，把对应路径加入集合；Phase 3 删除 iframe 与该常量。

### 3.9 数据流约定

- 页面进入时按需拉取（不做全局项目缓存）；跨页只共享设置、商品库、角色、模板、品牌。
- 长任务一律「提交 → 拿到 id → 轮询」，不在前端做重试风暴；恢复语义按 2.3。
- 所有写操作后以服务端返回为准，不做本地乐观覆盖（除输入框草稿）。
- 媒体 URL 统一 `cfUrl("/api/files/...")`；禁止读取/展示 `outputPath`、`filePath` 等绝对路径。

---

## 4. 任务分解

> 每个任务都可以单独开一个会话执行。开工前只读该任务列出的文件；完成后勾选本文对应复选框，并在提交信息里带任务编号（如 `feat(ai-video-native): T1.1 start page`）。

### Phase 0：基础设施（必须先做）

#### T0.1 后端同源代理与默认值接口

- [x] 已完成（2026-10-20）
- **目标**：`/api/clipforge/proxy/{path}` 与 `/api/clipforge/defaults` 可用，覆盖 JSON、二进制、Range、错误与未就绪。
- **开工前必读**：`local-runtime/wh_local/modules/clipforge/router.py`、`service.py`（只看状态字段与 URL）、`local-runtime/wh_local/app/main.py` 中 clipforge router 的挂载方式。
- **改动**：
  - 新增 `local-runtime/wh_local/modules/clipforge/proxy.py`。
  - `router.py` 挂载 `GET/POST/PUT/PATCH/DELETE/HEAD /proxy/{path:path}`。
  - 新增 `GET /defaults`：读取 `clipforge.local.json`（复用 `service._configured_settings_path()` 的候选解析），返回 `{providers, llm, defaultImageModel, defaultVideoModel}`；文件不存在返回 `{}`。
- **契约**：
  - 未就绪：`503 {"detail":{"code":"CLIPFORGE_NOT_READY","message":"AI 视频服务未就绪"}}`。
  - 上游 4xx/5xx 原样透传状态码与 body。
  - 保留 `Range`；响应头透传 `Content-Type/Content-Length/Content-Range/Accept-Ranges`。
  - 拒绝非 `api/` 前缀与含 `..` 的路径（400）。
- **验收**：sidecar ready 时 `GET /api/clipforge/proxy/api/health` 返回 `{service:"clipforge"}`；`GET .../proxy/api/output/...mp4` 带 `Range: bytes=0-1023` 返回 206；停止 sidecar 后返回 503。
- **测试**：新增 `local-runtime/tests/test_clipforge_proxy.py`（可复用现有测试里的 fake service / TestClient 模式）。
- **验证**：`cd local-runtime && python -m pytest tests/test_clipforge_proxy.py -q`。

#### T0.2 前端请求层与类型

- [x] 已完成（2026-10-20）
- **目标**：`clipforgeClient` + `endpoints/*` + `types.ts`，统一处理代理前缀、错误、二进制与取消。
- **开工前必读**：`web-frontend/src/modules/ai_video/api/clipforgeApi.ts`、`web-frontend/src/shared/api/apiClient.ts`、`integrations/clipforge/src/lib/db/schema.ts`（只取字段）。
- **改动**：
  - `api/clipforgeClient.ts`：`cfUrl(path)`、`cfJson<T>(path, init)`、`cfBlob(path, init)`、`cfHead(path)`；错误优先读 `{detail:{message}}` 再读 `{error}`；网络异常映射中文；支持 `AbortSignal`。
  - `api/types.ts`：Project/Script/Shot/Asset/Composition/AiTask/BatchJob/BatchItem/MediaSource 等（只保留前端消费的字段）。
  - `api/endpoints/`：projects、scripts、assets、materials、stock、compose、aiTasks、batch、settings、tasks、trends、upload。
  - 单测：`clipforgeClient.test.ts`（前缀拼接、错误映射、Abort）。
- **验收**：在一个临时页面能 `cfJson("/api/project")` 拿到列表；错误时抛中文 Error。
- **验证**：`cd web-frontend && node --experimental-strip-types --test src/modules/ai_video/api/*.test.ts && npx tsc --noEmit`。

#### T0.3 模块骨架与原生路由开关

- [x] 已完成（2026-10-20）
- **目标**：`AiVideoPage` 能在「原生页面 / iframe」之间按路由切换；`aiVideoPath` 进入页签状态并可持久化。
- **开工前必读**：`web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx`、`web-frontend/src/app/layout/WorkspaceShell.tsx`（ai_video 分支、tabs 定义）、`workspaceTabsPersistence.ts`、`nav.ts`。
- **改动**：
  - 新增 `pages/AiVideoShell.tsx`（标题卡 + 服务状态 + 路由分发 + 原生路由集合）。
  - `WorkspaceShell`：`aiVideoPath` 状态与 tab 持久化；侧栏点击写路径；未原生化的路径继续走 iframe。
  - `AiVideoPage.tsx` 拆分为 `AiVideoFrame`（现有 iframe 逻辑原样保留）+ `AiVideoShell`。
- **验收**：`/start`（即使仍是 iframe 占位）切换不丢状态；切页签再回来停留在同一路径。
- **验证**：`cd web-frontend && npm run verify`。

#### T0.4 设置与凭据 store

- [x] 已完成（2026-10-20）
- **目标**：`settingsStore` + `/api/clipforge/defaults` 注入 + 一个最小的「设置 → LLM/Provider」页。
- **开工前必读**：`integrations/clipforge/src/lib/stores/settings-store.ts`、`configured-defaults.ts`、`components/generation-settings.tsx`。
- **改动**：`state/settingsStore.ts`（zustand 或轻量自定义 store，与仓库现有偏好 hook 风格一致）；`pages/SettingsPage.tsx` 先做 providers + llm + 默认模型；`api/endpoints/settings.ts` 接 defaults、`/api/llm/test`、`/api/llm/models`。
- **验收**：填写 Key 后刷新浏览器仍在；`POST /api/llm/test` 通过；清空后由 defaults 补回。
- **验证**：`npm run verify` + 手动联调。

#### T0.5 复制第一批纯逻辑

- [x] 已完成（2026-10-20）
- **本批范围**：服务于 `/start`、`/projects`、`/products`、`/presenters`、`/project/clone`、`/batch` 的纯 TS（30 个主文件 + production-system/voice-report/emotion-acting/tts-voices/hook-patterns 等支撑模块），共 39 个文件（含抽取的 `style-values` / `script-categories` / `provider-types` / `qc-types`）。
- **本批未复制**：`assets-view`、`script-judge`、`publish-readiness`、`retake-patch`、`video-control-plan`、`model-capabilities`、`ad-compliance`、`ad-templates`、`utils`——它们只被 Phase 2 页面消费，随 T2.x 一起复制，避免提前拖入 OpenAI/ffmpeg/tailwind 依赖。
- **目标**：把 3.6 表中与 `/start`、`/projects`、`/products`、`/presenters`、`/project/clone`、`/batch` 相关的纯 TS 复制到 `modules/ai_video/lib/`。
- **开工前必读**：3.6 表格对应文件。
- **改动**：复制 + 去掉 `@/lib/db/schema` / i18n / React 依赖；补 `lib/__tests__` 中可复用的纯函数测试。
- **验收**：`npx tsc --noEmit` 通过；每个复制文件头有来源注释。
- **验证**：`npm run verify`。

### Phase 1：一级页面

#### T1.1 视频工作台 `/start`

- [x] 代码完成（2026-10-20）；手动全链路待联调（需真实 sidecar + 平台 Key）
- **依赖**：T0.1–T0.5
- **目标**：原生创建入口：来源面板 → 创作简报 → 提交（建项目/传图/生成脚本）→ 跳脚本页；409 选风格；最近项目。
- **开工前必读**：`integrations/clipforge/src/app/start/page.tsx`、`components/project-creation/creation-brief-form.tsx` 及其子面板、`lib/creation-submit.ts`、`lib/creation-entry-prefill.ts`。
- **接口**：`GET /api/project`、`GET /api/trends`、`POST /api/ingest/product`、`POST /api/project`、`POST /api/upload`、`PATCH /api/project/:id`、`POST /api/llm/script`、`POST /api/topic/script`。
- **改动**：`pages/StartPage.tsx` + `components/CreationBriefForm.tsx` + `styles`。
- **注意**：`recordStrategySelected` 是 server action，原生侧先**不发送**该埋点（不影响功能），后续如需要再加 sidecar 路由。
- **验收**：五种来源都能建项目并进入 `/project/:id/script`；脚本生成失败有中文错误；刷新后最近项目正确。
- **验证**：`npm run verify` + 手动全链路（商品图/主题/复刻各一次）。

#### T1.2 我的项目 `/projects`

- [x] 代码完成（2026-10-20）；手动联调待执行
- **目标**：项目列表（搜索/排序/续做/删除）+ 成片 feed。
- **接口**：`GET /api/project`、`GET /api/works`、`DELETE /api/project/:id`。
- **验收**：删除有二次确认；点击按项目状态跳到对应步骤；成片可下载。

#### T1.3 商品素材 `/products`

- [x] 代码完成（2026-10-20）；手动联调待执行。链接导入 + 手动新增 + 图片入库已验证。
- **目标**：商品库 CRUD + 链接导入 review gate + 图片上传。
- **接口**：`POST /api/ingest/product`、`POST /api/products/upload`；本地 store。
- **验收**：商品图最多 5 张；导入链接后可编辑再入库；「做视频」带预填跳 `/start`。

#### T1.4 主播素材 `/presenters`

- [x] 代码完成（2026-10-20）；手动联调待执行。角色绑定已在 `/start` 的真人出镜模式生效并随脚本请求下发；脚本页内的角色选择随 T2.1 接入同一个 store。
- **目标**：角色库 CRUD、设为默认、生成四视图定妆照、预览。
- **接口**：`POST /api/characters/sheet`。
- **验收**：生成的定妆照可预览/删除；角色在脚本页可选。

#### T1.5 爆款复刻 `/project/clone`

- [x] 代码完成（2026-10-20）；仅链接降级 + 真实参考视频解析接口已验证；文件选择 UI 待验。
- **目标**：参考视频解析 → 镜头骨架 → 商品信息 → 交接 `/start`。
- **接口**：`POST /api/replicate/analyze`、`POST /api/upload`。
- **验收**：无参考视频时可降级为通用结构；交接后预填正确。

#### T1.6 任务中心 `/batch`

- [x] 代码完成（2026-10-20）；并发 3、单条失败隔离、刷新后续跑均已验证。
- **目标**：批量出片执行器（建项目→脚本→（draft）合成）、并发池、断点续跑、同质化自检。
- **接口**：`GET/POST/PATCH /api/batch`、`POST /api/project`、`POST /api/llm/script`、`POST /api/project/:id/script-judge`、`PATCH /api/project/:id/scripts`、`POST /api/project/:id/stock-fill`、`POST /api/project/:id/compose`、`GET /api/project/:id/compose`、`GET /api/insights/homogeneity`。
- **验收**：并发 3；中断后可继续/丢弃；单条失败不影响其余。
  - 已实现：并发池 3、每阶段落库；刷新/关闭时用 `beforeunload` 标记卸载，在途请求中止不写成失败，未完成项保持 `running`，重进页面出现「继续未完成项 / 丢弃」；单条失败只影响该项。回归验证：3 条并发中 2 条完成、1 条因无素材失败且不影响其余；执行中刷新后点「继续未完成项」可续跑并完成。

#### T1.7 设置页补全 `/settings`

- [x] 代码完成（2026-10-20）；手动联调待执行。品牌配置已落到本地 store，导出页（T2.4）将读取同一份水印设置。
- **目标**：TTS、角色、品牌、诊断、模型目录刷新、生成参数。
- **接口**：`POST /api/tts`、`POST /api/tts/free`、`GET /api/health`、`POST /api/ai/models`、`POST /api/ai/test-provider`。
- **验收**：试听可播放；诊断信息可复制；品牌设置影响导出水印（若实现）。

### Phase 2：项目流水线

> 通用前置：`ProjectHeader` + `ProjectStepper` + 项目数据加载 hook（`GET /api/project/:id`）。

#### T2.1 脚本页 `/project/:id/script`

- [x] 代码完成（2026-10-20）；真实项目手动联调待执行。项目详情暂只有脚本页原生，其余步骤继续 iframe。
- **目标**：版本列表/切换、逐镜编辑、判官、免费流水线（启动+轮询+断点续跑）、原生整片两阶段（dryRun → 付费确认）。
- **接口**：`GET /api/project/:id`、`GET/PATCH /api/project/:id/scripts`、`POST /api/llm/script`、`POST /api/topic/script`、`POST /api/project/:id/script-judge`、`POST/GET /api/project/:id/pipeline`、`POST /api/project/:id/storyboard-film`、`POST /api/project/:id/storyboard-grid`、`POST /api/characters/sheet`。
- **验收**：切换版本落库；编辑保存；pipeline 进度可见且可恢复；超预算需显式确认。

#### T2.2 素材页 `/project/:id/assets`

- [x] 代码完成（2026-10-20）；真实生成/取消/恢复链路手动联调待执行。
- **目标**：逐镜生成/替换/上传/重拍、免费素材填充、九宫格、整片 dryRun、云端付费任务恢复、本地素材库。
  - 已实现：本地素材库（上传 1–12 个 / 搜索筛选 / 预览 / 套用到镜头 / 按本地素材填充未锁定镜头）、诊断重拍（六类症状单选、只追加一条补丁、补丁已存在不重复提交）、九宫格关键帧、整片 dryRun 估算（不提交付费任务）。
- **接口**：`GET/POST/PATCH /api/project/:id/assets`、`GET/POST /api/project/:id/materials`、`POST /api/upload`、`POST /api/project/:id/stock-fill`、`GET /api/ai/tasks`、`POST /api/ai/image`、`POST /api/ai/video`、`POST /api/ai/video/task`、`PATCH /api/project/:id/scripts`。
- **验收**：同一镜头多 take 只能选中一个；付费任务超时后可从「未取回」列表恢复，不重复提交。

#### T2.3 合成页 `/project/:id/video`

- [x] 代码完成（2026-10-20）；真实合成/BGM 上传/矩阵出片手动联调待执行。
- **目标**：时间线、配音/BGM/字幕/画幅/分辨率、合成轮询、成片预览下载、变体矩阵。
- **接口**：`GET /api/project/:id/compositions`、`.../compositions/:cid/timeline`、`POST /api/project/:id/compose`、`GET /api/project/:id/compose?compositionId=`、`POST /api/project/:id/bgm`、`POST /api/tts/free`。
- **验收**：按 `compositionId` 精确轮询；合成中可取消页面不丢状态；矩阵按版本独立轮询。
  - 已实现：时间线缩略图/逐镜时长与音频报告、渲染档位/画幅/分辨率/字幕预设、付费 TTS 与免费 Edge 回退、BGM 情绪/音量/闪避/上传、成片版本选择与下载、`compositionId` 落 localStorage 后离页可续查、变体矩阵逐个提交并各自轮询。

#### T2.4 导出页 `/project/:id/export`

- [x] 代码完成（2026-10-20）；真实平台导出/QC/门禁/更多产出手动联调待执行。
- **目标**：成片版本分组、发布文案、平台导出、QC/门禁、授权清单、A/B、更多产出。
- **接口**：`GET /api/project/:id/compositions`、`POST /api/llm/publish`、`POST /api/project/:id/export-platform`、`/qc`、`/gate`、`/credits`、`/cover`、`/carousel`、`/shop-qr`、`/end-card`、`/contact-sheet`、`/native-feel`、`/dub`、`POST /api/project/:id/metrics`。
- **验收**：下载/复制链接可用；QC/门禁结论展示；授权清单可下载 md。
  - 已实现：按出片策略分组并选中导出版本、七平台规格导出、发布文案 + 评论区话术（LLM 失败或未配置时自动回退到免 Key 模板文案，并展示 AIGC 声明）、QC 逐项结论、发布门禁汇总、授权清单 JSON + Markdown 下载、两组 A/B 变体各自轮询、封面/图文/二维码/片尾卡/联系表/原生感/译制、发布数据回填；品牌水印读取同一份 store（sidecar 当前不烧录水印，页面明确提示人工核对）。

#### T2.5 制作控制台 `/project/:id/production`

- [x] 代码完成（2026-10-20）；真实评估/修复/母版手动联调待执行。
- **目标**：成本卡、工作流开关、创意意图/Visual Bible、质量门禁裁决、模型路由、母版、修复、诊断。
- **接口**：`GET/PATCH /api/project/:id/production`、`GET/POST/PATCH /api/project/:id/quality`、`PATCH /api/project/:id/assets`、`POST /api/project/:id/repair`、`POST /api/project/:id/mastering`、`POST /api/ai/models`。
  - 已实现：成本区间与未知单价提示、九阶段工作流开关、创意意图/Visual Bible 编辑、候选质量评估与人工通过/驳回（通过即设为选中）、精准修复「先预演再显式确认付费执行」、按目标排序模型、成片分析 + 响度归一/去闪烁母版、版本快照、失败诊断与版本树计数。
- **验收**：裁决后 take 切换正确；修复/母版产物可预览。

#### T2.6 逐字稿 `/project/:id/transcript`

- [x] 代码完成（2026-10-20）；真实长视频转写/剪辑渲染手动联调待执行。
- **目标**：本地 ASR（Worker）分块转写 + 词级编辑 + 非破坏剪辑 + 字幕导出。
- **接口**：`GET/POST /api/project/:id/media`、`POST .../media/:sid/transcript`（start/heartbeat/checkpoint/complete/fail/cancel）、`GET .../media/:sid/audio`、`GET/POST .../media/:sid/edit`、`POST .../media/:sid/timeline`。
- **改动**：复制 `workers/asr.worker.ts`，新增依赖 `@huggingface/transformers`（确认 Vite Worker 打包）。
- **验收**：长视频可断点续转；剪辑渲染可取消/重试；SRT/VTT/OTIO 导出可用。
  - 已实现：本地 Worker 分块转写（5 分钟分块、断点续转、心跳、取消、WebGPU→WASM 回退）、词级删除/语气词一键标记/撤销重做/静音删除/起止裁剪/字幕短语校正、SRT/VTT/剪辑 JSON 导出、OTIO/EDL/CSV 时间线导出、剪辑预演与显式输出（轮询状态、取消、重试）、片段建议与批量输出。`@huggingface/transformers` 已加入依赖，Vite 构建产出独立 `asr.worker-*.js`。
  - 回归修复：Next 16 的 proxy 请求体默认上限 10MB，导致 >10MB 的视频导入被截断并误报「视频无法解码或文件已损坏」。已在 `next.config.ts` 显式设置 `experimental.proxyClientMaxBodySize: "1gb"`，并用 33.5MB / 30s 视频验证导入成功（此前 10.9MB 即失败）。
  - 卡死保护：`asr.worker.ts` 增加 WebGPU 初始化看门狗（120s 无进展回退 WASM），用于避免部分驱动上永久停在「加载模型」；该回退路径尚未完成真机验证。

### Phase 3：收尾

#### T3.1 移除 iframe 与内嵌协议

- [x] 代码完成（2026-10-20）。
- **目标**：所有路径原生后删除 iframe 与桥接代码。
- **改动**：
  - 删除 `pages/AiVideoPage.tsx` 的 iframe 分支、`NATIVE_ROUTES`、高度同步与滚轮转发、`AI_VIDEO_*_MESSAGE` 常量与相关测试。
  - ClipForge 侧删除 `lib/mainpg-embed-bridge.ts`、`app-shell.tsx` 的 embedded 分支与相关 effect、`proxy.ts` 的 embed 判定、`globals.css` 的 `.mainpg-embedded` 主题与高度补丁、相关测试。
  - 保留 `GET/POST /api/clipforge/status|start`（仍需要启动 sidecar）。
- **验收**：`grep -R "mainpg:ai-video" web-frontend integrations/clipforge/src` 为空；`npm run verify` 与 ClipForge 相关测试通过。
  - 已完成：`AiVideoFrame.tsx`、`NATIVE_ROUTES`、内嵌协议与 UI 模式双键同步全部删除，服务就绪门禁保留在 `AiVideoShell`；`web-frontend` 503 项测试、`integrations/clipforge` 1786 项测试（带内置 ffmpeg/ffprobe 路径）全部通过；验收 grep 为空。
  - 发布验证：`next build` 成功，新 artifact `5BraFH2O7N56f1Wz_tMZ1-e8e61aba0369` 已发布（含 T3.2 的 1GB 请求体修复）；`verify_clipforge_sidecar.py` PASS（健康、停止、换实例重启、产物哈希一致）。

#### T3.2 回归与文档

- [ ] 文档已更新；自动化回归已完成，剩余手动项交用户执行。
- **目标**：全链路回归 + 文档更新。
- **清单**：
  - 启动/停止 sidecar、异常退出自动重启、未就绪提示；
  - 五条创建链路、批量、复刻；
  - 脚本→素材→合成→导出全链路；
  - 逐字稿；
  - 主题切换（雾粉/经典等）下所有新页面的可读性；
  - 窄窗口（<800px）可用性；
  - 刷新/切页签后的路径与滚动位置。
- **文档**：更新 `README.md` 的 AI 视频章节、`docs/工作台使用手册-当前功能版.md`、删除本文件顶部的「待执行」并逐项勾选。
- **本轮已验证（2026-10-20，真实浏览器 + 本地 sidecar）**：
  - 服务：未就绪门禁、杀掉 sidecar 后 failed 提示与自动重启（新端口/新 instanceId）、重启后原生页面与路径保持。
  - 一级页面：视频工作台、我的项目、商品素材、主播素材、爆款复刻、任务中心、设置均正常渲染。
  - 创建链路：①「一句话主题」完成建项目 → 生成 3 版脚本 → 进入脚本页；免费草稿策略启动免费出片流水线，服务端完成 stock_fill → compose，产出成片（draft / 1080p / 9:16）。②「商品链接」用 Apple 商品页完成解析预填 → review → 入库（标题/卖点/图片均落库）。③「商品库预填」从商品库选择商品后名称/卖点/图片正确带入工作台。④「爆款复刻」在只有链接时正确降级为 6 段通用高转化结构；真实参考视频解析接口返回 6 镜 / 30s 的切分骨架。⑤商品库新增「云朵宠物窝」后，任务中心批量出片 1/1 完成；再做 3 条并发：2 条完成并产出 composition，1 条因无可用素材失败且不影响其余。
  - 脚本页：版本列表/切换、逐镜台词/画面/运镜、判官入口、保存按钮、免费流水线轮询与完成态。
  - 素材页：6 镜候选、本地素材库、诊断重拍下拉、九宫格与整片 dryRun（正确提示 15s 上限溢出）、单镜真实生图成功并新增 take。
  - 视频页：6 段时间线、成片版本、合成设置、变体矩阵渲染。
  - 导出页：真实平台导出（抖音 1080×1920）与下载链接、发布文案（LLM 成功一次；另验证「模板文案」按钮与 LLM 失败自动回退）、QC（720×1280 / 30.0s / -15.9 LUFS / 无黑帧静音冻结）、发布门禁（有条件通过）、授权清单（6 项可商用）、封面、7 张图文卡片、二维码缺商品链接提示、联系表、原生感版本、发布数据回填。
  - 制作控制台：成本区间 `$0.07–$7.00`、工作流开关、创意意图/Bible 保存、模型路由推荐、成片分析 + 母版渲染与下载、版本快照。
  - 逐字稿：导入视频（修复 >10MB 截断后 33.5MB 成功）、素材列表、模型/语言选择、启动转写与断点续传；同时修复「续转时旧的失败文案仍挂在运行中任务上」的前端状态同步问题。本地 Whisper 模型下载依赖 HuggingFace 网络，本机带宽下未在本轮完成全片转写。
  - 任务中心：商品选择、并发 3 / 防同质化 / 自动配画面与合成参数；真实批量执行 1/1 完成后，再做 3 条并发：2 条完成并产出 composition，1 条因无可用素材失败且不影响其余；三条的钩子/音色/BGM/字幕均按变体计划差异化。执行中刷新后出现「继续未完成项 / 丢弃」，点继续可续跑并完成。
  - 主题：桃花源 / 经典 / 高山草原 三种主题下脚本、导出、任务中心等页面文字可读，`data-theme` 正确切换并保持。
  - 窄窗口：502×528 下上述页面可用；脚本页、导出页、任务中心均无横向溢出。
  - **尚未覆盖（交用户手动执行）**：上传商品图链路、参考视频解析的文件选择 UI、付费生视频、全片 ASR。

---

## 5. 全局契约与约束

1. **凭据**：每个请求按接口需要注入；禁止写入 URL、日志、错误提示。
2. **绝对路径**：`outputPath`/`filePath` 只用于调试，UI 一律用 `url`/`timelineUrl`/`fileName`。
3. **错误文案**：代理统一带 `Accept-Language: zh-CN`；前端优先展示 `detail.message`/`error`，其次按状态码给中文兜底。
4. **长任务**：严格按 2.3 的 id + 轮询；禁止用「再提交一次」代替恢复。
5. **并发**：批量页并发 3；其他页面同一接口同一时刻最多 1 个在途请求。
6. **上传**：图片 ≤20MB（`/api/upload`）、素材 ≤80MB（`/materials`）、长视频 ≤1GB（`/media`）；大文件用 XHR 展示进度。
7. **安全**：代理只连当前实例的 loopback URL；不做任意 URL 转发；不新增对外监听端口。
8. **主题**：只用 `--theme-*` 变量，不写死颜色；新增样式集中在 `styles/aiVideoPage.css` 并分段注释。
9. **可回退**：Phase 3 之前，任何路径都可退回 iframe；不要提前删除桥接代码。
10. **代码归属**：`integrations/clipforge` 的改动只允许「新增供原生前端使用的接口/清理内嵌壳」，不允许改生成与合成逻辑。

---

## 6. 测试策略

| 层级 | 方式 |
| --- | --- |
| 后端代理 | `pytest`：透传、Range、503、非法路径 |
| 请求层/纯逻辑 | `node --test`：前缀、错误映射、参数构造、流程门控 |
| 页面契约 | 源码级断言（沿用仓库现有 `*.test.ts` 风格）：关键接口路径、轮询间隔、禁用绝对路径 |
| 类型 | `npm run verify`（含 `tsc --noEmit`） |
| 手工回归 | 每个任务完成时跑对应链路；Phase 3 全量回归 |
| ClipForge 侧 | `npx vitest run src/lib/__tests__/mainpg-embed-bridge.test.ts src/components/__tests__/app-shell-embed.test.ts`（Phase 3 前保持通过） |

---

## 7. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 页面体量大，逐页搬容易漏状态 | 功能倒退 | 每页以「接口调用清单 + 验收清单」为准；先跑通主链路，再补边缘操作 |
| 两套 UI 体系差异 | 开发慢、观感不一致 | 先建共享组件与样式基线（T0.3/T0.5），后续页面只组合 |
| 设置从 sidecar origin 迁移 | 老用户配置丢失 | 首次由 `/defaults` 补默认值；在设置页提示重新填写 Key |
| 付费任务重复提交 | 重复扣费 | 只允许 `taskId` 恢复；提交前显式二次确认与预算校验 |
| 长任务轮询遗漏 | 进度卡死 | 统一 `usePolling`，页面隐藏暂停、失败退避、卸载清理 |
| 逐字稿 Worker 打包 | 构建失败 | T2.6 单独验证 `new Worker(new URL(...), {type:"module"})` 与依赖体积 |
| 代理流式/大文件 | 内存暴涨 | 代理必须 `stream`，禁止 `await response.read()`；设读超时 |
| 未原生化路径的回退 | 体验割裂 | 用 `NATIVE_ROUTES` 控制，按任务逐项收敛，Phase 3 统一删除 |

---

## 8. 执行顺序总表

| 序号 | 任务 | 依赖 | 可独立会话 |
| --- | --- | --- | --- |
| 1 | T0.1 后端代理/默认值 | — | 是 |
| 2 | T0.2 请求层/类型 | T0.1 | 是 |
| 3 | T0.3 模块骨架/路由开关 | T0.2 | 是 |
| 4 | T0.4 设置 store 与最小设置页 | T0.2 | 是 |
| 5 | T0.5 复制第一批纯逻辑 | — | 是 |
| 6 | T1.1 视频工作台 | T0.3–T0.5 | 是 |
| 7 | T1.2 我的项目 | T0.3 | 是 |
| 8 | T1.3 商品素材 | T0.3 | 是 |
| 9 | T1.4 主播素材 | T0.3 | 是 |
| 10 | T1.5 爆款复刻 | T1.1 | 是 |
| 11 | T1.6 任务中心 | T1.3 | 是 |
| 12 | T1.7 设置补全 | T0.4 | 是 |
| 13 | T2.1 脚本页 | T1.1 | 是 |
| 14 | T2.2 素材页 | T2.1 | 是 |
| 15 | T2.3 合成页 | T2.2 | 是 |
| 16 | T2.4 导出页 | T2.3 | 是 |
| 17 | T2.5 制作控制台 | T2.2 | 是 |
| 18 | T2.6 逐字稿 | T2.2 | 是 |
| 19 | T3.1 移除 iframe/桥接 | 全部 | 否（收尾） |
| 20 | T3.2 回归与文档 | T3.1 | 否（收尾） |

---

## 9. 每完成一个任务的固定动作

1. 勾选本文对应任务复选框。
2. 更新 `NATIVE_ROUTES`（仅页面类任务）。
3. 跑该任务「验证」命令，记录结果。
4. 提交：`feat(ai-video-native): <任务编号> <一句话>`，只提交本任务涉及的文件。

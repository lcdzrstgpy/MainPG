# POD 爆款裂变 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not delegate tasks unless the user asks for parallel work. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 POD 中新增独立的「爆款裂变」模块，分析一张样图和一张白底产品图，生成 1–52 个不重复完整组合的商品款，并交付四宫格图片、标题、规格卡和上架导出。

**Architecture:** 裂变使用独立 API、领域表、`fission` 批次模式和款级 `FissionVariantPlan`，复用现有 POD repository/worker、图片拆分与 ONNX 超分、标题、规格卡、导出及按款结算。A/B 联合分析一次返回风格/元素/配色候选与结构化能力/规则候选；用户确认后，后端在创建批次事务中通过本地确定性验证并追加全系统能力库版本，当前批次立即使用该版本，历史批次冻结版本，连续 3 款生成失败时回滚当前指针。用户取消分析不会发布全局能力变更。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite migrations、pytest、React、TypeScript、Vite、Node test runner、现有 POD 图片/计费运行时。

## 实施状态（2026-10-15）

已在原分支 `deepseek/POD-change` 完成本地前后端实现，并由多个代理并行推进和复核；未创建分支或提交。Task 1–8 的代码已落地，Task 9 的自动化验收通过。

- 后端完整 POD 回归：705 passed，3 subtests passed，1 条现有 Starlette 弃用警告。
- 前端 `npm run verify`：373 passed，API 路径检查及 TypeScript 检查通过；`npm run build` 成功。
- 自动化覆盖能力发布/事务回滚、历史快照、连续三款失败回滚、拒绝版本防复活、部分交付结算、退款后重试及成功款导出。
- 款级实时状态从公共 POD item/title 状态投影，避免另建一套状态机；裂变领域表保存不可变方案与版本快照。
- 本轮修正了旧规格卡审计测试的 pytest 日志重复捕获问题，保留每款一次开始/完成的原断言。

手工浏览器验收、真实模型与远端计费联调仍待测试环境完成。具体证据、发布依赖和验收步骤见 [实施验收与发布依赖](2026-10-15-pod-fission-verification.md)。以下未勾选的“确认失败”步骤属于红灯阶段历史，收尾时不补造运行记录；“提交本任务”步骤未执行，本轮保留可审查的工作区改动。

## Global Constraints

- 新模块 ID 为 `pod_fission`，批次模式为 `fission`，构图模板 scope 为 `fission`。
- 每批款数为 1–52；一款对应一次 `grid` 生图调用和一张四宫格。
- 豆包分析 A/B 共用一次调用；结构化响应失败时自动重试一次。
- 用户只确认只读的产品/图案摘要和数量；不展示词表、能力、规则、权重和方案编排细节。
- 动态能力/规则全系统共享、追加版本、当前批次立即使用新版本；已冻结批次不可重算。
- 同一能力/规则版本连续 3 款图片生成或产物校验失败时回滚到上一版本；取消、余额不足、授权失败不参与该计数。
- 部分成功款可查看和导出，失败款按款重试并退款；标题与批次上架字段复用现有全定制行为。
- 不更改全定制、半定制和爆款复刻的现有业务语义。
- 开始实现前先检查 `git status` 和相关文件 diff。工作区已有的 `local-runtime/tests/test_clipforge_process.py`、`local-runtime/wh_local/modules/clipforge/process.py`、`local-runtime/wh_local/modules/pod_customization/title_runtime.py` 改动属于用户内容，必须保留且不得混入裂变任务提交。

---

## 文件地图

### 后端领域与持久化

- `local-runtime/wh_local/modules/pod_customization/fission_specs.py`：裂变分析、候选能力/规则、款级方案和批次请求 Pydantic 类型。
- `local-runtime/wh_local/modules/pod_customization/fission_registry.py`：能力/规则候选验证、追加版本、读取当前版本和版本回滚服务；所有规则表达式使用白名单 DSL，不执行 Python、SQL 或模型提供的代码。
- `local-runtime/wh_local/modules/pod_customization/fission_runtime.py`：一次联合 A/B 豆包分析、strict JSON schema、一次契约修复重试和分析结果标准化。
- `local-runtime/wh_local/modules/pod_customization/fission_planner.py`：能力匹配、规则 DSL 求值、变体候选抽取、组合去重和快照生成。
- `local-runtime/wh_local/modules/pod_customization/fission_prompts.py`：四宫格全局契约、模板、产品规则、Tier 元素权重及 60/30/10 配色的确定性提示词编译。
- `local-runtime/wh_local/modules/pod_customization/contracts.py`：新增 `fission` mode、scope、上传角色和数量边界。
- `local-runtime/wh_local/modules/pod_customization/repository.py`：分析/能力版本/学习事件/裂变批次/款级方案持久化；历史快照读取。
- `local-runtime/wh_local/modules/pod_customization/service.py`：上传、分析、确认创建、查询、重试、能力版本冻结和结算编排。
- `local-runtime/wh_local/modules/pod_customization/router.py`：独立 `/fission/*` API、multipart 限制和鉴权。
- `local-runtime/wh_local/modules/pod_customization/worker.py`：按款读取裂变 Prompt 与 A/B 参考图，调用现有四宫格生成、拆图、超分、标题和发布流程。
- `local-runtime/wh_local/modules/pod_customization/billing_contract.py`：新增 `pod_fission_v1` 批次、恢复和单款重试计划。
- `local-runtime/wh_local/billing.py`：接受 `pod_fission_v1`，按与 `pod_random_v1` 相同规则价区间冻结/结算并列入 POD profile 识别。
- `local-runtime/wh_local/app/main.py`：提供并注入裂变分析 runtime，沿用现有 POD service/router 注册。
- `local-runtime/wh_local/modules/pod_customization/migrations/023_fission_registry.sql`：全局能力/规则版本、当前版本指针和学习事件表。
- `local-runtime/wh_local/modules/pod_customization/migrations/024_fission_domain.sql`：裂变分析、批次和款级方案表。

### 前端

- `web-frontend/src/modules/pod_fission/types.ts`、`api/podFissionApi.ts`：独立 API 契约和上传/分析/批次操作。
- `web-frontend/src/modules/pod_fission/pages/PodFissionPage.tsx`：上传 A/B、只读识别摘要、数量确认、历史批次及结果视图。
- `web-frontend/src/modules/pod_fission/components/FissionSourceUploader.tsx`、`FissionProductUploader.tsx`：角色明确的图片上传和预览。
- `web-frontend/src/modules/pod_fission/data/podFissionModel.ts`、`podFissionDraft.ts`：批次展示状态与本地草稿恢复。
- `web-frontend/src/app/navigation/modules.ts`、`web-frontend/src/app/layout/WorkspaceShell.tsx`：POD 同级入口与页面挂载。
- `web-frontend/src/modules/personal_center/api/personalCenterApi.ts`、`pages/PersonalCenterPage.tsx`：消费流水类型和「POD 爆款裂变」标签。

### 测试

- 后端新增 `tests/test_fission_registry.py`、`tests/test_fission_runtime.py`、`tests/test_fission_planner.py`、`tests/test_fission_api.py`、`tests/test_fission_worker.py`、`tests/test_fission_billing.py`，并扩展 migration、app registration、模板隔离测试。
- 前端新增 `podFissionApi.test.ts`、`podFissionModel.test.ts`、页面源/渲染测试和导航测试；扩展个人中心 profile 映射测试。

---

## Task 1: 全系统动态能力与规则版本库

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/migrations/023_fission_registry.sql`
- Create: `local-runtime/wh_local/modules/pod_customization/fission_specs.py`
- Create: `local-runtime/wh_local/modules/pod_customization/fission_registry.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_registry.py`
- Test: `local-runtime/tests/test_pod_customization_migrations.py`

**Interfaces:**
- `LearnedCapabilityCandidate`：候选能力 ID、类型、依赖事实字段、触发条件、约束文本、禁止条件和模板兼容项。
- `LearnedRuleCandidate`：规则 ID、白名单 predicate AST、约束文本和引用能力 ID。
- `FissionRegistrySnapshot`：`capabilities`、`product_mappings`、`rules`、`capability_version`、`rule_version`。
- `FissionRegistryService.validate_candidates(candidates) -> ValidatedRegistryCandidates`：纯校验和规范化，不写库；输出可序列化候选与稳定内容 hash。
- Repository 原子 registry primitives: `append_fission_registry_version(connection, ...)`、`get_fission_registry_snapshot(connection, ...)`、`rollback_fission_registry(connection, ...)`。裂变领域任务再将这些 primitives 与批次写入组合为单个事务入口。
- `FissionRegistryService.get_current_snapshot() -> FissionRegistrySnapshot`。
- `FissionRegistryService.rollback_after_failures(capability_version, rule_version, failed_style_indices) -> bool`：达到连续 3 款时回退当前指针并记审计。

- [x] **Step 1: 写候选 schema、追加版本和回滚的失败测试**

测试 valid candidate 会追加新版本；无效事实字段、重复 ID、未知 predicate operator、超长约束、越权 SQL/代码样式载荷都拒绝且不改变当前指针；并发发布不丢版本；历史版本不可变；连续 2 款不回滚、第 3 款回滚、不同版本失败计数不混合。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_registry.py tests/test_pod_customization_migrations.py -q`
Expected: FAIL，因为注册表类型、迁移和 repository 方法尚不存在。

- [x] **Step 3: 建立 SQLite 追加式版本表与 repository 事务方法**

`023_fission_registry.sql` 建立 `pod_customization_capability_registry_versions`、`pod_customization_rule_registry_versions`、`pod_customization_registry_current` 和 `pod_customization_learning_events`。版本表使用全局 scope，不包含 workspace/user owner；版本记录只 INSERT，不 UPDATE/DELETE。当前指针以单行按 registry kind 保存。学习事件存 `analysis_id`、输入 SHA-256、候选 JSON、发布版本、状态、原因和时间。

在 repository 增加 `append_fission_registry_version(connection, ...)`、`get_fission_registry_snapshot(connection, ...)`、`rollback_fission_registry(connection, ...)`，这些方法显式接收同一个连接且不自行 commit。各自公开写操作负责 `BEGIN IMMEDIATE`，在锁内追加版本、更新 pointer、插入事件。Task 3 建立裂变领域表后，再由其实现的 `create_fission_batch_with_registry(...)` 将 registry primitives 与 batch/variant 写入组合为同一事务。禁止从事务内调用会自行开新连接的 service/repository 写方法。

- [x] **Step 4: 实现确定性 schema/DSL 校验与服务**

predicate 支持且仅支持 `eq`、`in`、`contains`、`gt`、`gte`、`exists`，事实字段必须来自 `SourceArtworkSpec`/`TargetProductSpec` 白名单；effects 只允许追加能力 ID、约束文本和禁用项，不支持代码执行、动态导入、SQL 或网络动作。候选校验是纯函数，不读取 current pointer；稳定计算规范化候选 hash，使相同候选重复发布幂等，不制造无意义新版本。repository 在写事务内基于锁定后的最新版本追加，避免校验与并发发布之间的 TOCTOU。

- [x] **Step 5: 实现失败滑窗与自动回滚**

按 `(capability_version, rule_version)` 记录款级终态结果；仅当图片最终生成失败或四格校验/持久化失败时计入。用户取消、计费拒绝、授权失败、任务基础设施未启动不计入。连续 3 个失败款原子回退当前 pointer，记录失败款索引；同批其他款仍按其冻结快照运行。

- [x] **Step 6: 运行目标测试与迁移回归**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_registry.py tests/test_pod_customization_migrations.py -q`
Expected: PASS；旧 migration checksum/修复逻辑不变，新 migration 在空库和升级库各只应用一次。

- [ ] **Step 7: 提交本任务**

```bash
git add local-runtime/wh_local/modules/pod_customization/migrations/023_fission_registry.sql local-runtime/wh_local/modules/pod_customization/fission_specs.py local-runtime/wh_local/modules/pod_customization/fission_registry.py local-runtime/wh_local/modules/pod_customization/repository.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_registry.py local-runtime/tests/test_pod_customization_migrations.py
git commit -m "feat(pod): add versioned fission capability registry"
```

## Task 2: A/B 联合分析与候选持久化

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/fission_runtime.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/replica_analysis_runtime.py` only for shared safe schema/normalization helpers if extraction avoids duplication; otherwise leave replica runtime unchanged.
- Modify: `local-runtime/wh_local/modules/pod_customization/billing_contract.py` to add the two-call fission analysis plan without changing replica analysis.
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/app/main.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_runtime.py`
- Test: `local-runtime/tests/test_pod_customization_billing_contract.py`
- Test: `local-runtime/tests/test_pod_customization_app_registration.py`

**Interfaces:**
- `FissionAnalysisRequest(source_asset_id, target_asset_id)`.
- `FissionAnalysisResult(source_spec, target_spec, style_vocabulary, element_vocabulary, palette_vocabulary, capability_candidates, rule_candidates, model_version, schema_version)`.
- `FissionAnalysisRuntime.analyze_assets(request, grant, call_ids, on_start, on_outcome) -> FissionAnalysisResult`.
- `PodCustomizationService.analyze_fission_assets(actor, request) -> dict` returns `analysis_id`, read-only summary, and success/failure state; it never returns internal vocabulary or registry candidates.

- [x] **Step 1: 写 strict schema、重试和隐私边界测试**

Fake HTTP session 验证 A/B 角色顺序、一次正常分析仅一次 provider POST、契约无效时最多额外一次修复 POST、二次失败返回失败记录。计费合同测试验证 `for_fission_analysis` 仅含稳定的 `analysis:1/2` 两个 `pod.title` call ID。测试响应前端 payload 只含摘要/状态/analysis_id，不包含候选词表、能力候选、规则候选、Prompt 或原始 JSON。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_runtime.py tests/test_pod_customization_billing_contract.py -q`
Expected: 裂变 runtime 和 `for_fission_analysis` 用例 FAIL；既有 billing contract 用例保持通过。

- [x] **Step 3: 实现分析 strict JSON schema 和标准化**

响应必须包含 A artwork facts、B product facts、用户摘要、候选风格/元素/颜色和候选能力/规则。模型自报 confidence 字段不进入门槛。字段缺失、类型错误、A/B 角色交叉或事实冲突属于契约失败。分析 attempt 上限为 2；以独立分析动作冻结稳定调用 ID `{analysis_id}:analysis:1` 和 `{analysis_id}:analysis:2`，feature 均为 `pod.title`。正常分析记录第 1 次 outcome，契约修复重试记录第 2 次，未使用调用记 `no_return`。沿用现有 POD `title` scope 零增量计费合同，但仍走 grant/outcome 生命周期；裂变分析不假设免费免授权。

- [x] **Step 4: 持久化分析并发布动态能力版本**

Task 2 仅在分析成功后持久化不可变候选和输入快照，不改变全局 current pointer；用户取消或未确认建批次不会发布能力变更。Task 3 的确认建批流程才负责能力发布和款级方案编排：读取 registry/template/compiler 快照、编排唯一方案并冻结计费，再调用 repository 原子入口。入口接收预期 registry versions，在单一 SQLite `BEGIN IMMEDIATE` 事务内重查幂等键和 current pointers；指针与预期版本不一致时整笔回滚并返回 `RegistryVersionConflict`，service 重新读取最新快照、重算确定性方案后重试，不重复调用豆包。版本冲突最多重算 3 次，仍持续冲突则释放/结算本次未使用调用并返回可重试错误。版本匹配时在同事务追加候选、读取发布后的 registry snapshot，并写入 fission batch、全部 variants、款级方案及 registry/template/compiler 快照；SQL 失败时版本 pointer、学习事件、批次和 variants 一起回滚。不得在事务内调用另开连接的 service/repository 方法。发布与建批次不增加豆包请求，满足「A/B 分析候选在本批生效」。

- [x] **Step 5: 接入 runtime 生命周期**

在 `main.py` 创建 `PodFissionAnalysisRuntime` 并注入 router/service；shutdown 时关闭 executor/session。服务实例共享单一 registry service/repository，避免 analyzer 自行写全局状态。

- [x] **Step 6: 跑 runtime、router 注册和 replica 隔离测试**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_runtime.py tests/test_pod_customization_billing_contract.py tests/test_pod_customization_app_registration.py wh_local/modules/pod_customization/tests/test_replica_analysis_runtime.py -q`
Expected: PASS；复刻 schema、版本、分析行为不变。

- [ ] **Step 7: 提交本任务**

```bash
git add local-runtime/wh_local/modules/pod_customization/fission_runtime.py local-runtime/wh_local/modules/pod_customization/billing_contract.py local-runtime/wh_local/modules/pod_customization/service.py local-runtime/wh_local/app/main.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_runtime.py local-runtime/tests/test_pod_customization_billing_contract.py local-runtime/tests/test_pod_customization_app_registration.py
git commit -m "feat(pod): analyze fission references and store candidates"
```

## Task 3: 裂变领域表、契约、上传与 API

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/migrations/024_fission_domain.sql`
- Modify: `local-runtime/wh_local/modules/pod_customization/contracts.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/router.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_api.py`
- Test: `local-runtime/tests/test_pod_request_limits.py`

**Interfaces:**
- `FissionImageRole = Literal["source_artwork", "target_product"]`。
- `FissionBatchCreate(client_request_id, analysis_id, requested_count: StrictInt 1..52, listing_fields)`。
- Routes: `POST /fission/images`、`POST /fission/analyze`、`POST /fission/batches`、`GET /fission/batches`、`GET /fission/batches/{batch_id}`、`POST /fission/batches/{batch_id}/styles/{style_index}/retry`。
- Repository methods: `create_fission_analysis`、`get_fission_analysis_for_actor`、`create_fission_batch_with_registry`、`get_fission_batch`、`list_fission_batches`、`get_fission_variant`。该原子入口接收已验证候选、预期 registry versions、编译后的款计划和 request hash；版本变化时以 `RegistryVersionConflict` 触发 service 重读、重算和最多 3 次提交尝试。

- [x] **Step 1: 寫 migration/API contract 測試**

測試 1 和 52 可建模、0/53/bool/string 拒绝；上传只接受两个规定 role；分析与资产必须属于当前 actor/workspace；幂等 key 相同且请求 hash 相同返回既有批次，不同 payload 返回 409；失败分析不可创建批次。注入 variant 写入失败时验证公共批次、fission 批次、registry pointer 和 learning event 全部回滚；并发 registry 版本变化返回 `RegistryVersionConflict`，service 按最新版本重算后再成功且 plan 冻结版本一致。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_api.py tests/test_pod_request_limits.py -q`
Expected: 新增裂变用例 FAIL，现有 POD request-limit 用例保持基线状态。

- [x] **Step 3: 增加独立裂变迁移表**

`024_fission_domain.sql` 创建 `pod_customization_fission_analyses`、`pod_customization_fission_batches`、`pod_customization_fission_variants`。分析表保存 A/B asset IDs、normalized facts、raw response、candidate payload（仅内部）、analysis/schema/model version 和 registry versions。批次表保存 idempotency key/hash、requested count、共同 listing fields 和 composition/registry/prompt/compiler snapshot versions。variant 表保存款号、`VariantPlan` JSON、unique `combination_fingerprint`、Prompt snapshot、图片/标题状态和错误。通过批次 FK 连接公共 `pod_customization_batches`，不得复用 `replica_batches/targets`。

- [x] **Step 4: 扩展模式和上传角色契约**

加入 `fission` 到 `PodBatchMode`，加入 `source_artwork`/`target_product` 资产 kind。裂变数量用独立常量 `MIN_FISSION_STYLES=1`、`MAX_FISSION_STYLES=52`。公共 batch `requested_count` 迁移已支持 1–200，不再重建该表。

- [x] **Step 5: 实现裂变 API、幂等和权限检查**

新增独立 route，沿用 `pod_customization.create/read` 权限。上传使用 20MB 单图上限与现有 multipart middleware；把 `/fission/images` 纳入 multipart 限制集合。分析时检查 asset ownership/kind。列表、详情、重试按 actor/workspace 隔离。

- [x] **Step 6: 实现 repository 分析和批次读写**

计费 grant 按现有 coordinator 流程取得；候选纯校验、registry/template/compiler 快照读取和唯一方案编排先完成。随后调用 `create_fission_batch_with_registry`：repository 在单个 `BEGIN IMMEDIATE` 事务中重查幂等键并比对预期 registry versions；冲突就完整回滚，service 重新读取、重算后最多再提交 3 次。版本一致时 repository 发布本次候选，取得实际发布版本，将该版本写入所有款计划快照，并创建公共 batch/fission batch/1..N variants。这样不会让方案按一个规则快照计算却冻结另一个版本。事务失败整组回滚，服务层对已冻结 grant settle/refund；事务成功后登记 durable billing run 并入队，入队失败走公共批次暂停恢复机制。

- [x] **Step 7: 跑 API、migration 与现有 replica API 测试**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_api.py tests/test_pod_customization_migrations.py wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py -q`
Expected: PASS；旧 replica tables 和 endpoints 不变。

- [ ] **Step 8: 提交本任务**

```bash
git add local-runtime/wh_local/modules/pod_customization/migrations/024_fission_domain.sql local-runtime/wh_local/modules/pod_customization/contracts.py local-runtime/wh_local/modules/pod_customization/repository.py local-runtime/wh_local/modules/pod_customization/service.py local-runtime/wh_local/modules/pod_customization/router.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_api.py local-runtime/tests/test_pod_request_limits.py
git commit -m "feat(pod): add fission analysis and batch APIs"
```

## Task 4: 独立构图模板池与确定性变体编排

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/fission_planner.py`
- Create: `local-runtime/wh_local/modules/pod_customization/fission_prompts.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/contracts.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/router.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/prompts.py` only to export/reuse existing Tier/color assignment helpers if importable without changing existing output.
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_planner.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_composition_runtime.py`

**Interfaces:**
- `FissionVariantPlan` fields match the approved spec: `style_index`, `style_family`, `primary_elements`, `secondary_elements`, `accent_elements`, `palette_primary`, `palette_secondary`, `palette_accent`, `composition_template_id/version`, `capability_registry_version`, `rule_registry_version`, `combination_fingerprint`.
- `plan_fission_variants(analysis, registry_snapshot, composition_snapshot, requested_count, batch_id) -> tuple[FissionVariantPlan, ...]`.
- `compile_fission_prompt(plan, source_spec, target_spec, rules, composition, attempt) -> str`.

- [x] **Step 1: 写 deterministic planner 和 prompt compiler 测试**

固定 seed 下两次编排结果逐字段一致；完整组合指纹唯一；合法近似主题允许；52 款边界可生成；候选空间不足且无法满足完整组合去重时，在计费前明确失败；四宫格全局契约、构图、产品可印区域和禁止项都在输出 Prompt，词表和内部候选不泄漏到 API 响应。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_planner.py -q`
Expected: FAIL，因为 planner/compiler 尚不存在。

- [x] **Step 3: 扩展构图 scope 为 fission 并使用默认模板**

在 `COMPOSITION_SCOPES` 加入 `fission`；更新 `_composition_scope`、`_default_composition_payload` 和 router/service docstrings。保存、编辑、激活、清空生效模板均必须按 scope 过滤；跨池 ID 返回 409。系统默认模板对 fission 有稳定 scope 与版本。

- [x] **Step 4: 实现确定性抽取和组合去重**

以 `(batch_id, style_index)` 派生随机种子。元素分组复用全定制主打/辅主/点缀 Tier 逻辑；配色复用 60/30/10 分配函数。对规范化后的风格 ID、排序元素 ID 和三角色配色 ID 计算 SHA-256 指纹。若生成数量超过完整组合空间，planner 在冻结计费前报出可生成数，不静默重复。

- [x] **Step 5: 实现四段提示词编译**

固定四宫格位置契约；融合模板四格说明、A 风格/元素变体、B 事实与能力规则、Tier 权重和颜色比例。保留原 POD 安全约束：不得复制 A 的人物/环境/水印，不得超出 B 可印区域，不得改变 B 产品结构。`attempt=2` 只强化产品结构/组合一致性，不另抽方案。

- [x] **Step 6: 将快照写入款计划并测试 scope 隔离**

确认创建时冻结 composition/version、能力/规则版本和 variant plans。模板后续改动不影响批次。补 full/replica/fission 三池互不覆盖的测试。

- [x] **Step 7: 跑 planner、模板与全定制权重回归**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_planner.py wh_local/modules/pod_customization/tests/test_composition_runtime.py wh_local/modules/pod_customization/tests/test_prompts.py -q`
Expected: PASS；全定制既有权重提示词快照保持不变。

- [ ] **Step 8: 提交本任务**

```bash
git add local-runtime/wh_local/modules/pod_customization/fission_planner.py local-runtime/wh_local/modules/pod_customization/fission_prompts.py local-runtime/wh_local/modules/pod_customization/contracts.py local-runtime/wh_local/modules/pod_customization/repository.py local-runtime/wh_local/modules/pod_customization/service.py local-runtime/wh_local/modules/pod_customization/router.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_planner.py local-runtime/wh_local/modules/pod_customization/tests/test_composition_runtime.py
git commit -m "feat(pod): compile deterministic fission variants"
```

## Task 5: Fission worker 与公共商品化输出

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/worker.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/replica_prompts.py` only if a shared reference contract helper can be extracted without changing replica prompt text; otherwise do not edit.
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_worker.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_api.py`

**Interfaces:**
- `PodBatchWorker._fission_style_prompt(batch, style_index, attempt) -> str` reads the persisted variant and frozen snapshots only.
- `PodBatchWorker._style_generation_inputs(...)` returns ordered image refs `(role="style_reference", A)` and `(role="target_product", B)` for fission.
- `PodCustomizationService.create_fission_batch(...)` registers the existing worker/billing run and returns standard batch response plus ordered variants.

- [x] **Step 1: 写首次生成、失败、恢复和重试测试**

Fake image runtime asserts one grid call per style per attempt, A/B reference order, exact persisted Prompt, no creative prompt; four panels published; ONNX metrics/fallback are produced by existing shared runtime; image failure after allowed attempts affects only that variant; title and export remain available for successful variants; retry uses same frozen plan and only target style changes status.

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_worker.py -q`
Expected: FAIL because worker does not recognize `mode="fission"`.

- [x] **Step 3: 新增 mode 分支，不重写公共媒体链路**

在 worker reference loading、prompt selection、retry/resume 和 finalization 处加入 `fission` 分支。复用 `_generate_listing_grid`、`_validate_style_grid`、`_process_style_grids` 和 `ai_runtime.split_listing_grid`，确保四图发布使用既有 ONNX 超分和 USM fallback。标题走与 full 相同链路。

- [x] **Step 4: 保存款级结果、版本失败事件和部分成功状态**

每款终态写入 fission variant 表并同步公共 batch item/title 状态。结果失败调用 Task 1 的 registry failure recorder。部分成功批次可返回成功款；batch final status 使用现有公共状态兼容值和 `fission` response 的 `partial` 投影，不扩展会破坏其他模式的通用状态机。

- [x] **Step 5: 添加款级重试与暂停恢复计划**

复用现有 style retry coordinator 和冻结授权机制。重试只为目标 `style_index` 冻结 image/title 调用，保留原 `VariantPlan`、模板版本、registry versions 和 Prompt snapshot。暂停恢复只重跑未完成款。

- [x] **Step 6: 跑 worker、replica/full 回归**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_worker.py wh_local/modules/pod_customization/tests/test_worker.py wh_local/modules/pod_customization/tests/test_replica_worker.py wh_local/modules/pod_customization/tests/test_title_workflow.py -q`
Expected: PASS；full/replica 分支的参考图和提示词断言不变。

- [ ] **Step 7: 提交本任务**

```bash
git add local-runtime/wh_local/modules/pod_customization/worker.py local-runtime/wh_local/modules/pod_customization/service.py local-runtime/wh_local/modules/pod_customization/repository.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_worker.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_api.py
git commit -m "feat(pod): generate fission variants through POD worker"
```

## Task 6: 按款计费与个人中心分类

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/billing_contract.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/billing.py`
- Modify: `web-frontend/src/modules/personal_center/api/personalCenterApi.ts`
- Modify: `web-frontend/src/modules/personal_center/pages/PersonalCenterPage.tsx`
- Test: `local-runtime/tests/test_pod_customization_billing_contract.py`
- Test: `local-runtime/tests/test_pod_customization_remote_billing.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_billing.py`
- Test: `web-frontend/src/modules/personal_center/pages/PersonalCenterPage.test.ts`

**Interfaces:**
- `POD_BILLING_PROFILE_FISSION = "pod_fission_v1"`。
- `PodCallPlan.for_fission_batch(batch_id, style_count)`、`for_fission_resume(...)`、`for_fission_style_retry(...)` all retain `pod.title` + `pod.image` outcome alignment and bill image by style.
- `PodCallPlan.for_fission_analysis(analysis_id)` is defined in Task 2: exactly two `pod.title` call IDs reserved for the initial analysis and one schema-repair attempt; the unused call settles as `no_return` under the existing zero-increment title scope.

- [x] **Step 1: 写批次计费合同和服务分类测试**

验证 1 和 52 款 profile、scope `title/four_grid`、幂等冻结、部分成功逐款扣款/退款、每款 5 次标题尝试不增加裂变图片单价、resume/retry 只冻结目标款；流水显示名称为「POD 爆款裂变」。分析两次预留和一次重试的对齐由 Task 2 的计费合同测试覆盖。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest tests/test_pod_customization_billing_contract.py tests/test_pod_customization_remote_billing.py wh_local/modules/pod_customization/tests/test_fission_billing.py -q`
Expected: 裂变画像相关断言 FAIL；现有画像测试保持通过。

- [x] **Step 3: 实现本地计划投影**

新增 initial/resume/retry 计划生成器，调用 ID 与款级 style index 对齐。服务按 batch `mode=fission` 选择 profile，所有重试和恢复均保持同一 profile。

- [x] **Step 4: 扩展计费 profile allowlist 和等价价格路径**

将 `pod_fission_v1` 纳入本地冻结、流水 POD 分类、scope 校验和结算 snapshot 校验；定价直接复用 `pod_random_v1` 的 price/multiplier 规则，不复制另一套价格常量。确保旧 `pod_random_v1`、`pod_semi_v1`、replica profile 的既有兼容行为。

- [x] **Step 5: 同步个人中心类型与名称**

前端 profile union 添加 `pod_fission_v1`，`POD_SERVICE_LABEL` 映射「POD 爆款裂变」。

- [x] **Step 6: 运行计费和本地流水测试**

Run: `cd local-runtime && .venv/bin/python -m pytest tests/test_pod_customization_billing_contract.py tests/test_pod_customization_remote_billing.py wh_local/modules/pod_customization/tests/test_fission_billing.py -q`
Run: `cd web-frontend && npm test`
Expected: PASS。

- [x] **Step 7: 配置计费服务发布依赖**

部署前确认 `RemotePodBillingCoordinator` 目标服务的 profile allowlist、POD scope 投影和单款定价分类接受 `pod_fission_v1`。该服务若不在当前 checkout，提交可审查的 API/发布依赖，不在客户端绕过白名单；生产启用入口前必须完成服务端配置和冻结/结算联调。

- [ ] **Step 8: 提交本任务**

```bash
git add local-runtime/wh_local/modules/pod_customization/billing_contract.py local-runtime/wh_local/modules/pod_customization/service.py local-runtime/wh_local/billing.py local-runtime/tests/test_pod_customization_billing_contract.py local-runtime/tests/test_pod_customization_remote_billing.py local-runtime/wh_local/modules/pod_customization/tests/test_fission_billing.py web-frontend/src/modules/personal_center/api/personalCenterApi.ts web-frontend/src/modules/personal_center/pages/PersonalCenterPage.tsx web-frontend/src/modules/personal_center/pages/PersonalCenterPage.test.ts
git commit -m "feat(billing): classify POD fission by style"
```

## Task 7: 前端上传、确认与批次创建

**Files:**
- Create: `web-frontend/src/modules/pod_fission/types.ts`
- Create: `web-frontend/src/modules/pod_fission/api/podFissionApi.ts`
- Create: `web-frontend/src/modules/pod_fission/components/FissionSourceUploader.tsx`
- Create: `web-frontend/src/modules/pod_fission/components/FissionProductUploader.tsx`
- Create: `web-frontend/src/modules/pod_fission/data/podFissionDraft.ts`
- Create: `web-frontend/src/modules/pod_fission/pages/PodFissionPage.tsx`
- Modify: `web-frontend/src/app/navigation/modules.ts`
- Modify: `web-frontend/src/app/layout/WorkspaceShell.tsx`
- Modify: `web-frontend/src/app/navigation/podCustomizationNavigation.test.ts`
- Test: `web-frontend/src/modules/pod_fission/api/podFissionApi.test.ts`
- Test: `web-frontend/src/modules/pod_fission/data/podFissionDraft.test.ts`

**Interfaces:**
- `FissionAnalysisResponse` contains only `analysis_id`, status, product summary, artwork summary and user-safe error.
- `CreateFissionBatchRequest` contains `analysis_id`, integer `requested_count`, shared listing fields and `client_request_id`.
- `podFissionApi`: `uploadImage(file, role)`、`analyzeAssets(body)`、`createBatch(body)`、`listBatches(limit, offset)`、`getBatch(id)`、`retryStyle(batchId, styleIndex)`。

- [x] **Step 1: 写 API、草稿和导航失败测试**

测试 multipart role/body、API 错误转用户文案、摘要类型不含候选池字段、draft account/workspace 隔离、POD 导航组包含 fission 且 defaultChildId 保持 full、WorkspaceShell 有 `pod_fission` 页面分发。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd web-frontend && npm test`
Expected: fission API/model/navigation test FAIL，因为新 module 未建立。

- [x] **Step 3: 实现类型、API、上传和草稿层**

图片角色只允许 `source_artwork` / `target_product`。前端不定义候选词表/能力/规则响应类型，避免服务端信息意外展示。draft 恢复前使用 assets endpoint 检查两张图仍可用；任一缺失要求重传。

- [x] **Step 4: 实现确认流程和数量校验**

分析成功才展示只读摘要和数量；数量只能输入整数 1–52。摘要不可编辑。生成按钮提交 `analysis_id + count + shared listing fields + request id`。分析失败可重试上传/取消；未点击确认不得调用创建批次 API。

- [x] **Step 5: 注册同级模块入口**

在 `pod_workflow.children` 加入「爆款裂变」，保持全定制为 defaultChildId；懒加载 `PodFissionPage`，导航 ID union 与 module metadata 一并更新。

- [x] **Step 6: 运行前端功能、导航和类型检查**

Run: `cd web-frontend && npm test && npx tsc --noEmit`
Expected: PASS。

- [ ] **Step 7: 提交本任务**

```bash
git add web-frontend/src/modules/pod_fission web-frontend/src/app/navigation/modules.ts web-frontend/src/app/layout/WorkspaceShell.tsx web-frontend/src/app/navigation/podCustomizationNavigation.test.ts
git commit -m "feat(pod): add fission creation workflow"
```

## Task 8: 批次结果、款级重试、规格卡和导出

**Files:**
- Create: `web-frontend/src/modules/pod_fission/data/podFissionModel.ts`
- Modify: `web-frontend/src/modules/pod_fission/pages/PodFissionPage.tsx`
- Modify: `web-frontend/src/modules/pod_fission/api/podFissionApi.ts`
- Modify: `web-frontend/src/modules/personal_center/api/personalCenterApi.ts`
- Modify: `web-frontend/src/modules/personal_center/pages/PersonalCenterPage.tsx`
- Test: `web-frontend/src/modules/pod_fission/data/podFissionModel.test.ts`
- Test: `web-frontend/src/modules/personal_center/pages/PersonalCenterPage.test.ts`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_export.py`

**Interfaces:**
- Batch response extends shared `PodBatch` with `mode="fission"`, A/B previews and ordered `variants`.
- Variant response contains style index, status, output image URLs, title status/copy, spec-card status and user-safe failure message; excludes raw prompt, vocabulary and registry contents.
- `normalizeFissionBatch(batch)` projects shared statuses into `queued/running/partial/completed/failed/cancelled` UI states.

- [x] **Step 1: 写 partial batch 和 successful-only export 测试**

后端测试部分成功只导出成功款、无成功款时拒绝空导出、共用 listing fields 套用到每个成功款、style retry 不改变其他款；前端模型测试 mixed success/failure 状态和可重试索引。

- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_export.py -q`
Run: `cd web-frontend && npm test`
Expected: 新裂变断言 FAIL。

- [x] **Step 3: 接入公共画廊、灯箱、标题与规格卡 UI**

复用 `PodBatchGallery`、`PodResultLightbox`、`SpecCardDrawer`、`PodBatchHistoryDrawer` 和 `PodFailedRetryDialog`。必要时给组件新增受控 props，而非复制一套画廊。详情只显示成品与业务字段，不显示内部组合词表和 prompt。

- [x] **Step 4: 实现成功款即时查看和导出**

批次运行中存在成功款即可显示结果与导出入口。导出集合以款级 image/title 状态均成功为准；失败款被排除但列表保留原款号。上架字段从批次快照读取，前端不能按款覆写。

- [x] **Step 5: 接入款级 retry 和进度轮询**

失败款触发单款 retry API；轮询只更新该 batch，保留已成功款和输出链接。request id 由 action UUID 生成，防止双击重复冻结。

- [x] **Step 6: 测试前端结果流和后端导出/标题/规格卡**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_export.py wh_local/modules/pod_customization/tests/test_spec_card_api.py wh_local/modules/pod_customization/tests/test_title_workflow.py -q`
Run: `cd web-frontend && npm test`
Expected: PASS。

- [ ] **Step 7: 提交本任务**

```bash
git add web-frontend/src/modules/pod_fission web-frontend/src/modules/personal_center/api/personalCenterApi.ts web-frontend/src/modules/personal_center/pages/PersonalCenterPage.tsx web-frontend/src/modules/personal_center/pages/PersonalCenterPage.test.ts local-runtime/wh_local/modules/pod_customization/tests/test_fission_export.py
git commit -m "feat(pod): show and export fission batch results"
```

## Task 9: 端到端验收与发布门槛

**Files:**
- Modify: `local-runtime/tests/test_pod_customization_app_registration.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/tests/test_fission_api.py`
- Modify: `web-frontend/src/app/navigation/podCustomizationNavigation.test.ts`
- Modify: `docs/superpowers/specs/2026-10-15-pod-fission-design.md` only when implementation reveals a material contract correction.

- [x] **Step 1: 跑裂变后端专项测试**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_fission_registry.py wh_local/modules/pod_customization/tests/test_fission_runtime.py wh_local/modules/pod_customization/tests/test_fission_planner.py wh_local/modules/pod_customization/tests/test_fission_api.py wh_local/modules/pod_customization/tests/test_fission_worker.py wh_local/modules/pod_customization/tests/test_fission_billing.py wh_local/modules/pod_customization/tests/test_fission_export.py -q`
Expected: PASS。

- [x] **Step 2: 跑 POD 后端回归**

Run: `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests tests/test_pod_customization_contracts.py tests/test_pod_customization_billing_flow.py tests/test_pod_customization_migrations.py tests/test_pod_request_limits.py tests/test_pod_customization_remote_billing.py -q`
Expected: PASS；如果已有基线失败，记录原始失败和与裂变改动的因果关系，不删除既有断言。

- [x] **Step 3: 跑前端测试、类型和生产构建**

Run: `cd web-frontend && npm run verify`
Run: `cd web-frontend && npm run build`
Expected: 两条命令均 exit 0。

- [ ] **Step 4: 手工验收核心用户路径**

用测试环境 A/B 图片跑 1 款和多款批次；确认摘要只读、词表/能力不显示；检查每款四宫格、拆图、超分、标题、规格卡和共用上架导出；人为制造一款失败确认其他成功款可用、失败款可独立重试；核对消费流水显示裂变标签和部分退款金额。

- [x] **Step 5: 验证能力学习和回滚的可追溯性**

确认一次分析产生一个全局追加版本并用于当前批次；另建新批次读取最新版本；已有批次仍能读回旧快照；同一版本连续 3 个终态图片失败回滚，基础设施/授权/取消失败不触发回滚。

- [ ] **Step 6: 检查发布配置**

确认远端计费服务白名单已加入 `pod_fission_v1` 且定价同 `pod_random_v1`；确认客户端和账单流水标签映射一致。未完成服务端支持时，不向生产用户开放创建入口。

- [x] **Step 7: 最终检查工作区差异**

Run: `git status --short`
Expected: 仅显示本功能预期文件；最初存在的三处 ClipForge/title runtime 改动仍保留且不在裂变提交中。

## 验收完成定义

- 用户能完成 A/B 上传、分析摘要确认、1–52 款裂变创建、进度查看、部分结果导出和失败款重试。
- 单批每款一个四宫格任务，所有变体完整组合唯一，方案可由快照重现。
- 动态全局能力库在当前批次即时生效、为新批次提供新版本、为历史批次冻结旧版本，并能按已确认阈值自动回滚。
- 账单按成功款结算且失败款退款，独立显示为「POD 爆款裂变」。
- 前后端专项测试、POD 回归和前端构建通过；既有未提交用户改动未被覆盖。

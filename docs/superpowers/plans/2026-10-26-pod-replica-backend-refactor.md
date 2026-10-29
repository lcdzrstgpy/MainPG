# POD 爆款复刻后端重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **执行状态（2026-10-26）：** Task 1–8 已全部完成；Task 9 的 Step 1–2 / Step 5 已完成，Step 3（真实环境冒烟）与 Step 4（线上日志观测）需在部署环境执行。逐 Task 证据见文末《实施记录》。

**Goal:** 把爆款复刻后端从「豆包识图 → 能力匹配 → 规则推导 → 豆包选构图参数 → 本地编译 Prompt」重构为「每个 A/B 对一次豆包调用直出主图提示词 → 主图锚定 → 构图模板派生三张」。

**Architecture:** 豆包只保留一次调用，输入 `[B, A]`，输出 `main_prompt`。主图用 `[B, A]` 生成；panel_2~4 只以主图为参考图，视角来自复刻构图模板（panel_1 锁定、后端忽略）。能力注册表、规则推导、四段式编译器、构图参数量产调用全部删除；计费形状保持 4 面板 × 2 次尝试。

**Tech Stack:** Python 3、Pydantic、SQLite 迁移、现有 POD service/repository/worker、Ark/豆包视觉 runtime、pytest。

**方案文档：** `docs/superpowers/specs/2026-10-26-pod-replica-anchor-panel-design.md`

## Global Constraints

| 约束 | 要求 |
|---|---|
| 参考图顺序 | 分析与主图生成统一 `[B(target_product), A(pattern_source)]`，即 image 1 = B、image 2 = A。顺序由 `MAIN_PROMPT_REFERENCE_ORDER = "B,A"` 常量控制（运行时常量名见 Task 2；spec 初稿写作 `REPLICA_MAIN_REFERENCE_ORDER`）。 |
| 豆包边界 | 只做一次调用，只输出 `main_prompt`；不返回事实字段、摘要或置信度。 |
| 主图视角 | 不可由用户或构图模板指定；模板 panel_1 锁定、后端忽略。 |
| 派生视角 | 只来自复刻构图模板的 panel_2/3/4；缺槽位用默认三拍兜底。 |
| 构图模板 | 保留模板池、生成、编辑、切换与跨 scope 409 校验；不新增第二套模板体系。 |
| 计划冻结 | 款级快照必须包含 `main_prompt`、派生三格指令、模板 id/版本、参考图顺序与各版本号。 |
| 历史批次 | 只读：可查看、导出，不支持下架重试、续跑、重新生成。 |
| 计费 | 每款 4 面板 × 2 次图片调用 + 标题，画像 `pod_replica_v1_b`；删除构图零计费计划。 |
| 兼容性 | 不改 `full` / `semi` / `fission` 行为；共享提示词段落搬迁必须保持原文逐字不变。 |
| 环境 | 后端验证使用 `conda run -n base`；单测不得调用真实豆包与真实图片 provider。 |

## 与现状的关键差异

1. **迁移编号**：方案文档写的是 `023`，但 `023`~`027` 已被裂变、模板视图、列式布局、账本占用；本计划使用 `028_replica_transfer_plans.sql`。
2. **运行时落点**：不新建独立 Ark lane，直接在 `replica_analysis_runtime.py` 内重写为「主图方案 runtime」，保留类名与 `_run` 底座，`app/main.py` 无需改装配。
3. **构图模板保留**：`resolve_replica_composition_template` 不删除，收窄为只返回 panel_2~4；旧批次不再支持重试，所以无需 legacy 计划读取器。
4. **分析失败不落新表**：新表只存成功方案；失败返回错误并写业务日志，避免把半成品方案写进缓存。

## 文件映射

### 新建

- `local-runtime/wh_local/modules/pod_customization/migrations/028_replica_transfer_plans.sql`
- `local-runtime/wh_local/modules/pod_customization/replica_panel_template.py`
- `local-runtime/wh_local/modules/pod_customization/prompt_anchor_blocks.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_plan_runtime.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_panel_template.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_transfer_plan_repository.py`

### 修改

- `replica_analysis_runtime.py`：重写为单次 `main_prompt` 调用。
- `replica_specs.py`：只保留精简 `ReplicaPlan`、`GenerationStrategy` 与版本常量。
- `replica_context.py`：读取精简计划，增加 legacy 计划守卫。
- `service.py`：分析、方案缓存、构图模板收窄、建批次、旧批次重试守卫。
- `repository.py`：方案表读写；款表计划快照映射。
- `contracts.py`：精简分析/目标/批次契约。
- `worker.py`：主图锚定、参考图改造、提示词取用、策略默认值。
- `billing_contract.py`：删除 `for_replica_composition` 与 `REPLICA_COMPOSITION_ATTEMPTS`。
- `composition_runtime.py`：复刻 scope 的生成提示词只描述派生三格，panel_1 固定占位。
- `column_prompts.py`：改为从 `prompt_anchor_blocks.py` 导入锚定段落，文本不变。
- `app/main.py`：不改装配（类名保留）；仅确认注入不变。

### 删除

- `replica_prompts.py`（旧批次历史回退路径；旧批次已只读）
- `replica_prompt_compiler.py`（四段式编译器只服务旧复刻链路）
- 相关测试：`test_replica_prompt_compiler.py`、`test_replica_prompts.py`
- **不删除**（实施修正）：`replica_capabilities.py` / `replica_planner.py` 及其测试——裂变直接复用 `match_capabilities` / `derive_rules`；`replica_specs.py` 的 A/B 事实模型同样保留给裂变。

## Task 1: 数据层（迁移 + repository）

**Files:**

- Create: `migrations/028_replica_transfer_plans.sql`
- Modify: `repository.py`
- Test: `tests/test_replica_transfer_plan_repository.py`

**Interfaces:**

- `create_replica_transfer_plan(*, plan_id, workspace_id, owner_user_id, source_asset_id, target_asset_id, prompt_version, model, reference_order, main_prompt) -> None`
- `find_replica_transfer_plan(workspace_id, owner_user_id, source_asset_id, target_asset_id, prompt_version, model, reference_order) -> dict | None`

- [ ] **Step 1: 写迁移。** 新建 `028_replica_transfer_plans.sql`：

```sql
CREATE TABLE IF NOT EXISTS pod_customization_replica_transfer_plans (
    plan_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    target_asset_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    reference_order TEXT NOT NULL,
    main_prompt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (workspace_id, owner_user_id, source_asset_id, target_asset_id, prompt_version, model, reference_order),
    FOREIGN KEY (source_asset_id) REFERENCES pod_customization_assets (asset_id),
    FOREIGN KEY (target_asset_id) REFERENCES pod_customization_assets (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_pod_replica_transfer_pair
    ON pod_customization_replica_transfer_plans
       (workspace_id, owner_user_id, source_asset_id, target_asset_id, created_at DESC);
```

- [ ] **Step 2: 写 repository 失败测试。** 覆盖：同键重复写入返回既有记录或幂等、不同 `reference_order` 不命中、跨用户不命中、空 `main_prompt` 拒绝。
- [ ] **Step 3: 实现 repository。** 插入时用 `INSERT ... ON CONFLICT(...) DO UPDATE SET main_prompt = excluded.main_prompt`；查询严格按 `workspace_id + owner_user_id` 限定。
- [ ] **Step 4: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_transfer_plan_repository.py -q`，预期通过。

## Task 2: 主图方案 runtime（单次豆包调用）

**Files:**

- Modify: `replica_analysis_runtime.py`
- Test: `tests/test_replica_plan_runtime.py`

**Interfaces:**

- `MAIN_PROMPT_VERSION = "pod-replica-main-prompt-v2"`
- `MAIN_PROMPT_REFERENCE_ORDER = "B,A"`
- `generate_main_prompt(source_asset: ReplicaAssetImage, target_asset: ReplicaAssetImage, *, grant, call_id, call_ids=None, on_start=None, on_outcome=None) -> ReplicaMainPromptResult`
- `ReplicaMainPromptResult(main_prompt: str, model: str, prompt_version: str, attempt_count: int)`

- [ ] **Step 1: 写失败测试。** 用假 session 断言：消息里第 1 张是 B、第 2 张是 A；合法 JSON 返回 `main_prompt`；空字符串或非法 JSON 触发契约重试；达到 `REPLICA_ANALYSIS_ATTEMPTS` 后失败；响应不得包含事实字段。
- [ ] **Step 2: 替换 schema。** 输出严格 JSON：`{"main_prompt": string}`，`additionalProperties: false`，`required: ["main_prompt"]`；长度上限 `MAIN_PROMPT_MAX_LENGTH = 12000`。
- [ ] **Step 3: 重写消息构造。** 系统提示词写入复刻铁律：只复制 A 的图案/配色/排布、不复制 A 的背景人物道具水印、优先满印（软引导，尽量覆盖提手/肩带/口袋等软质可印部件）、保持 B 结构材质部件、图案只落可印区、不新增文字 Logo 贴纸、参考图顺序固定 image 1 = B / image 2 = A。用户消息按 B、A 顺序附两张 data URL。
- [ ] **Step 4: 实现 `generate_main_prompt`。** 复用现有 `_run`（重试、grant、on_start/on_outcome），成功返回精简结果；删除 `generate_composition_plan`、`analyze_structure_views`（结构识图在别处使用，保留跨模块入口时先确认引用）。
- [ ] **Step 5: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_plan_runtime.py -q`。

## Task 3: 契约与分析服务

**Files:**

- Modify: `contracts.py`
- Modify: `service.py`
- Test: `tests/test_replica_api_main_flow.py`

**Interfaces:**

- `ReplicaAnalyzeRequest(source_asset_id, target_asset_id)`
- `analyze_replica_assets(actor, request) -> {analysis_id, status, source_asset_id, target_asset_id, prompt_version, model, cached}`

- [ ] **Step 1: 精简契约。** `ReplicaAnalyzeRequest` 删除 `fact_overrides` / `presentation_preferences`；`ReplicaTargetCreate` 删除二者；`ReplicaBatchCreate` 保留 `composition_id`、删除 `source_confirmed`；`generation_strategy` 仅允许 `independent_panels`（缺省按 `independent_panels`）。
- [ ] **Step 2: 写分析服务测试。** 断言：首次调用落方案表；第二次同 A/B 命中缓存且不再调用 runtime；返回体不含 `source` / `target` / `candidate_capabilities` / 置信度。
- [ ] **Step 3: 改写 `analyze_replica_assets`。** 校验资产角色 → 查 `find_replica_transfer_plan` → 命中直接返回 → 未命中冻结 `_freeze_replica_analysis` → `generate_main_prompt` → 写入方案表 → 返回精简响应。失败不写方案表，`billing_run.settle()` 保持现有语义。
- [ ] **Step 4: 删除旧逻辑。** 删 `_replica_analysis_payload`、`normalize_target_analysis` / `match_capabilities` 引用、失败分析落库分支。
- [ ] **Step 5: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py -q`。

## Task 4: 构图模板收窄（只作用于派生三格）

**Files:**

- Modify: `service.py`（`resolve_replica_composition_template`）
- Modify: `composition_runtime.py`（复刻 scope 生成提示词）
- Test: `tests/test_replica_api_main_flow.py`、`tests/test_composition_runtime.py`（如存在）

**Interfaces:**

- `resolve_replica_composition_template(actor, composition_id) -> CompositionTemplateOptions`
  - `panel_slots = ("panel_2", "panel_3", "panel_4")`
  - `required_slots` 同上
  - `panel_instructions` 只含这三格

- [ ] **Step 1: 写失败测试。** 覆盖：复刻模板只返回三格；缺 panel_3 返回 409；全定制模板用于复刻返回 409；无复刻模板时回退默认三拍。
- [ ] **Step 2: 收窄解析。** 读取模板 `panels` 时只取 panel_2/3/4；任一为空抛 `PodRepositoryError("复刻构图模板缺少派生视角", 409)`；默认兜底从 `DEFAULT_COMPOSITION_PANELS` 取 2~4。
- [ ] **Step 3: 调整模板生成提示词。** `_messages_for_generation` 在 `scope == "replica"` 时：任务描述只要求三张派生图；`panel_1` 固定占位（zh「主图视角由 AI 方案决定，不参与构图」/ en 对应英文）；`instructions` 明确「不要描述主图机位」。
- [ ] **Step 4: 落库前覆盖 panel_1。** `service.generate_composition` 在 `scope == replica` 时把返回的 `panel_1` 覆盖为占位常量，保证四格结构完整但不携带用户/模型给出的主图视角。
- [ ] **Step 5: 运行。** 相关 API 与 runtime 测试。

## Task 5: 建批次与计划冻结

**Files:**

- Modify: `service.py`（`create_replica_batch`、删除 `_resolve_replica_plans` / `_replica_plan_fingerprint` / `_replica_plan_row`）
- Modify: `repository.py`（`create_replica_batch` 的计划映射）
- Modify: `billing_contract.py`（删 `for_replica_composition`、`REPLICA_COMPOSITION_ATTEMPTS`）
- Test: `tests/test_replica_api_main_flow.py`、`tests/test_replica_billing.py`

**Interfaces:**

- 款级计划快照（写入 `replica_plan_json`）：

```json
{
  "plan_version": "replica-plan-v4",
  "main_prompt": "...",
  "prompt_version": "pod-replica-main-prompt-v2",
  "image_model_version": "...",
  "reference_order": "B,A",
  "composition_template_id": "...",
  "composition_template_version": "...",
  "derived_panels": {"panel_2": "...", "panel_3": "...", "panel_4": "..."},
  "generation_strategy": "independent_panels"
}
```

- [ ] **Step 1: 写失败测试。** 覆盖：缺 `analysis_id` 的目标直接 409；方案表查不到直接 409；成功批次的款表 `replica_plan_json` 含 `derived_panels`；不再冻结任何 `pod.title` 构图调用；计费计划仍 4×2 + 标题。
- [ ] **Step 2: 重写 `create_replica_batch`。** 先校验全部目标都有 `analysis_id` 与可用方案；解析一次复刻构图模板；为每款构造上述快照；删除构图计费 run 与 `_resolve_replica_plans` 调用。
- [ ] **Step 3: 调整 repository 映射。** `prompt_snapshot` 写 `main_prompt`；`template_version` 写构图模板版本；`compiler_version` 写 `MAIN_PROMPT_VERSION`；`capability_registry_version` / `rule_version` / `plan_fingerprint` 写空串；`generation_strategy` 写 `independent_panels`。
- [ ] **Step 4: 删除构图计费。** 删 `_freeze_replica_composition`、`for_replica_composition`、`REPLICA_COMPOSITION_ATTEMPTS`；保留 `for_replica_analysis` 与 `for_replica_batch`。
- [ ] **Step 5: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_billing.py -q`。

## Task 6: 生成链路（主图锚定 + 派生三张）

**Files:**

- Modify: `replica_specs.py`（精简 `ReplicaPlan`）
- Modify: `replica_context.py`（读取新计划 + legacy 守卫）
- Modify: `worker.py`
- Modify: `service.py`（`_replica_batch_strategy` 默认值）
- Test: `tests/test_replica_worker.py`、`tests/test_replica_independent_panels.py`

**Interfaces:**

- `ReplicaPlan`（新）：

```python
class ReplicaPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_version: str = "replica-plan-v4"
    main_prompt: str
    prompt_version: str
    image_model_version: str = ""
    reference_order: str = "B,A"
    composition_template_id: str = ""
    composition_template_version: str = ""
    derived_panels: dict[str, str] = {}
    generation_strategy: GenerationStrategy = "independent_panels"
```

- `load_frozen_plan(batch, style_index) -> ReplicaPlan`：先解析 JSON 的 `plan_version`，不是 `replica-plan-v4` 抛 `LegacyReplicaPlanError`。
- `_generate_listing_panels(batch, call, style_index, build_request, billing_run, execution_epoch=None)`：先生成 panel_1，再派生 2~4。
- `_style_generation_inputs(..., panel_index=None, anchor: bytes | None = None)`：panel_1 `[B, A]`；panel_2~4 `[主图]`。

- [ ] **Step 1: 写失败测试。** 覆盖：panel_1 参考图顺序为 B、A；panel_2~4 只有一张参考图且内容等于 panel_1 输出；主图两次失败不调用后续面板；派生提示词包含锚定锁与「禁止复刻主图机位」；派生视角来自计划里的 `derived_panels`。
- [ ] **Step 2: 精简计划模型。** 替换 `replica_specs.py` 中旧 `ReplicaPlan` 与事实/能力/规则/构图模型；保留 `GenerationStrategy` 与版本常量。
- [ ] **Step 3: 改造 `replica_context`。** `load_frozen_plan` 按新模型读取；新增 `LegacyReplicaPlanError`；`target_requires_frozen_plan` 保持「带 analysis_id 必须有计划」。
- [ ] **Step 4: 改造 worker 生成顺序。** `_generate_listing_panels` 循环改为：先生成 panel_1 并取 `media.content` 为锚点；`anchor is None` 且 panel_index > 1 直接报错；其余沿用 `_generate_one_panel` 的 2 次尝试。
- [ ] **Step 5: 改造参考图与提示词。** `_style_generation_inputs` 的 replica 分支：panel_1 读 A/B 并按 `[B, A]` 排序；panel_2~4 用 `ListingReferenceImage(role="main_view", content=anchor)`。`_replica_style_prompt`：panel_1 返回 `plan.main_prompt`（attempt 2 追加一句重试提示）；panel_2~4 调 `build_replica_panel_prompt`。
- [ ] **Step 6: 策略默认值。** `_replica_batch_strategy` 无条件返回 `independent_panels`；删除 `REPLICA_PANEL_STRATEGY_ENABLED` 与相关注释。
- [ ] **Step 7: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_worker.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_independent_panels.py -q`。

## Task 7: 派生提示词模板与共享锚定段落

**Files:**

- Create: `prompt_anchor_blocks.py`
- Create: `replica_panel_template.py`
- Modify: `column_prompts.py`
- Delete: `replica_prompt_compiler.py`、`replica_prompts.py`（`replica_capabilities.py` / `replica_planner.py` 保留给裂变，见文末实施记录）
- Test: `tests/test_replica_panel_template.py`

**Interfaces:**

- `DEFAULT_DERIVED_PANEL_DIRECTIONS: dict[str, str]`（panel_2/3/4）
- `build_replica_panel_prompt(plan: ReplicaPlan, panel_index: int, attempt: int = 1) -> str`

- [ ] **Step 1: 抽取共享段落。** 把 `column_prompts.py` 中的 `_ANCHOR_BINDING`、`_RIGID_ARTWORK_LOCK`、`_ANCHOR_CONTINUITY`、`_CAMERA_MUST_DIFFER`、`_OUTPUT_FORMAT_LOCK` 原样搬到 `prompt_anchor_blocks.py`（英文逐字不变），`column_prompts.py` 改为导入，避免两份漂移。
- [ ] **Step 2: 写模板失败测试。** 覆盖：panel_2/3/4 分别包含图案特写、结构/材质细节、中性背景完整产品；三张互不相同；包含锚定锁与「禁止复刻主图机位」；attempt 2 追加重试句；输出格式是单帧而不是 2×2。
- [ ] **Step 3: 实现 `replica_panel_template.py`。** 结构固定：锚点绑定 → 硬质图案锁 → 连续/差异分离 → 几何形态锁 → 本格拍法（来自 `plan.derived_panels`，缺失用默认）→ 单帧输出 → 禁止项。不注入图案元素清单、配色、场景或美术指导。
- [ ] **Step 4: 删除旧编译器与旧领域层。** 删 `replica_prompt_compiler.py` 与 `replica_prompts.py`（`replica_capabilities` / `replica_planner` 因裂变复用而保留）；清理 `service.py` / `worker.py` / `tests` 中的 import；`replica_prompts.build_replica_listing_prompt` 的历史回退路径一并删除（旧批次只读）。
- [ ] **Step 5: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_panel_template.py -q`；再跑 `tests/test_column_prompts.py`（如存在）确认共享段落搬迁未改变全定制文本。

## Task 8: 旧批次守卫（只读）

**Files:**

- Modify: `service.py`（`regenerate_style`、`retry_failed`、`resume_batch`）
- Modify: `worker.py`（`process_batch` 防御性守卫）
- Test: `tests/test_replica_api_main_flow.py`

- [ ] **Step 1: 写失败测试。** 构造旧计划（无 `plan_version` 或 `plan_version=1`）的复刻批次，断言重试、批量重试、续跑均返回 409，且没有产生 provider 调用或新的计费冻结。
- [ ] **Step 2: 实现守卫。** 新增 `_require_replica_retry_supported(batch)`：仅当 `mode == replica` 时检查全部款计划版本，非 `replica-plan-v4` 抛 `PodRepositoryError("历史批次不支持重试", 409)`；三个入口在冻结计费之前调用。
- [ ] **Step 3: worker 防御。** `process_batch` 入口若发现 replica 旧计划，直接失败并标记，不进入生图。
- [ ] **Step 4: 运行。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py -q`。

## Task 9: 回归与验收

**Files:**

- Modify: 测试文件与文档
- 方案文档：补记迁移编号修正（`023` → `028`）

- [ ] **Step 1: 后端全量复刻测试。**

```bash
conda run -n base pytest \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_worker.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_independent_panels.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_billing.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_persistence.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_export.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_transfer_plan_repository.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_plan_runtime.py \
  local-runtime/wh_local/modules/pod_customization/tests/test_replica_panel_template.py \
  -q
```

- [ ] **Step 2: 跨模块回归。** 跑 `tests/test_pod_customization_billing_contract.py`、`tests/test_pod_billing_v2_contract.py`、`tests/test_pod_customization_app_registration.py`，确认 full/semi/fission 未受影响。
- [ ] **Step 3: 冒烟。** 真实环境手动提交 1 款：上传 A/B → 自动分析 → 选择复刻构图模板 → 提交 → 检查 4 张图、标题、冻结/结算回执；确认主图参考图顺序为 B、A，派生图为单张主图参考。
  > 前端阶段完成前 UI 无法提交新契约，冒烟可先直连同一组接口：`POST /api/pod-customization/replica/images`（role=source/target）→ `POST /replica/analyze` → `POST /replica/batches` → `GET /replica/batches/{id}`。端口与本地令牌以启动器为准。
- [ ] **Step 4: 观测。** 检查业务日志：方案命中缓存、主图耗时、面板级重试、`no_return` 比例。
- [ ] **Step 5: 文档同步。** 更新方案文档的迁移编号与实施偏差；在 `docs/2026-10-14-POD爆款复刻与标题风控更新要点.md` 或新增说明中标注旧链路下线。

## 验收标准

- 新批次只产生 1 次豆包方案调用（同 A/B/版本/顺序命中缓存），不再有构图参数调用。
- 每款 4 张单图：主图参考 `[B, A]`，派生三张参考 `[主图]`。
- 派生视角来自复刻构图模板 panel_2/3/4；panel_1 模板内容对结果无影响。
- 计费形状与画像不变，历史批次重试/续跑返回 409。
- `full` / `semi` / `fission` 测试全绿。

## 风险与回滚

| 风险 | 应对 |
|---|---|
| 单次豆包输出过长或超时 | schema 限制 `main_prompt` 长度；保留 3 次契约重试；失败不写缓存，可重试。 |
| 主图错误放大到三张派生图 | 先跑 1~2 款人工对比；主图失败直接整款失败，不降级。 |
| 构图模板被写成主图视角 | 前端锁定 + 后端忽略 panel_1 + 单测覆盖。 |
| 共享提示词段落搬迁改变全定制行为 | 英文逐字搬迁，跑全定制提示词测试对比。 |
| 旧批次卡在暂停状态 | 上线前 drain；守卫统一返回 409，避免半途执行。 |

回滚：保留 `027` 及以前迁移与新表；代码回滚时新表可留在库中不影响旧逻辑；不删除任何历史数据。

## 实施记录（2026-10-26）

> **2026-10-27 追加**：方案提示词升级到 `pod-replica-main-prompt-v2`——在豆包识图阶段加入**软引导** `coverage_preference`：尽量铺满软质可印表面（主面/侧面/口袋/翻盖/提手/肩带等），减少大面积空白与纯色部件，但覆盖范围仍由模型按产品结构判断（不写死覆盖率、不做硬约束）。版本号进入方案缓存键，旧方案保持冻结，重新分析才会用到新提示词。

Task 1~9 已全部落地，后端测试全绿（`pod_customization/tests` **737 passed**；迁移注册表 / 崩溃安全 / 计费契约 / v2 契约 / app 装配与 OpenAPI 契约 **69 passed**）。

| Task | 落点 | 结果 |
|---|---|---|
| 1 | `migrations/028_replica_transfer_plans.sql`、`pod_migrations.py` 契约、`repository.create/find/get_replica_transfer_plan` | 唯一键幂等写入、跨用户隔离、空提示词拒绝 |
| 2 | `replica_analysis_runtime.py` 重写 | `generate_main_prompt` 单次调用、`[B, A]` 顺序、严格 `main_prompt` schema、契约重试 |
| 3 | `contracts.py` / `service.analyze_replica_assets` | 请求与响应瘦身；命中缓存不再调豆包；失败不落方案表 |
| 4 | `service.resolve_replica_composition_template`、`composition_runtime` | 只返回 panel_2~4；模板缺任一派生槽位 → 409；panel_1 固定占位并在落库前覆盖 |
| 5 | `service.create_replica_batch` / `_replica_plan_rows` | 冻结 v4 快照；缺 `analysis_id`、方案查不到/不属于本人均 409；删除构图计费与指纹链路 |
| 6 | `worker` / `replica_context` / `replica_specs` | 主图锚定、派生三格只用主图参考、策略固定独立面板、旧计划 `LegacyReplicaPlanError` |
| 7 | `prompt_anchor_blocks.py`、`replica_panel_template.py`、`column_prompts.py` | 共享锚定段落逐字搬迁；删除旧编译器与旧 Prompt |
| 8 | `service._require_replica_retry_supported`、`worker._require_supported_replica_plan` | 单款重试 / 批量重试 / 续跑统一 409；worker 入口防御 |
| 9 | 回归 | 复刻 + 裂变 + 全定制/半定制 + 迁移契约全部通过（Step 3/4 需部署环境执行） |

### 与计划的偏差

1. **能力/规则模块保留**：`replica_capabilities.py`、`replica_planner.py` 与 `replica_specs.py` 的 A/B 事实模型被**裂变**直接复用（`fission_planner` / `fission_specs`）。按计划删除会破坏 fission，故只从复刻链路摘除 import，文件保留。
2. **旧分析表只读**：`pod_customization_replica_analyses` 保留历史数据；`save/find/get_replica_analysis` 与 `find_frozen_replica_plan` 已删除（无调用方）。
3. **删批次连带清方案**：方案表按 A/B 缓存、带资产外键，`delete_batch` 现在会一并删除该批次的方案行（否则资产无法回收）。
4. **前端阶段已同步完成（2026-10-26）**：`web-frontend/src/modules/pod_replica` 已改为新契约——上传 A/B 后自动生成主图方案（只显示就绪/失败，不展示事实、无确认步骤）、提交只带 `analysis_id`、草稿版本升到 v2（旧草稿自动作废并提示重传）、复刻构图模板主图格锁定；`CompositionPanelsEditor` 在 replica scope 下禁用 panel_1。
5. **`target_requires_frozen_plan` 删除**：worker 不再保留旧 Prompt 回退分支，缺计划一律抛错，等价于原「带 analysis_id 必须有计划」的硬失败语义。
6. **anchor 传递媒体对象**：`_style_generation_inputs(..., anchor=GeneratedMedia)` 而非裸 `bytes`，因为派生参考图需要同时带上 `content_type`；内容仍严格等于 panel_1 的输出。

### 待验证（需真实环境）

- **Step 3 冒烟**：`create_app` 的 OpenAPI 已由 `test_replica_openapi_contract_drops_legacy_fields` 固化为新契约（分析只收 A/B、目标只留 `analysis_id`、批次无 `source_confirmed`）。执行时需用户提供本地启动端口与令牌，并确认允许产生**一次真实豆包 + 4 张图的计费**。
- **Step 4 观测**：部署后看业务日志「复刻方案命中缓存 / 复刻方案生成 / 面板级重试 / no_return 比例」。
- 参考图组合实测：P1 `[B, A]` / P2 `[A, B]` / P3 只给 `[B]`，确定最终顺序。
- 主图提示词长度分布与豆包超时、`no_return` 比例。
- 复刻构图模板生成后的派生视角是否可直接使用（默认定向文案可能偏细）。
- 前端阶段：已完成并通过 `npm run verify`（511 tests）与 `npm run build`；旧草稿（v1）会被清空并提示重新上传。
- 部署侧：本地服务已在 127.0.0.1:8010 重启（迁移 028 已应用，`/openapi.json` 与静态资源均已是新契约）。

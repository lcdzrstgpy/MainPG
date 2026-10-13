# POD 爆款复刻主流程实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 把 POD 爆款复刻从“固定单函数 Prompt”升级为覆盖全品类的事实分析、能力匹配、确定性规则、构图模板、四段式 Prompt 编译和可恢复生成主流程。

**Architecture:** A 原素材图和 B 目标产品图先由豆包输出结构化事实；本地代码把事实标准化，匹配通用能力并推导规则。用户确认展示偏好后，系统复用全定制构图模板调用豆包生成结构化摄影参数，随后冻结一个 `ReplicaPlan`，由代码编译最终 Prompt 并调用现有图片生成管线。所有输入、版本、计划和 Prompt 都保存到款级快照，重试和恢复只复用快照。

**Tech Stack:** Python 3、Pydantic、SQLite 迁移、现有 POD service/repository/worker、Ark/豆包多模态 runtime、React + TypeScript 前端、pytest、现有 Node 测试工具。

## Global Constraints

| 约束 | 要求 |
|---|---|
| 参考图角色 | A 永远是 `pattern_source`，B 永远是 `target_product`，顺序不能交换。 |
| 豆包边界 | 豆包只做 A/B 事实分析和摄影构图参数生成；不得由豆包自由生成规则或最终 Prompt。 |
| 代码边界 | 事实标准化、能力匹配、规则推导、计划冻结、Prompt 编译、持久化、重试、计费和恢复全部由本地代码完成。 |
| 能力集合 | 初始能力 ID 固定为 `flat_print`、`local_print`、`surface_wrap`、`multi_piece`、`standalone_display`、`hand_worn`、`body_worn`、`wall_mounted`、`macro_detail`、`white_background`、`fabric_material`、`paper_material`、`plastic_material`、`metal_material`、`gel_material`。 |
| 能力语义 | 能力表示“产品支持的处理或展示方式”，不代表本次任务一定拍该画面；`hand_worn` 只有用户或已选构图模板选中手部展示时才进入本次计划。 |
| 多部件 | `multi_piece` 是结构要求；至少一个画面必须展示全部部件。 |
| 构图 | 复用全定制构图模板；模板只负责摄影表达，不负责产品事实和复刻硬约束。提交时冻结。 |
| Prompt | 固定四段：全局复刻契约、目标事实与规则、冻结摄影构图、输出格式与禁止项。最终文本由代码编译。 |
| 复现 | 同一份分析、能力、规则、用户选择、模板和版本必须得到同一份 `ReplicaPlan`。失败重试、暂停恢复、进程重启均使用冻结计划。 |
| 第一阶段范围 | 不做视觉质检、`QualityReport`、质量门禁、失败码驱动自动修复和第四次豆包质检调用。图片生成、下载、拆图、落库成功即可交付。 |
| 兼容性 | 不改变 `full` 和 `semi` 既有行为；不要修改当前无关的 ClipForge 改动：`local-runtime/tests/test_clipforge_process.py`、`local-runtime/wh_local/modules/clipforge/process.py`。 |
| 环境 | 后端验证使用 `conda run -n base`；不调用真实豆包和真实图片 provider。 |

## 实现偏差与验收备注

以下为实作时相对本计划的明确取舍，均已在评审中确认或经代码核对：

1. **构图模板改为按模块分池**：计划原口径是「复用全定制构图模板」，但全定制模板是产品化的（伞/露营/陶瓷瓶等具体场景），跨模块复用会污染复刻。实作新增迁移 `021_composition_scope`，给 `pod_customization_compositions` 加 `scope`（`full`/`replica`），两池各自独立「生效」；复刻只认 replica 池，显式传全定制模板返回 409，无复刻模板时回退系统默认机位。
2. **独立面板的重试预算**：Task 7 Step 3 原文写「grid 预留 1 次、independent panels 预留 4 次」。实作保持 grid 既有「每款 2 次尝试」不变（避免回归 full/semi 计费与结算），independent_panels 按确认口径改为「每款 4 面板 × 2 次尝试 = 8 次」，且单面板失败只重试该面板。两款计费口径都以 link（1 款 = 1 link）为准，用户积分不变。
3. **调用级 Prompt 快照**：independent_panels 下款级 `prompt_snapshot` 仍是计划里的四宫格规范文本；本次真实提交的 4 个单面板提示词以 JSON 清单写入调用级快照（`pod_customization_generation_calls.prompt_snapshot`），保证审计可追溯。
4. **缺计划不回退**：带 `analysis_id` 的主流程款若缺冻结计划，worker 直接失败等待修复（不再静默回退旧 Prompt / grid）；只有无 `analysis_id` 的历史批次才允许兼容回退。同时款级计划与目标行改为同事务写入，消灭「有 analysis_id 却没有计划」的中间态。
5. **A（原素材）低置信度门禁**：`validate_plan_ready` 同时检查 source 与 target；A 有低置信度字段时必须由用户确认（`ReplicaBatchCreate.source_confirmed`）才允许提交。
6. **生图模型版本冻结**：`ReplicaPlan.image_model_version` 随计划写入当前 POD 生图模型版本。

已知的既有红灯（与本功能无关，未在本次改动）：店小秘导出测试仍按 42 列断言而模板已 51 列（来自 `97bc6c0e`）；工作区模板共享测试失败（来自更早的 `35884d78`）。

## 文件映射

### 新建

- `local-runtime/wh_local/modules/pod_customization/replica_specs.py`：A/B 事实、能力、规则、构图计划和 `ReplicaPlan` 的 Pydantic 模型，以及标准化函数。
- `local-runtime/wh_local/modules/pod_customization/replica_capabilities.py`：能力注册表、能力定义和事实到能力的匹配。
- `local-runtime/wh_local/modules/pod_customization/replica_planner.py`：确定性规则推导、展示计划选择和计划校验。
- `local-runtime/wh_local/modules/pod_customization/replica_prompt_compiler.py`：四段式 Prompt 编译器。
- `local-runtime/wh_local/modules/pod_customization/replica_analysis_runtime.py`：豆包 A/B 图片分析与构图参数生成的 runtime。
- `local-runtime/wh_local/modules/pod_customization/migrations/020_replica_analysis_plan.sql`：分析快照、款级计划和版本字段的增量迁移。
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_specs.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_capabilities.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_planner.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_prompt_compiler.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_analysis_runtime.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py`

### 修改

- `local-runtime/wh_local/modules/pod_customization/contracts.py`：增加分析请求、事实覆盖、展示偏好、构图模板和策略字段。
- `local-runtime/wh_local/modules/pod_customization/runtime_contracts.py`：补充独立面板生成所需的请求契约，保留现有两张参考图语义。
- `local-runtime/wh_local/modules/pod_customization/repository.py`：保存分析快照、冻结 `ReplicaPlan` 和实际 Prompt 快照；校验分析与资产归属。
- `local-runtime/wh_local/modules/pod_customization/service.py`：编排分析、确认、建批次、重生成和模板选择。
- `local-runtime/wh_local/modules/pod_customization/router.py`：新增分析、确认和批次创建字段的 API。
- `local-runtime/wh_local/modules/pod_customization/replica_prompts.py`：保留兼容入口，内部转调新编译器或明确拒绝旧的自由文本路径。
- `local-runtime/wh_local/modules/pod_customization/replica_context.py`：读取款级冻结计划而不是重新推导。
- `local-runtime/wh_local/modules/pod_customization/worker.py`：按冻结计划编译 Prompt，支持 grid/independent_panels，移除对固定单函数 Prompt 的依赖。
- `local-runtime/wh_local/modules/pod_customization/ai_runtime.py`：补充独立面板调用能力，保留现有 grid 轮询、下载、错误分类和超时策略。
- `local-runtime/wh_local/modules/pod_customization/billing_contract.py`、`remote_billing.py`：按生成策略计算 provider call 数量并冻结计费计划。
- `local-runtime/wh_local/app/main.py`：注入 `PodReplicaAnalysisRuntime`。
- `web-frontend/src/modules/pod_replica/data/podReplicaModel.ts`、`types.ts`、`podReplicaApi.ts`、`PodReplicaPage.tsx`、`ReplicaTargetDrawer.tsx`、`ReplicaTargetList.tsx`：识图确认、展示偏好、构图模板和提交状态。

## Task 1: 建立领域对象、能力注册表和确定性规划器

**Files:**

- Create: `local-runtime/wh_local/modules/pod_customization/replica_specs.py`
- Create: `local-runtime/wh_local/modules/pod_customization/replica_capabilities.py`
- Create: `local-runtime/wh_local/modules/pod_customization/replica_planner.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/contracts.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_specs.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_capabilities.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_planner.py`

**Interfaces:**

- `normalize_source_analysis(raw: dict, source_asset_id: str) -> SourceArtworkSpec`
- `normalize_target_analysis(raw: dict, target_asset_id: str, overrides: FactOverrides | None = None) -> TargetProductSpec`
- `match_capabilities(target: TargetProductSpec, preferences: PresentationPreferences) -> CapabilitySet`
- `derive_rules(source: SourceArtworkSpec, target: TargetProductSpec, capabilities: CapabilitySet, preferences: PresentationPreferences) -> ReplicaRuleSet`
- `build_default_composition_slots(target, capabilities, preferences) -> tuple[CompositionSlot, ...]`
- `ReplicaPlan` 必须包含 `source_artwork_spec`、`target_product_spec`、`capability_set`、`rule_set`、`composition_plan`、`generation_strategy`、`compiled_prompt` 和全部版本字段。

- [x] **Step 1: 先写事实模型失败测试。** 覆盖字段缺失、置信度范围、字段来源 `model/user/system`、A/B 资产 ID、图案文字可保留但新增文字禁止等行为。运行：`conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_specs.py -q`；预期为导入失败。
- [x] **Step 2: 实现事实和计划模型。** `SourceArtworkSpec` 保存图案主体及需要排除的背景、人物、道具、水印；`TargetProductSpec` 保存产品类型、部件、材质、内外结构、可印/不可印区域、使用方式和候选事实。所有值使用 `FactValue(value, confidence, source)`；低于配置阈值的字段标记 `needs_confirmation=True`。
- [x] **Step 3: 写能力注册表测试。** 断言美甲五片凝胶目标默认得到 `multi_piece + gel_material + macro_detail`，但不自动得到 `hand_worn`；目标支持手部使用且用户选择手部展示时才得到 `hand_worn`。断言未知建议标签进入 `unknown_capability`，不会进入 Prompt。
- [x] **Step 4: 实现能力定义。** 为每个初始能力提供 `required_facts`、`optional_facts`、`activation_conditions`、`generated_constraints`、`forbidden_conditions`、`compatible_templates`、`incompatible_capabilities`、`generation_strategy`、`prompt_slots` 和版本号。建议标签只能经过标准化映射、置信度判断和注册表校验。
- [x] **Step 5: 写规划器测试。** 覆盖：`component_count > 1` 生成完整部件展示要求；`printable_regions=front_surface` 生成只允许正面印花；凝胶生成材质约束；支持手部但用户关闭手模时不生成手部槽位；用户开启手模时生成手部槽位；事实冲突或低置信度阻止提交。
- [x] **Step 6: 实现确定性规划器。** 规则输出 `rule_id`、`rule_version`、`priority`、`trigger`、`effect`、`generation_constraints`、`source_reason`。展示要求由“能力 + 用户偏好 + 选中模板”共同决定，能力标签本身不等于必拍画面。固定 `multi_piece` 至少一个全套部件槽位。
- [x] **Step 7: 运行纯函数测试并提交。** 运行：`conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_specs.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_capabilities.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_planner.py -q`；预期全部通过。建议提交：`feat: add replica facts capabilities and planner`。

## Task 2: 实现豆包 A/B 分析和构图参数 runtime

**Files:**

- Create: `local-runtime/wh_local/modules/pod_customization/replica_analysis_runtime.py`
- Modify: `local-runtime/wh_local/app/main.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/composition_runtime.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_analysis_runtime.py`

**Interfaces:**

- `analyze_assets(actor, source_asset, target_asset) -> ReplicaAnalysisResult`
- `generate_composition_plan(actor, facts, capabilities, rules, template_snapshot, preferences) -> CompositionPlan`
- runtime 输出只能是严格 JSON，经 Pydantic 校验后才返回；不得返回最终 Prompt。

- [x] **Step 1: 读取现有 Ark 调用模式并写假 session 测试。** 测试构造 A/B 两张输入时角色顺序固定为 `pattern_source`、`target_product`；测试完整 JSON 返回、缺字段、非法枚举、超长字符串、重试和模型错误分类。
- [x] **Step 2: 实现 A/B 分析请求。** A 的 schema 只允许 `source_product_name`、`artwork_motifs`、`artwork_colors`、`artwork_layout`、`artwork_density`、`artwork_repeat_pattern`、`artwork_text`、`source_background_elements`、`source_people_and_props`、`source_watermarks_and_labels` 及置信度；B 的 schema 只允许产品结构、材质、部件、可印区域、不可印区域、使用方式、遮挡风险、候选事实和置信度。
- [x] **Step 3: 实现标准化入口。** runtime 返回原始响应和模型/schema 版本；立即调用 Task 1 的标准化函数。模型返回的建议标签只能作为候选，不得直接写能力数组。
- [x] **Step 4: 接入构图参数生成。** 把事实、能力、规则、模板支持槽位、用户偏好作为结构化输入；豆包只能从模板给出的槽位、角度、光线、背景、构图选项中选择。返回 `template_id`、`template_version`、四个 `panels`、camera、lighting、background、composition_notes 和 user_overrides。
- [x] **Step 5: 做输出安全校验。** 拒绝不存在的槽位、能力、规则 ID、超出模板参数范围的镜头参数、将手部槽位加入但 `hand_worn` 未被本次计划选中的结果；保留可解释错误码供前端展示。
- [x] **Step 6: 注入 app runtime 并运行测试。** 在 `app/main.py` 按现有 `PodBriefRuntime` 注入方式注册 runtime。运行：`conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_analysis_runtime.py -q`；预期全部通过。建议提交：`feat: add replica analysis and composition runtime`。

## Task 3: 持久化分析快照、计划快照和 API

**Files:**

- Create: `local-runtime/wh_local/modules/pod_customization/migrations/020_replica_analysis_plan.sql`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/router.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/contracts.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_persistence.py`
- Test: `local-runtime/tests/test_pod_customization_migrations.py`

**Interfaces:**

- `POST /api/pod-customization/replica/analyze`：接收 `source_asset_id`、`target_asset_id`、`fact_overrides`、`presentation_preferences`，返回 `analysis_id`、A/B 事实、候选标签、状态和版本。
- `POST /api/pod-customization/replica/batches`：接收 `analysis_id`、事实覆盖、展示偏好、可选 `composition_id`、`generation_strategy`、产品字段；服务端重新校验资产归属并冻结计划。
- `repository.get_replica_analysis(workspace_id, owner_user_id, analysis_id) -> ReplicaAnalysisRecord`
- `repository.save_replica_plan(target_id, plan: ReplicaPlan) -> None`

- [x] **Step 1: 设计迁移并写迁移测试。** 在现有 019 后新增 020；分析表按 `(workspace_id, owner_user_id, source_asset_id, target_asset_id, analysis_version)` 唯一。保存 A/B JSON、模型版本、schema 版本、状态、错误码、时间戳。目标表或款表增加 `analysis_id`、`replica_plan_json`、`prompt_snapshot`、能力/规则/模板/编译器版本、生成策略字段。
- [x] **Step 2: 实现迁移。** 所有 JSON 字段使用现有数据库 JSON/TEXT 约定；迁移重复执行安全；历史复刻批次保留旧数据并不会被自动重算。
- [x] **Step 3: 写 repository 所有权测试。** 断言无法读取其他用户的 `analysis_id`；A/B 资产不匹配、已删除资产、分析版本不兼容均拒绝；保存后可完整读取所有快照字段。
- [x] **Step 4: 实现分析 repository 和服务。** `analyze` 先校验上传资产，再调用 runtime，成功和失败都落库；相同资产和版本可复用成功分析；失败允许重新分析并保留旧记录。
- [x] **Step 5: 实现建批次服务。** 创建时必须有 `analysis_id`；重新标准化事实、应用用户允许的覆盖项、匹配能力、选择模板、推导规则、调用构图 runtime、校验并保存 `ReplicaPlan`。低置信度未确认、事实冲突、构图计划非法时返回明确 4xx。
- [x] **Step 6: 实现 API 和路由测试。** 覆盖 200、401、403、404、409、422；响应不得泄露其他用户资产。运行：`conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_api_main_flow.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_persistence.py local-runtime/tests/test_pod_customization_migrations.py -q`。建议提交：`feat: persist replica analysis and plans`。

## Task 4: 接入全定制构图模板并冻结 CompositionPlan

**Files:**

- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/composition_runtime.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/replica_context.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/tests/test_composition_service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_persistence.py`

**Interfaces:**

- `resolve_replica_composition_template(workspace_id, owner_user_id, composition_id: str | None) -> CompositionTemplateSnapshot`
- `freeze_replica_plan(...) -> ReplicaPlan`
- `replica_context.load_frozen_plan(batch_id, target_id) -> ReplicaPlan`

- [x] **Step 1: 写模板选择测试。** 传 `composition_id` 时必须是该用户可用的 active 模板；未传时取当前生效模板；模板删除、停用或版本不存在时拒绝提交；保存的 JSON 是提交瞬间快照。
- [x] **Step 2: 写展示语义测试。** 美甲目标启用 `multi_piece` 时四个面板中至少有一个 `show_all_components=true`；支持 `hand_worn` 但偏好关闭时四个面板都不能有手部；偏好开启时仅在允许槽位加入手部；白底、墙面、微距同样必须由本次模板/用户选择触发。
- [x] **Step 3: 实现模板冻结。** 复用 `pod_customization_compositions`、`composition_json` 和现有模板 service；把模板版本、豆包返回的构图参数、用户覆盖和最终 `CompositionPlan` 一起写入款级快照。
- [x] **Step 4: 加入计划版本。** 事实、展示偏好或模板改变时递增 `plan_version`，保留旧计划和旧结果；服务端重试只读取当前冻结版本。运行构图和复刻持久化测试。建议提交：`feat: freeze replica composition plans`。

## Task 5: 编译四段式 Prompt 并接入 worker

**Files:**

- Create: `local-runtime/wh_local/modules/pod_customization/replica_prompt_compiler.py`
- Create: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_prompt_compiler.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/replica_prompts.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/worker.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/replica_context.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`

**Interfaces:**

- `compile_replica_prompt(plan: ReplicaPlan, attempt: int = 1) -> str`
- `build_replica_listing_prompt(fields, attempt=1)`：保留旧调用兼容性；新 worker 不再用它重建已冻结计划。
- `worker` 每次生成前调用 `load_frozen_plan`，把 `compiled_prompt` 和 `prompt_compiler_version` 传给现有图片请求。

- [x] **Step 1: 写编译器测试。** 断言输出顺序为 PART 1、PART 2、PART 3、PART 4；包含 A/B 角色顺序、目标产品事实、可印区域、部件完整性、材质、构图面板、禁止项和 2×2 格式；相同计划确定性一致；attempt 2 只能增加一致性强化句。
- [x] **Step 2: 写注入和越界测试。** 用户产品名、模型 artwork 描述、构图备注中的换行、引号和超长文本必须经过长度限制和转义；`unknown_capability`、未确认事实和自由文本规则不能直接进入硬约束；不得出现原创配方词、palette/density/invent 等全定制创作语义。
- [x] **Step 3: 实现 PART 1。** 固定声明 A 只提供图案、B 只提供目标产品；不得复制 A 的产品、背景、人物、家具、道具、水印；不得新增文字、Logo、标签、贴纸；四格使用同一产品和同一图案。
- [x] **Step 4: 实现 PART 2。** 从 `TargetProductSpec`、`CapabilitySet`、`ReplicaRuleSet` 编译产品结构、部件、材质、可印/不可印区域、展示硬要求和禁止项。`hand_worn` 只有已进入 `CompositionPlan` 才能输出手部约束。
- [x] **Step 5: 实现 PART 3 和 PART 4。** PART 3 编译每个 panel 的主体、角度、镜头、光线、背景、摆放/使用关系和材质表现；PART 4 编译面板顺序、边界、完整性、不可裁切、禁止新增和本次重试约束。
- [x] **Step 6: 接入 worker 和快照。** 创建批次时保存真实最终 Prompt，不再写 `{}`；初次生成、款级重试、暂停恢复、进程重启都读取相同 Prompt snapshot。初期不添加任何视觉质检调用或质量状态。
- [x] **Step 7: 运行测试。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests/test_replica_prompt_compiler.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_prompts.py local-runtime/wh_local/modules/pod_customization/tests/test_replica_worker.py local-runtime/wh_local/modules/pod_customization/tests/test_resume_mixed_plan.py -q`。建议提交：`feat: compile frozen replica prompts in worker`。

## Task 6: 前端识图确认、展示偏好和构图入口

**Files:**

- Modify: `web-frontend/src/modules/pod_replica/data/podReplicaModel.ts`
- Modify: `web-frontend/src/modules/pod_replica/types.ts`
- Modify: `web-frontend/src/modules/pod_replica/podReplicaApi.ts`
- Modify: `web-frontend/src/modules/pod_replica/PodReplicaPage.tsx`
- Modify: `web-frontend/src/modules/pod_replica/ReplicaTargetDrawer.tsx`
- Modify: `web-frontend/src/modules/pod_replica/ReplicaTargetList.tsx`
- Test: `web-frontend/src/modules/pod_replica/**/*.test.*`（按现有测试目录约定新增）

**Interfaces:**

- draft 增加 `analysisId`、`analysisStatus`、`sourceArtworkSpec`、`targetProductSpec`、`candidateCapabilities`、`factOverrides`、`presentationPreferences`、`compositionId`。
- `analyzeReplicaAssets()` 调用 Task 3 的分析 API；`createReplicaBatch()` 必须提交 `analysis_id`、覆盖项、展示偏好和模板 ID。

- [x] **Step 1: 更新前端模型和序列化测试。** 草稿刷新后保留分析状态、事实确认和展示选择；旧草稿没有分析字段时显示“待识别”而不崩溃。
- [x] **Step 2: 接入上传后的识图。** A/B 资产都上传成功后自动发起一次分析；显示“识别中/待确认/已确认/分析失败”；失败提供重新分析动作，不允许带未完成分析提交。
- [x] **Step 3: 实现事实确认区。** 在 `ReplicaTargetDrawer` 展示产品类型、部件数量、材质、可印区域、不可印区域、支持使用方式、置信度和候选标签；允许修正事实和展示偏好，但禁止编辑全局硬约束、图案来源和不可印区域规则文本。
- [x] **Step 4: 接入构图组件。** 复用 `CompositionGenerateDrawer`、`CompositionManagerDrawer`、`CompositionEditDrawer`；把当前目标事实、能力和规则传给构图入口；用户可以选择手部、人体、墙面、微距、白底等展示偏好，默认值由模板推荐。
- [x] **Step 5: 更新目标列表和提交门禁。** 列表显示待识别、待确认、已确认；提交按钮只有所有目标分析确认、资产匹配、构图计划有效时可用。明确文案：产品支持手部使用不等于本次必须生成手部画面。
- [x] **Step 6: 运行前端测试。** 使用仓库已有 Node 命令运行 pod replica 模块测试和类型检查；预期上传、草稿、轮询、旧全定制入口行为不变。建议提交：`feat: add replica analysis confirmation UI`。

## Task 7: 生成策略、计费、重试和导出

**Files:**

- Modify: `local-runtime/wh_local/modules/pod_customization/runtime_contracts.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/ai_runtime.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/billing_contract.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/remote_billing.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/worker.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_runtime.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_billing.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_replica_export.py`

**Interfaces:**

- `generation_strategy = "grid" | "independent_panels"`
- grid：一次 `DirectListingGridRequest` 生成 2×2，使用现有 `split_grid_2x2`。
- independent_panels：四次单面板请求，共享同一 `source_artwork_spec`、`target_product_spec`、`rule_set` 和 `composition_plan`。
- `PodCallPlan` 必须按策略冻结 provider call 数；不新增质检调用。

- [x] **Step 1: 写 grid 回归测试。** 断言两张参考图顺序、模型字段、轮询/下载/拆图、现有计费 call ID 和重试行为与当前复刻一致。
- [x] **Step 2: 实现 independent panels 契约。** 新增面板角色和单面板请求字段；每次请求只改变 panel 视图，不改变图案、产品事实、规则或版本；失败只重试失败面板，结果落库仍绑定同一款计划。
- [x] **Step 3: 写计费测试。** grid 预留 1 次图片 provider call；independent panels 预留 4 次；分析和构图豆包调用各按现有远程计费口径记录；没有视觉质检 call。重复重试不会重复扣除已成功的 call。
- [x] **Step 4: 实现服务和 worker 选择。** 根据冻结 `ReplicaPlan.generation_strategy` 分支，保留现有超时、暂停、取消、恢复和进程重启语义；成功生成、下载、拆图和落库后直接进入结果状态。
- [x] **Step 5: 校验导出和多目标隔离。** 导出、标题、规格卡只读取当前目标款的快照；多目标批次不得复用第一款分析、Prompt、标题或构图。运行后端 replica runtime、billing、worker、export 测试。建议提交：`feat: support replica generation strategies and billing`。

## Task 8: 端到端回归、文档验收和交付检查

**Files:**

- Modify only if tests reveal an implementation defect in files listed above.
- Test: `local-runtime/wh_local/modules/pod_customization/tests/`
- Test: `local-runtime/tests/`
- Test: `web-frontend/` existing test and type-check commands.

- [x] **Step 1: 运行领域和 runtime 全套测试。** `conda run -n base pytest local-runtime/wh_local/modules/pod_customization/tests -q`；预期既有 full/semi/replica 测试全部通过。
- [x] **Step 2: 运行应用级 POD 测试。** `conda run -n base pytest local-runtime/tests/test_pod_customization_migrations.py local-runtime/tests/test_pod_customization_contracts.py local-runtime/tests/test_pod_customization_billing_contract.py local-runtime/tests/test_pod_customization_billing_flow.py local-runtime/tests/test_pod_customization_app_registration.py -q`；预期迁移、注入、契约和计费全部通过。
- [x] **Step 3: 做端到端假 provider 测试。** 用假豆包 session 和假图片 provider 跑一条“美甲五片凝胶 + 用户关闭手部”和一条“服饰 + 用户选择人体穿着”的完整链路，断言分析、能力、规则、构图、Prompt、生成结果和版本快照均可读取；断言第一条没有手部 panel，第二条有 body-worn panel。
- [x] **Step 4: 做失败恢复测试。** 注入上传失败、豆包 JSON 非法、图片模型超时、下载失败、拆图失败、暂停和进程重启；确认只发生工程级重试，恢复后仍使用原 `plan_version` 和 `prompt_snapshot`，没有质量门禁或质检调用。
- [x] **Step 5: 做静态自检。** 运行：`rg -n "QualityReport|quality.?gate|视觉质检|第四次|未完成|待补|占位" docs/superpowers/plans/2026-10-13-pod-replica-main-flow.md local-runtime/wh_local/modules/pod_customization web-frontend/src/modules/pod_replica`。计划步骤必须完整；代码中第一期不得引入 `QualityReport`、质量门禁或质检调用；若现有代码已有同名历史内容，只记录位置并确认本次没有新增。
- [x] **Step 6: 做类型和快照核对。** 检查所有函数签名、字段名和 API JSON 键与 Tasks 1–7 一致；核对 `prompt_snapshot` 不再是 `{}`；核对分析模型版本、分析 schema、能力 registry、rule、composition template、compiler、image model 版本全部冻结。
- [x] **Step 7: 保护无关改动并完成交付。** `git diff -- local-runtime/tests/test_clipforge_process.py local-runtime/wh_local/modules/clipforge/process.py` 只能显示任务开始前的改动；不要 reset、checkout、清理或把它们加入本功能提交。完成验证后建议提交：`test: verify replica main flow end to end`。

## 完成标准

满足以下条件才算主流程完成：

1. A/B 分析结果可查看、可确认、可修正，并能追溯模型和 schema 版本。
2. 系统能从标准化事实稳定匹配通用能力；建议标签不会绕过注册表进入规则或 Prompt。
3. 同一事实、能力、用户选择和版本得到同一套规则；`hand_worn` 只有本次明确选中才生成手部画面；`multi_piece` 始终要求一张完整部件画面。
4. 构图模板由全定制能力复用，摄影参数由豆包结构化生成并在提交时冻结。
5. 最终 Prompt 为代码编译的四段式，实际发送文本已落库；重试和恢复不重新随机生成计划。
6. grid 和 independent panels 两种策略都能按冻结计划生成并正确计费。
7. 第一阶段没有视觉质检、质量门禁或额外质检调用；图片生成、下载、拆图和落库成功即可交付。
8. full、semi 既有测试和行为不回归，ClipForge 无关改动未被修改。

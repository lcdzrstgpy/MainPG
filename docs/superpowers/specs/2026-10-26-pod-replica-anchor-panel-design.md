# POD 爆款复刻重构：单次识图出主图提示词 + 本地换视角派生

> 状态：待评审，未实施。
> 本版取代同目录下 2026-10-26 早先的「主图锚定」方案；锚定生成阶段保留，识图与规划阶段整体重写。

## 1. 决策摘要

1. **每个 A/B 对只调用一次豆包**：输入目标白底图 B 与样图 A（B 第一张、A 第二张），输出**第一张主图的最终提示词**；不再有第二次「构图参数」调用。
2. **主图由豆包提示词直接生成**，参考图第一阶段固定为 `[B, A]`（B 第一张、A 第二张）。
3. **panel_2~4 由复刻构图模板派生**：只以主图为参考图，换视角重拍；模板支持用户自定义视角，未配置时用默认三拍兜底。
4. **主图视角不可指定**：构图模板只作用于 panel_2~4；panel_1 的视角由豆包主图方案决定，模板里锁定、后端忽略。
5. **复刻链路彻底脱离**能力注册表、规则推导，以及「按模板再调一次豆包选构图参数」的第二次调用；**保留**复刻构图模板与前端模板入口。（能力/规则模块本身仍被裂变复用，见 §8.1 实施修正。）
6. **前端不展示方案**：用户上传后直接提交生成，没有字段级确认或修正。
7. **旧批次只读**：历史复刻批次仍可查看、导出，但不再支持重试、续跑或重新生成。

## 2. 背景

旧链路（`grid`）：一次 2×2 母图 + 本地拆四格，单格像素低、拆图易切歪、四格互相干扰。

旧链路（`independent_panels`）：四次独立调用，但因为「豆包识图 → 本地能力匹配 → 本地规则推导 → 豆包选构图参数 → 本地编译 Prompt」层数太多，且每张都带白底图 B，四张视角趋同，最终被关闭。

结论：与其在旧的多层结构上继续修补，不如把「理解 A/B 并给出主图方案」整体交给一次豆包调用，本地只保留两件事——**按构图模板换视角派生**和**硬规则兜底**。

## 3. 新链路

```text
上传样图 A + 目标白底图 B（每款一对）
        ↓ 1 次豆包调用（每对一次，结果缓存）
主图提示词 main_prompt
        ↓
panel_1 主图：参考图 [B, A] + main_prompt
        ↓ 主图作为唯一参考图
panel_2 图案细节 / panel_3 结构或使用 / panel_4 材质素材
   —— 复刻构图模板（用户可自定义视角，仅作用于这三张）
        ↓
四张单图落库（不再拆 2×2）
```

每款仍然是 4 次生图调用，计费形状不变。

## 4. 豆包单次调用契约

### 4.1 输入

- 一张目标白底图 B、一张样图 A，**顺序固定为 B 在前、A 在后**（image 1 = B，image 2 = A）。
- 分析与生图使用同一顺序，避免提示词里的参考图角色与实际提交顺序不一致。
- 顺序由常量 `REPLICA_MAIN_REFERENCE_ORDER` 控制，便于后续做顺序实验。
- 一次调用只处理一对；同批次多款各调一次，按 A/B 组合缓存复用。
- 沿用现有 `PodReplicaAnalysisRuntime` 的 Ark 视觉 lane、短期密钥、重试与零计费上报。

### 4.2 输出

严格 JSON，只保留一个字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `main_prompt` | string | 第一张主图的完整最终提示词，直接提交给生图模型 |

不返回方案摘要、事实字段或置信度；前端不展示、不确认。

`main_prompt` 必须按实际提交顺序描述参考图：**image 1 = 目标产品 B，image 2 = 样图 A**。

### 4.3 系统提示词铁律（写死在 runtime 内）

豆包不再受本地能力表、规则表约束，但系统提示词必须固定包含以下复刻铁律，避免方案跑偏：

- 参考图顺序固定为 **image 1 = 目标产品 B，image 2 = 样图 A**，主图提示词必须按该顺序描述两张图的角色。
- 只复制 A 的图案元素、配色与排布关系；不得复制 A 的背景、人物、家具、道具、水印、标签。
- 图案优先满印：尽量铺满软质可印表面（主面、侧面、可见底面、口袋、翻盖、提手/肩带/绑带等），减少大片空底与纯色部件；是否满印、哪些部件带印由模型按产品结构判断（`pod-replica-main-prompt-v2` 软引导）。单枚居中徽章仍应避免。
- 保持 B 的产品轮廓、结构、材质、部件数量与比例，不得增减部件。
- 图案只能落在 B 的可印区域；不得新增文字、Logo、贴纸。
- 输出一张方形主图，产品完整、不裁切、不倾斜变形；主图将作为后续三张细节图的唯一外观基准。
- 场景、光线、色调由豆包根据图案与产品自行决定，不参考任何本地模板。

### 4.4 版本与缓存

- 新增 `MAIN_PROMPT_VERSION`（初稿写作 `REPLICA_MAIN_PROMPT_VERSION`）；版本或模型变化即缓存失效。
- 按 `(source_asset_id, target_asset_id, prompt_version, model, reference_order)` 复用已生成的 `main_prompt`。
- 现有 `pod_customization_replica_analyses` 表保留给历史批次读取，新方案写入新表（见 §7）。

## 5. 主图生成

- 参考图：`[B(target_product), A(pattern_source)]`，第一阶段固定 B 在前、A 在后。
- 提示词：直接使用冻结的 `main_prompt`，不在本地追加业务字段。
- 尺寸：沿用 provider 映射，豆包方形档输出 2048×2048。
- 计费：`pod.image`，每款预留 2 次尝试。

### 5.1 参考图方案实验

参考图组合与顺序需要实测，第一阶段先固定一种，后续按同一批 A/B 对比：

| 方案 | 参考图 | 阶段 |
|---|---|---|
| 方案 P1 | `[B, A]` | 第一阶段默认 |
| 方案 P2 | `[A, B]` | 待测 |
| 方案 P3 | 只给 `[B]` | 待测 |

- 切换只改 `REPLICA_MAIN_REFERENCE_ORDER` 与系统提示词里的顺序描述，生成结果与 `main_prompt` 必须同步适配。
- 实验结论记录到本文档，再决定是否调整默认方案。
- 缓存键需包含参考图顺序或方案编号，避免不同顺序复用同一份 `main_prompt`。

## 6. 本地派生三张（换视角）

### 6.1 复刻构图模板（只作用于派生三格）

复刻保留现有构图模板功能（`scope=replica`），但模板职责从「四格全包」收窄为「只定义三张派生图的视角」：

| 模板槽位 | 复刻里的用途 | 是否可编辑 |
|---|---|---|
| panel_1 | 不使用。主图视角由豆包主图方案决定 | 否，前端锁定、后端忽略 |
| panel_2 | 图案特写：近距离裁切，展示纹样、印刷质感与材质细节 | 是 |
| panel_3 | 结构或材质细节：三分之四角度，换一个局部，保持与主图相同的光向 | 是 |
| panel_4 | 素材图：中性电商背景，完整产品居中、正面构图、无道具 | 是 |

默认模板（未配置时兜底）沿用全定制派生轮 `_default_round_direction` 的同款措辞：

| 槽位 | 默认视角 |
|---|---|
| panel_2 | 图案特写，揭示印刷质感与材质细节 |
| panel_3 | 另一个结构/材质局部，三分之四角度，光向与主图一致 |
| panel_4 | 中性背景完整产品，正面平视，设计完整可见 |

规则：

- 模板必须提供 panel_2/3/4 三条非空指令；panel_1 允许为空或占位，后端不读取。
- 前端在复刻 scope 下把 panel_1 显示为只读，并提示「主图视角由 AI 主图方案决定，不可指定」。
- 「生成新构图」的提示词只要求描述三张派生图；返回结果里的 panel_1 直接丢弃。
- 全定制模板不能用于复刻（保留现有 409 校验）；复刻模板也不能被全定制误用。
- 用户继续通过现有「构图模板」入口生成、编辑、切换，不新增第二套模板体系。

### 6.2 派生提示词结构（对齐全定制列式派生轮）

全定制的派生提示词已经验证过这套写法（`column_prompts.py` 的 A2/B2 段）：只给锚点、只改机位、把「必须连续」和「必须变化」分开写。复刻派生面板沿用同一套结构，只把「一轮四格」换成「单帧一张」。

固定顺序：

1. **锚点绑定**（对应 `_ANCHOR_BINDING`）：Image 1 是本款已确认的主图，是产品结构与图案的唯一事实来源；不得从零重画产品，也不得把主图当作待编辑原图做局部修改。
2. **硬质图案锁**（对应 `_RIGID_ARTWORK_LOCK`）：图案是贴在产品表面的硬质印刷层；换机位是把整层图案随产品一起转，不是重新排版、重新居中、缩放或改间距。主元素在产品面上的位置、相对大小、朝向、与边缘的距离必须与主图一致。
3. **连续 / 差异分离**（对应 `_ANCHOR_CONTINUITY`）：
   - 必须一致：产品身份、结构、材质、底色、图案、场景、台面、光向、色温、对比度；
   - 必须变化：机位角度、拍摄距离、裁切、景别、景深；
   - 禁止复刻主图的机位与裁切，同距离、同构图的整产品视角视为失败。
4. **几何与形态锁**（对应 `_PRODUCT_FORM_LOCK` 与几何锁）：轮廓、缝线、分片、五金位置、比例不得重画或重新分配。
5. **本格拍法**（对应 `_b2_block` 的 B2 段）：只描述相机——角度、距离、裁切、景别、景深；**取自复刻构图模板的 panel_2/3/4 槽位**，槽位缺失时用 §6.1 的默认视角兜底；不重写场景、光线、色调，这些由主图继承。
6. **输出格式**：单张方形全幅，无分隔、无边框、无拼贴（全定制是 2×2，这里改为单帧）。
7. **禁止项**：不得新增文字、Logo、标签、水印、贴纸；不得跨面板复制图案。

刻意不做的事（与全定制派生轮一致）：

- 不再注入图案元素清单、配色方案、美术指导或场景描述。图案已被锚点锁死，再写一遍会诱导模型重新设计。
- 不写跨面板的顺序信息，避免模型自行生成拼贴或四宫格。
- 重试（attempt 2）只追加一句「上一版机位与主图过近或画面无效，按本格拍法重拍」，不改变锚点。

实现上，锚定锁、连续性、机位差异、形态锁这几段从 `column_prompts.py` 提取为共享常量，避免复制两份后各自漂移；复刻模板只负责单帧化与槽位渲染。

### 6.3 生成顺序与失败语义

- 先出 panel_1；主图两次尝试都失败 → 整款失败，不生成后续。
- panel_2~4 依次生成，各自保留面板级 2 次尝试，仍使用本轮主图作锚点。
- 同一执行轮次内细节图失败只重试该面板；整款重试按现有批次重试计划执行。

## 7. 数据模型与冻结

### 7.1 新表

```sql
CREATE TABLE pod_customization_replica_transfer_plans (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  source_asset_id TEXT NOT NULL,
  target_asset_id TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model TEXT NOT NULL,
  reference_order TEXT NOT NULL,
  main_prompt TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (workspace_id, owner_user_id, source_asset_id, target_asset_id, prompt_version, model, reference_order)
);
```

### 7.2 款级冻结

款表继续保存计划快照，但内容精简为：

```json
{
  "plan_version": "replica-plan-v4",
  "main_prompt": "...",
  "prompt_version": "...",
  "image_model_version": "...",
  "reference_order": "B,A",
  "composition_template_id": "...",
  "composition_template_version": "...",
  "derived_panels": {
    "panel_2": "...",
    "panel_3": "...",
    "panel_4": "..."
  },
  "generation_strategy": "independent_panels"
}
```

- 不再保存 `source_artwork_spec` / `target_product_spec` / `capability_set` / `rule_set` / `composition_plan`。
- `derived_panels` 是从复刻构图模板冻结下来的 panel_2~4 指令，保证后续重试/归因可复现；panel_1 不冻结。
- `plan_fingerprint` 列保留，仅用于历史数据；新计划不再计算构图指纹。

## 8. 代码改动清单

### 8.1 删除

| 位置 | 内容 |
|---|---|
| `replica_prompt_compiler.py` | 整个文件删除（四段式编译器只服务旧复刻链路） |
| `replica_prompts.py` | 整个文件删除（旧批次的历史回退路径，旧批次已只读） |
| `replica_analysis_runtime.py` | `analyze_assets` / `generate_composition_plan`、A/B 结构化 schema 与构图 schema；改为单一 `main_prompt` schema（`analyze_structure_views` 保留给模板结构识图） |
| `service.py` | `match_capabilities` / `derive_rules` / `validate_plan_ready` 调用、`_replica_plan_fingerprint`、`_resolve_replica_plans`、`_freeze_replica_composition` |
| `repository.py` | 旧分析表读写（`save/find/get_replica_analysis`）与 `find_frozen_replica_plan`；旧表保留供历史查询 |
| `billing_contract.py` | `for_replica_composition`、`REPLICA_COMPOSITION_ATTEMPTS`，以及构图相关的零计费计划 |
| `contracts.py` | `fact_overrides`、`presentation_preferences`、`source_confirmed`；`generation_strategy` 收窄为 `independent_panels` |
| `replica_specs.py` | 旧 `ReplicaPlan`（事实/能力/规则/构图/编译文本）替换为 v4 精简计划 |
| 前端 | `ReplicaTargetDrawer` 的事实/偏好区、识图分析展示与确认、`podReplicaModel` 的 facts/overrides/preferences/确认逻辑、旧批次的重试/重新生成入口 |

> **实施修正（2026-10-26）**：`replica_capabilities.py`、`replica_planner.py` 以及
> `replica_specs.py` 里的 A/B 事实模型**不删除**——裂变（fission）仍直接复用
> `match_capabilities` / `derive_rules` / `normalize_*`。本次只保证复刻链路不再 import 它们，
> 删除动作留给「裂变独立化」单独评估，避免顺手改坏 fission 行为。

### 8.2 新增

| 位置 | 内容 |
|---|---|
| `replica_analysis_runtime.py`（重写） | 单次豆包调用：A/B → `main_prompt`，含铁律系统提示词、重试、缓存；保留类名与 Ark `_run` 底座，main.py 无需改装配 |
| `replica_panel_template.py`（新） | 默认三拍兜底 + 按复刻构图模板槽位渲染派生提示词 |
| `migrations/028_replica_transfer_plans.sql` | §7.1 新表（023~027 已被占用） |
| 前端 | 交互收敛为「上传 → 提交 → 看结果」；分析在后台自动完成，不展示、不确认；构图模板入口保留，复刻 scope 下锁定 panel_1 |

### 8.3 保留与改造

| 位置 | 处理 |
|---|---|
| `worker._generate_listing_panels` | 保留「先主图再派生」的锚定流程 |
| `worker._style_generation_inputs` | replica 分支改为：panel_1 用 `[B, A]`，panel_2~4 用 `[主图]` |
| `service.resolve_replica_composition_template` | 保留但收窄：只读取 panel_2~4，panel_1 锁定忽略；缺槽位用默认视角；跨 scope 仍返回 409 |
| `worker._replica_style_prompt` | panel_1 返回冻结 `main_prompt`；panel_2~4 用模板槽位（缺失时默认视角） |
| `worker._replica_panel_prompt_manifest` | 保留，审计四张真实提交文本 |
| 前端 `CompositionGenerateDrawer` / `ManagerDrawer` / `EditDrawer` | 保留复刻用法；复刻 scope 下 panel_1 只读，生成提示词只描述三张派生图 |
| `PodCallPlan.for_replica_batch / _style_retry / _batch_retry / _batch_resume` | 不改 |
| 标题、导出、批次控制、历史 grid 重试 | 不改 |

## 9. 计费

- 每款 4 个面板 × 2 次图片调用 + 标题调用，形状不变。
- 使用 B 档 `pod_replica_v1_b`；具体倍率由服务端配置，客户端只上报款数。
- 删除批次提交时「按模板再调豆包选构图参数」的冻结调用（原本零计费）。
- 用户主动生成/编辑构图模板仍保留，同样零计费；模板只影响 panel_2~4 的本地提示词。
- 单次识图仍按纯 `pod.title` 冻结，零计费，调用数从「识图 + 构图」降为 1。

## 10. 失败与重试

| 场景 | 行为 |
|---|---|
| 豆包方案生成失败（重试上限内） | 该款分析失败，前端可重新生成方案 |
| 主图两次失败 | 整款失败，不生成派生面板 |
| 细节图两次失败 | 该款失败；批次重试按现有计划重建调用 |
| 暂停/续跑 | 沿用现有调用 id 与画像 |
| 历史批次 | 见 §11 |

## 11. 兼容与历史批次

- 新批次走新链路；旧批次只读：仍可查看、导出，**不再支持重试、续跑、重新生成**。
- 新计划模型带 `plan_version`；读取到旧计划（无版本或版本未知）时，重试/续跑接口直接返回 409 并提示「历史批次不支持重试」，不触发任何 provider 调用。
- 不保留 legacy 计划读取器，也不恢复旧的能力/规则/构图代码。
- 旧的分析表、构图表、迁移文件与历史数据全部保留，不删数据。
- 上线前需确认没有仍在生成中的复刻批次（或在部署前让其跑完）；部署后卡在暂停状态的旧批次按只读处理。

## 12. 验证计划

单测：

1. 一次调用产出 `main_prompt`，缓存命中不再调用豆包；缓存键包含参考图顺序。
2. panel_1 参考图为 `[B, A]`，panel_2~4 参考图为 `[主图]`；`main_prompt` 里的参考图角色描述与实际顺序一致。
3. 主图两次失败不触发派生面板调用。
4. 派生提示词包含锚定锁、禁止复刻主图机位、图案锁、单帧格式。
5. 派生视角来自复刻构图模板 panel_2/3/4；槽位缺失时用默认视角；panel_1 槽位内容被忽略。
6. 全定制 scope 的模板用于复刻时返回 409；模板缺少 panel_2/3/4 任一指令时提交被拦下。
7. 计费计划仍为 4×2 + 标题，画像为 B 档。
8. 旧批次重试/续跑返回 409，且不产生 provider 调用。

回归：`test_replica_api_main_flow.py`、`test_replica_independent_panels.py`、`test_replica_worker.py`、`test_replica_billing.py`、`test_replica_persistence.py`、`test_replica_export.py`，以及前端 `podReplicaModel.test.ts`、`podReplicaApi.test.ts`。

人工验证：先跑 1~2 款，确认主图质量与三张派生图的视角差异，再全量。

## 13. 风险

1. **单次调用负载变重**：识图 + 方案 + 提示词一次输出，延迟与超时风险上升（当前 120s）。
2. **失去字段级置信度门禁**：若不再返回结构化事实，就没有低置信度确认；错误事实会直接进入生成。
3. **默认拍法不区分品类**：多件产品、佩戴类、平面类的差异只能靠主图与模板措辞兜底，用户可通过构图模板覆盖。
4. **可印区域判断质量**直接决定成图，系统提示词是本次重构的核心资产。
5. **删除面大**：后端约 8 个模块文件、前端 3~4 个组件，测试需大改。
6. **主图错误放大**：主图错则三张派生图全错。
7. **参考图顺序与组合影响构图**：B 在前更容易锁产品，但也可能压住图案表现，必须按 §5.1 实测后再定默认方案。
8. **模板越权**：如果 panel_1 槽位没有被前端锁定、后端忽略，用户会以为能指定主图视角；必须前后端双重约束。

## 14. 已确认与剩余事项

已确认：

1. 前端不展示方案，上传后直接生成。
2. 旧批次不再支持重试/续跑，仅保留查看与导出。
3. 删除能力注册表与规则推导；保留复刻构图模板与前端入口，但模板只作用于 panel_2~4，panel_1 锁定、不可指定。
4. 第一阶段主图参考图固定 `[B, A]`（B 第一张、A 第二张）；后续再测 `[A, B]` 与只给 `[B]`。

剩余：

1. 前端费用预估文案是否同步调整（当前 40–50/款）。
2. 上线窗口：确认部署时没有进行中的复刻批次。
3. 参考图方案 P1/P2/P3 的实测结论与最终默认值。

## 15. 任务拆解（实施顺序）

### 阶段 0 · 准备

| 任务 | 改动 | 验收 |
|---|---|---|
| T0.1 上线窗口盘点 | 查询并记录仍在运行/暂停的复刻批次；确认部署前无进行中批次 | 有明确的上线窗口结论；旧批次 id 清单留存 |
| T0.2 新常量落地 | 新增 `REPLICA_PLAN_VERSION`、`MAIN_PROMPT_VERSION`、`MAIN_PROMPT_REFERENCE_ORDER = "B,A"`、默认三拍兜底常量（本节初稿的 `REPLICA_MAIN_*` 前缀是同一组常量的早期命名，实现以计划 Task 2 的接口名为准） | 常量集中在新模块，旧文件不新增散落常量 |

### 阶段 1 · 数据与新运行时

| 任务 | 改动 | 验收 |
|---|---|---|
| T1.1 新表迁移 | `migrations/028_replica_transfer_plans.sql`（§7.1，含 `reference_order`，无 `summary` 列；023~027 已被占用） | 空库与现网副本均能迁移成功 |
| T1.2 方案运行时 | 在 `replica_analysis_runtime.py` 内重写：铁律系统提示词、`{main_prompt}` 严格 schema、重试、计费回调；保留类名与 `_run` | 单测：合法 JSON 成功；非法 JSON 触发契约重试；达到上限失败 |
| T1.3 方案持久化 | `repository` 新增 `find_replica_transfer_plan` / `create_replica_transfer_plan`，缓存键含 `reference_order` | 同 A/B/版本/模型/顺序命中缓存；任一维度变化不命中 |
| T1.4 分析接口改造 | `service.analyze_replica_assets` 改调新运行时；响应只返回状态与 `plan_id`；删除事实标准化与能力匹配 | `POST /replica/analyze` 不再返回任何事实字段；重复分析命中缓存 |
| T1.5 契约精简 | `contracts.py`：`ReplicaAnalyzeRequest` 去掉 `fact_overrides` / `presentation_preferences`；`ReplicaBatchCreate` **保留 `composition_id`**、去掉 `source_confirmed`，`generation_strategy` 只接受 `independent_panels` | OpenAPI 契约测试通过；旧客户端传多余字段按明确错误处理 |
| T1.6 计费裁剪 | 删除 `for_replica_composition` 及 service 中的构图冻结分支；保留 `for_replica_analysis` 单次调用 | 冻结请求中不再出现构图调用；分析仍为零计费 |
| T1.7 建批次重写 | `service.create_replica_batch` 读取 A/B 方案；解析复刻构图模板并只冻结 panel_2~4；款表写入精简计划快照（§7.2）；删除指纹计算 | 新批次款表含 `main_prompt` + `derived_panels`；不再调用豆包构图 |

### 阶段 2 · 生成链路

| 任务 | 改动 | 验收 |
|---|---|---|
| T2.1 派生模板 | 新增 `replica_panel_template.py`：默认三拍兜底 + 按模板槽位渲染单帧提示词；锚定锁/连续性/机位差异/形态锁从 `column_prompts.py` 提取为共享常量 | 单测：模板三格各有对应视角且互不相同；共享常量只有一份 |
| T2.2 策略默认值 | `service._replica_batch_strategy` 默认 `independent_panels`；`REPLICA_PANEL_STRATEGY_ENABLED` 删除 | 新批次无需前端传参即为独立面板策略 |
| T2.3 主图锚定流程 | `worker._generate_listing_panels` 先出 panel_1，再以主图字节派生 2~4 | 单测：主图失败时后续面板零调用；锚点字节等于主图输出 |
| T2.4 参考图改造 | `_style_generation_inputs`：panel_1 `[B, A]`；panel_2~4 `[主图]` | 单测断言参考图数量、角色与顺序 |
| T2.5 提示词取用 | `_replica_style_prompt`：panel_1 返回冻结 `main_prompt`；panel_2~4 用模板槽位（缺失时默认视角）；`_replica_panel_prompt_manifest` 同步 | 调用级快照等于真实提交的四份文本 |
| T2.6 旧计划守卫 | 读取到无 `plan_version` 的计划时，重试/续跑返回 409 | 单测：旧计划不触发 provider，错误码 409 |
| T2.7 模板槽位约束 | 后端忽略 panel_1、要求 panel_2/3/4 非空；前端复刻 scope 下锁定 panel_1；生成模板的提示词只描述三个派生视角 | 单测：panel_1 内容变化不影响派生提示词；缺槽位被拦下；全定制模板 409 |

### 阶段 3 · 删除旧层

| 任务 | 改动 | 验收 |
|---|---|---|
| T3.1 领域层删除 | 删 `replica_capabilities.py`；精简 `replica_specs.py` / `replica_planner.py` / `replica_prompts.py`；删 `replica_prompt_compiler.py` 中依赖事实/规则的部分 | 仓库内无残留 import；`pytest` 收集无失败 |
| T3.2 服务层清理 | 保留并收窄 `resolve_replica_composition_template`（只取 panel_2~4）；删 `_replica_plan_fingerprint` 与 `_resolve_replica_plans` 旧分支；router 的复刻构图路由保留 | 复刻 API 面保留上传、分析、建批次、查询、控制、导出、构图模板 |
| T3.3 前端页面清理 | `PodReplicaPage` **保留构图模板入口与三个抽屉**（复刻 scope 锁定 panel_1）；移除分析展示/确认、旧批次重试按钮 | 页面保留上传、产品信息、构图模板、提交、图库、历史（只读）、导出 |
| T3.4 前端模型清理 | `podReplicaModel` / `types.ts` / `podReplicaApi` 删除 facts/overrides/preferences/analysis 确认逻辑；保留 composition 相关类型 | 类型检查通过；请求体只剩新契约字段 |
| T3.5 全仓校验 | 后端 import/类型检查、前端 `tsc` / 单测 | 无未使用导入；无指向已删模块的引用 |

### 阶段 4 · 测试与验证

| 任务 | 改动 | 验收 |
|---|---|---|
| T4.1 后端单测重写 | 覆盖 §12 的 6 项 | 全部通过 |
| T4.2 回归套件 | `test_replica_api_main_flow` / `test_replica_independent_panels` / `test_replica_worker` / `test_replica_billing` / `test_replica_persistence` / `test_replica_export` | 全绿 |
| T4.3 前端测试 | `podReplicaModel.test.ts` / `podReplicaApi.test.ts` / 导航测试 | 全绿 |
| T4.4 人工验证 | 1~2 款实图：主图质量、三张视角差异、耗时、成本；对比 P1 `[B,A]` / P2 `[A,B]` / P3 只给 `[B]` | 有对比结论，明确默认参考图方案与是否全量 |
| T4.5 文档同步 | 更新架构文档中复刻章节，标注旧链路下线 | 文档与代码一致 |

### 阶段 5 · 上线

| 任务 | 改动 | 验收 |
|---|---|---|
| T5.1 迁移预演 | 在测试工作区跑 028 迁移 | 迁移可回滚，旧数据不受影响 |
| T5.2 发布与冒烟 | 上传 A/B → 提交 1 款 → 检查 4 张图、标题、冻结/结算回执 | 端到端成功，流水画像为 `pod_replica_v1_b` |
| T5.3 观测 | 检查方案生成耗时、失败率、面板级重试与 `no_return` 比例 | 无异常尖刺；失败可定位到具体调用 |

### 实施边界

- 不做新旧双轨、不加灰度开关（已确认彻底删）。
- 不迁移旧计划，不做字段级兼容；旧批次只读。
- 不调整前端费用文案，除非产品另行确认。
- 每个阶段结束跑一次对应测试，不把删除与新增混在同一个提交里。

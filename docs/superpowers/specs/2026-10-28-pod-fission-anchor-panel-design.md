# POD 爆款裂变重构：AI 一次出 N 套方案 + 主图与伴生三格

> 状态：已实施（2026-10-28）。
> 本方案取代 [2026-10-15-pod-fission-design.md](./2026-10-15-pod-fission-design.md) 中的 D3（联合分析 + 能力候选）、D4（用户确认识别摘要）、D10（一款一次四宫格）、D16–D19（能力库学习与自动回滚）。
> 生图编排参考复刻：[2026-10-26-pod-replica-anchor-panel-design.md](./2026-10-26-pod-replica-anchor-panel-design.md)。

## 1. 决策摘要

1. **一键开始**：取消前端独立的「分析素材」与摘要确认步骤。用户上传 A/B、选数量、点上架信息后点击「开始裂变」，后端串行完成「识图 → 出方案 → 冻结 → 生图」。
2. **方案由 AI 一次产出**：一次双图调用返回识别摘要 + N 套（1–5）裂变方案；每套带风格家族、元素、配色、差异说明和一条可直接使用的主图提示词。
3. **方案必须互相区分**：这是裂变不是复刻；N 套是 N 个独立设计，不是同一设计的 N 个视角。多样性作为软约束写进提示词，并用标签去重兜底。
4. **取消本地确定性编译器与规划器**：`fission_planner` / `fission_prompts` 的编译链路退出主链路；`variant.prompt_snapshot` 直接存 AI 给出的 `main_prompt`。
5. **生图改为单帧直出**：每款 4 次单帧调用——`panel_1` 主图 + `panel_2~4` 伴生格；策略标识 `independent_panels`。
6. **`panel_2~4` 只换机位**：以主图为唯一参考图，按裂变构图模板的 shot 重拍；新增本地模板 `fission_panel_template.py`（`pod-fission-panel-v1`）。
7. **`panel_1` 机位用构图模板 `panel_1.shot`**（默认 `wide lifestyle`），与复刻「忽略 panel_1」不同。
8. **硬约束写死在出方案系统提示词**：满印覆盖、边角不落空、提手/肩带/口袋必须带印、只印可印区、结构保真、禁止新增文字与第二套图案。
9. **能力库/规则库/学习回滚下线**：不再发布能力/规则版本、不再自动回滚；`learning_events` 只保留成功/失败与失败类型，用于事后归因。
10. **数量下调为 1–5 款/批**：`MAX_FISSION_STYLES` 从 10 改为 5，前端预设同步。
11. **历史 `grid` 批次只读**：可查看、导出，不可重试、续跑或重新生成。
12. **计费**：每款 4 面板 × 2 次尝试 + 标题；1 款 = 1 link，profile 仍 `pod_fission_v1`；出方案调用按 `pod.title` scope 以 0 上报（已确认）。
13. **接口/导出/规格卡/标题不变**；前端交互收敛为单一「开始裂变」按钮。
14. **成本**：单款约 0.48–0.58 元（最坏 0.96），5 款整批约 2.8 元，详见 §8.1。

## 2. 现状（2026-10-28 按代码核对）

| 环节 | 位置 | 现状 | 本方案 |
|---|---|---|---|
| 上传 | [service.py L675-L704](../../../local-runtime/wh_local/modules/pod_customization/service.py#L675-L704) | `source_artwork` / `target_product` 两个角色 | 不变 |
| A/B 分析 | [fission_runtime.py L315-L335](../../../local-runtime/wh_local/modules/pod_customization/fission_runtime.py#L315-L335)、[L30-L124](../../../local-runtime/wh_local/modules/pod_customization/fission_runtime.py#L30-L124) | 独立接口 `/fission/analyze`，输出事实 + 词表 + 能力/规则候选 | **改为建批次内部步骤**，契约改为摘要 + N 套方案 |
| 变体编排 | [fission_planner.py L321-L417](../../../local-runtime/wh_local/modules/pod_customization/fission_planner.py#L321-L417) | 词表池 + Tier 分配 + 60-30-10 + 指纹去重 | **退出主链路** |
| 提示词编译 | [fission_prompts.py L95-L192](../../../local-runtime/wh_local/modules/pod_customization/fission_prompts.py#L95-L192) | `pod-fission-prompt-v3`，四段式四宫格 | **退出主链路**，仅保留默认构图常量 |
| 构图模板 | [service.py L775-L789](../../../local-runtime/wh_local/modules/pod_customization/service.py#L775-L789) | `scope=fission`；四格 shot | 不变（`panel_2~4` 继续用 shot） |
| 批次冻结 | [repository.py L1467-L1667](../../../local-runtime/wh_local/modules/pod_customization/repository.py#L1467-L1667) | 事务内发布能力/规则版本 + 建内部模板 + 写方案 + 建 4 条结果 + 标题行 | **去掉发布与版本冲突重试**，其余不变 |
| 生图 | [worker.py L1606-L1707](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1606-L1707) | 每款一次 2×2 四宫格 | **改**：每款 4 次单帧 |
| 拆图 | [worker.py L3188-L3196](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L3188-L3196) | `split_listing_grid` 拆四格 | 新批次不再拆图 |
| 落库 | [worker.py L3319-L3398](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L3319-L3398) | 4 格写 `style_grid_results` | 不变（`variant_index=1..4`） |
| 计费 | [billing_contract.py L283-L305](../../../local-runtime/wh_local/modules/pod_customization/billing_contract.py#L283-L305) | 每款 `image:1/2` + 5 次标题 | **改**：`image:p1..p4:{1,2}` + 5 次标题 |
| 学习与回滚 | [fission_registry.py L210-L234](../../../local-runtime/wh_local/modules/pod_customization/fission_registry.py#L210-L234) | 记录 outcome；连续 3 款失败回滚版本 | **只保留事件记录**，去掉回滚 |
| 数量上限 | [contracts.py L40-L44](../../../local-runtime/wh_local/modules/pod_customization/contracts.py#L40-L44) | 1–10 款 | **下调为 1–5** |

## 3. 为什么改

1. **现有出图效果差**：确定性编译器把风格/元素/配色拆成受控组合，容易生成"可拼装但不成立"的设计；模型拿到完整设计意图时表现更好。
2. **`grid` 的三个硬伤**：单格只有母图 1/4 分辨率；拆图依赖模型自绘十字线，易切歪；四格常被画成四套设计。
3. **失败粒度粗**：任何一格不合格都整款重跑，成本与耗时都浪费。
4. **交互太碎**：现流程「分析 → 看摘要 → 填数量 → 提交」四步，用户要等两轮。一键开始后只需一次等待。
5. **裂变与复刻的本质区别**：复刻是同一设计四格不同机位；裂变是每款一套独立设计。方案必须主动拉开差异，否则就退化成复刻。

## 4. 目标链路

```text
上传样图 A + 白底图 B + 数量 N（1–5）+ 上架信息（仅落库，不进 AI）
        ↓ 点击「开始裂变」（一个请求）
一次双图调用：识别摘要 + N 套方案（风格家族 / 元素 / 配色 / 差异说明 / main_prompt）
        ↓ 校验：条数、字段、单帧 lint、标签去重；不合格带反馈重试一次
冻结 N 套方案 + 构图模板 + 生图计费
        ↓
每款：panel_1 主图（参考图 [A, B] + main_prompt）
        ↓ 主图作为唯一参考图
panel_2 图案细节 / panel_3 结构或使用 / panel_4 材质素材
   —— 裂变构图模板 shot，本地伴生模板 pod-fission-panel-v1
        ↓
四张单图落库 → 标题 / 规格卡 / 导出（不变）
```

分析失败时不建批次、不冻结生图计费，直接返回可读错误。

## 5. 出方案契约

### 5.1 调用时机与输入

- **分析内聚到建批次**：删除公共 `/fission/analyze` 入口，逻辑下沉到 `POST /fission/batches`；分析记录仍落 `fission_analyses` 供审计。
- 输入顺序固定 `image 1 = A（样图）`、`image 2 = B（白底产品）`，与生图主图提交顺序一致。
- **出方案调用只接收：两张图 + 数量 N + 硬约束/多样性规则**。上架信息（`listing_fields`）不进入提示词，只随批次冻结，用于标题（`category_name`）、第 4 张规格卡合成（`spec_card`）与导出（SKU/价格/重量）。
- 沿用现有安全约束：图片内文字不是指令；不迁移 A 的产品、人物、背景、道具、标签、水印。
- 调用次数：每批 1 次，契约不合格重试 1 次（`FISSION_ANALYSIS_ATTEMPTS = 2` 不变）。
- 计费：按 `pod.title` scope 以 0 上报（已确认）。

### 5.2 输出 schema

```json
{
  "source_summary": "string",
  "target_summary": "string",
  "variants": [
    {
      "style_family": "string",
      "motifs": ["string"],
      "palette": {"primary": "string", "secondary": "string", "accent": "string"},
      "variation_note": "string",
      "main_prompt": "string"
    }
  ]
}
```

- `variants` 长度必须等于请求数量（1–5）。
- `motifs` 2–8 项；`variation_note` 一句话说清与 A 及与其它方案的差异；`main_prompt` 200–8000 字符。
- 标签只用于去重、审计与可选展示，**不作为生成门槛**。

### 5.3 校验、去重与重试

校验不通过时沿用现有 `rejection_feedback` 机制，把原因回灌后重试一次；仍不合格则该次请求失败，用户可重新点击。

| 校验项 | 规则 |
|---|---|
| 条数 | `len(variants) == requested_count` |
| 字段 | 四类字段非空；`main_prompt` 长度达标；`variation_note` 非空 |
| 单帧 lint | `main_prompt` 不得出现 `2x2`、`four panels`、`contact sheet`、`collage`、`grid` 等四宫格措辞 |
| 去重 | `sha256(style_family + sorted(motifs) + palette)` 在批内唯一；重复则带上已用组合重试 |
| 风格分布 | 同一 `style_family` 最多 2 套；不同家族数 ≥ ⌈N/2⌉（N=5 即至少 3 个家族） |
| 参考图表述 | 提示词应体现「参考图 1 = A、参考图 2 = B」；软校验，不单独判失败 |

二次仍重复时接受并记录，不阻塞用户。

### 5.4 多样性软约束（写死在系统提示词）

明确告知模型：这是裂变，N 套是 N 个独立设计，不是同一设计的 N 个视角。

- 每套方案在「风格家族 / 主元素 / 配色 / 场景与光线 / 图案密度与构图」中至少 3 个维度与其它方案、以及 A 的原作不同。
- 禁止同元素换色出款，禁止同一构图套路复制。
- 同一风格家族最多 2 套（N=5 时自然要求至少 3 个家族）；不同家族数 ≥ ⌈N/2⌉。
- 即使只出 1 套，也要把 A 的元素重新组织（分组、密度、尺度或配色主次至少变一项），读起来是「受 A 启发的新设计」而不是复印。
- 每套 `main_prompt` 必须自带完整的风格、元素、配色、场景与光线描述，并写出自己的构图，不照抄 A 的排布，不写模板化空话。
- `variation_note` 必须说明本套与 A、以及与其它方案的差异点。

### 5.5 硬约束（写死在系统提示词）

满印与覆盖：

- 图案在可印区 **edge-to-edge 满印**；正面与两侧连续，缝线、拉链、底边只留窄边。
- 提手、肩带、绑带、口袋、翻盖等可分件 **必须延续同一套图案**，不得保留原色。
- 禁止大片空底布、单个漂浮图案、居中徽章；自然的小间距允许。

产品与边界：

- 只印 B 声明的可印区；不覆盖缝线、五金、拉链、内衬。
- 保持 B 的轮廓、比例、部件数量、开口与五金位置。
- 不得新增文字、字母、Logo、贴纸、边框、拼贴；A 图案自带文字可保留。
- A 是风格、元素与构图灵感的**参考库**：可以参考它的排布、密度与配色关系，但要组织成自己的构图，不照搬原作；A 的场景、光线与氛围可以参考，但要用自己的话重新描述，不逐字复现 A 的房间、窗景与道具。
- 不复制 A 的产品、人物、标签与水印；不引入第二套图案；不把图案做成 3D 物体。

多样性属于软约束（§5.4），满印与产品边界属于硬约束。两类内容集中定义成常量块（建议 `FISSION_PLAN_HARD_RULES` / `FISSION_PLAN_DIVERSITY_RULES`），便于迭代与测试断言。

### 5.6 冻结与审计

- 分析记录保存完整 `variants`（新增 `plans_json` 列）与 `plan_prompt_version`。
- 建批次时逐款写入 `fission_variants`：`variant_plan_json` 存该套方案的标签与 `main_prompt`，`prompt_snapshot` = `main_prompt`，`combination_fingerprint` = 去重指纹。
- 批次保存 `generation_strategy='independent_panels'`、`panel_prompt_version`、`prompt_version=plan_prompt_version`。
- 调用级审计：`generation_calls.prompt_snapshot` 存该次真实提交的 4 条面板提示词（JSON），做法同复刻的 [`_replica_panel_prompt_manifest`](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1410-L1434)。

## 6. 伴生三格模板（新增 `fission_panel_template.py`）

`pod-fission-panel-v1`，结构同 [replica_panel_template.py](../../../local-runtime/wh_local/modules/pod_customization/replica_panel_template.py#L77-L125)：

```text
=== PART 1 · ANCHOR BINDING（唯一参考图 = 已验收主图）===
SINGLE ANCHOR BINDING / RIGID_ARTWORK_LOCK / ANCHOR_CONTINUITY / GEOMETRY LOCK
=== PART 2 · THIS PANEL'S CAMERA（只准改机位）===
SHOOTING DIRECTION for panel_2/3/4（来自冻结的裂变构图模板 shot）
Framing-only clause + CAMERA_MUST_DIFFER
（panel_4 追加 MATERIAL PANEL：仅允许换净色电商底）
=== PART 3 · OUTPUT FORMAT AND PROHIBITIONS ===
SINGLE FRAME OUTPUT（禁止 2×2 / 拼贴 / 条带 / 分割线）
PROHIBITIONS（禁止新增文字、第二套图案、3D 化、带回 A 的场景）
（attempt=2 追加 RETRY ATTEMPT 2 OF 2）
```

- 复用 [prompt_anchor_blocks.py](../../../local-runtime/wh_local/modules/pod_customization/prompt_anchor_blocks.py) 的 `RIGID_ARTWORK_LOCK`、`ANCHOR_CONTINUITY`、`CAMERA_MUST_DIFFER`。
- 不注入元素、配色、规则：这些已在主图定稿。

## 7. 数据与迁移

新增 `029_fission_ai_plans.sql`：

```sql
ALTER TABLE pod_customization_fission_analyses
    ADD COLUMN plans_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE pod_customization_fission_analyses
    ADD COLUMN plan_prompt_version TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_fission_batches
    ADD COLUMN generation_strategy TEXT NOT NULL DEFAULT 'grid';
ALTER TABLE pod_customization_fission_batches
    ADD COLUMN panel_prompt_version TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_pod_fission_batches_strategy
    ON pod_customization_fission_batches (generation_strategy, status);
```

- 存量批次自动落到 `grid` → 只读；新批次写 `independent_panels`。
- 数量上限 1–5 是代码常量（`MAX_FISSION_STYLES`）与前端预设变更；迁移 `024` 的 CHECK 是 1–10，无需改表。
- `prompt_version` 改存方案提示词版本；`compiler_version` 不再写入（列保留）。
- `capability_registry_version` / `rule_registry_version` 新批次固定写 0（列保留，历史行不动）。
- 能力/规则版本表与 `registry_current` 转只读历史；`learning_events` 继续写入，只做失败归因。
- `repository.get_fission_batch` / `get_batch_internal` 透出 `generation_strategy`、`panel_prompt_version`。

### 7.1 本地保留 / 下线的硬编码清单

**下线（不再维护）**

| 内容 | 位置 | 说明 |
|---|---|---|
| 能力注册表 | `replica_capabilities.py` | `CAPABILITY_REGISTRY` 只被 `fission_planner` / `replica_planner` 引用，一并退出 |
| 规则推导 | `replica_planner.py` 的 `derive_rules` / `HARD_CONSTRAINTS` | 硬约束改由提示词常量表达 |
| 词表池 / Tier / 60-30-10 / 指纹组合 | `fission_planner.py` | 整文件退出主链路 |
| 四宫格编译器 | `fission_prompts.py` 的 `compile_fission_prompt` | 只保留 `default_fission_composition` |
| 能力/规则候选模型与发布回滚 | `fission_specs.py`、`fission_registry.py` | 候选模型可删；`learning_events` 写入保留 |

**保留（本地硬编码，需要随版本维护）**

| 内容 | 位置 | 为什么必须留 |
|---|---|---|
| 出方案系统提示词：硬约束块 + 多样性块 | `fission_runtime.py`（常量块） | 满印/可分件/禁项/多样性只能靠提示词表达 |
| 伴生三格模板 | 新增 `fission_panel_template.py` | `panel_2~4` 不调 AI，本地拼装才能保证只换机位、可复现 |
| 默认四格构图 | `fission_prompts.default_fission_composition` + `DEFAULT_COMPOSITION_PANELS` | 用户没配模板时的兜底机位 |
| 校验常量 | 出方案 runtime | 数量 1–5、单帧 lint 关键词、家族分布、去重指纹 |
| 版本常量 | 出方案 / 伴生模板 | `plan_prompt_version`、`panel_prompt_version`，用于冻结与追溯 |
| 图像后处理参数 | `worker.py` / `ai_runtime.py` | 交付尺寸、锐化/超分、质量闸门 |
| 历史注册表数据 | 三张 registry 表 | 只读历史，不再写入 |

结论：**不再有需要靠运营去"喂"或"校准"的本地知识库**；留下的是提示词文本、一个三格模板和一组校验常量，改动只影响新批次。

## 8. 计费契约

新增四个计划，不改动复刻方法：

| 方法 | 形状 | 用途 |
|---|---|---|
| `for_fission_batch(batch_id, style_count)` | 每款 `image:p1..p4:{1,2}` + `title:{1..5}` | 初始批次 |
| `for_fission_style_retry(action_id)` | 同形状（单款） | 单款重试 |
| `for_fission_batch_retry(action_id, image_indices, title_indices)` | 按勾选款展开 | 批量失败重试 |
| `for_fission_batch_resume(batch_id, resume_id, image_indices, title_indices)` | 按剩余款展开 | 暂停续跑 |

- 调用 id 形状：`{batch_id}:style:{i}:image:p{panel}:{attempt}`，复用 [`_panel_image_call_id`](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1313-L1327)。
- 出方案调用仍冻结为 2 次 `pod.title`，按 0 上报（已确认）。
- `billing_profile` 固定 `pod_fission_v1`；1 款仍上报 1 link。
- service 侧把 [`_batch_replica_generation_strategy`](../../../local-runtime/wh_local/modules/pod_customization/service.py#L3340-L3351) 泛化为按 mode 读取批次策略。
- 建批次的「能力库版本冲突重试 3 次」逻辑随注册表下线一并删除。
- **需服务端确认**：远端对 `pod_fission_v1` 单款多调用是否仍按 link 汇总。

### 8.1 成本测算（按 2026-10-28 报价）

生图 0.12 元/张输出图，参考图不单独计费；识图与标题同用 `doubao-seed-2-1-lite-260915`（输入 0.80 元/M、输出 2.70 元/M，缓存命中输入 0.16 元/M）。

| 项 | 口径 | 单款成本 |
|---|---|---|
| 生图（一次成功） | 4 张 × 0.12 | 0.48 元 |
| 生图（面板首次失败率 10% / 20%） | 4 × (1 + p) 张 | 0.53 / 0.58 元 |
| 生图（最坏，每面板 2 次） | 8 张 | 0.96 元 |
| 出方案（5 套方案） | 一次/批，输出约 2.5k–4k token | 0.01–0.02 元/批 |
| 标题 | 输入约 1.5k、输出约 200 token | 0.002 元 |
| **平均合计** | 按 15% 面板首次失败率 | **约 0.56 元** |

- 5 款整批：平均约 2.8 元（顺利 2.4 ～ 全最坏 4.8）。
- 对照现状 grid：平均约 0.14 元/款，改造后约 4 倍。
- 降本优先项：分析图降到 1024 以内、伴生三格评估更低尺寸档、重试只重跑失败格。

## 9. Worker 改动

1. **策略解析**：[`_style_generation_strategy`](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1289-L1294) 对 fission 读取批次 `generation_strategy`。
2. **输入构造**：`_style_generation_inputs` 的 fission 分支按 `panel_index` 分流：
   - `panel_1`：`variant.prompt_snapshot`（AI 主图提示词，`attempt=2` 追加一致性重试段）+ 参考图 `[A, B]`；
   - `panel_2~4`：伴生模板提示词 + 唯一参考图 = 主图锚点。
3. **面板编排**：复用 [`_generate_listing_panels`](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1329-L1363)（主图失败即整款失败，绝不带空锚点派生）；v1 串行生成，主图完成后三格并行作为后续优化。
4. **提示词函数**：`_fission_style_prompt` 拆成 `_fission_main_prompt` 与 `_fission_panel_prompt`；`_replica_panel_prompt_manifest` 泛化为按 mode 生成审计快照。
5. **单款重试**：[`regenerate_style`](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L952-L1185) 面板分支对 fission 生效。
6. **只读闸门**：新增 `_require_supported_fission_strategy(batch)`，`generation_strategy != 'independent_panels'` 的批次拒绝进入生图。
7. **失败归因**：面板失败仍走 `_record_fission_image_outcome`，但内部改为纯事件写入，不再调用回滚。

## 10. 接口 / 前端 / 导出

- **删除公共 `/fission/analyze`**：前端不再调用；`POST /fission/batches` 请求体新增 `source_asset_id`、`target_asset_id`、`requested_count`（1–5），保留 `client_request_id`、`listing_fields`、`title`。
- **建批次内部步骤**：幂等查询 → 校验资产角色 → 冻结并执行双图出方案调用 → 校验方案 → 读生效构图模板 → 冻结生图计费 → 单事务落库 → 入队 worker。分析失败时不建批次、不冻结生图计费。
- **前端交互**：上传 A/B → 选数量（1–5）→ 填上架信息 → 点「开始裂变」，没有中间确认页；失败时给出可读错误并允许重新点击。
- 数量预设改为 `[1, 3, 5]`，上限 5。
- `_batch_payload` 增加 `generation_strategy`（只读标记），前端据此禁用历史批次的「重试 / 重新生成」。
- 导出、规格卡、标题、`panel_asset_ids` / `public_urls` 汇总不变。

## 11. 历史批次只读

- 判定：`generation_strategy == 'grid'`（存量批次天然满足）。
- 允许：查看、导出、下载、查看标题与规格卡。
- 拒绝：单款重试、批量失败重试、暂停续跑、主动重新生成；返回 409 并给出可读提示。
- 旧批次不迁移、不重出方案、不改写冻结字段。

## 12. 验收与测试

单测：

- `test_fission_runtime`：N 条校验、单帧 lint、标签去重、风格分布、重试反馈；schema 拒绝多余字段。
- `test_fission_api`：一次请求完成出方案 + 建批次；`requested_count=6` 被拒；分析失败不建批次、不冻结生图计费；同一 `client_request_id` 幂等返回；历史批次重试 409。
- 新增 `test_fission_panel_template`：三格锚点绑定、对应 shot、单帧禁令、`attempt=2` 仅追加。
- `test_fission_billing`：四个新计划形状与 profile；`image:p{n}` 与 `_panel_image_call_id` 对齐。
- `test_fission_worker`：主图 → 三格串行、锚点传递、面板级重试只重跑失败格、主图双失败即整款失败、旧 grid 批次被拒。
- `test_migration`：`029` 幂等；存量行默认 `grid`、`plans_json='[]'`。

端到端：

1. 1 款：一键请求 → 四张单图 → 标题 → 导出。
2. 5 款：方案互不重复、同一家族不超过 2 套且至少 3 个家族、四格同款、失败款只重跑该款。
3. 故障注入：panel_3 两次失败 → 该款失败、其余款不受影响、按款退款。
4. 暂停续跑：停在主图之后 → 续跑只补 panel_2~4。
5. 历史批次：三个重试入口均 409。

指标：款级失败率、面板级重试命中率、单款端到端耗时、交付清晰度、方案多样性（标签重复率、家族分布）。

## 13. 风险与未决

| 风险 | 说明 | 处理 |
|---|---|---|
| 方案趋同 | N 越大越容易「换汤不换药」 | 多样性软约束 + 标签去重 + 风格分布校验 + 重试反馈；实测趋同则降级为「每款一次」 |
| 单次输出过长 | 5 套 × 完整提示词可能触顶或截断 | 校验条数；必要时限制 `main_prompt` 长度 |
| 无确认步骤 | 识别错了用户看不到摘要，只能重传重来 | 失败与摘要写入批次详情可事后查看；前端给出明确失败提示 |
| 主图错误传播 | 主图错了三格一致地错 | 面板级重试 + 款级重试；后续可加「主图验收」 |
| 覆盖无法自动判定 | 「边角没印到」没法精确量化 | 提示词硬约束 + 本地质量闸门兜底（纯色/重复/可见文字，见 [PatternQualityGate](../../../local-runtime/wh_local/modules/pod_customization/images.py#L18-L48)）+ 抽检 |
| 失去可控变体 | 不再有 60-30-10 与词表约束 | 已确认接受；标签仅用于审计 |
| 能力库闲置 | 版本表与回滚代码不再使用 | 转只读，不删除，便于回退 |
| 成本 ×4 | 单款约 0.48–0.58 元（最坏 0.96） | 已确认接受（§8.1） |
| 服务端计费口径 | 单款多调用的汇总方式 | 需服务端确认 |
| 出方案零计费口径 | 输出 token 增大，仍按 `pod.title` 以 0 上报 | 已确认按 0 上报；若远端拒收另行处理 |
| 构图模板 `panel_1` | 主图机位受用户模板影响 | 默认 `wide lifestyle`；模板不完整时建批次 409 |
| 历史批次只读 | 用户可能想重跑旧批次 | 与复刻一致；引导新建批次 |

## 14. 实施顺序

1. 迁移 `029` + repository 透出策略字段；注册表转只读；数量上限改 5。
2. 出方案契约（runtime 输出 schema、校验、多样性约束、重试）+ 单测。
3. `POST /fission/batches` 内聚分析：幂等 → 出方案 → 冻结 → 落库；删除公共 analyze 入口。
4. 伴生模板 + 单测。
5. 计费四个新计划 + service 冻结点切换。
6. worker 面板分支（主图 / 派生 / 面板级重试 / 只读闸门）+ 失败归因改为纯事件。
7. 前端：单一「开始裂变」按钮、数量 1–5、只读禁用。
8. 端到端验收与灰度。

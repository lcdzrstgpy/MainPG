# POD 全定制「美术指导」增强：批级全局字段 + 元素/配色权重 实施计划

> 配套方案：`docs/superpowers/specs/2026-10-12-pod-art-direction-global-fields-design.md`
> 状态：待启动。方案中的「事实」均已在主会话核实（2026-10-12），行号与之同源。
> 基线：`local-runtime/wh_local/modules/pod_customization/` + `web-frontend/src/modules/pod_customization/`。

---

## 0. 全局约束（不可违反）

| # | 约束 |
|---|---|
| C1 | **四段式骨架不动**：不拆段、不挪段、不改段头文案；`test_prompt_is_organized_into_four_parts` 必须继续通过 |
| C2 | **只有一处 AI 调用**：全流程仅 `brief` 那一次豆包调用（`brief_runtime`）。PART 1 / PART 4 / PART 3 都是 `prompts.py` 里的**静态字符串构造，不发起任何 AI 调用**；新字段只是作为变量插入 |
| C3 | **构图模版边界不破**：「只讲怎么拍，不讲拍的是什么」——材质/工艺/部件/道具名仍禁止进入构图模版（`composition_runtime._FORBIDDEN_CONTENT_RULE`），只有机位/景别/构图/光线可以进 |
| C4 | **CARVE-OUT 最高优先**：模板真实工艺 > 新工艺字段；工艺词只写「优先使用」，不写「必须使用」 |
| C5 | **场景逐款不同不可破**：PART 4 的「同批各款背景不得复用」硬约束保留 |
| C6 | **工艺批级唯一**：一个批次 = 一个模板 = 一个产品，`fabric_craft` 整批一个值 |
| C7 | **豆包只归纳不发明**：`fabric_craft` / `scene_mood` 由用户告知，豆包只规范化；用户没告知即留空、回退静态默认 |
| C8 | **改提示词必升版本**：`pod-brief-v1→v2`、`PATTERN_PROMPT_VERSION v2→v3`、`pod-composition-v3→v4`（已确认） |
| C9 | **不 commit、不起服务**；改动范围限于本计划列出的文件 |
| C10 | **向后兼容**：旧草稿缺新键不丢弃；旧批次 `style_elements` 旧形状（`primary/co/accents`）仍可渲染 |

---

## 1. 任务总览

| 任务 | 内容 | 文件 | 依赖 | 关键验收 |
|---|---|---|---|---|
| T1 | 契约字段 + 版本号 | `contracts.py`、`web-frontend/.../types/index.ts` | — | `BusinessFields` 含 `fabric_craft`/`scene_mood`；`BriefFieldRequest` 含两个可选入参；`PromptVersion` 认 `v3` |
| T2 | brief 生成链路 | `brief_runtime.py`、`service.py` | T1 | strict schema 含两键且允许空串；`user_provided` 入参只归纳不发明；版本 `pod-brief-v2` |
| T3 | PART 1：工艺行 + 60-30-10 静态规则 | `prompts.py` | T1 | 非空时渲染 `Fabric craft (this batch)`；`_COLOR_IMPACT_BLOCK` 含比例契约 |
| T4 | PART 4：吃 `scene_mood` + 光照降级 | `prompts.py` | T1 | 有基调时作为整批基调；删「warm directional light」，保留 GLOW |
| T5 | PART 3：元素 Tier 权重 | `prompts.py` | — | 返回 `hero/narrative/rhythm`；渲染三级；旧形状可读 |
| T6 | PART 3：配色权重引擎 60-30-10 | `prompts.py`、`worker.py` | T5 | 逐款三色赋值；`seed=batch_id` 传入；池 <3 回退 |
| T7 | 构图模版光照标尺 | `composition_runtime.py` | — | 生成规则含「低角度掠射侧光 + 漫射补光」标尺 |
| T8 | 前端数据层 | `types`、`podCustomizationDraft`、`podCustomizationModel`、`podBrief`、`podCustomizationApi` | T1 | 默认值/归一化/API 透传含两字段；旧草稿兼容 |
| T9 | 前端文本框内置字段模板 + 只读展示 | `PodBriefInput.tsx`、`PodCustomizationPage.tsx` | T8 | 「智能填写」文本框预置六字段模板；生成后确认摘要只读展示 |
| T10 | 全量回归验证 | 全部 | 全部 | 后端模块测试 + 前端类型/单测全绿 |

建议顺序：T1 → T2 → T3/T4/T5/T7（可并行）→ T6 → T8 → T9 → T10。

---

## 2. 关键实现约定（方案未明确处的落地口径）

以下 6 点方案文档未给死或表述不一致，本计划已定口径（D/E 已与你确认）。

| # | 事项 | 本计划口径 |
|---|---|---|
| A | **元素返回结构** | `assign_style_elements` 返回键由 `{primary, co, accents}` 改为 `{hero, narrative, rhythm}`；`build_style_listing_prompt` 内做 `_coerce_style_elements`，兼容旧批次 `primary/co/accents` 形状（旧数据不炸） |
| B | **配色池不足 3 色** | `assign_style_colors` 在 `len(color_preferences) < 3` 时返回全空；PART 3 **不渲染** 60-30-10 行，保留现有 `palette` 轮换行为（不拼接英文色板描述当颜色用） |
| C | **`seed` 传递** | `build_style_listing_prompt` 新增 `seed: str = ""` 形参；`worker._style_generation_inputs` 传 `seed=str(batch.get("batch_id") or "")`。`scene_mood` 直接从已有 `business_fields` 读，不改签名 |
| D | **构图模版版本号**（已确认） | T7 改构图提示词内容 → `composition_runtime.PROMPT_VERSION = "pod-composition-v4"` |
| E | **输入 vs 展示**（已澄清，非冲突） | **输入**：字段模板直接预置在「智能填写」的**文本框**里（产品名称 / 产品风格 / 产品工艺 / 使用场景 / 目标市场 / 目标人群），用户在标签后补内容，整段随 `brief` 交给豆包规范化；**展示**：豆包回填后的 `fabric_craft` / `scene_mood` 在**确认摘要以只读展示**（同 `design_theme`「统一风格」口径） |
| F | **brief 请求传输** | `BriefFieldRequest` 新增两个**可选**入参 `fabric_craft` / `scene_mood`（默认 `""`、`max_length=500`）；`_messages_for_brief` 以 `user_provided` 块传入，指令：**用户给了就只做规范化、没给再从 `user_brief` 归纳、两者都没有则留空** |

### 关键事实（已核实）

| 事实 | 依据 |
|---|---|
| 元素分配在**建批次时**按 `batch_id` 洗牌并落库 | `repository.py:467`；`prompts.py:94-126` |
| worker 逐款读回落库元素并构造 PART 3/4 | `worker.py:969-976`、`prompts.py:398-512` |
| 配色现在是逐款轮换、无比例 | `prompts.py:429-446`（`palette`/`accent_colors`） |
| brief strict schema 的 `required` = `_FIELD_KEYS`，键集合必须完全一致 | `brief_runtime.py:155-195`、`validate_brief_fields:215` |
| brief 入参链路：`BriefFieldRequest` → `service.generate_brief_fields` → `PodBriefRequest` → `_messages_for_brief`；冻结按新 `brief_id`，与请求内容无关 | `service.py:669-702`、`billing_contract.py:211-224` |
| 构图模版允许写光线 | `composition_runtime.py:427-433` |
| 前端 `prompt_version` 类型目前只到 `v2` | `types/index.ts:228` |

---

## 3. 任务详情

### Task 1 · 契约字段与版本号

**Files**
- Modify: `local-runtime/wh_local/modules/pod_customization/contracts.py`
- Modify: `web-frontend/src/modules/pod_customization/types/index.ts`

**Interfaces**
- Produces: `BusinessFields.fabric_craft`、`BusinessFields.scene_mood`（均 `Annotated[str, Field(max_length=500)]`，默认 `""`）
- Produces: `BriefFieldRequest.fabric_craft` / `.scene_mood`（可选入参，默认 `""`、`max_length=500`）
- Produces: `PromptVersion = Literal["v1", "v2", "v3"]`

- [ ] **Step 1：写失败测试**：断言 `BusinessFields(fabric_craft=...).fabric_craft` 可赋值、`BatchCreate(prompt_version="v3")` 通过、`prompt_version="v4"` 抛错（`tests/test_prompts.py` 或 `test_router.py` 就近加）。
- [ ] **Step 2：跑测试确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_prompts.py -k 'fabric or version' -q`
- [ ] **Step 3：实现**

```python
# contracts.py BusinessFields 内，design_theme 之后
fabric_craft: Annotated[str, Field(max_length=500)] = ""
scene_mood: Annotated[str, Field(max_length=500)] = ""
```
  `PromptVersion` 扩为 `Literal["v1", "v2", "v3"]`。`BriefFieldRequest` 增加两个可选入参（`extra="forbid"` 下旧客户端不传即取默认，兼容）：
  ```python
  fabric_craft: Annotated[str, Field(max_length=500)] = ""
  scene_mood: Annotated[str, Field(max_length=500)] = ""
  ```
  前端 `types/index.ts`：`PodBatch.prompt_version` 改为 `"v1" | "v2" | "v3"`；`PodBusinessFieldsDraft` 增加 `fabric_craft: string; scene_mood: string;`（`PodBusinessFields` / `PodBriefFieldsDraft` 由 Omit 自动继承）。
- [ ] **Step 4：跑测试确认 GREEN**

### Task 2 · brief 生成链路（唯一 AI 调用）

**Files**
- Modify: `local-runtime/wh_local/modules/pod_customization/brief_runtime.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`（`_run_brief_fields_authorized` 转发两个入参）
- Test: `.../tests/test_brief_runtime.py`

**Interfaces**
- Produces: 两字段进入 `_SCALAR_FIELDS` → 自动进入 `_FIELD_KEYS` 与 strict schema 的 `required`
- Produces: `PodBriefRequest.fabric_craft` / `.scene_mood`（dataclass 新字段，默认 `""`）
- Consumes: `BusinessFields.fabric_craft/scene_mood`（T1）、`BriefFieldRequest.fabric_craft/scene_mood`（T1）

- [ ] **Step 1：写失败测试**：构造 brief 响应缺 `fabric_craft` 键 → `validate_brief_fields` 抛错；给空串 → 通过且 `fields.fabric_craft == ""`；`_BRIEF_RESPONSE_FORMAT` 的 `required` 含两键、两键 schema **无 `minLength`**。
- [ ] **Step 2：确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_brief_runtime.py -q`
- [ ] **Step 3：实现**
  1. `_SCALAR_FIELDS` 追加 `"fabric_craft", "scene_mood"`（**不**加入 `_REQUIRED_SCALARS`）。
  2. `_BRIEF_RESPONSE_FORMAT` 的 `properties` 增加：
     ```python
     "fabric_craft": {"type": "string"},   # 允许空串：不设 minLength
     "scene_mood": {"type": "string"},
     ```
  3. `validate_brief_fields` 返回的 `BusinessFields(...)` 显式带 `fabric_craft=scalars["fabric_craft"]`、`scene_mood=scalars["scene_mood"]`。
  4. `PodBriefRequest` dataclass 增加 `fabric_craft: str = ""`、`scene_mood: str = ""`；`service._run_brief_fields_authorized` 构造时透传 `fabric_craft=request.fabric_craft, scene_mood=request.scene_mood`（冻结仍按新 `brief_id`，不受影响）。
  5. `_messages_for_brief` 增补来源块与措辞：
     - 新增 `"user_provided": {"fabric_craft": <入参原文>, "scene_mood": <入参原文>}`
     - `field_contract` 增补：
       - `fabric_craft`：「**只归纳用户明确告知的产品工艺**（先看 user_provided.fabric_craft，再看 user_brief 里有没有提到），规范成绗缝/特种缝纫/刺绣/滚边术语；两处都没提就**留空**，严禁自行推断或编造。」
       - `scene_mood`：「本批次场景基调/场景族（如：暖光木质厨房、金色午后花园桌），给整批各款提供同一基调；**用户没提就留空**。」
  6. `PROMPT_VERSION = "pod-brief-v2"`。
- [ ] **Step 4：确认 GREEN**（含 `test_brief_runtime.py` 既有 `assert result.prompt_version == PROMPT_VERSION`）

### Task 3 · PART 1：工艺行 + 60-30-10 静态规则

**Files**
- Modify: `local-runtime/wh_local/modules/pod_customization/prompts.py`
- Test: `.../tests/test_prompts.py`

**Interfaces**
- Produces: `_fabric_craft_lines(craft: str) -> list[str]`（空值返回 `[]`）
- Produces: `_COLOR_IMPACT_BLOCK` 增加「固化 60-30-10 比例」静态条

- [ ] **Step 1：写失败测试**：`build_direct_listing_prompt(BusinessFields(product_name="绗缝托特包", fabric_craft="绗缝"), "")` 含 `Fabric craft (this batch): 绗缝.` 且含「优先/以模板真实工艺为准」措辞；空 `fabric_craft` 时**不含**该行；两种情况下 PART 1 均含 `60-30-10`。
- [ ] **Step 2：确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_prompts.py -k 'fabric_craft or color_impact' -q`
- [ ] **Step 3：实现**
  - 在 `build_direct_listing_prompt` 的 `parts` 里、`_FABRIC_REALISM_BLOCK` 之后插入 `*_fabric_craft_lines(fields.fabric_craft)`；函数内容（非空时）：
    ```
    FABRIC CRAFT (this batch) — <craft>. Prefer these craft terms, but the template's TRUE construction
    always wins: never add a craft the template does not show (CARVE-OUT has the highest priority).
    ```
  - `_COLOR_IMPACT_BLOCK` 追加一条：
    `- 60-30-10 COLOR HARMONY: 约 60% 主基调色覆盖产品大面积、约 30% 结构副色用于提手/包边/拼接块、约 10% 点缀色只用于金线/金属件/小坠饰；三档比例必须读得出来。`
- [ ] **Step 4：确认 GREEN**

### Task 4 · PART 4：吃 `scene_mood` + 光照降级

**Files**
- Modify: `prompts.py`（`_set_design_block`、`build_style_listing_prompt`）
- Test: `.../tests/test_prompts.py`

**Interfaces**
- Produces: `_set_design_block(theme, palette, scene, *, scene_mood: str = "") -> str`
- Consumes: `business_fields.get("scene_mood")`（T1/T2）

- [ ] **Step 1：写失败测试**：
  1. `scene_mood="暖光木质厨房"` 时 prompt 含该基调句，且「逐款不同」硬约束仍在；
  2. 空 `scene_mood` 时按 `_STYLE_SCENES` 逐款轮换（现有 `test_set_design_scene_rotates_across_styles` 保持绿）；
  3. **光照降级**：prompt **不含** `warm directional light`，**仍含** `GLOW`。
- [ ] **Step 2：确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_prompts.py -k 'scene_mood or set_design or glow' -q`
- [ ] **Step 3：实现**
  - `_set_design_block` 增参 `scene_mood`；非空时在块首插入 `- Batch scene mood (this batch): <mood>.`，并把现有「starting point for this style: <scene>」口吻改为「**在该基调内的具体起点场景**」；保留「The scene must change from style to style」。
  - **删**现有 `- Lighting: warm directional light (…)` 那条（D10）；GLOW 条**原样保留**。
  - `build_style_listing_prompt` 读取 `scene_mood = str((business_fields or {}).get("scene_mood") or "").strip()` 并传入 `_set_design_block`。
- [ ] **Step 4：确认 GREEN**

### Task 5 · PART 3：元素 Tier 权重

**Files**
- Modify: `prompts.py`（`assign_style_elements`、`build_style_listing_prompt`）
- Test: `.../tests/test_prompts.py`

**Interfaces**
- Produces: `assign_style_elements(...) -> {"hero": str, "narrative": list[str], "rhythm": list[str]}`
- Produces: `_coerce_style_elements(elements) -> tuple[str, list[str], list[str]]`（新/旧形状通吃）
- 旧形状映射：`hero=primary`；`narrative=[co]`（旧数据只有 1 个辅主）；`rhythm=accents`

- [ ] **Step 1：写失败测试**：`assign_style_elements(COWBOY_KEYWORDS, i, "batch-a")` 键为 `hero/narrative/rhythm`；`len(narrative)==2`（池足够大时）、`rhythm` 无重复、相邻款 `hero` 不同、同 seed 可复现；`_coerce_style_elements({"primary":"a","co":"b","accents":["c"]})` 正确降级。
- [ ] **Step 2：确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_prompts.py -k 'assign or tier' -q`
- [ ] **Step 3：实现**
  ```
  hero       = shuffled[offset % count]                 # Tier 1 Hero Anchor，尺度 1.0
  narrative  = [shuffled[(offset+1)%count], shuffled[(offset+2)%count]]  # 去重、排除 hero
  rhythm     = 其余（去重、排除 hero+narrative，≤4）      # Tier 3 Rhythm，一组点缀
  ```
  边界：池空 → 三键空；`count==1` → 仅 `hero`。`build_style_listing_prompt` 的 `subject_block` 同步改为三级措辞（Hero / Narrative / Rhythm），保留「成组小景」「未分配元素禁止出现」及无 hero 时的「自创元素」回退；`signature` 的 `elements=` 段改为 `hero:.. narrative:.. rhythm:..`。
- [ ] **Step 4：确认 GREEN**（同步更新 `test_style_assignment_can_use_up_to_four_accents` 等相关断言）

### Task 6 · PART 3：配色权重引擎 60-30-10

**Files**
- Modify: `prompts.py`（新增 `assign_style_colors`；`build_style_listing_prompt` 增 `seed` 形参）
- Modify: `worker.py`（`_style_generation_inputs` 传 `seed`）
- Test: `.../tests/test_prompts.py`、`.../tests/test_worker.py`

**Interfaces**
- Produces: `assign_style_colors(color_preferences, style_index, seed) -> {"base": str, "structural": str, "highlight": str}`
- Consumes: `seed`（worker 传 `batch_id`）

- [ ] **Step 1：写失败测试**：≥3 色时三角色非空且互不相同、相邻款 `base` 不同、同 seed 可复现、款间可复现；<3 色时返回全空且 prompt **不含** `60-30-10`；prompt 含 `dominan…(60%)` / `structural…(30%)` / `highlight…(10%)` 三色赋值行。
- [ ] **Step 2：确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_prompts.py -k 'color_harmony or assign_style_colors' -q`
- [ ] **Step 3：实现**
  - `assign_style_colors`：以 `seed` 的 sha256 洗牌 `color_preferences`，`base=shuffled[offset%n]`、`structural=shuffled[(offset+ceil(n/3))%n]`、`highlight=shuffled[(offset+ceil(2n/3))%n]`，冲突时向后顺延保证三角色互异；`n<3` 返回 `{"base":"","structural":"","highlight":""}`。
  - `build_style_listing_prompt`：新增 `seed: str = ""`；用 `assign_style_colors` **替换**现有 `accent_colors` 段落，渲染一条 `COLOR HARMONY 60-30-10 (this style): base≈{base}(60%) / structural≈{structural}(30%) / highlight≈{highlight}(10%)`；`signature` 的 `accent_colors=` 段替换为 `color_harmony=...`。
  - `worker._style_generation_inputs` 调用处补 `seed=str(batch.get("batch_id") or "")`。
- [ ] **Step 4：确认 GREEN**（同步更新 `test_style_prompt_keeps_theme_and_rendering_contracts` / `test_style_prompt_has_no_accent_line_without_user_colors` 两处断言）

### Task 7 · 构图模版光照标尺

**Files**
- Modify: `local-runtime/wh_local/modules/pod_customization/composition_runtime.py`
- Test: `.../tests/test_composition_runtime.py`

**Interfaces**
- Produces: 新增 `_LIGHTING_QUALITY_RULE`，并入 `_messages_for_generation` 的 `hard_rules`

- [ ] **Step 1：写失败测试**：`_messages_for_generation(...)` 序列化后的 user 内容含「掠射侧光 / raking」与「漫射补光 / diffused」标尺词。
- [ ] **Step 2：确认 RED**
  `cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_composition_runtime.py -q`
- [ ] **Step 3：实现**
  - 新增常量：`_LIGHTING_QUALITY_RULE = "光线质量标尺：优先「低角度掠射侧光（raking side-light）+ 漫射环境补光（diffused fill）」——掠射光在针脚/绗缝凸起的边缘投下微阴影以显工艺立体感，漫射补光防止暗部死黑。仍不得写出任何具体事物名。"`
  - `hard_rules` 列表追加 `_LIGHTING_QUALITY_RULE`。
  - `PROMPT_VERSION = "pod-composition-v4"`（已确认）。
- [ ] **Step 4：确认 GREEN**

### Task 8 · 前端数据层

**Files**
- Modify: `types/index.ts`（T1 已完成 `PodBusinessFieldsDraft`；此处补 `PodBatch.prompt_version`）
- Modify: `data/podCustomizationDraft.ts`
- Modify: `data/podCustomizationModel.ts`
- Modify: `data/podBrief.ts`
- Modify: `api/podCustomizationApi.ts`（`generateBriefFields` 入参扩展）
- Test: `data/podCustomizationDraft.test.ts`、`data/podCustomizationModel.test.ts`、`data/podBrief.test.ts`

**Interfaces**
- Produces: 两字段进入草稿默认值、`businessFieldsForApi` 透传、`briefFieldsToDraft` 回填、旧草稿归一化
- Produces: `generateBriefFields` 入参改为 `{ brief: string; locale?: string; fabric_craft?: string; scene_mood?: string }`

- [ ] **Step 1：写失败测试**：空草稿两字段为 `""`；含两字段的草稿可往返保存/加载；缺两键的旧草稿归一化为 `""` 且不丢弃；`businessFieldsForApi` 输出含两字段（trim）；`briefFieldsToDraft` 映射两字段。
- [ ] **Step 2：确认 RED**
  `cd web-frontend && node --test --experimental-strip-types src/modules/pod_customization/data/podCustomizationDraft.test.ts src/modules/pod_customization/data/podCustomizationModel.test.ts src/modules/pod_customization/data/podBrief.test.ts`
- [ ] **Step 3：实现**
  - `podCustomizationDraft.ts`：`EMPTY_BUSINESS_FIELDS` 加两键；`isBusinessFields` 两键按 `copy_restrictions` 先例做 `undefined || string`（**不升草稿版本号**，无需迁移）；`businessFieldsOrDefault` 补 `?? ""`。
  - `podCustomizationModel.ts`：本地 `EMPTY_POD_BUSINESS_FIELDS` 补两键；`businessFieldsForApi` 补 `fabric_craft: fields.fabric_craft.trim()`、`scene_mood: fields.scene_mood.trim()`。
  - `podBrief.ts`：`briefFieldsToDraft` 补两键映射。
  - `api/podCustomizationApi.ts`：`generateBriefFields` 入参类型补 `fabric_craft?: string; scene_mood?: string`（`extra="forbid"` 已放宽为可选默认，兼容旧调用）。
- [ ] **Step 4：确认 GREEN**

### Task 9 · 前端「智能填写」文本框内置字段模板 + 生成后只读展示（D8）

**Files**
- Modify: `components/PodBriefInput.tsx`
- Modify: `pages/PodCustomizationPage.tsx`
- Test: `pages/PodCustomizationPage.test.ts`

**Interfaces**
- Produces（`PodBriefInput`）：非受控时文本框**预置字段模板** `DEFAULT_BRIEF_TEMPLATE`（不再新增任何独立输入控件 / props）
- Consumes: 页面 `businessFields` 状态（T8）

**两阶段语义（§2-E）**：① **输入**——**字段模板直接预置在「智能填写」的文本框里**（`产品名称：` / `产品风格：` / `产品工艺：` / `使用场景：` / `目标市场：` / `目标人群：`），用户在每个标签后补内容，整段文本随 `brief` 交给豆包规范化。② **展示**——豆包回填后的 `fabric_craft` / `scene_mood` 值在**确认摘要区只读展示**（与 `design_theme`「统一风格」同款只读口径），用户不可在此编辑结果。

> 后端 `BriefFieldRequest.fabric_craft/scene_mood` 两个**可选**入参保留（当前客户端不单独发送，值走 `brief` 文本由豆包归纳）；`extra="forbid"` 下旧客户端不传即取默认，兼容。

- [ ] **Step 1：写失败测试**：`PodBriefInput` 源码含 `DEFAULT_BRIEF_TEMPLATE`（六个中文字段标签）且 `useState(DEFAULT_BRIEF_TEMPLATE)`；**不含**任何 `pod-brief-provided` / `aria-label="产品工艺"` 之类的独立输入控件；确认摘要 `<dl>` 只读展示两字段。
- [ ] **Step 2：确认 RED**
  `cd web-frontend && node --test --experimental-strip-types src/modules/pod_customization/pages/PodCustomizationPage.test.ts`
- [ ] **Step 3：实现**
  - `PodBriefInput`：新增常量 `DEFAULT_BRIEF_TEMPLATE`（六行标签，`\n` 连接），非受控内部 state 默认取它；移除先前新增的两个独立输入控件与相关 props。
  - `PodCustomizationPage.tsx`：`<PodBriefInput>` 恢复为只传 `onGenerated / history / onSelectHistory`；确认摘要 `<dl>` 增加只读的「产品工艺」「场景基调」两行。
- [ ] **Step 4：确认 GREEN**

### Task 10 · 全量回归验证

- [ ] **Step 1：后端模块测试**
  `cd local-runtime && .venv/bin/python -m pytest -q wh_local/modules/pod_customization/tests`
- [ ] **Step 2：后端全量（含 `local-runtime/tests` 中 POD 相关）**
  `cd local-runtime && .venv/bin/python -m pytest -q local-runtime/tests -k pod`
  （在 `local-runtime` 目录下即为 `-k pod tests`，按实际路径调整）
- [ ] **Step 3：前端单测**
  `cd web-frontend && node --test --experimental-strip-types src/modules/pod_customization/**/*.test.ts`
- [ ] **Step 4：前端类型/构建**
  `cd web-frontend && npm run build`
- [ ] **Step 5：检查 diff**
  `git diff --check && git status --short`（无空白错误；仅计划内文件）

---

## 4. 验收清单

- [ ] 四段式段头与顺序不变；`test_prompt_is_organized_into_four_parts` 绿
- [ ] 工艺/场景字段：用户告知 → 豆包规范化 → 渲染进 PART 1/PART 4；未告知 → 留空回退静态默认
- [ ] PART 4 无 `warm directional light`，保留 `GLOW`；「逐款背景不同」仍成立
- [ ] PART 3：1 Hero + 2 Narrative + 1 组 Rhythm；60-30-10 三色赋值；款内/相邻款约束保持
- [ ] 构图模版生成规则含掠射侧光 + 漫射补光标尺；未引入任何具体事物名
- [ ] 版本号：`pod-brief-v2`、`PATTERN_PROMPT_VERSION v3`、`pod-composition-v4`
- [ ] 前端两字段可在「智能填写」区输入、随 brief 请求交给豆包、生成后只读展示；旧草稿兼容
- [ ] 生图链路（速创）、计费链路、四段式骨架**零改动**

---

## 5. 与方案文档的差异 / 需你拍板

| # | 差异 | 本计划处理 |
|---|---|---|
| D | 方案 §6 未列构图模版版本号递增 | **已确认**：`pod-composition-v3→v4`（§2-D） |
| E | 方案 §5.5/D8 与 §6 关于「输入 vs 只读展示」的表述 | **已澄清**：输入在智能填写区（随 brief 请求交豆包规范化），生成后只读展示；两者不是冲突（§2-E） |
| F | 方案未写明用户填的工艺/场景如何传给豆包 | 新增 `BriefFieldRequest` / `PodBriefRequest` 两个可选入参（§2-F），并在 `_messages_for_brief` 以 `user_provided` 块传入 |
| A | 方案 §5.4 未给元素返回键名 | 采用 `hero/narrative/rhythm` + 旧形状兼容（§2-A） |
| B | 方案 §8-1 配色池 <3 色未定论 | 采用「不渲染 60-30-10、保留现有 palette 轮换」（§2-B） |

以上为本计划的落地口径；如需调整请指出，否则按本计划开工。

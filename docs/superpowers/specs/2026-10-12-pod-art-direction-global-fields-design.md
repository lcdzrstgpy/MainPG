# POD 全定制「美术指导」增强：批级全局字段（工艺 / 使用场景）+ 元素权重

状态：**待确认**（2026-10-12）。代码未改动。
目标读者：POD 模块后端 / 前端开发，产品方。
目标基线：`local-runtime/wh_local/modules/pod_customization/` + `web-frontend/src/modules/pod_customization/`。

---

## 0. 一句话

不改造四段式骨架，也不照搬外部「四宫格机位」文案；新增**两个批级全局字段**——`工艺`（fabric_craft）与`使用场景`（scene_mood）。这两个字段**由用户告知**（用户在「智能填写」里说明），经**已有的 `brief` 接口那一次豆包调用**（`brief_runtime`）**归纳规范化**后产出（豆包不发明），再被**渲染进 `prompts.py` 里的静态 PART 1 / PART 4 提示词块**；**PART 1 / PART 4 本身是纯字符串构造，不发起任何 AI 调用**。同时把 PART 3 的元素分配由「位置序」升级为「Tier 权重序」。

---

## 1. 背景与现状核对

### 1.1 问题

外贸 POD 出图的共同短板是「每款元素轮换了，但整体美术依然捉襟见肘、缺高定质感」：扁、塑料感、元素平铺堆砌。根因是提示词里**没有工艺微触感、色彩主次、纺织专属布光**的专业约束。外部评审文档给了一套很细的建议（Trapunto 高浮雕绗缝、掠射侧光、60-30-10 配色、四宫格转化机位、元素三级视觉权重）。

### 1.2 现状核对（评审建议 → 代码落点）

| 评审建议 | 现状 | 差距 |
| --- | --- | --- |
| PART 4 = 美术指导（工艺 + 光照 + 配色） | PART 4 只有场景美术 `_set_design_block`（`prompts.py:366-395`） | 纺织工艺不在 PART 4，在 PART 1 的 `_FABRIC_REALISM_BLOCK`（`prompts.py:225-256`） |
| 微工艺：Trapunto / Matelassé / 缎面-链式绣 / 绳芯滚边 | 全仓无 `trapunto`；现有块只有「绗缝网格要规整、明线、蓬松均匀」 | 缺特种缝纫术语 |
| 掠射光 raking light | 只有「暖向光 + 发光点 GLOW」 | 缺「低角度侧擦光 + 针脚微阴影」 |
| 60-30-10 配色契约 | `_COLOR_IMPACT_BLOCK`（`prompts.py:204-221`）只要求最强主色 + 饱和通透 | 无比例契约 |
| PART 3 元素三级权重 | `assign_style_elements`（`prompts.py:94-126`）按**位置**洗牌分 primary/co/accents | 有主次尺度 1.0/0.6/0.25，无叙事层级 |
| PART 2 四宫格转化机位 | `_DEFAULT_LISTING_PANEL_LINES`（`prompts.py:139-146`）= 生活场景主图 / 微距 / 细节 / 中性白底素材 | 机位定义与评审不同 |

### 1.3 关键约束（决定了哪些建议能落、往哪落）

1. **四段式是产品侧拍板的硬结构**，段头顺序被测试锁定（`tests/test_prompts.py::test_prompt_is_organized_into_four_parts`），不拆不挪。
2. **构图模版由豆包生成，且其边界是「只讲怎么拍，不讲拍的是什么」**：明文禁止出现材质 / 部件 / 道具名（`composition_runtime.py:421-441`）。
   - ⇒ **机位 / 景别 / 构图 / 光线**（3/4 视角、微距、掠射侧光…）**可以**进构图模版。
   - ⇒ **纺织工艺词**（Trapunto / 缎面绣 / 绳芯滚边）属于「材质 / 部件」，**进不了**构图模版，只能留在静态块。
3. **一个批次 = 一个模板 = 一个产品**：工艺天然批级唯一（不会「一半绗缝一半别的」）。
4. **PART 4 现有硬约束：场景必须逐款不同，禁整批复用同一背景**（`prompts.py:390-391`）。这是为修复「各款背景雷同」而加的，不能被新机制破坏。

---

## 2. 目标与非目标

**目标**

| 编号 | 目标 |
| --- | --- |
| G1 | 把美术指导里的工艺 / 场景约束，从「写死在 `prompts.py` 的静态文本」升级为「**由用户告知**、`brief` 豆包仅做规范化后填入、可人工修订的批级字段」；**美术指导提示词块本身仍是静态构造，不新增 AI 调用** |
| G2 | 工艺成为批级唯一字段，整批一致，且不违背「忠实复现模板真实工艺」的 CARVE-OUT |
| G3 | 使用场景成为批级字段，但**保留款间变化**，不退回「背景雷同」 |
| G4 | 元素分配引入 Tier 权重（Hero/Narrative/Rhythm）、**配色引入 60-30-10 权重**，提升画面主次与叙事感 |
| G5 | 改动**不破坏**四段式骨架与构图模版的「只讲怎么拍」边界 |
| G6 | 「智能填写」输入区**内置字段清单**，明确告诉用户需要填写哪些字段（产品名+风格、产品工艺、使用场景基调），降低遗漏 |

**非目标**

- ❌ 不照搬评审的「3/4 主图 + 微距 + 侧面功能 + 平铺印花」四宫格文案（机位仍由构图模版决定）。
- ❌ 不重构 / 搬移四段式段落（工艺 / 光照 / 配色不整体迁往 PART 4）。
- ❌ 不让豆包自行编造工艺：**工艺由用户告知**，豆包只归纳、不发明（见 §5.2 / §7）。
- ❌ 不改生图链路（仍为速创 `image_gpt_2.5`），不改计费。

---

## 3. 已确认决策

| 编号 | 决策 |
| --- | --- |
| D1 | 四宫格机位**不照搬评审文案**，仍由「构图模版」决定 |
| D2 | 「构图模版更专业」通过**升级构图模版生成器（豆包）**实现，把机位/景别/光照语汇作为给豆包的质量标尺，而非硬编码进大提示词（**本期纳入**） |
| D3 | 新增 `工艺`为**批级全局字段**，与 `design_theme` 同级；**取值来源 = 用户告知**（用户最清楚自家产品工艺），`brief` 豆包只做规范化 |
| D4 | 新增 `使用场景`为**批级全局字段**；语义 = **批级场景基调 / 场景族**，款间在其中变化 |
| D5 | 新字段取值**由用户输入决定**，`brief` 豆包只归纳、不自由发挥；**PART 1 / PART 4 只接收字段值做静态拼接，不调 AI** |
| D6 | PART 3 元素分配**沿用现有确定性洗牌**，只把「抽取顺序」映射为 Tier 权重（**已确认接受「位置序 = 权重序」**） |
| D7 | 提示词内容变更必须**递增版本号**：`pod-brief-v1 → v2`、`PATTERN_PROMPT_VERSION v2 → v3` |
| D8 | 「智能填写」输入区**内置显式输入项**（独立输入控件），列出需用户提供的字段（产品工艺、使用场景基调等）；豆包只做规范化 |
| D9 | **光影归属 = 构图模版（豆包）**，大提示词不写光线；**规格固定为「低角度掠射侧光 + 漫射环境补光」**，落地于构图模版生成器（`composition_runtime.py`）（**本期纳入**） |
| D10 | PART 4 `_set_design_block` 的光照句**降级**：删去「暖向光」等主导性光照措辞，**只保留 GLOW 作为场景氛围** |
| D11 | 新增**配色权重引擎**：与元素同构的确定性轮换，逐款产出 **60-30-10 三角色色**（主基调 / 结构副色 / 点缀色） |
| D12 | 两个新字段**都列入 brief 字段集合、但允许返回空字符串**（即"必填但可为空"），落地 D5「用户没告知 → 留空」 |

---

## 4. 核心事实（已核实）

| 事实 | 依据 |
| --- | --- |
| 「整批统一风格」= `BusinessFields.design_theme` | `contracts.py:55` |
| 它由智能前置层生成，属 `_SCALAR_FIELDS` | `brief_runtime.py:133-139` |
| 它渲染在 **PART 1**，标签 `Batch-wide theme & style` | `prompts.py:338` |
| 前端不可编辑，仅确认摘要只读展示（`统一风格`） | `PodCustomizationPage.tsx:1001` |
| 元素分配是**确定性洗牌**（`batch_id` 为种子），非每次随机 | `prompts.py:111-114` |
| 当前分配是 **1 主打 + 1 辅主 + ≤4 点缀（3~6 个）** | `prompts.py:117-126` |
| brief 输出是 `json_schema` strict，且键集合必须与 `_FIELD_KEYS` 完全一致 | `brief_runtime.py:155-195`、`validate_brief_fields:215-216` |
| `PromptVersion` 目前只认 `v1/v2` | `contracts.py:18` |

---

## 5. 方案设计

**AI 调用边界（重要，避免歧义）**：本方案全流程**只有一处 AI 调用**——`brief` 接口那一次豆包调用（`brief_runtime`），它一次产出包括两个新字段在内的业务字段。PART 1 / PART 4 的提示词块（`_FABRIC_REALISM_BLOCK`、`_set_design_block` 等）**仍是 `prompts.py` 里的静态字符串构造，不发起任何 AI 调用**；新字段只是被**作为变量插入**进这些静态块。生图链路（速创 `image_gpt_2.5`）与计费链路均不变。

### 5.1 新增两个批级全局字段

| 字段（提案名） | 中文标签 | 语义 | 插入位置（静态块内变量） | 关键约束 |
| --- | --- | --- | --- | --- |
| `fabric_craft` | 工艺 | 批级唯一，整批一致 | PART 1，**叠加**在 `_FABRIC_REALISM_BLOCK` 之上 | 服从 CARVE-OUT：以模板真实工艺为准，不得凭空添加 |
| `scene_mood` | 场景基调 | 批级场景基调 / 场景族，款间变化 | PART 4 `_set_design_block` 入参，替换 `_STYLE_SCENES` 死轮换 | 仍必须满足「逐款背景不同」（`prompts.py:390-391`） |

**为什么走「字段」而不是继续写死在 `prompts.py`**：与 `design_theme` 同构——批级字段天然保证整批一致，且可人工修订。**工艺的取值来源是「用户告知」**（用户最清楚自家产品是绗缝还是帆布），`brief` 豆包只把用户的话规范化成术语，不发明；用户没提就留空、回退现有静态块。注意：这里说的「填」是 **`brief` 接口产出字段值**，不是让 PART 4 去调 AI。

> **配套（D8）**：为降低用户遗漏，在「智能填写」输入区**内置字段引导**——列出希望用户提供的字段（至少：产品工艺；配合场景基调），例如提示「请说明产品工艺：如绗缝 / 缎面绣 / 帆布平缝 / 绳芯滚边」。若自然语言提取不稳定，可退化为在输入区放一个**显式的「工艺」输入项**，由用户直接填。

### 5.2 生成链路（brief 那一次豆包）

- `contracts.py`：`BusinessFields` 新增 `fabric_craft` / `scene_mood` 两个标量，`max_length` 与其它标量一致（现状 500）。
- `brief_runtime.py`：
  - 加入 `_SCALAR_FIELDS`（因此自动进入 `_FIELD_KEYS` → strict schema 的 `required`）；
  - `field_contract` 增补两项描述；
  - **不加入** `_REQUIRED_SCALARS`（允许为空 → 留空即回退静态默认，避免契约修复重试率上升）；
  - 措辞要求：**归纳用户输入，不得自由发挥**：
    - `fabric_craft`：「**只归纳用户在输入里明确告知的产品工艺**，规范成特种缝纫/绗缝/刺绣/滚边术语；用户没提就**留空**，严禁自行推断或编造。」
    - `scene_mood`：「本批次场景基调/场景族（如：暖光木质厨房、金色午后花园桌），用于给整批各款提供同一基调，具体布置仍逐款变化。」
- 版本：`pod-brief-v1 → pod-brief-v2`。

### 5.3 提示词插入改动（`prompts.py`，纯静态字符串构造，无 AI 调用）

- **PART 1**：在业务字段渲染区（`prompts.py:333-344` 一带）新增一行
  `Fabric craft (this batch): {fabric_craft}.`，并紧邻 `_FABRIC_REALISM_BLOCK`，措辞强调「**优先使用以下工艺词，但必须以模板真实工艺为准，不得凭空添加模板没有的工艺**」。
  留空则不渲染该行（回退到现有静态块行为）。
- **PART 4**：`_set_design_block` 增加参数 `scene_mood`，行为：
  - `scene_mood` 非空 → 以它作为**整批场景基调**，`_STYLE_SCENES` 的逐款值降级为「在该基调内的具体起点场景」；
  - 空 → 维持现状（逐款轮换 7 条）。
  - 硬约束保留：场景逐款不同。
- 版本：`PATTERN_PROMPT_VERSION v2 → v3`。

### 5.4 PART 3 元素 Tier 权重（D6）

沿用 `assign_style_elements` 的确定性洗牌结果，把「抽取顺序」映射为三级权重：

| Tier | 占比 | 来源 | 现有字段映射 |
| --- | --- | --- | --- |
| Tier 1 Hero Anchor | ~50% | 洗牌序列第 1 个 | primary（尺度 1.0） |
| Tier 2 Narrative | ~30% | 洗牌序列后续 2 个 | 需要 1 个拆成 2 个：primary + 2 co |
| Tier 3 Rhythm | ~20% | 其余归为「1 组点缀纹样」 | accents（0.25，成组） |

- 取数从「1 主打 + 1 辅主 + ≤4 点缀」改为「**1 Hero + 2 Narrative + 1 组 Rhythm**」。
- 仍保持：款内不重复、相邻款 Hero 必不同、批内可复现。
- 渲染措辞同步更新 PART 3 的 `subject_block`（`prompts.py:448-467`）。

> **已知局限（已确认接受，D6）**：抽取顺序是**位置序**，不保证语义上是「主角材质」——「雪花」可能被排在 Hero、「姜饼屋」被排在 Rhythm。产品方已确认接受此局限；若日后要根治，需让 brief 生成元素时打 tier 标签（改元素契约）。

### 5.5 「智能填写」输入区的内置字段引导（D8）

在现有「智能填写」输入区（`PodBriefInput.tsx`）里**内置一组字段项**，明确列出**需要用户提供的字段**，让用户一眼知道"要填什么、怎么填"，避免遗漏工艺 / 场景这类只有用户知道的信息。

**内置字段清单（初版）**

| 用户需填写 | 用途 | 落到哪个字段 | 提示示例 |
| --- | --- | --- | --- |
| 产品名 + 风格 | 兜底全部业务字段 | `product_name` / `design_theme` 等 | 复古印花托特包，美式西南荒野风 |
| 产品工艺 | 批级工艺约束 | `fabric_craft` | 绗缝 / 缎面绣 / 帆布平缝 / 绳芯滚边（没有特殊工艺可留空） |
| 使用场景基调 | 批级场景基调 | `scene_mood` | 暖光木质厨房 / 金色午后花园桌 |

**呈现形态（已定 D8：显式输入项）**

把上表字段做成**独立输入控件**（例如一个「产品工艺」输入框、一个「使用场景基调」输入框），用户逐项填写；豆包只把用户填写的内容**规范化**成术语。

**共同规则**：**豆包都只归纳、不发明**；用户没填的字段**留空**，并回退到现有静态默认（工艺 → `_FABRIC_REALISM_BLOCK`，场景 → `_STYLE_SCENES` 逐款轮换）。

### 5.6 光影归属（D9 / D10）

**已定**

- **归属 = 构图模版（豆包）**：大提示词（PART 4）**不再写光线**。
- **规格 = 固定「低角度掠射侧光（raking side-light）+ 漫射环境补光（diffused fill）」**：掠射光在针脚 / 绗缝凸起边缘投下微阴影，漫射补光防暗部死黑。
- **落地 = 构图模版生成器**（`composition_runtime.py`）：把该规格写进生成规则，作为豆包产出四格光照的质量标尺（属 D2 那条线，**本期纳入**）。

**PART 4 连带改动（D10）**

- `_set_design_block` 现有光照句「warm directional light…」**删去主导性光照措辞**；
- **只保留 GLOW**（场景里要有至少一个可见暖光源作氛围）——GLOW 属于「场景元素」而非「产品主光」，不与构图模版打架。

**与评审的偏差（明确记录，避免以后对不上）**

- 评审要求「光影放进 PART 4 美术指导引擎」；本方案改为「**光影归构图模版**」，因为现有架构里光线本来就在构图模版（§1.3-2）。
- 因此 PART 4 的「引擎」**不含光学模块**，只保留「场景 + 氛围光（GLOW）」。

### 5.7 配色权重引擎：60-30-10（D11）

**背景**：现有配色**本来就是逐款轮换**——`palette` 取自 `_STYLE_PALETTES[(offset*7)%12]`（若用户给了配色则用用户的），`accent_colors` 从 `color_preferences` 按 `(offset*7)%n` / `(offset*7+3)%n` 取两色（`prompts.py:429-446`）。它和元素一样是"轮换抽词"，但**没有主次、没有比例**——评审说的"配色无主次、毫无层次"正指这里。

**做法（与元素 Tier 引擎同构）**：新增 `assign_style_colors(color_preferences, style_index, seed)`，以 `batch_id` 为种子洗牌配色池、按款序轮换，为每款产出三个角色色：

| 角色 | 占比 | 来源 | 作用 |
| --- | --- | --- | --- |
| Dominant Base 主基调色 | ~60% | 洗牌序列 1 个 | 产品本体 / 大面积底色（如复古水洗亚麻白、复古粉、鼠尾草绿、深绯红） |
| Structural Accent 结构副色 | ~30% | 洗牌序列 1 个 | 提手、包边、大面积拼接块 |
| Pop Highlight 点缀色 | ~10% | 洗牌序列 1–2 个 | 金线刺绣、金属拉链铜光、小坠饰 |

- 仍保持：款内三角色**不重复**、相邻款**主基调不同**、批内**可复现**。
- 配色池来源：优先 `color_preferences`（用户 / brief 给的 ≥10 个具体颜色）；缺失时回退现有 `_STYLE_PALETTES` 轮换。

**渲染**

- **PART 3（逐款，动态）**：把现有 `accent_colors` 升级为 60-30-10 三色赋值，例如
  `COLOR HARMONY 60-30-10: base≈{base}(60%) / structural≈{structural}(30%) / highlight≈{highlight}(10%)`。
- **PART 1（静态）**：`_COLOR_IMPACT_BLOCK` 增加"固化 60-30-10 比例"的静态规则句，与现有「产品用最强主色 + 饱和通透」互补。

**与评审的偏差（明确记录）**：评审说"在 PART 4 固化色彩比例规则"；本方案把**比例规则（静态）**放 PART 1、**逐款配色赋值（动态）**放 PART 3——因为配色轮换本就在 PART 3（§1.3-1 的四段式职责）。

---

## 6. 契约 / 版本 / 改动清单

**后端**

- `contracts.py`：`BusinessFields` + `fabric_craft` / `scene_mood`；`PromptVersion` 增加 `"v3"`。
- `brief_runtime.py`：`_SCALAR_FIELDS` / `field_contract` / 措辞 / 版本号。
- `prompts.py`：PART 1 插入工艺字段 + `_COLOR_IMPACT_BLOCK` 加 60-30-10 静态规则（D11）；PART 4 吃 `scene_mood` 且**光照句降级为只留 GLOW（D10）**；PART 3 元素 Tier 权重（D6）+ 配色三色赋值（D11）；`PATTERN_PROMPT_VERSION`。
- `composition_runtime.py`（**D9，本期纳入**）：构图模版生成规则加入「低角度掠射侧光 + 漫射补光」光照标尺。
- `service.py`：确认新字段随 `business_fields` 透传（`design_theme` 已在，新增字段同路径，无需新逻辑，但需核对快照写入）。
- `worker.py`：核对 `_style_generation_inputs` → `build_style_listing_prompt` 是否需显式传 `scene_mood`（大概率随 `business_fields` 走）。

**前端**

- `types/index.ts`、`data/podCustomizationDraft.ts`（初始值与归一化）、`data/podCustomizationModel.ts`（默认值 / 合并 / 摘要展示）、`data/podBrief.ts`（回填映射）。
- **「智能填写」输入区（`PodBriefInput.tsx` / `PodCustomizationPage.tsx`）新增显式输入项（D8）**：产品工艺、使用场景基调等做成独立输入控件，见 §5.5。
- 展示形态：`scene_mood` 可沿用 `design_theme` 的「隐藏只读 + 确认摘要展示」；**`fabric_craft` 按 D8 以显式输入项呈现**，便于用户告知。

**测试**

- `tests/test_prompts.py`：四段式断言不受影响；新增「PART 1 含工艺行」「PART 1 含 60-30-10」「PART 4 含场景基调」「PART 4 光照句已降级为只留 GLOW」「PART 3 含配色三色赋值」用例；元素 Tier 与配色角色数量断言更新。
- `tests/test_brief_runtime.py`：新字段的 schema / 契约 / 留空回退。
- `tests/test_composition_runtime.py`：构图模版生成规则含「掠射侧光 + 漫射补光」标尺（D9）。
- 前端 `podCustomizationModel.test.ts` / `podBrief.test.ts` / `podCustomizationDraft.test.ts`。

---

## 7. 风险与边界

| 风险 | 缓解 |
| --- | --- |
| 豆包看不到实物、可能编造工艺（如给光面帆布包写 Trapunto） | **工艺由用户告知**，豆包只归纳、不发明；用户没提即留空；字段措辞「须以模板真实工艺为准、不得凭空添加」；PART 1 里工艺词写「**优先使用**」而非「必须使用」，CARVE-OUT 仍为最高优先 |
| `scene_mood` 若被当成死场景，回归「背景雷同」 | 字段语义固定为「基调/场景族」，PART 4 硬约束「逐款不同」保留 |
| 新字段进 brief 字段集合后，契约修复重试率上升 | D12：字段列入但允许返回空串；不进 `_REQUIRED_SCALARS` |
| 配色池不足 3 色（用户只给了 <3 个颜色） | 回退 `_STYLE_PALETTES` 补齐，或角色色缺省留空（见 §8） |
| 版本号未递增导致新旧批次无法归因 | D7 强制：改提示词必升版本 |
| 老草稿缺新字段 | 归一化缺失键为空字符串，不丢弃旧草稿（沿用现有 `copy_restrictions` 做法） |
| 工艺全局 vs 批内多产品 | 已核实「一个批次 = 一个模板 = 一个产品」，成立 |

---

## 8. 遗留细节（主要决策已闭合）

原第 1–5 条均已拍板，不再列为未决：

| 原条目 | 结论 |
| --- | --- |
| ① 新字段是否算"必填" | **D12**：列入字段集合、允许返回空串 |
| ② 元素 Tier 语义分层 | **D6**：接受「位置序 = 权重序」，不做语义分层 |
| ③ 构图模版是否纳入本期 | **D2 / D9**：纳入本期 |
| ④ 60-30-10 配色 | **D11**：做成配色权重引擎 |
| ⑤ D8 输入区形态 | **D8**：显式输入项 |

以下为实现时需落定的小细节：

1. **配色池不足 3 色时**：`color_preferences` 少于 3 个 → 回退 `_STYLE_PALETTES` 补齐，或角色色缺省留空。
2. **元素 Tier 2 的取数顺序**：确认 = 洗牌序列第 2、3 个（Hero = 第 1；Rhythm = 其余成组）。
3. **`scene_mood` 为空时**：确认回退 `_STYLE_SCENES` 逐款轮换不变。

---

## 9. 交付文件清单

**修改（后端）**

- `local-runtime/wh_local/modules/pod_customization/contracts.py`
- `local-runtime/wh_local/modules/pod_customization/brief_runtime.py`
- `local-runtime/wh_local/modules/pod_customization/prompts.py`
- `local-runtime/wh_local/modules/pod_customization/composition_runtime.py`（D9：光照标尺）
- `local-runtime/wh_local/modules/pod_customization/service.py`（核对透传）
- `local-runtime/wh_local/modules/pod_customization/worker.py`（核对透传）
- `local-runtime/wh_local/modules/pod_customization/tests/test_prompts.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_brief_runtime.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_composition_runtime.py`（D9）

**修改（前端）**

- `web-frontend/src/modules/pod_customization/types/index.ts`
- `web-frontend/src/modules/pod_customization/data/podCustomizationDraft.ts`
- `web-frontend/src/modules/pod_customization/data/podCustomizationModel.ts`
- `web-frontend/src/modules/pod_customization/data/podBrief.ts`
- `web-frontend/src/modules/pod_customization/components/PodBriefInput.tsx`（显式输入项：产品工艺 / 使用场景基调，见 §5.5）
- `web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.tsx`（输入区挂载）
- 对应 `*.test.ts`

**无需改动**：生图链路（速创）、计费链路、四段式骨架与段头。

# POD 全定制重构：两级批次 + 按角色列式生成

状态：**待评审 v2**（2026-10-22）
目标读者：POD 后端 / 前端开发，产品方
目标基线：`local-runtime/wh_local/modules/pod_customization/` + `web-frontend/src/modules/pod_customization/`

> v2 相对 v1 的变化：补齐**两级批次定义**、**四轮提示词分别重构**、**构图模板从「四格」改为「四轮」**，并明确旧构图模板的兼容方式。

---

## 0. 一句话

现在一次调用生成「一款的四格」，四格之间只有文字约束、没有像素锚，换机位就重排图案、改结构。重构后引入两级批次：**一级批次**定款数与整批风格；**二级批次**固定 4 款，内部分 **4 次调用（4 轮）**——第 1 轮一次出 4 款主图，第 2/3/4 轮分别以这 4 张主图为锚点，生成 4 款的细节图 A / 细节图 B / 素材图。**4 款 = 4 次调用；计费改为按二级批次上报（1 组 = 1 link）。**

---

## 1. 现状与归因

| 环节 | 现状 | 位置 |
| --- | --- | --- |
| 一次生成 | full 每款一次 provider 调用，固定 `2048x2048` 方阵母图 | `image_providers/doubao.py:38-45` |
| 拆格 | 本地按象限行优先硬切，左上→右上→左下→右下 | `ai_runtime.py:251-268` |
| 角色 | `LISTING_IMAGE_ROLES = ("lifestyle","detail_a","detail_b","hero")` | `prompts.py:47-49`、`prompts.py:303-308` |
| 跨格一致性 | 只有文字锁 `ARTWORK LAYOUT LOCK` | `prompts.py:469-489` |
| 四段式提示词 | PART 1 核心契约 / PART 2 四格机位 / PART 3 元素轮换 / PART 4 美术指导 | `prompts.py:394-412` |
| 构图模板 | 一段大白话 → 四格画面指令（`panel_1..4`），只保留最新一份 | `contracts.py:175-260`、`composition_runtime.py:461-503` |
| 落库 | 每款 4 行 `style_grid_results` | `repository.py:4220-4286` |

**问题归因**（四点，互相独立）：

1. 只有文字锁，没有像素锚：换机位时模型在重新画，主元素位置、尺度、接花间距都会漂。
2. 四格共享同一次去噪，风格齐但「同一件刚性产品 + 同一张印刷布局」没有被约束住。
3. 每格有效分辨率只有 1024（2048 切四份，交付 800），细节格最易糊、最易自由发挥。
4. 拆完没有跨格校验，也没有局部重画能力；一格崩要整组重试。

---

## 2. 目标 / 非目标

**目标**

| 编号 | 目标 |
| --- | --- |
| G1 | 定义两级批次：L1 定款数/整批风格；L2 固定 4 款、内含 4 轮调用 |
| G2 | 每款 4 张图共享同一张主图像素锚；4 款 = 4 次调用 |
| G3 | 提示词按轮次重构：第 1 轮 A1 硬约束 + B1 构图 + C1 元素/色系轮转 + D1 美术指导；第 2/3/4 轮只有 A2 硬约束/主体锚定 + B2 场景构图 |
| G4 | 构图模板语义从「四格怎么拍」改为「四轮怎么拍」：每个 slot 注入对应轮次的 **B 段场景构图**；第 1 轮全量生效，第 2/3/4 轮只取视角部分；旧模板仍可直接使用 |
| G5 | 失败按「组 × 角色」隔离，支持「单款 × 缺失角色」重跑 |
| G6 | 旧批次、旧客户端、旧单款重跑走 legacy，行为不变 |

**非目标**

- ❌ 不改复刻 / 裂变 / 半定制的生成架构。
- ❌ 不改四张交付图的角色语义与前端列序。
- ❌ 不做生成后视觉校验与自动重绘（下一期）。
- ❌ 不改远端「1 款 = 1 link」的计费口径。
- ❌ 不改交付分辨率策略。

---

## 3. 两级批次模型

### 3.1 L1 一级批次（batch）

**职责**：定「这一批要做多少款、整体是什么风格」。

持有并冻结：

- `requested_count`（必须是 4 的正整数倍，4–200）
- `template_id` / 模板快照 / 结构事实表
- `business_fields`（产品、品类、市场、人群、卖点、主题风格、配色、禁用元素、面料工艺）
- `creative_prompt`（运营创意编辑）
- `composition_json`（构图模板快照）
- `prompt_version`（v19）与 `generation_layout`（`legacy` / `column_v1`）
- 计费冻结（1 款 = 1 link）

生命周期沿用现有批次状态机（`queued / generating_patterns / compositing / generating_titles / completed / partial_failure / failed / pausing / paused / cancelling / cancelled / settlement_pending`）。

**前端看到的仍然是 L1**：一款一行、四列，进度按款计数。

### 3.2 L2 二级批次（style group）

**职责**：固定 4 款，负责这 4 款的 4 轮生成。

- 第 `g` 组覆盖 `style_index = 4g-3 .. 4g`，`group_count = requested_count / 4`。
- 每组的 4 轮：

| 轮次 | role | 产出 | 参考锚 |
| --- | --- | --- | --- |
| 第 1 轮 | `lifestyle`（主图） | 4 款主图 | 模板 + 结构视图 + 结构事实表 |
| 第 2 轮 | `detail_a`（细节 A） | 4 款细节 A | 4 张主图 |
| 第 3 轮 | `detail_b`（细节 B） | 4 款细节 B | 4 张主图 |
| 第 4 轮 | `hero`（素材图） | 4 款素材图 | 4 张主图 |

- 组内象限↔款绑定固定：左上=第 1 款、右上=第 2 款、左下=第 3 款、右下=第 4 款。
- **每一轮（含第 2/3/4 轮）的输出格式都是 2×2 四格**，拆分后每格归属一个款；不允许把细节轮改成单张全幅。
- 第 2/3/4 轮只依赖第 1 轮，**三路并行**；关键路径是 2 次调用。
- L2 状态：`queued` → `generating_primary` → `generating_derived` → `completed / partial_failure / failed`。
- L2 是执行单元，不是计费单元：计费 link 仍然挂在 L1 的每一款上。

### 3.3 两级关系

```
L1 batch（N 款，N % 4 == 0，整体风格 + 计费冻结）
├── L2 group 1（款 1..4）
│   ├── round 1  lifestyle  → 4 张主图
│   ├── round 2  detail_a   → 4 张细节 A
│   ├── round 3  detail_b   → 4 张细节 B
│   └── round 4  hero       → 4 张素材图
├── L2 group 2（款 5..8）
└── ...
```

- L1 状态由 L2 汇总：所有款 4 格齐 → `completed`；有失败 → `partial_failure`；全失败 → `failed`。
- 暂停/取消在 L1 发起；worker 在每轮 sheet 边界检查，已发出的调用跑完即停。
- 断点续跑：按 L2 的 sheet 状态跳过已完成轮次。

---

## 4. 提示词重构（核心）

每一轮独立拼装，不再是一套提示词描述一款四格：**第 1 轮四段（A1/B1/C1/D1），第 2/3/4 轮两段（A2/B2）**。

### 4.1 第 1 轮（主图轮）：A1 + B1 + C1 + D1

| 段 | 名称 | 内容 |
| --- | --- | --- |
| A1 | 整图硬约束 | 2×2 等分 + 干净分割线；同一产品、同一结构/材质/比例；GEOMETRY LOCK；结构事实表；透明/亮面材质锁；平印锁；禁文字/水印/象限标号；内饰不印；**新增 `QUADRANT ISOLATION LOCK`**（四格 = 四款，禁止串图串色）；删除「四格同图案」与 `ARTWORK LAYOUT LOCK` |
| B1 | 场景构图 | 构图模板 `panel_1` **原样注入**：机位与角度、景别、主体位置与留白、光线、背景处理。本轮四格共用同一机位；新增 `SAME CAMERA, DIFFERENT ARTWORK` |
| C1 | 元素轮转 / 色系风格引擎 | 按象限分别注入该款的 hero/narrative/rhythm、pattern_layout、60-30-10 配色、rendering、density；每格只允许本款元素 |
| D1 | 美术指导 | 沿用现有 SET DESIGN / COLOR IMPACT / MATERIAL REALISM / 面料工艺块，并按批级 `art_direction` 增强：场景有人味、**5-8 个同题材道具按近/中/远分层**、暖向主光、画面内可见光源、浅景深、材质真实、色彩通透。**光线方向与背景以 B1 为准，D1 只补质感标准** |

关键新增块：

```
QUADRANT ISOLATION LOCK — the four panels are four DIFFERENT product designs (four styles of one
collection) on the SAME physical product. Each quadrant's motifs, palette, scale rhythm and
rendering are exclusive to that quadrant. Never copy, blend, bleed or swap any motif or colour
across quadrants or across the divider. If two quadrants read as the same design, the result is
FAILED.
```

```
SAME CAMERA, DIFFERENT ARTWORK — all four quadrants share one camera setup, one lens, one framing
and one lighting direction (the primary-image role). The four quadrants must differ by ARTWORK,
never by camera angle. Four different elevations, crops or scene styles in one sheet = FAILED.
```

### 4.2 第 2/3/4 轮（派生轮）：只有 A2 + B2

| 段 | 名称 | 内容 |
| --- | --- | --- |
| A2 | 整图硬约束 + 主体锚定 | **`OUTPUT FORMAT LOCK`（2×2 等分 + 干净分割线，必须）**；`ANCHOR BINDING`（参考图 k ↔ 象限 k）；**明确「必须参考第 1 轮结果图，不得重画主体或图案」**；`RIGID ARTWORK LOCK`；产品/几何/材质/结构事实表；禁文字；象限隔离 |
| B2 | 场景构图 | 构图模板 `panel_2/3/4` **原样注入，是机位的唯一来源**：改变视角/景别/构图/景深；场景、光线、色温、明暗与锚点连续；**禁止复刻锚点机位**（同距离同裁切判失败）；素材轮默认干净中性背景；同一轮四格共用该 slot |

**为什么 2/3/4 轮只有 A2 + B2**：图案与元素已经被锚点锁死，再写 C（轮转引擎）只会诱导模型重新设计；美术质感在第 1 轮已经定稿，再写 D 等于允许它换场景。派生轮唯一应该变化的就是视角，而视角正好由 B2 的构图 slot 控制。因此 C/D 不渲染，只在 A2 里保留一句「保持锚点的材质、光线、色温与明暗」；景深属于本轮机位的一部分，必须随模板方向变化。

关键新增块：

```
OUTPUT FORMAT LOCK (mandatory in rounds 2/3/4 as well) — every call returns ONE square 2x2 contact
sheet with exactly four equal panels and one clean centred divider, same format as round 1. Never
return a single full-frame image, a 1x4 or 4x1 strip, unequal panels, a collage with borders, or any
number of panels other than four. "This call only needs the detail image" never means "return one
image": the call still returns four detail panels, one per style.
```

```
ANCHOR BINDING — the four attached reference images ARE the accepted round-1 result. They are the
FOUR different styles of this contact sheet, bound by position, not by content:
- reference image 1 = the product shown in the TOP-LEFT quadrant
- reference image 2 = TOP-RIGHT
- reference image 3 = BOTTOM-LEFT
- reference image 4 = BOTTOM-RIGHT
Each quadrant uses ONLY its own reference image. Never regenerate the product or the artwork from
scratch, and never copy, blend or swap motifs, colours or layouts between quadrants.
```

```
RIGID ARTWORK LOCK — the printed artwork is a rigid sheet glued to the product. Changing the camera
rotates the whole sheet with the product; it is never permission to re-layout, re-centre, rotate,
rescale or re-space the artwork. The hero motif's position on the product face, its size relative to
the face, its orientation and its distance from seams/edges must be identical to the anchor. A detail
panel is a zoom into the SAME layout, never a new version of the artwork.
```

### 4.3 内置硬规则改写

现有 `_BUILTIN_HARD_RULES`（`prompts.py:419-427`）是「一款四格」口径，column 模式改用：

1. 每个二级批次（4 款）正常生成 4 轮，每轮一张 2×2；失败最多重试一次。
2. 每轮四格固定对应组内 4 个款：左上=第 1 款、右上=第 2 款、左下=第 3 款、右下=第 4 款。
3. 同一款的四轮产物必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致；同一轮四格必须是四个不同款，禁止串色串图。
4. 不得添加未授权品牌、商标、版权角色、文字或水印。
5. 内饰/内衬保持无花色统一纯色，图案只印外表面。

### 4.4 模块与版本

- 新增 `column_prompts.py`，从 `prompts.py` 复用常量与轮转引擎（`_coerce_style_elements`、`assign_style_colors`、`_STYLE_*`、结构锁块、`_set_design_block`），避免把 `prompts.py` 继续撑大。
- 主要函数：
  - `build_column_base(fields, subject_anchor, structure_facts)`：共享产品/结构锁与批次字段。
  - `build_column_round_prompt(base, *, role, group_styles, composition, attempt, reference_roles)`：
    - `role == lifestyle` → 渲染 **A1 + B1 + C1 + D1**；
    - `role in detail_a/detail_b/hero` → 渲染 **A2 + B2**；
    - 构图 slot 只注入 **B 段**，不直接写进 D。
  - `build_column_single_panel_prompt(...)`：单款单角色重跑（1:1 直出，不拆格）——**下一期**，v1 不实现。
- `PATTERN_PROMPT_VERSION` 升 **v19**（`prompts.py`）：v18 为列式首版，v19 修正派生轮机位锁（景深可变化、禁止复刻锚点机位）。
- 旧 `build_direct_listing_prompt` / `build_style_listing_prompt` 保留给 legacy 批次与旧重跑。

### 4.5 锚点下发：复用 COS 公网 URL

- 第 1 轮拆出的 4 个主图面板在后处理阶段已经发布到 COS，并写入 `pod_customization_style_grid_publications.public_url`。
- 第 2/3/4 轮直接读这 4 个 `public_url` 作为参考图，**不再读本地字节、不再转 base64**；provider 的 `prepare` 在引用带 URL 时直接透传。
- 需要扩展 `ListingReferenceImage` / `GridReference`：增加可选 `url`；`ai_runtime._grid_references` 原样保留 URL；`doubao.prepare` 优先用 URL，缺失时才回退 data URL。
- 前置条件：COS 公网可访问（现有面板发布已经要求 public URL）。如果 URL 不可访问，回退到「压到 768 的 JPEG data URL」。
- 这样 4 张锚点不增加请求体积，也不需要重复上传。

---

## 5. 构图模板改造

### 5.1 语义变化：四格 → 四轮

`panel_1..4` 键与存储结构**不变**，但含义从「一张图里的四格」变成「四轮调用的角色机位」：

| 键 | 旧含义 | 新含义 |
| --- | --- | --- |
| `panel_1` | 左上格（主图）怎么拍 | 第 1 轮四款主图共用的机位/景别/构图/光线/背景 |
| `panel_2` | 右上格（细节 A）怎么拍 | 第 2 轮四款细节 A 共用 |
| `panel_3` | 左下格（细节 B）怎么拍 | 第 3 轮四款细节 B 共用 |
| `panel_4` | 右下格（素材图）怎么拍 | 第 4 轮四款素材图共用 |

旧模板**无需数据迁移**：每个 slot 原本描述的就是该角色的拍法，换成「整轮四格共用」后文字依然成立。UI 文案改为「这一轮四款共用的机位/景别/构图/光线/背景」。

### 5.2 构图模板 → 四轮提示词的注入关系

**先厘清现状：场景目前从哪来（full 模式）**

| 来源 | 现状 | 位置 |
| --- | --- | --- |
| `scene_mood` | 批级字段；非空时作为整批「场景基调 / 场景族」，逐款场景降级为基调内的具体起点 | `contracts.py:104`、`prompts.py:677-692` |
| `_STYLE_SCENES` | `scene_mood` 为空时使用的**代码内置场景池**，共 7 条，按款序轮换 | `prompts.py:649-657`、`prompts.py:853` |
| `_set_design_block` | 代码生成的场景细节：道具近/中/远分层、暖向主光、画面内可见光源、浅景深、接触阴影 | `prompts.py:677-722` |
| 构图模板 | **目前只注入 PART 2 的机位描述，没有进入 PART 4** | `prompts.py:682` 注释原文：「构图模板链路并未进入生图提示词」 |

所以这里不是「提高构图模板的优先级」，而是**新增一条注入链路**：把豆包生成的四轮构图指令，分别注入每轮提示词的 **B 段场景构图**。

构图模板属于 L1，创建批次时冻结进 `composition_json`；批内**所有 L2 组共用同一份**，每个 L2 的四次调用各读一个 slot：

| 轮次 | 读取 slot | 注入 B 段 | 生效范围 |
| --- | --- | --- | --- |
| 第 1 轮 主图 | `panel_1` | **B1**：机位/角度、景别、主体位置与留白、光线、背景处理（整句） | 全量生效，是该轮场景与光线的第一来源；D1 的默认光线/背景让位给 B1 |
| 第 2 轮 细节 A | `panel_2` | **B2**：机位/景别/构图/景深 | 只控制视角；场景、光线、色温、明暗必须与锚点连续，机位不得复刻锚点 |
| 第 3 轮 细节 B | `panel_3` | 同上 | 同上 |
| 第 4 轮 素材 | `panel_4` | 同上 | 只控制视角；背景默认干净中性，模板写了背景也以不破坏锚点产品/图案为前提 |

**场景来源优先级**

第 1 轮（B1 + D1）：

| 优先级 | 来源 | 生效条件 |
| --- | --- | --- |
| 1 | 构图模板 `panel_1` 的光线/背景 | 用户显式写了 |
| 2 | `scene_mood` 批级场景基调 | `panel_1` 未写光线/背景 |
| 3 | `_STYLE_SCENES` 逐款轮换 | `scene_mood` 也为空 |
| 恒定叠加 | D1 的光线质量硬规则（必须有方向光、画面内可见光源、接触阴影、浅景深） | 始终生效；构图模板只能指定光源类型/方向，不能取消「必须有方向光」 |

第 2/3/4 轮（B2）：

| 优先级 | 来源 | 生效条件 |
| --- | --- | --- |
| 1 | 锚点的场景、光线、色温、明暗 | 始终生效（一致性硬约束；景深随模板方向变化） |
| 2 | 构图模板 `panel_2/3/4` 的机位/景别/构图留白 | 只允许改变视角，不允许换场景 |
| 3 | 素材轮的干净中性背景 | `panel_4` 未写背景时 |

三条硬规则：

1. **同一轮四格共用该 slot**：机位、景别、构图留白一致，四格只靠图案区分。
2. **第 1 轮全量、派生轮只取视角**：B1 吃整句；B2 只吃「怎么换机位」，场景与光线必须跟锚点。
3. **冲突处理**：产品结构/图案一致性以锚点与结构事实表为准；第 1 轮的光线/背景以 `panel_1` 为准；派生轮以锚点为准。

**实现方式（不改存储结构）**：每个 slot 仍是一句话（`zh` + `en`），整句注入 B 段；派生轮在 B2 追加一句「该句只用于机位、景别与构图留白，背景与光线必须与参考图一致」。不做字符串切分，避免解析脆弱。若以后需要对美术指导做更细的控制，再把 slot 扩成 `camera / shot / framing / lighting / background / art_direction` 结构化字段。

### 5.3 构图生成提示词 v5

`composition_runtime.py` 的 `PROMPT_VERSION` 从 `pod-composition-v4` 升到 `pod-composition-v5`（`composition_runtime.py:51`）：

- `task` 从「四张商品图」改为「四轮生成、每轮四款同屏」。
- `panel_contract` 的引导语改为角色轮次口径。
- 新增硬规则：
  - 同一轮四格机位必须一致，差异只来自图案；
  - 只描述怎么拍，不描述拍什么；
  - 每轮一句话，句式仍是「机位与角度 + 景别 + 构图/主体位置与留白 + 光线 + 背景处理」；
  - 每轮的一句话会被注入对应轮次的 B 段：第 1 轮整句注入 B1（同时约束 D1 的光线/背景），第 2/3/4 轮只取机位/景别/构图留白注入 B2。
- `_slot_guidance` 按角色重写（主图轮 / 细节轮 / 素材轮）。

### 5.4 默认构图模板改写

全定制的系统默认模板已落地为「四轮差异化机位」（`DEFAULT_COLUMN_COMPOSITION_PANELS`，`layout=role_v1`）：

- 主图轮：略低机位 45° 斜侧中全景，主体占画面约 70%，三分点 + 大面积留白；
- 细节 A：正上方 90° 俯拍，主体完整、占画面约 90%，四边近贴边、无台面与衬托物；
- 细节 B：贴近表面极端微距，只保留约 1/10 面积的局部纹理，主体轮廓与背景全部出画；
- 素材轮：正上方垂直俯拍，主体居中占约 60%，四边等距留白、纯净中性背景。

关键是把「占画比例 / 边缘是否出画」写成可判定的量化约束——只写「俯拍 / 微距」时模型会收敛成同一张。
复刻/裂变仍使用旧的 `DEFAULT_COMPOSITION_PANELS`（四格语义），不能用四轮文案污染。
平铺产品仍强制四轮全俯拍。

### 5.5 与旧模板的关系

- 旧模板记录 `layout='panel_v1'`，新模板记录 `layout='role_v1'`；仅用于归因，不影响可用性。
- 旧模板可直接参与 column 模式；不静默改写用户文本。
- 新页面首次打开旧模板时给一条提示：「构图模板已按四轮生效，每一轮的四款共用该轮机位，可继续编辑」。

### 5.6 D1 美术指导当时是怎么写的

D1 不是 LLM 生成的，是后端**硬编码的 SET DESIGN 块**，在 `build_style_listing_prompt` 里按款拼装（`prompts.py:850-869`）：

| 组成 | 作用 | 位置 |
| --- | --- | --- |
| `_PART4_HEADER` | 声明美术指导的目标：真实面料工艺 + 场景美感，美感来自 PART 1 主题 + 本款 PART 3 元素 | `prompts.py:409-412` |
| `_set_design_block` | 场景主体规则：必须是有人味的实景，不能是空棚/空地；3-5 个同题材道具分近/中/远三层；暖向主光 + 接触阴影；画面内必须有可见暖光源；浅景深；产品为 hero | `prompts.py:677-722` |
| `_flat_set_design_block` | 平铺产品的版本：只能俯拍平面，不许房间/桌沿/家具/垂坠；1-3 个平面道具 | `prompts.py:660-674` |
| `_COLOR_IMPACT_BLOCK` | 色彩通透、在色板内拉满饱和与亮度、暖白平衡，禁止灰暗 | `prompts.py:334-350` |
| `_MATERIAL_REALISM_BLOCK` | 按模板真实材质渲染，禁止喷漆感/通用外壳 | `prompts.py:356+` |
| `_fabric_craft_lines` | 把批级面料工艺词转成渲染要求 | `prompts.py:380-392` |
| `scene_mood` | 批级场景基调，非空时覆盖逐款场景 | `prompts.py:854` |

**column 模式下必须改写的三句**（它们现在都是「一款四格」口径）：

1. `The scene must change from style to style inside this batch` → 改成「**同一轮四格共用一套场景与光位；跨轮由构图 slot 换场景；跨组在模板未写场景时按组轮换兜底**」。
2. `Panels 1-3 are ONE shoot ... material panel (bottom-right) neutral` → 改成「**第 1 轮四格同一次拍摄；素材轮按 `panel_4`，未写背景时中性**」。
3. `Fill the frame with 3-5 props ... colours from this style's palette` → 改成「**一轮四格共用一套道具布置，道具 5-8 件、近/中/远分层、逐件写材质与颜色；配色按各款 palette 微调，禁止四格做成四套布景**」。

保留不变：方向光、画面内可见光源、接触阴影、浅景深、材质真实、色彩通透、产品为 hero。D1 只在第 1 轮渲染；第 2/3/4 轮不渲染 D1，质感由 A2 的锚点一致性承接。

### 5.7 批级美术指导：随模板图智能生成，整批共用

**做法**：在现有「智能填写」环节把当前模板的主图一起送给豆包视觉模型，顺手产出批级美术指导，存进 L1，整批（所有 L2、所有轮次）共用。

**输入**

- 现有 brief + `fabric_craft` / `scene_mood`；
- 模板主图：默认取当前选中模板的 `primary`，未选模板时允许手动上传一张；
- 模板图只用于读**产品材质、形态、结构、可印区域**；禁止复制模板上的图案、品牌、背景、道具。

**输出**（结构化 JSON + 中文可读，供用户确认/编辑）

```json
{
  "scene": "清晨的木质厨房岛台，右侧一扇半开的百叶窗把光切成条状斜切过台面",
  "lighting": "低角度暖金色侧逆光从右后方打来，在台面拉出长投影；左侧用白色反光板补出通透暗部",
  "color_temperature": "主体偏暖约 3400K，窗外冷蓝约 5600K 形成冷暖对比",
  "mood": "刚起床的安静厨房，有生活痕迹但不凌乱",
  "palette": ["暖胡桃木棕", "奶油白", "鼠尾草绿", "黄铜金"],
  "props": [
    "近景：釉面陶瓷杯，杯口淡金描边，杯身有轻微使用痕迹",
    "近景：虚化的亚麻布角，只留织物纹理与暖色反光",
    "中景：两块折叠亚麻餐巾，米白与鼠尾草绿叠放",
    "中景：厚切木质砧板，边缘有刀痕与清晰木纹",
    "远景：黄铜壶里一束尤加利叶，叶片投影落在台面",
    "点缀：散落的咖啡豆与一小碟黄油，形成暖色高光点"
  ],
  "background": "背景压暗、f/2.0 浅景深；远处橱柜只留木色与黄铜把手的模糊轮廓",
  "composition_hint": "产品放在右下三分点，左上留出窗光与蒸汽的空间；前景留一片虚化亚麻布",
  "avoid": ["冷白棚拍", "纯灰背景", "道具遮挡产品", "与印花重复的立体元素"]
}
```

**画面冲击力硬要求**（写进生成提示词，缺一项就判不合格）

1. **写具体光源**：光源类型、方向、色温、投影方向必须写死；禁止「柔和光线」「自然光」这类空话。
2. **至少两种对比**：冷暖对比、明暗对比、材质对比（木/金属/织物/陶瓷）里至少写两种。
3. **三层空间**：前景（虚化）、中景（产品与主道具）、远景（压暗/虚化）必须齐；至少一个前景虚化元素。
4. **道具 5-8 件**：按近/中/远分层，逐件写材质与颜色；至少两种材质；不得遮挡产品，不得把印花元素做成 3D 道具。
5. **主体定位**：写明产品在画面的位置（三分点/居中）与留白方向，避免四格都堆满。
6. **禁止空词**：「干净」「简约」「高级感」「氛围感」这类没有画面信息的词一律不写。

**存储与共用**

- 写入 L1 `art_direction_json`，建批时冻结；同一 L1 下所有 L2 组、全部四轮共用。
- 第 1 轮注入 D1；第 2/3/4 轮不重复注入，场景与光线由锚点继承。

**与构图模板的优先级（第 1 轮）**

| 优先级 | 来源 |
| --- | --- |
| 1 | 构图模板 `panel_1` 的机位/景别/构图 |
| 2 | 构图模板 `panel_1` 显式写的光线/背景 |
| 3 | 批级 `art_direction` 的 scene / lighting / color_temperature / mood / props |
| 4 | `scene_mood` |
| 5 | `_STYLE_SCENES` 逐款/逐组轮换 |
| 恒定叠加 | D1 光线质量硬规则（方向光、可见光源、接触阴影、浅景深） |

**生成方式**

- 复用复刻识图的 Ark 视觉 lane（`replica_analysis_runtime.py:395-405` 的 `image_url` 模式，模型 `doubao-seed-2-1-lite-260915`），一次调用。
- 计费沿用 brief 的纯 title 口径；失败不阻塞，回退现有硬编码 D1。
- prompt 必须写死：模板图只用于读产品材质/形态；场景、光影、冷暖是「为这个产品设计」的结果，不是从模板图背景里抄；不得复制模板图案、品牌、水印。

**注意**：模板图大多是白底产品图，本身没有场景可读。视觉调用主要价值是读材质与形态，再结合 brief 设计场景与冷暖；不要期待它从白底图里「看出」一个现成场景。

**交互**

- 智能填写区显示「已附带模板主图」；
- 生成后出现「美术指导（整批共用）」卡片，可编辑、可重生成、可清空；
- 随草稿保存，建批时提交 `art_direction`。

---

## 6. 调度、失败与重跑

### 6.1 调度

新增 `_process_style_column_streaming`（legacy 的 `_process_style_grids_streaming` 原样保留）：

```
for 每个 L2 group（受 image_workers 约束）：
    投第 1 轮 primary sheet
    返回 → 拆 4 格 → 校验 → 落库 variant 1 × 4 款 → 投该组 detail_a / detail_b / hero
    detail_a / detail_b / hero 三路并行
    每张返回 → 拆 4 格 → 落库对应 variant × 4 款
```

- 组间流水线：第 g 组进入派生轮时，第 g+1 组可同时跑主图轮。
- 后处理走独立池（`POD_POSTPROCESS_WORKERS=3`），拆格/发布/落库不占生图槽位。
- 进度沿用款级计数；面板按「4 款一批」到达，前端字段与 1.5s 轮询不变。
- 每张 sheet 完成 `touch_batch_progress`。

### 6.2 失败

- 粒度 = 「组 × 角色」（一张 sheet），attempt 1/2。
- **逐格验收**：拆出 4 格后逐格过质量门（非空、非纯色、尺寸合格），不能只看「拆出 4 格」。现有 `_validate_style_grid` 只校验数量（`worker.py:1909-1916`），这里要补上逐格检查。
- **第 1 轮全有或全无**：任一格无效即整组派生轮不启动，4 款标记 failed；v1 不做补偿重跑，避免交付「三缺一」。
- **派生轮 barrier**：一个组的第 1 轮必须 4 格全部有效、且 4 个公网 URL 已发布，才开派生轮。
- 第 1 轮整组重试两次后仍无有效主图 → 该组 4 款的 4 格全部 failed。
- 第 2/3/4 轮失败两次 → 只把该角色对应的 4 个款的行置 failed，其他角色继续交付。
- 新增 `repository.fail_style_variant(batch_id, style_indices, variant_index, error)`；现有 `fail_style_grid` 只用于第 1 轮全失败。

### 6.3 失败与重跑（v1 不做用户重跑）

- **批次内自动重试保留**：单张 sheet 的 provider/校验失败按现有 attempt 1/2 自动再试一次。
- 两次都失败 → 按角色标记 failed；v1 **不提供用户手动重跑**。
- **column 批次禁用现有单款重生成 / 批量重试失败项**：现有 `claim_style_regeneration` 要求 4 行都是 failed/completed 并把 4 行一起重置（`repository.py:4594-4602`），在新架构下会把已完成的图一起冲掉。v1 在前端隐藏这两个入口，后端对 column 批次返回 409，避免误用。
- **续跑冻结仍要改**：`_freeze_paused_batch_remainder` 现在按「款 × 2 次尝试」冻结，column 模式要按「款 × 缺失角色 × 2 次尝试」冻结，否则会把已完成角色的调用重复计费。
- 角色级重跑（单面板直出、只重置缺失 variant、按缺失角色冻结）列下一期，方案保留但不进本期实现。

### 6.4 并发与吞吐

**现状容量**

- 生图 provider 并发 = `image_workers` = 8（`wh_local/app/main.py:695`；`ai_runtime.py:76-85` 把 `provider_concurrency` 设为同一个值）。
- 批处理并发 = `batch_workers` = 2，两个批次共享同一个运行时和同一个 8 路信号量。
- 豆包同步 2K 单次耗时按 60–180s 估（`ARK_RESULT_TIMEOUT_SECONDS=180`）。

**结论：8 个二级批次并发可以，但不是 32 路同时生图**

- 8 个 L2 只是 8 个逻辑状态机；真正在飞的 provider 调用仍受全局信号量 8 限制。
- 每个 L2 的 4 次调用有依赖（第 1 轮 → 第 2/3/4 轮），所以「8 组并发」的实际形态是：8 个组交替占用 8 个槽。

**建议参数**

| 参数 | 建议 | 理由 |
| --- | --- | --- |
| provider 并发 | **保持 8，不提高** | 这是现网已验证的上限；32 路同时打豆包会触发 429/超时，收益为零 |
| 活跃 L2 组上限 | **8**（= provider 并发） | 初始填满窗口；组完成后才放下一组 |
| 组内派生并发 | **最多 3**（第 2/3/4 轮同时） | 三路互不依赖 |
| 派生轮优先级 | **高于新组第 1 轮** | 避免 8 个槽全被主图轮占住、派生轮排队 |
| 组间公平 | 派生轮按组轮询 | 防止某一组长期占槽 |

**调度规则**

1. 启动：依次放行第 1 轮，直到全局 8 个槽填满（最多 8 组）。
2. 某组第 1 轮完成 → 其 3 个派生调用**优先于**尚未放行的新组第 1 轮。
3. 某组 4 轮全部结束 → 释放活跃组名额，放行下一组。
4. 同一组内派生调用按 `detail_a → detail_b → hero` 轮询，避免素材轮被饿死。
5. 多批次公平：`batch_workers=2` 时两批共享 8 路，建议按批次轮询放行，单批最多 4 组，避免一个批次饿死另一个。
6. 心跳：`POD_PROGRESS_TIMEOUT_SECONDS=900` 大于单次生图 180s，不会被误判；但每张 sheet 开始/结束都要 `touch_batch_progress`。
7. 遇到 429 / 可重试错误按现有 attempt 1/2 处理；首版不做动态降并发，先观察。

**耗时估算（按单次 90s 估）**

| 款数 | 组数 | 当前（1 款 1 次） | 新方案 | 说明 |
| --- | --- | --- | --- | --- |
| 4 | 1 | ~1 波 ≈ 1.5–3 min | ~2 波 ≈ 3–6 min | 小组延迟约翻倍（依赖导致） |
| 8 | 2 | ~1 波 ≈ 1.5–3 min | ~2 波 ≈ 3–6 min | 同上 |
| 20 | 5 | ~3 波 | ~3–4 波 | 接近持平 |
| 100 | 25 | ~13 波 | ~13 波 | 持平 |

**两个必须接受的取舍**

1. 小批次（4/8 款）延迟约翻倍：第 1 轮不出完不能开派生轮，这是像素锚换来的代价。
2. 8 组并发时单组完成时间不再均匀：先完成的组先出图，UI 按款推进即可。

**不要做的事**

- 不要把 provider 并发直接提到 32：豆包同步 2K 在高并发下更容易超时，429 会拖慢整体。
- 不要让单个组一次占满 8 个槽（例如同时发 4 轮），否则其他组会被饿死。
- 不要把 `batch_workers` 调大来「提高并发」：它只增加批次间争抢，不增加 provider 容量。

---

## 7. 数据模型

新增迁移 `migrations/026_style_column_layout.sql`：

```sql
-- L1 批次：布局、组数、冻结元数据
ALTER TABLE pod_customization_batches ADD COLUMN generation_layout TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE pod_customization_batches ADD COLUMN group_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pod_customization_batches ADD COLUMN generation_meta_json TEXT NOT NULL DEFAULT '';
ALTER TABLE pod_customization_batches ADD COLUMN art_direction_json TEXT NOT NULL DEFAULT '';

-- L2 二级批次
CREATE TABLE pod_customization_style_groups (
    group_id       TEXT PRIMARY KEY,
    batch_id       TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    owner_user_id  TEXT NOT NULL,
    group_index    INTEGER NOT NULL,
    style_indices_json TEXT NOT NULL,
    status         TEXT NOT NULL,          -- queued|generating_primary|generating_derived|completed|partial_failure|failed|cancelled
    current_round  TEXT NOT NULL DEFAULT '',
    execution_epoch INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE (batch_id, group_index)
);

-- L2 × 角色：每轮一张 sheet 的执行状态
CREATE TABLE pod_customization_group_sheets (
    sheet_id      TEXT PRIMARY KEY,
    group_id      TEXT NOT NULL,
    batch_id      TEXT NOT NULL,
    role          TEXT NOT NULL,           -- lifestyle|detail_a|detail_b|hero
    status        TEXT NOT NULL,           -- queued|generating|completed|failed
    grid_asset_id TEXT NOT NULL DEFAULT '',
    attempt       INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    prompt_snapshot TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (group_id, role)
);
CREATE INDEX idx_pod_group_sheets_batch ON pod_customization_group_sheets(batch_id, role);

-- 构图模板：新旧版标记（不改现有数据结构）
ALTER TABLE pod_compositions ADD COLUMN layout TEXT NOT NULL DEFAULT 'panel_v1';
```

- `generation_meta_json` 冻结：`{"reference_view_roles": [...], "style_groups": [[1,2,3,4],...], "prompt_version": "v19", "image_model": "..."}`。**生图模型必须在建批时冻结**，否则运行中切到速创会让派生轮没有锚点能力。
- `group_sheets.prompt_snapshot` 在首次执行时写入该轮实际使用的完整提示词，用于断点续跑与事后归因；否则代码升级后续跑会用新提示词重画，破坏一致性。
- 资产引用护栏要加一条：`_ASSET_IN_USE_PREDICATES`（`repository.py:3079-3091`）必须包含 `pod_customization_group_sheets.grid_asset_id`，否则缓存清扫会把母图当孤儿删掉。删除批次时也要级联删除 `style_groups` / `group_sheets`。
- 四格面板仍写 `pod_customization_style_grid_results`，**前端与导出链路零改动**。
- 新资产 kind：`direct_listing_sheet`（母图）；面板仍为 `direct_listing_panel`。
- 迁移需同步三处：SQL 文件、`wh_local/pod_migrations.py` 的 `POD_MIGRATION_CONTRACTS`、`local-runtime/tests/test_module_migration_registry.py`（当前清单只到 024，需补 025/026）。

---

## 8. 计费：按二级批次（4 款）上报

用户决策：不再按 provider 调用逐条上报，改为**一个二级批次报一次「生成四款」**，形态对齐半定制的「一组 = 1 link」。

- **沿用现有画像 `pod_random_v1`，不新增画像、不改单价配置**；按组上报，把 4 款报进去：`link_count = group_count`，`item_count = style_count`（即 `item_count = link_count × 4`）。
- 冻结 payload 复用半定制的组形态（`billing_contract.py:609-626`）：

```json
{
  "idempotency_key": "pod:batch:{batch}:initial",
  "link_count": 5,
  "item_count": 20,
  "scope": ["four_grid", "title"],
  "billing_profile": "pod_random_v1"
}
```

- 内部仍逐调用记账（幂等、断点续跑、审计），但远端只按组汇总：
  - 每 L2：4 个角色 × 2 次尝试 = 8 个 `pod.image`，id `{batch}:group:{g}:role:{role}:image:{attempt}`；
  - 每款：5 次标题尝试，id `{batch}:group:{g}:style:{i}:title:{attempt}`；
  - `_product_batch_groups()` 需要支持 `:group:{g}:` 前缀（现有只解析 `:style:{n}:`，`billing_contract.py:723-734`）。
- 结算（每组一条 link，两个 subitem）：
  - `four_grid`：该组第 1 轮的 4 个主图都有效（主图角色 success）即 `success`，否则 `no_return`；**细节 A / 细节 B / 素材失败不影响计费**。
  - `title`：该组 4 个款都有至少一次标题 success 才 `success`，否则 `no_return`。
- 幂等与资产：母图写 `group_sheets.grid_asset_id`，同时把同一个 `grid_asset_id` 写进该组 8 条 `generation_calls`，续跑不重调、缓存清扫不误删。
- `initial_call_count` = `group_count × 8 + style_count × 5`。
- 暂停续跑只对未完成组重新冻结；已完成组不再产生调用。

> 联调时必须确认：一组 4 款的冻结/结算总积分 = 4 × 现有单款价格；如果远端只看 `link_count` 不看 `item_count`，需要在那里补一条「按 item_count 计价」的规则。

---

## 9. 前端

| 改动 | 位置 |
| --- | --- |
| 预设档位改为 4 的倍数 `[4,8,20,40,60,100]` | `data/podCustomizationModel.ts:30` |
| 移除「自定义款数」按钮与输入框；校验 4 的倍数 | `pages/PodCustomizationPage.tsx:1207-1216`、`:649-653` |
| 建批 payload 增加 `generation_layout: "column_v1"` | `pages/PodCustomizationPage.tsx:719-726`、`types/index.ts:351-359` |
| 构图模板文案改为「四轮每轮四款共用」 | 构图抽屉组件 + `podCustomization.css` |
| 智能填写区附带当前模板主图，生成并展示「批级美术指导」卡片（可编辑/重生成/清空） | `components/PodBriefInput.tsx`、`pages/PodCustomizationPage.tsx` |
| 草稿与建批 payload 增加 `art_direction` | `data/podCustomizationDraft.ts`、`types/index.ts` |
| 生成中提示补「本批按 4 款一组生成，主图完成后并行生成其余视角」 | `components/PodBatchGallery.tsx:156-166` |
| 进度按「面板/轮次」展示：显示「主图 4/4 · 细节 A 3/4 · …」，避免款级结算导致进度条长时间 0% | `data/podCustomizationModel.ts:377-409`、`components/PodBatchGallery.tsx:156` |
| column 批次隐藏「单款重生成 / 批量重试失败项」入口（v1 不做用户重跑） | `components/PodBatchGallery.tsx:159-166`、`pages/PodCustomizationPage.tsx:1024-1039` |
| 结果表格与四列语义**不变**；重跑入口对 column 批次隐藏 | `components/PodBatchGallery.tsx:157-183` |

L2 默认不单独暴露 UI；如需排查，可在 `get_batch` 响应里附带 `groups` 摘要（可选，不影响现有字段）。

---

## 10. 兼容与降级

| 场景 | 处理 |
| --- | --- |
| 升级前创建的批次 | `generation_layout='legacy'`，续跑/重试走旧路径 |
| 旧客户端建批（不传 layout） | 默认 legacy，count 仍允许 1–200 |
| 当前生图模型是速创 | 不特殊处理：不拦截、不降级，按通用多参考图路径下发，效果不保证；后续再处理 |
| 模板没有结构视图 | 允许；第 1 轮退化为单主图，派生轮锚点逻辑不变 |
| 结构事实表为空 | 允许；对应提示词块不渲染 |
| 旧构图模板 | 直接可用，不迁移、不改写 |

---

## 11. 改动清单

**后端（新增）**

- `migrations/026_style_column_layout.sql`
- `column_generation.py`（组划分、轮次状态机、象限映射）
- `column_prompts.py`（四轮提示词 + 单面板重跑提示词）
- `tests/test_column_generation.py`、`tests/test_column_prompts.py`、`tests/test_billing_column.py`

**后端（修改）**

- `contracts.py`：`BatchCreate.generation_layout` 与 `art_direction`；column 模式 count 校验；构图 `layout`；`BriefFieldRequest.template_asset_id`；`BriefFieldResponse.art_direction`
- `service.py`：建批校验模型能力/款数；创建 L2 组与 sheet；`_freeze_batch` 分流 `for_column_batch`；续跑按缺失角色冻结；`generate_brief_fields` 附带模板图并产出美术指导
- `repository.py`：批次 layout/group_count/meta/art_direction；L2 组与 sheet 的增删查改；`fail_style_variant`
- `worker.py`：`_process_style_column_streaming`（活跃组上限 + 派生轮优先调度）；`_style_generation_inputs` 支持 role；`_image_call_id` 支持 role；按组落库与 fan-out
- `billing_contract.py`：`for_column_batch`（按组）、组级冻结/结算（沿用 `pod_random_v1`）
- `brief_runtime.py`：视觉版 brief（带模板图）→ `art_direction`，失败回退文本版
- `composition_runtime.py`：v5 提示词与 slot 引导
- `prompts.py`：`PATTERN_PROMPT_VERSION → v19`；内置硬规则增加 column 版本；默认构图增加 role 版本
- `wh_local/pod_migrations.py`、`local-runtime/tests/test_module_migration_registry.py`

**前端（修改）**

- `data/podCustomizationModel.ts`、`data/podCustomizationDraft.ts`
- `pages/PodCustomizationPage.tsx`、`components/PodBriefInput.tsx`、`types/index.ts`
- 构图抽屉组件与 `styles/podCustomization.css`
- 测试：`podCustomizationModel.test.ts`、`PodCustomizationPage.test.ts`

**明确不改**

- 复刻 / 裂变 / 半定制链路
- 四格拆图与交付尺寸策略
- 前端结果表格结构与导出链路
- 远端计费价格与 link 口径

---

## 12. 决策记录

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| D1 | 两级批次：L1 定款数/风格，L2 固定 4 款、内含 4 轮调用 | 已确认 |
| D2 | 款数强制 4 的倍数；前端只给预设档位 | 已确认 |
| D3 | 锚点 = 第 1 轮拆出的 4 张主图，顺序对应象限 | 已确认 |
| D4 | 第 2/3/4 轮只依赖第 1 轮，三路并行 | 已确认 |
| D5 | 第 1 轮沿用四段式（硬约束/构图/轮转引擎/美术指导） | 已确认 |
| D6 | 第 2/3/4 轮只有 A2 + B2：A2 写足硬约束与主体锚定，B2 只换视角 | 已确认 |
| D7 | 构图模板语义改为「四轮」；每个 slot 只注入该轮的 B 段（第 1 轮全量，第 2/3/4 轮只取视角）；旧模板直接兼容 | 已确认 |
| D8 | 构图生成提示词升 v5；POD 提示词升 v18 | 已确认 |
| D9 | 失败按「组 × 角色」隔离；款级完成需 4 格齐 | 已确认 |
| D10 | v1 不做用户重跑；保留批次内 attempt 1/2；角色级重跑下一期 | 已确认 |
| D11 | 计费按 L2 上报：1 组 = 1 link，沿用 `pod_random_v1`，`item_count` 报 4 款 | 已确认 |
| D12 | column 模式不为速创做特殊处理：不拦截、不降级，按通用多参考图路径下发，效果不保证 | 已确认 |
| D13 | 旧批次、旧客户端、旧重跑走 legacy | 已确认 |
| D14 | 锚点板仅作为实测失败后的 fallback | 已确认 |
| D15 | 智能填写附带模板主图，顺带生成批级美术指导；L1 冻结，整批共用，只注入第 1 轮 D1 | 已确认 |
| D16 | provider 并发保持 8；活跃 L2 组上限 8；派生轮优先于新组第 1 轮 | 已确认 |
| D17 | 逐格验收；第 1 轮全有或全无；派生轮以 4 格齐为 barrier；v1 不做补偿重跑 | 已确认 |
| D18 | 建批时冻结生图模型；每张 sheet 首次执行时冻结 prompt 快照 | 已确认 |
| D19 | 远端按组上报；内部逐调用记账；母图资产同时写 group_sheets 与 generation_calls | 已确认 |
| D20 | 组结算条件 = 主图成功即计费；细节/素材失败不影响计费 | 已确认 |
| D21 | 派生轮机位以构图模板为唯一来源：景深/机位/裁切必须随模板变化，禁止复刻锚点机位；外层只锁场景/光线/色温/明暗 | 已确认 |

**无未决项。**

---

## 13. 验证

**自动化**

```bash
cd /Users/Zhuanz/Desktop/MainPG/local-runtime
python -m pytest wh_local/modules/pod_customization/tests -q
python -m pytest tests -k pod -q
cd ../web-frontend && npm test && npm run verify
```

新增用例覆盖：

- L1 建批 → 生成 L2 组与 4 张 sheet；款数非 4 倍数被拒。
- 第 1 轮拆出的 4 格分别落到 4 个 `style_index` 的 `variant_index=1`。
- 派生轮参考图顺序 = 象限顺序，锚点读取自各款 `variant_index=1`。
- detail_b 失败不影响 detail_a/hero；款级状态 `partial_failure`。
- 断点续跑不重复调用已完成的 sheet。
- column 计费按组：4 款 = 1 个 link；冻结 payload `link_count=组数`、`item_count=款数`；结算每组一条 link、两个 subitem；联调确认一组总额 = 4 × 单款价。
- 第 2/3/4 轮参考图使用第 1 轮已发布的 `public_url`，不再走 base64。
- column 批次调用单款重生成 / 批量重试接口返回 409。
- 旧批次 legacy 结果与改动前一致。
- 构图 v5：同一轮四格机位一致、逐轮角色不变；旧模板文本可原样使用。
- 智能填写带模板图时返回 `art_direction`；无图或视觉调用失败时回退文本版，不阻塞建批。
- `art_direction` 随建批冻结；D1 优先级为 构图 slot > art_direction > `scene_mood` > `_STYLE_SCENES`。

**人工 A/B（上线前必做）**

- 固定困难件模板（透明亚克力 / 细结构）+ 8 款（2 组），legacy vs column 各跑一轮。
- 盲评：① 同一款四张是否同一件商品；② 图案位置/尺度是否一致；③ 四款之间是否串色串图。
- 验收线：①② 明显优于 legacy；③ 无明显串图。

---

## 14. 风险与边界

| 风险 | 缓解 |
| --- | --- |
| 4 张参考图与 4 个象限绑定不稳 | 显式绑定 + 固定顺序 + 每格写死本款 motif/palette 交叉校验；失败切锚点板 |
| 第 1 轮四款同屏串色/趋同 | `QUADRANT ISOLATION LOCK` + 每格独立配方；盲评设「串图率」 |
| 第 1 轮畸变被派生轮忠实继承 | 第 1 轮继续用模板多视图 + 结构事实表；后续加生成后校验 |
| 一张 sheet 失败连带 4 款同角色 | 角色级隔离；主图失败才整款失败；重跑只补缺失角色 |
| 构图模板语义变化让用户困惑 | UI 文案改为「每轮四款共用」；旧模板原样可用；不静默改写 |
| 计费与调用形状不一致 | 1 款 1 link；column 计划 8 次/款；结算条件首版不动；新增计费单测 |
| 关键路径变长（每组 2 轮） | 组间流水线 + 派生轮并行；窗口上限仍按 `image_workers` |
| 提示词过长稀释契约 | 四段分块、按轮只渲染本角色；结构块只在有事实表时出现 |
| 锚点绑定错位（第 2 张参考被用到左上格） | ANCHOR BINDING 位置绑定 + 每格写死本款 motif/palette；失败切锚点板；后续可加视觉校验 |
| 4 张锚点请求体积过大 | 直接复用已发布的 COS 公网 URL（首选）；不可访问时回退 768 JPEG data URL |
| 第 1 轮提示词同时含 4 套配方，长度显著变长 | 加提示词长度预算测试；配方块只渲染差异项，公共锁复用 |
| 大批次（100 款）共用同一套美术指导导致场景单调 | 保留批级共用；提供「同族场景按组轮换」开关，只换布景不换光影与冷暖 |

---

## 15. 分阶段落地

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P1 | 迁移 026 + L1/L2 数据模型 + 契约 + 计费计划 | 迁移/计费单测通过；legacy 不受影响 |
| P2 | `column_prompts.py` 四轮提示词 + 构图 v5 + 批级美术指导生成/注入 + v19 版本 | 提示词快照测试；人工检查四轮文本 |
| P3 | worker 列式调度 + 角色级失败 + 断点续跑 | 2 组样例端到端跑通 |
| P4 | 失败路径：逐格验收、角色失败标记、禁用用户重跑 | 失败用例通过；column 批次无重跑入口 |
| P5 | 前端款数档位、建批字段、构图文案、智能填写美术指导卡片 | 前端测试与 `npm run verify` |
| P6 | 困难件 A/B 与灰度 | 盲评达标后默认开启 `column_v1` |

# POD 全定制：撤销配色权重引擎，改为「主题前缀色板 + 模型自选」

状态：**已确认**（2026-10-27），待实施。代码未改动。
范围：仅全定制两条链路（列式 `column_v1` + legacy 四宫格）；半定制与裂变本期不动。
目标读者：POD 后端 / 提示词维护者。
目标基线：`local-runtime/wh_local/modules/pod_customization/` + `web-frontend/src/modules/pod_customization/`。

---

## 0. 一句话

删掉 60-30-10 面积化配色引擎（`assign_style_colors` 的角色赋值与逐款随机取色），把配色的**分组与权威**
交回「智能填写」的同一次豆包调用：一次输出两组颜色——`color_preferences`（主题强关联色，建议 5-6）
与 `accent_colors`（建议点缀色，建议 6-10）；本地只做词组化与去重，不判组、不校验数量/前缀、
不抽样、不分配 base/structural/highlight；生图时两组分别注入，软约束「每格主色必须来自主题组，
点缀色只能辅佐」。面积预算只保留元素 Tier 一套。

---

## 1. 现状核对

### 1.1 配色从哪里来、被谁改写

| 环节 | 现状 | 位置 |
| --- | --- | --- |
| 智能填写产出 | `color_preferences` 至少 6 个具体颜色名，建议 6-10；schema `minItems=6` + 代码校验 <6 触发契约修复 | [brief_runtime.py L255-L259](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L255-L259)、[L411-L415](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L411-L415)、[contracts.py L205-L206](../../../local-runtime/wh_local/modules/pod_customization/contracts.py#L205-L206) |
| 本地归一化 | `_brief_color_preferences` 只做 str→list、去空 | [prompts.py L262-L268](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L262-L268) |
| 本地「引擎」 | `assign_style_colors`：以 `batch_id` 为种子洗牌色板，逐款取三色并固定角色 | [prompts.py L226-L259](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L226-L259) |
| 全定制列式（v25 主链路） | 每款 `color_harmony = base≈X(60%) / structural≈Y(30%) / highlight≈Z(10%)` | [column_prompts.py L136-L170](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L136-L170)、[L285-L289](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L285-L289) |
| 全定制 legacy 四宫格 | 同上，且额外有「浅色 base 只能用于留白/背景」条款 | [prompts.py L821-L834](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L821-L834)、[L899-L906](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L899-L906) |
| 半定制 | 每格从色板里按 `offset*7` 轮换取两色作 `Color focus` | [prompts.py L997-L1038](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L997-L1038) |
| 全局色块（两条链路共用） | `_COLOR_IMPACT_BLOCK`：60% DOMINANT BASE 必须是产品本体色、必须是「batch theme's hero colour」；白壳+微弱图案 = FAILURE | [prompts.py L384-L400](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L384-L400) |
| 裂变（独立链路） | 自己的 `palette_vocabulary` + `assign_style_colors` + 固定 60-30-10 文案 | [fission_planner.py L364](../../../local-runtime/wh_local/modules/pod_customization/fission_planner.py#L364)、[fission_prompts.py L176](../../../local-runtime/wh_local/modules/pod_customization/fission_prompts.py#L176) |

### 1.2 现状暴露的问题

1. **模型已经给了色板，本地又随机覆盖一次**：色板本来是识图/文本模型对主题的判断结果，`assign_style_colors`
   再用 `batch_id` 洗牌重排，等于用一个与画面无关的随机数决定「哪一色是 60% 的底色」。
2. **面积口径冲突**：60% 的 base 要求覆盖大面积，而满印契约要求图案覆盖 ≥90%、只留细缝
   （[column_prompts.py L276-L284](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L276-L284)）。
   两个预算落在同一张可印表面上，不可能同时成立。
3. **「底色」一词三义**：产品空白本体 / 印花图案的地色 / 四格画面整体色调，被四个条款混用：
   `identical … base color`（[column_prompts.py L227-L228](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L227-L228)，
   要求四格同色）、每款 `base≈` 各不同、全局块要求「batch hero colour」单色、
   `QUADRANT ISOLATION LOCK` 要求每格 palette 独占（[L78-L84](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L78-L84)）。
4. **强制 ≥6 色会逼模型凑色**：与元素池「宁少勿滥」的口径相反；模型为了过契约会补相近色。
5. **主次判断被写死成固定比例**：60/30/10 是外来评审的经验值，不适用于所有主题/品类；
   模型对「哪一色该当主角」本来就有画面级判断能力。

---

## 2. 目标与非目标

**目标**

| 编号 | 目标 |
| --- | --- |
| G1 | 删除面积化 60-30-10：不再按比例分配 base / structural / highlight |
| G2 | 只保留元素 Tier 一套面积预算；配色改为不绑比例的「颜色层级（主 / 辅 / 点缀）」表达 |
| G3 | 色板由模型产出，命名对齐元素口径：**颜色名带主题前缀**，禁止抽象色词；**不强制数量** |
| G4 | 本地只做词组化 / 去重，不随机抽取、不分配角色、不计算覆盖率；整份色板交给模型 |
| G5 | 消除「底色」歧义，修正四格同色与逐格异色的直接冲突 |
| G6 | 提示词版本递增，历史批次冻结快照不回填 |

**非目标**

- 不改元素 Tier 引擎（`assign_style_elements`、`element_area_shares`、满印密度契约）。
- 不改美术指导 `art_direction.palette`（那是**场景**配色，与印花色板是两回事）。
- 不改生图链路、参考图策略与计费。

---

## 3. 已确认决策（用户，2026-10-27）

| 编号 | 决策 |
| --- | --- |
| D1 | 放弃面积化 60-30-10，改为「颜色层级」不绑比例 |
| D2 | 只保留元素面积份额一套预算，把面积口径的冲突消除 |
| D3 | 限制识图模型输出：颜色要像元素一样带主题前缀 |
| D4 | 不强制要求输出指定数量的颜色 |
| D5 | 本地词组化后不随机抽取、不给覆盖率，全部交给模型自由发挥 |
| D6 | **本期只改全定制**（列式 + legacy）；半定制与裂变不动 |
| D7 | **不设色数上限**（也不设下限），只由提示词建议 6-10 |
| D8 | 主题前缀取**「主题词 + 颜色」**（如「圣诞红」），不是「主题意象 + 颜色」 |
| D9 | **保留**「产品与印花必须是画面最强色彩陈述」这句（不再绑定产品本体色） |
| D10 | 色板分**两个字段**：`color_preferences` = 主题强关联色，新增 `accent_colors` = 建议点缀色 |
| D11 | 两组的划分由**同一次 brief 调用直接输出**；本地只做提示词软限制，**不做任何硬编码判组 / 数量 / 前缀校验** |
| D12 | 生成软约束：每格**主色必须来自主题组（至少用 1 款）**，点缀色只能当辅色/点缀，相邻象限主色不同 |

---

## 4. 方案

### 4.1 智能填写（brief）：一次调用输出两组颜色

**数据形状**（D10）

| 字段 | 语义 | 建议数量（软） | 命名 |
| --- | --- | --- | --- |
| `color_preferences`（沿用） | 主题强关联色 | 5-6 | 必须「主题词 + 颜色」 |
| `accent_colors`（新增） | 建议点缀色 | 6-10，可更多 | 具体颜色名即可；金属 / 中性 / 对比色都允许，不要求主题前缀 |

两组由**同一次 brief 调用**一起产出（D11）：不加第二次 AI 调用，也不在本地做任何分组判断。

**schema**（[brief_runtime.py L255-L259](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L255-L259)）

- `color_preferences` 去掉 `minItems`，改为可为空数组（键仍在 `required` 内，允许返回 `[]`）；
- 新增 `accent_colors`：`{"type": "array", "items": {"type": "string", "minLength": 1}}`，同样不设
  `minItems` / `maxItems`，加入 `required`（允许空数组）；
- 不设上下限（D7）；沿用全局 `BRIEF_LIST_MAX_ITEMS=200` 与单条 120 字防御上限。

**校验**（[L411-L415](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L411-L415)、[L199-L214](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L199-L214)）

- 删除「至少 `BRIEF_COLOR_PREFERENCES_MIN_ITEMS` 个」的抛错分支；
- `_LIST_FIELDS` 增加 `accent_colors`，复用现有 `_normalized_items`（去空、去重、超长拦截）；
- **不加**任何「颜色必须带前缀」「主题色不得少于 N」「点缀色不得多于 M」的代码校验（D11）。

**提示词**（[L783-L785](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L783-L785) 与 [L816-L821](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L816-L821)）

`field_contract` 拆成两条：

```text
color_preferences：主题强关联色，5-6 个（可略少）。必须「主题词 + 颜色」，即先取 design_theme 的
  核心主题词，后接具体颜色名，例如主题「圣诞」→「圣诞红」「圣诞墨绿」「圣诞暖金」；
  同一主题下共享前缀是预期行为。这些颜色必须有直接的主题依据，不得拿与主题无关的颜色充数。

accent_colors：建议点缀色，6-10 个（可多可少）。写具体颜色名即可，可以是金属色、中性色、
  与主题形成对比的跳色；不要求主题前缀。它们只用于点缀与辅助，不得替代主题强关联色当主色。
```

`color_preferences_recipe` 同步改写为上面两段（保留「禁止抽象描述：高饱和度、撞色、冷色调、
高级灰、莫兰迪色、复古色系」的约束）；`accent_colors_recipe` 只补一句「不要为了凑数重复相近色」。

**本地处理边界**（D11）

- 只做 `str→list`、去空、去重、超长拦截；不判组、不调换、不抽样、不补前缀；
- 两组顺序在提示词里保持模型输出顺序，便于生图模型判断强弱；
- 旧批次没有 `accent_colors` → 默认 `[]`，`color_preferences` 仍按「主题强关联色」渲染，不炸。

**版本**：`pod-brief-v6 → pod-brief-v7`；`ART_DIRECTION_PROMPT_VERSION` 不动。

### 4.2 全定制生成：删除配色引擎

- `build_column_style_recipes` 不再调用 `assign_style_colors`；`ColumnStyleRecipe` 删除 `color_harmony`
  字段（[column_prompts.py L105-L126](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L105-L126)、[L136-L170](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L136-L170)）。
- 两组色板整批共用，**只在 PART 1 渲染一次**；每款配方只给本象限的用色约定，避免四份重复：

  ```text
  # PART 1（批级，渲染一次）
  Theme colours (strong theme tie; must use at least one): <color_preferences>
  Accent colours (supporting only; never the dominant): <accent_colors>

  # PART 3（每象限）
  - 用色层级（本象限）：主色必须取自 PART 1 的主题强关联色（至少用 1 款），辅色 / 点缀色可取自点缀组；
    不要求固定比例，但主次必须一眼读得出来；不得把所有颜色平均铺开；
    相邻象限的主色必须取自不同的主题强关联色。
  ```

- legacy 路径（`build_style_listing_prompt`）用同样的两行替换现有的 `palette = …` 与 60-30-10 行
  （[prompts.py L821-L834](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L821-L834)、[L899-L906](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L899-L906)）。
- `build_style_listing_prompt` 的 `seed` 形参在删掉配色分配后只服务元素分配，保留不动。
- 两组颜色的选择、主色归属、点缀使用全部由生图模型判断；代码只负责把两个数组原样渲染进提示词，
  不做「第 1 个当主色」之类的隐式约定（D11）。

### 4.3 全局色块：从「比例 + 本体色」改为「层级 + 明度分离」

`_COLOR_IMPACT_BLOCK`（[prompts.py L384-L400](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L384-L400)）改动：

| 现行句子 | 处理 |
| --- | --- |
| `Max out saturation and luminosity inside the palette …` | **保留**（修复灰暗偏色，与比例无关） |
| `Build the PRODUCT BODY itself on the palette's strongest … A pale, white or neutral product shell with only faint motifs is a FAILURE …` | **删除**「产品本体必须是最饱和色」与「浅色不得当本体」；改为「画面必须有明确的色彩主角，产品或其印花要与背景形成价值/饱和度差」（见下） |
| `TONAL SEPARATION …` | **保留** |
| `60-30-10 COLOR HARMONY (fixed ratio contract) …` | **整段删除**，替换为无比例版本 |

替换后的两句草案：

```text
- COLOR HIERARCHY (no fixed ratio): pick one dominant colour statement for this quadrant, one or two
  supporting colours and one accent from the batch palette. The hierarchy must read at a glance, but no
  percentage split is prescribed. Spreading every palette colour evenly, with no clear lead, is a FAILURE.
- COLOUR AUTHORITY: the product and its print must be the boldest colour statement in frame; a washed-out
  or grey-beige result is a FAILURE. Put the palette's strongest colours on the product/print, and keep
  pale or neutral tones for negative space, lining or the scene.
```

保留后半句的「浅色用于留白/内衬/场景」，是因为它解决的是「白包白底、画面发灰」，与比例无关；
但不再断言「产品本体色必须是最饱和色」，从而不再与「四格同底色」冲突。
`COLOUR AUTHORITY` 整句**确认保留**（D9），只删掉它上面的「本体必须是色板最强色」绑定。

### 4.4 修正「底色」三义

| 位置 | 现状 | 改为 |
| --- | --- | --- |
| PART 1 一致性锁（[column_prompts.py L227-L228](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L227-L228)） | `identical … base color and print surfaces` | `identical … blank substrate colour and print surfaces`；并补一句「打印配色可按象限不同」 |
| 全局色块 | 产品本体 = batch hero colour（单数） | 删除该断言（见 §4.3） |
| 每款配方 | `base≈X(60%)` 与四格同底色冲突 | 删除角色赋值；四格差异改由「相邻象限主色必须取自不同主题强关联色」承担 |

同时保留既有 `QUADRANT ISOLATION LOCK` 的 palette 独占句，与新差异句一起构成四格色彩差异化约束。

### 4.5 半定制（本期不改，D6）

`semi` 每格的 `Color focus` 两色轮换（[prompts.py L1019-L1038](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L1019-L1038)）
**保持现状**。已知代价：半定制仍按 `offset*7` 本地取两色，与改造后的全定制口径不一致；
这是本期明确接受的范围取舍，不在全定制改造里顺手改。

### 4.6 裂变（本期不改，D6）

`fission_planner` 的 `palette_vocabulary` 与 `fission_prompts` 的 60-30-10 文案**保持现状**。
`_fingerprint` 依赖 palette 三元组（[fission_planner.py L364-L382](../../../local-runtime/wh_local/modules/pod_customization/fission_planner.py#L364-L382)），
同步改造会牵动去重与指纹，风险大于收益。
因此 `assign_style_colors` **保留**，仅裂变继续使用；全定制两条链路不再调用它。

### 4.7 版本号

| 常量 | 现值 | 改为 | 位置 |
| --- | --- | --- | --- |
| `PROMPT_VERSION`（brief） | `pod-brief-v6` | `pod-brief-v7` | [brief_runtime.py L45](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L45) |
| `PATTERN_PROMPT_VERSION` | `v25` | `v26` | [prompts.py L67](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L67) |
| `COLUMN_PROMPT_VERSION` | `v25` | `v26` | [column_prompts.py L64](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L64) |

历史批次：`prompt_snapshot` 已在建批/首次执行时冻结，不回填、不重渲染；
读旧快照的续跑路径不受影响。

---

## 5. 代码改动清单

| 文件 | 改动 |
| --- | --- |
| `brief_runtime.py` | schema 去 `minItems`、新增 `accent_colors`；删 <6 抛错；`_LIST_FIELDS` 加 `accent_colors`；改两份 recipe 与 field_contract；`PROMPT_VERSION` → v7 |
| `contracts.py` | `BusinessFields` 新增 `accent_colors: list[str]`（默认空）；删 `BRIEF_COLOR_PREFERENCES_MIN_ITEMS` 常量及其注释 |
| `prompts.py` | 删 legacy 配色分配与 60-30-10 渲染行；legacy 渲染 THEME / ACCENT 两行；改 `_COLOR_IMPACT_BLOCK`；`PATTERN_PROMPT_VERSION` → v26；半定制 `Color focus` **不动** |
| `column_prompts.py` | `ColumnStyleRecipe` 删 `color_harmony`；`build_column_style_recipes` 不再取色；`_style_block` 渲染 THEME / ACCENT 两行；base prompt 的 `base color` → `blank substrate colour`；`COLUMN_PROMPT_VERSION` → v26 |
| `fission_*` | 不动（D6） |
| 前端 | `PodBusinessFieldsDraft` / `PodBriefFieldsDraft` 加 `accent_colors`；`EMPTY_*`、`briefFieldsToDraft`、`businessFieldsForApi` 的 split 各加一行；`isBusinessFields` 按「后加键」处理（缺失视为空串），**不升草稿版本**——与 `fabric_craft` / `scene_mood` 同模式；半定制草稿的空白字段同步补一个空键（类型共享）。颜色不在主表单展示，无需新增控件 |
| 后端签名清理 | `build_style_listing_prompt` 与 `build_column_style_recipes` 删除只为配色洗牌服务的 `seed` 形参（元素分配仍在调用方完成） |

---

## 6. 测试计划

| 用例 | 现值 | 改为 |
| --- | --- | --- |
| `test_direct_listing_prompt_carries_the_static_60_30_10_rule`（[test_prompts.py L402-L405](../../../local-runtime/wh_local/modules/pod_customization/tests/test_prompts.py#L402-L405)） | 断言含 60-30-10 | 断言**不含** `60-30-10` / `DOMINANT BASE`；含 `COLOR HIERARCHY` |
| `test_style_prompt_renders_color_harmony_tiers_with_user_colors`（[L521-L528](../../../local-runtime/wh_local/modules/pod_customization/tests/test_prompts.py#L521-L528)） | 断言 `color_harmony=` + `(60%)` | 断言含 `THEME COLOURS` / `ACCENT COLOURS` 两行、**不含** `(60%)/(30%)/(10%)`、含「主色必须来自主题组」 |
| `test_*color_harmony*`（[L171-L183](../../../local-runtime/wh_local/modules/pod_customization/tests/test_prompts.py#L171-L183)） | 有/无色板两态 | 改为「有主题色/点缀色 → 两行分别注入；两组都空 → 不渲染配色行」 |
| brief 校验用例 | <6 色应失败 | 改为：两组都为空通过、各 2-3 个通过、带前缀样例通过；超长仍失败；**不校验前缀与数量** |
| brief schema 新增 | — | `accent_colors` 为数组、可为空、键必须在 required |
| column 新增 | — | 不含 60-30-10；含 substrate 表述；THEME / ACCENT 两行存在 |
| 前端草稿 | — | 旧草稿缺 `accent_colors` 时不丢草稿、归一化为空串；join/split 往返不被截断 |
| semi / fission | — | 本期不改，现有用例保持通过 |

运行方式：

```bash
cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_prompts.py \
  wh_local/modules/pod_customization/tests/test_column_prompts.py \
  wh_local/modules/pod_customization/tests/test_brief_runtime.py -q
```

---

## 7. 风险与取舍

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 四格配色趋同 | 现在每款 base 不同是确定性保证；改为模型自选后只能靠提示词 | 保留 `QUADRANT ISOLATION LOCK`，新增「相邻象限主色必须取自不同主题强关联色」；接受少量不稳定 |
| 主次失衡 | 去掉比例后模型可能把色板平均铺开 | `COLOR HIERARCHY` 明确「无比例但必须一眼读得出主次，平均铺开 = FAILURE」 |
| 画面变灰/变淡 | 原 `_COLOR_IMPACT_BLOCK` 的一部分是为修复灰暗 | 保留饱和/亮度/明度分离三段；只删比例与「本体必须最饱和色」 |
| 可复现性下降 | 随机分配 → 模型自由 | 提示词仍在建批/首轮冻结，重试沿同一 prompt；同批重放结果由模型侧确定性决定 |
| 分组质量不稳 | 豆包可能把不适合主题的颜色塞进主题组，或点缀色被当主色 | 分组与主色归属都由提示词约束，代码不兜底；运营可在智能填写结果里直接改文本（两组都是逗号/顿号分隔，可手工调整） |
| 色数不足 | 两组都不设硬下限，模型可能给得很少 | 提示词分别建议 5-6 / 6-10；不足时生图模型按主题自选，不报错 |
| 前端草稿兼容 | 新增 `accent_colors` 会改变草稿形状 | 按「后加键」归一化：旧草稿缺失视为空串，**不升版本、不加迁移**（已验证 tsc 与前端用例通过） |
| 历史批次 | 旧 `color_harmony` 已冻结在快照里 | 不回填；旧批次 `accent_colors` 默认空，`color_preferences` 继续按主题组渲染 |

---

## 8. 决策记录（2026-10-27 已确认）

| 问题 | 结论 |
| --- | --- |
| 半定制是否同批改 | **不改**，本期只动全定制（§4.5） |
| 裂变是否同批改 | **不改**，`assign_style_colors` 保留给裂变（§4.6） |
| 是否设色数上限 | **不设**，仅提示词建议 6-10（D7） |
| 主题前缀严格度 | **「主题词 + 颜色」**（圣诞红），共享前缀是预期行为（D8） |
| 是否保留「产品与印花是最强色彩陈述」 | **保留**，但不再绑定产品本体色（D9） |
| 色板是否分组 | **分两组双字段**：`color_preferences` 主题强关联色 + `accent_colors` 点缀色（D10） |
| 分组在哪里做 | **同一次 brief 调用直接输出**；本地零硬编码，只做提示词软限制（D11） |
| 生成时如何使用两组 | 每格主色必须来自主题组（至少 1 款）；点缀色只做辅佐；相邻象限主色不同（D12） |

暂无遗留待确认项。实施前需要用户明确批准开始改代码。

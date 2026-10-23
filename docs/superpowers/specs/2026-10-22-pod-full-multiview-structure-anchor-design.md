# POD 全定制「困难件结构锚定」：多视图参考 + 结构事实表

状态：**已确认，首期已实现**（2026-10-22）。首期范围 = Q1-A / Q2-B / Q3-A / Q4-B / Q5-B / Q6-B / Q7-B / Q9-A。
目标读者：POD 模块后端 / 前端开发，产品方。
目标基线：`local-runtime/wh_local/modules/pod_customization/` + `web-frontend/src/modules/pod_customization/`。

---

## 0. 一句话

全定制现在只把**一张模板图**当参考；遇到透明亚克力、玻璃、亮面、细结构这类「人眼第一眼都难认主体」的产品，模型拿不到足够的几何证据，只能按先验补全，于是必然畸变。本方案把模板升级为「**1 张主图 + 0–3 张结构视图（正/侧/俯/尺寸）**」，并在上传时**一次性**把视图转成可校验的「**结构事实表**」（部件数、层数、板厚、材质透明度…），以**文字硬约束**注入提示词；结构视图以**多图并列**方式下发给豆包 Seedream（当前默认生图模型）。

**第一版只对豆包 Seedream 负责**：速创（`image_gpt` / `image_gpt_2.5`）边缘化——不为其做多图适配与验收，命中速创时自动降级为「只传主图」，行为与现状完全一致。**不改变四段式骨架、不改计费口径。**

---

## 1. 背景与现状核对

### 1.1 问题现象

上传样例：

- `WechatIMG23.jpg`：白底上的透明亚克力分层展示架，3/4 俯视，主体与背景几乎无明度差。
- `WechatIMG38.jpg`：同一产品的另一视角，**粉色底 + 尺寸标注（31mm / 32.95mm / 20mm / 10.2mm）**，轮廓反而清晰。

同一件产品，第二张「能看见」，第一张「几乎看不见」。全定制只用第一张这类图，失败路径是固定的：

1. 单视角 → 背面、层数、板厚、层板与前挡板的连接方式全靠模型猜；
2. 透明材质在白底上边界不可辨 → 模型连「主体在哪、有几层」都不确定；
3. 提示词只说「别改形」，没有给出**可对齐的部件事实**，也没有「透明材质」这一支约束；
4. 结果：层数错、每层瓣数错、比例被拉扁/拉高、透明件被画成磨砂白塑料或加了不存在的侧板。

### 1.2 现状核对（代码落点）

| 环节 | 现状 | 差距 |
| --- | --- | --- |
| 模板上传 | 单文件：`POST /templates`，form 只有 `file` + `name`（`router.py:167-190`）；前端 `uploadTemplate(file, name)`（`podCustomizationApi.ts:42-49`），上传 UI 单选（`TemplateLibraryDrawer.tsx:144-151`） | 无多视图概念，无视图角色 |
| 模板存储 | 一模板 = 一 `asset_id`（`migrations/001_pod_customization.sql:21-62`、`repository.create_template:393-422`） | 无「视图集」列 |
| 批次生图入参 | full 分支只返回 `template_image` 单图（`worker.py:1317-1331`） | 无多参考图 |
| 试跑链路 | `run_direct_listing_trial` 同样只读单图（`service.py:1147-1213`） | 同上 |
| 参考图下发 | `_grid_references` 已支持 `reference_images` 多图，顺序保留（`ai_runtime.py:189-204`） | **能力已有**，full 未使用 |
| 端点能力 | 豆包 `image` 支持数组（`doubao.py:110-115`），且已是默认模型（`catalog.py:29-33`）；速创走 `urls`，2.5 是逗号拼接字符串（`suchuang.py:141-152`） | 多图行为只在豆包上验证；速创不纳入本期 |
| 模型选择 | 运行时按设置读模型，默认豆包 Seedream（`ai_runtime.py:332-345`、`catalog.py:32-33`） | 需按「模型 → 平台」决定是否下发多图 |
| 材质约束 | `_MATERIAL_REALISM_BLOCK` 把 glass 混在「光滑/硬质」一行（`prompts.py:347-367`） | 无「保持透明 / 可见厚度 / 折射高光 / 不得变磨砂白」 |
| 几何约束 | `GEOMETRY LOCK` 只写「别改形、别改比例」（`prompts.py:585`）；`_SUBJECT_ANCHOR_HEADER` 是用户手写自由文本（`prompts.py:421-426`） | 无可校验的部件数量与尺寸事实 |
| 视觉分析能力 | 复刻链路已有「多图 → 严格 JSON」的豆包视觉调用范式（`replica_analysis_runtime.py:390-400`，端点 `:808-815`） | full 未使用 |
| 重试通道 | 每款固定 attempt 1/2（`prompts.py:716-718`、`build_style_listing_prompt`） | 可用于「结构不符 → 纠正重试」 |
| 出图校验 | 目前只有整图质量门（尺寸/纯色/重复/文字，`images.py:18-50`） | 无「结构保真」校验 |

### 1.3 关键约束

1. **四段式骨架与段头被测试锁定**（`tests/test_prompts.py::test_prompt_is_organized_into_four_parts`），只允许在既有段落内加块。
2. **提示词「禁止任何文字」是硬规则**（`_NO_QUADRANT_LABELS`，`prompts.py:455-459`）。而尺寸图天生带数字/箭头 → 必须显式声明「参考图上的标注不是产品的一部分」。
3. **模板图是结构基准**（`GEOMETRY LOCK`）。新增视图只能「补充结构证据」，不能替代主图，也不能引入第二套比例。
4. **模型对首图有强偏置**：多图传参时第一张权重最高，主白底图必须排第一。
5. **一个批次 = 一个模板 = 一个产品**，所以结构事实天然批级唯一，可随模板缓存。
6. **速创边缘化**：第一版只保证豆包 Seedream 的多图效果；速创的多图行为不验证、不适配，命中速创时自动退回单主图（行为等同现状），不阻塞、不回退质量。

---

## 2. 目标与非目标

**目标**

| 编号 | 目标 |
| --- | --- |
| G1 | 模板支持「主图 + 结构视图集」，按角色存储（`primary` / `side` / `top` / `dimension`），并冻结进模板快照 |
| G2 | 结构视图以**多图并列**下发给豆包 Seedream；速创命中时自动降级为只传主图（不与本期目标冲突） |
| G3 | 上传时一次性产出「结构事实表」，可人工确认/修订，并作为**最高优先级文字硬约束**注入提示词 |
| G4 | 提示词新增透明/亮面材质锁与部件数量/尺寸字面锁，版本升 `v17` |
| G5 | 明确「参考图 vs 提示词」冲突时的优先级，避免模型在两者之间摇摆 |
| G6 | 不破坏四段式骨架、不提价、不改现状成功路径（普通布艺产品行为不变） |

**非目标**

- ❌ 不做「三视图 → 3D 模型 → 渲染」的重建管线（成本与延迟都不成比例）。
- ❌ 不把多视图设为硬性必填（避免阻塞普通产品的小单）。
- ❌ 不改四段式段落顺序与职责。
- ❌ 不改计费口径与生图模型选择逻辑（`_resolve_pod_image_model`）。
- ❌ **不为速创做多图适配、验收或视图板兜底**；速创只在「模型 → 平台」判断里退回单主图。
- ❌ 不做「上传端自动抠图/换底」以外的高级图像处理（见 §5.6，本期只作可选）。

---

## 3. 已确认决策

| 编号 | 决策 | 状态 |
| --- | --- | --- |
| D1 | 方向 = **三视图上传辅助锚定**，与主图并存，不替代主图 | 已确认 |
| D2 | 视图角色固定为 `primary` / `side` / `top` / `dimension`，主图永远排第一 | 已确认 |
| D3 | 视图集随模板保存，并**冻结进批次 prompt_snapshot**（批次回放/续跑可复现） | 已确认 |
| D4 | 下发形态 = **豆包多图并列**；速创命中时只传 `primary`，不做视图板兜底 | 已确认 |
| D5 | 结构事实表在**上传时**生成一次（模板级缓存），不在每批次重复调用 | 已确认 |
| D6 | 事实表仅在低置信度时要求用户确认，确认结果入模板 | 已确认 |
| D7 | 提示词版本 `PATTERN_PROMPT_VERSION v16 → v17` | 已确认 |
| D8 | 本期不做「生成后结构校验闸门」 | 已确认（下期） |
| D9 | **第一版只为豆包 Seedream 负责**：速创边缘化，不做多图适配与验收 | 已确认 |

---

## 4. 核心事实（已核实）

| 事实 | 依据 |
| --- | --- |
| full 链路只有单张参考图 | `worker.py:1327-1331`；`service.py:1206-1213` |
| provider 层已就绪，支持有序多图 | `runtime_contracts.py:38-61`、`ai_runtime.py:189-204` |
| 豆包单图传字符串、多图传数组 | `doubao.py:110-115` |
| 速创 2.5 的 `urls` 必须逗号拼接（**本期不依赖**） | `suchuang.py:141-148` |
| 参考图只接受 JPEG/PNG/WEBP | `assets.py:16-20`、`assets.py:143`、`runtime_contracts.py:12` |
| 模板上传大小上限独立于图片上限 | `router.py:171-181` |
| 现有重试固定两次 | `prompts.py:716-718` |
| 现有质量门不判结构 | `images.py:18-50` |
| 视觉分析可直接复用 Ark chat 范式 | `replica_analysis_runtime.py:390-400`、`:808-815` |

---

## 5. 方案设计

### 5.1 参考图层级（L1）：视图集上传

模板由「一张图」升级为「一个视图集」：

| 角色 | 数量 | 作用 | 硬约束 |
| --- | --- | --- | --- |
| `primary` | 1（必填，即现有模板图） | 唯一结构基准 + 首图 | 保持白底、无遮挡、完整轮廓 |
| `side` / `top` | 各 0–1 | 补层数、板厚、连接方式、对称性 | 只取结构，背景/道具不进入结果 |
| `dimension` | 0–1 | 提供真实尺寸 | **只读数字**；箭头/标注/彩色底一律不得进图 |

要点：

- 视图**全部可选**。普通布艺产品维持现状（只传 `primary`），零行为变化。
- 顺序固定：`primary` → `side` → `top` → `dimension`。禁止把尺寸图排第一。
- 尺寸图是「文字污染源」：必须在提示词里显式声明「参考图上的尺寸线、箭头、数字、彩色背景不是产品的一部分，不得出现在成品图中」。
- **上传入口在批次表单**（「构图定制」右侧的「结构视图锚定」勾选框，勾选后才展开三视图）：勾选并选图后，创建批次前先调用 `POST /templates/{id}/views` 把视图挂到**当前选中的个人模板**（同角色覆盖、其余保留），再由模板派生批次；系统模板不支持，需要先保存为个人模板。

### 5.2 多图下发（L2）：只管豆包

第一版不做「视图板」合成，多图直接并列下发给豆包 Seedream：

- `prepare` 把每张视图转成 base64 data URL，`generate` 按「单图传字符串、多图传数组」提交（`doubao.py:72-115`）；
- 张数上限与顺序权重**实测确认**（§8-Q2），首图必须是 `primary`；
- **速创命中时降级**：由「模型 → 平台」（`catalog.provider_for_model`，`catalog.py:59-61`）判断，非豆包平台只传 `primary` 一张，行为与现状一致；不引入视图板、不改速创实现。

> 取舍：不做视图板 = 少一个派生资产、少一条合成代码路径，也不会有「拼图后单张分辨率下降」的副作用。代价是速创用户享受不到多视图（本期接受，见 D9）。

### 5.3 结构事实表（L3，ROI 最高）

上传时触发**一次**豆包视觉调用，把视图集转成严格 JSON（复用 `replica_analysis_runtime` 的多图 + JSON 契约范式）：

```json
{
  "product_type": "acrylic tiered display stand",
  "part_count": 4,
  "tiers": 4,
  "scallops_per_tier": 14,
  "thickness_mm": 10.2,
  "height_mm": 31,
  "material": "clear acrylic",
  "transparency": "high",
  "surface_finish": "glossy",
  "symmetry": "mirror",
  "connections": "stepped slots, no fasteners",
  "printable_regions": ["front plate surface"],
  "views_used": ["primary", "side", "top", "dimension"],
  "confidence": { "tiers": 0.9, "scallops_per_tier": 0.7, "thickness_mm": 0.95 }
}
```

设计要点：

- **低置信度字段不猜**：`confidence < 阈值` 的字段留空，并在 UI 标「需确认」，绝不让模型编数字。
- **用户可改**：确认后才写入模板（对标复刻的 `source_confirmed` 思路），避免一次误识别污染整批。
- **模板级缓存**：同一模板多批次复用，不重复计费。
- **注入方式**：作为文字硬约束放进 PART 1，位置在 `_SUBJECT_ANCHOR_HEADER` 附近（同级或更高），措辞为「字面事实」而不是「建议」。

为什么用文字而不是「再给一张图」：文字约束可被校验、可复用、不与主图抢注意力；而且视觉模型能读出人眼难读的尺寸标注，正好补上模型读不了标注的短板。

### 5.4 提示词 v17（L4）

在既有四段式内部新增三块，不新增段落：

| 新块 | 位置 | 内容要点 |
| --- | --- | --- |
| `REFERENCE VIEWS CONTRACT` | PART 1（`GEOMETRY LOCK` 之后） | 第 1 张=主图/结构基准，第 2 张=侧面，第 3 张=俯视，最后=尺寸图；**只取结构**；标注/箭头/彩色底/道具一律丢弃；多张图冲突时以主图为准 |
| `TRANSPARENT & GLOSSY MATERIAL LOCK` | PART 1（`_MATERIAL_REALISM_BLOCK` 之内或紧邻） | 保持通透、可见板厚、边缘高光与折射；**禁止**变磨砂/奶白/不透明；禁止添加不存在的侧板、背板、底座；透明件与背景必须有可辨的明度或边缘分离 |
| `PART COUNT LOCK` | PART 1（紧随结构事实表） | 字面数量：`exactly 4 tiers, 14 scallops per tier, 31 mm tall, 10.2 mm thick`；任何数量偏差即失败；四格必须一致 |

另外：

- `ARTWORK LAYOUT LOCK` 已覆盖「四格同布局」，无需改动。
- 结构事实表为空时（普通产品），三块中的后两块不渲染，行为回退现状。

### 5.5 冲突优先级（明确写死，避免模型摇摆）

| 维度 | 优先级（高 → 低） |
| --- | --- |
| 结构 / 尺寸 / 部件数 | 结构事实表 > 三视图 > 主图 > 提示词 |
| 材质表现（透明/哑光/绗缝） | 结构事实表材质 > 提示词材质块 > 主图观感 |
| 图案 / 配色 / 场景 / 四格机位 | 提示词 > 一切参考图 |
| 参考图上的标注 / 背景 / 道具 | 一律丢弃（无优先级，直接禁止） |

这段同时要落成提示词句子与文档，避免以后改提示词时把它改回去。

### 5.6 可选增强（本期不做，记录备选）

1. **透明件自动换底**：检测「低对比度边缘 + 无 alpha」时，把主图合成到中灰卡（如 `#B9BDC4`）再传，提升主体可识别度。风险：改变了「模板结构基准」的视觉呈现，需要 A/B 验证。
2. **轮廓/边缘图**：额外生成一张高对比轮廓图作为参考，专门喂「几何」而不是「外观」。
3. **生成后结构校验闸门**：四格出图后用视觉模型比对事实表（部件数/层数/轮廓长宽比），不符则带纠正语重试一次（复用 attempt=2）。
4. **上传端困难件提示**：命中透明/玻璃/亚克力/多部件关键词，或事实表 `confidence` 偏低时，提示「建议补充正视图/侧视图」，但不硬拦。
5. **视图板（速创兜底）**：若日后要恢复速创多视图，再把多图拼成一张单图下发；本期不做（D9）。

---

## 6. 契约 / 版本 / 改动清单

**后端（数据与接口）**

- `migrations/025_template_views.sql`（新增）：模板级视图集与事实表字段（形状见 §8-Q6）。
- `contracts.py`：`BatchCreate` 不变（仍只传 `template_id`，视图集随模板走）；如需上传接口的请求体类型，新增 `TemplateViewsUpload`。
- `router.py`：`POST /templates` 支持 `views[]`（可选）；或另开 `POST /templates/{template_id}/views`。
- `repository.py`：`create_template` 落视图集；`get_template` 返回视图集；快照表同步。
- `assets.py`：新增 `kind="template_view"`；同一内容由内容寻址自动去重。

**后端（生成链路）**

- `worker.py:_style_generation_inputs`（full 分支）：按当前模型所属平台分流——豆包 → `reference_images`（角色 `product_primary` / `product_side` / `product_top` / `product_dimension`，顺序固定）；非豆包 → 维持 `template_image` 单图。平台判断复用 `catalog.provider_for_model`（`catalog.py:59-61`），不新写平台配置。
- `service.py:run_direct_listing_trial`：同步改造（试跑链路必须与批次一致，否则验证结论不可迁移）。
- `ai_runtime.py`：无需改动（已支持有序多图）；`reference_count` 日志自动变成 N。
- `prompts.py`：新增三块 + `build_*` 签名吃 `structure_facts`；`PATTERN_PROMPT_VERSION → v17`。

**后端（分析）**

- 新增 `structure_analysis_runtime.py`（或并入现有 runtime）：多图 → 严格 JSON，复用 `replica_analysis_runtime` 的调用方式与重试策略；失败时**不阻塞上传**，事实表留空并标记 `analysis_failed`。

**前端**

- `podCustomizationApi.ts:42-49`：`uploadTemplate` 支持多视图。
- `TemplateLibraryDrawer.tsx:144-151`：上传区增加「结构视图（可选）」多选，展示角色。
- 新增「结构事实表确认」面板（可编辑、标低置信度字段、可跳过）。
- `types/index.ts`：模板类型补视图集与事实表。

**测试**

- `tests/test_prompts.py`：新三块的渲染/不渲染分支、版本号断言、四段式骨架不变。
- 新增 `tests/test_structure_analysis_runtime.py`：JSON 契约、低置信度留空、失败降级。
- `tests/test_worker.py`：豆包多参考图顺序 + 速创回退单图。
- `tests/test_direct_listing_trial.py`：试跑链路同样按平台分流。
- 前端 `podCustomizationApi.test.ts` / `TemplateLibraryDrawer.test.ts`。

---

## 7. 风险与边界

| 风险 | 缓解 |
| --- | --- |
| 尺寸图的数字/箭头被画进成品图 | 提示词显式声明「标注不是产品」；尺寸图固定排最后；必要时只用事实表里的数字、不传尺寸图 |
| 多视图给出互相矛盾的结构（不同版本/不同尺寸的产品） | 主图优先 + 事实表需用户确认；提示词写明冲突处理顺序 |
| 豆包多图张数上限/顺序权重未知 | 上线前用固定样例实测（§8-Q2）；首图固定 `primary`；超限时按 `primary > side > top > dimension` 截断 |
| 速创用户选不到多视图效果 | 本期接受（D9）；速创自动退回单主图，行为不变，不产生新故障 |
| 视觉分析误识别，反而把错误写死成硬约束 | 低置信度留空 + 用户确认；事实表可编辑；不做「强制套用」 |
| 提示词变长稀释关键契约 | 新块只在有视图集/事实表时渲染；普通产品提示词长度不变 |
| 新增视觉调用带来成本 | 模板级一次 + 缓存；失败不重试到阻塞；成本计入 §8-Q4 决策 |
| 老模板/老批次没有视图集 | 缺列即回退单图，行为不变；不改历史快照 |

---

## 8. 决策记录

| 编号 | 问题 | 结论 |
| --- | --- | --- |
| Q1 | 三视图是「建议」还是「困难件强制」？ | **A 全可选**（不阻塞普通产品） |
| Q2 | 豆包一次传几张视图？ | **B 最多 3 张**（primary + side + top） |
| Q3 | 事实表生成时机与归属？ | **A 上传时**（模板级一次） |
| Q4 | 事实表要不要用户确认？ | **B 仅低置信度时确认**（任意核心字段 `< 0.7`） |
| Q5 | 生成后结构校验是否本期做？ | **B 下一期** |
| Q6 | 视图集与事实表存哪？ | **B 独立 `pod_customization_template_views` 表** + 模板表事实列 |
| Q7 | 尺寸图要不要传？ | **B 只把数字写进事实表**：`dimension` 只入识图，不下发生图 |
| Q8 | 透明件自动换底是否做？ | **B 验证后再定**（本期不做） |
| Q9 | 模型下拉里速创怎么处理？ | **A 保留可选，不额外改 UI**（本期通过行为降级 + 文档说明） |

---

## 9. 交付文件清单（首期已实现）

**新增（后端）**

- `local-runtime/wh_local/modules/pod_customization/migrations/025_template_views.sql`
- `local-runtime/wh_local/modules/pod_customization/structure_analysis.py`（契约、解析、提示词块渲染）
- `local-runtime/wh_local/modules/pod_customization/template_views.py`（视图 → 有序参考图）
- `local-runtime/wh_local/modules/pod_customization/tests/test_structure_analysis.py`

**修改（后端）**

- `contracts.py`（`TemplateStructureUpdate`）
- `router.py`（`POST /templates` 支持 `side` / `top` / `dimension`；`POST /templates/{id}/views` 批次表单补视图；`PATCH /templates/{id}/structure`）
- `repository.py`（模板视图增删查、事实表读写、批次 prompt 冻结）
- `service.py`（上传即识图、事实表修订、试跑链路多图）
- `worker.py`（按平台分流下发参考图，批次级缓存）
- `prompts.py`（v17 三块 + 事实表注入）
- `billing_contract.py`（`for_structure_analysis`，纯 title 零计费）
- `replica_analysis_runtime.py`（`analyze_structure_views`，复用 Ark 视觉 lane）
- `image_providers/catalog.py`（`model_supports_multi_view`）
- `wh_local/pod_migrations.py`（025 迁移契约）
- `tests/test_prompts.py` / `tests/test_persistence.py` / `tests/test_worker.py` / `tests/test_router.py`

**修改（前端）**

- `api/podCustomizationApi.ts`（`attachTemplateViews`、结构事实表 PATCH）
- `components/PodStructureFactsPanel.tsx`（低置信度确认面板，批次表单内复用）
- `pages/PodCustomizationPage.tsx`（构图定制右侧的「结构视图锚定」开关 + 三视图上传 + 建批前补视图/确认流程）
- `components/TemplateLibraryDrawer.tsx`（保持原有单图上传，不再承载结构视图）
- `types/index.ts`、`styles/podCustomization.css`

**无需改动**：生图 provider 实现、计费链路、四段式骨架与段头、拆格与切图逻辑。

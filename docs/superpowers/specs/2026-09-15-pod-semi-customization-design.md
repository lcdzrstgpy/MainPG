# POD 半定制（纯图案生成）完整方案

状态：**v2 待确认**（2026-09-15 第二轮评审：去掉模板/参考图，纯提示词生成花色图案，代码未改动）
目标读者：POD 定制链路前后端实现者、验收人

---

## 0. 一句话

在现有「POD 定制（全定制）」之外新增**半定制**链路：侧栏「POD 定制」下挂二级菜单，**不用模板、不用参考图**，纯靠提示词让速创出 2×2 四宫格花色图案 → 本地拆 4 张 800×800 → **每格即一个款**（4 格 = 4 款）→ 全批次打成一个 zip；**不出标题、不要 SKU/售价/规格卡/店小秘导出**，画面里**只有图案本身**（无产品、无主体、无场景、无文字），用户拿图自行印到产品上。**全程不接图床**：速创结果下载后只落本地资产，不上传 COS、不生成公网地址。

---

## 1. 背景与目标

### 1.1 目标

| 编号 | 目标 |
|---|---|
| R1 | 新增独立入口：侧栏「POD 定制」变为二级分组，下挂「全定制 / 半定制」 |
| R2 | 产物 = **纯图案**：画面内不含任何产品/主体/场景道具/文字，只有花色图案本身 |
| R3 | **不用模板、不用参考图**：半定制完全按提示词文生图（速创调用不带 `urls`） |
| R4 | **一款一图**：4 格 = 4 款；每次发起严格按 4 的倍数（`count % 4 == 0`），速创调用次数 = `count / 4` |
| R5 | 不出标题、不出 SKU / 申报价 / 重量 / 规格卡、不出店小秘 / 妙手导出 |
| R6 | 交付：**一个批次打成一个 zip**（不是按组/按款分包），包内 `style_001.png` … |
| R7 | 计费**独立口径**：每款随机 **32–38 积分**（与全定制 40–50 口径隔离，独立 profile） |
| R8 | 后端最大化复用现有 POD worker/拆分/发布/计费底座；前端最大化复用现有 POD 页面区块（**含智能填写**） |
| R9 | **不接图床**：速创返回的图下载后只落本地资产，不上传 COS、不产出公网 URL；预览走本地资产端点，交付走 zip |

### 1.2 非目标

- ❌ 不改动全定制链路的行为与计费口径（`pod_random_v1` 保持）。
- ❌ 不做图案抠图、去底、二值化、蒙版扩边（D1：四宫格原图即交付图）。
- ❌ 不做无缝平铺（seamless tile）专项约束（P1 可作 prompt 选项）。
- ❌ 不做店铺/链接级上架（无 `listing_fields`、无店小秘/妙手 xlsx、无 `listing_ready`）。
- ❌ 不做每款一次速创调用（成本 ×4，已否决，见附录 B）。
- ❌ 不新建半定制后端模块（复用 `wh_local/modules/pod_customization`）。
- ❌ 不让半定制复用/选择模板（用户明确「不需要模板」）。

---

## 2. 已确认决策

### 2.1 用户决策（2026-09-15）

| 编号 | 决策 |
|---|---|
| D1 | 交付图 = 速创四宫格**拆分后的单格原图**，不做任何后处理 |
| D2 | 一款一图：**4 格 = 4 款**；发起数量严格按 4 的倍数 |
| D3 | 入口：侧栏「POD 定制」下**新增二级侧边栏**（全定制 / 半定制） |
| D4 | 不要上架字段（SKU / 申报价 / 重量 / 规格卡），也不要店小秘导出 |
| D5 | 交付：按批次打包成**一个 zip** |
| D6 | 计费：独立计费，**每款 32–38 积分** |
| D7 | 生图流程复用：仍是一次速创调用出 2×2 四宫格、本地拆 4 张 800×800 |
| D8 | 不要标题（不注入标题运行时） |
| D9 | **纯图案**：只要图案，不包含任何主体 |
| D10 | **不需要模板**：根据提示词生成花色图案，用户自行用其软件印到产品上 |
| D11 | zip 内文件名：`style_001.png` |
| D12 | 部分失败**允许下载**（只打成功款，款号断号） |
| D13 | 重试粒度：**整组重试** |
| D14 | 保留并复用「智能填写 + 业务字段」 |
| D15 | **不接图床**：图片直接下载放本地，不上传 COS / 不生成公网链接 |

### 2.2 技术决策

| 编号 | 决策 | 依据 |
|---|---|---|
| T1 | 不重建 `batches` 表；迁移只 `ADD COLUMN mode`，模板三列约束用**惰性占位模板**满足 | `batches.template_id / template_snapshot_id / template_name` 均 `NOT NULL` + FK（`migrations/001:70-72, 89-90`）；SQLite 去 FK 必须重建表，而重建需在事务外关 FK（PRAGMA 在事务内是 no-op）→ 崩溃安全成本高。备选见附录 B |
| T2 | 半定制 role 用 `pattern_1..pattern_4` | 天然不触发 `role=="hero"` 的规格卡合成（`worker.py:1421-1423`）与 `role=="lifestyle"` 的标题提交（`worker.py:1448-1478`），零额外 if |
| T3 | 计费用独立 profile `pod_semi_v1` | 现有 `_product_batch_groups()` 按 `:style:` 分组会把 link 算成组数（`billing_contract.py:284-295`），半定制要按款计费，必须独立投影 |
| T4 | 前端抽共享组件，半定制页只做薄壳 | 用户要求最大化复用现有 POD 页面 |

---

## 3. 术语定义

| 术语 | 定义 | 对应现有概念 |
|---|---|---|
| 全定制 | 现状链路：1 款 = 1 次速创调用 = 4 张成品图 + 1 条标题 | `style_index` |
| 半定制 | 新链路：1 **组** = 1 次速创调用 = 4 张纯图案，每张 = 1 **款** | 复用 `style_index`（组） |
| 组（group） | 一次速创调用产出的一格四宫格；DB 里仍写 `style_index` | `style_index` |
| 款（交付单元） | 拆出来的单张图案；**款号 = (style_index-1)*4 + variant_index** | 现有 `item.index`（`repository.py:465`） |
| 花色图案 | 只有图案本身，无产品/主体/场景/文字的图 | — |
| 一拆 4 | 本地把 2×2 四宫格切成 4 张 800×800 | `media.py:610-661` |

**换算**：输入 `count` = **交付款数**（4 的倍数，4–200）→ 组数 = `count / 4`（≤ 50）→ 速创调用 = `count / 4` 次。

---

## 4. 已核实的事实（含依据）

| 事实 | 依据 |
|---|---|
| 批次一次生图 = 一次 provider 请求出一张四宫格 | `worker.py:1246-1313`、`ai_runtime.py:135-189` |
| **现有提交体必须带 1 张参考图**：`urls: [reference_url]`（2.5 为字符串） | `ai_runtime.py:234-241` |
| 参考图先上传 COS 换公网地址，非公网即判 `non_retryable_local` 失败 | `ai_runtime.py:191-212`（207-211） |
| `GeneratedMedia.reference_count` 现固定为 1 | `ai_runtime.py:180-189` |
| worker 执行前会读模板快照与模板图字节 | `worker.py:486-490` |
| worker 构造生图请求时带 `template_id / template_image / template_content_type` | `worker.py:1113-1120` |
| 请求尺寸 `1024x1024`，模型由 `SystemConfigService.get_pod_image_model()` 决定 | `runtime_contracts.py:38-47`、`ai_runtime.py:439-453` |
| 分段超时 240s、轮询 `/api/async/detail`、下载失败按「已返回结果」处理 | `ai_runtime.py:47, 282-288, 159-165` |
| 拆分 = 本地 Pillow，不发 AI 调用；每格缩到 800×800 | `ai_runtime.py:379-382`、`media.py:610-661`（647-648） |
| 拆分后按 `variant_index` 1..4 依次绑定 role | `worker.py:1405-1412`、`prompts.py:12` |
| 规格卡只合成到 `hero`；标题只在 `lifestyle` 发布后触发 | `worker.py:1421-1423`、`worker.py:1448-1478` |
| 现有发布循环把每格上传 COS 并写 `publications.public_url`（内容寻址，可重复覆盖） | `worker.py:1426-1433`、`repository.py:2478-2484` |
| 前端已有**本地资产预览通道**：`pattern_preview_url = /api/pod-customization/assets/{asset_id}` | `service.py:2059`、`router.py:356-370`、`usePodAssetUrl.ts` |
| `title_runtime is None` 时 worker 已支持无标题链路（按图片计数定终态） | `worker.py:507-521` |
| 建批事务写：批次行 + 每款 4 行 `style_grid_results` + 每款 1 行 `style_titles` + companion 标记 | `repository.py:336-422`（380-390 / 412-415 / 416-421） |
| `batches` 表无 mode 列；形态靠 companion 表判定 | `migrations/001:64-94`、`migrations/003:2-6`、`repository.py:3129-3133` |
| `batches.template_id / template_snapshot_id / template_name` 为 `NOT NULL` + FK | `migrations/001:70-72, 89-90` |
| `pod_customization_templates` 与 `_template_snapshots` 的 `asset_id` 为 `NOT NULL` + FK | `migrations/001:26, 50, 36, 57` |
| `style_grid_results` 唯一键 `(batch_id, style_index, variant_index)`；`publications.role` 是自由 TEXT | `migrations/003:23`、`migrations/004:3-8` |
| 计数刷新按「每款 4 张全部 settled 才算 processed」 | `repository.py:3074-3127` |
| 计费冻结：link_count = 按 `:style:` 分组数，profile 固定 `pod_random_v1`，每 link 40–50 | `billing_contract.py:229-242, 284-295`、`billing.py:23-26, 2016-2043` |
| 结算：`item_results` 条数必须 = 冻结 link_count；每 link subitem feature 集合必须 = 冻结 scope；`four_grid` 成功即扣费，否则整款退还 | `billing.py:2583-2593, 2611-2636` |
| 冻结 profile 白名单只允许 product / `pod_random_v1` | `billing.py:1963`、`auth_server.py:1277-1280` |
| 资产内容寻址落盘，可 `read()` / `path()` 取盘上文件 | `assets.py:37-71, 76-88` |
| 款号 = `(style_index-1)*4 + variant_index` 已是现有 payload 的 `index` | `repository.py:465`、`service.py:2053-2057` |
| 前端导航：POD 定制当前是**顶层单项**；新模块必须登记进 `workspaceModules` 与 `workspacePageModules` | `modules.ts:79-85, 169-200, 202-218`、`WorkspaceShell.tsx:437-482` |
| 前端全定制数量选项 `[2, 10, 20, 40, 60, 100]`；草稿版本 4 | `podCustomizationModel.ts:31`、`podCustomizationDraft.ts:3` |
| 前端已有跨模块 import 先例 | `ProductSourceDrawer.tsx:7-8` |
| 现有 prompt 硬规则：画面内禁止出现文字 | `prompts.py:142` |
| 测试运行器 `node --test --experimental-strip-types`；页面/组件用源码字符串断言 | 既有 `PodSpecCardEntry.test.ts` 等 |

---

## 5. 复用策略总览

| 能力 | 策略 | 说明 |
|---|---|---|
| 速创轮询 / 下载 / 内容类型 / 错误分类 / 重试类 | **直接复用** | `ai_runtime.py` 全部保留，只加「无参考图」提交分支 |
| 四宫格本地拆分 → 4×800×800 | **直接复用，零改动** | `media.split_four_grid` |
| COS 发布（公网图） | **不使用**（D15） | 半定制不调 `publish_listing_image`、不写 `publications.public_url`；预览走本地资产端点 |
| worker 流水线（生图窗口 / 两轮重试 / 暂停 / 取消 / 恢复 / fencing / 进度租约） | **复用，加 mode 分支** | 跳过模板读取、跳过标题与规格卡 |
| 业务字段（元素/风格/类目） | **直接复用** | `BusinessFields`（`contracts.py:59-73`）+ `assign_style_elements`（`prompts.py:85-117`） |
| 模板库 / 上传 / 标定 | **不使用**（D10） | 半定制不读模板、不要求标定 |
| 标题链路 / 规格卡 / 上架字段 / 店小秘 / 妙手导出 | **不使用** | 不写 `style_titles`、不注入 `listing_fields` |
| 表结构 | **复用**，仅加一列 `mode` + 占位模板 seed（T1） | 迁移 014 |
| 计费 | **新增独立 profile** | `pod_semi_v1`，32–38/款 |
| 前端页面 | **最大化复用现有 POD 页面**：抽共享组件/hook，半定制页只做薄壳 | 见 §10.2 |
| 交付 | **新增 zip 端点** | 复用 `PodAssetStore.path()` 读盘打包 |

---

## 6. 数据模型与契约

### 6.1 DB 迁移（014，唯一一处结构变更）

```sql
-- 014_semi_customization.sql
ALTER TABLE pod_customization_batches ADD COLUMN mode TEXT NOT NULL DEFAULT 'full';
```

* 历史批次自动 `full`；`full` = 现有全定制链路，行为零变化。
* 模板三列（T1）由**惰性占位模板**兜底：`repository.ensure_semi_placeholder(workspace_id, owner_user_id)` 首次创建半定制批次时 seed 一条
  * 极小的 1×1 PNG 资产行 + 模板行 + 快照行（名字「半定制占位模板」，`deleted_at` 非空 → **不出现在模板列表**，查询过滤见 `repository` 模板列表 SQL 的 `deleted_at = ''`）；
  * 半定制批次写 `template_id / template_snapshot_id / template_name` 指向它，worker 半定制分支**永不读取**该图；
  * 该 seed 只为满足 `NOT NULL` + FK，不做任何业务用途。

### 6.2 半定制创建契约（新增）

```jsonc
// POST /api/pod-customization/semi/batches
{
  "count": 20,                 // 交付款数；4..200 且必须 % 4 == 0（D2）
  "prompt_version": "v1",
  "business_fields": { /* 复用 contracts.BusinessFields，product_category 必填 */ },
  "creative_prompt": "",
  "title": ""                  // 批次名；为空时 zip 名回退批次短号
}
```

* **没有 `template_id`**（D10）；`listing_fields` 被 `extra="forbid"` 天然拒绝。
* 校验：`count % 4 != 0` → 422，文案「半定制数量必须是 4 的倍数」。

### 6.3 批次 payload（复用 `service._batch_payload`，新增字段）

```jsonc
{
  "id": "bat_xxx",
  "mode": "semi",              // 新增：full | semi
  "status": "completed",
  "style_count": 5,            // 组数 = count / 4
  "item_count": 20,            // 交付款数（= 请求 count）
  "items": [
    { "index": 1, "style_index": 1, "variant_index": 1, "role": "pattern_1", "status": "completed",
      "public_url": "",                                        // 半定制恒为空（不接图床，D15）
      "pattern_preview_url": "/api/pod-customization/assets/{asset_id}" },  // 本地资产端点预览
    { "index": 2, "style_index": 1, "variant_index": 2, "role": "pattern_2", "status": "completed",
      "public_url": "", "pattern_preview_url": "/api/pod-customization/assets/{asset_id}" }
    // ... 共 item_count 条；index 即款号（zip 内 style_001.png 对应 index=1）
  ],
  "style_titles": {}           // 半定制恒为空
}
```

### 6.4 role 常量（新增，见 T2）

```python
SEMI_PATTERN_ROLES = ("pattern_1", "pattern_2", "pattern_3", "pattern_4")
```

### 6.5 生图提交（无参考图分支，新增）

| 项 | 全定制（现状） | 半定制 |
|---|---|---|
| 参考图 | 模板图 1 张 → COS 公网地址（`ai_runtime.py:191-212`） | **无**（不发布、不校验公网） |
| 提交体 | `{"prompt", "size", "urls": [url]}`（2.5 用 `aspectRatio`，`234-241`） | `{"prompt", "size"}`（**不带 `urls`**；速创已确认支持纯文生图） |
| `reference_count` | 1 | 0 |
| 轮询 / 下载 / 超时 / 错误分类 | 复用 | **完全复用** |
| 尺寸 / 模型 | `1024x1024` / `_resolve_pod_image_model()` | **完全复用** |

实现方式（二选一，P0 取 a）：
- **a**：`DirectListingGridRequest` 的参考图字段改可选，`generate_listing_grid` 在无参考图时跳过发布并把 `urls` 置空；
- b：新增 `generate_semi_pattern_grid`。选 a 的理由：轮询/下载/错误处理只有一份，避免分叉。

**已确认（用户 2026-09-15）**：速创支持纯文生图，提交体不带 `urls` 即可返回四宫格 → 无需任何兜底占位图。

### 6.6 图片落盘与预览（不接图床，D15）

| 环节 | 全定制（现状） | 半定制 |
|---|---|---|
| 速创返回的四宫格 | 下载到本地字节 → 落资产（`kind=direct_listing_grid`） | **直接复用**（同一条路径） |
| 拆分后的 4 张 800×800 | 逐张上传 COS → 写 `publications.public_url`（`worker.py:1426-1433`） | **落本地资产即可**：`assets.save_image` → `pattern_asset_id`；**不调 `publish_listing_image`**，`publications.public_url` 留空 |
| 前端预览 | 优先 `public_url`，回退 `pattern_preview_url` | 只用 `pattern_preview_url`（`/api/pod-customization/assets/{asset_id}`，走本地资产端点 + 登录鉴权） |
| 交付 | 店小秘 xlsx 依赖公网 URL | zip 直接 `assets.path()` 读本地文件 |
| 磁盘占用 | — | 800×800 JPEG 约 0.1–0.3 MB/款；`count=200` 单批约 20–60 MB（沿用现有资产目录与删批次清文件逻辑） |

**收益**：半定制不再依赖 COS 配置与公网可达性（现有全定制在 COS 未配置时会以 `non_retryable_local` 失败）。
**代价**：图片不再有公网地址，仅本机/局域网可预览；这也意味着半定制**不能**走店小秘/妙手导出（本来就不需要）。

### 6.7 Prompt（半定制版本，新增）

* 新增 `build_semi_pattern_prompt(...)`，复用差异化机制（元素分配 `assign_style_elements`、composition/palette 轮换 `prompts.py:197-214`）。
* offset 细化到「格」：`offset = (style_index - 1) * 4 + (variant_index - 1)`，保证**同组 4 格互不相同**、跨组也不同（对应用户「一张的四宫格都不一样」）。
* 硬约束（D9/D10）：
  * 画面**只有图案**：不含产品、不含任何主体/器物/人、不含场景背景；
  * **无文字**（沿用并强化 `prompts.py:142`：不出现字母、数字、logo、印章、水印）；
  * 白底或单色底，图案满幅/居中单元，边缘干净（便于用户自行套版印刷）；
  * 四格 = 4 个不同的花色方案（同批内风格可统一，但图案必须有可见差异）。
* 业务字段（D14）参与风格/元素/配色约束 —— 这正是保留「智能填写」的原因。

---

## 7. 执行链路（复用现有 worker，只加 mode 分支）

```
创建：POST /semi/batches
  → 校验 count % 4 == 0
  → ensure_semi_placeholder（T1，仅首次）
  → service._freeze_batch（新 PodCallPlan.for_semi_batch，见 §8）
  → repository.create_batch(mode="semi")：批次行 + 组数×4 行 style_grid_results + companion 标记
     （**不写 style_titles**、不校验模板/标定）
  → worker.submit

执行：worker._process_style_grids_streaming（复用）
  组 i：速创 1 次调用（**无参考图**）→ 结果下载到本地 → 2×2 四宫格
     → 本地拆分 4 × 800×800 → 逐张落本地资产（**不上传 COS**）
     → role = pattern_1..pattern_4
     → finish_style_grid_result 落库（public_url 留空）
     → 【跳过】COS 发布、规格卡合成、标题提交
  两轮 attempt 后仍失败的组 → fail_style_grid（复用）
  → 终态：图片口径（worker.py:507-512）

交付：GET /semi/batches/{id}/download → zip（style_001.png …，读本地文件）
```

### 7.1 具体接入点

| 位置 | 改动 |
|---|---|
| `worker._process_batch_authorized`（`worker.py:486-490`） | `mode=="semi"` 时跳过模板快照/模板图读取（传空字节） |
| `worker._stream_style_attempts.submit_style`（`worker.py:1063-1120`） | prompt 走 `build_semi_pattern_prompt`；`DirectListingGridRequest` 不带参考图 |
| `worker._process_style_grids`（`worker.py:1396-1513`） | `roles = LISTING_IMAGE_ROLES if mode == "full" else SEMI_PATTERN_ROLES`；`mode=="semi"` 跳过 `publish_listing_image`（不写公网 URL） |
| `worker.regenerate_style`（`worker.py:657-758`） | 半定制「整组重试」复用（D13），跳过标题部分 |
| `service.create_batch`（`service.py:236-262`） | 新增 `create_semi_batch`（同实现加 mode 参数，跳过标题预算与模板 preflight） |
| `service` 注入标题（`service.py:1622/1641/1675/1699`） | 半定制恒 `include_title=False` / 不注入 `title_runtime` |
| `repository.create_batch`（`repository.py:336-422`） | 加 `mode` 写列；`semi` 跳过 `style_titles` |
| `repository.preflight_batch`（`repository.py:424-444`） | 半定制**不走**模板/标定校验 |
| `service._batch_payload`（`service.py:1907-1964`） | 输出 `mode / style_count / item_count` |

### 7.2 零改动

`spec_card.py`、`export.py`、`dianxiaomi.py`、`title_runtime.py`、`product_processing/infrastructure/media.py`、`images.py`（legacy 拆分器）。

---

## 8. 计费（独立 profile `pod_semi_v1`）

### 8.1 口径

| 项 | 全定制 | 半定制 |
|---|---|---|
| profile | `pod_random_v1` | `pod_semi_v1` |
| 计费单元 | 1 link = 1 款（= 1 组 = 1 次速创调用） | 1 link = 1 款（= 1 格图案） |
| 冻结 link_count | 组数 | **交付款数** = `count` |
| 每 link 单价 | 40–50 随机 | **32–38 随机** |
| scope | `{four_grid}`（无标题时） | `{four_grid}` |

### 8.2 实现要点

1. `billing_contract.py` 新增 `PodCallPlan.for_semi_batch(batch_id, *, count)`：call 仍按组下发（`{batch_id}:style:{group}:image:{attempt}`），worker 无需感知差异。
2. 新增 `semi_batch_freeze_payload()`：`link_count = count`、`scope = ["four_grid"]`、`billing_profile = "pod_semi_v1"`。
3. 新增 `semi_batch_settlement_payload(outcomes)`：按**格**展开 link —— 组 g 的 4 个 link（`(g-1)*4+1 … (g-1)*4+4`）都取该组 image call 成败；每 link 一个 `{feature: "four_grid", status}`，`link_idx` 从 1 连续。
4. `billing.py`：新增 `BATCH_BILLING_PROFILE_POD_SEMI = "pod_semi_v1"`、`POD_SEMI_LINK_PRICE_MIN_POINTS = 32`、`POD_SEMI_LINK_PRICE_VARIANTS = 7`；白名单（`1963`）加入；冻结分支（`2016-2043`）按 profile 选区间的随机值；`is_pod` 判定（`598`）与定价展示区间（`1210-1213`）同步。
5. `auth_server.py:1277-1280`：profile 白名单同步。
6. `remote_billing.py:60-80`：按 mode 传对应 profile。

**为什么必须独立 profile**：现有 `_product_batch_groups()` 按 `:style:` 分组，天然把 link 数算成组数；半定制按「款」计费，必须独立投影，否则会少收 3/4 的钱（T3）。

---

## 9. 交付与打包（zip）

### 9.1 端点

```
GET /api/pod-customization/semi/batches/{batch_id}/download
→ 200 application/zip
  Content-Disposition: attachment; filename="POD-SEMI-{批次短号}-{count}款.zip"
X-POD-Semi-Count: {已有张数}
```

### 9.2 打包粒度（用户明确）

**一个批次 = 一个 zip**：批次内所有可交付款打成一个包，不按组拆包、不按款单独出包、不分卷。

```
批次（mode=semi, count=20）
  └─ POD-SEMI-{批次短号}-20款.zip
       ├─ style_001.png   （组 1 / 格 1）
       ├─ style_002.png   （组 1 / 格 2）
       ├─ style_003.png   （组 1 / 格 3）
       ├─ style_004.png   （组 1 / 格 4）
       ├─ style_005.png   （组 2 / 格 1）
       └─ … 直到 style_020.png
```

* zip 内**扁平存放**，不建子目录。
* 重复下载内容一致（内容寻址资产 + 稳定命名）。

### 9.3 规则

| 项 | 规则 |
|---|---|
| 打包范围 | 本批次全部已完成款（一次请求打成一个包） |
| 权限 | `read` 权限；校验 workspace + owner（复用现有批次归属校验） |
| 批次形态 | 非 `mode == "semi"` → 404 |
| 批次状态 | 允许 `completed / partial_failure / failed / cancelled`；**部分失败允许下载**（D12），只打已 completed 的款，款号断号 |
| 文件命名 | `style_001.png` …（3 位补零，款号 = `item.index`，D11） |
| 文件内容 | `style_grid_results.pattern_asset_id` 原图字节（`assets.path()` 读盘，不做转码） |
| 空结果 | 一张都没有 → 409 +「当前批次没有可下载的图案」 |
| 审计 | 复用 `pod_customization_export_records`（kind=`semi_zip`），不存文件字节 |

### 9.4 前端展示

批次卡片头部显示「已出 12/20 款」，配「下载 ZIP（本批次全部款）」按钮（有 ≥1 张可点）。

---

## 10. 前端交互规格

### 10.1 导航改造（二级侧边栏）

```ts
export type WorkspaceModuleId = ... | "pod_customization" | "pod_semi_customization" | ...;
export type WorkspaceNavigationGroupId = "product_workflow" | "combo_workflow" | "sourcing_workflow" | "pod_workflow";

const podCustomization: WorkspaceModule = { id: "pod_customization", label: "全定制", ... };        // 由「POD定制」改为「全定制」
const podSemiCustomization: WorkspaceModule = { id: "pod_semi_customization", label: "半定制", ... }; // 新增

// workspaceModules：原顶层 podCustomization 替换为分组
{
  id: "pod_workflow", label: "POD定制", iconClass: "iconfont icon-skin",
  description: "POD 全定制与半定制",
  defaultChildId: "pod_customization",
  children: [podCustomization, podSemiCustomization],
}
// workspacePageModules：追加 podSemiCustomization
```

`WorkspaceShell.tsx`：新增 import + `renderTab` 的 `case "pod_semi_customization"`。
连带：`app/navigation/modules.test.ts`（顺序断言）、`app/navigation/podCustomizationNavigation.test.ts`。

### 10.2 页面复用（最大化复用现有 POD 页面，含智能填写）

**方式**：先把 `PodCustomizationPage.tsx` 的可复用区块抽成共享组件（落在 `modules/pod_customization/components/`，全定制页改为引用同一组件），半定制页只写差异部分，**不复制表单与交互代码**。

| 区块 | 复用方式 | 说明 |
|---|---|---|
| 页头 | **抽共享组件** `PodWorkbenchHeader` | 半定制**不渲染模板入口**（D10），隐藏导出/模板按钮（props 控制） |
| 业务信息列：**智能填写 `PodBriefInput`** + 业务字段表单 + 高级创意编辑 | **抽共享组件** `PodSetupColumn` | 一句话生成、历史记录、字段合并逻辑原样复用（D14） |
| 生成数量 | 共享组件 + `countOptions` prop | 半定制传 4 的倍数列表，并显示「= N 款图案 / N/4 次生图」 |
| 开始生成 + 冻结提示 | 共享容器，提交回调由页面注入 | 半定制注入 `POST /semi/batches`（预计冻结 32–38 × N） |
| 草稿自动落盘 | **抽共享 hook** `usePodDraftAutosave`（key/版本参数化） | 半定制用独立 storage key |
| 批次操作条（暂停/取消/继续/删除/重试） | **抽共享组件** `PodBatchToolbar`（props 控制按钮） | 半定制只保留**整组重试**（D13），追加「下载 ZIP」 |
| 批次结果区 | 半定制**自建薄组件**，复用公共子件（状态圆点、图片瓦片、进度条） | `PodBatchGallery` 是「款 = 4 图」（`PodBatchGallery.tsx:127-137`），半定制是「款 = 1 图」，故抽子件而非塞 mode 分支 |
| 大图灯箱 | **直接复用** `PodResultLightbox.tsx` | — |
| 历史抽屉 | **直接复用** `PodBatchHistoryDrawer.tsx` | — |
| 资产渲染 | **直接复用** `usePodAssetUrl.ts` / `PodAssetImage` | 因为不接图床，预览固定走 `pattern_preview_url`（本地资产端点） |
| 模板库抽屉 | **不渲染**（D10） | — |
| 失败重试弹窗 | 复用 `PodFailedRetryDialog.tsx`，但只保留「整组重试」一组 | 无标题组 |
| 不出现 | 标题编辑器、SKU/申报价/重量、规格卡入口、店小秘/妙手导出、上架详情抽屉、模板入口 | — |

**护栏**：共享组件抽取必须**行为不变**；全定制既有测试（`PodCustomizationPage.test.ts`、`PodSpecCardEntry.test.ts`、`PodFailedRetryDialog.test.ts`）全绿作为回归门。

### 10.3 前端数据与 API

| 文件 | 职责 |
|---|---|
| `modules/pod_semi_customization/api/podSemiCustomizationApi.ts` | `createBatch / getBatch / listBatches / pause / cancel / resume / delete / regenerateGroup / downloadZip` |
| `modules/pod_semi_customization/data/podSemiCustomizationModel.ts` | 数量校验（4 的倍数）、款号 ↔ (组, 格) 映射、`style_001.png` 命名、进度/状态文案 |
| `modules/pod_semi_customization/data/podSemiCustomizationDraft.ts` | 独立草稿：`mainpg:pod-semi-customization:v1:{account}:{workspace}` |
| `modules/pod_semi_customization/styles/podSemiCustomization.css` | 复用 `--pod-*` 变量；类名 `pod-semi-*` |

---

## 11. 改动点清单

### 11.1 后端（`local-runtime/wh_local`）

| 文件 | 改动 |
|---|---|
| `modules/pod_customization/migrations/014_semi_customization.sql` | 新增：`batches.mode` 列 |
| `modules/pod_customization/contracts.py` | 新增 `SemiBatchCreate`（无 template_id，count % 4 校验）；新增 `SEMI_PATTERN_ROLES` |
| `modules/pod_customization/ai_runtime.py` | `DirectListingGridRequest` 参考图可选；无参考图时跳过 COS 发布、提交体不带 `urls`、`reference_count=0` |
| `modules/pod_customization/prompts.py` | 新增 `build_semi_pattern_prompt`（纯图案、无主体/无文字、offset 到格） |
| `modules/pod_customization/billing_contract.py` | 新增 `for_semi_batch` / `semi_batch_freeze_payload` / `semi_batch_settlement_payload` |
| `modules/pod_customization/repository.py` | `create_batch(mode=...)`（semi 不写 style_titles）；`ensure_semi_placeholder`；payload 加 `mode/style_count/item_count` |
| `modules/pod_customization/service.py` | `create_semi_batch`（不走模板 preflight）；半定制不注入 `title_runtime`；zip 打包 |
| `modules/pod_customization/worker.py` | 跳读模板图；role 按 mode 选；prompt 按 mode 选；**跳过 COS 发布**；整组重试复用 |
| `modules/pod_customization/router.py` | 新增 `/semi/batches*`（create / list / get / pause / cancel / resume / delete / regenerate / download） |
| `modules/pod_customization/remote_billing.py` | profile 按 mode |
| `wh_local/billing.py` | 新 profile 常量 + 白名单 + 32–38 区间 + 定价展示区间 |
| `wh_local/customer/auth_server.py` | profile 白名单同步 |

**零改动**：`spec_card.py`、`export.py`、`dianxiaomi.py`、`title_runtime.py`、`images.py`、`product_processing/infrastructure/media.py`。

### 11.2 前端（`web-frontend/src`）

| 文件 | 改动 |
|---|---|
| `modules/pod_customization/pages/PodCustomizationPage.tsx` | **重构为引用共享区块**（行为不变） |
| `modules/pod_customization/components/PodSetupColumn.tsx` | 新增（抽出）：业务字段 + 智能填写 + 高级创意 + 数量 + 开始生成 |
| `modules/pod_customization/components/PodWorkbenchHeader.tsx` | 新增（抽出）：页头与模板/历史入口（模板入口可关闭） |
| `modules/pod_customization/components/PodBatchToolbar.tsx` | 新增（抽出）：暂停/取消/继续/删除/重试 |
| `modules/pod_customization/data/usePodDraftAutosave.ts` | 新增（抽出）：草稿自动落盘 hook |
| `app/navigation/modules.ts` | 分组 `pod_workflow` + 模块 `pod_semi_customization`；两处数组登记 |
| `app/layout/WorkspaceShell.tsx` | import + `case` |
| `modules/pod_semi_customization/**` | 页面薄壳 + 款维度结果区 + api/data/草稿/styles |
| 测试 | `modules.test.ts`、`podCustomizationNavigation.test.ts` 同步；全定制既有测试全绿（抽取回归门） |

---

## 12. 测试计划

### 12.1 后端

| 测试 | 覆盖 |
|---|---|
| `tests/test_semi_batch_worker.py` | 无模板批次可跑通；不读模板图；role=`pattern_1..4`；4 格=4 款映射；不触发规格卡与标题；**不调 COS 发布（`public_url` 为空）**；两轮重试；暂停/取消/恢复；终态按图片计数；**整组重试**只重跑该组 4 格 |
| `tests/test_semi_image_request.py` | 提交体**不含 `urls`**；`reference_count=0`；轮询/下载失败分类与全定制一致 |
| `tests/test_semi_billing_contract.py` | `link_count == count`（不是 count/4）；同组 4 link 共享 image call 成败；scope/子项校验；失败时 4 link 全退 |
| `tests/test_semi_zip_export.py` | `style_001.png` 命名；只含 completed；partial_failure 断号且可下载；空结果 409；越权 404；非 semi 批次 404；**zip 内容来自本地文件**（不依赖任何公网 URL） |
| `tests/test_semi_router.py` | `count % 4 != 0` 422；带 `listing_fields` 被拒；无需 template_id |
| `tests/test_billing_batch_pricing.py`（扩展） | `pod_semi_v1` 32–38 区间与展示区间 |

### 12.2 前端

| 测试 | 风格 |
|---|---|
| `data/podSemiCustomizationModel.test.ts` | 行为断言：4 的倍数校验、款号映射、`style_001.png` 命名 |
| `data/podSemiCustomizationDraft.test.ts` | 行为断言：独立 key、账号隔离、损坏回退 |
| `pages/PodSemiCustomizationPage.test.ts` | 源码字符串断言：无模板入口、无标题/SKU/导出；含智能填写与 ZIP 下载；数量选项全为 4 的倍数 |
| `app/navigation/modules.test.ts` | 更新：`pod_workflow` 分组 + 两个子项顺序 |

运行：`node --test --experimental-strip-types <文件>`；后端 `pytest local-runtime/wh_local/modules/pod_customization/tests local-runtime/tests -k "pod or semi or billing"`。

---

## 13. 工作量与分期

| 范围 | 内容 | 备注 |
|---|---|---|
| P0-a 生图 | 无参考图提交分支 + 半定制 prompt（纯图案、四格差异化） | 速创纯文生图已确认支持；剩 prompt 样张（§15-1） |
| P0-b 链路 | 迁移 014 + mode 分支 + 占位模板 seed + 跳过模板/标题/规格卡/**COS 发布** + 整组重试 | 复用为主 |
| P0-c 计费 | 独立 profile + 按款冻结/结算 | 覆盖「同组 4 link 共享成败」 |
| P0-d 交付 | zip 端点（`style_001.png`，部分失败可下） | 依赖 `assets.path()` |
| P0-e 前端 | 二级导航 + 抽共享区块（含智能填写）+ 半定制薄壳 + 独立草稿 | 抽取须行为不变 |
| P1 | 单格重生成、无缝平铺选项、导出审计增强 | 非必需 |

---

## 14. 风险与护栏

| # | 风险 | 护栏 |
|---|---|---|
| 1 | 半定制计费误用全定制分组（按组算 link）导致少收 3/4 | 独立 profile + 独立 freeze/settle 投影 + 专项测试（§12.1） |
| 2 | 复用 worker 时误触发规格卡/标题 | role `pattern_*` + 测试断言「不写 style_titles」 |
| 3 | `count % 4 != 0` 导致最后一组只能交 1–3 格 | 契约 422 + 前端数量只给 4 的倍数 |
| 4 | 全定制链路被 mode 分支污染 | `mode` 默认 `full`；全定制既有测试全绿（回归门） |
| 5 | 导航改造导致老用户找不到全定制 | 分组 `defaultChildId = pod_customization`；全定制页不变，仅标签改「全定制」 |
| 6 | 图案里混进文字/主体，用户无法直接套版 | prompt 硬约束（§6.7）+ P0 样张验收（§15-2） |
| 7 | zip 下载越权读他人资产 | 复用 workspace + owner 归属校验（与 `router.py:356-370` 同源） |
| 8 | 批量过大导致 provider 成本失控 | `count ≤ 200`（调用 ≤ 50 次）；沿用生图窗口与分段超时 |
| 9 | 抽共享组件时改坏全定制页（表单/智能填写/草稿） | 先抽组件再改页面，抽完立刻跑三套既有测试 |
| 10 | 纯文生图没有参考图锚定，四格风格可能漂移、跨组差异不可控 | prompt 内固定「同批统一风格 + 四格必须不同」约束（§6.7）+ 用元素分配（`assign_style_elements`）与 offset 轮换控制差异度；P0 样张验收（§15-1） |
| 11 | 占位模板 seed 被误当真实模板 | `deleted_at` 非空（列表 SQL 过滤）+ `source='system'` + 半定制分支永不读取；seed 幂等 |
| 12 | 不接图床后：图只能在装本机的工作台里看，换机/分享看不了；本地磁盘随批次增长 | 符合用户明确要求（D15）；预览走本地资产端点；沿用现有资产目录与「删批次清文件」逻辑，zip 下载后可自行留存；磁盘增长列入运维观察项 |
| 13 | 半定制若被误接导出（店小秘/妙手需要公网 URL）会直接失败 | 半定制的导出只有 zip；导出端点按 `mode` 拒绝（复用「非 semi → 404」同源校验） |

---

## 15. 待确认 / 待验证

1. **半定制 prompt 初稿样张**（唯一剩余项）：确认「纯图案、无主体、无文字、白底/单色底」的实际观感、同组四格差异度与跨组差异度；确认后冻结 prompt 文案。
2. （可选）zip 内是否额外带一个批次清单文件（如 `manifest.txt`）—— 当前方案**不带**。

已确认项（留档）：
- ✅ **速创支持纯文生图**：提交体不带 `urls` 即可返回四宫格（用户 2026-09-15 确认）→ §6.5 无需兜底占位图。
- ✅ 不需要模板/参考图（D10）；不接图床（D15）；zip 命名 `style_001.png`（D11）；部分失败可下载（D12）；整组重试（D13）；保留智能填写 + 业务字段（D14）。

---

## 附录 A：全定制 vs 半定制 对照

| 维度 | 全定制 | 半定制 |
|---|---|---|
| 模板 / 参考图 | 必须有（已标定） | **无**（D10） |
| 1 次速创调用产出 | 4 张成品图（一款） | 4 张纯图案（四款） |
| 交付单元 | 款（4 图 + 1 标题） | 款（1 图） |
| 数量含义 | 款数（1 款 = 1 次调用） | 交付款数（4 的倍数；调用 = 款数/4） |
| role | `hero / detail_a / detail_b / lifestyle` | `pattern_1..pattern_4` |
| 标题 / 规格卡 / 上架字段 | 有 | 无 |
| 导出 | 店小秘 / 妙手 xlsx | zip（`style_001.png` …） |
| 重试粒度 | 单款 / 整款 | **整组**（4 款一起，D13） |
| 计费 | `pod_random_v1`，40–50/款 | `pod_semi_v1`，32–38/款 |
| 入口 | 「POD 定制 → 全定制」 | 「POD 定制 → 半定制」 |

## 附录 B：已评估并否决的路线（留档）

| 路线 | 结论 | 理由 |
|---|---|---|
| 每款各自一次速创调用、只用 1 格 | ❌ | provider 成本 ×4，其余 3 格浪费（用户否决） |
| 按速创调用次数计费 | ❌ | 与「每款 32–38 积分」不符 |
| 同页模式切换（全定制/半定制共用一页） | ❌ | 用户要求侧栏二级菜单；上架字段/导出会污染半定制 |
| 独立后端模块 `modules/pod_semi_customization` | ❌ | 生图/拆分/发布/worker 全要重写，违背「尽量复用」 |
| 新建半定制专用结果表/发布表 | ❌ | `style_grid_results` + `publications` 已有 `(style, variant)` 与自由 `role` |
| 图案抠图 / 去底 / 二值化 | ❌ | 用户明确「四宫格原图即蒙版」，不做后处理 |
| 按组分包 / 每款一个 zip / 只选部分款下载 | ❌ | 用户明确「按批次打包打到一个 zip 文件」 |
| 半定制沿用模板（哪怕让用户可选） | ❌ | 用户明确「不需要模板，按提示词生成花色」 |
| 半定制也上传 COS / 走图床拿公网地址 | ❌ | 用户明确「不要接图床，直接拿下来放本地」（D15）；且半定制不导出 xlsx，公网 URL 无用途 |
| **重建 `batches` 表把模板列改可空**（T1 备选） | ⏸ | 需在事务外 `PRAGMA foreign_keys=OFF`（事务内为 no-op）+ 子表 FK 级联风险 + 崩溃安全回归成本；P1 若清理技术债再评估 |
| 新增半定制专用生图函数（而非给现有函数加可选参考图） | ❌ | 轮询/下载/错误分类会分叉两份 |

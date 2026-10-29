# POD 自由定制（用户自带提示词）设计方案

状态：**客户端 P0 已实现，待服务端画像联调**（2026-10-29）
- 已完成：契约/模型目录/迁移 030、Repository、两阶段计费计划、Service/Router、Worker 单图链路、导出映射、前端自由定制页与导航。
- 待服务端：`pod_free_flash_v1` / `pod_free_pro_v1` 白名单、倍率分类、`pod_modules` 34/72、`ark` key 下发。
- 验证：客户端 `pytest` 自由定制 7 项通过；相关 POD 套件 120 项通过（`test_same_batch_title_generation_serializes_accepted_titles` 为既有并发时序 flake，单文件运行稳定通过）；前端 `npm run build` 与 `npm test` 通过。

> **2026-10-29 交互改版（以本节为准）**
> - 模型切换移到二级大标题右侧；左栏顶部改为当前模型介绍。
> - 张数在建批时直接选 4/5/6，右侧立即出现对应占位框；首个主图高亮。
> - 改成**单槽位逐张生成/重生成**：点任意未生成槽位 → 左侧提示词切换 → 生成完自动占位并高亮下一张；重生成替换旧图并按新一次生图计费。
> - 标题等**所有槽位出完**后再生成；全部出完才开放标题与店小秘导出信息编辑。
> - 参考图只对主图生效；第 2~N 张固定以主图为主要参考。
> - 后端对应接口：`POST /free/batches/{id}/panels/{variant}/generate`，一次只冻结 1 张。

目标读者：POD 前后端实现者、计费/服务端对接人、验收人
目标基线：`local-runtime/wh_local/modules/pod_customization/`、`web-frontend/src/modules/pod_*`

---

## 0. 一句话

在「POD 定制」下新增**自由定制**：我们只提供生图接口、模型选择、参考图上传、批次/标题/导出底座，**生图提示词一个字都不内置**，全部由用户逐张填写；一批次先出第 1 张，再由用户选择做到 4~6 张，后续每张单独写提示词并**默认以第 1 张为参考图**；模型支持 Seedream 5.0 **Flash / Pro** 切换，两档走独立计费画像；标题可选生成且不收费，图片完成后复用全定制的店小秘/妙手导出链路。

---

## 1. 背景与目标

### 1.1 目标

| 编号 | 目标 |
|---|---|
| R1 | 侧栏「POD 定制」下新增第三个子项「自由定制」，与全定制 / 半定制 / 爆款复刻 / 爆款裂变并列 |
| R2 | **生图提示词零内置**：提交给模型的 prompt 就是用户输入原文，不加前缀、不加后缀、不加风格模板、不加硬锁、不自动翻译 |
| R3 | 一次提交 = 一个产品批次；**第 1 张单独生成**，第 1 张成功后再由用户选择本批总图数（4 / 5 / 6） |
| R4 | 后续第 2~N 张**每张一条独立提示词**，默认统一以**第 1 张成品图**作为参考图，保证同一产品的一致性 |
| R5 | 模型可选 **Seedream 5.0 Flash / Pro**；同一批次模型冻结，不支持中途切换；两档单价不同 |
| R6 | 参考图可选上传 **0~4 张**，只作用于第 1 张；第 2~N 张默认只参考第 1 张 |
| R7 | 标题**可选**：生图前勾选是否同步生成；勾选后在第 1 张成功后按全定制标题链路产出；**标题不收费** |
| R8 | 图片完成后可**编辑店小秘导出信息**（类目 / SKU / 申报价 / 重量 / 尺寸），再复用全定制导出为店小秘或妙手表格 |
| R9 | 图片交付：本地预览 + 单张下载 + 整批 ZIP；导出需要 COS 公网地址的部分复用全定制发布链路 |
| R10 | 不新增独立后端模块：复用 `pod_customization` 的 worker / 资产 / 计费 / 标题 / 导出底座，只新增 `mode=free` 分支和自由定制专属的少量表与端点 |

### 1.2 非目标

- ❌ 不做智能填写、不做豆包改写提示词、不做构图模板、不做批级美术指导。
- ❌ 不做四宫格母图与本地拆图：一次调用只出 1 张成品图。
- ❌ 不内置任何风格、质量、负面、文字禁令等提示词（包括「no text / no watermark」这类系统后缀）。
- ❌ 不在自由定制里生成规格卡图，也不做规格卡合成；尺寸信息只进表格。
- ❌ 不允许同一批次混用 Flash 与 Pro，也不允许中途改模型。
- ❌ 不把自由定制做成「一条提示词重复出 N 张」；N 张必须各自有提示词。
- ❌ 不新建独立计费引擎；只在现有冻结/结算通道上新增画像。

---

## 2. 已确认决策

| 编号 | 决策 | 来源 |
|---|---|---|
| D1 | 粒度：一条提示词只出 1 张，不做四宫格 | 2026-10-29 用户确认 |
| D2 | 一批次最多 6 张；第 1 张出来后再选 4~6，并为第 2~N 张逐张写提示词 | 2026-10-29 用户确认 |
| D3 | 第 2~N 张默认统一用第 1 张做参考图 | 2026-10-29 用户确认 |
| D4 | 参考图可选上传 0~4 张 | 2026-10-29 用户确认 |
| D5 | Flash / Pro 走两个独立计费画像，具体单价待运营确认 | 2026-10-29 用户确认 |
| D6 | 接入全定制后续导出代码 | 2026-10-29 用户确认 |
| D7 | 生图前让用户选是否同步生成标题；标题不收费 | 2026-10-29 用户确认 |
| D8 | 导出前让用户编辑完店小秘导出信息，再导出为表格 | 2026-10-29 用户确认 |
| D9 | 生图提示词零内置，全部用户填写 | 2026-10-29 用户确认 |

---

## 3. 术语与数量口径

| 术语 | 定义 | 对应现有概念 |
|---|---|---|
| 自由批次 | 一个产品的一次制作 | `pod_customization_batches` |
| 第 1 张 | 主图；用户建批时输入提示词与可选参考图 | `variant_index = 1`，角色 `hero` |
| 第 N 张 | 后续图；每张一条提示词，参考第 1 张 | `variant_index = 2..6` |
| 款 | 自由定制一个批次只有 1 个款（一个产品） | `style_index = 1` |
| 图片调用 | 一张图 = 一次 provider 调用 = 一个计费 link | `pod.image` |
| 标题 | 一个批次一条标题，零计费 | `pod.title` |

数量口径：

- 阶段一：1 张。
- 阶段二：用户选 `N ∈ {4, 5, 6}`，且 `N > 当前已出图数`；实际新增 `N - 1` 张。
- 单批最多 6 次生图调用，最多 1 条标题。
- `requested_count` 在批次行里固定为 `1`（一个款），图片张数另存 `generation_meta_json.free_target_count`，避免影响导出的「款」循环。

---

## 4. 已核实事实（含依据）

| 事实 | 依据 |
|---|---|
| POD 生图模型目录当前是速创 2 个 + 豆包 Flash 1 个，全局只有一个模型下拉 | [catalog.py](../../../local-runtime/wh_local/modules/pod_customization/image_providers/catalog.py#L26-L33) |
| 豆包模型走方舟同步接口，支持 base64 参考图、多图数组、`size` 档位或显式宽高 | [doubao.py](../../../local-runtime/wh_local/modules/pod_customization/image_providers/doubao.py#L1-L50) |
| 方舟方形母图当前统一映射为 `2048x2048`，像素上限常量 `4_624_220` | [doubao.py](../../../local-runtime/wh_local/modules/pod_customization/image_providers/doubao.py#L33-L47) |
| 模型在调用时按全局配置解析，不在批次上冻结 | [ai_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/ai_runtime.py#L122-L182)、[ai_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/ai_runtime.py#L340-L367) |
| 生图请求契约 `DirectListingGridRequest` 目前没有模型字段 | [runtime_contracts.py](../../../local-runtime/wh_local/modules/pod_customization/runtime_contracts.py#L54-L66) |
| 半定制以 `mode=semi` 复用批次、跳过模板与标题，`_style_generation_inputs` 有独立分支 | [contracts.py](../../../local-runtime/wh_local/modules/pod_customization/contracts.py#L430-L450)、[worker.py](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1630-L1638) |
| 半定制用惰性占位模板满足 `batches` 模板列 NOT NULL + FK | [repository.py](../../../local-runtime/wh_local/modules/pod_customization/repository.py#L1113-L1155) |
| 批次 `mode` 列已存在，历史默认 `full` | [014_semi_customization.sql](../../../local-runtime/wh_local/modules/pod_customization/migrations/014_semi_customization.sql) |
| 图片结果按 `(batch_id, style_index, variant_index)` 唯一 | [003_style_grid_v2.sql](../../../local-runtime/wh_local/modules/pod_customization/migrations/003_style_grid_v2.sql) |
| 标题只在 `role == "lifestyle"` 发布成功后触发 | [worker.py](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L3513-L3543) |
| 标题请求 = 主图 + 业务字段 + 创意提示词；业务字段可为空但会影响准确性 | [title_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/title_runtime.py#L185-L195)、[title_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/title_runtime.py#L538-L620) |
| 店小秘/妙手导出要求每个款恰好 4 个角色，且公网 HTTPS 地址齐全 | [export.py](../../../local-runtime/wh_local/modules/pod_customization/export.py#L246-L258)、[export.py](../../../local-runtime/wh_local/modules/pod_customization/export.py#L446-L508) |
| 店小秘列定义固定，轮播图 / 产品素材图分别取全部图片与 hero | [dianxiaomi.py](../../../local-runtime/wh_local/modules/pod_customization/dianxiaomi.py#L8-L60) |
| 计费画像决定单价；半定制 `pod_semi_v1` 32 积分/组（4 款） | [billing_contract.py](../../../local-runtime/wh_local/modules/pod_customization/billing_contract.py#L28-L59)、[billing.py](../../../local-runtime/wh_local/billing.py#L33-L41) |
| 结算要求上报的 outcome 与冻结计划**逐调用完全一致** | [billing_contract.py](../../../local-runtime/wh_local/modules/pod_customization/billing_contract.py#L1066-L1104) |
| POD 批次进度 / 终态按「款」统计，半定制另行按张计数 | [service.py](../../../local-runtime/wh_local/modules/pod_customization/service.py#L3960-L4005) |
| 前端 POD 分组已有 4 个子项，导航与页面模块需两处登记 | [modules.ts](../../../web-frontend/src/app/navigation/modules.ts#L119-L133)、[modules.ts](../../../web-frontend/src/app/navigation/modules.ts#L317-L324) |

---

## 5. 产品流程

### 5.1 阶段一：生成第 1 张

用户在自由定制页填写：

1. **模型**：Flash / Pro 二选一（同批冻结）。
2. **提示词**：必填，1~4000 字，原样提交。
3. **参考图**：可选 0~4 张（JPEG/PNG/WEBP，单张 ≤ 10 MB）。
4. **是否同步生成标题**：开关，默认开。
5. **标题参考信息（可选）**：产品名称 / 品类 / 目标市场 / 人群 / 卖点 / 文案限制。只用于标题与导出，**绝不进入生图提示词**。
6. **批次名（可选）**：为空时取提示词前 120 字。

点击「生成第 1 张」：

```
创建批次（mode=free, style_index=1, variant=1）
  → 冻结 1 个图片 link（+ 标题调用，若开启）
  → worker 调方舟一次，出 1 张
  → 落本地资产 + 发布 COS（导出需要）
  → 第 1 张成功后，若开启标题，生成 1 条标题（零计费）
  → 批次进入「已完成第 1 张 / 等待继续」状态
```

### 5.2 阶段二：选择 4~6 张并逐张写提示词

第 1 张成功后，页面解锁「继续生成」区：

1. 单选本批总图数：**4 / 5 / 6**（小于 4 不给选，因为导出至少需要 4 张固定角色）。
2. 为第 2~N 张分别填写提示词（每张一个文本框，全部必填）。
3. 页面明示：第 2~N 张默认以第 1 张为参考图，用户提示词原样提交。
4. 点击「生成剩余 N-1 张」：
   - 冻结 `N-1` 个图片 link（与阶段一同画像）；
   - 插入第 2~N 张的提示词与结果占位；
   - worker 按变体逐个生成，单张失败只影响该张；
   - 全部收敛后批次进入 `completed` / `partial_failure`。

### 5.3 状态机（不新增批次状态）

| 时点 | 批次状态 | 判定 |
|---|---|---|
| 阶段一排队 / 生成 | `queued` / `running` / `compositing` | 复用现有状态 |
| 阶段一完成、未继续 | `completed` | 已有 1 张；`free_target_count == 0` |
| 用户点「继续生成」 | 新 epoch → `queued` → `running` | 复用 resume 的 fencing 机制 |
| 阶段二完成 | `completed` / `partial_failure` / `failed` | 复用现有终态 |

> 刻意不新增 `awaiting_panels` 状态：仓库里大量 SQL 按终态/非终态判断（见 [repository.py](../../../local-runtime/wh_local/modules/pod_customization/repository.py#L3645-L3719)），新增状态会牵连清扫、结算、导出与补跑。用 `generation_meta_json.free_target_count` 表达「可继续」，对现有状态机零侵入。

---

## 6. 提示词与参考图语义

### 6.1 生图提示词零内置

| 项 | 规则 |
|---|---|
| 提交内容 | `prompt` 字段 = 用户输入原文，逐字提交 |
| 不加前缀/后缀 | 不拼「产品名称：」「SUPER IMPORTANT」等任何系统文案 |
| 不加硬锁 | 不注入几何锁、满印锁、文字禁令、配色规则、相机指令 |
| 不改写/翻译 | 用户写中文就发中文，用户写英文就发英文 |
| 不自动补负面词 | 不追加 `no text, no watermark` 等 |
| 唯一例外 | `sanitize_prompt_for_ark` 只处理历史 v1 标记；自由提示词不含该标记，实际等于原文直通（[doubao.py](../../../local-runtime/wh_local/modules/pod_customization/image_providers/doubao.py#L272-L293)） |

这是模块的核心卖点，也是主要风险：画面质量、产品一致性、合规审核全部由用户提示词和模型自身审核负责。方案不做任何「偷偷补一句」的兜底。

### 6.2 参考图

| 图 | 参考图来源 | 说明 |
|---|---|---|
| 第 1 张 | 用户上传 0~4 张 | 走方舟 `image` 字段：单图字符串、多图数组；base64 data URL 内嵌，不要求先上 COS |
| 第 2~N 张 | 第 1 张成品图（固定） | 单图参考；用于锁定同一产品/主体 |
| 后续扩展（P1） | 允许用户改选其它已出图 | P0 不开放，避免「参考链漂移」 |

约束：

- 上传的参考图只属于本批次，校验 workspace + owner。
- 参考图不是模板：不读取结构事实、不做多视图、不做标定。
- 若上游对参考图数量/格式报 4xx，按 `non_retryable_4xx` 失败并退款，错误原文回显给用户。

### 6.3 模型与分辨率

| 模型 | 目录值（建议） | 展示名 | 平台 | 说明 |
|---|---|---|---|---|
| Seedream 5.0 Flash | `doubao-seedream-5-0-flash-260915` | Seedream 5.0 Flash | 豆包 / ark | 快、便宜；当前 POD 默认模型 |
| Seedream 5.0 Pro | `doubao-seedream-5-0-pro-260628` | Seedream 5.0 Pro | 豆包 / ark | 质量更高；**上线前必须在方舟控制台核对实际模型 ID** |

规则：

1. **Pro 只对自由定制开放**：全局「POD 生图模型」下拉仍只展示现有 3 项（速创 2 + Flash），不把 Pro 暴露给全定制/半定制/复刻/裂变，避免出现「用 Pro 出图却按 Flash 画像计费」的漏洞。
2. 自由定制的模型在**建批时冻结**到 `generation_meta_json.image_model`，整批所有图片都用它。
3. 每批只允许 ark 平台（Flash/Pro 都是豆包），因此冻结时必须拿到 `ark` provider key；拿不到就按现有机制暂停等授权。
4. 分辨率 P0 固定方阵 `2048×2048`，复用 `ark_size`；Pro 的 4K 先不做：
   - 现有 `ARK_MAX_TOTAL_PIXELS = 4_624_220` 会拦住 4096×4096；
   - 4K 需要单独实测方舟档位、耗时与计费，放 P1。
5. 超时：Flash 沿用 180s；Pro 建议 240s（以实测为准），只对 Pro 生效。

模型 ID 与能力来源见附录 B。

---

## 7. 数据模型与契约

### 7.1 迁移 030：自由定制提示词表

只新增一张表，不改 `batches` 列（`mode` 已有）：

```sql
-- 030_free_customization.sql
CREATE TABLE IF NOT EXISTS pod_customization_free_panels (
    batch_id TEXT NOT NULL,
    variant_index INTEGER NOT NULL,          -- 1..6
    prompt TEXT NOT NULL,                    -- 用户原文，不存任何拼装结果
    reference_asset_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, variant_index),
    FOREIGN KEY (batch_id) REFERENCES pod_customization_batches (batch_id) ON DELETE CASCADE
);
```

- 图片状态、资产、公网地址继续写 `pod_customization_style_grid_results` + `publications`：
  - `style_index = 1` 固定；
  - `variant_index = 1..N`；
  - 角色映射见 §11.1。
- 不在 `free_panels` 里冗余状态，避免两处状态漂移。
- 批次级配置写 `generation_meta_json`：
  ```json
  {
    "image_model": "doubao-seedream-5-0-pro-260628",
    "title_enabled": true,
    "free_target_count": 0,
    "free_stage": "initial"
  }
  ```
  `free_target_count` 在阶段二提交时更新为 4/5/6。

### 7.2 建批契约（阶段一）

```jsonc
// POST /api/pod-customization/free/batches
{
  "client_request_id": "uuid",          // 幂等键
  "image_model": "doubao-seedream-5-0-flash-260915",
  "prompt": "用户原文……",
  "reference_asset_ids": ["asset_x", "asset_y"],  // 0..4
  "title_enabled": true,
  "business_fields": {                  // 可选，只给标题/导出用
    "product_name": "帆布托特包",
    "product_category": "女士手提包"
  },
  "title": ""                           // 批次名，可选
}
```

校验：

| 字段 | 规则 |
|---|---|
| `prompt` | 去首尾空白后 1~4000 字；空白拒绝 |
| `image_model` | 只允许 `FREE_IMAGE_MODELS` 两个值 |
| `reference_asset_ids` | 0~4 个，去重，必须属于当前 workspace/owner |
| `title_enabled` | bool |
| `client_request_id` | 同 actor/workspace 内幂等；重复提交返回原批次 |

### 7.3 继续生成契约（阶段二）

```jsonc
// POST /api/pod-customization/free/batches/{batch_id}/panels
{
  "target_image_count": 6,
  "panels": [
    { "variant_index": 2, "prompt": "第二张的提示词……" },
    { "variant_index": 3, "prompt": "第三张的提示词……" },
    { "variant_index": 4, "prompt": "第四张的提示词……" },
    { "variant_index": 5, "prompt": "第五张的提示词……" },
    { "variant_index": 6, "prompt": "第六张的提示词……" }
  ]
}
```

校验：

- 批次 `mode=free` 且第 1 张 `completed`；否则 409。
- `target_image_count ∈ {4,5,6}`，且大于当前已存在的最大变体号。
- `panels` 必须恰好覆盖 `2..target_image_count`，不能重复、不能跳号、不能少。
- 每条 prompt 1~4000 字。
- 一旦提交不可减少张数；单张失败可重试（§8.3）。
- 阶段二只允许提交一次；重复提交返回 409（幂等键同样防重）。

### 7.4 批次 payload（复用 `_batch_payload`，新增字段）

```jsonc
{
  "id": "bat_x",
  "mode": "free",
  "status": "completed",
  "image_model": "doubao-seedream-5-0-pro-260628",
  "title_enabled": true,
  "target_image_count": 6,        // 阶段一未继续时为 0
  "completed_image_count": 4,
  "items": [
    {
      "variant_index": 1,
      "style_index": 1,
      "role": "hero",
      "status": "completed",
      "prompt": "用户第 1 张原文",
      "pattern_preview_url": "/api/pod-customization/assets/{asset_id}",
      "public_url": "https://...",       // 发布失败时为空
      "publish_status": "published",     // published | failed | pending
      "error_message": ""
    }
    // … 2..N 张
  ],
  "title": {
    "status": "completed",
    "title": "…",
    "english_title": "…",
    "description": "…",
    "source": "ai"                      // ai | manual
  },
  "export": { "ready": false, "block_reason": "…" }
}
```

### 7.5 API 一览

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/free/images` | 上传参考图，返回 asset_id / 尺寸 / 本地预览 URL |
| `POST` | `/free/batches` | 阶段一：建批 + 冻结 1 张 + 入队 |
| `GET` | `/free/batches` | 历史列表（`mode=free`） |
| `GET` | `/free/batches/{id}` | 批次详情 |
| `POST` | `/free/batches/{id}/panels` | 阶段二：选择 4~6 张 + 逐张提示词 + 冻结 N-1 张 |
| `POST` | `/free/batches/{id}/panels/{variant}/retry` | 单张重试（新冻结，只跑该张） |
| `POST` | `/free/batches/{id}/cancel` | 取消未开始的调用 |
| `DELETE` | `/free/batches/{id}` | 删除批次（清理本地资产） |
| `PUT` | `/free/batches/{id}/listing-fields` | 保存店小秘导出信息 |
| `PATCH` | `/batches/{id}/styles/1/title` | 复用全定制：手动改标题/文案 |
| `POST` | `/batches/{id}/styles/1/title/regenerate` | 复用全定制：重新生成标题（零计费） |
| `GET` | `/free/batches/{id}/download` | 整批 ZIP |
| `GET` | `/batches/{id}/exports/dianxiaomi` | 复用全定制导出 |
| `GET` | `/batches/{id}/exports/miaoshou` | 复用全定制导出 |

`PUT listing-fields` 复用现有 `ListingFields` 模型（[contracts.py](../../../local-runtime/wh_local/modules/pod_customization/contracts.py#L368-L381)）；建批时不要求填，导出前补齐即可。

---

## 8. 执行链路（复用 worker，新增 free 分支）

### 8.1 阶段一

```
service.create_free_batch
  → ensure_semi_placeholder（复用惰性占位模板，或新增中性别名）
  → PodCallPlan.for_free_panels([1], title_enabled=…, profile=flash/pro)
  → repository.create_free_batch(mode="free")：批次行 + style_grid_results(1,1) + free_panels(1)
  → worker.submit

worker._stream_free_panels(batch, variants=[1])
  → prompt = free_panels[1].prompt          // 原文
  → references = 上传的 0~4 张
  → request.model = batch.image_model
  → 调方舟 1 次 → 1 张
  → 落本地资产 → 发布 COS（失败不判图失败，只记 publish_status=failed）
  → 写 style_grid_results(style=1, variant=1, role=hero)
  → 若 title_enabled：claim_style_title + _generate_style_title(hero)
  → 结算（成功扣 1 link，标题零计费）
```

### 8.2 阶段二

```
service.continue_free_batch
  → 校验第 1 张完成、target 4..6、panels 覆盖 2..N
  → 插入 free_panels(2..N) + style_grid_results(1, 2..N)（同一事务）
  → PodCallPlan.for_free_panels([2..N], title_enabled=False, profile 同批)
  → 开新 epoch，批次状态 queued，worker.submit

worker._stream_free_panels(batch, variants=[2..N])
  → 逐张读 prompt
  → 参考图 = 第 1 张成品资产（单张）
  → 调方舟 → 落资产 → 发布 COS → 写结果
  → 全部收敛后按现有逻辑结算并置终态
```

P0 建议**逐张串行**（最多 5 次），不引入新的并发池：

- 单批最多 6 张，串行实现的重启/计费/断点最简单；
- 每张失败独立重试 1 次（共 2 次尝试），与现有 POD 口径一致；
- 后续若需要提速，再把 `[2..N]` 放进现有 `image_workers` 窗口。

### 8.3 单张重试

- 只重置该 `variant` 的 `style_grid_results` 为 `queued`，清空错误与旧公网地址；
- 新建一个只含该变体的 `PodCallPlan`，call id 带 `:variant:{v}:retry:{uuid}`；
- worker 只跑该变体，成功后覆盖同一结果行；
- 不影响其它已成功图片，也不重复扣费。

### 8.4 与现有链路的隔离点

| 位置 | 改动 | 风险控制 |
|---|---|---|
| `_style_generation_inputs` | 新增 `mode=="free"` 分支：返回用户原文 + 参考图 | 放在 semi 分支之前/之后均可，不影响 full/semi/replica |
| `_process_style_grids` | 不进入 free（free 走新 `_process_free_panel`），避免四格角色假设 | 现有函数零改动 |
| 标题触发 | free 在第 1 张成功时触发；不复用 `role=="lifestyle"` 条件 | 不动全定制触发逻辑 |
| 规格卡 | free 不调用 `_spec_card_publish_media` | 全定制行为不变 |
| 模型解析 | `DirectListingGridRequest` 新增可选 `model`，空值回落全局 | 全定制/semi/replica 不传，行为不变 |
| 批次终态 | 复用 `completed`，靠 meta 判断可继续 | 不改状态枚举与清扫 SQL |

---

## 9. 计费

### 9.1 画像与单价

| 画像 | 用途 | 计费单元 | 建议基准（**待运营确认**） |
|---|---|---|---|
| `pod_free_flash_v1` | 自由定制 · Flash | 1 张 = 1 link | **34 积分/张** |
| `pod_free_pro_v1` | 自由定制 · Pro | 1 张 = 1 link | **72 积分/张** |

> 平台兑换口径：**1 元 = 100 积分**（[TOPUP_PROMOTION_INTERNAL_TEST.md](../../TOPUP_PROMOTION_INTERNAL_TEST.md#L7)），所以 1 积分 = 0.01 元。以下成本都换算成积分。

### 9.1.1 上游成本拆解

官方口径（Flash / Pro）：

| 项 | Flash | Pro |
|---|---|---|
| 输出一张 | 0.12 元 = **12 积分** | 0.30 元 = **30 积分** |
| 输入第 1 张 | 免费 | 免费 |
| 输入第 2 张起 | 0.02 元/张 = **2 积分/张** | 同左 |
| 标题（doubao-seed-2.1-lite） | 约 0.01~0.015 元/次 ≈ **1~1.5 积分** | 同左 |

按我们这条链路估算单张综合成本：

| 成本项 | Flash | Pro | 说明 |
|---|---|---|---|
| 输出 | 12 | 30 | 一次调用一张 |
| 参考图输入 | 0~6 | 0~6 | 第 1 张免费；第 2~4 张各 2 积分；第 2~N 张只参考第 1 张，输入为 0 |
| 标题摊销 | 0.3~1.5 | 0.3~1.5 | 1 条标题/批；4 张批摊 0.3~0.5，只出 1 张时按 1.5 兜底 |
| 重试/波动预算 | 1.5 | 3.5 | 约 10%，覆盖失败重试与上游抖动 |
| **单张综合成本** | **约 14~20** | **约 34~41** | 参考图取 0~3 张额外输入 |

定价建议：**Flash 34 / Pro 72**（2026-10-29 用户确认）。

| 指标 | Flash 34 | Pro 72 |
|---|---|---|
| 对比综合成本 | 14~20 | 34~41 |
| 成本倍数 | 1.7~2.4× | 1.8~2.1× |
| 毛利率 | 约 41%~59% | 约 43%~53% |
| 4 张批总价 | 136 积分 | 288 积分 |
| 与 Pro 价格比 | Pro : Flash = 2.12 : 1，低于输出成本 2.5 : 1 | Pro 毛利略薄于 Flash |

关键结论：

- **原来的 8 / 12 积分是亏的**：Flash 输出成本就已经 12 积分，8 积分卖一张等于每张倒贴 4 积分。该数值必须废弃。
- 标题不单独收费，成本摊进每张单价。一个批次只有 1 条标题，4 张批摊薄后不到 0.5 积分/张；即使只出 1 张，34/72 的毛利也覆盖得住。
- 第 2~4 张参考图最多增加 6 积分成本，**建议平台吸收、不单列加价**；如果运营要求精确传导，可加「第 2 张起每张参考图 +2 积分」，但这会让 UI 和客诉变复杂，不推荐 P0。
- 毛利护栏：**Flash 不低于 30、Pro 不低于 72**。当前 Pro 72 已是走量底线，再低会在 4 张参考图 + 标题重试的场景下打平甚至亏损。
- 套餐赠送稀释：999 元套餐赠送 100% 积分，有效积分成本约 0.5 分/积分。34/72 在该档 Flash 平均约 6% 毛利、Pro 平均约 -3%（基本打平到略亏）；若大套餐是主力，Pro 建议 80+；要求所有套餐都保 25%+，需上到 **Flash 43 / Pro 100** 左右。不建议超过 Pro 110，否则单张价格贴近复刻整款（100 积分/款四图），用户会直接转去复刻。
- 客户端**不写死金额**：页面只读服务端 `pricing.pod_modules` 的 `charge_min_points / charge_max_points`，缺失就隐藏价格并置灰入口。

标题：**0 积分**。阶段一的冻结计划里会预留 5 条 `pod.title` 调用；服务端对 POD 画像的纯 title 子项必须继续按 0 计价。

### 9.2 上报通道：统一接口，只换画像

**不新增冻结 / 结算端点。** 客户端复用现有 POD 计费通道，服务端只按 `billing_profile` 区分价格：

| 契约 | 冻结 | 结算 | 自由定制的差异 |
|---|---|---|---|
| v1（默认） | `POST /api/customer/billing/batch/freeze` | `POST /api/customer/billing/batch/settle` | `billing_profile` = `pod_free_flash_v1` / `pod_free_pro_v1` |
| v2（`WH_LOCAL_BILLING_CONTRACT_VERSION=v2`） | `POST /api/customer/billing/v2/pod/freezes` | `POST .../freezes/{id}/settle` | `module=pod_free`、`unit_kind=panel`、`scope` 对象、`links[]` |

v1 阶段一冻结（标题开启）：

```jsonc
{
  "idempotency_key": "pod:free-batch:{batch_id}:initial",
  "link_count": 1,
  "item_count": 1,
  "scope": ["title", "four_grid"],
  "billing_profile": "pod_free_flash_v1"
}
```

v1 阶段二冻结（例如本批 4 张，新增 3 张）：

```jsonc
{
  "idempotency_key": "pod:free-batch:{batch_id}:panels:{uuid}",
  "link_count": 3,
  "item_count": 3,
  "scope": ["four_grid"],
  "billing_profile": "pod_free_flash_v1"
}
```

v1 结算（每张一条 link）：

```jsonc
{
  "freeze_id": "frz_x",
  "items": [
    {
      "link_idx": 1,
      "subitems": [
        { "feature": "title", "status": "success" },      // 阶段一才有；零计费
        { "feature": "four_grid", "status": "success" }    // 图片；成功才扣 34/72
      ]
    }
  ]
}
```

v2 形状由客户端 `build_v2_freeze_payload` / `build_v2_settlement_payload` 生成（[remote_billing.py](../../../local-runtime/wh_local/modules/pod_customization/remote_billing.py#L53-L104)）：

```jsonc
// freeze
{
  "idempotency_key": "pod:free-batch:{batch_id}:initial",
  "module": "pod_free",
  "billing_profile": "pod_free_pro_v1",
  "unit_kind": "panel",
  "link_count": 1,
  "scope": { "image": true, "title": true },
  "unit_ids": ["panel-0"],
  "item_count": 1
}

// settle
{
  "idempotency_key": "frz_x:settle",
  "links": [
    {
      "link_idx": 0,
      "unit_id": "panel-0",
      "subitems": [
        { "feature": "image", "status": "success" },
        { "feature": "title", "status": "success" }
      ]
    }
  ]
}
```

关键约定：

1. **`item_count == link_count`**。不要套用半定制的 `item_count == link_count × 4` 校验。
2. 阶段一、阶段二是**两个独立冻结**；各自幂等键不同，各自结算，互不影响。
3. 图片子项 v1 叫 `four_grid`、v2 叫 `image`；自由定制里它是「一张图」的计费占位名，不表示四宫格。
4. 标题子项永远 0 积分；图片子项按画像单价 34 / 72 计费。
5. 结算要求上报 link 数与冻结一致、每条 link 的子项覆盖冻结 scope；否则服务端会拒绝。

### 9.3 冻结与结算口径

| 阶段 | `link_count` | `scope` | 说明 |
|---|---|---|---|
| 阶段一 | 1 | 标题开启：`["title","four_grid"]`；关闭：`["four_grid"]` | 1 张图 + 可选标题 |
| 阶段二 | `N-1` | `["four_grid"]` | 只跑图片，标题不重复 |

- **1 张图 = 1 个 link**，不按组、不按批次。
- 同一批两阶段使用同一画像（模型冻结）。
- 阶段一标题调用全部挂在唯一 link 上，因此不会出现「其它 link 必须补 title 子项」的校验问题。
- 失败：图片两次尝试都失败 → 该 link `no_return`，全额退；标题失败不影响图片扣费。
- 续跑/重试：每次新冻结独立结算，沿用现有「失败也退款、无重试溢价」口径。

### 9.4 `PodCallPlan` 需要的扩展

为避免把「变体」误当「款」分组，`PodCallPlan` 增加：

```python
free_variants: tuple[int, ...] = ()
```

- `module = "pod_free"`；
- `unit_kind = "panel"`；
- `unit_ids = ("panel-0", …)`；
- `_product_batch_groups()` 在 `free_variants` 非空时按 call id 里的 `:variant:{n}:` 分组，而不是 `:style:{n}:`；
- `freeze_payload` 的 `link_count` = 本计划变体数；
- `settlement_payload` 每个变体一条 link：任一图片尝试成功即 `four_grid=success`；阶段一若 scope 含 `title`，同一 link 再报 `title` 状态。

call id 约定：

```
图片：{batch}:style:1:variant:{v}:image:{attempt}
标题：{batch}:style:1:title:{attempt}
重试：{batch}:style:1:variant:{v}:retry:{uuid}:image:{attempt}
```

> `:style:1:` 保留是为了让现有 `_title_call_ids` 与 `_generate_style_title` 直接复用；变体分组由新增的 `free_variants` 分支负责。

### 9.5 服务端对接清单（不在本工作区实现）

> 按项目边界，服务端实现不由本工作区修改；以下是客户端上线前置条件，需转交服务端维护者。

1. `billing_profile` 白名单加入 `pod_free_flash_v1` / `pod_free_pro_v1`（冻结、结算通道都要放行）。
2. `_multiplier_category()` 在 `startswith("pod")` 兜底**之前**精确匹配两个新画像，分别归入 `pod_free_flash` / `pod_free_pro`；同步加入 `MULTIPLIER_CATEGORIES`。
3. `pricing.pod_modules` 增加两条：
   ```jsonc
   { "key": "pod_free_flash_v1", "label": "POD 自由定制 · Flash", "unit_label": "张", "charge_min_points": 34, "charge_max_points": 34, "base_charge_min_points": 34, "base_charge_max_points": 34, "multiplier_percent": 100 }
   { "key": "pod_free_pro_v1",   "label": "POD 自由定制 · Pro",   "unit_label": "张", "charge_min_points": 72, "charge_max_points": 72, "base_charge_min_points": 72, "base_charge_max_points": 72, "multiplier_percent": 100 }
   ```
   数值仅为建议，最终由运营配置。
4. 冻结回执必须下发 `ark` provider key（自由定制全部走豆包；只有 `wuyin` 会直接卡在「授权不可用」）。
5. 新画像下的 `pod.title` 子项保持零计费。
6. 管理端「价格倍率」新增两张卡（Flash / Pro），允许独立调价；调价只影响之后的新冻结。
7. 老客户端不认识新画像 → 不请求 → 行为不变；新客户端在 `pod_modules` 缺少两条时自动隐藏/置灰入口（§12.1）。

---

## 10. 标题

| 项 | 规则 |
|---|---|
| 开关时机 | 建批（生图前）选择；默认开 |
| 触发时点 | 第 1 张成功且已发布（或本地完成）后 |
| 输入 | 第 1 张图 + 可选 business_fields + 用户提示词原文 |
| 提示词 | 复用全定制 `pod-title-v1` 固定契约；这是标题链路，不属于「生图提示词零内置」范围 |
| 输出 | title / english_title / description / visual_theme / keywords，与全定制一致 |
| 修改 | 导出前可手动改标题/文案，复用 `PATCH /batches/{id}/styles/1/title` |
| 重生成 | 复用 `POST /batches/{id}/styles/1/title/regenerate`，零计费 |
| 失败 | 不阻塞图片；用户可手动填文案或重试 |
| 关闭时 | 不预留标题调用，不生成；导出前必须手动填完整文案 |

`business_fields` 全部可空；为空时标题模型只靠图推断，准确性会下降，页面需提示「填写产品名称/品类可提升标题质量」。

---

## 11. 导出与交付

### 11.1 图片角色映射

| variant | 角色 | 用途 |
|---|---|---|
| 1 | `hero` | 产品素材图 / 主图 |
| 2 | `detail_a` | 轮播图 / 描述 |
| 3 | `detail_b` | 轮播图 / 描述 |
| 4 | `lifestyle` | 预览图 / 轮播图 / 描述 |
| 5 | `extra_a` | 仅追加到轮播图与描述 |
| 6 | `extra_b` | 仅追加到轮播图与描述 |

- 导出硬性要求：variant 1~4 全部 `completed` 且有公网 HTTPS 地址；variant 5~6 可选。
- 缺 1~4 任意一张 → 导出按钮禁用，提示「至少完成并发布主图 + 三张后续图」。
- 5~6 张不会新增表格列，只拼进「轮播图」与描述 HTML，保持店小秘模板不变。

### 11.2 复用全定制导出

| 项 | 做法 |
|---|---|
| 可导出判定 | 复用 `analyze_dianxiaomi_export`，但 `_style_images` 增加 free 分支（按 variant → role 映射，允许 5~6 张） |
| 行组装 | 复用 `_build_row`；新增可选参数 `extra_image_urls`，只有 free 传 5~6 张，全定制默认空 |
| 标题/文案 | 复用 `style_titles`（style 1）与手动文案 |
| 上架信息 | 复用 `ListingFields` 与 `PodListingFieldsEditor`；导出前必须补齐 |
| 规格卡 | **不合成**；尺寸只从 `listing_fields.spec_card` 读取进表格 |
| 妙手 | 与店小秘同源，自动可用 |
| 全定制回归 | `_style_images(batch, items)` 的 full/semi/replica 分支保持原判定；现有导出测试必须全绿 |

### 11.3 ZIP 与单张下载

```
GET /api/pod-customization/free/batches/{id}/download
→ 200 application/zip
  Content-Disposition: attachment; filename="POD-FREE-{批次短号}-{张数}张.zip"
```

- 包内扁平：`free_001.png` … `free_00N.png`（3 位补零，按 variant 排序）。
- 只打已完成张；部分失败允许下载（断号）。
- 全部失败 → 409「当前批次没有可下载的图片」。
- 单张预览走本地资产端点 `/assets/{asset_id}`；单张下载复用现有资产下载。

---

## 12. 前端交互

### 12.1 导航与灰度

- `WorkspaceModuleId` 增加 `pod_free_customization`；`modules.ts` 的 `pod_workflow.children` 追加「自由定制」（建议排在半定制之后）。
- `WorkspaceShell.tsx` 增加 `case "pod_free_customization"`。
- 灰度开关：`usePodModulePricing` 同时读 `pod_free_flash_v1` / `pod_free_pro_v1`；两条都缺失时整页置灰并提示「服务端未开通自由定制」，禁止建批，避免冻结失败。
- 价格展示：模型卡片上分别显示「当前价格：X 积分/张」；不写死数字。

### 12.2 骨架：二级大标题 + 下方左右两块

**是的，完全沿用现有 POD 页的骨架**，不另起一套视觉：

```
<section class="pod-free-customization-page">
  <header class="pod-page-header">                       ← 二级大标题条
    左：图标 + 英文眉标 + h1「POD 自由定制」+ 价格胶囊
    右：查看定制记录历史 / 下载全部图片（按状态出现）
  </header>

  <div class="pod-workbench-grid">                       ← 下方左右两块
    <aside class="pod-setup-column">                     ← 左：0.382，设置
      模型卡 / 提示词卡 / 参考图卡 / 标题卡 / 批次名 / 主按钮
    </aside>
    <main class="pod-results-column">                    ← 右：0.618，结果
      批次头 / 图片结果 / 继续生成面板 / 标题卡 / 导出卡
    </main>
  </div>
</section>
```

依据：

- 全定制页就是这个结构：顶栏 + `.pod-workbench-grid`（[PodCustomizationPage.tsx](../../../web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.tsx#L1058-L1077)）。
- 生效布局是 0.382 : 0.618 的左右两栏，左设置、右结果（[podCustomization.css](../../../web-frontend/src/modules/pod_customization/styles/podCustomization.css#L421-L430)）。
- 半定制页同样复用 `.pod-page-header` + 0.382/0.618 两栏（[PodSemiCustomizationPage.tsx](../../../web-frontend/src/modules/pod_semi_customization/pages/PodSemiCustomizationPage.tsx#L434-L457)、[podSemiCustomization.css](../../../web-frontend/src/modules/pod_semi_customization/styles/podSemiCustomization.css#L62-L69)）。
- `≤900px` 自动收成单列（[podCustomization.css](../../../web-frontend/src/modules/pod_customization/styles/podCustomization.css#L719-L726)）。

因此自由定制**直接复用** `.pod-page-header` / `.pod-workbench-grid` / `.pod-setup-column` / `.pod-results-column` / `.pod-setup-card` / `.pod-section-title` / `.pod-start-button` / `.pod-page-price`，只新增 `pod-free-*` 作为差异类；卡片的圆角、阴影、字号、按钮风格与全定制/半定制一致。

### 12.3 顶栏（二级大标题）

| 位置 | 内容 |
|---|---|
| 图标 | `iconfont icon-formatpainter`（与半定制同族，表示「画」） |
| 眉标 | `POD FREE CUSTOMIZATION · USER PROMPT` |
| 主标题 | `POD 自由定制` |
| 价格胶囊 | 跟随当前选中模型：`当前价格：34 积分/张（Flash）`；未拉到价格时隐藏 |
| 右侧操作 | `查看定制记录历史`；当前批次有图时追加 `下载全部图片`；**不放**模板 / 构图按钮 |
| 流程条（可选） | 顶栏下方一条细胶囊：`1 写提示词 → 2 出主图 → 3 选 4~6 张 → 4 导出`，样式对齐裂变的 `.pod-fission-flow` |

### 12.4 左栏（0.382，设置）

自上而下 5 张 `.pod-setup-card`，最后是主按钮：

| 卡片 | 内容 |
|---|---|
| 1. 模型 | Flash / Pro 两个可选 tile（不是下拉）：展示「快 / 高质量」与各自单价；选中态高亮；副文案「整批固定，生成后不可切换」 |
| 2. 生图提示词 | `.pod-section-title`：`IMAGE PROMPT` / `生图提示词`；textarea 约 180px 高，0/4000 计数；副文案「原样提交，系统不追加任何内容」 |
| 3. 参考图 | 0~4 个上传 tile（缩略图 + 删除）；副文案「只作用于第 1 张，第 2~N 张默认参考第 1 张」 |
| 4. 标题 | 开关「同步生成标题（免费）」，默认开；折叠区「标题参考信息」放产品名/品类/市场/人群/卖点/文案限制，全部可空；副文案「只用于标题与导出，不进入生图提示词」 |
| 5. 批次名 | 单行输入，可空；为空时取提示词前 120 字 |
| 主按钮 | `.pod-start-button`：`生成第 1 张 · N 积分`；busy 时显示提交态；未开通/未填提示词/未选模型时禁用 |

已建批后左栏仍可继续编辑，但只影响「下一批」；页面明示「已提交批次的提示词与模型已冻结，不受修改影响」。

### 12.5 右栏（0.618，结果）的四种状态

**A. 还没有批次**

一个空状态卡：`左侧写好提示词，生成第 1 张后在这里继续选 4~6 张并逐张写提示词`。不放大图占位。

**B. 第 1 张生成中**

批次头（批次名 / 状态 / 删除）+ 轻进度条 + 一张 1:1 骨架卡，文案「正在生成第 1 张」。

**C. 第 1 张完成、尚未选总张数**

```
批次头：批次名 · 已出 1 张 · 删除 / 下载
├─ 主图大卡：1:1 大图（点开 PodResultLightbox）、重试、下载、提示词摘要
├─ 标题卡：生成中 / 成功可编辑 / 失败可重试
└─ 继续生成卡（整栏宽）
    ├─ 总图数：4 / 5 / 6 分段按钮（低于当前已出图数禁用）
    ├─ 第 2..N 张：逐行「序号徽标 + textarea」，全部必填
    ├─ 提示：第 2~N 张默认以第 1 张为参考图
    ├─ 费用摘要：本步冻结 (N-1) × 单价
    └─ 主按钮：生成剩余 N-1 张 · X 积分
```

「继续生成卡」放在右栏而不是左栏：左栏只有约 300px，写 5 条提示词太窄；右栏约 0.618 宽，且用户需要一边看第 1 张一边写后续提示词。

**D. 阶段二生成中 / 已完成**

```
批次头：批次名 · 完成 k/N · 取消 / 删除 / 下载 ZIP
├─ 进度条：已出 k/N 张 · 失败 x 张
├─ 图片网格 1..N：3 列（≤900px 时 2 列）
│   每格 = 序号/角色徽标 + 图片 + 提示词摘要 + 状态 + 重试/下载
├─ 标题卡：标题 / 英文标题 / 描述，可编辑；可重新生成（免费）
└─ 导出卡
    ├─ 文案摘要 + 店小秘信息摘要
    ├─ 「编辑导出信息」→ PodListingFieldsEditor 抽屉
    ├─ 「导出店小秘」/「导出妙手」/「下载全部图片 ZIP」
    └─ 缺 1~4 核心图或上架信息未补齐时置灰，并显示具体原因
```

右栏顶部固定一条「本批设置」细条：`Flash / Pro · 同步标题 · 4~6 张`，避免用户滚动后忘记本批冻结了什么。

### 12.6 响应式与视觉规范

- 桌面：0.382 / 0.618 两栏；左栏卡片纵向堆叠，右栏结果区独立滚动。
- `≤1080px`：保持两栏但收窄左栏。
- `≤900px`：单列，顺序为「设置 → 结果」；继续生成卡的提示词行改为单列。
- 颜色全部走 `--pod-*` / `--theme-*` 变量，不在自由定制页写死颜色。
- 动效只做卡片出现、进度条与按钮 hover；遵循 `prefers-reduced-motion`。
- 结果图统一 1:1 裁切预览，点开 lightbox 看原图；不在网格里拉伸变形。

### 12.7 明确不出现

- 智能填写、豆包改写、业务字段里的设计主题/元素/配色生成。
- 模板入口、模板库、构图模板、批级美术指导、结构视图。
- 四宫格、拆图、场景优化、规格卡合成入口。
- 半定制的「4 的倍数」数量选择；自由定制只有 4/5/6。

### 12.8 前端文件

| 文件 | 职责 |
|---|---|
| `modules/pod_free_customization/pages/PodFreeCustomizationPage.tsx` | 页面薄壳与阶段切换 |
| `modules/pod_free_customization/components/FreePromptCard.tsx` | 模型 + 提示词 + 参考图 + 标题开关 |
| `modules/pod_free_customization/components/FreeContinuePanel.tsx` | 4~6 选择与逐张提示词 |
| `modules/pod_free_customization/components/FreeResultGrid.tsx` | 图片结果与重试/下载 |
| `modules/pod_free_customization/components/FreeExportPanel.tsx` | 标题/店小秘信息/导出 |
| `modules/pod_free_customization/api/podFreeCustomizationApi.ts` | API 封装 |
| `modules/pod_free_customization/data/podFreeCustomizationModel.ts` | 校验、状态文案、角色映射、价格格式化 |
| `modules/pod_free_customization/data/podFreeCustomizationDraft.ts` | 独立草稿 storage key |
| `modules/pod_free_customization/styles/podFreeCustomization.css` | 复用 `--pod-*` 变量 |

复用：`PodAssetImage` / `usePodAssetUrl`、`PodResultLightbox`、`PodListingFieldsEditor`、`PodListingDetailDrawer`、`usePodModulePricing`（扩展两个 key，单位改为「张」）。

---

## 13. 改动点清单

### 13.1 后端（`local-runtime/wh_local`）

| 文件 | 改动 |
|---|---|
| `modules/pod_customization/migrations/030_free_customization.sql` | 新增 `pod_customization_free_panels` |
| `modules/pod_customization/image_providers/catalog.py` | 新增 Pro 目录项；新增 `FREE_IMAGE_MODELS`；全局下拉过滤掉 Pro |
| `modules/pod_customization/image_providers/doubao.py` | Pro 超时（可选）；其余请求体不变 |
| `modules/pod_customization/runtime_contracts.py` | `DirectListingGridRequest.model` 可选字段 |
| `modules/pod_customization/ai_runtime.py` | 按请求模型解析；`pod_image_grant_provider(model=None)`；Pro 超时 |
| `modules/pod_customization/contracts.py` | `PodBatchMode` 加 `free`；`FreeBatchCreate` / `FreePanelsCreate` / 参考图上传响应 |
| `modules/pod_customization/repository.py` | `create_free_batch` / `append_free_panels` / `list_free_panels` / `finish_free_panel` / `list_free_batches` / `get_free_batch` |
| `modules/pod_customization/service.py` | 阶段一/阶段二/单张重试/取消/删除/ZIP/listing-fields 保存；payload 扩展 |
| `modules/pod_customization/worker.py` | `mode=="free"` 分支；`_stream_free_panels`；`_process_free_panel`；标题在第 1 张后触发；批量模型 provider key 校验 |
| `modules/pod_customization/billing_contract.py` | 两个画像常量；`PodCallPlan.for_free_panels`；`free_variants` 分组/冻结/结算；module/unit 常量 |
| `modules/pod_customization/remote_billing.py` | 画像按批次透传（现有逻辑即可，需确认） |
| `modules/pod_customization/router.py` | `/free/*` 端点与参考图上传 |
| `modules/pod_customization/export.py` | `_style_images` free 分支；`_build_row` 可选 `extra_image_urls`；full/semi/replica 不变 |
| `wh_local/billing.py`（本地镜像） | 新画像常量、白名单、展示区间；仅本地测试与契约参考 |

**零改动**：`spec_card.py`、`title_runtime.py`、`dianxiaomi.py`、`brief_runtime.py`、`composition_runtime.py`、`prompts.py`、`column_prompts.py`、`media.py` 的拆图逻辑。

### 13.2 前端（`web-frontend/src`）

| 文件 | 改动 |
|---|---|
| `app/navigation/modules.ts` | `pod_free_customization` 模块 + `pod_workflow.children` 追加 |
| `app/layout/WorkspaceShell.tsx` | import + render case |
| `app/navigation/modules.test.ts` | 子项顺序断言更新 |
| `modules/pod_customization/data/usePodModulePricing.ts` | key 类型与单位表加入两个 free 画像 |
| `modules/pod_free_customization/**` | 新增页面、组件、api、data、styles |
| `modules/pod_customization/components/PodListingFieldsEditor.tsx` | 只在需要时暴露受控入口；行为不变 |

---

## 14. 测试计划

### 14.1 后端

| 测试 | 覆盖 |
|---|---|
| `test_free_contracts.py` | 模型白名单、prompt 长度、参考图 0~4、target 4~6、panels 覆盖 2..N、幂等 |
| `test_free_worker_initial.py` | 1 次调用只出 1 张；**provider 收到的 prompt 与用户原文逐字一致**；不拆图；不触发规格卡 |
| `test_free_worker_panels.py` | 第 2~N 张参考图 = 第 1 张；逐张失败独立；单张重试只跑该张；阶段二前必须第 1 张完成 |
| `test_free_model_override.py` | 批次 Flash 用 flash、Pro 用 pro，不受全局模型影响；grant provider=ark；全定制行为不变 |
| `test_free_billing.py` | 两画像冻结/结算；1 张=1 link；标题零计费；失败全退；两阶段独立冻结 |
| `test_free_title.py` | 开启时第 1 张后生成；关闭时无标题调用；标题失败不影响图片扣费；手动改标题可导出 |
| `test_free_export.py` | 1~4 角色映射；5~6 只进轮播图/描述；缺 1~4 禁止导出；全定制导出回归 |
| `test_free_zip.py` | `free_001..N` 命名；部分失败断号；空结果 409；越权 404 |
| `test_free_router.py` | 端点权限、404/409、重复提交、非 free 批次拒绝 |

### 14.2 前端

| 测试 | 风格 |
|---|---|
| `podFreeCustomizationModel.test.ts` | 行为断言：4/5/6 校验、参考图 0~4、prompt 长度、角色映射 |
| `PodFreeCustomizationPage.test.ts` | 源码字符串断言：无智能填写/模板/构图/规格卡；有模型切换、提示词原文提示、4~6 面板、导出入口 |
| `modules.test.ts` | 导航子项顺序 |
| 回归 | 全定制 / 半定制 / 复刻 / 裂变既有页面测试全绿 |

运行：

```bash
cd /Users/Zhuanz/MainPG/local-runtime
.venv/bin/python -m pytest wh_local/modules/pod_customization/tests -k "free or export or billing or title"

cd /Users/Zhuanz/MainPG/web-frontend
node --test --experimental-strip-types src/modules/pod_free_customization/**/*.test.ts src/app/navigation/modules.test.ts
```

---

## 15. 风险与护栏

| # | 风险 | 护栏 |
|---|---|---|
| 1 | 零内置提示词，出图质量/产品一致性完全依赖用户 | 产品定位明确；参考图给到 0~4 张；第 2~N 张默认锁第 1 张；不做隐性补齐 |
| 2 | 用户提示词触发上游审核 4xx | 按 `non_retryable_4xx` 失败并全额退；回显上游错误摘要；不自动改写提示词 |
| 3 | Pro 被全局模型下拉选中，全定制按 Flash 价出 Pro 图 | Pro 只进 `FREE_IMAGE_MODELS`，全局 `model_choices()` 过滤；全定制不暴露 Pro |
| 4 | 两阶段冻结导致「只出 1 张就放着」的孤儿批次 | 阶段一结束即显示「继续生成」入口与历史提醒；导出按钮禁用；不额外收费 |
| 5 | 阶段二提交后不可减图，用户误选 6 张 | 提交前二次确认显示总价；单张失败可退；选择后不可减少 |
| 6 | COS 发布失败导致图在本地但导出不了 | 图片生成成功即保留本地资产；`publish_status=failed` 可重试发布；ZIP 始终可用 |
| 7 | 服务端未加新画像，冻结直接失败 | 前端以 `pod_modules` 两条作为灰度开关；缺失时整页置灰 |
| 8 | 标题零计费未对齐，阶段一被多扣 | 服务端对接清单列为上线硬前置；测试断言 title-only 退款 |
| 9 | 新增 `free_variants` 分组改坏全定制结算 | 只在 `free_variants` 非空时生效；全定制/半定制/复刻结算测试全绿 |
| 10 | 模型 ID 写错（Pro 版本后缀不同） | 上线前在方舟控制台核对；目录值集中在 `catalog.py` 一处 |
| 11 | 4K 被像素上限拦截 | P0 固定 2048；4K 放 P1 并先实测 |
| 12 | 导出 5~6 张改坏店小秘列 | 不新增列，只追加到既有「轮播图/描述」；全定制 4 张导出测试作回归门 |

---

## 16. 待确认 / 待验证

1. **单价最终值**：**Flash 34 / Pro 72 积分/张**（2026-10-29 用户确认）；写入服务端倍率后以 `pod_modules` 下发为准。毛利护栏：Flash ≥ 30、Pro ≥ 72。
2. **Pro 模型 ID**：文档示例为 `doubao-seedream-5-0-pro-260628`，需在方舟控制台核对实际可用 ID。
3. **Pro 超时**：240s 是否足够，需实测；若经常超时再调。
4. **5~6 张是否只进轮播图**：当前方案不新增列；若运营希望 5~6 张也用于「详情图」等其它位置，需要再改表格映射。
5. **单张重试是否 P0**：当前按 P0 设计；若排期紧张可降为 P1（P0 只支持失败后整批「继续生成」）。
6. **标题默认开关**：当前默认开；若担心标题耗时影响首图感知，可改默认关。
7. **参考图上限 4 张**：方舟多图上限需实测确认；若上游支持更多且运营需要，再放宽。

---

## 附录 A：全定制 / 半定制 / 自由定制 对照

| 维度 | 全定制 | 半定制 | 自由定制 |
|---|---|---|---|
| 生图提示词 | 系统模板 + 业务字段 + 元素轮转 | 系统半定制模板 | **用户原文，零内置** |
| 模板 / 参考图 | 必须有模板 | 无模板、无参考图 | 无模板；可选 0~4 张参考图 |
| 一次调用产出 | 1 张 2×2 母图（拆 4 张） | 1 张 2×2 母图（拆 4 款图案） | **1 张成品图** |
| 批次结构 | N 款 × 4 图 | N/4 组 × 4 款图案 | 1 款 × 4~6 图 |
| 后续图参考 | 模板 / 母图 | 无 | **第 1 张成品图** |
| 标题 | 每款 1 条 | 无 | 可选 1 条，零计费 |
| 规格卡 | hero 合成 | 无 | 无 |
| 导出 | 店小秘 / 妙手 | ZIP | 店小秘 / 妙手 + ZIP |
| 模型 | 全局 POD 模型 | 全局 POD 模型 | **批次内 Flash / Pro 冻结** |
| 计费 | `pod_random_v1` 按款 | `pod_semi_v1` 按组 | **`pod_free_flash_v1` / `pod_free_pro_v1` 按张** |

---

## 附录 B：模型与上游价格来源

- 火山方舟模型页：Seedream 5.0 pro 模型 ID 示例 `doubao-seedream-5-0-pro-260628`。
  <https://docs.volcengine.com/docs/82379/2582774?lang=zh>
- 运营提供的官方计费口径（2026-10-29）：
  - Seedream 5.0 Pro：输出 0.30 元/张；输入首图免费，第 2 张及以后 0.02 元/张。
  - Seedream 5.0 Flash：输出 0.12 元/张；输入免费。
  - 以方舟控制台实际账单为准。
- 方舟模型价格页：doubao-seed-2.1-lite 文本输入约 0.4~0.8 元/百万 tokens、输出约 6~8 元/百万 tokens（标题 token 成本按此估算）。
  <https://ark.volcengine.com/region:cn-beijing/docs/model-pricing>
- 方舟图片生成 API：支持单图生成、多图参考、1K/2K/4K 档位。
  <https://docs.volcengine.com/docs/82379/1541523>

> 以上价格只用于推导建议积分档，不直接写进客户端。

---

## 附录 C：已评估并否决的路线

| 路线 | 结论 | 理由 |
|---|---|---|
| 一条提示词重复出 N 张 | ❌ | 用户明确「每张单独提示词」「一提示词一图精出」 |
| 一次调用出 2×2 四宫格再拆 | ❌ | 单张像素被摊薄，不符合「精出」 |
| 建批时一次性冻结 4~6 张 | ❌ | 用户要先看第 1 张再决定总数；两阶段冻结更贴合流程 |
| 为自由定制新建独立后端模块 | ❌ | 资产/worker/标题/导出/计费全要重写，违背复用原则 |
| 新增 `awaiting_panels` 批次状态 | ❌ | 牵连清扫/结算/导出/补跑的大量状态判断；用 meta 表达即可 |
| 自由定制的标题也走零内置提示词 | ❌ | 用户明确要求「参考全定制流程产出标题」；标题有固定输出契约 |
| 把 Pro 暴露到全局 POD 模型下拉 | ❌ | 全定制按 Flash 画像计费，会出现高价模型低价出图 |
| 5~6 张新增店小秘列 | ❌ | 模板列固定；只追加到轮播图/描述，避免破坏现有表格 |

---

## 附录 D：服务端实施提示词（可直接复制）

> 用途：把下面整段交给负责 auth-api / 计费服务的开发或 AI。
> 本文不含服务端代码；服务端仓库、表名与函数名以实际为准。

```text
你是服务端 auth-api / 计费服务的维护者。请为 POD「自由定制」新增两个计费画像，
并复用现有 POD 冻结/结算通道；不要新增冻结/结算端点，不要改客户端契约。

## 背景
客户端「自由定制」一次调用只生成 1 张图，按张计费；一个批次分两个阶段：
- 阶段一：1 张图 + 可选 1 条标题；
- 阶段二：用户选 4/5/6 张后，再生成 N-1 张图。
标题对用户免费，成本摊进图片单价。

## 客户端已固定的画像
- 自由定制 · Flash：billing_profile = "pod_free_flash_v1"，34 积分/张
- 自由定制 · Pro：  billing_profile = "pod_free_pro_v1"，  72 积分/张
- 1 张图 = 1 个 link；item_count == link_count，不要套用半定制的 ×4 校验。
- 阶段一、阶段二是两个独立冻结，幂等键不同，各自结算。
- 标题子项 feature = "title"，永远 0 积分。
- 图片子项 v1 叫 "four_grid"，v2 叫 "image"；自由定制里它代表 1 张图，不是四宫格。

## v1 载荷（默认契约）
冻结：POST /api/customer/billing/batch/freeze
阶段一示例：
{
  "idempotency_key": "pod:free-batch:{batch_id}:initial",
  "link_count": 1,
  "item_count": 1,
  "scope": ["title", "four_grid"],
  "billing_profile": "pod_free_flash_v1"
}
阶段二示例（本批 4 张，新增 3 张）：
{
  "idempotency_key": "pod:free-batch:{batch_id}:panels:{uuid}",
  "link_count": 3,
  "item_count": 3,
  "scope": ["four_grid"],
  "billing_profile": "pod_free_flash_v1"
}

结算：POST /api/customer/billing/batch/settle
{
  "freeze_id": "frz_x",
  "items": [
    {
      "link_idx": 1,
      "subitems": [
        { "feature": "title", "status": "success" },
        { "feature": "four_grid", "status": "success" }
      ]
    }
  ]
}

## v2 载荷（启用 WH_LOCAL_BILLING_CONTRACT_VERSION=v2 时）
冻结：POST /api/customer/billing/v2/pod/freezes
{
  "idempotency_key": "pod:free-batch:{batch_id}:initial",
  "module": "pod_free",
  "billing_profile": "pod_free_pro_v1",
  "unit_kind": "panel",
  "link_count": 1,
  "scope": { "image": true, "title": true },
  "unit_ids": ["panel-0"],
  "item_count": 1
}
结算：POST /api/customer/billing/v2/pod/freezes/{freeze_id}/settle
{
  "idempotency_key": "{freeze_id}:settle",
  "links": [
    {
      "link_idx": 0,
      "unit_id": "panel-0",
      "subitems": [
        { "feature": "image", "status": "success" },
        { "feature": "title", "status": "success" }
      ]
    }
  ]
}

## 你要做的改动
1. 画像白名单
   - 冻结、结算、重冻结、冻结状态、v2 keys/close 等所有校验 billing_profile 的地方，
     加入 "pod_free_flash_v1" 和 "pod_free_pro_v1"。
2. 倍率分类
   - 在 _multiplier_category() 里，把两个 free 画像放在 startswith("pod") 兜底之前精确匹配，
     分别归入 "pod_free_flash" / "pod_free_pro"；
   - 同步加入 MULTIPLIER_CATEGORIES，否则倍率配置不会产出这两个分类。
3. 定价与 pod_modules
   - 基准价：Flash 34、Pro 72，单位「张」。
   - pricing.pod_modules 追加两条：
     { "key": "pod_free_flash_v1", "label": "POD 自由定制 · Flash", "unit_label": "张",
       "charge_min_points": 34, "charge_max_points": 34,
       "base_charge_min_points": 34, "base_charge_max_points": 34, "multiplier_percent": 100 }
     { "key": "pod_free_pro_v1", "label": "POD 自由定制 · Pro", "unit_label": "张",
       "charge_min_points": 72, "charge_max_points": 72,
       "base_charge_min_points": 72, "base_charge_max_points": 72, "multiplier_percent": 100 }
   - active_pricing() 与 summary 必须同口径；数组顺序追加在现有 POD 条目之后。
4. 冻结校验
   - link_count 允许 1..6；item_count 必须等于 link_count（free 专属，不要按 semi ×4）。
   - scope 只接受 ["title","four_grid"] 或 ["four_grid"]（v1）；
     v2 scope 接受 {image:bool,title:bool}。
   - v2 只接受 module="pod_free"、unit_kind="panel"；unit_ids 长度必须等于 link_count，
     unit_id 建议 "panel-0"...；links[].unit_id 必须与冻结时一致。
   - 同一 idempotency_key 重放必须返回同一冻结，不得重复扣/重复冻结。
5. 结算校验
   - items/links 数必须等于冻结 link_count；
   - 每条 link 的 subitems 必须覆盖冻结 scope；
   - four_grid/image 为 success 时按画像单价扣该 link；no_return 全额退该 link；
   - title 为 success 或 no_return 都按 0 计费，不影响图片扣费；
   - 结算金额按冻结时快照，不受后续调价影响。
6. 密钥
   - free 画像的冻结回执必须包含 ark provider key：
     v1: keys: [{provider:"ark", api_key:"...", expires_at:"..."}]
     v2: keys: {ark:["..."]}
   - 缺失 ark 时客户端会暂停等授权；不要只发 wuyin。
7. 管理端
   - 「价格倍率」新增两张卡：POD 自由定制 · Flash / Pro；
   - 支持单条价值、80%~120% 快捷、恢复基础定价、变更记录；
   - 变更记录分类展示名补 pod_free_flash → POD 自由定制 · Flash，pod_free_pro → POD 自由定制 · Pro。
8. 兼容与回归
   - 不改 pod_random_v1 / pod_semi_v1 / pod_fission_v1 / pod_replica_v1 的现有价格与结算；
   - 历史冻结按原快照结算；
   - 老客户端不请求新画像，行为不变；
   - 未知 billing_profile 返回 400 与明确 code，不要静默按 pod 通用价收费。

## 验收
1. GET /api/customer/billing/summary → pricing.pod_modules 包含两条 free 条目，数值 34 / 72，unit_label=张。
2. 阶段一 freeze（flash，scope title+four_grid，link_count=1）→ frozen_points=34，link_prices=[34]，keys 含 ark。
3. 阶段一 settle：four_grid=success、title=success → 扣 34；four_grid=no_return → 退 34；title=no_return 不影响。
4. 阶段二 freeze（4 张批新增 3 张）→ frozen_points=102；3 条 link 各自成功才各扣 34。
5. Pro 同流程 → 72 / 216。
6. 把 Flash 倍率调到 50% → 新冻结 17/张；已冻结仍按 34 结算。
7. 用旧的 pod_random_v1 冻结/结算做回归 → 金额与行为不变。
8. v2 通道跑一遍：module=pod_free、unit_kind=panel、scope 对象、links[].unit_id 对齐，结果与 v1 一致。
```

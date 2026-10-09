# POD 半定制实施计划（P0）

> 配套方案：`docs/superpowers/specs/2026-09-15-pod-semi-customization-design.md`（v2）
> 状态：待启动。方案中的所有「事实」均已在主会话核实（2026-09-15），本计划中的行号与之同源。

---

## 0. 全局约束（不可违反）

| # | 约束 |
|---|---|
| C1 | **复用优先**：半定制不新建后端模块，不改 `ai_runtime` 的轮询/下载/超时/错误分类；不新建结果表/发布表 |
| C2 | **全定制零回归**：`mode` 默认 `full`；`wh_local/modules/pod_customization/tests` 与 `local-runtime/tests` 全绿是每个任务的提交门槛 |
| C3 | **4 格 = 4 款**：`count % 4 == 0`（4–200）；组数 = `count / 4`；速创调用 = `count / 4` |
| C4 | **纯图案**：prompt 层禁止产品/主体/场景/文字；`role = pattern_1..pattern_4` |
| C5 | **不接图床**：不调 `publish_listing_image`、不写 `publications.public_url`；预览走 `/api/pod-customization/assets/{asset_id}` |
| C6 | **不做后处理**：拆分后单格原图即交付物（不抠图/不二值化/不缩放改写） |
| C7 | **交付**：一个批次一个 zip，包内扁平 `style_001.png` …（款号 = `item.index`）；部分失败允许下载（只打 completed 款） |
| C8 | **计费独立**：profile `pod_semi_v1`，每款随机 32–38 积分；冻结 `link_count = count`（款数），不是组数 |
| C9 | **不出**：标题、SKU/申报价/重量/规格卡、listing_fields、店小秘/妙手导出 |
| C10 | **不 commit、不起服务**（除需要本地验证时启动 8010）；改动范围限于本计划列出的文件 |

---

## 1. 任务总览

| 任务 | 内容 | 依赖 | 关键验收 |
|---|---|---|---|
| T1 | 契约 + 迁移 014 | — | `SemiBatchCreate` 校验；`mode` 列存在且历史批次为 `full` |
| T2 | 无参考图生图分支（`ai_runtime`） | T1 | 提交体无 `urls`；`reference_count=0` |
| T3 | 半定制 prompt（`prompts`） | — | 纯图案硬约束；同组四格 offset 不同 |
| T4 | 仓储层（`repository`） | T1 | `semi` 批次不写 `style_titles`；占位模板 seed 幂等 |
| T5 | 执行层（`worker`） | T2/T3/T4 | 不读模板图、不发布 COS、role=pattern_*、整组重试 |
| T6 | 服务层（`service`） | T4/T5 | `create_semi_batch` 不走模板 preflight；zip 打包正确 |
| T7 | 路由（`router`） | T6 | `/semi/*` 全端点；非法入参 422/404/409 |
| T8 | 计费（`billing_contract`/`billing`/`auth_server`/`remote_billing`） | T1 | `link_count == count`；同组 4 link 共享成败 |
| T9 | 前端导航二级菜单 | — | `pod_workflow` 分组 + `defaultChildId` 正确 |
| T10 | 前端抽共享区块 | T9 | 全定制页行为不变 + 三套既有测试全绿 |
| T11 | 半定制页面薄壳 | T9/T10/T7 | 无模板入口/无导出；zip 下载可用 |
| T12 | 端到端验收 | 全部 | 见 §13 |

建议顺序：T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12。
T2/T3/T9 可与 T4 并行；T10 必须在 T11 之前完成。

---

## 2. T1 契约与迁移

### 文件
- `local-runtime/wh_local/modules/pod_customization/contracts.py`
- `local-runtime/wh_local/modules/pod_customization/migrations/014_semi_customization.sql`（新增）

### 步骤
1. 新增角色常量（与 `prompts.LISTING_IMAGE_ROLES` 同级、不修改原常量）：
   ```python
   SEMI_PATTERN_ROLES = ("pattern_1", "pattern_2", "pattern_3", "pattern_4")
   ```
2. 新增 `SemiBatchCreate`（`ConfigDict(extra="forbid")`）：
   - `count: int = Field(strict=True, ge=4, le=200)`
   - `prompt_version: PromptVersion = "v1"`
   - `business_fields: BusinessFields = Field(default_factory=BusinessFields)`
   - `creative_prompt: str = Field(default="", max_length=4000)`
   - `title: str = Field(default="", max_length=120)`
   - **无 `template_id`、无 `listing_fields`**（多传即 422）
   - `model_validator`：`count % 4 != 0` → `ValueError("半定制数量必须是 4 的倍数")`；`business_fields.product_category` 空 → 报错（沿用现有口径）
3. 迁移 014：
   ```sql
   ALTER TABLE pod_customization_batches ADD COLUMN mode TEXT NOT NULL DEFAULT 'full';
   ```
   （迁移执行器按文件名顺序执行并登记 `schema_migrations`，与既有 001–013 同机制）

### 验收
- `SemiBatchCreate(count=10, ...)` 抛错、`count=12` 通过、`count=2`/`201` 抛错。
- 迁移后既有批次 `mode='full'`；重复执行迁移不报错（幂等）。

---

## 3. T2 无参考图生图分支

### 文件
- `local-runtime/wh_local/modules/pod_customization/runtime_contracts.py`（若 `DirectListingGridRequest` 定义在此）
- `local-runtime/wh_local/modules/pod_customization/ai_runtime.py`

### 步骤（方案 §6.5，取实现方式 a）
1. `DirectListingGridRequest` 的 `template_id / template_image / template_content_type` 改为**可选**（默认 `""` / `b""`），其余字段不变；不改尺寸（`1024x1024`）与模型解析（`_resolve_pod_image_model`，`ai_runtime.py:439-453`）。
2. `generate_listing_grid`（`ai_runtime.py:135-189`）：
   - `reference_url = self._publish_listing_reference(request) if request.template_image else ""`
   - `reference_count=0 if not reference_url else 1`
   - 其余（`provider_slot`、超时、错误分类）**一行不改**
3. `_submit_suchuang_grid`（`ai_runtime.py:214-241`）：
   - `reference_url` 为空时提交体**不带 `urls`**（2.5 分支同样省略 `urls`），其余字段不变
4. `_publish_listing_reference` 保持不变（仅在有待发布参考图时被调用）。

### 验收（新增 `tests/test_semi_image_request.py`）
- 无参考图请求：`json` 里 `"urls" not in body`；`GeneratedMedia.reference_count == 0`；**不调用** `upload_content_addressed_to_cos`
- 有参考图时：提交体与现状逐字节等价（回归断言，防止改坏全定制）
- 轮询失败/下载失败/超时的 `status_class` 与全定制一致

---

## 4. T3 半定制 prompt

### 文件
- `local-runtime/wh_local/modules/pod_customization/prompts.py`

### 步骤
1. 新增 `build_semi_pattern_prompt(base_prompt, *, group_index, variant_index, attempt, business_fields, creative_prompt, style_elements)`：
   - `offset = (group_index - 1) * 4 + (variant_index - 1)`，用于 composition / palette / density 轮换（复用 `prompts.py:197-214` 的取模手法）
   - 硬约束文本（C4）：**画面只有图案本体**；不含产品/包装/器物/人/场景背景；**不出现任何文字**（字母、数字、logo、印章、水印）；白底或单色底、图案满幅或居中单元、边缘干净；同批统一风格基调、**四格必须互不相同**
   - `attempt == 2` 时追加重试强化句（参考 `prompts.py:257-263` 的既有做法）
   - 元素主语复用 `assign_style_elements` 结果（`prompts.py:85-117`，批次创建时已分配）
2. **不改** `build_style_listing_prompt` / `build_direct_listing_prompt`。

### 验收（扩展 `tests/test_prompts.py`）
- `build_semi_pattern_prompt` 对同一 `(group, variant)` 输出确定性一致；不同 `variant` 输出不同（offset 生效）
- 输出**不含**产品/场景类词，且含「不出现文字」约束句
- 既有 prompt 测试全绿

### 说明
prompt 文案最终以**样张**定稿（方案 §15-1）：建议 T3 完成后先建一个 `count=4` 的最小批次（或临时用 `direct-listing-trials` 风格的单次调用）肉眼确认四格形态，再冻结文案。

---

## 5. T4 仓储层

### 文件
- `local-runtime/wh_local/modules/pod_customization/repository.py`

### 步骤
1. `create_batch(...)`（`repository.py:336-422`）加 `mode: str = "full"` 参数：
   - 批次行写入 `mode`（`repository.py:369-379`）
   - `mode == "semi"` 时**跳过** `style_titles` 写入（`repository.py:416-421`）
   - 其余（`style_grid_results` N×4 行、`style_elements`、companion 标记）不变
2. `ensure_semi_placeholder(workspace_id, owner_user_id) -> tuple[str, str, str]`（方案 T1/§6.1）：
   - seed 一条 1×1 PNG 资产（代码内置常量字节）+ 模板行（`source='system'`、`deleted_at` **非空**、`calibration_status='ready'`）+ 快照行（version 与模板一致）
   - 幂等：已存在直接返回 `(template_id, snapshot_id, name)`
   - 命名：`template_id="semi-pattern-placeholder"`、`snapshot_id="semi-pattern-placeholder-v1"`、`name="半定制占位模板"`
3. `get_batch_internal` / `get_batch` 返回 `mode`（`repository.py:446-462` 起）
4. payload 相关查询补 `mode`（对外字段由 T6 组装）。

### 验收（扩展 `tests/test_persistence.py`）
- `create_batch(mode="semi")` 后：`style_titles` 无行；`style_grid_results` 有 `count/4 × 4` 行；批次 `mode='semi'`
- `create_batch()`（不传 mode）行为与现状一致：`mode='full'` 且 `style_titles` 有 N 行
- 占位模板连续调用两次只 seed 一次；**不出现在** `list_templates` 结果里（`deleted_at` 过滤）

---

## 6. T5 执行层（worker）

### 文件
- `local-runtime/wh_local/modules/pod_customization/worker.py`

### 步骤
1. `_process_batch_authorized`（`worker.py:486-490`）：`batch["mode"] == "semi"` 时**不读**模板快照/模板图（`template_content=b""`、`content_type=""`）。
2. `_stream_style_attempts.submit_style`（`worker.py:1063-1120`）：
   - prompt 走 `build_semi_pattern_prompt`（semi）或现状（full）
   - `DirectListingGridRequest` 构造：semi 时不带模板字段（`trial_id` 保持 `{batch}-style-{i}-attempt-{n}` 不变，billing call id 不变）
3. `_process_style_grids`（`worker.py:1396-1513`）：
   - `roles = LISTING_IMAGE_ROLES if mode == "full" else SEMI_PATTERN_ROLES`（`worker.py:1405`）
   - semi 时**跳过** `publish_listing_image`（`worker.py:1426-1433`）：只 `_save_asset(kind="direct_listing_panel")` + `finish_style_grid_result(public_url="", ...)`
   - `role == "hero"` 规格卡分支（`1421-1423`）与 `role == "lifestyle"` 标题分支（`1448-1478`）因 role 不匹配自动跳过 —— **不要额外加 if**
4. `regenerate_style`（`worker.py:657-758`）：semi 时跳过标题相关步骤（整组重试 = 重跑该组一次生图 + 重拆 + 重落库）。
5. 终态口径不变（`title_runtime is None` 分支，`worker.py:507-512`）。

### 验收（新增 `tests/test_semi_batch_worker.py`）
- 用 fake provider（沿用 `test_worker.py` 既有注入方式）跑通 `count=8`（2 组、8 款）
- 断言：未调用 COS 发布（注入的 publisher 无调用）、`publications.public_url == ""`、`style_titles` 无行、role 全为 `pattern_*`
- 组内一格失败 → 该组 4 款全失败；两轮后仍失败 → 批次 `partial_failure`
- 暂停/取消/恢复复用路径可用；`regenerate_style` 只重跑该组

---

## 7. T6 服务层

### 文件
- `local-runtime/wh_local/modules/pod_customization/service.py`

### 步骤
1. `create_semi_batch(actor, request: SemiBatchCreate)`：
   - `ensure_semi_placeholder(...)` → 冻结（`PodCallPlan.for_semi_batch`，见 T8）→ `repository.create_batch(mode="semi", ...)` → `worker.submit`
   - **不调用** `preflight_batch` 的模板/标定校验（无模板）；业务字段校验沿用 `SemiBatchCreate`
2. 半定制**不注入** `title_runtime`：`service` 构造 worker 时按 mode 传 `title_runtime=None`（对照 `service.py:1622/1641/1675/1699` 的 `include_title` 口径）。
3. `_batch_payload`（`service.py:1907-1964`）输出 `mode` / `style_count`（组数）/ `item_count`（款数）；`_item_payload`（`service.py:2044-2068`）semi 时 `public_url=""` 且 `pattern_preview_url` 保留。
4. 新增 `build_semi_zip(actor, batch_id) -> tuple[bytes, str]`：
   - 归属校验（workspace + owner）→ `mode != "semi"` → `PodRepositoryError(404)`
   - 取 `style_grid_results` 中 `status='completed'` 且 `pattern_asset_id` 非空的行，按款号 `index = (style_index-1)*4 + variant_index` 升序
   - `zipfile.ZipFile(BytesIO, "w", ZIP_DEFLATED)`：`writestr(f"style_{index:03d}{suffix}", assets.read(rel))`，`suffix` 由资产 `content_type` 推导（`.png`/`.jpg`）
   - 空结果 → `ValueError`（路由转 409，文案「当前批次没有可下载的图案」）
   - 写 `pod_customization_export_records`（kind=`semi_zip`，只记计数与状态，不存字节）
   - 返回 `(zip_bytes, f"POD-SEMI-{batch_id[:8]}-{item_count}款.zip")`

### 验收
- `tests/test_semi_zip_export.py`：命名 `style_001.png`；`partial_failure` 断号（如缺 003、007）仍可下载；空结果 409；越权 404；非 semi 批次 404；zip 内容为本地文件字节（与 `assets.read` 逐字节一致）

---

## 8. T7 路由

### 文件
- `local-runtime/wh_local/modules/pod_customization/router.py`

### 步骤（前缀仍是 `/api/pod-customization`，`router.py:51`；权限依赖照抄既有端点）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/semi/batches` | 创建半定制批次（body = `SemiBatchCreate`） |
| GET | `/semi/batches` | 列表（`limit/offset`，复用既有分页口径） |
| GET | `/semi/batches/{batch_id}` | 详情（payload 带 `mode/style_count/item_count`） |
| POST | `/semi/batches/{batch_id}/pause` \| `/cancel` \| `/resume` | 复用既有服务方法，按 mode 校验 |
| DELETE | `/semi/batches/{batch_id}` | 删除批次 + 清本地文件（复用既有逻辑） |
| POST | `/semi/batches/{batch_id}/styles/{group_index}/regenerate` | 整组重试（D13） |
| GET | `/semi/batches/{batch_id}/download` | zip：`Response(content=..., media_type="application/zip")` + `Content-Disposition` + `X-POD-Semi-Count` |

错误映射沿用 `_call`（`router.py:375-412`）：422 入参、404 归属/形态、409 空结果/状态不允许。

### 验收（新增 `tests/test_semi_router.py`）
- `count=10` → 422；带 `listing_fields` → 422；带 `template_id` → 422
- 非 semi 批次访问 `/semi/batches/{id}` → 404
- zip 端点返回 `application/zip` 且头正确
- 权限：无 `create` 权限不可建批（沿用既有口径）

---

## 9. T8 计费

### 文件
- `local-runtime/wh_local/modules/pod_customization/billing_contract.py`
- `local-runtime/wh_local/modules/pod_customization/remote_billing.py`
- `local-runtime/wh_local/billing.py`
- `local-runtime/wh_local/customer/auth_server.py`

### 步骤
1. `billing_contract.py` 新增 `PodCallPlan.for_semi_batch(batch_id, *, count)`：
   - 调用序列与 `for_batch` 同构但**只含图片调用**（无 title 调用）：`group_index` 取 `1..count/4`，`{batch_id}:style:{group}:image:1|2`
   - plan 上记录半定制的**款数**（供投影使用，例如新增不可变字段 `semi_item_count: int = 0`）
2. 新增 `semi_batch_freeze_payload()` → `{idempotency_key, link_count: count, scope: ["four_grid"], billing_profile: "pod_semi_v1"}`
3. 新增 `semi_batch_settlement_payload(outcomes)`：按款展开 —— 组 g 的 4 个 link（`(g-1)*4+1 … (g-1)*4+4`）取该组 image call 的成败；每 link 一个 `{feature: "four_grid", status}`；`link_idx` 连续 1..count
4. `billing.py`：
   - `BATCH_BILLING_PROFILE_POD_SEMI = "pod_semi_v1"`；`POD_SEMI_LINK_PRICE_MIN_POINTS = 32`；`POD_SEMI_LINK_PRICE_VARIANTS = 7`（32..38）
   - profile 白名单（`billing.py:1963`）加入新 profile
   - 冻结分支（`billing.py:2016-2043`）按 profile 选区间随机；「纯 title scope → 0 积分」规则对 semi 同样适用
   - 结算分支（`billing.py:2560-2636`）：semi profile 与 pod 走同一套校验（scope ⊆ `{title, four_grid}`、子项数 = `link_count`、`four_grid` 成功即扣费否则退还）
   - `is_pod` 判定（`billing.py:598`）与定价展示区间（`billing.py:1210-1213`）按 profile 返回 32–38
5. `auth_server.py:1277-1280`：profile 归一/校验白名单加入 `pod_semi_v1`。
6. `remote_billing.py:60-80`：按批次 mode 传对应 freeze payload。

### 验收（新增 `tests/test_semi_billing_contract.py`，扩展 `local-runtime/tests/test_billing_batch_pricing.py`）
- `count=8` → `link_count == 8`（**不是 2**）；scope == `["four_grid"]`；profile == `pod_semi_v1`
- 结算：组 1 失败、组 2 成功 → 前 4 个 link `no_return`（退还）、后 4 个 `success`（扣费）
- 子项数与 `link_count` 不等 → 400；scope 不匹配 → 400
- 单 link 单价落在 32..38；多 link 之间取值独立

---

## 10. T9 前端导航（二级侧边栏）

### 文件
- `web-frontend/src/app/navigation/modules.ts`
- `web-frontend/src/app/layout/WorkspaceShell.tsx`
- `web-frontend/src/app/navigation/modules.test.ts`、`web-frontend/src/app/navigation/podCustomizationNavigation.test.ts`

### 步骤
1. `WorkspaceModuleId` 增加 `"pod_semi_customization"`；`WorkspaceNavigationGroupId` 增加 `"pod_workflow"`。
2. `podCustomization` 的 label 由「POD定制」改为「全定制」；新增 `podSemiCustomization`（`label: "半定制"`，`description: "按提示词生成纯图案花色图，按批次打包下载"`）。
3. `workspaceModules`：把原顶层 `podCustomization` 替换为分组
   `{ id: "pod_workflow", label: "POD定制", iconClass: "iconfont icon-skin", description: "POD 全定制与半定制", defaultChildId: "pod_customization", children: [podCustomization, podSemiCustomization] }`
4. `workspacePageModules` 追加 `podSemiCustomization`（否则 `moduleTab()` 取不到模块）。
5. `WorkspaceShell.tsx:32` 附近加 import，`renderTab`（`437-482`）加 `case "pod_semi_customization"`。
6. 同步两个导航测试的顺序/存在性断言。

### 验收
- `node --test --experimental-strip-types src/app/navigation/modules.test.ts src/app/navigation/podCustomizationNavigation.test.ts` 全绿
- 手动：侧栏出现「POD定制」分组，展开有「全定制 / 半定制」，点分组默认落到全定制

---

## 11. T10 前端抽共享区块（行为不变）

### 文件（新增，全部放 `web-frontend/src/modules/pod_customization/`）
| 文件 | 内容 |
|---|---|
| `components/PodWorkbenchHeader.tsx` | 页头 + 模板/历史按钮；props：`title`、`showTemplateActions`、回调 |
| `components/PodSetupColumn.tsx` | 业务信息列：`PodBriefInput` 智能填写 + `BUSINESS_FIELDS` 文本域 + 高级创意编辑 + 数量选择 + 「保存为系统模板」+ 「开始生成」；props：`countOptions`、`onSubmit`、`submitLabel`、`showSpecCardEntry`、`showSaveTemplate` |
| `components/PodBatchToolbar.tsx` | 暂停/取消/继续/删除/重试失败；props：`actions`（要显示哪些）与回调 |
| `data/usePodDraftAutosave.ts` | 草稿自动落盘 hook；props：`storageKey`、`version`、`snapshot`、`enabled` |

### 步骤
1. 从 `PodCustomizationPage.tsx` **原样搬运**上述区块（含事件处理），页面改为引用；**不改任何文案与行为**。
2. 每次搬完立即跑既有测试：
   ```
   node --test --experimental-strip-types \
     src/modules/pod_customization/pages/PodCustomizationPage.test.ts \
     src/modules/pod_customization/pages/PodSpecCardEntry.test.ts \
     src/modules/pod_customization/components/PodFailedRetryDialog.test.ts \
     src/modules/pod_customization/api/podCustomizationApi.test.ts \
     src/modules/pod_customization/data/podCustomizationModel.test.ts \
     src/modules/pod_customization/data/podCustomizationDraft.test.ts
   ```
3. 若既有测试断言了页面源码字符串（如 `PodCustomizationPage.test.ts` 断言某处标记），改为断言**页面或其引用的组件**中出现该标记（优先放宽测试而不是复制代码）。

### 验收
- 上述 6 个测试文件全绿（= 回归门）
- 全定制页人工冒烟：智能填写、业务字段、SKU、规格卡入口、开始生成、批量工具条、草稿刷新后仍在

---

## 12. T11 半定制页面薄壳

### 文件（新增 `web-frontend/src/modules/pod_semi_customization/`）
| 文件 | 内容 |
|---|---|
| `pages/PodSemiCustomizationPage.tsx` | 薄壳：`PodWorkbenchHeader`（无模板入口）+ `PodSetupColumn`（`countOptions=[4,8,20,40,60,100]`、无 SKU/规格卡入口）+ 款维度结果区 |
| `components/PodSemiGallery.tsx` | 结果区：批次头（状态/进度/暂停/取消/继续/整组重试/下载 ZIP）+ 款卡片（`style_001` 缩略图 + 款号 + 状态）；图片走 `PodAssetImage` + `pattern_preview_url` |
| `api/podSemiCustomizationApi.ts` | `createBatch / listBatches / getBatch / pause / cancel / resume / delete / regenerateGroup / downloadZip` |
| `data/podSemiCustomizationModel.ts` | 数量校验（4 的倍数）、款号 ↔ (组, 格) 映射、`style_001.png` 命名、进度与状态文案 |
| `data/podSemiCustomizationDraft.ts` | 草稿读写：`mainpg:pod-semi-customization:v1:{account}:{workspace}` |
| `styles/podSemiCustomization.css` | 复用 `--pod-*` 变量；类名 `pod-semi-*` |

### 步骤
1. 提交：`count` 必须 4 的倍数（前端也拦一次）、提示「= N 款图案 / N/4 次生图」、冻结提示「预计冻结 32–38 × N 积分」。
2. 草稿用 `usePodDraftAutosave`（T10 产出），key 与全定制隔离。
3. zip 下载：复用 `podCustomizationApi` 里既有 blob 下载手法（`podCustomizationApi.ts:83-124`），文件名取响应头 `Content-Disposition`。
4. 结果区**不出现**：标题、SKU、申报价、重量、规格卡入口、店小秘/妙手导出、上架详情抽屉、模板入口。

### 验收（新增 `pages/PodSemiCustomizationPage.test.ts`，源码字符串断言）
- 引用 `PodSetupColumn` / `PodWorkbenchHeader`（证明复用而非复制）
- 不出现「店小秘」「规格卡」「SKU」等字样与导出按钮
- 含「下载 ZIP」与 `pattern_preview_url`
- 数量选项全为 4 的倍数
- 另加行为断言 `data/podSemiCustomizationModel.test.ts`：`style_001.png` 命名、款号映射、4 的倍数校验

---

## 13. T12 端到端验收

### 命令
```bash
# 后端全量
cd /Users/Zhuanz/Desktop/MainPG/local-runtime && .venv/bin/python -m pytest -q wh_local/modules/pod_customization/tests tests
# 前端受影响测试（含抽取回归门）
cd /Users/Zhuanz/Desktop/MainPG/web-frontend && node --test --experimental-strip-types \
  src/app/navigation/modules.test.ts \
  src/app/navigation/podCustomizationNavigation.test.ts \
  src/modules/pod_customization/pages/PodCustomizationPage.test.ts \
  src/modules/pod_customization/pages/PodSpecCardEntry.test.ts \
  src/modules/pod_customization/components/PodFailedRetryDialog.test.ts \
  src/modules/pod_customization/api/podCustomizationApi.test.ts \
  src/modules/pod_customization/data/podCustomizationModel.test.ts \
  src/modules/pod_customization/data/podCustomizationDraft.test.ts \
  src/modules/pod_semi_customization/pages/PodSemiCustomizationPage.test.ts \
  src/modules/pod_semi_customization/data/podSemiCustomizationModel.test.ts \
  src/modules/pod_semi_customization/data/podSemiCustomizationDraft.test.ts
# 前端类型/构建（tsc --noEmit + vite build）
cd /Users/Zhuanz/Desktop/MainPG/web-frontend && npm run build
```

### 人工端到端清单
1. 启动 8010 + 5173；侧栏「POD定制 → 半定制」可进入。
2. 智能填写一句话 → 字段回填 → 选 `count=4` → 开始生成。
3. 计费：冻结为 `32–38 × 4`，结算后按成功款扣费、失败款退还。
4. 出图：4 张互不相同的纯图案；预览可见（本地资产端点）；**无公网 URL**。
5. 下载 ZIP：`POD-SEMI-…-4款.zip`，内含 `style_001.png … style_004.png`。
6. 制造一格失败（或断网重试）→ 批次 `partial_failure` → zip 仍可下（断号）。
7. 回归：全定制页功能与观感与改动前一致（智能填写/SKU/规格卡/标题/导出）。

---

## 14. 附录：不变量与文件归属

**必须保持不变（改坏即为回归）**
- 全定制：`pod_random_v1` 计费、4 角色发布到 COS、标题 5 次重试、规格卡合成、店小秘/妙手导出
- 既有迁移 001–013 与历史批次数据
- `media.split_four_grid` 的 800×800 与 JPEG 质量参数
- 全定制既有测试与重试/fencing/进度租约语义

**文件归属**
| 任务 | 可动文件 |
|---|---|
| T1–T8 | `local-runtime/wh_local/modules/pod_customization/{contracts,ai_runtime,runtime_contracts,prompts,repository,worker,service,router,billing_contract,remote_billing}.py` + `migrations/014_*.sql` + 对应测试；`wh_local/{billing.py,customer/auth_server.py}` |
| T9–T11 | `web-frontend/src/app/navigation/*`、`web-frontend/src/app/layout/WorkspaceShell.tsx`、`web-frontend/src/modules/pod_customization/{components,data}/*`、`web-frontend/src/modules/pod_semi_customization/**` |

**样张验收（方案 §15-1）**
T3 完成后、T11 之前，用 `count=4` 跑一次真实批次，人工确认：① 只有图案、无产品/主体/场景；② 无任何文字；③ 四格互不相同；④ 白底/单色底、边缘干净。不满足则调 prompt 文案并重跑，直到通过再进入前端联调。

# 产品预检图片池与素材库重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将每个商品预检页重构为可对照的“处理前图片池”和“处理后素材库”；原图只能手动加入素材库，处理图自动进入素材库；主图严格等于轮播第一张。

**Architecture:** 以 V2 `media_asset_id` 为唯一图片身份。后端在 `task_preview` 投影阶段，为每个 V2 绑定创建无复制的预检代理记录，并返回统一的 `PreviewImageAsset` 读模型；前端不再把 V1 预览资产和 V2 分组分别渲染。`image_manifest_v2` 扩展为“素材库成员 + 导出清单”，其中轮播数组的第 0 项是唯一主图来源。

**Tech Stack:** Python 3、FastAPI、SQLAlchemy、Pydantic、pytest、React、TypeScript、CSS、Node contract tests。

## 全局约束

- [ ] 不迁移、不清洗、不重写历史草稿；本方案只保证部署后的新任务和重新加载后的 V2 草稿正确投影。
- [ ] `media_asset_id` 是存储权威；`preview_url` 仅供 `<img>` 展示，不能用于推断文件路径或生成新资产。
- [ ] V2 原图代理不得复制图片字节到 preview-assets 目录，也不得触发 COS 上传；预检定稿时直接使用已物化的统一媒体文件。
- [ ] 处理前图片池始终只读；唯一操作是“加入素材库/移出素材库”，不能直接设主图、加入轮播或加入详情。
- [ ] 处理后素材库自动包含 AI 生成图、尺寸画布交回图和用户上传图；手动加入的原图也在此显示。
- [ ] 主图不单独维护：`main_asset_id` 必须为空或等于 `carousel_asset_ids[0]`。所有服务端保存、前端操作和导出前校验均执行此归一化。
- [ ] 完整保留现有“设为主图、加入轮播、加入详情、删除、排序、上传、重试同步”能力；仅改变入口、分组和主图语义。
- [ ] 不在本计划执行过程中提交或暂存当前脏工作区；每个任务以测试和 `git diff --check` 作为完成依据。

---

## 目标数据模型与交互规则

### 1. 图片来源与展示分组

| 后端媒体来源 | 预检展示位置 | 是否自动入素材库 | 用户可做的操作 |
|---|---|---:|---|
| `remote_source`，角色 `main/gallery/detail/sku` | 处理前图片池，对应“原始主图/原始轮播/原始详情/原始 SKU”子组 | 否 | 加入素材库、预览、重试同步 |
| `ai_generated` | 处理后素材库，“AI 处理图” | 是 | 设为主图、加入轮播、加入详情、预览 |
| `dimension_rendered` | 处理后素材库，“尺寸图” | 是 | 同上 |
| `preview_upload` | 处理后素材库，“本地导入” | 是 | 同上 |
| 物化失败/等待中的 V2 资源 | 仍在来源所属分组 | 否，直到 ready | 显示状态；失败/可重试时重试同步 |

### 2. `PreviewImageManifest` 的兼容扩展

```ts
type PreviewImageManifest = {
  main_asset_id: string;
  carousel_asset_ids: string[];
  detail_asset_ids: string[];
  semantic_asset_ids: Record<string, string>;
  library_asset_ids: string[]; // 新增：人工从原图池加入素材库的稳定预览代理 ID
};
```

归一化规则必须在 Python domain 和 TypeScript model 同时实现：

```text
1. 去空值、去重，保持第一次出现顺序。
2. 若 carousel 非空：main = carousel[0]。
3. 若 carousel 为空但 main 非空：carousel = [main]，main 保持该值。
4. 若两者都为空：main = ""。
5. library 不影响导出；它只定义人工原图是否可进入“处理后素材库”。
6. semantic 中的 hero 指向 carousel[0]；已不存在于轮播的语义引用必须移除。
```

### 3. 页面骨架

```text
PP-000181  ·  可导出 / 已修改 / 版本
商品信息（紧凑可折叠区域，不占用图片区左侧）

┌──────────────────────── 处理前图片池 ────────────────────────┐
│ 原始主图 | 原始轮播 | 原始 SKU | 原始详情                      │
│ [只读图片卡 + 加入素材库]                                      │
└───────────────────────────────────────────────────────────────┘

┌──────────────────────── 处理后素材库 ────────────────────────┐
│ AI 处理图 | 尺寸图 | 本地导入 | 已选入的原图                   │
│ [图片卡 + 设为主图 / 加入轮播 / 加入详情]                      │
└───────────────────────────────────────────────────────────────┘

轮播图（第 1 张标“主图”，排序后自动更新主图）
详情图
```

商品编号是每张预检卡的顶部标题，不单独占一列。图片区使用整行宽度；表单在其上方或折叠在标题下方，禁止留出图片无关的大面积左侧空白。

---

### Task 1: 归一化主图/轮播和素材库清单契约

**Files:**

- Modify: `local-runtime/wh_local/modules/product_processing/domain/preview_images.py`
- Modify: `web-frontend/src/modules/product_processing/types/index.ts`
- Modify: `web-frontend/src/modules/product_processing/data/precheckImageModel.ts`
- Test: `local-runtime/tests/test_product_processing_preview_images.py`（新建）
- Test: `web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts`（新建）

**Consumes:** 已有 `PreviewImageManifest`、`addAssets()`、`moveAsset()`、`selectMainAsset()`。

**Produces:** 含 `library_asset_ids` 的跨端 manifest，且其任何合法状态都满足“主图 = 轮播第一张”。

- [ ] **Step 1: 写 Python 失败测试。**

```python
def test_manifest_uses_first_carousel_asset_as_its_only_main() -> None:
    manifest = PreviewImageManifest.from_value({
        "main_asset_id": "old-main",
        "carousel_asset_ids": ["b", "a", "b"],
        "library_asset_ids": ["source-1", "source-1"],
        "semantic_asset_ids": {"carousel.hero": "old-main", "carousel.detail": "gone"},
    })
    assert manifest.main_asset_id == "b"
    assert manifest.carousel_asset_ids == ("b", "a")
    assert manifest.library_asset_ids == ("source-1",)
    assert manifest.semantic_asset_ids == {"carousel.hero": "b"}

def test_manifest_promotes_lone_main_to_first_carousel_item() -> None:
    manifest = PreviewImageManifest.from_value({"main_asset_id": "only-main"})
    assert manifest.carousel_asset_ids == ("only-main",)
    assert manifest.main_asset_id == "only-main"
```

- [ ] **Step 2: 确认 Python 测试失败。**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_preview_images.py -q`

Expected: FAIL，因 `library_asset_ids` 和主图归一化尚不存在。

- [ ] **Step 3: 最小实现 Python domain。**

在 `PreviewImageManifest` 增加不可变 tuple 字段 `library_asset_ids`；在 `__post_init__` 先使用 `_ordered_ids()` 归一化轮播、详情和素材库，再按“目标数据模型”第 2 节计算 main 与 semantic。`as_dict()`、`from_value()`、`live_asset_ids()` 必须保留 library 字段；`live_asset_ids()` 继续只返回导出需要的 main/carousel/detail，避免把“加入素材库”误当作发布选择。

- [ ] **Step 4: 写 TypeScript 失败测试。**

```ts
test("setting main moves it to carousel position one", () => {
  const next = selectMainAsset(manifest({ carousel_asset_ids: ["a", "b"] }), "b");
  assert.deepEqual(next.carousel_asset_ids, ["b", "a"]);
  assert.equal(next.main_asset_id, "b");
});

test("moving carousel changes main with the first card", () => {
  const next = moveAsset(manifest({ carousel_asset_ids: ["a", "b"] }), "carousel", "b", -1);
  assert.equal(next.main_asset_id, "b");
});
```

- [ ] **Step 5: 确认 TypeScript 测试失败。**

Run: `npx tsx --test web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts`

Expected: FAIL，当前 `selectMainAsset()` 只修改 `main_asset_id`，不会移动顺序。

- [ ] **Step 6: 最小实现前端模型。**

增加 `normalizeManifest()`、`promoteToLibrary()`、`removeFromLibrary()`。

```ts
export function selectMainAsset(manifest: PreviewImageManifest, assetId: string) {
  const next = copyManifest(manifest);
  const remaining = next.carousel_asset_ids.filter((id) => id !== assetId);
  next.carousel_asset_ids = [assetId, ...remaining];
  return normalizeManifest(next);
}
```

`moveAsset(..., "carousel", ...)`、`removeAsset(..., "main"|"carousel", ...)`、`addAssets(..., "main"|"carousel", ...)` 全部在返回前调用 `normalizeManifest()`；只允许原图池按钮调用 `promoteToLibrary()`。

- [ ] **Step 7: 验证。**

Run:

```bash
cd local-runtime && python -m pytest tests/test_product_processing_preview_images.py -q
cd ../web-frontend && npx tsc --noEmit
```

Expected: PASS。另运行 TypeScript contract test；若仓库未安装 `tsx`，记录该缺失并使用等价 Node 文本契约检查，不安装依赖。

---

### Task 2: 将 V2 原始素材投影为可安全引用的预检代理

**Files:**

- Modify: `local-runtime/wh_local/modules/product_processing/media_asset_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/preview_image_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `web-frontend/src/modules/product_processing/types/index.ts`
- Test: `local-runtime/tests/test_product_processing_media_api.py`
- Test: `local-runtime/tests/test_product_processing_preview_image_projection.py`（新建）

**Consumes:** `MediaAssetService.list_draft_media()`、`PreviewImageService.project_item_images()`、`PreviewImageAssetRow.media_asset_id`。

**Produces:** 每个 V2 binding 一条稳定的预检代理；代理直接展示统一媒体内容，带有 `bucket`、`source_kind`、`media_status` 和 `media_asset_id`，不复制字节。

- [ ] **Step 1: 写失败测试：原始 V2 资源必须出现在预检投影。**

```python
def test_v2_projection_exposes_original_main_gallery_sku_and_detail_without_copying(tmp_path):
    service, task_id, draft_id = seeded_v2_product_with_ready_source_media(tmp_path)
    preview = service.task_preview(task_id, workspace_id="ws")
    assets = preview["items"][0]["assets"]
    assert {(x["bucket"], x["source_kind"]) for x in assets} >= {
        ("source", "main"), ("source", "gallery"), ("source", "sku"), ("source", "detail"),
    }
    source = next(x for x in assets if x["source_kind"] == "main")
    assert source["media_asset_id"]
    assert source["preview_url"].startswith("/api/product-processing/media-assets/")
    assert source["managed_path"] == ""  # only internal row assertion; never an API field
```

- [ ] **Step 2: 确认测试失败。**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_preview_image_projection.py -q`

Expected: FAIL，当前 `project_item_images()` 仅从旧 result URL/预览资产构建列表，V2 原图没有预检代理。

- [ ] **Step 3: 增加受控统一媒体文件解析。**

在 `MediaAssetService` 增加仅服务端使用的方法：

```python
def require_ready_managed_file(self, asset_id: str, *, workspace_id: str) -> tuple[Path, str]:
    """Return a workspace-scoped ready unified asset; never accept a URL or raw path."""
```

它只接受 ready 状态和 `ProductProcessingAssets.require_workspace_media_asset()` 校验后的路径。不得暴露 managed path 给 API。

- [ ] **Step 4: 实现稳定预检代理登记。**

在 `PreviewImageService` 增加 `register_media_proxy()`。identity 必须固定为：

```text
media-proxy:{workspace_id}:{task_id}:{product_draft_id}:{media_asset_id}
```

代理 `PreviewImageAssetRow` 写入 `media_asset_id`，`managed_path/source_url` 保持空；新增 repository 查询 `list_media_proxies(task_id, draft_id, workspace_id)`。`public_asset()` 在发现 `media_asset_id` 时从 `MediaAssetService.public_asset()` 生成签名 `/media-assets/{id}/content` URL，并返回：

```json
{
  "bucket": "source|processed",
  "source_kind": "main|gallery|sku|detail|",
  "media_asset_id": "...",
  "media_status": "ready|pending|materializing|retryable|failed"
}
```

bucket 只由媒体 origin 决定：`remote_source` 为 source，其余为 processed。`source_kind` 只在 source 时从 binding role 产生。不得根据 URL 或文件名猜测来源。

- [ ] **Step 5: 在 `project_item_images()` 先投影 V2 bindings，再处理旧兼容数据。**

对 `media_contract_version >= 2`，从 draft media bindings 登记代理并按 `sort_order` 输出；旧 `image_manifest` URL 只作为兼容输入，不能再生成任何 `managed_path="/api/..."` 的行。继续调用现有 URL 修复兜底，保证历史空卡可显示，但不得将它作为新数据通路。

- [ ] **Step 6: 实现预检定稿的统一媒体读取。**

预检最终发布和 workbook 生成在读取被选择的代理时，若有 `media_asset_id`，调用 `require_ready_managed_file()` 读取统一媒体；若仍是传统 preview asset，保留既有读取路径。这样原图手动加入素材库后可被设为主图/轮播/详情并正确导出，不需要重复下载、复制或提前发布 COS。

- [ ] **Step 7: 验证。**

Run:

```bash
cd local-runtime && python -m pytest \
  tests/test_product_processing_preview_image_projection.py \
  tests/test_product_processing_media_api.py \
  tests/test_product_processing_media_materialization.py \
  tests/test_product_processing_media_ingress.py -q
```

Expected: PASS。断言 pending/failed 素材没有 preview URL，ready 素材有 V2 签名 URL，且不会额外调用外网 fetcher。

---

### Task 3: 保存“手动加入素材库”并对素材库执行强校验

**Files:**

- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/preview_image_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/api/schemas.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Test: `local-runtime/tests/test_product_processing_preview_image_projection.py`
- Test: `local-runtime/tests/test_product_processing_preview_finalize.py`

**Consumes:** Task 1 manifest `library_asset_ids`、Task 2 proxy 记录。

**Produces:** 原图“加入素材库”成为保存后的草稿状态；未加入的原图不可被输出清单直接引用。

- [ ] **Step 1: 写失败测试：原图仅加入素材库后不进入导出。**

```python
def test_promoted_source_is_persisted_but_not_exported_until_selected(service):
    item, source_proxy = v2_preview_item_with_source_proxy(service)
    saved = service.save_product_preview(
        item.task_id,
        [{
            "product_draft_id": item.draft_id,
            "expected_preview_revision": 0,
            "expected_result_version": item.result_version,
            "overrides": {"image_manifest_v2": {"library_asset_ids": [source_proxy.id]}},
        }],
        workspace_id="ws",
    )
    projected = service.task_preview(item.task_id, workspace_id="ws")["items"][0]
    assert source_proxy.id in projected["image_manifest"]["library_asset_ids"]
    assert source_proxy.id not in projected["image_manifest"]["carousel_asset_ids"]
```

- [ ] **Step 2: 确认失败。**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_preview_image_projection.py -q`

Expected: FAIL，保存 schema/repository 当前会丢弃 `library_asset_ids`。

- [ ] **Step 3: 实现保存和校验。**

`save_preview_manifests()` 允许 library IDs，但必须验证它们属于同一 workspace/task/draft 的代理或预览资产。`library_asset_ids` 不得让任意 ID 绕过任务归属检查；`carousel/detail/main` 仍按现有导出资产校验。保存后 `project_item_images()` 计算：

```text
素材库 = 所有 processed 代理 + 所有 upload 资产 + manifest.library_asset_ids 对应的 source 代理
原图池 = 所有 source 代理（无论是否已加入素材库，加入后显示“已在素材库”）
```

- [ ] **Step 4: 写失败测试：选入原图作为主图会自动成为轮播第 1 项。**

```python
def test_source_selected_as_main_is_serialized_as_first_carousel_item(service):
    item, source_proxy = v2_preview_item_with_source_proxy(service)
    save_manifest(service, item, {
        "library_asset_ids": [source_proxy.id],
        "main_asset_id": source_proxy.id,
        "carousel_asset_ids": [],
    })
    manifest = service.task_preview(item.task_id, workspace_id="ws")["items"][0]["image_manifest"]
    assert manifest["main_asset_id"] == source_proxy.id
    assert manifest["carousel_asset_ids"] == [source_proxy.id]
```

- [ ] **Step 5: 实现并验证。**

保存入口统一调用 Task 1 的 domain normalizer；若 source 没有先加入 `library_asset_ids` 却被设置为 main/carousel/detail，返回 422，错误文案为“请先将处理前图片加入素材库”。

Run:

```bash
cd local-runtime && python -m pytest \
  tests/test_product_processing_preview_image_projection.py \
  tests/test_product_processing_preview_finalize.py -q
```

Expected: PASS。

---

### Task 4: 重写预检图片区为“原图池 + 素材库 + 导出清单”

**Files:**

- Modify: `web-frontend/src/modules/product_processing/components/PrecheckImageManager.tsx`
- Create: `web-frontend/src/modules/product_processing/components/PrecheckSourcePool.tsx`
- Create: `web-frontend/src/modules/product_processing/components/PrecheckMaterialLibrary.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/data/precheckImageModel.ts`
- Modify: `web-frontend/src/modules/product_processing/styles/product-processing.css`
- Test: `web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts`
- Test: `web-frontend/src/modules/product_processing/data/precheckImageLayoutContract.test.ts`（新建）

**Consumes:** Task 1 manifest helpers、Task 2 `PreviewImageAsset.bucket/source_kind/media_status`、Task 3 persisted library IDs。

**Produces:** 无重复 V1/V2 图片区、可对照的两个池子、保持现有操作的素材库和以轮播第 1 张为主图的导出清单。

- [ ] **Step 1: 写前端源文件契约测试。**

```ts
test("precheck renders source pool before processed material library", () => {
  assert.match(managerSource, /处理前图片池/);
  assert.match(managerSource, /处理后素材库/);
  assert.doesNotMatch(managerSource, /统一素材状态/);
});

test("source-pool cards only promote to library", () => {
  const sourceCard = extractComponentSource(managerSource, "PrecheckSourcePool");
  assert.match(sourceCard, /加入素材库/);
  assert.doesNotMatch(sourceCard, /设为主图/);
  assert.doesNotMatch(sourceCard, /加入轮播/);
});

test("main is rendered as carousel item one instead of a separate panel", () => {
  assert.match(managerSource, /轮播图.*第 1 张即主图/s);
  assert.doesNotMatch(managerSource, /precheck-main-section/);
});
```

- [ ] **Step 2: 确认前端契约失败。**

Run: `npx tsx --test web-frontend/src/modules/product_processing/data/precheckImageLayoutContract.test.ts`

Expected: FAIL，当前组件并列渲染“统一素材状态 + 可用素材库 + 独立主图”。

- [ ] **Step 3: 创建 `PrecheckSourcePool`。**

按 `source_kind` 固定顺序 `main → gallery → sku → detail` 分四个子区；每张卡显示业务标签、同步状态和预览。ready 卡的唯一业务操作是：

```tsx
<button disabled={disabled || alreadyInLibrary} onClick={() => onPromote(asset.id)}>
  {alreadyInLibrary ? "已在素材库" : "加入素材库"}
</button>
```

retryable/failed 卡使用现有 `onRetryMediaAsset(asset.media_asset_id)`；pending/materializing 卡不可选、不显示虚假的“无预览”。

- [ ] **Step 4: 创建 `PrecheckMaterialLibrary`。**

库卡按 `AI 处理图 → 尺寸图 → 本地导入 → 已选原图` 排序。保留并复用当前的预览、设主图、加入轮播、加入详情按钮。对手动加入的原图增加“移出素材库”；移出只删除 `library_asset_ids`，如果该图正在 carousel/detail/main，按钮禁用并提示先从导出清单移除。

- [ ] **Step 5: 合并主图与轮播输出区。**

删除独立“主图” section。轮播 section 标题改为：`轮播图（第 1 张即主图）`；第 1 张卡显示“主图”徽标。点击“设为主图”调用 `selectMainAsset()`，使目标图移动到 index 0。任意轮播排序后调用 `normalizeManifest()`；删除第 1 张自动令下一张成为主图。轮播为空时 main 必须为空。

- [ ] **Step 6: 在页面层停止并列渲染旧 V2 registry。**

`ProductProcessingPrecheckPage` 不再对每个草稿单独调用 `getDraftMedia()` 供 UI 拼装；`task_preview` 的统一 `assets` 投影是组件唯一输入。保留重试 callback，调用资产的 `media_asset_id`。删除 `draftMedia` 状态和其加载失败处理，避免同一张图片在“统一素材状态”和“可用素材库”出现两次。

- [ ] **Step 7: 调整布局，消除左侧空白。**

在 `product-processing.css`：

```css
.precheck-card { display: grid; gap: 16px; }
.precheck-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
.precheck-fields { display: grid; grid-template-columns: minmax(280px, .9fr) minmax(0, 1.4fr); }
.precheck-images { min-width: 0; display: grid; gap: 18px; }
.precheck-asset-grid { grid-template-columns: repeat(auto-fill, minmax(164px, 1fr)); }
```

在窄屏时信息区退化为单列。禁止为 `.precheck-fields` 分配固定左栏宽度，也禁止图片区设置导致空列的 `grid-column: 2`。

- [ ] **Step 8: 验证前端。**

Run:

```bash
cd web-frontend && npx tsc --noEmit
npx tsx --test src/modules/product_processing/data/precheckImageModel.test.ts \
  src/modules/product_processing/data/precheckImageLayoutContract.test.ts
```

Expected: PASS。若没有 `tsx`，不要安装依赖；运行等价 Node 源码断言并在交付说明中记录限制。

---

### Task 5: 全链路验收、兼容和人工验证

**Files:**

- Modify: `local-runtime/tests/test_product_processing_media_api.py`
- Modify: `local-runtime/tests/test_product_processing_dimension_canvas.py`
- Modify: `web-frontend/src/modules/product_processing/data/precheckImageLayoutContract.test.ts`
- Verify only: `local-runtime/wh_local/workbench.sqlite3`（不得写入或清洗）

**Consumes:** Tasks 1–4。

**Produces:** 对 PP-000181 同类商品的自动化与人工验收证据。

- [ ] **Step 1: 写端到端失败测试。**

```python
def test_v2_product_precheck_has_readonly_sources_and_processed_library_after_canvas_acceptance(service):
    task_id, draft_id = seeded_full_v2_product(service)
    before = service.task_preview(task_id, workspace_id="ws")["items"][0]
    assert any(x["bucket"] == "source" for x in before["assets"])
    assert any(x["bucket"] == "processed" for x in before["assets"])

    source = next(x for x in before["assets"] if x["bucket"] == "source")
    save_library_membership(service, before, [source["id"]])
    after = service.task_preview(task_id, workspace_id="ws")["items"][0]
    assert source["id"] in after["image_manifest"]["library_asset_ids"]
```

- [ ] **Step 2: 确认失败并完成实现。**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_media_api.py -q`

Expected: 初始 FAIL；Task 1–4 完成后 PASS。

- [ ] **Step 3: 跑完整相关回归。**

Run:

```bash
cd local-runtime && python -m pytest \
  tests/test_product_processing_preview_images.py \
  tests/test_product_processing_preview_image_projection.py \
  tests/test_product_processing_preview_finalize.py \
  tests/test_product_processing_media_api.py \
  tests/test_product_processing_media_materialization.py \
  tests/test_product_processing_media_ingress.py \
  tests/test_product_processing_dimension_canvas.py -q
cd .. && git diff --check
```

Expected: 全部 PASS，`git diff --check` 无输出。

- [ ] **Step 4: 人工验收（新建测试商品，不清洗历史）。**

1. 导入一个含主图、轮播、SKU 图、详情图的 1688 链接并完成处理。
2. 打开预检：原图仅出现在“处理前图片池”且按四类区分；处理图仅出现在“处理后素材库”。
3. 点击一张原始 SKU 图“加入素材库”，刷新页面后仍在库中；未点击的原图仍不在库中。
4. 在素材库将该 SKU 图设为主图：它成为轮播第一张，并显示主图徽标。
5. 拖动/按钮交换轮播顺序：新的第一张自动显示为主图；删除第一张后下一张成为主图。
6. 将一张素材加入详情图，保存、重新加载、完成预审并导出；确认导出主图、轮播、详情图顺序与页面一致。
7. 尺寸画布交回后刷新预检：尺寸图只出现在处理后素材库，且不产生“无预览”“等待同步”或重复卡。
8. 在浏览器缩放至 1280px 与 1440px：商品编号在顶部、图片区全宽，没有左侧大面积空白。

## 回滚与风险控制

- 前端可通过 `media_contract_version >= 2` 启用新布局；V1 草稿继续使用现有兼容布局，禁止混用两套 identity。
- 新增 `library_asset_ids` 是 JSON 向后兼容字段；旧草稿缺失时按空数组处理。
- 任何 V2 代理无法解析 ready 统一媒体时返回 status/error，不降级为本地路径或未签名 URL。
- 如果预检定稿解析统一媒体失败，只允许显示稳定错误并阻止导出；不得把失败图片替换成来源 URL 或触发同步外部下载。
- 不为 PP-000181 或任何历史行执行 SQL 修复；仅通过重新加载的读模型提供兼容展示。

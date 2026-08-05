# Daily Collection Draft Pools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route webpage-manual and immediately returned OneBound API collection into one product-draft table with distinct source views and asynchronously persisted source images.

**Architecture:** `product_processing_drafts` remains the sole draft record. Ingress assigns `web_manual_capture` or `onebound_api`; related source-image rows retain each remote URL and track the managed copy. The product-processing page consumes filtered API responses, while 点小蜜 is outside the implementation.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, SQLite, pytest, React 18, TypeScript, Vite.

## Global Constraints

- Do not introduce a second product-draft table or put image bytes in SQLite.
- Preserve original image URLs after copying files to managed storage.
- Valid new collection sources are exactly `web_manual_capture` and `onebound_api`.
- Each candidate returned by a OneBound API collection run must create or update an `onebound_api` draft immediately.
- Failed image copies remain retryable and never remove the draft.
- Do not modify any 点小蜜 integration, status, or user interface.

## File Structure

- Create: `local-runtime/wh_local/modules/product_processing/migrations/002_source_image_sync.sql`
- Create: `local-runtime/tests/product_processing/test_source_image_sync.py`
- Create: `web-frontend/src/modules/product_processing/api/productProcessingApi.ts`
- Create: `web-frontend/src/modules/product_processing/pages/ProductProcessingPage.tsx`
- Create: `web-frontend/src/modules/product_processing/styles/product-processing.css`
- Create: `web-frontend/src/shared/api/apiClient.ts`
- Modify: `local-runtime/wh_local/db.py`
- Modify: `local-runtime/wh_local/data_collection/routes.py`
- Modify: `local-runtime/wh_local/app/main.py`
- Modify: `local-runtime/wh_local/modules/product_processing/{api/router.py,service.py,infrastructure/{orm.py,assets.py,repository.py}}`
- Modify: `local-runtime/tests/data_collection/{test_sqlite_ingestion.py,test_daily_selection_hardening.py}`
- Modify: `web-frontend/src/{main.tsx,app/layout/WorkspaceShell.tsx}`
- Modify: `web-frontend/src/modules/daily_selection/api/dailySelectionApi.ts`

### Task 1: Add durable source-image synchronization

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/migrations/002_source_image_sync.sql`
- Modify: `local-runtime/wh_local/db.py:347-380`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/orm.py:124-138`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/assets.py:19-78`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py:363-416,555-564`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:92-156,381-392`
- Test: `local-runtime/tests/product_processing/test_source_image_sync.py`

**Interfaces:**
- Consumes: `fetch_public_image(url) -> FetchedPublicImage` from `wh_local.data_collection.public_image_fetch`.
- Produces: `sync_draft_source_images(draft_id: int, workspace_id: str = "local") -> dict[str, int]` and `retry_draft_source_images(draft_id: int, workspace_id: str = "local") -> dict[str, int]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_draft_seeds_pending_source_and_detail_images(service):
    draft, _ = service.create_draft({
        "source_type": "web_manual_capture", "candidate_id": "plugin:temu:42",
        "title": "杯子", "source_ref": "https://www.temu.com/goods.html?goods_id=42",
        "image_url": "https://cdn.example.test/main.jpg",
        "source_image_urls": ["https://cdn.example.test/main.jpg"],
        "source_detail_image_urls": ["https://cdn.example.test/detail.jpg"],
    })
    images = service.source_images(draft_id=draft["id"])["images"]
    assert [(row["kind"], row["sync_status"]) for row in images] == [
        ("source", "pending"), ("detail", "pending")]

def test_sync_keeps_remote_url_and_makes_failure_retryable(service_with_fetcher):
    draft = make_pending_draft(service_with_fetcher)
    assert service_with_fetcher.sync_draft_source_images(draft["id"]) == {"ready": 1, "failed": 1}
    images = service_with_fetcher.source_images(draft_id=draft["id"])["images"]
    assert images[0]["url"] == "https://cdn.example.test/main.jpg"
    assert images[0]["sync_status"] == "ready" and images[0]["local_path"]
    assert images[1]["sync_status"] == "failed" and images[1]["sync_error"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest -q local-runtime/tests/product_processing/test_source_image_sync.py`

Expected: FAIL because synchronization status, seeding, and service methods do not exist.

- [ ] **Step 3: Add the migration and models**

```sql
ALTER TABLE product_processing_source_images ADD COLUMN sync_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE product_processing_source_images ADD COLUMN sync_error TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_product_processing_source_images_sync_status
    ON product_processing_source_images (sync_status);
```

Register `product_processing:002_source_image_sync` after `001` in `db.py`; add `sync_status` and `sync_error` to `SourceImageAssetRow` and its response projection. Add `ProductProcessingAssets.save_source_image(content, filename, content_type)` using a SHA-256 file name under `source-image-library`.

- [ ] **Step 4: Implement idempotent seeding and copy transitions**

```python
def sync_draft_source_images(self, draft_id: int, workspace_id: str = "local") -> dict[str, int]:
    self.get_draft(draft_id, workspace_id)
    ready = failed = 0
    for image in self.repository.claim_syncable_source_images(draft_id, workspace_id):
        try:
            fetched = self._public_image_fetcher.fetch(image["url"])
            path = self.assets.save_source_image(fetched.content, fetched.final_url, fetched.media_type)
        except Exception as error:
            self.repository.fail_source_image(image["id"], str(error), workspace_id)
            failed += 1
        else:
            self.repository.complete_source_image(image["id"], str(path), workspace_id)
            ready += 1
    return {"ready": ready, "failed": failed}
```

Inject the public fetcher into `ProductProcessingService` for test replacement. Seed source/detail image rows whenever a new or revived draft is created. Repository claims must change `pending` or `failed` to `syncing` transactionally; completion stores `local_path`, and failure stores a concise error without modifying `url`.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q local-runtime/tests/product_processing/test_source_image_sync.py`

Expected: PASS.

Run: `git add local-runtime/wh_local/db.py local-runtime/wh_local/modules/product_processing/migrations/002_source_image_sync.sql local-runtime/wh_local/modules/product_processing/infrastructure/orm.py local-runtime/wh_local/modules/product_processing/infrastructure/assets.py local-runtime/wh_local/modules/product_processing/infrastructure/repository.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/product_processing/test_source_image_sync.py && git commit -m "feat: persist product draft source images"`

### Task 2: Enforce source-specific collection ingress

**Files:**
- Modify: `local-runtime/wh_local/data_collection/routes.py:309-372,521-557`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:319-365,394-454`
- Test: `local-runtime/tests/data_collection/test_sqlite_ingestion.py:301-363`
- Test: `local-runtime/tests/data_collection/test_daily_selection_hardening.py:115-136`

**Interfaces:**
- Produces: valid plugin products as `web_manual_capture` and returned OneBound run candidates as `onebound_api`.
- Produces: a background image-sync task only after a product draft exists.

- [ ] **Step 1: Write failing source-routing tests**

```python
def test_plugin_product_capture_creates_manual_draft(client, database_path):
    session = create_plugin_session(client)
    response = client.post("/plugin/product-capture/draft", json={
        "session_token": session["session_token"], "product": valid_temu_product()})
    assert response.status_code == 200
    assert stored_source_type(database_path) == "web_manual_capture"

def test_successful_temu_link_result_creates_manual_draft(client, database_path):
    session, command = queue_temu_link(client)
    response = client.post("/plugin/result", json={
        "session_token": session["session_token"], "command_id": command["command_id"],
        "status": "succeeded", "result": {"product": valid_temu_product()}})
    assert response.status_code == 200
    assert stored_source_type(database_path) == "web_manual_capture"

def test_preview_immediately_creates_api_drafts(client, database_path):
    response = client.post("/desktop/daily-selection/preview", json=valid_api_criteria())
    assert response.status_code == 200
    assert stored_source_types(database_path) == ["onebound_api"]
```

- [ ] **Step 2: Run the routing tests to verify failure**

Run: `pytest -q local-runtime/tests/data_collection/test_sqlite_ingestion.py local-runtime/tests/data_collection/test_daily_selection_hardening.py`

Expected: FAIL because current values are `plugin_capture` and `daily_selection_handoff`, the preview route does not write API drafts, and a Temu command result remains only a queue result.

- [ ] **Step 3: Normalize manual plugin products and immediate API candidates**

```python
def _plugin_product_to_draft(product: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **sanitized_product, "source_type": "web_manual_capture",
        "source_platform": platform, "candidate_id": candidate_id,
        "source_ref": source_ref, "title": title, "product_name": title,
        "image_url": image_url, "source_image_urls": images,
    }
```

After a successful `temu_link_capture` result, accept only `result["product"]` mappings through the same normalizer and create/reuse the manual draft. Invalid or incomplete result payloads retain command diagnostics but create no draft. After both `preview` and `preview-from-1688-link` return a run, create or reuse an `onebound_api` draft for each returned candidate, retaining its `selection_run_id`, collection mode, and source evidence. `daily_selection_runs` remains the collection-run audit record; it must not become a second draft pool. The existing confirmation endpoint only marks selected drafts for product processing and must not create additional drafts.

- [ ] **Step 4: Schedule background image work at successful ingress points**

```python
background_tasks.add_task(
    plugin_draft_writer.sync_draft_source_images, draft["id"], workspace_id
)
```

Inject `BackgroundTasks` into plugin-draft, plugin-result, `preview`, and `preview-from-1688-link` routes. Schedule only after a draft transaction succeeds; if a task cannot run, its source-image rows remain `pending` for the retry endpoint.

- [ ] **Step 5: Verify and commit**

Run: `pytest -q local-runtime/tests/data_collection/test_sqlite_ingestion.py local-runtime/tests/data_collection/test_daily_selection_hardening.py`

Expected: PASS.

Run: `git add local-runtime/wh_local/data_collection/routes.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/data_collection/test_sqlite_ingestion.py local-runtime/tests/data_collection/test_daily_selection_hardening.py && git commit -m "feat: separate manual and api draft ingress"`

### Task 3: Expose source views and image retries in the local API

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/{api/router.py,service.py,infrastructure/repository.py}`
- Modify: `local-runtime/wh_local/app/main.py:19-34,98-112`
- Test: `local-runtime/tests/product_processing/test_source_image_sync.py`

**Interfaces:**
- Produces: `GET /product-processing/drafts?source_type=web_manual_capture|onebound_api`.
- Produces: `POST /product-processing/drafts/{draft_id}/source-images/retry`.

- [ ] **Step 1: Write failing HTTP tests**

```python
def test_drafts_filter_by_source_type(client):
    create_draft(client, source_type="web_manual_capture", candidate_id="manual-1")
    create_draft(client, source_type="onebound_api", candidate_id="api-1")
    response = client.get("/product-processing/drafts", params={"source_type": "onebound_api"})
    assert [item["candidate_id"] for item in response.json()["drafts"]] == ["api-1"]

def test_retry_source_images_schedules_existing_draft(client, draft_id):
    assert client.post(f"/product-processing/drafts/{draft_id}/source-images/retry").status_code == 200
```

- [ ] **Step 2: Implement the query and retry contract**

```python
@router.post("/drafts/{draft_id}/source-images/retry")
def retry_source_images(draft_id: int, background_tasks: BackgroundTasks,
                        workspace_id: str = Header(default="local", alias="X-Workspace-ID")):
    draft = _call(service.get_draft, draft_id, _workspace(workspace_id))
    background_tasks.add_task(service.retry_draft_source_images, draft_id, _workspace(workspace_id))
    return {"draft": draft, "sync": {"status": "scheduled"}}
```

Pass optional `source_type` through router, service, and repository. Reject values outside the two documented collection values with 422. Register `create_product_processing_router(product_processing)` in `create_app` using the same singleton service passed to daily-selection.

- [ ] **Step 3: Verify and commit**

Run: `pytest -q local-runtime/tests/product_processing/test_source_image_sync.py local-runtime/tests/data_collection/test_sqlite_ingestion.py`

Expected: PASS.

Run: `git add local-runtime/wh_local/app/main.py local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/wh_local/modules/product_processing/infrastructure/repository.py local-runtime/tests/product_processing/test_source_image_sync.py && git commit -m "feat: expose product draft source views"`

### Task 4: Render the product-processing source views

**Files:**
- Create: `web-frontend/src/modules/product_processing/api/productProcessingApi.ts`
- Create: `web-frontend/src/modules/product_processing/pages/ProductProcessingPage.tsx`
- Create: `web-frontend/src/modules/product_processing/styles/product-processing.css`
- Create: `web-frontend/src/shared/api/apiClient.ts`
- Modify: `web-frontend/src/app/layout/WorkspaceShell.tsx:1-11,82-91`
- Modify: `web-frontend/src/main.tsx`
- Modify: `web-frontend/src/modules/daily_selection/api/dailySelectionApi.ts:8-32`

**Interfaces:**
- Consumes: the source-filtered drafts API and source-image retry route from Task 3.
- Produces: `apiRequest<T>(path: string, init?: RequestInit): Promise<T>` from `shared/api/apiClient.ts` for all local frontend modules.
- Produces: a `ProductProcessingPage` for the existing `product_processing` navigation id.

- [ ] **Step 1: Extract the shared request client and add typed product-processing requests**

```ts
export type DraftSourceType = "web_manual_capture" | "onebound_api";
export type ProductDraft = {
  id: number; source_type: DraftSourceType; source_ref: string; title: string;
  image_url: string; image_path: string;
  raw_payload: { source_platform?: string; collection_mode?: string };
};
export async function listProductDrafts(sourceType?: DraftSourceType): Promise<ProductDraft[]> {
  const query = sourceType ? `?source_type=${encodeURIComponent(sourceType)}` : "";
  return (await apiRequest<{ drafts: ProductDraft[] }>(`/product-processing/drafts${query}`)).drafts;
}
```

Move the existing token, authorization-header, JSON-body, and error-detail handling from `dailySelectionApi.ts` into `shared/api/apiClient.ts` as the exported `apiRequest`. Change `dailySelectionApi.ts` to import it, then use the same helper in `productProcessingApi.ts`; do not duplicate fetch or error parsing logic.

- [ ] **Step 2: Implement the three views and image state**

```tsx
const VIEWS = [
  { key: "all", label: "全部草稿", sourceType: undefined },
  { key: "manual", label: "网页手动采集", sourceType: "web_manual_capture" },
  { key: "api", label: "万邦 API 采集", sourceType: "onebound_api" },
] as const;
```

Fetch when the selected view changes. Each card displays its source label, platform, source link, API mode when supplied, and image state. Prefer `/product-processing/drafts/{id}/image` when `image_path` exists; otherwise show the remote source URL. A failed image shows “图片待补齐” and the retry control. Do not render any 点小蜜 action.

- [ ] **Step 3: Register page and styles**

```tsx
{activeModuleId === "product_processing" && <ProductProcessingPage />}
```

Use a `product-processing-page` root selector and import the CSS from `main.tsx`.

- [ ] **Step 4: Verify and commit**

Run: `cd web-frontend && npm run build`

Expected: PASS with no TypeScript errors.

Run: `git add web-frontend/src/modules/product_processing web-frontend/src/app/layout/WorkspaceShell.tsx web-frontend/src/main.tsx && git commit -m "feat: add product draft source views"`

### Task 5: Complete regression and documentation

**Files:**
- Modify: `local-runtime/wh_local/data_collection/README.md`

- [ ] **Step 1: Document the accepted source flow**

```markdown
| 来源 | 草稿来源类型 | 入库时机 |
| --- | --- | --- |
| Temu / 1688 网页手动采集 | `web_manual_capture` | 插件采集成功后 |
| 万邦 API | `onebound_api` | API 采集结果返回后 |
```

State that original URLs remain saved while managed copies synchronize asynchronously, and that 点小蜜 is outside this module.

- [ ] **Step 2: Run final checks**

Run: `pytest -q local-runtime/tests/data_collection local-runtime/tests/product_processing local-runtime/tests/price_verification && (cd web-frontend && npm run build) && git diff --check`

Expected: all backend tests and the frontend build pass with no whitespace error.

- [ ] **Step 3: Commit documentation**

Run: `git add local-runtime/wh_local/data_collection/README.md && git commit -m "docs: describe daily collection draft pools"`

## Plan Self-Review

- Spec coverage: Tasks 1-3 implement one physical pool, source labels, safe asynchronous image copies, retries, and immediate API ingress. Task 4 provides the three UI views. Task 5 verifies the scope boundary.
- Placeholder scan: no incomplete implementation markers or unspecified work remains.
- Type consistency: all tasks use the same two source values and the `sync_draft_source_images` / `retry_draft_source_images` service methods.

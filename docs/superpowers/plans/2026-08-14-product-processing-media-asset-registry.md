# Product Processing Media Asset Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make every image introduced by newly confirmed product drafts flow through a durable asset_id registry from intake through precheck, dimension canvas, and final publication.

**Architecture:** Add canonical media asset and business-binding tables. New drafts use media_contract_version=2; old drafts remain on the existing URL/path flow. Remote source image materialization is lease-based and recoverable; dimension canvas copies ready assets into its own workspace snapshot.

**Tech Stack:** Python, FastAPI, SQLAlchemy, SQLite WAL, Pydantic, Pillow, React, TypeScript, pytest.

## Global Constraints

- Do not backfill, materialize, delete, or modify history with media_contract_version=1.
- asset_id and binding_id are the only V2 image identities; URLs are display-only.
- Never accept or reconstruct a server path from a client URL.
- Only ready assets with path, hash, type, width, and height may render or publish.
- Preserve V1 endpoints and behavior for old drafts during rollout.

---

## Task 1: Add V2 schema and storage primitives

**Files:**

- Create: local-runtime/wh_local/modules/product_processing/infrastructure/media_asset_orm.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/orm.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_orm.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_orm.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/database.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/assets.py
- Test: local-runtime/tests/test_product_processing_media_assets.py

**Produces:** MediaAssetRow, MediaBindingRow, ProductDraftRow.media_contract_version, PreviewImageAssetRow.media_asset_id, DimensionCanvasAssetRow.source_media_asset_id, save_media_asset, and require_workspace_media_asset.

- [ ] Write failing tests proving a fresh DB contains two media tables and indexes; V1 drafts default to version 1; V2 drafts accept version 2; media storage rejects URLs, files outside its workspace root, and missing files.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_assets.py

  Expected: FAIL because V2 schema and storage methods do not exist.

- [ ] Create MediaAssetRow and MediaBindingRow with the exact fields in the approved design. Import them before Base.metadata.create_all.
- [ ] Extend the existing SQLite repair path with additive columns only: media_contract_version, media_asset_id, source_media_asset_id. Do not insert rows or modify old records.
- [ ] Add a workspace-scoped content-addressed media-assets root. save_media_asset must verify SHA-256 before atomic write; require_workspace_media_asset must only resolve the matching workspace root.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_assets.py local-runtime/tests/test_product_processing_preview_images.py

  Expected: PASS.

## Task 2: Implement canonical asset registration, bindings, and leases

**Files:**

- Create: local-runtime/wh_local/modules/product_processing/infrastructure/media_asset_repository.py
- Create: local-runtime/wh_local/modules/product_processing/media_asset_service.py
- Test: local-runtime/tests/test_product_processing_media_materialization.py

**Consumes:** MediaAssetRow, MediaBindingRow, ProductProcessingAssets, fetch_public_image, validate_preview_image.

**Produces:**

    register_remote_asset(workspace_id, source_url)
    register_local_asset(workspace_id, origin, content, content_type)
    bind_asset(workspace_id, asset_id, product_draft_id, role, ...)
    materialize_pending(workspace_id=None, limit=20)
    read_ready_asset(asset_id, workspace_id)

- [ ] Write failing tests for one ready remote asset, one retryable download failure, one permanent invalid-image failure, one expired materialization lease, and two different bindings sharing one source URL.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_materialization.py

  Expected: FAIL because the repository and service do not exist.

- [ ] Implement register_remote_asset using canonical URL SHA-256 and register_local_asset using verified byte SHA-256.
- [ ] Implement binding_key from workspace, draft, role, slot, SKU, source identity, and sort order. Repeated registration must return existing records.
- [ ] Implement claim/complete/fail lease transitions. Use fetch_public_image and validate_preview_image. Classify invalid URLs/non-images as failed and network/429/5xx as retryable.
- [ ] Implement public_asset so it returns asset ID, state, metadata, bounded errors, and a display URL only for ready assets.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_assets.py local-runtime/tests/test_product_processing_media_materialization.py

  Expected: PASS.

## Task 3: Convert new daily-selection intake, including SKU media

**Files:**

- Modify: local-runtime/wh_local/modules/product_processing/service.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/repository.py
- Modify: local-runtime/wh_local/data_collection/routes.py
- Test: local-runtime/tests/test_product_processing_media_ingress.py
- Test: local-runtime/tests/test_product_processing_handoff_reentry.py

**Consumes:** DailySelectionHandoffEnvelope and MediaAssetService.

**Produces:** create_draft_with_media and V2-only materialization scheduling.

- [ ] Write a failing handoff fixture with main, gallery, detail, and two SKU images. Assert media_contract_version=2; all roles are bound; SKU ID and label are retained; handoff replay adds no duplicate binding.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_ingress.py local-runtime/tests/test_product_processing_handoff_reentry.py

  Expected: FAIL because handoff intake currently writes only legacy source image rows.

- [ ] Add a repository transaction that creates the V2 draft, registers remote assets, creates bindings, and saves the handoff receipt before upstream acknowledgement.
- [ ] Extract source_variant_records image_url into role=sku bindings. A URL already present in gallery must still receive its own SKU binding.
- [ ] Schedule materialize_pending for V2 drafts after transaction commit. Keep sync_draft_source_images untouched for V1 drafts.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_ingress.py local-runtime/tests/test_product_processing_handoff_reentry.py

  Expected: PASS.

## Task 4: Add V2 media APIs and precheck manifests

**Files:**

- Modify: local-runtime/wh_local/modules/product_processing/api/router.py
- Modify: local-runtime/wh_local/modules/product_processing/api/schemas.py
- Modify: local-runtime/wh_local/modules/product_processing/service.py
- Modify: local-runtime/wh_local/modules/product_processing/preview_image_service.py
- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/preview_image_repository.py
- Test: local-runtime/tests/test_product_processing_preview_images.py
- Test: local-runtime/tests/test_product_processing_media_assets.py

**Produces:**

    GET  /product-processing/drafts/{draft_id}/media
    GET  /product-processing/media-assets/{asset_id}/content
    POST /product-processing/media-assets/{asset_id}/retry
    image_manifest_v2

- [ ] Write failing tests showing a V2 media response is grouped by main/gallery/detail/sku/carousel/dimension, contains no managed_path, and creates URLs only for ready assets.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_preview_images.py local-runtime/tests/test_product_processing_media_assets.py

  Expected: FAIL because V2 APIs and manifests do not exist.

- [ ] Add the list/content/retry endpoints with workspace, draft/task ownership, state, and signature validation.
- [ ] Route generated images and precheck uploads through register_local_asset; set PreviewImageAssetRow.media_asset_id.
- [ ] Write V2 result manifests using asset IDs. Retain V1 preview behavior only when media_asset_id is empty.
- [ ] Make finalization resolve V2 bytes through MediaAssetService and snapshot asset IDs plus content hashes.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_preview_images.py local-runtime/tests/test_product_processing_media_assets.py

  Expected: PASS.

## Task 5: Convert V2 dimensions to local asset snapshots

**Files:**

- Modify: local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py
- Modify: local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py
- Modify: local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py
- Test: local-runtime/tests/test_product_processing_dimension_canvas.py

**Consumes:** image_manifest_v2 and MediaAssetService.read_ready_asset.

**Produces:** V2 canvas imports with source_media_asset_id and reviewed dimension bindings.

- [ ] Write failing tests for a ready V2 source asset and a ready V2 generated carousel asset. Assert import copies either to the dimension workspace, stores source_media_asset_id, and makes no remote download. Assert pending media is visible as unavailable rather than yielding a broken URL.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_dimension_canvas.py

  Expected: FAIL because the current import infers source from URLs and paths.

- [ ] Branch by media_contract_version. For V2, read candidates only from V2 manifest asset IDs and bindings; resolve ready bytes; write a dimension workspace snapshot. Preserve the existing V1 import path unchanged.
- [ ] On accepted render, register origin=dimension_rendered; create the active dimension binding; deactivate only the former active dimension binding for carousel.dimension_background; increment preview_revision.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_preview_images.py

  Expected: PASS.

## Task 6: Convert V2 frontend contracts and recover materialization

**Files:**

- Modify: local-runtime/wh_local/modules/product_processing/api/router.py
- Modify: web-frontend/src/modules/product_processing/types/index.ts
- Modify: web-frontend/src/modules/product_processing/api/productProcessingApi.ts
- Modify: web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts
- Modify: web-frontend/src/modules/product_processing/components/PrecheckImageManager.tsx
- Modify: web-frontend/src/modules/product_processing/components/DimensionCanvasImportDialog.tsx
- Modify: web-frontend/src/modules/product_processing/components/DimensionCanvasStage.tsx
- Test: web-frontend/src/modules/product_processing/data/precheckImageModel.test.ts
- Test: web-frontend/src/modules/product_processing/data/dimensionCanvasApi.test.ts

**Produces:** MediaAssetView, V2 SKU grouping, state-aware precheck cards, and asset-ID-only canvas selection.

- [ ] Write failing TypeScript fixtures for ready, pending, retryable, and failed assets; grouped SKU media; and an import request that never derives a path from previewUrl.
- [ ] Run: npm test -- --runInBand

  Expected: FAIL for V2 fixtures.

- [ ] Add MediaAssetView, MediaBindingView, and ImageManifestV2 to TypeScript types. Add V2 draft media and retry clients; only use V1 parsing where contractVersion is missing or 1.
- [ ] Update precheck components to show all source categories including SKU images, materialization state, and retry actions.
- [ ] Update canvas import to expose only ready V2 media candidates; use preview URLs for display only.
- [ ] On router lifespan, recover V2 pending/retryable/expired leases. Query only product_processing_media_assets and never scan V1 source_images.
- [ ] Run: npm test -- --runInBand

  Expected: PASS.

## Task 7: End-to-end verification and rollout

**Files:**

- Modify: local-runtime/tests/test_product_processing_media_ingress.py
- Modify: local-runtime/tests/test_product_processing_dimension_canvas.py
- Modify: local-runtime/tests/test_product_processing_preview_images.py

- [ ] Add one end-to-end test: handoff with main/gallery/detail/SKU media -> materialization fixture -> V2 carousel manifest -> canvas import -> render acceptance -> finalization snapshot. Assert every final asset is ready and its hash matches the snapshot.
- [ ] Run: pytest -q local-runtime/tests/test_product_processing_media_ingress.py local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_preview_images.py

  Expected: PASS.

- [ ] Run: pytest -q local-runtime/tests

  Expected: PASS with no V1 regression.

- [ ] Run: npm test -- --runInBand

  Expected: PASS.

## Rollout and Rollback

1. Deploy additive schema and V2 code with V2 draft creation disabled.
2. Enable V2 only for newly confirmed daily-selection handoffs.
3. Verify ready/retryable/failed distribution, SKU bindings, and absence of external download during V2 canvas import.
4. Enable V2 precheck and canvas UI after backend checks pass.
5. To roll back, disable V2 draft creation and materialization scheduling. Do not delete V2 rows; V1 workflows remain unchanged.

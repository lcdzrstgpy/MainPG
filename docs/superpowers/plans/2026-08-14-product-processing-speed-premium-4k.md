# Product Processing Speed and Premium 4K Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the obsolete image-count selector and reduce ordinary and premium product-processing wall time while preserving existing output-quality contracts.

**Architecture:** Keep ordinary products on one 2K four-grid call and route premium products through one 4K four-grid call with a dedicated high-resolution splitter. Remove provider head-of-line blocking, remember transient batch failures, and keep image work overlapped with size and variant work. Preserve legacy request compatibility and existing cache behavior.

**Tech Stack:** React/TypeScript, FastAPI/Pydantic, Python threading/concurrent.futures, Pillow, requests, pytest, Node test runner.

---

### Task 1: Remove obsolete frontend selector

**Files:**
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingTaskPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingVerifyPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/types/index.ts`
- Test: `web-frontend/src/modules/product_processing/pages/ProductProcessingTaskPage.test.tsx`

- [ ] Delete `IMAGE_GENERATION_OPTIONS` and its `单次生图` JSX block.
- [ ] Stop exposing an editable `imageGenerationCount` option for new tasks while sending the compatibility value `4` at the API boundary.
- [ ] Replace premium help text that says “4 张独立单图” with “1 次 4K 四宫格，拆分为 4 张高清图”.
- [ ] Run the focused frontend tests and `npm.cmd --prefix web-frontend run build`; expect success.

### Task 2: Add premium 4K generation and high-resolution splitting

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/provider_config.py`
- Modify: `local-runtime/wh_local/modules/product_processing/domain/prompts.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/tests/test_product_processing_reference_integration.py`
- Modify: `local-runtime/tests/test_product_processing_image_quality.py`

- [ ] Add `gpt-image-2-4k` and `4096x4096` premium defaults and a dedicated exact 2x2 premium-grid prompt.
- [ ] Add a splitter that requires a near-4K square source, validates center guides, emits four approximately 2048-square JPEG 4:4:4 parts, and generates only an 800 summary thumbnail.
- [ ] Replace four parallel premium 1K calls with one premium 4K call and at most one whole-grid retry.
- [ ] Keep source-image fallback when the retry cannot produce four trusted parts.
- [ ] Run the premium/reference and image-quality tests; expect success.

### Task 3: Remove text-provider head-of-line blocking

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/ai_client.py`
- Modify: `local-runtime/tests/test_product_processing_ai_client.py`

- [ ] Change unknown-route single-flight so followers wait only a short bounded probe window, then call the same healthy primary concurrently.
- [ ] Keep immediate batch-wide skip for structured `key_has_no_route_providers` responses.
- [ ] Add short model cooldown after consecutive timeout, 429, or 5xx failures; successful responses reset the transient health state.
- [ ] Preserve the configured fallback order and long per-request timeout.
- [ ] Run the focused AI client tests; expect success.

### Task 4: Unify ordinary and premium pipeline scheduling

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py`
- Test: `local-runtime/tests/test_product_processing_image_generation_modes.py`
- Test: `local-runtime/tests/test_product_processing_reliability.py`

- [ ] Submit both ordinary and premium image work to the existing media future immediately after structured text passes.
- [ ] Keep variant and dimension repairs in the side pool and merge notes deterministically.
- [ ] Prime validated reference-image bytes and OCR runtime while the remote image request is pending.
- [ ] For ordinary unsplittable grids, retry the 2K grid once; retain per-slot 1K repair only for identified bad slots.
- [ ] Run the image-generation-mode and reliability tests; expect success.

### Task 5: Preserve completed work across retry

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/orm.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/database.py`
- Test: `local-runtime/tests/test_product_processing_reliability.py`

- [ ] Persist stage receipts containing the task item, stage, input hash, validated output reference, and completion time.
- [ ] Reuse a receipt only when the exact stage input hash and managed output validation still match.
- [ ] Invalidate downstream receipts when title, source images, prompt version, model profile, or processing options change.
- [ ] Verify retry reuses valid text/images and reruns only the failed stage.

### Task 6: Focused integration verification

**Files:**
- Inspect: all files modified by Tasks 1-5.

- [ ] Run `python -m pytest tests/test_product_processing_ai_client.py tests/test_product_processing_image_generation_modes.py tests/test_product_processing_image_quality.py tests/test_product_processing_reference_integration.py tests/test_product_processing_reliability.py -q` from `local-runtime`.
- [ ] Run `npm.cmd --prefix web-frontend run build`.
- [ ] Run `git diff --check` and report every intentionally modified or untracked file without committing implementation changes.

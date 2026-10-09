# Temu Gallery-only Image Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Temu product capture from submitting any page-wide images when its semantic product gallery is available.

**Architecture:** Keep the existing semantic gallery extractor as the sole source for Temu product images. Generic candidate discovery remains available only for non-Temu platforms and as the single-image fallback when Temu has no semantic gallery result.

**Tech Stack:** Chrome extension JavaScript; Node.js built-in test runner.

## Global Constraints

- Do not use document-wide image scanning or keyword blocklists to populate Temu `product_image_urls` when `gallery_images` is non-empty.
- Preserve current image extraction behavior for non-Temu platforms.
- Preserve a Temu fallback only when semantic gallery extraction returns no image.

---

### Task 1: Restrict Temu image output to its semantic gallery

**Files:**

- Modify: `W-H-browser-extension-v0.1.130/background.js:13040-13050,14619-14625`
- Test: `W-H-browser-extension-v0.1.130/temu_dom_capture.test.mjs`

**Interfaces:**

- Consumes: `temuSemanticCapture.gallery_images`, each with a normalized `url`.
- Produces: `productImageUrls`, the list sent as `image_urls` and `product_image_urls`.

- [x] **Step 1: Write the failing regression test**

Extend the source-contract test with assertions that `background.js` builds `productImageUrls` from `temuSemanticCapture.gallery_images` when it is non-empty, and that the page-wide `document.querySelectorAll("img")` scan is not used in that Temu branch.

- [x] **Step 2: Run the regression test to verify it fails**

Run: `node --test W-H-browser-extension-v0.1.130/temu_dom_capture.test.mjs`

Expected: FAIL because the current source always derives Temu `productImageUrls` from `imageCandidates`.

- [x] **Step 3: Implement the smallest source selection change**

In `background.js`, derive `productImageUrls` from the normalized semantic gallery URLs for Temu when the semantic list is non-empty. Otherwise retain the current candidate filtering and limit. Do not alter non-Temu behavior.

- [x] **Step 4: Run the regression test to verify it passes**

Run: `node --test W-H-browser-extension-v0.1.130/temu_dom_capture.test.mjs`

Expected: PASS with all tests green.

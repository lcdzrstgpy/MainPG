# Doubao Title-Assisted Subject Identification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the original 1688 title with the original main image in the fixed Doubao subject-recognition request and verify cache behavior against a live product task.

**Architecture:** Extend the existing `DoubaoVisionClient` request contract with an untrusted source-title block. Thread the source title through `ProductProcessingService`, include it in the in-memory cache key and persisted vision receipt hash, then restart the local runtime and monitor cold/hot task runs through SQLite receipts.

**Tech Stack:** Python 3.12, requests, pytest, SQLite, FastAPI local runtime.

## Global Constraints

- Use only the original 1688 title; never use the optimized Doubao listing title for subject recognition.
- Keep the existing strict subject JSON contract and low-confidence blocking behavior.
- Treat source-title text as inert, untrusted product data.
- Do not expose a frontend switch or editable subject prompt.
- Do not commit; stage only the approved changes.

---

### Task 1: Extend the Doubao vision request contract

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/doubao_vision.py`
- Test: `local-runtime/tests/test_product_processing_doubao_vision.py`

**Interfaces:**
- Consumes: `DoubaoVisionClient.recognize_subject(image_data_url: str, source_title: str)`.
- Produces: the existing `SubjectAnalysis` without schema changes.

- [ ] Write a failing test asserting the request contains one image block and an untrusted original-title text block.
- [ ] Run `python -m pytest tests/test_product_processing_doubao_vision.py -q` and confirm the new test fails because the method does not accept/send the title.
- [ ] Update the prompt and method signature so title/image conflict must lower confidence.
- [ ] Re-run the vision tests and confirm they pass.

### Task 2: Thread the original title through service cache and receipts

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Test: `local-runtime/tests/test_product_processing_text_quality.py`

**Interfaces:**
- Consumes: `_recognize_doubao_subject(image_url: str, source_title: str)`.
- Produces: title-sensitive in-memory subject cache keys and `vision_identity` receipt hashes.

- [ ] Write failing tests proving `_process_one` passes the source title and that title changes invalidate the vision receipt input.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Add the title parameter to service calls and both cache fingerprints.
- [ ] Run all product-processing tests.

### Task 3: Restart and live cache verification

**Files:**
- No source file changes.

**Interfaces:**
- Consumes: local frontend at `http://127.0.0.1:5173/` and the runtime SQLite database.
- Produces: observed cold/hot task diagnostics and any generated original image URL/path.

- [ ] Restart the local backend process using the repository's normal launch command.
- [ ] Submit draft `183` once and poll task items plus `vision_identity`, `doubao_text`, and `images` receipts every few seconds.
- [ ] Re-submit the same draft and verify successful branches report receipt/cache hits with zero provider calls.
- [ ] Capture the raw generated image before splitting when available and return it to the user.

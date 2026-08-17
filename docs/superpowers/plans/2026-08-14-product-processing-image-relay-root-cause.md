# Product Image Relay Root-Cause Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, for each slow or failed product image request, whether time and failure originated in local reference preparation, local queueing, the relay `/images/edits` request, or retrieval of the returned image; do this without recording prompts, credentials, or source URLs.

**Architecture:** Keep the existing synchronous image-edit contract unchanged during the investigation. Add a bounded, append-only `image_request_trace` event list to the existing per-item `images` stage receipt. `ProductImageProcessor` emits sanitised events at each component boundary; `ProductProcessingService` persists them immediately through the existing receipt repository so a process crash or a 10-minute in-flight call still leaves useful evidence.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, `requests`, pytest.

## Global Constraints

- Do not log API keys, `Authorization` values, prompt text, source URLs, image bytes, response bodies, or returned image URLs.
- Do not make billable probe calls automatically. Any controlled image probe requires an explicit operator confirmation and must show its estimated request count before execution.
- Preserve the current production endpoint and payload contract until the trace identifies the slow boundary.
- Existing task history must remain readable; receipt fields are additive JSON only and require no schema migration.
- Use monotonic-clock durations in milliseconds and UTC ISO timestamps for event ordering.

## Findings Already Established

- The active backend uses `https://station-88.aicoming.top/v1`, `gpt-image-2-2k`, `2048x2048`, and the `/images/edits` endpoint.
- A normal four-grid call currently uploads a generated 2048px scaffold plus up to four uncompressed source images as multipart `image[]` fields.
- The relay model-list request succeeds, so DNS/TLS/authentication to the configured relay are functional. That does not prove the heavier image-edit request or returned-image download are healthy.
- Task 17 recorded `Response ended prematurely` on one image attempt and a successful HTTP response with no safe image result on another. No existing record separates request queue, multipart upload/relay wait, JSON parsing, and returned-image download.
- `IMAGE_GENERATION_TOTAL_TIMEOUT_SECONDS` is only checked before an edit attempt. `_request_edit` can continue in the edit request and then in a separate 360-second result download, so the observed end-to-end duration can exceed 660 seconds.

## File Structure

- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py` — define sanitised trace events, time local preparation and both HTTP hops, and enforce trace-safe error classification.
- Modify: `local-runtime/wh_local/modules/product_processing/service.py` — create one trace sink per task item, persist it into the `images` stage receipt, and expose a non-sensitive task diagnostic summary.
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py` — add a read-only route for an item’s trace with the same workspace scoping as existing task routes.
- Modify: `local-runtime/tests/test_product_processing_image_quality.py` — cover event ordering, payload facts, and failure classification without real network calls.
- Modify: `local-runtime/tests/test_product_processing_media_api.py` — cover workspace-scoped read-only trace API output and redaction.
- Modify: `local-runtime/tests/test_product_processing_reliability.py` — cover persistence of an in-flight trace event when the image request never completes normally.

---

### Task 1: Define the trace contract at the image-client boundary

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py:76-115, 185-285, 992-1051`
- Test: `local-runtime/tests/test_product_processing_image_quality.py`

**Interfaces:**
- Produces: `ImageRequestTraceEvent` as a JSON-safe dictionary with `event`, `at`, `elapsed_ms`, `attempt`, `provider`, `model`, `request_path`, `reference_count`, `reference_bytes`, `status_code`, `error_class`, and `error_message`.
- Produces: optional `trace_sink: Callable[[dict[str, object]], None]` accepted by `ProductImageProcessor.generate` and `_generate_with_limits`.
- Consumes: existing `MediaProcessingError`, `_safe_error`, `_retry_class`, and provider dictionaries.

- [ ] **Step 1: Write the failing trace-contract tests**

```python
def test_request_edit_trace_records_post_and_result_download_without_secrets(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    # Fake POST returns an HTTPS image URL; fake GET returns PNG bytes.
    result = ProductImageProcessor._request_edit(
        _provider(), "secret prompt", [(b"abc", "source.jpg", "image/jpeg")],
        trace_sink=events.append, attempt=1,
    )
    assert result[1] == "image/png"
    assert [event["event"] for event in events] == [
        "edit_request_started", "edit_response_received",
        "result_download_started", "result_download_finished",
    ]
    assert events[0]["reference_count"] == 1
    assert events[0]["reference_bytes"] == 3
    assert "secret prompt" not in repr(events)
    assert "test-key" not in repr(events)

def test_request_edit_trace_records_chunked_response_failure(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.setattr(media_module._SESSION, "post", _raise_chunked_encoding_error)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        ProductImageProcessor._request_edit(
            _provider(), "prompt", [(b"abc", "source.jpg", "image/jpeg")],
            trace_sink=events.append, attempt=2,
        )
    assert events[-1]["event"] == "edit_request_failed"
    assert events[-1]["error_class"] == "connection_error"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_quality.py -k trace -q`

Expected: FAIL because `_request_edit` has no `trace_sink` parameter and emits no events.

- [ ] **Step 3: Add a sanitised event helper and instrument the actual boundaries**

```python
TraceSink = Callable[[dict[str, object]], None]

def _emit_trace(sink: TraceSink | None, event: str, **fields: object) -> None:
    if sink is None:
        return
    sink({"event": event, "at": datetime.now(timezone.utc).isoformat(), **fields})
```

Pass `trace_sink` and `attempt` from `_generate_with_limits` to `_request_edit`. Emit events only for: `reference_prepare_finished`, `edit_request_started`, `edit_response_received`, `edit_request_failed`, `result_download_started`, `result_download_finished`, and `result_download_failed`. Use `urlsplit(...).path` for the request path and result-host presence only; never retain any full URL. Time the existing calls with `time.perf_counter()` and store integer millisecond deltas.

- [ ] **Step 4: Run the focused tests**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_quality.py -k 'trace or second_grid_attempt' -q`

Expected: PASS.

- [ ] **Step 5: Commit the trace contract**

```bash
git add local-runtime/wh_local/modules/product_processing/infrastructure/media.py local-runtime/tests/test_product_processing_image_quality.py
git commit -m "feat(product-processing): trace image relay boundaries"
```

### Task 2: Persist in-flight image traces per task item

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:2304-2314, 3360-3690`
- Test: `local-runtime/tests/test_product_processing_reliability.py`

**Interfaces:**
- Consumes: `ProductImageProcessor.generate(..., trace_sink=...)` from Task 1 and `ProductProcessingRepository.upsert_stage_receipt(...)`.
- Produces: an `images` receipt payload containing `trace_version: 1`, `trace_state: "running" | "finished" | "failed"`, and a capped `image_request_trace` list.

- [ ] **Step 1: Write the failing persistence test**

```python
def test_inflight_image_trace_is_persisted_before_provider_returns(service, monkeypatch) -> None:
    item_id = _create_running_item(service)
    observed: list[dict] = []

    def block_after_emit(*, trace_sink, **_kwargs):
        trace_sink({"event": "edit_request_started", "attempt": 1, "elapsed_ms": 0})
        observed.append(service.repository.load_stage_receipt(1, item_id, "images"))
        raise requests.Timeout("simulated")

    monkeypatch.setattr(service._media_processor(), "generate", block_after_emit)
    _run_image_stage(service, item_id)
    assert observed[0]["output"]["trace_state"] == "running"
    assert observed[0]["output"]["image_request_trace"][0]["event"] == "edit_request_started"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_reliability.py -k inflight_image_trace -q`

Expected: FAIL because image traces are not persisted until the whole item completes.

- [ ] **Step 3: Implement a capped immediate receipt sink**

At the beginning of `_generate_grid_images`, create a closure bound to `task_id`, `task_item_id`, `workspace_id`, and the stable image-stage input hash. For every event, append at most 32 events and call `upsert_stage_receipt(..., stage_name="images", output_data=...)` with:

```python
{
    "trace_version": 1,
    "trace_state": "running",
    "image_request_trace": events,
    "last_event": events[-1]["event"],
}
```

On success set `trace_state` to `finished`; in every image exception path set it to `failed` and retain the final sanitised error class. Do not overwrite pre-existing structured-text receipt fields.

- [ ] **Step 4: Run focused reliability tests**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_reliability.py -k 'trace or image' -q`

Expected: PASS.

- [ ] **Step 5: Commit the receipt persistence work**

```bash
git add local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_reliability.py
git commit -m "feat(product-processing): persist in-flight image traces"
```

### Task 3: Add a safe read-only diagnostic endpoint and UI-independent operator view

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py:79-100`
- Test: `local-runtime/tests/test_product_processing_media_api.py`

**Interfaces:**
- Produces: `ProductProcessingService.task_item_image_trace(task_id: int, item_id: int, workspace_id: str) -> dict[str, object]`.
- Produces: `GET /api/product-processing/tasks/{task_id}/items/{item_id}/image-trace`.
- Returns: task and item identifiers, item status, trace state, trace events, and an operator classification; never raw receipt JSON, URLs, or credentials.

- [ ] **Step 1: Write the failing API tests**

```python
def test_image_trace_endpoint_returns_sanitised_events(client, seeded_task) -> None:
    response = client.get(f"/api/product-processing/tasks/{seeded_task.id}/items/{seeded_task.item_id}/image-trace")
    assert response.status_code == 200
    assert response.json()["trace_events"][0]["event"] == "edit_request_started"
    assert "prompt" not in repr(response.json()).lower()
    assert "http" not in repr(response.json()).lower()

def test_image_trace_endpoint_rejects_other_workspace(client, seeded_other_workspace_task) -> None:
    response = client.get(f"/api/product-processing/tasks/{seeded_other_workspace_task.id}/items/{seeded_other_workspace_task.item_id}/image-trace")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_media_api.py -k image_trace -q`

Expected: FAIL with 404 because the route does not exist.

- [ ] **Step 3: Implement the projection and classification**

Return one of these exact operator classifications based solely on trace facts:

| Condition | `classification` |
|---|---|
| no `edit_request_started` after reference preparation | `local_preparation_or_queue` |
| `edit_request_failed` with `connection_error` or `unknown_outcome_timeout` | `relay_connection_or_response` |
| edit response received but no result-download start | `relay_response_contract` |
| result download failed | `result_asset_delivery` |
| finished trace | `completed` |

The route must call the service projection rather than returning repository rows directly.

- [ ] **Step 4: Run the focused API tests**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_media_api.py -k image_trace -q`

Expected: PASS.

- [ ] **Step 5: Commit the diagnostic API**

```bash
git add local-runtime/wh_local/modules/product_processing/service.py local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/tests/test_product_processing_media_api.py
git commit -m "feat(product-processing): expose sanitised image trace"
```

### Task 4: Run controlled comparisons and reach a single root-cause verdict

**Files:**
- Create: `local-runtime/manual_tests/product_processing_image_trace.md`
- Test: `local-runtime/tests/test_product_processing_image_quality.py`

**Interfaces:**
- Consumes: the trace endpoint from Task 3.
- Produces: one comparison record per explicit operator-run task, with no credentials or image content stored in the manual test document.

- [ ] **Step 1: Document the non-billable baseline checks**

Document these exact checks before any image generation:

```bash
curl -sS -X POST -H 'X-Workspace-ID: default' \
  http://127.0.0.1:8010/api/product-processing/ai/ping
curl -sS -H 'X-Workspace-ID: default' \
  http://127.0.0.1:8010/api/product-processing/engine/status
```

Expected: ping succeeds quickly; engine reports `image_configured: true`. A failure here is local DNS/TLS/authentication configuration, not model-generation latency.

- [ ] **Step 2: Define the billable comparison, gated by explicit operator approval**

Run exactly two one-product tasks with the same input after approval:

1. Current production contract: scaffold + four source references, 2K image edit.
2. Diagnostic contract: scaffold + one source reference, same model, size, prompt, and relay.

Collect the `image-trace` endpoint output immediately for each. Do not run retries automatically; use one provider attempt in each comparison.

- [ ] **Step 3: Apply the verdict rules**

| Trace outcome | Root-cause verdict | Next implementation scope |
|---|---|---|
| POST time is slow/fails in both requests, but ping is healthy | relay image-edit queue/model route | hard end-to-end deadline and provider failover; no source-payload rewrite yet |
| Four-reference request is materially slower while one-reference request is healthy | local oversized multipart design | resize/compress references and lower grid reference limit |
| POST succeeds quickly but result download is slow/fails | relay result-asset delivery | separate download deadline and trusted asset-host policy |
| trace shows limiter/semaphore wait before POST | local queue/configuration | align `image_workers` and rate limiter with configured provider capacity |

- [ ] **Step 4: Verify documentation and test suite**

Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_quality.py tests/test_product_processing_media_api.py tests/test_product_processing_reliability.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the runbook**

```bash
git add local-runtime/manual_tests/product_processing_image_trace.md
git commit -m "docs(product-processing): add image relay root-cause runbook"
```

## Acceptance Criteria

- While an image request is in flight, an operator can see whether it has reached local preparation, relay POST, relay response, or result download.
- A failed task records the attempt count, model, reference count/bytes, component-boundary timing, status code when present, and sanitised error class.
- No trace output contains credentials, prompts, source URLs, raw response bodies, or image bytes.
- Two approved single-product comparison runs can distinguish the relay model route from the local multi-reference payload design using recorded evidence, not inference.
- Existing product-processing tests remain green.

## Self-Review

- Scope coverage: local send path, relay response path, result delivery path, queueing, privacy, controlled comparison, and verdict rules are each covered by a task.
- Placeholder scan: no TBD/TODO or unspecified test command remains.
- Interface consistency: Task 1 supplies `trace_sink`; Task 2 persists it; Task 3 reads its projected form; Task 4 consumes the read-only endpoint.

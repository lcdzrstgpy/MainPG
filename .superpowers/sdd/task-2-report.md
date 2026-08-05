# Task 2 — Source-specific collection ingress

## Status

Implemented and committed as `a809200 feat: separate manual and api draft ingress`.

## RED evidence

Before implementation, the required focused test command failed with three expected regressions:

```text
pytest -q local-runtime/tests/data_collection/test_sqlite_ingestion.py local-runtime/tests/data_collection/test_daily_selection_hardening.py
3 failed, 15 passed
```

The failures proved that plugin capture stored `plugin_capture`, successful Temu link results created no draft, and preview created no API draft.

## GREEN evidence

Final focused verification:

```text
python -m compileall -q local-runtime/wh_local/data_collection/routes.py local-runtime/wh_local/modules/product_processing/service.py
pytest -q local-runtime/tests/data_collection/test_sqlite_ingestion.py local-runtime/tests/data_collection/test_daily_selection_hardening.py
18 passed in 1.27s
```

`git diff --check` also completed with no whitespace errors.

## Changes

- Plugin product capture normalizes valid products to `web_manual_capture`, preserves the platform, canonical source reference, source image URLs, and removes sensitive payload fields.
- Both plugin-result endpoints now ingest only successful `temu_link_capture` results whose `result.product` is a valid mapping. Invalid/incomplete result products remain queue diagnostics and create no draft.
- Both OneBound preview routes immediately create or reuse `onebound_api` drafts. Draft payloads retain the selection run ID, collection mode, criteria, counts, and source evidence; daily-selection intake remains audit data rather than a second draft pool.
- Each successful ingress schedules source-image synchronization only after its draft transaction has completed.
- Confirmation handoffs now require the already-ingressed candidate draft and only record consumption; they cannot create a second draft.
- Added regressions for plugin ingress, Temu-link ingress, immediate preview and 1688-link ingress, source metadata retention, and confirmation reuse without duplication.

## Full-suite result and concerns

`pytest -q local-runtime/tests` produced `103 passed, 7 failed`.

- Two failures are stale, untracked duplicate `test_daily_selection_hardening 2.py` tests that still assert the intentionally removed behavior where confirmation creates a `daily_selection_handoff` draft.
- Three real-API duplicate tests (`test_real_api_more.py`, ` 2.py`, ` 3.py`) expect obsolete `ApiEvidence.api_call` attributes.
- Two unrelated price-verification tests fail because `QuoteService` has no `create_capture_batch` method.

These failures are outside Task 2's staged files and were not changed.

Review found no P0/P1 defects. Its P2 note was that additional coverage could be added for scheduling and malformed-result paths; the required focused coverage is green and no scope expansion was made.

## P1 provenance repair — repeated OneBound candidates

### Root cause

`ProductProcessingService.create_draft` returned an active matching candidate draft before applying the incoming OneBound payload. A subsequent preview therefore reused the physical draft but left its `selection_run_id` and raw run-scoped payload (collection mode, criteria, and source evidence) from the previous run.

### RED evidence

The added two-run preview regression uses the same OneBound candidate in `run-first` and `run-second`, while deliberately changing the criteria and evidence timestamp. Before the repair it failed as expected:

```text
pytest -q tests/data_collection/test_sqlite_ingestion.py -k repeated_preview_refreshes_existing_api_draft_with_current_run_provenance
1 failed, 7 deselected
AssertionError: assert 'run-first' == 'run-second'
```

### Repair and GREEN evidence

When an existing active draft is ingressed as `onebound_api`, the service now retains the one draft ID but refreshes its database `selection_run_id` and raw payload from the current run, then reseeds source-image URLs. The normal manual ingress early return is unchanged, as are confirmation handoff behavior and source-image records.

Focused verification:

```text
pytest -q tests/data_collection/test_sqlite_ingestion.py tests/data_collection/test_daily_selection_hardening.py tests/product_processing/test_source_image_sync.py
22 passed in 1.69s
```

`python -m compileall -q wh_local/modules/product_processing/service.py` and `git diff --check` also completed successfully.

### Scope and concerns

- Changed only product-draft OneBound reuse behavior and the SQLite route-level regression fixture/test.
- No 点小蜜 or frontend files were changed.
- The refreshed draft remains a single candidate row; its current-run provenance means older-run filtered draft views no longer include it, while each run's durable daily-selection audit record remains intact.

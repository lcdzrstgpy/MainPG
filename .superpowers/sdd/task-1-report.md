# Task 1: durable source-image synchronization

## Result

Product drafts now seed idempotent `source` and `detail` source-image rows at creation or revival.  Image synchronization retains each original remote URL, downloads through the public-image fetch boundary, stores byte-addressed managed files, and tracks retryable `pending` / `syncing` / `ready` / `failed` transitions.

## RED evidence

Command:

```text
pytest -q local-runtime/tests/product_processing/test_source_image_sync.py
```

Before implementation: `1 failed, 1 error`.

- The seeding assertion received `[]` rather than pending source/detail rows.
- Construction with `public_image_fetcher` raised `TypeError`, proving the injectable fetch boundary and sync API were absent.

## GREEN evidence

```text
pytest -q local-runtime/tests/product_processing/test_source_image_sync.py
# 2 passed in 0.53s

pytest -q local-runtime/tests/data_collection/test_sqlite_ingestion.py
# 4 passed in 0.58s

python -m compileall -q local-runtime/wh_local/modules/product_processing local-runtime/wh_local/db.py
# exit 0
```

A clean temporary runtime database also registered `product_processing:002_source_image_sync` and contained both `sync_status` and `sync_error` columns. `git diff --check` exited cleanly.

## Changed files

- `local-runtime/wh_local/modules/product_processing/migrations/002_source_image_sync.sql`
- `local-runtime/wh_local/db.py`
- `local-runtime/wh_local/modules/product_processing/infrastructure/orm.py`
- `local-runtime/wh_local/modules/product_processing/infrastructure/assets.py`
- `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py`
- `local-runtime/wh_local/modules/product_processing/service.py`
- `local-runtime/tests/product_processing/test_source_image_sync.py`

## Commit

- `c0d5eb0 feat: persist product draft source images`

## Self-review

- Seeding uses the existing `(product_draft_id, url)` uniqueness rule, so repeated creation/revival does not duplicate rows.
- Claiming uses conditional status updates in the same transaction, so a row already claimed by another worker is not returned for a duplicate copy.
- Completion writes only `local_path`, status, and error state; failure leaves the original remote URL untouched and truncates its error to 500 characters.
- SHA-256 managed paths live under `source-image-library`; identical image bytes are not written twice.
- No frontend or 点小蜜 files are staged.

## Concerns

The full `pytest -q local-runtime/tests` run had 3 unrelated failures and 101 passes. All are duplicate `test_real_api_more*::test_image_search` cases that access the missing `ApiEvidence.api_call` attribute after public-image rejection; this task does not modify those tests or `ApiEvidence`.

## Stale source-image claim recovery fix

### Root cause

`claim_syncable_source_images` changed an eligible row to `syncing`, but persisted no claim time and only selected `pending` / `failed` rows. A process interruption after the claim therefore made the row permanently ineligible for `retry_draft_source_images`.

### Fix

- Added migration `product_processing:003_source_image_sync_lease`, which persists `sync_claimed_at` and `sync_claim_token` and indexes `(sync_status, sync_claimed_at)`.
- A claim has a five-minute lease. `pending`, `failed`, and stale (or legacy timestamp-less) `syncing` rows are eligible; fresh `syncing` rows remain protected.
- Every successful claim gets a new token. Completion/failure requires the active token, so an expired worker cannot overwrite a later worker's result after its claim is reclaimed.
- Completion/failure clear lease metadata. The original remote `url` is never changed, and existing managed-file storage behavior is untouched.

### RED evidence

```text
pytest -q local-runtime/tests/product_processing/test_source_image_sync.py
# 1 failed, 2 passed in 1.01s
```

The new stale-claim regression failed at `UPDATE ... SET sync_claimed_at` with `sqlite3.OperationalError: no such column: sync_claimed_at`, proving the required durable lease state was absent.

### GREEN and verification evidence

```text
pytest -q local-runtime/tests/product_processing/test_source_image_sync.py
# 3 passed in 0.46s

python -m compileall -q local-runtime/wh_local/modules/product_processing local-runtime/wh_local/db.py
# exit 0

git diff --check
# exit 0

pytest -q local-runtime/tests/data_collection/test_sqlite_ingestion.py
# 4 passed in 0.82s

python -c '<temporary runtime DB migration assertion>'
# migration=product_processing:003_source_image_sync_lease columns=sync_claimed_at,sync_claim_token
```

The full suite command `pytest -q local-runtime/tests` produced `3 failed, 102 passed in 2.81s`. The only failures remain the three duplicate `test_real_api_more*::test_image_search` assertions against missing `ApiEvidence.api_call`; this fix does not touch that API or those tests.

### Changed files

- `local-runtime/wh_local/modules/product_processing/migrations/003_source_image_sync_lease.sql`
- `local-runtime/wh_local/db.py`
- `local-runtime/wh_local/modules/product_processing/infrastructure/orm.py`
- `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py`
- `local-runtime/wh_local/modules/product_processing/service.py`
- `local-runtime/tests/product_processing/test_source_image_sync.py`

### Constraints checked

- The stale-retry regression confirms the source image keeps `https://cdn.example.test/main.jpg` as its remote URL.
- Managed source-image save policy is unchanged.
- No 点小蜜 files were changed.

### Concern

The lease duration is five minutes. A source download that legitimately runs longer may be reclaimed; claim-token fencing prevents the older worker from changing the final synchronization state, though duplicate byte-addressed managed-file work can occur.

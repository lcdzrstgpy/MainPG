# OneBound 1688 real-response compatibility report

Date: 2026-08-04

## Scope

- `local-runtime/wh_local/modules/daily_selection/provider.py`
- `local-runtime/wh_local/modules/daily_selection/normalizer.py`
- Provider and normalizer regression tests only

## TDD evidence

The new regression tests were written before the implementation. The initial Conda-base run of the provider and normalizer tests failed in the expected five places:

- upload used `POST` instead of the required `GET` query parameters;
- provider audit did not count top-level `items.item`;
- image search did not read top-level `items.imgid`;
- normalizer returned no candidates for both list and single-object `items.item` payloads.

## Changes

- `upload_img` now uses a GET request with `imgcode=<base64>` and `cache=no`, with no request body.
- Image search now accepts `items.imgid` and preserves the legacy `data.imgid` fallback; it passes `cache=no`.
- Provider audit item counts support top-level `items.item` lists and single objects while retaining legacy `data.items` lists.
- The normalizer accepts top-level `items.item` as either a list or a single object, while retaining legacy `data.items` support.

## Verification

All verification used the injected `FakeTransport`; no real OneBound or other network/API request was made.

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_provider.py local-runtime/tests/daily_selection/test_normalizer.py -q
36 passed in 0.09s

conda run -n base python -m pytest local-runtime/tests/daily_selection -q
90 passed in 0.13s
```

`git diff --check` reported no whitespace errors for the changed implementation and test files.

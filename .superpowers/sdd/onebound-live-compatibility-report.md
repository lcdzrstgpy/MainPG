# OneBound 1688 real-response compatibility report

Date: 2026-08-04

## Scope

- `local-runtime/wh_local/modules/daily_selection/provider.py`
- `local-runtime/wh_local/modules/daily_selection/normalizer.py` (previous response-shape compatibility)
- Provider and normalizer regression tests

## Response-shape compatibility

- Provider audit counts top-level `items.item` lists and single objects while retaining legacy `data.items` lists.
- The normalizer accepts top-level `items.item` as either a list or a single object while retaining legacy `data.items` support.

## Corrected image-search protocol

The earlier GET upload assumption was superseded by verified protocol evidence. `upload_img` now uses:

- `POST`;
- URL query parameters `key`, `secret`, and `cache=no` only;
- an `application/x-www-form-urlencoded` body containing only `imgcode=<base64>`.

Image-ID extraction preserves legacy `data.imgid` and recursively searches `items.item` mappings/lists for non-empty `imgid`, `img_id`, or `url` fields. Image search continues to send `cache=no`.

## TDD evidence

The protocol tests were updated before the provider implementation. The initial Conda-base provider run failed in four expected cases:

- upload was GET rather than POST;
- `imgcode` was incorrectly in query parameters rather than the form body;
- nested object `items.item.imgid` was not accepted;
- list/nested `img_id` and `url` paths were not accepted.

The regression tests use only `FakeTransport`; no OneBound or other real network/API request was made.

## Verification

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_provider.py -q
28 passed in 0.10s
```

The unfiltered daily-selection suite is currently blocked at collection by an unrelated, untracked `test_routes.py`: it imports `wh_local.modules.daily_selection.routes`, which is not present. This task did not change that test or module.

`git diff --check` reported no whitespace errors for the provider, provider-test, and report changes.

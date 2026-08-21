# POD + Shop Foundation Integration Progress

- Target baseline: `upstream/feature/client-processing-auto-repair@3c56dc1`
- Integration branch: `codex/pod-shop-foundation-integration`
- Backend baseline: 679 passed, 8 pre-existing failures (app update, hardened fetcher, one receipt reason assertion).
- Frontend baseline: 115 passed, 1 pre-existing stale ProfitActivity table-header assertion.
- Integration rule: target authentication, API management, billing, patch manager, and server AI gateway remain authoritative; old shared files are not merged wholesale.

## Tracks

- [x] 1688 shop search, automatic draft intake, collection worker, plugin contract, SKU retry handoff
- [x] New POD module, exports, title processing, UI and forward-only migrations
- [x] POD remote freeze/settle/recovery billing contract and admin pricing keys
- [x] Shared composition root, migration registry and navigation integration
- [x] Full backend/frontend/build regression and packaging checks

## Final verification

- Backend full suite: 869 passed, 8 failed; the 8 failures are the same pre-existing baseline set (release-version fixtures, updater/patch platform fixtures, hardened media-fetcher assertions, and one image failure-reason assertion). New failures: 0.
- Frontend Node suite: 130 passed, 1 failed; the remaining failure is the pre-existing stale ProfitActivity literal table-header assertion. New failures: 0.
- Frontend production build: passed (Vite chunk-size warning only).
- Browser plugin contracts: 6 passed; packaged `SHA256SUMS.txt`: 12/12 passed.
- Python compileall, migration crash/legacy-schema recovery, app route registration, focused secret scan, and `git diff --check`: passed.
- Independent final review: no Critical or Important findings; one non-blocking follow-up remains for a bounded shutdown timeout on the shop collection worker.

# POD + Shop Foundation Integration Progress

- Target baseline: `upstream/feature/client-processing-auto-repair@3c56dc1`
- Integration branch: `codex/pod-shop-foundation-integration`
- Backend baseline: 679 passed, 8 pre-existing failures (app update, hardened fetcher, one receipt reason assertion).
- Frontend baseline: 115 passed, 1 pre-existing stale ProfitActivity table-header assertion.
- Integration rule: target authentication, API management, billing, patch manager, and server AI gateway remain authoritative; old shared files are not merged wholesale.

## Tracks

- [ ] 1688 shop search, automatic draft intake, collection worker, plugin contract, SKU retry handoff
- [ ] New POD module, exports, title processing, UI and forward-only migrations
- [ ] POD remote freeze/settle/recovery billing contract and admin pricing keys
- [ ] Shared composition root, migration registry and navigation integration
- [ ] Full backend/frontend/build regression and packaging checks

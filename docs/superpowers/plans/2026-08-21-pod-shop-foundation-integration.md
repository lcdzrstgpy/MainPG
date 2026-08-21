# POD and Shop Foundation Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Work from refreshed target baseline `dac01dbb`; never replace target foundation files with the old branch versions.

**Goal:** Integrate whole-shop 1688 intake and the new POD workflow into the latest auth, API-management, billing, and update foundation.

**Architecture:** Keep the target branch as the source of truth. Port module-local code in parallel, then integrate only narrow additions into shared composition, migrations, navigation, and billing files.

**Tech Stack:** Python/FastAPI/SQLite, React/TypeScript/Vite, browser extension JavaScript, pytest/Vitest.

## Global Constraints

- Shop collection and intake do not charge points.
- POD uses `pod.title` and `pod.image` through shared freeze/grant/settle infrastructure.
- No local production fallback to long-lived Ark/Wuyin/OneBound keys.
- Templates are workspace-shared; POD batches/results/exports are owner-private.
- The new POD replaces the old AI Service POD; old tables remain untouched.
- Shared files `app/main.py`, `db.py`, Product Processing service/router/provider, customer auth, billing, and navigation are target-first manual integrations.
- Outside `pod_customization` and the approved 1688 shop UI delta, every frontend file and navigation decision must match `upstream/feature/client-processing-auto-repair@dac01dbb`.
- The user workspace must not expose the old `AI 服务` or `系统配置` entries; no local API-key configuration page may be restored.

## Parallel Track A: Whole-shop collection

- Port provider parsing, page normalization, repository, service, worker, leases/fencing, migrations 005/006, and backend tests.
- Add the narrow `intake_shop_candidate` transaction to the target Product Processing repository/service without replacing target billing or auto-repair logic.
- Embed the shop UI into the target Daily Selection page and preserve preview-task, cancellation, empty retry, and SKU repull behavior.
- Fix plugin poll/result shape and command support for `temu_link_capture` and `temu_flux_accel`.
- Verify with focused shop, plugin, tenant-isolation, migration, and frontend tests.

## Parallel Track B: New POD module

- Port `pod_customization`, title handling, Dianxiaomi export, runtime isolation, frontend module, and focused tests.
- Change template access to workspace sharing while retaining owner-private batches/results/exports.
- Adapt target `DoubaoArkClient` and image processing through injectable transports without changing server-managed defaults.
- Remove old AI Service POD UI/API/recovery behavior while retaining non-POD AI Service capabilities and old tables.
- Add forward-only POD migrations 001–007 and migration-upgrade tests.

## Parallel Track C: POD billing foundation

- Extend shared pricing with required `pod.title` and `pod.image` items; missing prices fail closed.
- Add `/api/customer/billing/pod/freeze`, `/settle`, and `/{freeze_id}` on top of existing wallet, ledger, freeze, grant, pricing-version, TTL, and admin audit services.
- Add encrypted grant/regrant responses, item-level idempotency, full refund for no-return/unstarted calls, and settlement reconciliation.
- Wire POD billing through the existing local customer session and admin proxy; never persist remote tokens or provider keys.
- Verify balance, partial failure, duplicate settle, regrant, account revocation, pricing-version, and secret-redaction behavior.

## Integration Track

- Merge the refreshed upstream commit `dac01dbb`, preserving its responsive dashboard, personal-center, price-verification, product-processing, and global-style changes exactly.
- Compare every remaining frontend delta against upstream; allow only POD files, removal of the legacy AI-Service POD sub-mode, and the Daily Selection shop-collection delta.
- Manually compose module lifecycles/routers in target `app/main.py`.
- Append migration registration in target `db.py`, including Price Verification 007/008.
- Merge navigation cases without weakening `adminOnly` or role filtering.
- Run backend, frontend, extension, migration, build, and packaging regressions.
- Perform low-budget real smoke tests for shop-to-draft and POD-freeze-to-export when credentials are available.

## Required Verification

```bash
cd local-runtime && pytest -q
cd web-frontend && npm test -- --run
cd web-frontend && npm run build
node --test 'W-H-浏览器采集插件-v0.1.109(1)'/*.test.mjs
```

Completion also requires clean fresh/upgrade migrations, successful application startup, no old POD entry, and no secrets in logs or SQLite.

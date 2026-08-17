# Profit Activity Dynamic Sites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an administrator to create a reusable profit-activity site whose fees immediately drive calculation, archive and filtering flows.

**Architecture:** Keep US/CO/EC as legacy calculation modes. Persist custom sites in a new workspace-scoped table and resolve each requested site through one service API before calculating. The frontend fetches these profiles and renders navigation/settings from the API rather than a fixed array.

**Tech Stack:** React/TypeScript/Vite, FastAPI/Pydantic, SQLAlchemy, SQLite migrations, node:test, pytest.

## Global Constraints

- Do not overwrite existing station fees, products or activity results.
- New site codes are uppercase 2–12 character identifiers and all new fee fields default to 0.
- Existing US, CO and EC calculation algorithms remain unchanged.
- Work directly in the current workspace; do not create a branch or worktree.

---

### Task 1: Persist and resolve custom site profiles

**Files:**
- Create: `local-runtime/wh_local/modules/profit_activity/migrations/003_dynamic_sites.sql`
- Modify: `local-runtime/wh_local/db.py`, `local-runtime/wh_local/modules/profit_activity/domain/models.py`, `local-runtime/wh_local/modules/profit_activity/infrastructure/orm.py`, `local-runtime/wh_local/modules/profit_activity/infrastructure/repository.py`
- Test: `local-runtime/tests/test_profit_activity_dynamic_sites.py`

- [ ] Write a failing test that creates `BR`, verifies it is visible only in its workspace, and verifies each fee is Decimal zero.
- [ ] Run the test and confirm the custom-site repository API is missing.
- [ ] Add `ProfitSiteProfile`, `profit_activity_sites`, ORM mapping and migration registration; expose create/list/get/update repository methods.
- [ ] Run the focused test and confirm it passes.

### Task 2: Apply custom-site rules to all profit workflows

**Files:**
- Modify: `local-runtime/wh_local/modules/profit_activity/domain/engine.py`, `local-runtime/wh_local/modules/profit_activity/service.py`, `local-runtime/wh_local/modules/profit_activity/api/schemas.py`, `local-runtime/wh_local/modules/profit_activity/api/router.py`
- Test: `local-runtime/tests/test_profit_activity_dynamic_sites.py`

- [ ] Write failing tests that calculate/archive/filter a created `BR` site and that reject unknown site codes.
- [ ] Run the focused test and confirm the legacy `SiteCode` restriction fails it.
- [ ] Replace fixed-site validation with service profile resolution, calculate custom sites using the generic formula, and accept dynamic codes in route/schema parameters.
- [ ] Run focused backend tests and confirm calculation, archive and filter all pass.

### Task 3: Render and create sites in the settings dialog

**Files:**
- Modify: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.tsx`, `web-frontend/src/modules/profit_activity/styles/profitActivityTest.css`
- Test: `web-frontend/src/modules/profit_activity/pages/ProfitActivityTestPage.test.ts`

- [ ] Write failing source-level tests for API-driven profiles, the `+ 新增站点` action, name/code validation and centered close control.
- [ ] Run the test and confirm it fails before UI changes.
- [ ] Fetch sites, replace the fixed profile list, create/select/save custom profiles, and update the site switcher to use the returned profiles.
- [ ] Add compact creation form and center the close icon with a fixed square button.
- [ ] Run the focused frontend test and confirm it passes.

### Task 4: Verify migration, full behavior and production build

**Files:**
- Modify: only files required by failures from the preceding tasks.

- [ ] Run the dynamic-site backend tests under the project Python 3.12 runtime.
- [ ] Run `node --experimental-strip-types --test src/modules/profit_activity/pages/ProfitActivityTestPage.test.ts`.
- [ ] Run `npm run build` from `web-frontend`.
- [ ] Run `git diff --check` and review the changed-file list for unrelated edits.

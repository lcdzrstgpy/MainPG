# ClipForge Sidecar Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the upstream ClipForge application as a managed local service and expose it as an AI-video module inside MainPG.

**Architecture:** Vendor ClipForge's source directly under `integrations/clipforge` (not a Git submodule or runtime reference), build its Next standalone output into the MainPG installer, and launch it from the MainPG FastAPI lifecycle with a bundled Node runtime. A small MainPG router reports the local URL and a React page embeds that URL. ClipForge retains its project, task persistence and FFmpeg composition code; its provider registry exposes only Volcengine and Suchuang.

**Tech Stack:** FastAPI/Python, React/Vite, Next.js/TypeScript, Vitest, pytest, FFmpeg.

## Global Constraints

- Do not copy `node_modules`, ClipForge runtime data, or compiled `.next` output into Git.
- Bind the sidecar only to `127.0.0.1`; data belongs below MainPG's runtime data directory.
- Never expose provider keys in MainPG browser responses or query strings.
- A non-idempotent provider submit must not be automatically retried.

---

### Task 1: Vendor the upstream application and document its AGPL provenance

**Files:**
- Create: `integrations/clipforge` (vendored source copy)
- Create: `docs/licenses/clipforge-AGPL.md`

- [ ] Copy the upstream ClipForge source directly into `integrations/clipforge`, excluding its nested Git metadata and generated artifacts.
- [ ] Verify `node_modules`, `.next`, `data`, and release artifacts are untracked.
- [ ] Record the upstream URL, pinned revision, and AGPL source-offer obligation.

### Task 2: Add a test-first managed-sidecar runtime

**Files:**
- Create: `local-runtime/wh_local/modules/clipforge/service.py`
- Create: `local-runtime/wh_local/modules/clipforge/router.py`
- Create: `local-runtime/wh_local/modules/clipforge/__init__.py`
- Test: `local-runtime/tests/test_clipforge_module.py`
- Modify: `local-runtime/wh_local/app/main.py`

**Interfaces:**
- `ClipForgeService(source_root: Path, data_root: Path, node_binary: str = "node")`
- `start() -> ClipForgeStatus`, `stop() -> None`, `status() -> ClipForgeStatus`
- `GET /api/clipforge/status -> {available, state, url?, message?}`

- [ ] Write tests proving a ready probe returns a loopback URL and that process shutdown is invoked once.
- [ ] Run the targeted pytest test and verify it fails because the module does not exist.
- [ ] Implement port allocation, standalone-entry validation, child startup/readiness probing, durable logs, and safe shutdown.
- [ ] Register the service/router in the FastAPI lifespan; unavailable Node/build output must leave MainPG healthy and return a diagnostic status.
- [ ] Run targeted pytest tests and verify they pass.

### Task 3: Surface the sidecar in the MainPG workspace

**Files:**
- Create: `web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx`
- Create: `web-frontend/src/modules/ai_video/api/clipforgeApi.ts`
- Create: `web-frontend/src/modules/ai_video/pages/AiVideoPage.test.tsx`
- Modify: `web-frontend/src/app/navigation/modules.ts`
- Modify: `web-frontend/src/app/layout/WorkspaceShell.tsx`

- [ ] Write a failing page test for loading, unavailable, and iframe-ready states.
- [ ] Implement the API client and a lazy-loaded “AI 视频” workspace module that embeds only the returned localhost URL.
- [ ] Run the focused frontend test and existing navigation tests.

### Task 4: Restrict ClipForge to Volcengine and Suchuang

**Files:**
- Create: `integrations/clipforge/src/lib/providers/suchuang.ts`
- Modify: `integrations/clipforge/src/lib/providers/index.ts`
- Modify: `integrations/clipforge/src/lib/stores/settings-store.ts`
- Test: `integrations/clipforge/src/lib/__tests__/providers-suchuang.test.ts`

- [ ] Write failing provider tests for Suchuang image/video task submission, task-status normalization, and no retry after an uncertain submit.
- [ ] Implement Suchuang's model descriptor catalog with explicit endpoint/body/status adapters; register only Suchuang and Volcengine as user-selectable providers.
- [ ] Preserve per-project image/video model choice and task-model persistence.
- [ ] Run focused Vitest tests and TypeScript/lint checks.

### Task 5: Build and end-to-end verify the reuse boundary

**Files:**
- Modify: `docs/licenses/clipforge-AGPL.md`
- Modify: `README.md`

- [ ] Build ClipForge standalone and run it through the MainPG service with a temporary data directory.
- [ ] Verify `/api/clipforge/status` becomes ready and the UI route renders its iframe.
- [ ] Document build/package prerequisites: Node 20+, pnpm 10+, generated standalone assets, FFmpeg, and source availability.

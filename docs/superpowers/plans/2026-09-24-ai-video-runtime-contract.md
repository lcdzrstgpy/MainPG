# MainPG AI 视频运行链整理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MainPG、ClipForge sidecar 和 AI 视频前端之间的构建、启动、健康检查契约整理为可验证、不可变、可恢复的一条运行链。

**Architecture:** `integrations/clipforge` 只作为源码；构建结果经过静态校验和 smoke test 后发布到版本化 artifact，MainPG 后端只运行 `<app_root>/server.js`。FastAPI 使用异步生命周期状态机和结构化健康探针管理 sidecar，React 前端轮询真实状态，并将服务健康与 iframe bridge 健康分开。

**Tech Stack:** Next.js 16.2.1、TypeScript、Vitest、Node.js 20+、pnpm 10+、FastAPI、Python、pytest、React 18、Vite、FFmpeg/FFprobe、PowerShell installer。

## Global Constraints

- 只在分支 `codex/ai-video-runtime-contract` 和隔离 worktree 中实施；基线为 `751909c3408e9b1b98b9755fa59cbae8859d3f6f`。
- 唯一 ClipForge 源码为 `integrations/clipforge`；不得读取或同步 `/Users/Zhuanz/Desktop/电商视频/clipforge`。
- MainPG 不得直接运行 `integrations/clipforge/.next/standalone`。
- 部署入口恒为 `<app_root>/server.js`；运行数据和日志不得写入 artifact。
- MainPG sidecar 必须保留 Node ABI；不得运行 Electron 的 `bundle-standalone.mjs` 后处理。
- sidecar 只绑定 `127.0.0.1`；浏览器响应不得包含密钥、绝对路径或原始堆栈。
- 不迁移 ClipForge 业务 API，不修改九宫格、速创、商品处理、POD 或 AI 视频创作业务。
- 所有行为变化先写失败测试；每个任务单独提交，禁止夹带无关文件。
- Python 命令使用项目可运行的 Python 解释器执行 `python -m pytest`；不要使用当前系统的 Python 3.9 运行声明为 Python 3.14 的完整环境。

---

## 文件结构与职责

### ClipForge 构建与健康

- `integrations/clipforge/src/lib/runtime-health.ts`：生成不泄露路径的结构化健康结果。
- `integrations/clipforge/src/app/api/health/route.ts`：只负责将健康结果映射为 HTTP 200/503。
- `integrations/clipforge/scripts/lib/mainpg-sidecar-artifact.mjs`：复制、验证、smoke test、哈希并发布不可变 artifact。
- `integrations/clipforge/scripts/prepare-mainpg-sidecar.mjs`：CLI 参数解析，不承载验证细节。

### MainPG 后端

- `local-runtime/wh_local/modules/clipforge/artifact.py`：解析 packaged app 或开发态 `current.json`，返回构建状态。
- `local-runtime/wh_local/modules/clipforge/health.py`：验证 `/api/health` JSON 和 `/start` 非空 HTML。
- `local-runtime/wh_local/modules/clipforge/process.py`：跨平台创建和终止完整进程树。
- `local-runtime/wh_local/modules/clipforge/service.py`：唯一 sidecar 生命周期状态机。
- `local-runtime/wh_local/modules/clipforge/router.py`：HTTP 状态码和 DTO 映射。

### MainPG 前端

- `web-frontend/src/modules/ai_video/state/clipforgeServiceState.ts`：纯状态判定、轮询间隔和实例 key。
- `web-frontend/src/modules/ai_video/hooks/useClipForgeService.ts`：请求、轮询和陈旧响应保护。
- `web-frontend/src/modules/ai_video/components/AiVideoServiceState.tsx`：非 ready 状态展示。
- `web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx`：ready 后的 iframe、导航和 bridge 生命周期。

---

### Task 1: 建立 ClipForge 结构化健康契约

**Files:**
- Create: `integrations/clipforge/src/lib/runtime-health.ts`
- Create: `integrations/clipforge/src/lib/__tests__/runtime-health.test.ts`
- Modify: `integrations/clipforge/src/app/api/health/route.ts`

**Interfaces:**
- Produces: `RuntimeHealthPayload`、`buildRuntimeHealth(input) -> RuntimeHealthPayload`
- Produces: `GET /api/health -> HTTP 200 | 503`
- Consumes later: MainPG `health.py` strictly validates this schema.

- [ ] **Step 1: Write the failing health-contract tests**

Create `runtime-health.test.ts` with real assertions against the exported builder:

```ts
import { describe, expect, it } from "vitest";
import { buildRuntimeHealth } from "@/lib/runtime-health";

const healthy = {
  instanceId: "instance-a",
  database: { status: "ok" as const },
  migrations: { status: "ok" as const },
  dataDirWritable: { status: "ok" as const },
  ffmpeg: { status: "ok" as const },
  ffprobe: { status: "ok" as const },
};

describe("MainPG runtime health contract", () => {
  it("reports ok only when every critical check is ok", () => {
    expect(buildRuntimeHealth(healthy)).toEqual({
      service: "clipforge",
      schemaVersion: 1,
      instanceId: "instance-a",
      status: "ok",
      checks: {
        database: { status: "ok" },
        migrations: { status: "ok" },
        dataDirWritable: { status: "ok" },
        ffmpeg: { status: "ok" },
        ffprobe: { status: "ok" },
      },
    });
  });

  it("reports error and a stable code without absolute paths", () => {
    const payload = buildRuntimeHealth({
      ...healthy,
      database: { status: "error" as const, code: "DB_UNAVAILABLE" },
    });
    expect(payload.status).toBe("error");
    expect(payload.checks.database).toEqual({ status: "error", code: "DB_UNAVAILABLE" });
    expect(JSON.stringify(payload)).not.toContain("/Users/");
    expect(JSON.stringify(payload)).not.toContain("C:\\");
  });
});
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
cd integrations/clipforge
pnpm exec vitest run src/lib/__tests__/runtime-health.test.ts
```

Expected: FAIL because `@/lib/runtime-health` does not exist.

- [ ] **Step 3: Implement the pure payload builder**

Create `runtime-health.ts` with these exact public types and rule:

```ts
export type RuntimeCheck =
  | { status: "ok" }
  | { status: "error"; code: string };

export type RuntimeHealthInput = {
  instanceId: string;
  database: RuntimeCheck;
  migrations: RuntimeCheck;
  dataDirWritable: RuntimeCheck;
  ffmpeg: RuntimeCheck;
  ffprobe: RuntimeCheck;
};

export type RuntimeHealthPayload = {
  service: "clipforge";
  schemaVersion: 1;
  instanceId: string;
  status: "ok" | "error";
  checks: Omit<RuntimeHealthInput, "instanceId">;
};

export function buildRuntimeHealth(input: RuntimeHealthInput): RuntimeHealthPayload {
  const { instanceId, ...checks } = input;
  const status = Object.values(checks).every((check) => check.status === "ok") ? "ok" : "error";
  return { service: "clipforge", schemaVersion: 1, instanceId, status, checks };
}
```

- [ ] **Step 4: Replace the leaking route with the strict contract**

In `route.ts`:

- Use `MAINPG_CLIPFORGE_INSTANCE_ID || "standalone"`.
- Convert `dbInitError`, `dbMigrationError`, a real `projects` count query, data-dir writability, `ffmpegBin()` and `ffprobeBin()` into the five checks.
- Probe media binaries with `execFile(binary, ["-version"], { timeout: 2000 })`; map any failure to `FFMPEG_UNAVAILABLE` or `FFPROBE_UNAVAILABLE`.
- Cache the two media probe promises for the lifetime of the Node process; health polling must not spawn FFmpeg and FFprobe every five seconds.
- Return `NextResponse.json(payload, { status: payload.status === "ok" ? 200 : 503 })`.
- Remove `runtime`, `db.file`, `paths.dataDir`, `paths.migrationsDir` and media absolute paths from the browser response.

The route must end with this response mapping:

```ts
const payload = buildRuntimeHealth({
  instanceId: process.env.MAINPG_CLIPFORGE_INSTANCE_ID || "standalone",
  database: databaseCheck,
  migrations: migrationsCheck,
  dataDirWritable: dataDirCheck,
  ffmpeg: ffmpegCheck,
  ffprobe: ffprobeCheck,
});
return NextResponse.json(payload, { status: payload.status === "ok" ? 200 : 503 });
```

- [ ] **Step 5: Run tests and build**

Run:

```bash
cd integrations/clipforge
pnpm exec vitest run src/lib/__tests__/runtime-health.test.ts src/lib/__tests__/ffmpeg-path.test.ts
pnpm build
```

Expected: focused tests PASS and Next production build completes.

- [ ] **Step 6: Commit**

```bash
git add integrations/clipforge/src/lib/runtime-health.ts integrations/clipforge/src/lib/__tests__/runtime-health.test.ts integrations/clipforge/src/app/api/health/route.ts
git commit -m "fix(ai-video): define structured ClipForge health"
```

---

### Task 2: 发布并验证不可变 MainPG sidecar artifact

**Files:**
- Create: `integrations/clipforge/scripts/lib/mainpg-sidecar-artifact.mjs`
- Create: `integrations/clipforge/scripts/mainpg-sidecar-artifact.test.mjs`
- Modify: `integrations/clipforge/scripts/prepare-mainpg-sidecar.mjs`
- Modify: `integrations/clipforge/package.json`

**Interfaces:**
- Produces: `validateMainpgArtifact(appRoot, options?) -> ArtifactMetadata`
- Produces: `publishMainpgArtifact({ sourceRoot, outputRoot, smokeTest?, resolveModule? }) -> Promise<PublishedArtifact>`
- Produces: `readCurrentArtifact(outputRoot) -> PublishedArtifact`
- Artifact metadata: `{ schemaVersion: 1, artifactId, buildId, runtime: "node", nodeModuleAbi }`

- [ ] **Step 1: Write failing artifact-contract tests**

The test file must create temporary fixtures and cover these cases:

```js
import { describe, expect, it } from "vitest";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  publishMainpgArtifact,
  readCurrentArtifact,
  validateMainpgArtifact,
} from "./lib/mainpg-sidecar-artifact.mjs";

function fixture(root) {
  const suffix = process.platform === "win32" ? ".exe" : "";
  for (const dir of [
    ".next/server",
    ".next/static",
    "node_modules/next",
    "node_modules/ffmpeg-static",
    `node_modules/@ffprobe-installer/ffprobe/${process.platform}-${process.arch}`,
    "public",
    "drizzle",
  ]) mkdirSync(join(root, dir), { recursive: true });
  writeFileSync(join(root, "server.js"), "export {};");
  writeFileSync(join(root, "package.json"), "{}");
  writeFileSync(join(root, ".next/BUILD_ID"), "build-a\n");
  writeFileSync(join(root, ".next/server/middleware-manifest.json"), "{}");
  writeFileSync(join(root, ".next/required-server-files.json"), JSON.stringify({ files: [".next/server/middleware-manifest.json"] }));
  writeFileSync(join(root, "node_modules/next/package.json"), "{}");
  writeFileSync(join(root, "node_modules/next/index.js"), "export {};");
  writeFileSync(join(root, `node_modules/ffmpeg-static/ffmpeg${suffix}`), "fixture");
  writeFileSync(join(root, `node_modules/@ffprobe-installer/ffprobe/${process.platform}-${process.arch}/ffprobe${suffix}`), "fixture");
}

it("rejects server.js-only partial builds", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-partial-"));
  writeFileSync(join(root, "server.js"), "export {};");
  expect(() => validateMainpgArtifact(root, { resolveModule: () => join(root, "node_modules/next/index.js") })).toThrow(/BUILD_ID/);
});

it("rejects a missing required-server-file", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-required-"));
  fixture(root);
  rmSync(join(root, ".next/server/middleware-manifest.json"));
  expect(() => validateMainpgArtifact(root, { resolveModule: () => join(root, "node_modules/next/index.js") })).toThrow(/middleware-manifest/);
});

it("rejects module resolution outside the artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-escape-"));
  fixture(root);
  expect(() => validateMainpgArtifact(root, { resolveModule: () => "/source/node_modules/next/index.js" })).toThrow(/outside artifact/);
});

it("does not replace current.json when smoke testing fails", async () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-publish-"));
  const sourceRoot = join(root, "source");
  const standalone = join(sourceRoot, ".next", "standalone");
  const outputRoot = join(root, "output");
  fixture(standalone);
  mkdirSync(join(sourceRoot, ".next", "static"), { recursive: true });
  mkdirSync(join(sourceRoot, "public"), { recursive: true });
  mkdirSync(join(sourceRoot, "drizzle"), { recursive: true });
  mkdirSync(outputRoot, { recursive: true });
  const pointer = join(outputRoot, "current.json");
  const previous = '{"schemaVersion":1,"artifactId":"old","relativePath":"artifacts/old"}\n';
  writeFileSync(pointer, previous);

  await expect(publishMainpgArtifact({
    sourceRoot,
    outputRoot,
    resolveModule: () => join(standalone, "node_modules", "next", "index.js"),
    smokeTest: async () => { throw new Error("smoke failed"); },
  })).rejects.toThrow("smoke failed");

  expect(readFileSync(pointer, "utf8")).toBe(previous);
});
```

- [ ] **Step 2: Run the test and verify failure**

```bash
cd integrations/clipforge
pnpm exec vitest run scripts/mainpg-sidecar-artifact.test.mjs
```

Expected: FAIL because the artifact library does not exist.

- [ ] **Step 3: Implement strict static validation**

`validateMainpgArtifact` must:

1. Resolve `appRoot` and require `server.js`, `package.json`, `.next/BUILD_ID`, `.next/required-server-files.json`, `node_modules/next/package.json`, `public`, `.next/static`, and `drizzle`.
2. Parse `required-server-files.json` and require every `files[]` entry below `appRoot`.
3. Use `createRequire(join(appRoot, "server.js"))` by default to resolve `next`, `next/dist/server/lib/start-server`, `@swc/helpers/_/_interop_require_default`, `styled-jsx`, and `better-sqlite3`.
4. Compare `realpathSync(resolved)` with `realpathSync(appRoot) + sep`; throw `Dependency <id> resolved outside artifact` on escape.
5. Read `mainpg-sidecar.json` when validating an already-published root and reject `runtime !== "node"`.

Use the exact return shape. Before publication `artifactId` is null; `--verify-root` sets `requireMetadata: true` and requires a non-empty id from `mainpg-sidecar.json`:

```js
return {
  schemaVersion: 1,
  artifactId: publishedMetadata?.artifactId ?? null,
  buildId,
  runtime: "node",
  nodeModuleAbi: process.versions.modules,
};
```

- [ ] **Step 4: Implement staged publication and smoke testing**

`publishMainpgArtifact` must follow this order:

```js
const standalone = join(sourceRoot, ".next", "standalone");
const staging = mkdtempSync(join(outputRoot, ".staging-"));
cpSync(standalone, staging, { recursive: true });
copyIfPresent(join(sourceRoot, ".next", "static"), join(staging, ".next", "static"));
copyIfPresent(join(sourceRoot, "public"), join(staging, "public"));
copyIfPresent(join(sourceRoot, "drizzle"), join(staging, "drizzle"));
copyMediaModules(sourceRoot, staging, ["ffmpeg-static", "@ffprobe-installer/ffprobe"]);
const checked = validateMainpgArtifact(staging);
const digest = digestTree(staging);
const artifactId = `${checked.buildId}-${digest.slice(0, 12)}`;
writeFileSync(join(staging, "mainpg-sidecar.json"), JSON.stringify({ ...checked, artifactId }, null, 2) + "\n");
await smokeTest(staging, artifactId);
renameSync(staging, join(outputRoot, "artifacts", artifactId));
atomicWriteJson(join(outputRoot, "current.json"), { schemaVersion: 1, artifactId, relativePath: `artifacts/${artifactId}` });
```

Implementation requirements:

- `digestTree` walks sorted relative paths and hashes relative path + file bytes; it excludes `mainpg-sidecar.json`.
- `resolveModule` is forwarded to static validation only for fixture isolation; production callers omit it and use `createRequire`.
- If the final artifact directory already exists, validate it and remove only the temporary staging directory.
- `atomicWriteJson` writes a sibling temporary file, fsyncs/closes it, then renames it over `current.json`.
- The default smoke test starts `node server.js` with a temporary `APP_DATA_DIR`, `APP_MIGRATIONS_DIR`, `MAINPG_CLIPFORGE_INSTANCE_ID`, `HOSTNAME=127.0.0.1`, and a free port.
- It waits for matching healthy `/api/health`, requires non-empty `/start?embed=mainpg` HTML, stops the child in `finally`, and verifies `digestTree` is unchanged.
- A failed static check or smoke test must not write `current.json`.

- [ ] **Step 5: Make the CLI thin and explicit**

`prepare-mainpg-sidecar.mjs` must support:

```text
node scripts/prepare-mainpg-sidecar.mjs --output-root <directory>
node scripts/prepare-mainpg-sidecar.mjs --verify-root <app-root>
```

The first command calls `publishMainpgArtifact`; the second calls `validateMainpgArtifact`. Add:

```json
"prepare:mainpg": "node scripts/prepare-mainpg-sidecar.mjs"
```

Do not call or import `bundle-standalone.mjs`.

- [ ] **Step 6: Run targeted and production verification**

```bash
cd integrations/clipforge
pnpm exec vitest run scripts/mainpg-sidecar-artifact.test.mjs src/lib/__tests__/runtime-health.test.ts
pnpm build
pnpm prepare:mainpg -- --output-root ../../local-runtime/outputs/wh-local/clipforge
node scripts/prepare-mainpg-sidecar.mjs --verify-root ../../local-runtime/outputs/wh-local/clipforge/artifacts/$(node -p "require('../../local-runtime/outputs/wh-local/clipforge/current.json').artifactId")
```

Expected: tests PASS; a versioned artifact and `current.json` are created; verification prints the selected artifact id; smoke test leaves the artifact hash unchanged.

- [ ] **Step 7: Commit**

```bash
git add integrations/clipforge/scripts/lib/mainpg-sidecar-artifact.mjs integrations/clipforge/scripts/mainpg-sidecar-artifact.test.mjs integrations/clipforge/scripts/prepare-mainpg-sidecar.mjs integrations/clipforge/package.json
git commit -m "build(ai-video): publish immutable ClipForge artifact"
```

---

### Task 3: 让 Windows 安装器复制已验证 artifact

**Files:**
- Modify: `local-runtime/build_installer.ps1`
- Modify: `local-runtime/tests/test_workbench_packaging.py`

**Interfaces:**
- Consumes: Task 2 `current.json` and `--verify-root` CLI.
- Produces: packaged layout `<dist>/MainPG/clipforge/app/server.js` and sibling `clipforge/node.exe`.

- [ ] **Step 1: Write failing packaging assertions**

Add this test to `test_workbench_packaging.py`:

```python
def test_installer_packages_the_verified_clipforge_app_root() -> None:
    script = (Path(__file__).parents[1] / "build_installer.ps1").read_text(encoding="utf-8")

    assert "--output-root" in script
    assert "current.json" in script
    assert "--verify-root" in script
    assert 'Join-Path $clipforgeBundle "app"' in script
    assert 'Join-Path $clipforgeApp "server.js"' in script
    assert '.next\\standalone' not in script
    assert "bundle-standalone.mjs" not in script
```

- [ ] **Step 2: Run the test and verify failure**

```bash
cd local-runtime
python -m pytest tests/test_workbench_packaging.py -q
```

Expected: FAIL because the installer still copies `.next\standalone` directly.

- [ ] **Step 3: Update the installer flow**

After `pnpm build`, set a dedicated publish root and invoke:

```powershell
$clipforgePublishRoot = Join-Path $PSScriptRoot "outputs\wh-local\clipforge"
& $nodeCommand.Source scripts\prepare-mainpg-sidecar.mjs --output-root $clipforgePublishRoot
if ($LASTEXITCODE -ne 0) { throw "ClipForge artifact publication failed" }
$clipforgeCurrent = Get-Content -LiteralPath (Join-Path $clipforgePublishRoot "current.json") -Raw | ConvertFrom-Json
$clipforgePublishedApp = Join-Path $clipforgePublishRoot $clipforgeCurrent.relativePath
& $nodeCommand.Source scripts\prepare-mainpg-sidecar.mjs --verify-root $clipforgePublishedApp
if ($LASTEXITCODE -ne 0) { throw "ClipForge artifact verification failed" }
```

Replace the old `$clipforgeStandalone` copy with:

```powershell
$clipforgeBundle = Join-Path $dist "clipforge"
$clipforgeApp = Join-Path $clipforgeBundle "app"
New-Item -ItemType Directory -Force -Path $clipforgeBundle | Out-Null
Copy-Item -LiteralPath $clipforgePublishedApp -Destination $clipforgeApp -Recurse -Force
if (-not (Test-Path -LiteralPath (Join-Path $clipforgeApp "server.js") -PathType Leaf)) {
    throw "Packaged ClipForge app root is missing server.js"
}
& $nodeCommand.Source scripts\prepare-mainpg-sidecar.mjs --verify-root $clipforgeApp
if ($LASTEXITCODE -ne 0) { throw "Packaged ClipForge app verification failed" }
Copy-Item -LiteralPath $nodeCommand.Source -Destination (Join-Path $clipforgeBundle "node.exe") -Force
```

Remove the later manual media-module copy loop because Task 2 already includes media modules before validation.

- [ ] **Step 4: Run tests and inspect PowerShell syntax**

```bash
cd local-runtime
python -m pytest tests/test_workbench_packaging.py -q
```

Expected: PASS. On Windows CI/build host additionally run `powershell -NoProfile -Command "[scriptblock]::Create((Get-Content -Raw build_installer.ps1)) | Out-Null"` and expect exit 0.

- [ ] **Step 5: Commit**

```bash
git add local-runtime/build_installer.ps1 local-runtime/tests/test_workbench_packaging.py
git commit -m "build(ai-video): package verified ClipForge app root"
```

---

### Task 4: 统一开发态与安装态 artifact 解析

**Files:**
- Create: `local-runtime/wh_local/modules/clipforge/artifact.py`
- Create: `local-runtime/tests/test_clipforge_artifact.py`
- Modify: `local-runtime/wh_local/modules/clipforge/__init__.py`

**Interfaces:**
- Produces: `ClipForgeBuild(state, app_root, artifact_id, message)`
- Produces: `resolve_clipforge_build(install_root, data_root) -> ClipForgeBuild`
- Consumes later: `ClipForgeService(build_resolver=...)`.

- [ ] **Step 1: Write failing resolver tests**

Cover packaged precedence, safe development pointer, missing build, invalid metadata, missing manifest, and traversal rejection:

```python
def test_packaged_app_root_uses_server_at_root(tmp_path: Path) -> None:
    app_root = tmp_path / "clipforge" / "app"
    write_valid_artifact(app_root, artifact_id="packaged-a")

    build = resolve_clipforge_build(tmp_path, tmp_path / "data")

    assert build.state == "available"
    assert build.app_root == app_root.resolve()
    assert build.app_root / "server.js" == app_root / "server.js"


def test_dev_pointer_cannot_escape_artifacts_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    pointer = data_root / "clipforge" / "current.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text('{"schemaVersion":1,"artifactId":"bad","relativePath":"../../outside"}')

    build = resolve_clipforge_build(tmp_path / "install", data_root)

    assert build.state == "invalid"
    assert build.app_root is None
```

`write_valid_artifact` must create `server.js`, `mainpg-sidecar.json`, `.next/BUILD_ID`, `.next/server/middleware-manifest.json`, `node_modules/next/package.json`, `public`, `.next/static`, and `drizzle`.

- [ ] **Step 2: Run and verify failure**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_artifact.py -q
```

Expected: FAIL because `artifact.py` does not exist.

- [ ] **Step 3: Implement immutable build resolution**

Use these exact public dataclasses/types:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ClipForgeBuildState = Literal["available", "missing", "invalid"]

@dataclass(frozen=True)
class ClipForgeBuild:
    state: ClipForgeBuildState
    app_root: Path | None
    artifact_id: str | None
    message: str
```

Resolution order:

1. `WH_CLIPFORGE_APP_ROOT` when set.
2. `<install_root>/clipforge/app` when it contains `server.js`.
3. `<data_root>/clipforge/current.json`, with `relativePath` resolved below `<data_root>/clipforge/artifacts`.
4. `missing`.

`validate_app_root` must require the same runtime subset as the Node validator: root `server.js`, `mainpg-sidecar.json` with `runtime=node`, `.next/BUILD_ID`, `.next/required-server-files.json`, middleware manifest, Next package, static, public and drizzle. Return `invalid` with a stable user message; do not include an absolute path.

- [ ] **Step 4: Export the resolver without changing the running service yet**

Export `ClipForgeBuild`, `ClipForgeBuildState` and `resolve_clipforge_build` from `wh_local.modules.clipforge.__init__`. Keep `_clipforge_source_root` and the old service constructor unchanged in this commit; Task 6 switches both together so every intermediate commit remains importable and testable.

- [ ] **Step 5: Run tests**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_artifact.py -q
```

Expected: artifact tests PASS.

- [ ] **Step 6: Commit**

```bash
git add local-runtime/wh_local/modules/clipforge/artifact.py local-runtime/wh_local/modules/clipforge/__init__.py local-runtime/tests/test_clipforge_artifact.py
git commit -m "refactor(ai-video): resolve one ClipForge app-root contract"
```

---

### Task 5: 建立严格健康探针与跨平台进程树管理

**Files:**
- Create: `local-runtime/wh_local/modules/clipforge/health.py`
- Create: `local-runtime/wh_local/modules/clipforge/process.py`
- Create: `local-runtime/tests/test_clipforge_health.py`
- Create: `local-runtime/tests/test_clipforge_process.py`

**Interfaces:**
- Produces: `wait_until_ready(base_url, process, instance_id, timeout_s=30.0) -> None`
- Produces: `popen_group_options() -> dict[str, object]`
- Produces: `terminate_process_tree(process, grace_s=5.0) -> None`
- Consumes later: `ClipForgeService`.

- [ ] **Step 1: Write failing health-probe tests**

Use injected `opener`, `clock` and `sleep` parameters so tests do not wait:

```python
@pytest.mark.parametrize("payload", [
    b"",
    b"not-json",
    b'{"service":"wrong","schemaVersion":1,"instanceId":"i","status":"ok","checks":{}}',
    b'{"service":"clipforge","schemaVersion":1,"instanceId":"old","status":"ok","checks":{}}',
    b'{"service":"clipforge","schemaVersion":1,"instanceId":"i","status":"error","checks":{}}',
])
def test_probe_rejects_false_ready_payloads(payload: bytes) -> None:
    process = RunningProcess()
    opener = SequenceOpener([Response(200, payload)])
    with pytest.raises(ClipForgeHealthError):
        wait_until_ready("http://127.0.0.1:54321", process, "i", timeout_s=0, opener=opener)


def test_probe_requires_matching_health_and_non_empty_start_html() -> None:
    health = b'{"service":"clipforge","schemaVersion":1,"instanceId":"i","status":"ok","checks":{"database":{"status":"ok"},"migrations":{"status":"ok"},"dataDirWritable":{"status":"ok"},"ffmpeg":{"status":"ok"},"ffprobe":{"status":"ok"}}}'
    opener = SequenceOpener([Response(200, health), Response(200, b"<html>ready</html>")])
    wait_until_ready("http://127.0.0.1:54321", RunningProcess(), "i", opener=opener)
```

Add explicit tests for HTTP 400, HTTP 404, empty start HTML and a child that exits before readiness.

- [ ] **Step 2: Write failing process-tree tests**

Verify:

- POSIX Popen options contain `start_new_session=True`.
- Windows options contain `CREATE_NEW_PROCESS_GROUP`.
- POSIX termination calls `os.killpg(pid, SIGTERM)`, waits, then uses `SIGKILL` only after timeout.
- Windows termination calls `taskkill /PID <pid> /T`, then `/F` only after timeout.
- Already-exited children are no-ops.

- [ ] **Step 3: Run tests and verify failure**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_health.py tests/test_clipforge_process.py -q
```

Expected: FAIL because both modules are absent.

- [ ] **Step 4: Implement strict readiness parsing**

`wait_until_ready` must request `/api/health`, require HTTP 200, parse JSON and check:

```python
required_checks = {"database", "migrations", "dataDirWritable", "ffmpeg", "ffprobe"}
if payload.get("service") != "clipforge": raise ClipForgeHealthError("unexpected health service")
if payload.get("schemaVersion") != 1: raise ClipForgeHealthError("unsupported health schema")
if payload.get("instanceId") != instance_id: raise ClipForgeHealthError("health instance mismatch")
if payload.get("status") != "ok": raise ClipForgeHealthError("health checks failed")
if any(payload.get("checks", {}).get(name, {}).get("status") != "ok" for name in required_checks):
    raise ClipForgeHealthError("required health check failed")
```

After health passes, request `/start?embed=mainpg`, require HTTP 200, `Content-Type` containing `text/html`, and at least one non-whitespace byte. Retry connection errors/503 until deadline; do not treat 400/404 as success.

- [ ] **Step 5: Implement process-group behavior**

Keep OS branching inside `process.py`. `terminate_process_tree` must always wait after TERM, escalate only on timeout, and tolerate `ProcessLookupError`. It must not use `shell=True`.

- [ ] **Step 6: Run the focused tests**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_health.py tests/test_clipforge_process.py -q
```

Expected: all targeted tests PASS.

- [ ] **Step 7: Commit**

```bash
git add wh_local/modules/clipforge/health.py wh_local/modules/clipforge/process.py tests/test_clipforge_health.py tests/test_clipforge_process.py
git commit -m "fix(ai-video): verify health and manage process trees"
```

---

### Task 6: 将 ClipForgeService 改为异步、可观察的状态机

**Files:**
- Modify: `local-runtime/wh_local/modules/clipforge/service.py`
- Modify: `local-runtime/wh_local/modules/clipforge/router.py`
- Modify: `local-runtime/wh_local/modules/clipforge/__init__.py`
- Modify: `local-runtime/wh_local/app/main.py`
- Rewrite focused cases: `local-runtime/tests/test_clipforge_module.py`
- Modify: `local-runtime/tests/test_runtime_exit_tabs.py`

**Interfaces:**
- Consumes: `Callable[[], ClipForgeBuild]`, `wait_until_ready`, `popen_group_options`, `terminate_process_tree`.
- Produces: `ClipForgeStatus`, `ClipForgeError`, `start()`, `status()`, `stop()`.
- Produces: stable `/api/clipforge/status` and `/api/clipforge/start` DTOs.

- [ ] **Step 1: Replace obsolete tests with state-machine tests**

Tests must use fake process/factory and threading events; cover:

```python
def test_start_returns_starting_without_waiting_for_probe(valid_build, service_factory) -> None:
    probe_entered = threading.Event()
    probe_release = threading.Event()
    service = service_factory(valid_build, probe=lambda *_: (probe_entered.set(), probe_release.wait()))

    status = service.start()

    assert status.state == "starting"
    assert status.build_state == "available"
    assert status.instance_id
    assert probe_entered.wait(1)
    probe_release.set()


def test_repeated_start_is_idempotent(service_factory) -> None:
    service, popen = service_factory.blocked_start()
    first = service.start()
    second = service.start()
    assert second.instance_id == first.instance_id
    assert popen.call_count == 1


def test_failed_state_persists_across_status_reads(service_factory) -> None:
    service = service_factory.with_probe_error(ClipForgeHealthError("bad health"))
    diagnostic = wait_for_state(service, "failed").error.diagnostic_id
    assert service.status().state == "failed"
    assert service.status().error.diagnostic_id == diagnostic


def test_old_generation_cannot_overwrite_restarted_instance(service_factory) -> None:
    service, generation_one, generation_two = service_factory.two_generation_service()
    first = service.start()
    assert generation_one.probe_entered.wait(1)

    service.stop()
    second = service.start()
    generation_two.probe_release.set()
    ready = wait_for_state(service, "ready")
    assert ready.instance_id == second.instance_id
    assert ready.instance_id != first.instance_id

    generation_one.probe_release.set()
    assert service.status().instance_id == second.instance_id
    assert service.status().state == "ready"
```

The test helper `two_generation_service()` returns one service plus two fake-generation controls, each exposing `probe_entered` and `probe_release` events. Also add direct assertions for process exit after ready, unavailable/invalid builds, log separation, full-tree stop, and media-path mapping for Darwin arm64, Linux x86_64, Windows AMD64.

- [ ] **Step 2: Run the focused tests and verify failure**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_module.py tests/test_runtime_exit_tabs.py -q
```

Expected: FAIL because the current service blocks, lacks states and accepts the old constructor.

- [ ] **Step 3: Define immutable public DTOs**

Use exact fields:

```python
ClipForgeRuntimeState = Literal["unavailable", "stopped", "starting", "ready", "failed", "stopping"]

@dataclass(frozen=True)
class ClipForgeError:
    code: str
    message: str
    retryable: bool
    exit_code: int | None = None
    diagnostic_id: str | None = None

@dataclass(frozen=True)
class ClipForgeStatus:
    state: ClipForgeRuntimeState
    build_state: ClipForgeBuildState
    available: bool
    url: str | None
    instance_id: str | None
    message: str
    error: ClipForgeError | None = None
```

`available` must be assigned only as `build_state == "available"`.

- [ ] **Step 4: Implement asynchronous start and generation isolation**

Constructor:

```python
def __init__(
    self,
    build_resolver: Callable[[], ClipForgeBuild],
    data_root: Path,
    node_binary: str = "node",
    *,
    process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ready_probe: Callable[..., None] = wait_until_ready,
) -> None:
```

`start()` must resolve the build, perform only a short locked transition, then launch `_start_worker(generation, build, instance_id, diagnostic_id)` on a daemon thread and return `starting`. The worker must:

1. Create `logs/<diagnostic-id>.log` and keep at most the newest 10 logs.
2. Start `[node_binary, app_root / "server.js"]` with `cwd=app_root`, group options and env containing `NODE_ENV`, `HOSTNAME`, `PORT`, `APP_DATA_DIR`, `APP_MIGRATIONS_DIR`, `MAINPG_CLIPFORGE_INSTANCE_ID`, `FFMPEG_PATH`, `FFPROBE_PATH`.
3. Publish the process/url only if generation still matches.
4. Call the strict ready probe outside the lock.
5. Set ready only if generation still matches and process is alive.
6. Start a watcher that records `CLIPFORGE_PROCESS_EXITED` when the current process exits.
7. On error, terminate that worker's process tree and persist `failed` with a stable code and diagnostic id.

`status()` may observe an exited process as a safety net, but must never turn `failed` into `stopped`. `stop()` increments generation before terminating so stale workers cannot write back.

- [ ] **Step 5: Implement router status codes and serialization**

The payload must use camelCase:

```python
def _payload(status: ClipForgeStatus) -> dict[str, object]:
    return {
        "state": status.state,
        "buildState": status.build_state,
        "available": status.available,
        "url": status.url,
        "instanceId": status.instance_id,
        "message": status.message,
        "error": None if status.error is None else {
            "code": status.error.code,
            "message": status.error.message,
            "retryable": status.error.retryable,
            "exitCode": status.error.exit_code,
            "diagnosticId": status.error.diagnostic_id,
        },
    }
```

`POST /start` returns 200 for ready, 202 for starting, 409 for unavailable/invalid build, and 500 only for unexpected internal failure. `GET /status` always returns 200.

- [ ] **Step 6: Wire lifecycle and hard-exit cleanup**

- FastAPI startup calls `clipforge.start()` without waiting for ready.
- Lifespan shutdown calls idempotent `clipforge.stop()`.
- Delete `_clipforge_source_root`; create a `clipforge_build_resolver()` closure that calls `resolve_clipforge_build(config.install_root, config.data_dir)` and pass it to `ClipForgeService`.
- Adjust `_clipforge_node_binary` to search `<install_root>/clipforge/node.exe` and `<install_root>/clipforge/node`, then return `node`.
- Replace the single product-processing exit callback with a bounded composite callback that calls `product_processing.cancel_all_active_for_shutdown()` and `clipforge.stop()` before `os._exit(0)`.
- Add a runtime-exit test with two spies and assert both are called once before the monkeypatched hard exit.

- [ ] **Step 7: Run backend verification**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_artifact.py tests/test_clipforge_health.py tests/test_clipforge_process.py tests/test_clipforge_module.py tests/test_runtime_exit_tabs.py -q
```

Expected: all focused tests PASS; the old “HTTP 400 means ready” test no longer exists.

- [ ] **Step 8: Commit**

```bash
git add local-runtime/wh_local/modules/clipforge local-runtime/wh_local/app/main.py local-runtime/tests/test_clipforge_module.py local-runtime/tests/test_runtime_exit_tabs.py
git commit -m "refactor(ai-video): model asynchronous sidecar lifecycle"
```

---

### Task 7: 提取前端服务状态与真实轮询

**Files:**
- Modify: `web-frontend/src/modules/ai_video/api/clipforgeApi.ts`
- Create: `web-frontend/src/modules/ai_video/state/clipforgeServiceState.ts`
- Create: `web-frontend/src/modules/ai_video/state/clipforgeServiceState.test.ts`
- Create: `web-frontend/src/modules/ai_video/hooks/useClipForgeService.ts`

**Interfaces:**
- Produces: frontend `ClipForgeStatus` matching Task 6 DTO.
- Produces: `pollDelayForState`, `clipForgeInstanceKey`, `canEmbedClipForge`.
- Produces: `useClipForgeService() -> { status, error, refresh, start }`.

- [ ] **Step 1: Write executable pure-state tests**

Use Node type stripping and import the real module:

```ts
import assert from "node:assert/strict";
import test from "node:test";
import { canEmbedClipForge, clipForgeInstanceKey, pollDelayForState } from "./clipforgeServiceState.ts";

test("polls starting quickly and ready periodically", () => {
  assert.equal(pollDelayForState("starting"), 800);
  assert.equal(pollDelayForState("ready"), 5000);
  assert.equal(pollDelayForState("stopped"), null);
  assert.equal(pollDelayForState("failed"), null);
});

test("embeds only a ready instance with url and instance id", () => {
  assert.equal(canEmbedClipForge({ state: "ready", url: "http://127.0.0.1:1", instanceId: "a" }), true);
  assert.equal(canEmbedClipForge({ state: "ready", url: "http://127.0.0.1:1", instanceId: null }), false);
  assert.equal(canEmbedClipForge({ state: "failed", url: "http://127.0.0.1:1", instanceId: "a" }), false);
});

test("instance key changes on url or instance id", () => {
  assert.notEqual(
    clipForgeInstanceKey({ url: "http://127.0.0.1:1", instanceId: "a" }),
    clipForgeInstanceKey({ url: "http://127.0.0.1:2", instanceId: "a" }),
  );
});
```

- [ ] **Step 2: Run and verify failure**

```bash
cd web-frontend
node --test --experimental-strip-types src/modules/ai_video/state/clipforgeServiceState.test.ts
```

Expected: FAIL because the module is absent.

- [ ] **Step 3: Expand API DTOs and add cancellation**

Use exact types:

```ts
export type ClipForgeRuntimeState = "unavailable" | "stopped" | "starting" | "ready" | "failed" | "stopping";
export type ClipForgeBuildState = "available" | "missing" | "invalid";
export type ClipForgeError = {
  code: string;
  message: string;
  retryable: boolean;
  exitCode: number | null;
  diagnosticId: string | null;
};
export type ClipForgeStatus = {
  state: ClipForgeRuntimeState;
  buildState: ClipForgeBuildState;
  available: boolean;
  url: string | null;
  instanceId: string | null;
  message: string;
  error: ClipForgeError | null;
};
```

`getClipForgeStatus(signal?: AbortSignal)` and `startClipForge(signal?: AbortSignal)` must pass `signal` into `apiRequest`.

- [ ] **Step 4: Implement pure state helpers**

```ts
export function pollDelayForState(state: ClipForgeRuntimeState): number | null {
  if (state === "starting" || state === "stopping") return 800;
  if (state === "ready") return 5000;
  return null;
}

export function canEmbedClipForge(status: Pick<ClipForgeStatus, "state" | "url" | "instanceId"> | null): boolean {
  return Boolean(status?.state === "ready" && status.url && status.instanceId);
}

export function clipForgeInstanceKey(status: Pick<ClipForgeStatus, "url" | "instanceId"> | null): string | null {
  return status?.url && status.instanceId ? `${status.instanceId}@${status.url}` : null;
}
```

- [ ] **Step 5: Implement the polling hook with stale-response protection**

The hook must:

- issue one initial `refresh()`;
- increment `requestSequence.current` for every request and apply a result only when it matches the latest sequence;
- abort the in-flight request and clear timers on unmount;
- schedule the next refresh from the returned state's `pollDelayForState`;
- after `start()` receives starting/ready, update immediately and schedule polling;
- expose backend error message without erasing a persisted `status.error`.

Use `setTimeout`, not `setInterval`, so requests never overlap.

- [ ] **Step 6: Run tests and TypeScript build**

```bash
cd web-frontend
node --test --experimental-strip-types src/modules/ai_video/state/clipforgeServiceState.test.ts
npm run build
```

Expected: state tests PASS and TypeScript/Vite build completes.

- [ ] **Step 7: Commit**

```bash
git add web-frontend/src/modules/ai_video/api/clipforgeApi.ts web-frontend/src/modules/ai_video/state web-frontend/src/modules/ai_video/hooks/useClipForgeService.ts
git commit -m "refactor(ai-video): poll truthful sidecar state"
```

---

### Task 8: 分离服务健康与 iframe bridge 健康

**Files:**
- Create: `web-frontend/src/modules/ai_video/components/AiVideoServiceState.tsx`
- Modify: `web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx`
- Modify: `web-frontend/src/modules/ai_video/pages/AiVideoPage.test.ts`
- Modify: `web-frontend/src/modules/ai_video/styles/aiVideoPage.css`

**Interfaces:**
- Consumes: Task 7 hook and pure state helpers.
- Preserves: `mainpg:ai-video-navigate` and `mainpg:ai-video-location` message types.
- Produces: distinct `serviceReady`, `bridgeReady`, `bridgeTimedOut` UI behavior.

- [ ] **Step 1: Extend tests before editing the page**

Keep existing executable navigation reducer tests. Add assertions that the page:

- obtains status from `useClipForgeService` rather than one mount-only request;
- keys iframe by `clipForgeInstanceKey(status)`;
- resets `AI_VIDEO_NAV_INITIAL_STATE` when instance key changes;
- shows “视频服务已就绪” before the first trusted location message;
- shows “AI 视频已连接” only after bridge ready;
- sets a 10,000ms bridge timeout and cleans it up;
- renders `AiVideoServiceState` for all non-embeddable states.

Add an executable pure reducer test for reset behavior rather than relying only on source regex:

```ts
test("a new sidecar instance resets iframe navigation readiness", () => {
  const connected = { activeHref: "/batch", ready: true, pendingHref: null };
  assert.deepEqual(resetAiVideoNavForInstance(connected), AI_VIDEO_NAV_INITIAL_STATE);
});
```

- [ ] **Step 2: Run and verify failure**

```bash
cd web-frontend
node --test --experimental-strip-types src/modules/ai_video/pages/AiVideoPage.test.ts
```

Expected: FAIL because polling hook, instance key and bridge timeout are not wired.

- [ ] **Step 3: Implement the state component**

`AiVideoServiceState` props:

```ts
type Props = {
  status: ClipForgeStatus | null;
  requestError: string;
  onStart: () => void;
};
```

Copy rules:

| State | Heading | Button |
|---|---|---|
| initial | `正在检查 AI 视频服务` | disabled |
| unavailable | `AI 视频服务未安装完整` | disabled |
| stopped | `AI 视频服务未启动` | `启动 AI 视频服务` |
| starting | `正在启动 AI 视频服务` | disabled |
| failed | `AI 视频服务启动失败` | `重新启动` when retryable |
| stopping | `正在关闭 AI 视频服务` | disabled |

For failed state show `error.message` and `诊断编号：<diagnosticId>` when present. Never render `exitCode` as the primary user message.

- [ ] **Step 4: Reset bridge state per instance**

In `AiVideoPage`:

```ts
const { status, error, start } = useClipForgeService();
const instanceKey = clipForgeInstanceKey(status);
const serviceReady = canEmbedClipForge(status);
const bridgeReady = navState.ready;

useEffect(() => {
  const reset = resetAiVideoNavForInstance(navStateRef.current);
  navStateRef.current = reset;
  setNavState(reset);
  setBridgeTimedOut(false);
}, [instanceKey]);
```

Define the tested reset helper beside the existing navigation reducer:

```ts
export function resetAiVideoNavForInstance(_state: AiVideoNavState): AiVideoNavState {
  return { ...AI_VIDEO_NAV_INITIAL_STATE };
}
```

Render iframe only when `serviceReady`; set `key={instanceKey ?? "clipforge-none"}` so a new backend instance cannot reuse the old document. Start a 10-second timeout while serviceReady and not bridgeReady; clear it on bridge ready, instance change or unmount.

Badge copy:

- `bridgeReady`: `● AI 视频已连接`
- `bridgeTimedOut`: `● 页面桥接超时`
- otherwise: `● 视频服务已就绪`

Do not change the seven navigation entries or trusted-origin/source checks.

- [ ] **Step 5: Add minimal styles**

Add modifiers for ready/warning/error badge states and a small diagnostic id line. Reuse the existing theme variables and preserve current responsive layout.

- [ ] **Step 6: Run frontend verification**

```bash
cd web-frontend
node --test --experimental-strip-types src/modules/ai_video/state/clipforgeServiceState.test.ts src/modules/ai_video/pages/AiVideoPage.test.ts
npm run build
```

Expected: tests PASS; build completes; existing navigation protocol tests remain green.

- [ ] **Step 7: Commit**

```bash
git add web-frontend/src/modules/ai_video/components/AiVideoServiceState.tsx web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx web-frontend/src/modules/ai_video/pages/AiVideoPage.test.ts web-frontend/src/modules/ai_video/styles/aiVideoPage.css
git commit -m "fix(ai-video): separate service and iframe readiness"
```

---

### Task 9: 增加真实 sidecar 验收并完成全链验证

**Files:**
- Create: `local-runtime/devtools/verify_clipforge_sidecar.py`
- Create: `local-runtime/tests/test_clipforge_sidecar_verifier.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: published app root and Task 6 service.
- Produces: a local acceptance command with non-zero exit on any contract violation.

- [ ] **Step 1: Write verifier unit tests**

Extract pure helpers and test:

```python
def test_tree_digest_changes_when_artifact_is_mutated(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    (root / "server.js").write_text("a", encoding="utf-8")
    before = tree_digest(root)
    (root / "server.js").write_text("b", encoding="utf-8")
    assert tree_digest(root) != before


def test_acceptance_requires_ready_and_matching_instance() -> None:
    assert_status_ready({"state": "ready", "instanceId": "a", "url": "http://127.0.0.1:1"}, "a")
    with pytest.raises(AssertionError):
        assert_status_ready({"state": "ready", "instanceId": "old", "url": "http://127.0.0.1:1"}, "new")
```

- [ ] **Step 2: Run and verify failure**

```bash
cd local-runtime
python -m pytest tests/test_clipforge_sidecar_verifier.py -q
```

Expected: FAIL because the verifier does not exist.

- [ ] **Step 3: Implement the real verifier**

CLI:

```text
python devtools/verify_clipforge_sidecar.py --app-root <artifact-root> [--node <node-binary>]
```

It must:

1. Compute a sorted SHA-256 digest of every artifact file.
2. Create a temporary data root and a static `ClipForgeBuild(state="available", app_root=app_root, artifact_id=artifact_id, message="ready")` resolver.
3. Start `ClipForgeService` and require the immediate state to be `starting`.
4. Poll `status()` to `ready` within 35 seconds.
5. Fetch `/api/health` and `/start?embed=mainpg`; require matching instance id and non-empty HTML.
6. Stop the service and assert no child process remains.
7. Start it a second time and require a different instance id.
8. Stop again and assert the artifact digest equals the original digest.
9. Print one concise PASS line containing artifact id and both instance ids; return non-zero on failure.

- [ ] **Step 4: Document the exact developer flow**

In `README.md`, add an “AI 视频 sidecar” section with:

```bash
cd integrations/clipforge
pnpm install --frozen-lockfile
pnpm build
pnpm prepare:mainpg -- --output-root ../../local-runtime/outputs/wh-local/clipforge

cd ../../local-runtime
CLIPFORGE_APP_ROOT="$(python -c 'import json,pathlib; p=pathlib.Path("outputs/wh-local/clipforge"); print(p / json.loads((p / "current.json").read_text())["relativePath"])')"
python devtools/verify_clipforge_sidecar.py --app-root "$CLIPFORGE_APP_ROOT"
```

Explain that the selected app root comes from `outputs/wh-local/clipforge/current.json`; do not instruct developers to run `.next/standalone/server.js` directly.

- [ ] **Step 5: Run the full focused suite**

```bash
cd integrations/clipforge
pnpm exec vitest run scripts/mainpg-sidecar-artifact.test.mjs src/lib/__tests__/runtime-health.test.ts src/lib/__tests__/ffmpeg-path.test.ts
pnpm build
pnpm prepare:mainpg -- --output-root ../../local-runtime/outputs/wh-local/clipforge

cd ../../local-runtime
python -m pytest tests/test_clipforge_artifact.py tests/test_clipforge_health.py tests/test_clipforge_process.py tests/test_clipforge_module.py tests/test_clipforge_sidecar_verifier.py tests/test_runtime_exit_tabs.py tests/test_workbench_packaging.py -q
python devtools/verify_clipforge_sidecar.py --app-root "$(python -c 'import json,pathlib; p=pathlib.Path("outputs/wh-local/clipforge"); print(p / json.loads((p / "current.json").read_text())["relativePath"])')"

cd ../web-frontend
node --test --experimental-strip-types src/modules/ai_video/state/clipforgeServiceState.test.ts src/modules/ai_video/pages/AiVideoPage.test.ts
npm run build
```

Expected: all focused tests PASS; both production builds complete; real verifier prints PASS.

- [ ] **Step 6: Manual MainPG acceptance**

Run MainPG frontend and backend from the isolated worktree, then verify:

1. `POST /api/clipforge/start` returns 202 quickly while state is starting.
2. `GET /api/clipforge/status` eventually returns ready with `buildState=available` and an `instanceId`.
3. AI 视频页 first shows “视频服务已就绪”, then “AI 视频已连接” after the trusted iframe location message.
4. Killing the ClipForge child changes backend/frontend to failed and keeps the diagnostic id stable across repeated status reads.
5. “重新启动” creates a new instance id and a new iframe.
6. Closing MainPG leaves no ClipForge Node or FFmpeg process.
7. The artifact digest remains unchanged.

- [ ] **Step 7: Commit**

```bash
git add local-runtime/devtools/verify_clipforge_sidecar.py local-runtime/tests/test_clipforge_sidecar_verifier.py README.md
git commit -m "test(ai-video): verify sidecar recovery end to end"
```

---

## DeepSeek 交接规则

1. 从提交 `84e8edcb`、分支 `codex/ai-video-runtime-contract` 开始，不从异常主目录复制文件。
2. 严格按 Task 1 → Task 9 顺序；每个任务完成测试和提交后再进入下一项。
3. 任一基线测试失败时，记录命令和完整失败摘要；不要通过删除断言、放宽健康条件或恢复 `<500` 即 ready 来绕过。
4. 不执行 `git reset --hard`、`git checkout --` 或批量清理；不提交计划之外的文件。
5. 若实际 Next 16 manifest 布局与 fixture 不一致，先打印 `.next/required-server-files.json` 并调整路径归一化，但验收标准仍是“manifest 列出的全部文件均存在于 artifact 内”。
6. 完成后提供九个提交 SHA、测试命令结果、真实 sidecar verifier 输出和仍存在的限制。

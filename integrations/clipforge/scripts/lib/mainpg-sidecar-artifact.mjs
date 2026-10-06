// MainPG sidecar 不可变 artifact 的发布与验证。
//
// 契约（见 docs/superpowers/specs/2026-09-24-ai-video-runtime-contract-design.md §5）：
//   1. integrations/clipforge 只作为源码，.next/standalone 是临时构建目录；
//   2. 构建结果必须经过静态校验 + 隔离 smoke test，才能发布成版本化目录；
//   3. 发布后只原子替换 current.json，运行中的旧实例继续读旧目录；
//   4. 运行期不得写 artifact（smoke test 前后哈希必须一致）；
//   5. 这里只产出 Node ABI 产物，绝不调用 bundle-standalone.mjs 的 Electron ABI 后处理。
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  closeSync,
  cpSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve, sep } from "node:path";

export const METADATA_FILE = "mainpg-sidecar.json";
const CURRENT_FILE = "current.json";
const ARTIFACTS_DIR = "artifacts";

// artifact 必须自带的运行期入口与清单：缺任何一项都不能发布/启动。
const REQUIRED_ENTRIES = [
  "server.js",
  ".next/BUILD_ID",
  "package.json",
  ".next/required-server-files.json",
  ".next/server/middleware-manifest.json",
  "node_modules/next/package.json",
  "public",
  ".next/static",
  "drizzle",
];

// 这些依赖必须能从 artifact 内部解析；任何"回退到源码根 node_modules"都必须被判失败，
// 因为安装态不存在源码根，开发态的回退会让残缺产物看起来仍能启动。
const DEPENDENCY_IDS = [
  "next",
  "next/dist/server/lib/start-server",
  "@swc/helpers/_/_interop_require_default",
  "styled-jsx",
  "better-sqlite3",
];

// @ffprobe-installer 的真实布局：包装包是 @ffprobe-installer/ffprobe，
// 平台二进制则是独立的 @ffprobe-installer/<platform>-<arch> 包，两者都要复制。
function mediaModules() {
  return ["ffmpeg-static", "@ffprobe-installer/ffprobe", `@ffprobe-installer/${process.platform}-${process.arch}`];
}

export function mediaBinaryPaths() {
  const suffix = process.platform === "win32" ? ".exe" : "";
  return {
    ffmpeg: join("node_modules", "ffmpeg-static", `ffmpeg${suffix}`),
    ffprobe: join("node_modules", "@ffprobe-installer", `${process.platform}-${process.arch}`, `ffprobe${suffix}`),
  };
}

function defaultResolveModule(id, appRoot) {
  const nodeRequire = createRequire(join(appRoot, "server.js"));
  return nodeRequire.resolve(id);
}

function insideArtifact(realPath, realRoot) {
  return realPath === realRoot || realPath.startsWith(realRoot + sep);
}

/**
 * 静态校验一个部署根：入口、清单、目录与依赖解析范围。
 * 通过时返回 artifact metadata（未发布时 artifactId 为 null）。
 */
export function validateMainpgArtifact(appRoot, options = {}) {
  const root = resolve(appRoot);
  if (!existsSync(root)) {
    throw new Error(`ClipForge artifact root does not exist: ${root}`);
  }
  const resolveModule = options.resolveModule ?? defaultResolveModule;
  const requireMetadata = options.requireMetadata === true;

  const media = mediaBinaryPaths();
  const missing = [];
  for (const entry of [...REQUIRED_ENTRIES, media.ffmpeg, media.ffprobe]) {
    if (!existsSync(join(root, entry))) missing.push(entry);
  }

  const requiredServerFiles = join(root, ".next", "required-server-files.json");
  if (existsSync(requiredServerFiles)) {
    let manifest;
    try {
      manifest = JSON.parse(readFileSync(requiredServerFiles, "utf8"));
    } catch (error) {
      throw new Error(`Invalid .next/required-server-files.json: ${error.message}`);
    }
    const files = Array.isArray(manifest?.files) ? manifest.files : [];
    if (files.length === 0) {
      throw new Error(".next/required-server-files.json does not list any files");
    }
    for (const file of files) {
      if (typeof file !== "string" || !file.trim()) continue;
      const normalized = file.replace(/^\.\//, "");
      if (!existsSync(join(root, normalized))) missing.push(normalized);
    }
  }

  if (missing.length > 0) {
    const unique = [...new Set(missing)];
    throw new Error(`ClipForge artifact is incomplete; missing: ${unique.join(", ")}`);
  }

  const buildId = readFileSync(join(root, ".next", "BUILD_ID"), "utf8").trim();
  if (!buildId) {
    throw new Error("ClipForge artifact has an empty .next/BUILD_ID");
  }

  const realRoot = realpathSync(root);
  for (const id of DEPENDENCY_IDS) {
    let resolved;
    try {
      resolved = resolveModule(id, root);
    } catch (error) {
      throw new Error(`Dependency ${id} cannot be resolved from the artifact: ${error.message}`);
    }
    if (typeof resolved !== "string" || !resolved) {
      throw new Error(`Dependency ${id} resolved to an empty path`);
    }
    let real;
    try {
      real = realpathSync(resolved);
    } catch {
      real = resolve(resolved);
    }
    if (!insideArtifact(real, realRoot)) {
      throw new Error(`Dependency ${id} resolved outside artifact: ${real}`);
    }
  }

  let publishedMetadata = null;
  const metadataPath = join(root, METADATA_FILE);
  if (existsSync(metadataPath)) {
    try {
      publishedMetadata = JSON.parse(readFileSync(metadataPath, "utf8"));
    } catch (error) {
      throw new Error(`Invalid ${METADATA_FILE}: ${error.message}`);
    }
    if (publishedMetadata?.runtime !== "node") {
      throw new Error(
        `ClipForge artifact runtime must be "node" (MainPG sidecar keeps Node ABI), found: ${String(publishedMetadata?.runtime)}`,
      );
    }
  }
  if (requireMetadata) {
    if (!publishedMetadata) {
      throw new Error(`${METADATA_FILE} is missing from the published artifact`);
    }
    if (!publishedMetadata.artifactId) {
      throw new Error(`${METADATA_FILE} does not carry an artifactId`);
    }
  }

  return {
    schemaVersion: 1,
    artifactId: publishedMetadata?.artifactId ?? null,
    buildId,
    runtime: "node",
    nodeModuleAbi: process.versions.modules,
    path: root,
  };
}

/** 目录内容哈希：排序后的相对路径 + 文件字节；不含发布元数据，因此发布前后保持一致。 */
export function digestTree(root) {
  const base = resolve(root);
  const hash = createHash("sha256");
  for (const relativePath of listFiles(base)) {
    if (relativePath === METADATA_FILE) continue;
    hash.update(relativePath);
    hash.update("\0");
    hash.update(readFileSync(join(base, relativePath)));
    hash.update("\0");
  }
  return hash.digest("hex");
}

function listFiles(root) {
  const found = [];
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const full = join(directory, entry.name);
      if (entry.isDirectory() || (entry.isSymbolicLink() && statSync(full).isDirectory())) {
        visit(full);
      } else {
        found.push(relative(root, full));
      }
    }
  };
  visit(root);
  return found.sort();
}

function copyIfPresent(from, to) {
  if (!existsSync(from)) return;
  cpSync(from, to, { recursive: true, force: true });
}

function copyMediaModules(sourceRoot, staging, modules) {
  for (const moduleId of modules) {
    const from = join(sourceRoot, "node_modules", moduleId);
    if (!existsSync(from)) continue;
    const to = join(staging, "node_modules", moduleId);
    mkdirSync(dirname(to), { recursive: true });
    cpSync(from, to, { recursive: true, force: true });
  }
  // npm/pnpm 有时会丢掉媒体二进制的可执行位（尤其 macOS/Linux 的 @ffprobe-installer）。
  // 在发布前修好，避免运行期 FFPROBE_PATH 指向一个不可执行的二进制。
  if (process.platform !== "win32") {
    const media = mediaBinaryPaths();
    for (const entry of [media.ffmpeg, media.ffprobe]) {
      const binary = join(staging, entry);
      if (existsSync(binary)) chmodSync(binary, 0o755);
    }
  }
}

function atomicWriteJson(path, value) {
  const temporary = `${path}.${process.pid}.tmp`;
  const descriptor = openSync(temporary, "w");
  try {
    writeFileSync(descriptor, JSON.stringify(value, null, 2) + "\n");
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
  renameSync(temporary, path);
}

function loopbackPort() {
  return new Promise((resolvePort, rejectPort) => {
    const server = createServer();
    server.once("error", rejectPort);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => resolvePort(port));
    });
  });
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

async function stopChild(child, graceMs = 5000) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolveExit) => child.once("exit", resolveExit));
  try {
    if (process.platform === "win32") child.kill();
    else process.kill(-child.pid, "SIGTERM");
  } catch {
    child.kill("SIGTERM");
  }
  const timedOut = await Promise.race([exited.then(() => false), sleep(graceMs).then(() => true)]);
  if (!timedOut) return;
  try {
    if (process.platform === "win32") child.kill("SIGKILL");
    else process.kill(-child.pid, "SIGKILL");
  } catch {
    child.kill("SIGKILL");
  }
  await exited;
}

/** 默认 smoke test：用隔离数据目录启动 staging，验证结构化健康与非空 HTML，并确保产物树未被修改。 */
export async function smokeTestArtifact(appRoot, artifactId, options = {}) {
  const root = resolve(appRoot);
  const dataRoot = mkdtempSync(join(tmpdir(), "clipforge-smoke-"));
  const port = await loopbackPort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const media = mediaBinaryPaths();
  const env = {
    ...process.env,
    NODE_ENV: "production",
    HOSTNAME: "127.0.0.1",
    PORT: String(port),
    APP_DATA_DIR: join(dataRoot, "data"),
    APP_MIGRATIONS_DIR: join(root, "drizzle"),
    MAINPG_CLIPFORGE_INSTANCE_ID: artifactId,
    // 与 MainPG 运行态一致：媒体二进制来自 artifact 自身，而不是开发机的 PATH。
    FFMPEG_PATH: join(root, media.ffmpeg),
    FFPROBE_PATH: join(root, media.ffprobe),
    ...(options.env ?? {}),
  };

  const before = digestTree(root);
  const child = spawn(process.execPath, [join(root, "server.js")], {
    cwd: root,
    env,
    stdio: "ignore",
    detached: process.platform !== "win32",
  });

  try {
    await waitForHealth(baseUrl, artifactId, child, options.timeoutMs ?? 30000);
    const start = await fetchText(`${baseUrl}/start?embed=mainpg`, 5000);
    if (!start.contentType.includes("text/html")) {
      throw new Error(`ClipForge smoke test: /start did not return HTML (${start.contentType})`);
    }
    if (start.body.trim().length === 0) {
      throw new Error("ClipForge smoke test: /start returned an empty HTML body");
    }
  } finally {
    await stopChild(child);
    rmSync(dataRoot, { recursive: true, force: true });
  }

  const after = digestTree(root);
  if (after !== before) {
    throw new Error("ClipForge smoke test modified the artifact tree");
  }
}

async function waitForHealth(baseUrl, instanceId, child, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = "no response";
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`ClipForge smoke test: server exited early (code ${child.exitCode})`);
    }
    try {
      const response = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(2000) });
      if (response.status === 200) {
        const payload = await response.json();
        requireHealthyPayload(payload, instanceId);
        return;
      }
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(250);
  }
  throw new Error(`ClipForge smoke test: health check timed out (${lastError})`);
}

function requireHealthyPayload(payload, instanceId) {
  const checks = payload?.checks ?? {};
  const required = ["database", "migrations", "dataDirWritable", "ffmpeg", "ffprobe"];
  if (payload?.service !== "clipforge") throw new Error("unexpected health service");
  if (payload?.schemaVersion !== 1) throw new Error("unsupported health schema");
  if (payload?.instanceId !== instanceId) throw new Error("health instance mismatch");
  if (payload?.status !== "ok") throw new Error("health checks failed");
  for (const name of required) {
    if (checks?.[name]?.status !== "ok") throw new Error(`required health check failed: ${name}`);
  }
}

async function fetchText(url, timeoutMs) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (response.status !== 200) {
    throw new Error(`GET ${url} returned HTTP ${response.status}`);
  }
  return {
    contentType: response.headers.get("content-type") ?? "",
    body: await response.text(),
  };
}

/**
 * 从源码根构建一次 staging、静态校验、smoke test，然后发布成版本化 artifact 并原子更新 current.json。
 * 任何一步失败都不写 current.json，残缺 staging 也不会成为当前产物。
 */
export async function publishMainpgArtifact({
  sourceRoot,
  outputRoot,
  smokeTest = smokeTestArtifact,
  resolveModule,
} = {}) {
  const standalone = join(sourceRoot, ".next", "standalone");
  if (!existsSync(join(standalone, "server.js"))) {
    throw new Error(`Missing ${join(standalone, "server.js")}. Run \`pnpm build\` first.`);
  }
  mkdirSync(outputRoot, { recursive: true });
  const staging = mkdtempSync(join(outputRoot, ".staging-"));
  const validateOptions = resolveModule ? { resolveModule } : undefined;
  try {
    cpSync(standalone, staging, { recursive: true });
    copyIfPresent(join(sourceRoot, ".next", "static"), join(staging, ".next", "static"));
    copyIfPresent(join(sourceRoot, "public"), join(staging, "public"));
    copyIfPresent(join(sourceRoot, "drizzle"), join(staging, "drizzle"));
    copyMediaModules(sourceRoot, staging, mediaModules());

    const checked = validateMainpgArtifact(staging, validateOptions);
    const digest = digestTree(staging);
    const artifactId = `${checked.buildId}-${digest.slice(0, 12)}`;
    writeFileSync(join(staging, METADATA_FILE), JSON.stringify({ ...checked, artifactId, digest }, null, 2) + "\n");

    await smokeTest(staging, artifactId);

    const artifactsRoot = join(outputRoot, ARTIFACTS_DIR);
    const finalRoot = join(artifactsRoot, artifactId);
    if (existsSync(finalRoot)) {
      validateMainpgArtifact(finalRoot, { ...validateOptions, requireMetadata: true });
      rmSync(staging, { recursive: true, force: true });
    } else {
      mkdirSync(artifactsRoot, { recursive: true });
      renameSync(staging, finalRoot);
    }

    atomicWriteJson(join(outputRoot, CURRENT_FILE), {
      schemaVersion: 1,
      artifactId,
      relativePath: `${ARTIFACTS_DIR}/${artifactId}`,
    });

    return {
      schemaVersion: 1,
      artifactId,
      buildId: checked.buildId,
      runtime: "node",
      nodeModuleAbi: checked.nodeModuleAbi,
      appRoot: finalRoot,
      digest,
    };
  } catch (error) {
    rmSync(staging, { recursive: true, force: true });
    throw error;
  }
}

/** 读取 current.json 指向的已发布 artifact，并重新做一次完整验证。 */
export function readCurrentArtifact(outputRoot, options = {}) {
  const pointer = join(outputRoot, CURRENT_FILE);
  if (!existsSync(pointer)) {
    throw new Error(`No published ClipForge artifact: ${pointer} is missing`);
  }
  let pointerData;
  try {
    pointerData = JSON.parse(readFileSync(pointer, "utf8"));
  } catch (error) {
    throw new Error(`Invalid ${pointer}: ${error.message}`);
  }
  const relativePath = pointerData?.relativePath;
  if (typeof relativePath !== "string" || !relativePath) {
    throw new Error(`${pointer} does not carry a relativePath`);
  }
  const appRoot = resolve(outputRoot, relativePath);
  if (!insideArtifact(appRoot, resolve(outputRoot))) {
    throw new Error(`Published artifact path escapes the publish root: ${relativePath}`);
  }
  const metadata = validateMainpgArtifact(appRoot, { ...options, requireMetadata: true });
  return { ...metadata, appRoot, digest: digestTree(appRoot) };
}

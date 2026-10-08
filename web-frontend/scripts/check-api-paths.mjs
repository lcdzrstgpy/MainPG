#!/usr/bin/env node
/**
 * 构建期守卫：确保前端发出的每个请求路径在开发态都能被 vite 代理，或在 public/ 有静态资源。
 *
 * 背景（2026-10-08 踩坑）：
 *   开发态页面在 5173、API 在 8010，请求靠 vite 的 server.proxy 转发。
 *   若某个请求路径不在 proxy 清单里，vite 会命中 **SPA fallback** 返回 index.html(200)，
 *   前端把它当「成功」——表现为假成功 / 静默失效，且**只在开发态复现**，极难排查。
 *   本脚本把这类问题前移到构建期直接报错。
 *
 * 用法：
 *   node scripts/check-api-paths.mjs        （已挂在 npm run build 前置）
 *
 * 判定规则：请求路径的首段必须满足其一 ——
 *   ① 在 vite.config.ts 的 server.proxy 前缀内；或
 *   ② 是 public/ 下真实存在的顶层目录（静态资源，dev 由 vite 直接服务）。
 * 覆盖范围：fetch / apiRequest / httpJson / httpBlob / ppDownload 的字面量参数，
 *           以及 `const XXX_BASE|_PATH|_URL = "/..."` 这类会拼进请求的常量。
 * 不覆盖：完全动态拼接（如 fetch(url)）——静态分析无法判定，脚本会静默跳过。
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, extname } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const srcDir = join(root, "src");
const publicDir = join(root, "public");

// ① 从 vite.config.ts 抽取代理前缀
const viteConfig = readFileSync(join(root, "vite.config.ts"), "utf8");
const proxyPrefixes = [...viteConfig.matchAll(/^\s*"(\/[^"]+)"\s*:\s*sameOriginProxy\(\)/gm)].map((m) => m[1]);
if (proxyPrefixes.length === 0) {
  console.error("[check-api-paths] 未能从 vite.config.ts 解析出任何代理前缀，脚本需同步更新。");
  process.exit(1);
}

// ② public/ 下真实存在的顶层目录
const publicTop = new Set(
  readdirSync(publicDir)
    .filter((name) => statSync(join(publicDir, name)).isDirectory())
    .map((name) => `/${name}`),
);

// 递归收集源码文件（排除测试文件：里面的路径多是 fixture）
function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      out.push(...walk(full));
    } else if ([".ts", ".tsx"].includes(extname(full)) && !/\.test\.tsx?$/.test(full)) {
      out.push(full);
    }
  }
  return out;
}

const REQUEST_FN = /\b(?:fetch|apiRequest|httpJson|httpBlob|ppDownload)\s*\(\s*[`'"]([^`'"]+)/g;
const BASE_CONST = /\bconst\s+[A-Z0-9_]*(?:_BASE|_PATH|_URL)\s*=\s*[`'"]([^`'"]+)/g;

/** prefix -> { example, file } */
const found = new Map();

for (const file of walk(srcDir)) {
  const source = readFileSync(file, "utf8");
  for (const re of [REQUEST_FN, BASE_CONST]) {
    re.lastIndex = 0;
    let match;
    while ((match = re.exec(source)) !== null) {
      const raw = match[1];
      if (!raw.startsWith("/") || raw.startsWith("//")) continue; // 变量拼接 / 协议相对外链，跳过
      const prefix = "/" + raw.split("/").filter(Boolean)[0];
      if (!found.has(prefix)) {
        found.set(prefix, { example: raw, file: relative(root, file).replace(/\\/g, "/") });
      }
    }
  }
}

const violations = [...found.entries()]
  .filter(([prefix]) => !proxyPrefixes.includes(prefix) && !publicTop.has(prefix))
  .map(([prefix, meta]) => ({ prefix, ...meta }));

console.log(`[check-api-paths] vite 代理前缀（${proxyPrefixes.length}）：${proxyPrefixes.join(" ")}`);
console.log(`[check-api-paths] public 静态目录（${publicTop.size}）：${[...publicTop].join(" ")}`);

if (violations.length === 0) {
  console.log(`[check-api-paths] OK：${found.size} 个请求路径首段前缀全部可路由。`);
  process.exit(0);
}

console.error("\n[check-api-paths] ✗ 发现未被 vite 代理覆盖的请求路径。");
console.error("  开发态这类请求会命中 SPA fallback 返回 index.html(200) → 前端「假成功」/ 静默失效：\n");
for (const v of violations) {
  console.error(`  ✗ ${v.prefix}    例：${v.example}    (${v.file})`);
}
console.error("\n修法：");
console.error("  · 若它是后端接口 → 在 web-frontend/vite.config.ts 的 server.proxy 补上该前缀（同一个 sameOriginProxy()）；");
console.error("  · 若它是静态资源 → 目录需真实存在于 web-frontend/public/ 下。");
process.exit(1);

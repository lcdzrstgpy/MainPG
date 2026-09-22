// Prepare the Next standalone tree for MainPG's bundled Node runtime.
// Unlike bundle-standalone.mjs this deliberately keeps Node ABI binaries: the
// service is launched by node.exe, not Electron.
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const standalone = join(root, ".next", "standalone");
const entry = join(standalone, "server.js");
if (!existsSync(entry)) {
  throw new Error("Missing .next/standalone/server.js. Run `pnpm build` first.");
}

for (const [from, to] of [
  [join(root, ".next", "static"), join(standalone, ".next", "static")],
  [join(root, "public"), join(standalone, "public")],
  [join(root, "drizzle"), join(standalone, "drizzle")],
]) {
  if (!existsSync(from)) continue;
  mkdirSync(to, { recursive: true });
  cpSync(from, to, { recursive: true, force: true });
}

if (!existsSync(join(standalone, "node_modules", "next", "package.json"))) {
  throw new Error("Standalone dependencies are incomplete: next was not traced into node_modules.");
}

console.log(`MainPG sidecar prepared: ${standalone}`);

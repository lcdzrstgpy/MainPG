// MainPG sidecar 发布 CLI（薄封装：只解析参数，验证细节全在 lib/mainpg-sidecar-artifact.mjs）。
//
//   node scripts/prepare-mainpg-sidecar.mjs --output-root <directory>   发布不可变 artifact
//   node scripts/prepare-mainpg-sidecar.mjs --verify-root <app-root>   校验一个部署根
//
// 刻意不调用 bundle-standalone.mjs：那条路径会把 better-sqlite3 换成 Electron ABI，
// 而 MainPG sidecar 必须保持 Node ABI。
import { resolve } from "node:path";
import { publishMainpgArtifact, validateMainpgArtifact } from "./lib/mainpg-sidecar-artifact.mjs";

const USAGE = [
  "Usage:",
  "  node scripts/prepare-mainpg-sidecar.mjs --output-root <directory>",
  "  node scripts/prepare-mainpg-sidecar.mjs --verify-root <app-root>",
].join("\n");

function parseArgs(argv) {
  const parsed = { outputRoot: null, verifyRoot: null };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--output-root") parsed.outputRoot = argv[++index] ?? null;
    else if (token === "--verify-root") parsed.verifyRoot = argv[++index] ?? null;
    else if (token === "--help" || token === "-h") parsed.help = true;
    else throw new Error(`Unknown argument: ${token}`);
  }
  return parsed;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(USAGE);
    return;
  }
  if (args.outputRoot && args.verifyRoot) {
    throw new Error(`--output-root and --verify-root are mutually exclusive\n${USAGE}`);
  }
  if (args.outputRoot) {
    const published = await publishMainpgArtifact({
      sourceRoot: process.cwd(),
      outputRoot: resolve(args.outputRoot),
    });
    console.log(`MainPG sidecar published: ${published.artifactId}`);
    console.log(`  app root: ${published.appRoot}`);
    console.log(`  build id: ${published.buildId} (node ABI ${published.nodeModuleAbi})`);
    console.log(`  digest:   ${published.digest}`);
    return;
  }
  if (args.verifyRoot) {
    const verified = validateMainpgArtifact(resolve(args.verifyRoot), { requireMetadata: true });
    console.log(
      `MainPG sidecar verified: ${verified.artifactId} (buildId ${verified.buildId}, node ABI ${verified.nodeModuleAbi})`,
    );
    return;
  }
  throw new Error(USAGE);
}

main().catch((error) => {
  console.error(`MainPG sidecar preparation failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});

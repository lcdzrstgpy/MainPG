import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { ConfiguredDefaults } from "@/lib/configured-defaults";

/**
 * 读取运维下发的默认设置文件 `clipforge.local.json`（平台 Key、LLM、默认模型）。
 *
 * 配置来源（按优先级，取到第一份即用）：
 *   1. `WH_CLIPFORGE_CONFIG` 指向的文件（MainPG 运行时注入路径：凭据只留在磁盘上，
 *      不进环境变量明文、不进前端存储）；
 *   2. `<APP_DATA_DIR>/clipforge.local.json`、`<cwd>/clipforge.local.json`（独立/打包部署各放一份）。
 * 与参考图中转的 cos.local.json 是同一套查找顺序。
 *
 * 解析不出来就返回 null：sidecar 照常启动，只是前端拿不到预填的 Key。
 */
export function configuredDefaultsPaths(): string[] {
  const paths: string[] = [];
  const explicit = String(process.env.WH_CLIPFORGE_CONFIG ?? "").trim();
  if (explicit) paths.push(explicit);
  const dataDir = String(process.env.APP_DATA_DIR ?? "").trim();
  if (dataDir) paths.push(join(dataDir, "clipforge.local.json"));
  paths.push(join(process.cwd(), "clipforge.local.json"));
  return paths;
}

export function resolveConfiguredDefaults(): ConfiguredDefaults | null {
  for (const path of configuredDefaultsPaths()) {
    if (!existsSync(path)) continue;
    try {
      const parsed = JSON.parse(readFileSync(path, "utf8")) as ConfiguredDefaults;
      if (parsed && typeof parsed === "object") return parsed;
    } catch {
      // 一份坏配置不该把页面拖下水：跳过它，继续找下一份
    }
  }
  return null;
}

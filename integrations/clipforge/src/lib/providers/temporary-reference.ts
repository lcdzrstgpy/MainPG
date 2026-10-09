import { createHash, createHmac, randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { extname, join } from "node:path";

/**
 * 参考图的「公网中转」：把本地图临时发布到腾讯 COS，返回一张上游能抓到的预签名 URL。
 *
 * 为什么必须有它：速创（无垠）这类异步接口的 `urls` / `images` 是交给上游去**抓**的，
 * 传 base64 data URI 或本地路径时上游抓不到，提交阶段就会被判
 * `{"code":500,"msg":"转发请求失败: 目标服务器返回 500 错误"}`（已实测复现）。
 * MainPG 媒体模块为此一直走 COS 中转（`ai_service/temporary_cos.py`），这里与它同一套做法：
 * 私有对象 + 预签名 GET，用完删除（视频任务期间不能删，改成延迟清理）。
 *
 * 配置来源（按优先级，取到第一份完整配置即用）：
 *   1. `WH_MEDIA_COS_CONFIG` 指向的 cos.local.json（MainPG 运行时注入路径：凭据只留在磁盘上，
 *      不进环境变量、不进前端存储）；
 *   2. `<APP_DATA_DIR>/cos.local.json`、`<cwd>/cos.local.json`（独立/打包部署各放一份即可）。
 * 配置缺失时 `resolveTemporaryReferenceConfig()` 返回 null，调用方保持原行为并把原因讲清楚——
 * 绝不静默发一个上游抓不到的 URL。
 */
export interface TemporaryReferenceConfig {
  bucket: string;
  region: string;
  secretId: string;
  secretKey: string;
  /** 对象键前缀，固定带一个专属目录，绝不与其它模块的对象混在一起。 */
  prefix: string;
}

const DEFAULT_PREFIX = "mainpg-clipforge/transient";

function configFromJson(raw: string): TemporaryReferenceConfig | null {
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const bucket = String(parsed.bucket ?? "").trim();
    const region = String(parsed.region ?? "").trim();
    const secretId = String(parsed.secret_id ?? parsed.secretId ?? "").trim();
    const secretKey = String(parsed.secret_key ?? parsed.secretKey ?? "").trim();
    if (!bucket || !region || !secretId || !secretKey) return null;
    // 凭据通常只授权到某个前缀（实测 tej 桶仅允许 cos_prefix/*），中转对象必须落在它下面
    const scoped = String(parsed.cos_prefix ?? parsed.prefix ?? "").trim().replace(/^\/+|\/+$/g, "");
    return {
      bucket,
      region,
      secretId,
      secretKey,
      prefix: scoped ? `${scoped}/${DEFAULT_PREFIX}` : DEFAULT_PREFIX,
    };
  } catch {
    return null;
  }
}

function configPaths(): string[] {
  const paths: string[] = [];
  const explicit = String(process.env.WH_MEDIA_COS_CONFIG ?? "").trim();
  if (explicit) paths.push(explicit);
  const dataDir = String(process.env.APP_DATA_DIR ?? "").trim();
  if (dataDir) paths.push(join(dataDir, "cos.local.json"));
  paths.push(join(process.cwd(), "cos.local.json"));
  return paths;
}

export function resolveTemporaryReferenceConfig(): TemporaryReferenceConfig | null {
  for (const path of configPaths()) {
    if (!existsSync(path)) continue;
    try {
      const config = configFromJson(readFileSync(path, "utf8"));
      if (config) return config;
    } catch {
      // 读不了就继续找下一份，绝不因为一份坏配置把整条链打断
    }
  }
  return null;
}

/** 上游能直接抓的地址才允许原样透传；data URI / 本地 /api/files 路径都必须中转。 */
export function isPublicHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value.trim());
}

export function decodeDataUri(value: string): { bytes: Buffer; contentType: string } | null {
  if (!value.startsWith("data:")) return null;
  const comma = value.indexOf(",");
  if (comma < 0) return null;
  const meta = value.slice(5, comma);
  const isBase64 = /;base64/i.test(meta);
  const contentType = meta.split(";")[0] || "image/jpeg";
  try {
    const bytes = isBase64
      ? Buffer.from(value.slice(comma + 1), "base64")
      : Buffer.from(decodeURIComponent(value.slice(comma + 1)), "utf8");
    return bytes.length ? { bytes, contentType } : null;
  } catch {
    return null;
  }
}

function hmacSha1(key: string | Buffer, data: string): string {
  return createHmac("sha1", key).update(data, "utf8").digest("hex");
}

function sha1Hex(data: string): string {
  return createHash("sha1").update(data, "utf8").digest("hex");
}

/** COS 签名要用小写、排序并用 `;` 连接的头/参数名列表。 */
function listOf(names: string[]): string {
  return [...names].map((name) => name.toLowerCase()).sort().join(";");
}

function buildSignature(input: {
  config: TemporaryReferenceConfig;
  method: "put" | "get" | "delete";
  pathname: string;
  /** 签名时参与的参数（不含签名本身），按 COS 规则 key 排序。 */
  params: Record<string, string>;
  headers: Record<string, string>;
  signTime: string;
  keyTime: string;
}): string {
  const paramKeys = Object.keys(input.params).map((key) => key.toLowerCase()).sort();
  const headerKeys = Object.keys(input.headers).map((key) => key.toLowerCase()).sort();
  const httpParameters = paramKeys.map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(input.params[key] ?? "")}`).join("&");
  const httpHeaders = headerKeys.map((key) => `${encodeURIComponent(key)}=${encodeURIComponent(input.headers[key] ?? "")}`).join("&");
  const httpString = [input.method, input.pathname, httpParameters, httpHeaders, ""].join("\n");
  const stringToSign = ["sha1", input.signTime, sha1Hex(httpString), ""].join("\n");
  const signKey = hmacSha1(input.config.secretKey, input.keyTime);
  return hmacSha1(signKey, stringToSign);
}

function signatureQuery(input: {
  config: TemporaryReferenceConfig;
  method: "put" | "get" | "delete";
  pathname: string;
  params: Record<string, string>;
  headers: Record<string, string>;
  expiresSeconds: number;
}): string {
  const now = Math.floor(Date.now() / 1000);
  const signTime = `${now};${now + input.expiresSeconds}`;
  const keyTime = signTime;
  const signature = buildSignature({ ...input, signTime, keyTime });
  return [
    `q-sign-algorithm=sha1`,
    `q-ak=${encodeURIComponent(input.config.secretId)}`,
    `q-sign-time=${encodeURIComponent(signTime)}`,
    `q-key-time=${encodeURIComponent(keyTime)}`,
    `q-header-list=${encodeURIComponent(listOf(Object.keys(input.headers)))}`,
    `q-url-param-list=${encodeURIComponent(listOf(Object.keys(input.params)))}`,
    `q-signature=${signature}`,
  ].join("&");
}

function objectHost(config: TemporaryReferenceConfig): string {
  return `${config.bucket}.cos.${config.region}.myqcloud.com`;
}

function extensionFor(contentType: string): string {
  const known: Record<string, string> = { "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp" };
  return known[contentType.toLowerCase()] ?? (extname(contentType) || ".image");
}

export interface PublishedReference {
  key: string;
  url: string;
}

/**
 * 本地字节 → COS 私有对象 + 预签名 GET URL（上游可直接抓取）。
 * 上传/签名失败一律抛错：宁可让调用方明确失败，也不发一个抓不到的 URL 去换一个 500。
 */
export async function publishTemporaryReference(
  config: TemporaryReferenceConfig,
  bytes: Buffer,
  contentType: string,
  expiresSeconds = 1800
): Promise<PublishedReference> {
  const date = new Date();
  const dayPath = `${date.getUTCFullYear()}/${String(date.getUTCMonth() + 1).padStart(2, "0")}/${String(date.getUTCDate()).padStart(2, "0")}`;
  const key = `${config.prefix}/${dayPath}/${randomUUID()}${extensionFor(contentType)}`;
  const pathname = `/${key}`;
  // 用查询串形式的预签名 PUT（与腾讯 SDK 的 get_presigned_url 一致）：Authorization 头形式
  // 在本桶上会被 COS 判 AccessDenied/Request has expired，同一份签名放进 query 才通。
  const signedHeaders = { host: objectHost(config) };
  const authorization = signatureQuery({ config, method: "put", pathname, params: {}, headers: signedHeaders, expiresSeconds: 300 });

  const response = await fetch(`https://${objectHost(config)}${pathname}?${authorization}`, {
    method: "PUT",
    headers: { "Content-Type": contentType },
    body: new Uint8Array(bytes),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`参考图中转上传失败: HTTP ${response.status} ${detail.slice(0, 200)}`.trim());
  }

  // 预签名 GET：对象保持私有，URL 在 expiresSeconds 内可被上游抓取
  const getQuery = signatureQuery({ config, method: "get", pathname, params: {}, headers: {}, expiresSeconds });
  return { key, url: `https://${objectHost(config)}${pathname}?${getQuery}` };
}

/** 尽力删除中转对象；失败只记录，绝不影响已经拿到的生成结果。 */
export async function deleteTemporaryReference(config: TemporaryReferenceConfig, key: string): Promise<void> {
  try {
    const pathname = `/${key}`;
    const authorization = signatureQuery({
      config,
      method: "delete",
      pathname,
      params: {},
      headers: { host: objectHost(config) },
      expiresSeconds: 300,
    });
    const response = await fetch(`https://${objectHost(config)}${pathname}?${authorization}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) {
      const detail = await response.text().catch(() => "");
      warnDeleteOnce(`HTTP ${response.status} ${detail.slice(0, 120)}`.trim());
    }
  } catch (error) {
    warnDeleteOnce(error instanceof Error ? error.message : String(error));
  }
}

/** 删除失败只提醒一次（多数部署的密钥没有 DeleteObject 权限，靠桶的生命周期规则过期）。 */
let deleteWarningShown = false;
function warnDeleteOnce(detail: string): void {
  if (deleteWarningShown) return;
  deleteWarningShown = true;
  console.warn(
    `[temporary-reference] 中转对象删除被拒（${detail}）——已忽略；请给中转前缀配置生命周期过期规则，否则临时图会留在桶里`
  );
}

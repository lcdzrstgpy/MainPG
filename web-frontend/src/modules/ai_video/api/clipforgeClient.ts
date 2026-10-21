/**
 * ClipForge 前端请求层（T0.2）。
 *
 * 所有请求都走 MainPG 同源代理 `/api/clipforge/proxy`，浏览器永远不知道 sidecar 的随机端口：
 * - 错误文案优先取 `{detail:{message}}`，其次 `{detail}` / `{error}`，最后按状态码给中文兜底；
 * - 取消（AbortSignal）原样抛出，调用方据此静默处理，不当作失败提示；
 * - 2xx 但返回 HTML（打包态被 SPA fallback 接走）视为失败，避免把「拿错内容」伪装成空数据；
 * - 这里不引入任何运行时依赖，便于 `node --experimental-strip-types --test` 直接执行单测。
 */

export const CF_PROXY_PREFIX = "/api/clipforge/proxy";

export type CfInit = Omit<RequestInit, "body"> & {
  /** 传入对象时自动 JSON.stringify 并补 Content-Type；FormData/Blob 请直接使用 body。 */
  json?: unknown;
  body?: BodyInit | null;
};

/** 带 HTTP 状态码与稳定错误码的请求失败；网络层失败时 status 为 0。 */
export class ClipForgeRequestError extends Error {
  readonly status: number;
  readonly code: string | null;
  /** 原始错误体（如 409 选风格需要读取 candidates），非 JSON 时为 null。 */
  readonly payload: unknown;

  constructor(
    message: string,
    options: { status?: number; code?: string | null; payload?: unknown } = {},
  ) {
    super(message);
    this.name = "ClipForgeRequestError";
    this.status = options.status ?? 0;
    this.code = options.code ?? null;
    this.payload = options.payload ?? null;
  }
}

/** 把 sidecar 相对路径拼成同源代理地址；兼容有无前导斜杠。 */
export function cfUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${CF_PROXY_PREFIX}${normalized}`;
}

export async function cfJson<T>(path: string, init?: CfInit): Promise<T> {
  const response = await cfFetch(path, init);
  if (response.status === 204 || response.status === 205) return undefined as T;

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    const text = await response.text().catch(() => "");
    throw new ClipForgeRequestError(
      looksLikeHtml(text)
        ? "AI 视频服务返回了网页而不是数据，请重启服务后重试"
        : `AI 视频服务返回了无法识别的内容（HTTP ${response.status}）`,
      { status: response.status },
    );
  }
  try {
    const value = (await response.json()) as T | null;
    return (value ?? {}) as T;
  } catch {
    throw new ClipForgeRequestError("AI 视频服务返回了无效的 JSON 数据", { status: response.status });
  }
}

export async function cfBlob(path: string, init?: CfInit): Promise<Blob> {
  const response = await cfFetch(path, init);
  return response.blob();
}

/** 轻量探测（媒体是否存在、Content-Length 等）；HTTP 非 2xx 同样抛错。 */
export async function cfHead(path: string, init?: CfInit): Promise<Response> {
  return cfFetch(path, { ...init, method: "HEAD" });
}

async function cfFetch(path: string, init?: CfInit): Promise<Response> {
  const { json, headers, body: rawBody, ...rest } = init ?? {};
  const requestHeaders = new Headers(headers);
  let body: BodyInit | null | undefined = rawBody;
  if (json !== undefined) {
    body = JSON.stringify(json);
    if (!requestHeaders.has("Content-Type")) requestHeaders.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(cfUrl(path), { ...rest, body, headers: requestHeaders });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ClipForgeRequestError("无法连接 AI 视频服务，请确认服务已启动");
  }

  if (!response.ok) throw await errorFromResponse(response);
  return response;
}

async function errorFromResponse(response: Response): Promise<ClipForgeRequestError> {
  const fallback = messageForStatus(response.status);
  let message = fallback;
  let code: string | null = null;
  let payload: unknown = null;

  try {
    payload = (await response.json()) as unknown;
    const body = payload as { detail?: unknown; error?: unknown; code?: unknown } | null;
    if (body && typeof body === "object") {
      if (typeof body.code === "string" && body.code) code = body.code;
      const detail = body.detail;
      if (typeof detail === "string" && detail.trim()) {
        message = detail;
      } else if (detail && typeof detail === "object") {
        const nested = detail as { message?: unknown; code?: unknown };
        if (typeof nested.message === "string" && nested.message.trim()) message = nested.message;
        if (typeof nested.code === "string" && nested.code) code = nested.code;
      }
      if (message === fallback && typeof body.error === "string" && body.error.trim()) {
        message = body.error;
      }
    }
  } catch {
    // 非 JSON 错误体：保留状态码兜底文案
  }

  return new ClipForgeRequestError(message, { status: response.status, code, payload });
}

function messageForStatus(status: number): string {
  switch (status) {
    case 400:
      return "AI 视频请求参数有误（HTTP 400）";
    case 401:
    case 403:
      return `没有访问 AI 视频服务的权限（HTTP ${status}）`;
    case 404:
      return "AI 视频服务中找不到请求的内容（HTTP 404）";
    case 409:
      return "操作冲突，请刷新后重试（HTTP 409）";
    case 413:
      return "文件太大，请压缩后重试（HTTP 413）";
    case 415:
      return "文件格式不支持（HTTP 415）";
    case 499:
      return "请求已取消（HTTP 499）";
    case 502:
      return "AI 视频服务暂时不可用（HTTP 502）";
    case 503:
      return "AI 视频服务未就绪（HTTP 503）";
    case 504:
      return "AI 视频服务响应超时（HTTP 504）";
    default:
      return `AI 视频请求失败（HTTP ${status}）`;
  }
}

function looksLikeHtml(text: string): boolean {
  return /^\s*<(?:!doctype|html|head|body)\b/i.test(text);
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

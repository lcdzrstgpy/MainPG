import { toUserMessage } from "../../../transport/http/client";

type ApiContext = {
  baseUrl: string;
  token: string;
  workspaceId: string;
};

type JsonRequestInit = Omit<RequestInit, "body"> & { body?: unknown };

export class PpRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "PpRequestError";
    this.status = status;
  }
}

function buildUrl(context: ApiContext, path: string): string {
  const base = context.baseUrl.replace(/\/$/, "");
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${base}${normalized}`;
}

function authHeaders(context: ApiContext): HeadersInit {
  const headers: HeadersInit = {
    "X-Workspace-ID": context.workspaceId,
  };
  if (context.token) {
    headers["Authorization"] = `Bearer ${context.token}`;
  }
  return headers;
}

// 本模块的请求多为长耗时任务：SKU 规格图批量 OCR（本地 CPU 推理，单批 20 条链接）、
// 近百条商品的结果预检聚合、导出收尾等，受本机 SQLite 锁与 OCR 推理排队影响，
// 几十秒到数分钟都属正常，30s 短限会把正常慢请求误杀成「请求超时」。
// 这里只保留 10 分钟上限做挂死兜底，不再对慢请求设短限。
const DEFAULT_TIMEOUT_MS = 10 * 60_000;

/** 带超时的 fetch：慢请求挂起会无限堆积，10 分钟后强制中断并给出可读错误。 */
function fetchWithTimeout(input: string, init: RequestInit, timeoutMs: number = DEFAULT_TIMEOUT_MS): Promise<Response> {
  const controller = new AbortController();
  const externalSignal = init.signal ?? null;
  const onAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onAbort, { once: true });
  }
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  return fetch(input, { ...init, signal: controller.signal })
    .catch((error: unknown) => {
      const timedOut = controller.signal.aborted && !(externalSignal?.aborted ?? false);
      if (timedOut) throw new PpRequestError("请求超时，请稍后重试", 0);
      throw error;
    })
    .finally(() => {
      window.clearTimeout(timer);
      if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
    });
}

export async function ppRequest<T>(
  context: ApiContext,
  path: string,
  options: JsonRequestInit = {}
): Promise<T> {
  const url = buildUrl(context, path);
  const headers = new Headers(authHeaders(context));
  if (options.headers) {
    new Headers(options.headers).forEach((value, key) => {
      headers.set(key, value);
    });
  }
  const { body, ...rest } = options;
  const needsBody = body !== undefined;
  const fetchBody = needsBody
    ? typeof body === "string"
      ? body
      : JSON.stringify(body)
    : undefined;
  if (needsBody && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  const response = await fetchWithTimeout(url, {
    ...rest,
    method: rest.method ?? (needsBody ? "POST" : "GET"),
    headers,
    body: fetchBody,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string"
        ? payload.detail
        : typeof payload === "string"
        ? payload
        : JSON.stringify(payload);
    throw new PpRequestError(toUserMessage(detail || `请求失败: ${response.status}`), response.status);
  }
  return payload as T;
}

export async function ppUpload<T>(
  context: ApiContext,
  path: string,
  formData: FormData
): Promise<T> {
  const url = buildUrl(context, path);
  const headers = new Headers(authHeaders(context));
  const response = await fetchWithTimeout(url, {
    method: "POST",
    headers,
    body: formData,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string"
        ? payload.detail
        : typeof payload === "string"
        ? payload
        : JSON.stringify(payload);
    throw new PpRequestError(toUserMessage(detail || `上传失败: ${response.status}`), response.status);
  }
  return payload as T;
}

export async function ppDownload(
  context: ApiContext,
  path: string,
  filename: string
): Promise<void> {
  const url = buildUrl(context, path);
  const headers = new Headers(authHeaders(context));
  const response = await fetch(url, { headers });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(toUserMessage(text || `下载失败: ${response.status}`));
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  // 不能紧跟着同步回收：下载尚未真正读取 blob 时数据源已被释放，
  // 个别浏览器会下到空文件。与 ProfitActivityTestPage / podCustomizationApi 保持一致。
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export type { ApiContext };

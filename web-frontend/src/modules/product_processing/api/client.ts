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
  const response = await fetch(url, {
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
    throw new PpRequestError(detail || `请求失败: ${response.status}`, response.status);
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
  const response = await fetch(url, {
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
    throw new PpRequestError(detail || `上传失败: ${response.status}`, response.status);
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
    throw new Error(text || `下载失败: ${response.status}`);
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

export type { ApiContext };

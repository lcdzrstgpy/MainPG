type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

type RequestOptions = {
  method?: HttpMethod;
  body?: unknown;
  token?: string;
};

const TOKEN_KEY = "wh_demo_token";
const ACCOUNT_KEY = "wh_demo_account";

function apiBaseUrl() {
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

function authToken(explicitToken?: string) {
  return explicitToken ?? window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function getAuthToken() {
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function getAuthAccount<T>() {
  try {
    return JSON.parse(window.localStorage.getItem(ACCOUNT_KEY) ?? "null") as T | null;
  } catch {
    return null;
  }
}

export function saveAuthSession(token: string, account: unknown) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(ACCOUNT_KEY, JSON.stringify(account ?? {}));
}

export function clearAuthSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(ACCOUNT_KEY);
}

export async function httpJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = authToken(options.token);
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : `请求失败 (HTTP ${response.status})`;
    throw new Error(detail);
  }

  return payload as T;
}

export async function httpBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = authToken(options.token);
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => "请求失败");
    throw new Error(detail || "请求失败");
  }

  return response.blob();
}

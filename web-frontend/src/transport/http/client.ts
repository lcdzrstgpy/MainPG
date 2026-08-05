type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

type RequestOptions = {
  method?: HttpMethod;
  body?: unknown;
  token?: string;
};

const DEFAULT_DEV_TOKEN = "dev-admin-token";

function apiBaseUrl() {
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

function authToken(explicitToken?: string) {
  return explicitToken ?? window.localStorage.getItem("wh_demo_token") ?? DEFAULT_DEV_TOKEN;
}

export async function httpJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method: options.method ?? "GET",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${authToken(options.token)}`,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : "请求失败";
    throw new Error(detail);
  }

  return payload as T;
}


import { isSessionExpired, notifySessionExpired, toUserMessage } from "../../transport/http/client";

const TOKEN_KEY = "wh_demo_token";

export function getApiToken(): string {
  const stored = window.localStorage.getItem(TOKEN_KEY);
  if (stored) return stored;
  return import.meta.env.VITE_WH_API_TOKEN || "dev-admin-token";
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${getApiToken()}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Keep the status-based fallback when the response is not JSON.
    }
    // 与 httpJson 保持一致：会话失效统一走登出事件，避免用户停留在页面反复 401。
    if (isSessionExpired(response, message)) notifySessionExpired();
    throw new Error(toUserMessage(message));
  }

  // 204/空体等无 JSON 的成功响应兜底为空对象，避免抛英文 SyntaxError 绕过中文映射。
  return response.json().catch(() => ({})) as Promise<T>;
}

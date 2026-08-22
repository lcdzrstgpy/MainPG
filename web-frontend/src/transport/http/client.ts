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

const SESSION_EXPIRED_EVENT = "auth:session-expired";

/** 通知应用层登录状态已失效（登录超时 / 远程会话缺失），用于自动返回登录页。 */
export function notifySessionExpired(): void {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
}

function isSessionExpired(response: Response, detail: string): boolean {
  if (response.status === 401) return true;
  return /login session expired|remote customer session is missing|invalid bearer token|missing bearer token/i.test(detail);
}

/**
 * 把服务端/网络错误转换为用户可读的中文提示（不再外露英文）。
 * - 已知业务错误映射为具体中文 + 解决建议；
 * - 纯英文的未知错误兜底为通用中文提示；
 * - 已是中文的提示原样返回；
 * - `settings_revision_conflict` 等仅供内部重试判断的标记保留原文。
 */
export function toUserMessage(raw: string): string {
  const message = String(raw ?? "").trim();
  if (!message) return "操作失败，请稍后重试";
  // 内部判断用标记（乐观锁重试等），不直接展示给用户，保留原文
  if (message.includes("settings_revision_conflict")) return message;
  // 登录会话失效
  if (
    /remote customer session is missing/i.test(message) ||
    /login session expired/i.test(message) ||
    /invalid bearer token/i.test(message) ||
    /missing bearer token/i.test(message) ||
    /session (has )?expired/i.test(message)
  ) {
    return "登录状态已过期，请退出后重新登录";
  }
  // 账号或密码错误
  if (/invalid username\/email or password/i.test(message)) return "账号或密码不正确，请核对后重试";
  // 积分/余额不足
  if (/insufficient (points|balance)|not enough (points|balance)|balance is not enough/i.test(message)) {
    return "积分余额不足，请先充值后再操作";
  }
  // 图搜/采集额度
  if (/budget|quota|rate limit|too many requests/i.test(message)) return "当日调用额度已用完，可于次日自动恢复后再试";
  // 请求超时
  if (/timeout|timed out|took too long/i.test(message)) return "请求超时，请稍后重试";
  // 网络异常
  if (/failed to fetch|network (error|request failed)|load failed|net::|offline/i.test(message)) {
    return "网络连接异常，请检查网络后重试";
  }
  // 上游/服务商接口失败
  if (/provider request failed|upstream (error|request failed)|bad gateway/i.test(message)) {
    return "服务商接口请求失败，请稍后重试";
  }
  // 权限不足
  if (/forbidden|permission denied|no permission|not authorized|insufficient permission/i.test(message)) {
    return "没有权限执行此操作，请确认账号权限后重试";
  }
  // 内容不存在
  if (/not found|does not exist/i.test(message)) return "请求的内容不存在或已被删除";
  // 服务器繁忙
  if (/internal server error|unexpected error|server error|service unavailable/i.test(message)) {
    return "服务器繁忙，请稍后重试";
  }
  // 核价图搜相关业务文案
  if (message.includes("no retained")) return "当前没有已保留的 SKC，无法创建货源图搜任务。";
  if (message.includes("select at least")) return "请先在图搜结果中选择至少一个候选货源后再完成入库。";
  // 已是中文（含中文）→ 原样返回
  if (/[\u4e00-\u9fa5]/.test(message)) return message;
  // 其余英文 → 通用中文兜底
  return "操作失败，请稍后重试";
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
    if (isSessionExpired(response, detail)) notifySessionExpired();
    throw new Error(toUserMessage(detail));
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
    if (isSessionExpired(response, detail)) notifySessionExpired();
    throw new Error(toUserMessage(detail || "请求失败"));
  }

  return response.blob();
}

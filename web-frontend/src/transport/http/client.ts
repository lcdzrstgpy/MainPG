type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

type RequestOptions = {
  method?: HttpMethod;
  body?: unknown;
  token?: string;
  /** 覆盖默认超时（毫秒）。用于外部慢接口（如 1688 图搜），默认 30s 不够时单独放宽。 */
  timeoutMs?: number;
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

/** 通知应用层登录状态已失效（登录超时 / 远程会话缺失 / 被顶替），用于自动返回登录页。 */
export function notifySessionExpired(reason?: string): void {
  window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT, { detail: reason || "" }));
}

export function isSessionExpired(response: Response, detail: string): boolean {
  // 认证入口自身的 401 是业务拒绝（密码错/验证码错/账号停用），不是会话过期，
  // 不能触发回登录页（否则登录页输错密码会被"踢"）。
  if (/invalid (username\/email or password)|invalid or expired (reset token|email code)|a valid 6-digit email code is required|user is not registered on the server|customer account is not active/i.test(detail)) {
    return false;
  }
  if (response.status === 401) return true;
  return /login session expired|remote customer session is missing|invalid bearer token|missing bearer token|session revoked/i.test(detail);
}

/**
 * 全局 fetch 拦截器：任意接口返回 401（登录会话失效/远程会话缺失）即统一派发
 * auth:session-expired，由 App 回到登录页。
 *
 * 用于兜住未走 httpJson/httpBlob 的裸 fetch 调用（如 product_processing、
 * profit_activity、DailySelectionPage、useChangePoller 等模块自带的 fetch），
 * 避免这些路径在会话过期时只显示"操作失败，请稍后重试"、让用户手动退出重登。
 */
const FETCH_INTERCEPTOR_KEY = "__wh_session_fetch_interceptor__";
const interceptorWindow = window as unknown as Record<string, unknown>;
if (!interceptorWindow[FETCH_INTERCEPTOR_KEY]) {
  interceptorWindow[FETCH_INTERCEPTOR_KEY] = true;
  const originalFetch = window.fetch.bind(window);
  // 认证入口的 401 是业务拒绝（密码/验证码错误），不应触发会话过期回登录页。
  const AUTH_ENTRY_PATH =
    /\/api\/customer\/(login|register|activate|email-code|password-reset|change-password|forgot-password)(\/|$)/i;
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const response = await originalFetch(input, init);
    if (response.status === 401) {
      const url = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url;
      if (!AUTH_ENTRY_PATH.test(url)) notifySessionExpired();
    }
    return response;
  };
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

  // ---- 注册 / 登录 / 邮箱验证码 ----
  // 后端这批 detail 全是英文，原先一条都不匹配，用户在注册页无论遇到哪种失败
  // 都只看到「操作失败，请稍后重试」——邀请码错、邮箱已注册、验证码过期完全分不清。
  if (/invitation code has been used up/i.test(message)) return "这个邀请码的名额已经用完了，请找对接人要一个新的";
  if (/invitation code has expired/i.test(message)) return "邀请码已过期，请找对接人要一个新的";
  if (/invitation code is invalid/i.test(message)) return "邀请码不正确，请核对后重新填写";
  if (/invitation code is required/i.test(message)) return "请填写邀请码";
  if (/please wait 60 seconds before requesting another email code/i.test(message)) {
    return "验证码刚发过，请 60 秒后再点一次";
  }
  if (/too many email code requests/i.test(message)) return "这个邮箱获取验证码太频繁了（1 小时最多 5 次），请过一会儿再试";
  if (/too many invalid email code attempts/i.test(message)) return "验证码错误次数太多，请重新获取一个新的验证码";
  if (/invalid or expired email code/i.test(message)) return "邮箱验证码不正确或已过期，请重新获取验证码";
  if (/a valid 6-digit email code is required/i.test(message)) return "请输入 6 位数字验证码";
  if (/verification email could not be sent|email verification service is not configured/i.test(message)) {
    return "验证码邮件发送失败，请稍后重试；一直失败请把提示发给我们";
  }
  if (/username or email already exists/i.test(message)) {
    return "这个用户名或邮箱已经注册过了，可以直接登录；忘记密码就在登录页点「忘记密码？」重置";
  }
  // ---- 登录与会话 ----
  if (/too many failed login attempts/i.test(message)) return "登录失败次数过多，请 15 分钟后再试";
  if (/session revoked/i.test(message)) return "你的账号已在其他设备登录，本机已退出";
  if (/login session expired|invalid bearer token|missing bearer token/i.test(message)) return "登录状态已失效，请重新登录";
  // ---- 修改用户名 ----
  if (/username is required/i.test(message)) return "请输入新的用户名";
  if (/username must be 3-32 characters/i.test(message)) return "用户名需要 3-32 个字符";
  if (/username contains unsupported characters/i.test(message)) return "用户名只能包含中英文、数字、下划线和连字符";
  if (/new username must be different/i.test(message)) return "新用户名不能与当前用户名相同";
  if (/username already taken/i.test(message)) return "这个用户名已被占用，换一个试试";
  if (/username can only be changed once every 30 days/i.test(message)) return "30 天内只能修改一次用户名，过段时间再来";
  if (/a verified email is required to change username/i.test(message)) return "当前账号没有绑定邮箱，无法修改用户名";
  if (/missing account/i.test(message)) return "账号信息缺失，请重新登录后再试";
  if (/password must be at least 6 characters/i.test(message)) return "密码至少 6 位，请重新设置";
  if (/a valid email is required/i.test(message)) return "邮箱格式不正确，请检查后重新填写";
  if (/account not found/i.test(message)) return "找不到这个账号，请确认用户名或邮箱有没有写错";
  if (/customer account is not active|user account is not active/i.test(message)) return "这个账号已被停用，请联系对接人";
  if (/user is not registered on the server/i.test(message)) return "这个账号还没有在服务器上注册，请先用邮箱注册";
  if (/invalid or expired reset token/i.test(message)) return "重置链接已失效，请回登录页重新获取验证码";

  // ---- 浏览器插件 ----
  if (/plugin session is offline|invalid plugin session|plugin session not found|missing plugin session/i.test(message)) {
    return "插件和工作台的连接断开了，请在插件面板点「连接插件」重连";
  }
  if (/at most two active capture batches/i.test(message)) return "插件采集同时最多跑 2 个批次，请等前面的批次结束后再试";
  if (/plugin queue is unavailable/i.test(message)) return "插件采集服务暂不可用，请稍后重试";

  // ---- 产品库 / 利润活动 ----
  if (/product_id_already_exists/i.test(message)) return "这个商品 ID 在产品库里已经有了，直接编辑那一条就行";
  if (/site_code_already_exists/i.test(message)) return "这个站点已经存在了";
  if (/site_code_invalid|site_code_path_mismatch/i.test(message)) return "站点信息对不上，请刷新页面后重试";
  if (/numeric value (is required|must be positive)|settings value must be numeric/i.test(message)) {
    return "数值填得不对：售价、成本、重量都要填大于或等于 0 的数字";
  }
  if (/only \.xlsx or \.xlsm is supported/i.test(message)) return "只支持 .xlsx 或 .xlsm 格式的表格，请换一个文件";
  if (/no eligible products to dispatch/i.test(message)) return "没有可提交的产品，请检查勾选和站点";
  if (/profit_activity_company_write_required/i.test(message)) return "当前账号没有修改公司数据的权限，请联系对接人";
  // 后端把缺失字段用逗号拼在一起返回（如 product_image_required,source_url_required），
  // 逐字翻译没意义，统一提示去补哪几列。
  if (/^[a-z_]+_required(,[a-z_]+_required)+$/.test(message)) {
    return "表格里还缺必填项，请把商品 ID、售价、成本、重量、商品主图和货源链接补齐后再导入";
  }

  // ---- POD 定制 ----
  if (/POD billing request was rejected/i.test(message)) return "计费服务拒绝了这次请求，请稍后重试；一直失败就把提示发给我们";
  if (/POD billing (permission|authentication) is required/i.test(message)) return "当前账号的 POD 计费权限有问题，请联系对接人";
  if (/POD billing service returned an invalid response/i.test(message)) return "计费服务返回异常，请稍后重试";
  if (/POD template upload is too large/i.test(message)) return "模板图片太大了，请换一张小一点的再传";
  if (/POD scene optimization is not available|POD single-image regeneration is not available/i.test(message)) {
    return "这个版本还不支持该功能";
  }

  // ---- 版本更新（后端这些错误原本原样英文展示在弹窗里）----
  if (/SHA-256 (does not match|mismatch)|size mismatch/i.test(message)) {
    return "更新包校验没通过，通常是下载不完整，请点「重新更新」再试一次";
  }
  if (/manifest/i.test(message)) return "更新信息校验失败，请稍后重试；一直失败请把提示发给我们";
  if (/No verified (update|patch) is available to install/i.test(message)) return "没有可安装的更新包，请重新检查更新";
  if (/MainPG-Updater\.exe is missing/i.test(message)) return "更新程序缺失，请重新安装完整版";
  if (/Automatic updates are only available on Windows/i.test(message)) return "当前系统不支持自动更新，请手动下载安装包";
  if (/download returned non-binary data/i.test(message)) return "更新包下载异常，请稍后重试";
  if (/Cross-origin update actions are not allowed/i.test(message)) return "更新请求来源不被允许，请从工作台里点更新";

  // ---- 其它 ----
  if (/preview finalization exceeded the time budget/i.test(message)) return "图片发布超时了，可以点「仅重试失败图片」再试一次";
  if (/unsupported miaoshou template kind/i.test(message)) return "妙手导出的模板类型不对，请选择「服饰类」或「非服饰类」";
  if (/\bis not configured\b/i.test(message)) return "相关服务还没配置好，请联系对接人处理";
  if (/provider is unavailable/i.test(message)) return "上游服务暂时不可用，请稍后重试";

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
  // 数据校验/结构不一致（Pydantic schema 错误，如 extra_forbidden / 字段缺失）。
  // 必须放在权限判断之前：否则报错文本里的 "extra_forbidden" 会被 /forbidden/ 误判成“没有权限”。
  if (/validation error|\[type=|extra_forbidden|extra inputs are not permitted|field required|input_value=/i.test(message)) {
    return "服务端返回的数据结构与当前版本不一致，请刷新页面重试或联系管理员确认表结构";
  }
  // 权限不足（用词边界 \bforbidden\b，避免误匹配 Pydantic 的 extra_forbidden）
  if (/\bforbidden\b|permission denied|no permission|not authorized|insufficient permission|permission required/i.test(message)) {
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
  // 其它「xxx is required」类必填错误：给一句人话，别落通用兜底
  if (/\bis required\b/i.test(message)) return "必填信息没填完整，请检查后再试";
  // 已是中文（含中文）→ 原样返回
  if (/[\u4e00-\u9fa5]/.test(message)) return message;
  // 其余英文 → 通用中文兜底
  return "操作失败，请稍后重试";
}

const REQUEST_TIMEOUT_MS = 30_000;

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("请求超时，请稍后重试");
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function httpJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = authToken(options.token);
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetchWithTimeout(
    `${apiBaseUrl()}${path}`,
    {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    },
    options.timeoutMs,
  );

  const contentType = response.headers.get("content-type") ?? "";
  let payload: any = {};
  if (contentType.includes("application/json")) {
    payload = await response.json().catch(() => ({}));
  } else if (!response.ok) {
    // 非 JSON 错误响应（网关 200 html / 拦截页等）：把文本作为 detail 透出，
    // 避免静默解析成空对象把真实失败吞掉。
    payload = { detail: (await response.text().catch(() => "")) || `请求失败 (HTTP ${response.status})` };
  }

  if (!response.ok) {
    const detail = detailFromPayload(payload, response.status);
    if (isSessionExpired(response, detail)) notifySessionExpired(detail);
    throw new Error(toUserMessage(detail));
  }

  return payload as T;
}

export async function httpBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = authToken(options.token);
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetchWithTimeout(
    `${apiBaseUrl()}${path}`,
    {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    },
    options.timeoutMs,
  );

  if (!response.ok) {
    const detail = await response.text().catch(() => "请求失败");
    if (isSessionExpired(response, detail)) notifySessionExpired(detail);
    throw new Error(toUserMessage(detail || "请求失败"));
  }

  return response.blob();
}

/**
 * 从错误响应体里取出给用户看的文本。
 *
 * 后端有两种写法：字符串 detail，以及 `{"code": "...", "message": "中文提示"}` 这种对象
 * detail（采集服务不可用、候选不可确认入库等）。以前只认字符串，对象会被丢掉，用户看到
 * 的是「请求失败 (HTTP 503)」——后端写好的那句中文反而看不到。
 */
function detailFromPayload(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return `请求失败 (HTTP ${status})`;
}

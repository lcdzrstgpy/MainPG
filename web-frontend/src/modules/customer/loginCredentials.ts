import { httpJson } from "../../transport/http/client";
import {
  sanitizeSavedAccounts,
  type SavedLoginAccount,
  type SavedLoginListPayload,
} from "./loginCredentialsModel";

type ListPayload = SavedLoginListPayload;
type RevealPayload = { ok?: boolean; password?: unknown };

const LOGIN_CREDENTIALS_BASE = "/api/login-credentials";

/** 拉取已保存账号列表。本地服务不可用 / 旧版本无此接口时静默返回空列表。 */
export async function fetchSavedLogins(): Promise<SavedLoginAccount[]> {
  try {
    const data = await httpJson<ListPayload>(LOGIN_CREDENTIALS_BASE, { token: "" });
    if (data.ok === false) return [];
    return sanitizeSavedAccounts(data);
  } catch {
    return [];
  }
}

/** 登录成功后保存账号（remember 为 true 时连同密码）。失败静默，不影响登录。 */
export async function saveLogin(
  identifier: string,
  password: string,
  remember: boolean,
): Promise<void> {
  try {
    await httpJson(LOGIN_CREDENTIALS_BASE, {
      method: "POST",
      body: { identifier, password: remember ? password : null, remember },
      token: "",
    });
  } catch {
    // 静默：账号记忆是便利功能，失败不允许影响登录本身。
  }
}

/** 取回某账号已保存的密码（选中账号时按需拉取，不随列表下发）。 */
export async function revealSavedPassword(identifier: string): Promise<string | null> {
  try {
    const data = await httpJson<RevealPayload>(`${LOGIN_CREDENTIALS_BASE}/reveal`, {
      method: "POST",
      body: { identifier },
      token: "",
    });
    return typeof data.password === "string" && data.password ? data.password : null;
  } catch {
    return null;
  }
}

/** 清除某账号已保存的密码（重置密码成功后调用），账号本身保留。 */
export async function clearSavedPassword(identifier: string): Promise<void> {
  try {
    await httpJson(`${LOGIN_CREDENTIALS_BASE}/clear-password`, {
      method: "POST",
      body: { identifier },
      token: "",
    });
  } catch {
    // 静默。
  }
}

/** 移除某个已保存账号。 */
export async function deleteSavedLogin(identifier: string): Promise<void> {
  try {
    await httpJson(`${LOGIN_CREDENTIALS_BASE}/${encodeURIComponent(identifier)}`, {
      method: "DELETE",
      token: "",
    });
  } catch {
    // 静默。
  }
}

/**
 * 登录页「账号管理」的纯数据模型（不依赖浏览器环境，便于 node 测试）。
 * 网络访问部分见 loginCredentials.ts。
 */

/** 本机保存的登录账号（密码仅由本地服务保管，列表接口不下发明文）。 */
export type SavedLoginAccount = {
  identifier: string;
  has_password: boolean;
  updated_at?: string;
};

export type SavedLoginListPayload = { ok?: boolean; accounts?: unknown };

/** 过滤后端返回中的脏数据（防御旧版本 / 异常数据）。 */
export function sanitizeSavedAccounts(payload: SavedLoginListPayload): SavedLoginAccount[] {
  const raw = Array.isArray(payload.accounts) ? payload.accounts : [];
  const accounts: SavedLoginAccount[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const identifier =
      typeof record.identifier === "string" ? record.identifier.trim() : "";
    if (!identifier) continue;
    accounts.push({
      identifier,
      has_password: record.has_password === true,
      updated_at: typeof record.updated_at === "string" ? record.updated_at : undefined,
    });
  }
  return accounts;
}

/** 在已保存账号中查找与输入值精确匹配的账号（用于回显「记住密码」状态）。 */
export function matchSavedAccount(
  accounts: SavedLoginAccount[],
  identifier: string,
): SavedLoginAccount | null {
  const target = identifier.trim();
  if (!target) return null;
  return accounts.find((account) => account.identifier === target) ?? null;
}

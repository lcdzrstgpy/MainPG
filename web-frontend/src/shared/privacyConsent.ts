/**
 * 隐私政策「已同意」记录（登录页勾选框用）。
 *
 * 合规前提：勾选框**首次必须由用户主动勾选**，不得默认勾选同意
 * （TC260《网络安全标准实践指南》与小米《APP 常见隐私安全问题与自查整改指引》
 * 均明确要求「不应设置为默认勾选/同意」，北京互联网法院亦有自动勾选判违法的案例）。
 * 本模块只负责「用户主动勾过之后不再重复要求」的记忆，并在**协议版本变更时自动失效**，
 * 让用户对新版本重新确认；取消勾选即撤回记录（合规要求提供便捷的撤回方式）。
 *
 * 纯逻辑 + 惰性访问 localStorage（顶层不碰 window），便于 node 环境直接测试。
 */

/**
 * 隐私政策版本的**兜底值**。
 *
 * 版本权威已经移到服务端（`/privacy.json` 的 `version`），这里只在「拉不到远端」时
 * 使用：断网、服务端未上线、文档未发布。它必须与内置的
 * `web-frontend/public/privacy.html` 里「版本：vX.Y」保持一致 ——
 * 由 `privacyConsent.test.ts` 读取文档比对，改了文档忘改这里会测试失败。
 */
export const PRIVACY_POLICY_VERSION_FALLBACK = "v1.2";

/** 兼容旧引用（测试与既有调用）；语义同 `PRIVACY_POLICY_VERSION_FALLBACK`。 */
export const PRIVACY_POLICY_VERSION = PRIVACY_POLICY_VERSION_FALLBACK;

/** 服务端版本号格式：`v1`、`v1.2`、`v1.2.3`…；不符合就按兜底处理。 */
const POLICY_VERSION_PATTERN = /^v\d+(\.\d+)*$/;

/**
 * 解析「本次应当校验的隐私政策版本」。
 *
 * 远端文档不可用/字段脏/未发布 → 回退兜底常量，**不改变登录流程**。
 * 注意：版本回滚（v1.3 → v1.2）也会让已同意的用户重新勾选，这是刻意保守的行为。
 */
export function resolvePolicyVersion(remote?: { version?: unknown } | null): string {
  const raw = typeof remote?.version === "string" ? remote.version.trim() : "";
  return POLICY_VERSION_PATTERN.test(raw) ? raw : PRIVACY_POLICY_VERSION_FALLBACK;
}

const STORAGE_KEY = "mainpg.privacyAgreed";

type PrivacyConsentRecord = { version?: unknown; at?: unknown };

function storage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    // 隐私模式 / 存储被禁用：按「未同意」处理，不影响本次登录本身。
    return null;
  }
}

/** 记录里的版本是否与当前政策版本一致。脏数据、缺字段、版本不符一律视为未同意。 */
export function isConsentRecordValid(
  record: unknown,
  policyVersion: string = PRIVACY_POLICY_VERSION,
): boolean {
  if (!record || typeof record !== "object") return false;
  const version = (record as PrivacyConsentRecord).version;
  return typeof version === "string" && version === policyVersion;
}

/** 是否已同意当前版本的隐私政策。 */
export function readPrivacyConsent(policyVersion: string = PRIVACY_POLICY_VERSION): boolean {
  const store = storage();
  if (!store) return false;
  const raw = store.getItem(STORAGE_KEY);
  if (!raw) return false;
  try {
    return isConsentRecordValid(JSON.parse(raw), policyVersion);
  } catch {
    return false;
  }
}

/** 记录「已同意」（勾选框被主动勾上时调用）。 */
export function savePrivacyConsent(
  at: string = new Date().toISOString(),
  policyVersion: string = PRIVACY_POLICY_VERSION,
): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(STORAGE_KEY, JSON.stringify({ version: policyVersion, at }));
  } catch {
    // 静默：记录失败不影响登录流程，只是下次仍需重新勾选。
  }
}

/** 撤回同意记录（取消勾选时调用）。 */
export function clearPrivacyConsent(): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(STORAGE_KEY);
  } catch {
    // 静默。
  }
}

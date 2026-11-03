import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  PRIVACY_POLICY_VERSION,
  PRIVACY_POLICY_VERSION_FALLBACK,
  clearPrivacyConsent,
  isConsentRecordValid,
  readPrivacyConsent,
  resolvePolicyVersion,
  savePrivacyConsent,
} from "./privacyConsent.ts";

/** 造一个最小 localStorage，并把 window 挂到 globalThis 上（模块内部惰性读取）。 */
function withFakeStorage(run: (dump: () => Record<string, string>) => void) {
  const map = new Map<string, string>();
  const fake = {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
  };
  const globalRef = globalThis as unknown as { window?: unknown };
  const original = globalRef.window;
  globalRef.window = { localStorage: fake };
  try {
    run(() => Object.fromEntries(map));
  } finally {
    if (original === undefined) delete globalRef.window;
    else globalRef.window = original;
  }
}

test("isConsentRecordValid 只认「版本完全一致」的对象", () => {
  assert.equal(isConsentRecordValid({ version: "v1.1" }, "v1.1"), true);
  // 版本不同 / 缺字段 / 类型不对 / 脏数据 → 一律未同意（不能放宽，否则改版后不会重新征求）
  assert.equal(isConsentRecordValid({ version: "v1.0" }, "v1.1"), false);
  assert.equal(isConsentRecordValid({ at: "2026-01-01" }, "v1.1"), false);
  assert.equal(isConsentRecordValid({ version: 1.1 }, "v1.1"), false);
  assert.equal(isConsentRecordValid({}, "v1.1"), false);
  assert.equal(isConsentRecordValid(null, "v1.1"), false);
  assert.equal(isConsentRecordValid("v1.1", "v1.1"), false);
});

test("未勾选过时读取为未同意；勾选后写入并回读为已同意", () => {
  withFakeStorage((dump) => {
    assert.equal(readPrivacyConsent(), false);
    savePrivacyConsent("2026-10-14T03:00:00.000Z");
    assert.equal(readPrivacyConsent(), true);
    const stored = JSON.parse(dump()["mainpg.privacyAgreed"]);
    assert.equal(stored.version, PRIVACY_POLICY_VERSION);
    assert.equal(stored.at, "2026-10-14T03:00:00.000Z");
  });
});

test("取消勾选（撤回同意）会清掉记录", () => {
  withFakeStorage((dump) => {
    savePrivacyConsent("2026-10-14T03:00:00.000Z");
    assert.equal(readPrivacyConsent(), true);
    clearPrivacyConsent();
    assert.equal(readPrivacyConsent(), false);
    assert.deepEqual(dump(), {});
  });
});

test("政策版本升级后旧记录自动失效（要求重新同意）", () => {
  withFakeStorage(() => {
    savePrivacyConsent("2026-10-14T03:00:00.000Z", "v1.1");
    assert.equal(readPrivacyConsent("v1.1"), true);
    assert.equal(readPrivacyConsent("v1.2"), false);
  });
});

test("存储里是脏数据时不抛异常，按未同意处理", () => {
  withFakeStorage(() => {
    const store = (globalThis as unknown as { window: { localStorage: Storage } }).window.localStorage;
    store.setItem("mainpg.privacyAgreed", "{ 不是 JSON");
    assert.equal(readPrivacyConsent(), false);
    store.setItem("mainpg.privacyAgreed", "null");
    assert.equal(readPrivacyConsent(), false);
  });
});

test("隐私政策文档里的版本号与本模块常量一致（单一事实源由测试守住）", () => {
  const html = readFileSync(new URL("../../public/privacy.html", import.meta.url), "utf8");
  const matched = /版本：\s*(v[0-9][0-9.]*)/.exec(html);
  assert.ok(matched, "public/privacy.html 里应能找到「版本：vX.Y」");
  assert.equal(
    matched[1],
    PRIVACY_POLICY_VERSION,
    "public/privacy.html 的版本号已更新，请同步 shared/privacyConsent.ts 的 PRIVACY_POLICY_VERSION",
  );
});


test("resolvePolicyVersion 只接受 v<数字> 形式，其余一律回退兜底常量", () => {
  assert.equal(resolvePolicyVersion({ version: "v1.3" }), "v1.3");
  assert.equal(resolvePolicyVersion({ version: "  v2  " }), "v2");
  assert.equal(resolvePolicyVersion({ version: "v1.2.3" }), "v1.2.3");
  // 脏数据 / 缺字段 / 类型不对 / 未发布 → 回退，不改变登录流程
  assert.equal(resolvePolicyVersion({ version: "abc" }), PRIVACY_POLICY_VERSION_FALLBACK);
  assert.equal(resolvePolicyVersion({ version: "1.2" }), PRIVACY_POLICY_VERSION_FALLBACK);
  assert.equal(resolvePolicyVersion({ version: 1.2 }), PRIVACY_POLICY_VERSION_FALLBACK);
  assert.equal(resolvePolicyVersion({ version: "" }), PRIVACY_POLICY_VERSION_FALLBACK);
  assert.equal(resolvePolicyVersion({}), PRIVACY_POLICY_VERSION_FALLBACK);
  assert.equal(resolvePolicyVersion(null), PRIVACY_POLICY_VERSION_FALLBACK);
  assert.equal(resolvePolicyVersion(undefined), PRIVACY_POLICY_VERSION_FALLBACK);
});

test("远端版本升版后，旧记录不再视为已同意", () => {
  withFakeStorage(() => {
    savePrivacyConsent("2026-10-14T03:00:00.000Z", "v1.2");
    assert.equal(readPrivacyConsent("v1.2"), true);
    assert.equal(readPrivacyConsent(resolvePolicyVersion({ version: "v1.3" })), false);
    // 远端拉不到时仍按兜底常量判定，不会把已同意的用户顶回去
    assert.equal(readPrivacyConsent(resolvePolicyVersion(null)), true);
  });
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  matchSavedAccount,
  sanitizeSavedAccounts,
  type SavedLoginAccount,
} from "./loginCredentialsModel.ts";

test("sanitizeSavedAccounts 过滤脏数据并保留合法账号", () => {
  const accounts = sanitizeSavedAccounts({
    ok: true,
    accounts: [
      {
        identifier: "alex@example.com",
        has_password: true,
        updated_at: "2026-10-13T07:00:00+00:00",
      },
      { identifier: "  ops-team-a  ", has_password: false },
      { identifier: "", has_password: true },
      { identifier: 42, has_password: true },
      null,
      "junk",
      { has_password: true },
    ],
  });

  assert.deepEqual(accounts, [
    {
      identifier: "alex@example.com",
      has_password: true,
      updated_at: "2026-10-13T07:00:00+00:00",
    },
    { identifier: "ops-team-a", has_password: false, updated_at: undefined },
  ]);
});

test("sanitizeSavedAccounts 对非数组 / 缺失字段返回空列表", () => {
  assert.deepEqual(sanitizeSavedAccounts({}), []);
  assert.deepEqual(sanitizeSavedAccounts({ accounts: null }), []);
  assert.deepEqual(sanitizeSavedAccounts({ accounts: "junk" }), []);
});

test("has_password 只认布尔 true（阻止字符串/数字谎报）", () => {
  const accounts = sanitizeSavedAccounts({
    accounts: [
      { identifier: "a@b.c", has_password: "true" },
      { identifier: "d@e.f", has_password: 1 },
      { identifier: "g@h.i", has_password: true },
    ],
  });

  assert.deepEqual(
    accounts.map((account) => account.has_password),
    [false, false, true],
  );
});

test("matchSavedAccount 精确匹配（忽略首尾空白），未命中返回 null", () => {
  const accounts: SavedLoginAccount[] = [
    { identifier: "alex@example.com", has_password: true },
    { identifier: "ops-team-a", has_password: false },
  ];

  assert.equal(
    matchSavedAccount(accounts, " alex@example.com ")?.identifier,
    "alex@example.com",
  );
  assert.equal(matchSavedAccount(accounts, "ops-team-a")?.has_password, false);
  // 前缀 / 部分输入不算命中（避免输入过程中的抖动回显）。
  assert.equal(matchSavedAccount(accounts, "alex@example.co"), null);
  assert.equal(matchSavedAccount(accounts, ""), null);
  assert.equal(matchSavedAccount(accounts, "   "), null);
});

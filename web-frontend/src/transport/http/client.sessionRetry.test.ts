import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const client = readFileSync(new URL("./client.ts", import.meta.url), "utf8");
const profitApi = readFileSync(
  new URL("../../modules/profit_activity/api/profitActivityApi.ts", import.meta.url),
  "utf8",
);

test("多候选令牌重试的请求不被全局拦截器就地判定会话失效", () => {
  assert.match(client, /export const AUTH_RETRY_MANAGED/);
  assert.match(client, /response\.status === 401 && !retryManaged/);
});

test("会话失效关键词判定拆分为可复用函数", () => {
  assert.match(client, /export function isSessionExpiredDetail/);
  assert.match(client, /return response\.status === 401 && isSessionExpiredDetail\(detail\)/);
});

test("profit_activity 的请求全部走受管 fetch 并只认登录会话令牌的失效", () => {
  assert.match(profitApi, /\[AUTH_RETRY_MANAGED\]: true/);
  assert.match(profitApi, /token === getAuthToken\(\)/);
  // 只允许 authedFetch 内部那一处裸 fetch，调用方必须全部走受管 fetch
  const bareFetches = profitApi
    .split("\n")
    .filter((line) => /await fetch\(/.test(line) && !/AUTH_RETRY_MANAGED/.test(line));
  assert.equal(
    bareFetches.length,
    0,
    `调用方不应再有裸 fetch（会触发拦截器误踢），发现：${bareFetches.join(" | ")}`,
  );
});

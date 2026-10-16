import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const client = readFileSync(new URL("./client.ts", import.meta.url), "utf8");

// 真实案例背景：产品处理「继续处理」在计费服务侧鉴权失败（令牌失效）时，
// 原先只显示通用兜底「操作失败，请稍后重试」，用户与客服都无从判断；
// 实测「退出登录 → 重新登录 → 继续处理」可恢复。这组断言锁定各分支的映射。
test("计费授权类错误映射为可操作中文提示，不再落通用兜底", () => {
  // 远端令牌被拒（被顶替/失效）：重登可恢复
  assert.match(
    client,
    /remote billing session was rejected[\s\S]{0,300}请退出后重新登录再试/,
  );
  // 本地会话缺少远端令牌（本地服务重启会出现）：重登可恢复
  assert.match(
    client,
    /server billing session is unavailable[\s\S]{0,300}请退出后重新登录再试/,
  );
  // 远端计费服务不可用 / 返回异常 / 业务拒绝：稍后重试
  assert.match(
    client,
    /remote billing service is unavailable[\s\S]{0,300}计费服务暂时不可用/,
  );
  assert.match(
    client,
    /remote billing service returned an invalid[\s\S]{0,300}计费服务返回异常/,
  );
  assert.match(
    client,
    /remote billing request was rejected[\s\S]{0,300}计费服务拒绝了这次请求/,
  );
});

test("计费授权映射必须位于「服务器繁忙」通用兜底之前，避免再次被吞", () => {
  const billingAt = client.indexOf("remote billing session was rejected");
  const busyFallbackAt = client.indexOf("internal server error");
  assert.ok(billingAt > 0, "计费授权映射规则缺失");
  assert.ok(
    busyFallbackAt > billingAt,
    "计费授权映射被移到通用兜底之后会再次吞掉真实原因",
  );
});

test("高频英文错误继续覆盖（素材过大/模型不支持聊天）", () => {
  assert.match(client, /uploaded material is too large[\s\S]{0,200}上传的文件太大了/);
  assert.match(client, /selected model does not support chat[\s\S]{0,200}不支持对话/);
});

test("低频黑洞也覆盖（素材不可用/数据格式/素材授权）", () => {
  assert.match(client, /asset is unavailable[\s\S]{0,200}素材暂时不可用/);
  assert.match(client, /title must be a string[\s\S]{0,200}数据格式不对/);
  assert.match(client, /missing asset token[\s\S]{0,200}素材授权已失效/);
});

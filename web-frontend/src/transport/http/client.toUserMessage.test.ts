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

test("后端已经用中文说清原因时，不再被 not found 之类的英文关键词覆盖", () => {
  // 真实案例：Temu 层把上游原话包成
  // 「Temu 拒绝了这次提交：The requested content does not exist or has been deleted（错误码 2000000）」，
  // 结果被下面那条 /not found|does not exist/ 命中，整段换成「请求的内容不存在或已被删除」——
  // 用户既看不到上游原话也看不到错误码，根本无从下手。
  const idxGuard = client.indexOf(
    "/[\\u4e00-\\u9fa5]/.test(message) && /not found|does not exist/i.test(message)",
  );
  const idxGeneric = client.indexOf(
    'if (/not found|does not exist/i.test(message)) return "请求的内容不存在或已被删除";',
  );
  assert.ok(idxGuard > -1, "应有「中文原因优先」的守卫");
  assert.ok(idxGeneric > -1, "通用 not found 映射仍在");
  assert.ok(idxGuard < idxGeneric, "中文守卫必须排在通用映射之前");
});

test("httpBlob 404 要把请求路径带出来", () => {
  // 「请求的内容不存在或已被删除」在草稿图/素材/商品/路由写错四种情况下文案一模一样，
  // 不带上 path 就等于没给排查线索。
  const start = client.indexOf("export async function httpBlob");
  assert.ok(start > -1);
  const body = client.slice(start, client.indexOf("\n}", start));
  assert.match(body, /response\.status === 404 \? `（\$\{path\}）`/);
});

test("本地媒体地址读图前剥掉过期签名，改用登录态鉴权", () => {
  // 后端 preview_url() 签出来的地址只活 ~2 小时。草稿库/发布页把地址存进表单，
  // 过几小时再读必然 404 —— 文件还在，纯签名过期。httpBlob 自带 Bearer，
  // 所以剥掉 signature/expires 走后端「无签名 + 已登录」那条分支。
  assert.match(client, /function stripMediaSignature/);
  assert.match(client, /url\.searchParams\.delete\("signature"\)/);
  assert.match(client, /url\.searchParams\.delete\("expires"\)/);
  // 必须真的用上，否则等于写了个死函数
  assert.match(client, /stripMediaSignature\(path\)/);
  // workspace_id 不能一起删：图片是按工作区目录存的
  assert.doesNotMatch(
    client.slice(client.indexOf("function stripMediaSignature"), client.indexOf("export async function httpBlob")),
    /searchParams\.delete\("workspace_id"\)/,
  );
});

test("缩略图不能把 localhost 地址当公网直接丢给 img", () => {
  // <img> 带不了 Authorization，而签名两小时就过期 → 缩略图永远加载不出来
  const strip = readFileSync(
    new URL("../../modules/temu_publish/components/TemuImageStrip.tsx", import.meta.url),
    "utf8",
  );
  assert.match(strip, /127\\\.0\\\.0\\\.1\|localhost/);
});

test("httpBlob 遇到绝对地址不要再拼一次 apiBaseUrl", () => {
  // 缩略图会把 `http://127.0.0.1:8010/api/...` 这种指回本地后端的完整地址交给 httpBlob，
  // 再拼一次 base 就变成 `http://127.0.0.1:8010http://127.0.0.1:8010/api/...`。
  const start = client.indexOf("export async function httpBlob");
  const body = client.slice(start, client.indexOf("\n}", start));
  assert.match(body, /\^https\?:\\\/\\\/\$?\/i\.test\(target\) \? target : `\$\{apiBaseUrl\(\)\}\$\{target\}`/);
});

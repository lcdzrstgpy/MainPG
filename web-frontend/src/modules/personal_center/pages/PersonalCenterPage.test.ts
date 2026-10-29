import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PersonalCenterPage.tsx", import.meta.url), "utf8");

test("consumption history renders server timestamps in the user's local timezone", () => {
  // 服务端流水时间是 UTC，必须经 Date 转成本地时区再格式化，
  // 而不是把原始 ISO 字符串直接截断显示（那样会显示成 UTC）。
  assert.match(source, /function formatUsageTime\(/);
  assert.match(source, /const date = new Date\(iso\);/);
  assert.match(source, /date\.getFullYear\(\)/);
  assert.doesNotMatch(source, /created_at\.replace\("T", " "\)\.slice\(0, 19\)/);
});

test("consumption history distinguishes POD batches from product processing", () => {
  assert.match(source, /pod_customization\.batch/);
  assert.match(source, /POD 定制/);
});

test("fission consumption uses its own POD service label", () => {
  assert.match(source, /pod_fission_v1: "POD 爆款裂变"/);
  const contract = readFileSync(new URL("../api/personalCenterApi.ts", import.meta.url), "utf8");
  assert.match(contract, /"pod_fission_v1"/);
});

test("pricing panel shows server-provided POD module prices and hides the block when absent", () => {
  // 单价必须来自服务端 pod_modules，客户端只做展示与兜底命名，不写死价格。
  assert.match(source, /pricingPodModules/);
  assert.match(source, /pricing\?\.pod_modules/);
  assert.match(source, /podPricingModules\.length > 0/);
  assert.match(source, /POD 按模块计价/);
  assert.match(source, /pod_semi_v1: "POD 半定制"/);
  // 复刻统一画像后由服务端追加，客户端不写死价格、只补展示名兜底。
  assert.match(source, /pod_replica_v1: "POD 爆款复刻"/);
  assert.doesNotMatch(source, /POD 全定制[^\n]*4[05]\s*积分/);
  const contract = readFileSync(new URL("../api/personalCenterApi.ts", import.meta.url), "utf8");
  assert.match(contract, /pod_modules\?: BillingPricingModule\[\]/);
  assert.match(contract, /unit_label\?: string/);
});

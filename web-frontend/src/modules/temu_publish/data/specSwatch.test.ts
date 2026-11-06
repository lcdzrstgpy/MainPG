import assert from "node:assert/strict";
import test from "node:test";

import { groupSpecColors, specColorGroup } from "./specSwatch.ts";

test("色系识别：中文/英文色名都能归组", () => {
  assert.equal(specColorGroup("黑色")?.key, "black");
  assert.equal(specColorGroup("Black")?.key, "black");
  assert.equal(specColorGroup("象牙白")?.key, "white");
  assert.equal(specColorGroup("浅绿色")?.key, "green");
  assert.equal(specColorGroup("酒红")?.key, "red");
  assert.equal(specColorGroup("卡其色")?.key, "brown");
  assert.equal(specColorGroup("多色")?.key, "multi");
});

test("色系识别：认不出来返回 null，不猜", () => {
  assert.equal(specColorGroup("均码"), null);
  assert.equal(specColorGroup(""), null);
});

test("分组：按色系聚合并保持定义顺序，认不出的进「其它」", () => {
  const groups = groupSpecColors(["浅绿色", "黑色", "S", "黑色 "]);
  assert.deepEqual(
    groups.map((item) => item.label),
    ["黑", "绿", "其它"],
  );
  assert.deepEqual(groups[0].values, ["黑色", "黑色 "]);
  assert.deepEqual(groups[1].values, ["浅绿色"]);
  assert.deepEqual(groups[2].values, ["S"]);
});

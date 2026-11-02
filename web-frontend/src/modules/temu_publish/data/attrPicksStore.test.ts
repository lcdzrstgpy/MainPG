import assert from "node:assert/strict";
import test from "node:test";

// node 里没有 window/localStorage：用内存实现顶上，测的是存储语义而不是浏览器
const memory = new Map<string, string>();
(globalThis as unknown as { window: unknown }).window = {
  localStorage: {
    getItem: (key: string) => memory.get(key) ?? null,
    setItem: (key: string, value: string) => memory.set(key, value),
    removeItem: (key: string) => memory.delete(key),
  },
};

const store = await import("./attrPicksStore.ts");
const {
  ATTR_PICKS_HISTORY_MAX,
  ATTR_PICKS_KEY,
  ATTR_PICKS_KEY_V1,
  DRAFT_ATTR_PREFILL_MAX,
  DRAFT_ATTR_PREFILL_KEY,
  readAttrPicksHistory,
  readAttrPicksHistoryAt,
  readCurrentAttrPicks,
  readDraftAttrPrefills,
  saveAttrPicksHistory,
  saveCurrentAttrPicks,
  saveDraftAttrPrefills,
  storageScope,
} = store;

test.beforeEach(() => memory.clear());

test("账号作用域：没有登录信息时落到 local", () => {
  assert.equal(storageScope(), "local");
});

test("按叶子类目分开存：切类目不会把上一个类目的填写冲掉", () => {
  saveAttrPicksHistory("u1", 1001, { 11: { vid: 1, text: "" } });
  saveAttrPicksHistory("u1", 1002, { 22: { vid: 2, text: "" } });

  assert.equal(readAttrPicksHistory("u1", 1001)[11]?.vid, 1);
  assert.equal(readAttrPicksHistory("u1", 1002)[22]?.vid, 2);
  // 换个账号读不到别人的
  assert.deepEqual(readAttrPicksHistory("u2", 1001), {});
});

test("旧版单条存档仍能读出来（升级那一下不能把用户刚填的清空）", () => {
  memory.set(
    `${ATTR_PICKS_KEY_V1}:u1`,
    JSON.stringify({ catId: 777, picks: { 33: { vid: 3, text: "" } } }),
  );
  assert.equal(readAttrPicksHistory("u1", 777)[33]?.vid, 3);
  // 类目对不上就不能用
  assert.deepEqual(readAttrPicksHistory("u1", 888), {});
});

test("新版没有记录时回落到旧版；新版有记录时以新版为准", () => {
  memory.set(
    `${ATTR_PICKS_KEY_V1}:u1`,
    JSON.stringify({ catId: 777, picks: { 33: { vid: 3, text: "" } } }),
  );
  saveAttrPicksHistory("u1", 777, { 44: { vid: 4, text: "" } });

  const got = readAttrPicksHistory("u1", 777);
  assert.equal(got[44]?.vid, 4, "新版存在就用新版");
  assert.equal(got[33], undefined, "不能把新旧合并，会串出已经不存在的 pid");
});

test("类目历史按时间裁剪，不无限增长", () => {
  for (let index = 0; index < ATTR_PICKS_HISTORY_MAX + 5; index += 1) {
    saveAttrPicksHistory("u1", 5000 + index, { 1: { vid: index + 1, text: "" } });
  }
  const raw = JSON.parse(memory.get(`${ATTR_PICKS_KEY}:u1`) ?? "{}") as Record<string, unknown>;
  assert.equal(Object.keys(raw).length, ATTR_PICKS_HISTORY_MAX);
  // 最早写的被裁掉、最新的必须留着（按写入顺序裁，不按同一毫秒会撞的 at）
  assert.equal(readAttrPicksHistory("u1", 5000)[1], undefined);
  assert.equal(
    readAttrPicksHistory("u1", 5000 + ATTR_PICKS_HISTORY_MAX + 4)[1]?.vid,
    ATTR_PICKS_HISTORY_MAX + 5,
  );
});

test("草稿预填按条合并：分页加载时不能把别的页算好的冲掉", () => {
  saveDraftAttrPrefills("u1", { "ai:1:a": { catId: 1, picks: { 1: { vid: 1, text: "" } }, at: 1 } });
  saveDraftAttrPrefills("u1", { "ai:1:b": { catId: 2, picks: { 2: { vid: 2, text: "" } }, at: 2 } });

  const all = readDraftAttrPrefills("u1");
  assert.equal(all["ai:1:a"]?.catId, 1);
  assert.equal(all["ai:1:b"]?.catId, 2);
});

test("草稿预填按时间裁剪，且新写入的不会被裁掉", () => {
  const batch: Record<string, { catId: number; picks: Record<number, { vid: number; text: string }>; at: number }> = {};
  for (let index = 0; index < DRAFT_ATTR_PREFILL_MAX + 10; index += 1) {
    batch[`ai:${index}`] = { catId: 1, picks: {}, at: index };
  }
  saveDraftAttrPrefills("u1", batch);
  saveDraftAttrPrefills("u1", { latest: { catId: 9, picks: {}, at: Date.now() } });

  const all = readDraftAttrPrefills("u1");
  assert.equal(Object.keys(all).length, DRAFT_ATTR_PREFILL_MAX);
  assert.equal(all.latest?.catId, 9, "刚写进去的必须留下");
  assert.equal(all["ai:0"], undefined, "最早写的应被裁掉");
});

test("刷新恢复用「当前这一条」：按类目隔离，类目对不上就丢弃", () => {
  saveCurrentAttrPicks("u1", 900, { 1: { vid: 5, text: "" } });
  assert.equal(readCurrentAttrPicks("u1", 900)[1]?.vid, 5);
  // 换了类目不能把上一个类目的选择搬过来（pid 完全不同）
  assert.deepEqual(readCurrentAttrPicks("u1", 901), {});
  assert.deepEqual(readCurrentAttrPicks("u2", 900), {});
});

test("当前选择与同类目历史是两套东西：写当前不污染历史", () => {
  saveAttrPicksHistory("u1", 900, { 1: { vid: 7, text: "" } });
  const historyAt = readAttrPicksHistoryAt("u1", 900);
  assert.ok(historyAt > 0);

  // 自动填/草稿估填铺的值只写「当前」，历史必须原样
  saveCurrentAttrPicks("u1", 900, { 1: { vid: 9, text: "" } });

  assert.equal(readCurrentAttrPicks("u1", 900)[1]?.vid, 9, "刷新恢复用新铺的值");
  assert.equal(readAttrPicksHistory("u1", 900)[1]?.vid, 7, "同类目复用的历史不能被改掉");
  assert.equal(readAttrPicksHistoryAt("u1", 900), historyAt, "历史版本不变 → 别的草稿不用重估");
});

test("历史版本时间戳：没有记录是 0，写过就变大", () => {
  assert.equal(readAttrPicksHistoryAt("u1", 1234), 0);
  saveAttrPicksHistory("u1", 1234, { 1: { vid: 1, text: "" } });
  const first = readAttrPicksHistoryAt("u1", 1234);
  assert.ok(first > 0);
  // 再写一次版本必须变（否则草稿库判断不出「历史更新了，要重估」）
  saveAttrPicksHistory("u1", 1234, { 1: { vid: 2, text: "" } });
  assert.ok(readAttrPicksHistoryAt("u1", 1234) >= first);
});

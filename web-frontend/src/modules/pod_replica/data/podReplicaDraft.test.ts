import assert from "node:assert/strict";
import test from "node:test";

import {
  POD_REPLICA_DRAFT_VERSION,
  createEmptyPodReplicaDraft,
  loadPodReplicaDraft,
  podReplicaDraftStorageKey,
  savePodReplicaDraft,
  type PodReplicaDraft,
  type PodReplicaStorage,
} from "./podReplicaDraft.ts";
import { createEmptyReplicaTarget, defaultReplicaPresentationPreferences, type ReplicaTargetDraft } from "./podReplicaModel.ts";

/** 内存版 localStorage，用于隔离测试草稿的读写行为。 */
function memoryStorage(initial: Record<string, string> = {}): PodReplicaStorage & { dump: () => Record<string, string> } {
  const store = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    setItem: (key: string, value: string) => { store.set(key, value); },
    removeItem: (key: string) => { store.delete(key); },
    dump: () => Object.fromEntries(store),
  };
}

function target(clientId: string, assetId: string, productName: string): ReplicaTargetDraft {
  const base = createEmptyReplicaTarget(clientId);
  return {
    ...base,
    assetId,
    filename: `${assetId}.png`,
    width: 800,
    height: 800,
    productName,
    listingFields: { title_mode: "long", suggested_price_usd: "9.99", category_name: "抱枕", skus: [{ name: "S", declared_price: "3", weight_g: "120" }] },
  };
}

test("draft storage keys isolate by account and workspace and carry the version", () => {
  const keyA = podReplicaDraftStorageKey("acc-1", "ws-1");
  assert.notEqual(keyA, podReplicaDraftStorageKey("acc-2", "ws-1"));
  assert.notEqual(keyA, podReplicaDraftStorageKey("acc-1", "ws-2"));
  assert.match(keyA, new RegExp(`v${POD_REPLICA_DRAFT_VERSION}`));
  assert.match(keyA, /acc-1/);
  assert.match(keyA, /ws-1/);
});

test("a saved draft round-trips and stays isolated per workspace", () => {
  const storage = memoryStorage();
  const state: PodReplicaDraft = { version: POD_REPLICA_DRAFT_VERSION, source: { assetId: "asset_source", filename: "bag.png", width: 900, height: 900 }, targets: [target("c1", "asset_a", "抱枕")] };
  assert.deepEqual(savePodReplicaDraft("acc", "ws", state, storage), { ok: true });

  const loaded = loadPodReplicaDraft("acc", "ws", storage);
  assert.equal(loaded.error, undefined);
  assert.equal(loaded.state.source?.assetId, "asset_source");
  assert.deepEqual(loaded.state.targets.map((item) => item.assetId), ["asset_a"]);

  // 其他工作区读不到这份草稿。
  assert.deepEqual(loadPodReplicaDraft("acc", "other-ws", storage).state, createEmptyPodReplicaDraft());
});

test("drafts only persist asset ids and fields, never image bytes", () => {
  const storage = memoryStorage();
  savePodReplicaDraft("acc", "ws", { version: POD_REPLICA_DRAFT_VERSION, source: { assetId: "asset_source", filename: "bag.png" }, targets: [target("c1", "asset_a", "抱枕")] }, storage);
  const raw = Object.values(storage.dump())[0];
  assert.match(raw, /asset_source/);
  assert.match(raw, /asset_a/);
  assert.doesNotMatch(raw, /data:image/);
  assert.doesNotMatch(raw, /base64/i);
  const parsed = JSON.parse(raw) as { targets: Array<Record<string, unknown>> };
  assert.equal(parsed.targets[0].clientId, "c1");
  assert.equal(Object.prototype.hasOwnProperty.call(parsed.targets[0], "dataUrl"), false);
});

test("loading clones targets so later edits cannot mutate the stored draft", () => {
  const storage = memoryStorage();
  savePodReplicaDraft("acc", "ws", { version: POD_REPLICA_DRAFT_VERSION, source: null, targets: [target("c1", "asset_a", "抱枕")] }, storage);
  const loaded = loadPodReplicaDraft("acc", "ws", storage);
  loaded.state.targets[0].listingFields.skus[0].name = "改过的";
  const reloaded = loadPodReplicaDraft("acc", "ws", storage);
  assert.equal(reloaded.state.targets[0].listingFields.skus[0].name, "S");
});

test("corrupted or outdated drafts are discarded with a warning", () => {
  const corrupted = memoryStorage({ [podReplicaDraftStorageKey("acc", "ws")]: "{ not json" });
  const result = loadPodReplicaDraft("acc", "ws", corrupted);
  assert.ok(result.error);
  assert.deepEqual(result.state, createEmptyPodReplicaDraft());
  assert.equal(Object.keys(corrupted.dump()).length, 0);

  const outdated = memoryStorage({ [podReplicaDraftStorageKey("acc", "ws")]: JSON.stringify({ version: 999, targets: [] }) });
  assert.ok(loadPodReplicaDraft("acc", "ws", outdated).error);
});

test("unavailable storage fails closed without throwing", () => {
  assert.ok(loadPodReplicaDraft("acc", "ws", null).error);
  assert.deepEqual(loadPodReplicaDraft("acc", "ws", null).state, createEmptyPodReplicaDraft());
  assert.deepEqual(savePodReplicaDraft("acc", "ws", createEmptyPodReplicaDraft(), null), { ok: false, error: "无法保存爆款复刻草稿：浏览器本地存储不可用。" });
});

test("analysis confirmation and presentation choices survive a draft round-trip", () => {
  const storage = memoryStorage();
  const confirmed: ReplicaTargetDraft = {
    ...target("c1", "asset_a", "美甲套装"),
    analysisId: "analysis-1",
    analysisStatus: "confirmed",
    sourceArtworkSpec: { artwork_motifs: { value: ["玫瑰"], confidence: 0.9, source: "model" } },
    targetProductSpec: { product_type: { value: "nail_tip_set", confidence: 0.9, source: "model" } },
    candidateCapabilities: ["multi_piece", "gel_material"],
    unknownCapabilities: ["hand_painted"],
    factOverrides: { material: "gel" },
    presentationPreferences: { ...defaultReplicaPresentationPreferences(), use_hand_model: true },
  };
  savePodReplicaDraft("acc", "ws", { version: POD_REPLICA_DRAFT_VERSION, source: null, targets: [confirmed] }, storage);

  const loaded = loadPodReplicaDraft("acc", "ws", storage);
  const restored = loaded.state.targets[0];
  assert.equal(restored.analysisId, "analysis-1");
  assert.equal(restored.analysisStatus, "confirmed");
  assert.deepEqual(restored.candidateCapabilities, ["multi_piece", "gel_material"]);
  assert.deepEqual(restored.unknownCapabilities, ["hand_painted"]);
  assert.deepEqual(restored.factOverrides, { material: "gel" });
  assert.equal(restored.presentationPreferences.use_hand_model, true);
  assert.equal(restored.presentationPreferences.use_body_model, false);
});

test("in-flight analysis is not persisted as analyzing", () => {
  const storage = memoryStorage();
  const analyzing: ReplicaTargetDraft = {
    ...target("c1", "asset_a", "抱枕"),
    analysisId: "analysis-2",
    analysisStatus: "analyzing",
  };
  savePodReplicaDraft("acc", "ws", { version: POD_REPLICA_DRAFT_VERSION, source: null, targets: [analyzing] }, storage);

  // 恢复后回到待识别，由用户重新触发分析，而不是卡在「识别中」。
  assert.equal(loadPodReplicaDraft("acc", "ws", storage).state.targets[0].analysisStatus, "pending");
});

test("legacy draft without analysis fields loads as pending instead of crashing", () => {
  const storage = memoryStorage();
  const legacy = { ...target("c1", "asset_a", "抱枕") } as Record<string, unknown>;
  for (const key of ["analysisId", "analysisStatus", "analysisError", "sourceArtworkSpec", "targetProductSpec", "candidateCapabilities", "unknownCapabilities", "factOverrides", "presentationPreferences"]) {
    delete legacy[key];
  }
  storage.setItem(podReplicaDraftStorageKey("acc", "ws"), JSON.stringify({ version: POD_REPLICA_DRAFT_VERSION, source: null, targets: [legacy] }));

  const loaded = loadPodReplicaDraft("acc", "ws", storage);
  assert.equal(loaded.error, undefined);
  const restored = loaded.state.targets[0];
  assert.equal(restored.analysisStatus, "pending");
  assert.deepEqual(restored.candidateCapabilities, []);
  assert.deepEqual(restored.factOverrides, {});
  assert.equal(restored.presentationPreferences.use_lifestyle_scene, true);
  assert.equal(restored.productName, "抱枕");
});

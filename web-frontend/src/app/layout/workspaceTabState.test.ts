import assert from "node:assert/strict";
import test from "node:test";

const { WorkspaceTabScrollStore } = await import(new URL("./workspaceTabState.ts", import.meta.url).href);

test("workspace tab scroll store keeps positions independent for each tab", () => {
  const store = new WorkspaceTabScrollStore();

  store.save("daily-selection", { windowY: 420, contentY: 36 });
  store.save("price-verification", { windowY: 180, contentY: 12 });

  assert.deepEqual(store.restore("daily-selection"), { windowY: 420, contentY: 36 });
  assert.deepEqual(store.restore("price-verification"), { windowY: 180, contentY: 12 });
});

test("workspace tab scroll store returns a copy and discards closed tab state", () => {
  const store = new WorkspaceTabScrollStore();
  store.save("product-task-1", { windowY: 720, contentY: 0 });

  const restored = store.restore("product-task-1");
  assert.ok(restored);
  restored.windowY = 1;
  assert.equal(store.restore("product-task-1")?.windowY, 720);

  store.remove("product-task-1");
  assert.equal(store.restore("product-task-1"), undefined);
});

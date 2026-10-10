import assert from "node:assert/strict";
import test from "node:test";

import { podImageSource } from "./podImageSource.ts";

test("公网链接优先，本地地址作为加载失败时的回退", () => {
  assert.deepEqual(
    podImageSource("https://cdn.example.com/a.png", "/api/pod-customization/assets/a1"),
    { path: "https://cdn.example.com/a.png", fallbackPath: "/api/pod-customization/assets/a1" },
  );
});

test("没有公网链接时直接用本地地址，且不设置回退", () => {
  assert.deepEqual(
    podImageSource("", "/api/pod-customization/assets/a1"),
    { path: "/api/pod-customization/assets/a1" },
  );
  assert.deepEqual(podImageSource(null, "/api/pod-customization/assets/a1"), {
    path: "/api/pod-customization/assets/a1",
  });
});

test("只有公网链接时不改写成空回退", () => {
  assert.deepEqual(podImageSource("https://cdn.example.com/a.png", null), {
    path: "https://cdn.example.com/a.png",
  });
});

test("两者都缺失时返回空，交由调用方展示占位", () => {
  assert.deepEqual(podImageSource(undefined, undefined), {});
  assert.deepEqual(podImageSource("   ", "  "), {});
});

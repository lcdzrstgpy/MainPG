import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_SETTINGS,
  SETTINGS_STORAGE_KEY,
  configuredDefaultsPatch,
  readSettingsFrom,
  sanitizeSettings,
  writeSettingsTo,
  type SettingsState,
  type SettingsStorage,
} from "./settingsData.ts";

class MemoryStorage implements SettingsStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("空存储 / 坏数据时回落到默认设置，不抛异常", () => {
  assert.deepEqual(readSettingsFrom(null), DEFAULT_SETTINGS);
  assert.deepEqual(readSettingsFrom(new MemoryStorage()), DEFAULT_SETTINGS);

  const broken = new MemoryStorage();
  broken.setItem(SETTINGS_STORAGE_KEY, "{not json");
  assert.deepEqual(readSettingsFrom(broken), DEFAULT_SETTINGS);

  assert.deepEqual(sanitizeSettings([1, 2, 3]), DEFAULT_SETTINGS);
});

test("填写过的 Key 与模型写盘后能原样读回（刷新不丢）", () => {
  const storage = new MemoryStorage();
  const state: SettingsState = {
    ...DEFAULT_SETTINGS,
    providers: {
      ...DEFAULT_SETTINGS.providers,
      volcengine: { enabled: true, apiKey: "volc-key", baseUrl: "https://ark.example.com" },
    },
    llm: {
      provider: "DeepSeek",
      baseUrl: "https://api.deepseek.com",
      apiKey: "sk-test",
      model: "deepseek-v4-flash",
      visionModel: "deepseek-v4-flash",
    },
    defaultImageModel: "image_gpt",
    defaultVideoModel: "video_minimax_h3",
  };

  writeSettingsTo(storage, state);

  assert.deepEqual(readSettingsFrom(storage), state);
});

test("uiMode 持久化在设置对象里", () => {
  const storage = new MemoryStorage();
  writeSettingsTo(storage, { ...DEFAULT_SETTINGS, uiMode: "pro" });

  assert.equal(readSettingsFrom(storage).uiMode, "pro");
});

test("坏字段按缺失处理：非法枚举回落默认、负数预算归零、坏 provider 丢弃", () => {
  const state = sanitizeSettings({
    providers: {
      volcengine: { enabled: "yes", apiKey: 42 },
      "bad key!": { enabled: true, apiKey: "x" },
      suchuang: { enabled: true, apiKey: "ok" },
    },
    defaultResolution: "4k",
    defaultAspectRatio: "3:4",
    uiMode: "easy",
    chainMode: "loop",
    spendCapUsd: -12,
    customModels: [{ id: "m1", provider: "suchuang", modelId: "x", name: "", mediaType: "video" }, { id: "bad" }],
  });

  assert.equal(state.providers.volcengine.enabled, false);
  assert.equal(state.providers.volcengine.apiKey, "");
  assert.equal(state.providers["bad key!"], undefined);
  assert.equal(state.providers.suchuang.apiKey, "ok");
  assert.equal(state.defaultResolution, "720p");
  assert.equal(state.defaultAspectRatio, "9:16");
  assert.equal(state.uiMode, "simple");
  assert.equal(state.chainMode, "pin");
  assert.equal(state.spendCapUsd, 0);
  assert.deepEqual(state.customModels, [
    { id: "m1", provider: "suchuang", modelId: "x", name: "x", mediaType: "video" },
  ]);
});

test("兼容 zustand persist 的 {state, version} 包装", () => {
  const state = sanitizeSettings({
    version: 6,
    state: { llm: { apiKey: "sk-wrapped", baseUrl: "https://api.deepseek.com", model: "m", provider: "p" } },
  });

  assert.equal(state.llm.apiKey, "sk-wrapped");
  assert.equal(state.llm.baseUrl, "https://api.deepseek.com");
});

test("运维默认值只补空字段，绝不覆盖用户已填内容", () => {
  const state: SettingsState = {
    ...DEFAULT_SETTINGS,
    providers: { ...DEFAULT_SETTINGS.providers, suchuang: { enabled: true, apiKey: "user-key" } },
    llm: { ...DEFAULT_SETTINGS.llm, apiKey: "sk-user" },
    defaultImageModel: "image_user",
  };
  const defaults = {
    providers: {
      suchuang: { enabled: true, apiKey: "ops-key" },
      volcengine: { enabled: true, apiKey: "ops-volc" },
    },
    llm: { baseUrl: "https://api.deepseek.com", apiKey: "sk-ops", model: "deepseek-v4-flash" },
    defaultImageModel: "image_ops",
    defaultVideoModel: "video_ops",
  };

  const patch = configuredDefaultsPatch(state, defaults);

  assert.equal(patch.providers?.suchuang.apiKey, "user-key");
  assert.equal(patch.providers?.volcengine.apiKey, "ops-volc");
  assert.equal(patch.llm, undefined);
  assert.equal(patch.defaultImageModel, undefined);
  assert.equal(patch.defaultVideoModel, "video_ops");
});

test("没有可补字段或没有运维配置时返回空补丁", () => {
  assert.deepEqual(configuredDefaultsPatch(DEFAULT_SETTINGS, null), {});
  assert.deepEqual(configuredDefaultsPatch(DEFAULT_SETTINGS, {}), {});
});

import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { configuredDefaultsPatch, type ConfiguredDefaults } from "@/lib/configured-defaults";
import { resolveConfiguredDefaults } from "@/lib/configured-defaults-file";
import type { SettingsState } from "@/lib/stores/settings-store";

const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

/** 只有 providers / llm / 默认模型与本模块相关，其余字段用最小替身即可 */
function state(overrides: Partial<SettingsState> = {}): SettingsState {
  return {
    providers: {
      volcengine: { enabled: false, apiKey: "" },
      suchuang: { enabled: false, apiKey: "" },
    },
    llm: { provider: "", baseUrl: "", apiKey: "", model: "", visionModel: "" },
    defaultImageModel: "",
    defaultVideoModel: "",
    ...overrides,
  } as SettingsState;
}

const defaults: ConfiguredDefaults = {
  providers: { suchuang: { enabled: true, apiKey: "suchuang-key" } },
  llm: {
    provider: "DeepSeek",
    baseUrl: "https://api.deepseek.com",
    apiKey: "llm-key",
    model: "deepseek-flash",
    visionModel: "deepseek-flash",
  },
  defaultImageModel: "image_gpt",
  defaultVideoModel: "video_wan_3.0",
};

describe("configuredDefaultsPatch：运维配置只补空值", () => {
  it("空设置时把平台 Key、LLM 与默认模型全部补上", () => {
    const patch = configuredDefaultsPatch(state(), defaults);

    expect(patch.providers?.suchuang).toEqual({ enabled: true, apiKey: "suchuang-key" });
    // 未配置的其它平台不能被抹掉，否则读取 providers.volcengine 的调用方会崩
    expect(patch.providers?.volcengine).toEqual({ enabled: false, apiKey: "" });
    expect(patch.llm).toEqual({ ...defaults.llm });
    expect(patch.defaultImageModel).toBe("image_gpt");
    expect(patch.defaultVideoModel).toBe("video_wan_3.0");
  });

  it("用户已经填过的值一律不动（配置不覆盖用户）", () => {
    const patch = configuredDefaultsPatch(
      state({
        providers: {
          volcengine: { enabled: false, apiKey: "" },
          suchuang: { enabled: true, apiKey: "user-key" },
        },
        llm: { provider: "自定义", baseUrl: "https://example.test", apiKey: "user-llm", model: "my-model" },
        defaultImageModel: "image_gpt_2.5",
        defaultVideoModel: "minimax_h3",
      }),
      defaults
    );

    expect(patch).toEqual({});
  });

  it("没有配置（null）时返回空补丁", () => {
    expect(configuredDefaultsPatch(state(), null)).toEqual({});
  });

  it("空白的 Key 不构成「已配置」，仍然补", () => {
    const patch = configuredDefaultsPatch(
      state({ providers: { suchuang: { enabled: true, apiKey: "   " } } }),
      defaults
    );

    expect(patch.providers?.suchuang?.apiKey).toBe("suchuang-key");
  });
});

describe("resolveConfiguredDefaults：读取 WH_CLIPFORGE_CONFIG", () => {
  const path = "/tmp/clipforge-test-defaults.json";

  afterEach(() => {
    delete process.env.WH_CLIPFORGE_CONFIG;
    rmSync(path, { force: true });
  });

  it("按 WH_CLIPFORGE_CONFIG 指向的文件解析配置", () => {
    writeFileSync(path, JSON.stringify(defaults));
    process.env.WH_CLIPFORGE_CONFIG = path;

    expect(resolveConfiguredDefaults()).toEqual(defaults);
  });

  it("文件缺失或不是合法 JSON 时返回 null（不把页面拖下水）", () => {
    process.env.WH_CLIPFORGE_CONFIG = "/tmp/clipforge-test-missing.json";
    expect(resolveConfiguredDefaults()).toBeNull();

    writeFileSync(path, "{ 不是 json");
    process.env.WH_CLIPFORGE_CONFIG = path;
    expect(resolveConfiguredDefaults()).toBeNull();
  });
});

/**
 * 源码契约：默认设置必须由 root layout 读出并交给 initializer —— sidecar 随机端口导致
 * origin 每次都变、localStorage 里的设置读不到，这条通路是「重启后不用重输 Key」的唯一来源。
 */
describe("默认设置注入的接线（源码契约）", () => {
  const layout = read("src/app/layout.tsx");
  const initializer = read("src/components/configured-defaults-initializer.tsx");
  const serverReader = read("src/lib/configured-defaults-file.ts");

  it("root layout 读取配置并挂载 initializer", () => {
    expect(layout).toContain("resolveConfiguredDefaults");
    expect(layout).toContain("ConfiguredDefaultsInitializer");
    expect(layout).toContain("<ConfiguredDefaultsInitializer defaults={configuredDefaults} />");
  });

  it("initializer 只写回补丁，不整份覆盖 store", () => {
    expect(initializer).toContain("configuredDefaultsPatch");
    expect(initializer).toContain("store.setState(patch)");
  });

  it("服务端读取入口认 WH_CLIPFORGE_CONFIG 与 APP_DATA_DIR", () => {
    expect(serverReader).toContain("WH_CLIPFORGE_CONFIG");
    expect(serverReader).toContain("APP_DATA_DIR");
    expect(serverReader).toContain("clipforge.local.json");
  });
});

import { describe, expect, it } from "vitest";

describe("火山语音 V3 HTTP Chunked", () => {
  it("将分块 SSE 记录解码为完整 MP3", async () => {
    const { parseVolcengineTTSStream } = await import("@/lib/tts");
    async function* chunks() {
      yield Buffer.from('data: {"code":0,"data":"aGVs"}\n');
      yield Buffer.from('data: {"code":0,"data":"bG8="}\n\ndata: {"code":20000000}\n\n');
    }
    await expect(parseVolcengineTTSStream(chunks())).resolves.toEqual(Buffer.from("hello"));
  });

  it("将服务端业务错误保留为可见失败原因", async () => {
    const { parseVolcengineTTSStream } = await import("@/lib/tts");
    async function* chunks() {
      yield Buffer.from('data: {"code":45000000,"message":"speaker permission denied"}\n\n');
    }
    await expect(parseVolcengineTTSStream(chunks())).rejects.toThrow("speaker permission denied");
  });

  it("解析 V3 配置的资源、引擎和语速", async () => {
    const { resolveTTSConfig } = await import("@/lib/tts-presets");
    expect(resolveTTSConfig({
      enabled: true,
      provider: "volcengine-speech",
      apiKey: "test-key",
      voice: "zh_female_vv_uranus_bigtts",
      speechRate: 15,
    }, {})).toMatchObject({
      provider: "volcengine-speech",
      baseUrl: "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
      resourceId: "seed-tts-2.0",
      model: "seed-tts-2.0-expressive",
      speechRate: 15,
    });
  });

  it("迁移旧的 OpenAI/方舟配音设置，不复用旧 Key", async () => {
    const { migrateSettings } = await import("@/lib/stores/settings-store");
    const migrated = migrateSettings({
      tts: {
        enabled: true,
        provider: "openai",
        baseUrl: "https://ark.cn-beijing.volces.com/api/v3",
        apiKey: "old-media-key",
        model: "doubao-tts",
        voice: "zh_female_cancan",
      },
      activeProductionProfile: "balanced",
    } as never);
    expect(migrated.tts).toMatchObject({
      enabled: true,
      provider: "volcengine-speech",
      baseUrl: "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
      apiKey: "",
      model: "seed-tts-2.0-expressive",
      voice: "",
      resourceId: "seed-tts-2.0",
      speechRate: 10,
    });
  });
});

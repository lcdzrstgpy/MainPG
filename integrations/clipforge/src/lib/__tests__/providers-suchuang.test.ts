import { afterEach, describe, expect, it, vi } from "vitest";

import { SuchuangProvider } from "../providers/suchuang";

const apiKey = "speed-key";
const baseUrl = "https://api.wuyinkeji.com";

function provider() {
  return new SuchuangProvider({ name: "suchuang", apiKey, baseUrl });
}

describe("SuchuangProvider", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the documented async-video endpoint and media fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 200, data: { id: "task-123" } })));
    vi.stubGlobal("fetch", fetchMock);

    const submitted = await provider().submitVideoTask({
      modelId: "video_wan_3.0",
      mode: "image-to-video",
      prompt: "商品缓慢旋转展示",
      firstFrameUrl: "https://cdn.example/first.png",
      lastFrameUrl: "https://cdn.example/last.png",
      referenceImageUrls: ["https://cdn.example/ref.png"],
      referenceVideoUrls: ["https://cdn.example/ref.mp4"],
      referenceAudioUrls: ["https://cdn.example/ref.mp3"],
      audioEnabled: true,
      duration: 8,
      width: 1080,
      height: 1920,
    });

    expect(submitted).toEqual({ taskId: "task-123", modelId: "video_wan_3.0" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${baseUrl}/api/async/video_wan_3.0?key=${apiKey}`);
    expect(init.headers).toMatchObject({ Authorization: apiKey });
    expect(JSON.parse(init.body)).toEqual({
      prompt: "商品缓慢旋转展示",
      first_frame: "https://cdn.example/first.png",
      last_frame: "https://cdn.example/last.png",
      images: "https://cdn.example/ref.png",
      videos: "https://cdn.example/ref.mp4",
      audios: "https://cdn.example/ref.mp3",
      generate_audio: true,
      ratio: "9:16",
      duration: 8,
    });
  });

  it("normalizes a completed async task and finds its video URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 200,
      data: { status: "success", output: { video_url: "https://cdn.example/final.mp4" } },
    }))));

    await expect(provider().getTaskStatus("task-123")).resolves.toMatchObject({
      taskId: "task-123",
      status: "completed",
      result: { videoUrls: ["https://cdn.example/final.mp4"] },
    });
  });

  it("does not retry an uncertain paid-task submission", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("gateway error", { status: 502, statusText: "Bad Gateway" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(provider().submitVideoTask({
      modelId: "video_wan_3.0",
      mode: "text-to-video",
      prompt: "测试",
    })).rejects.toMatchObject({ code: "API_ERROR" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

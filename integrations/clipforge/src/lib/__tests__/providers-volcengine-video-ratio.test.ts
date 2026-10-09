import { describe, expect, it } from "vitest";

import { VolcEngineProvider } from "@/lib/providers/volcengine";
import type { VideoOptions } from "@/lib/providers/types";

/**
 * 火山方舟规定：任务类型由 content.role 决定——只要带了 first_frame / last_frame，
 * 就是「首帧/首尾帧生视频」，此时 ratio 必须为 adaptive，否则直接 400
 * （InvalidParameter.TaskTypeConstraint）。
 * 这里替换掉 protected 的 request，直接断言真实发出的 body。
 */
function captureProvider() {
  const provider = new VolcEngineProvider({
    name: "volcengine",
    apiKey: "test-key",
    baseUrl: "https://example.com",
  });
  const bodies: Record<string, unknown>[] = [];
  const view = provider as unknown as {
    request: (path: string, options?: { body?: Record<string, unknown> }) => Promise<unknown>;
  };
  view.request = async (_path, options) => {
    bodies.push(options?.body ?? {});
    return { id: "task-1" };
  };
  return { provider, bodies };
}

function base(overrides: Partial<VideoOptions>): VideoOptions {
  return { modelId: "doubao-seedance-2-5-260128", mode: "text-to-video", prompt: "商品旋转", ...overrides };
}

describe("火山引擎视频 ratio 与首帧约束", () => {
  it("文生视频：没有首尾帧时按画幅发具体比例", async () => {
    const { provider, bodies } = captureProvider();

    await provider.submitVideoTask(base({ width: 1080, height: 1920 }));

    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({ ratio: "9:16" });
    expect(bodies[0].content).toEqual([{ type: "text", text: "商品旋转" }]);
  });

  it("首帧生视频：ratio 必须为 adaptive，且首帧作为 first_frame 下发", async () => {
    const { provider, bodies } = captureProvider();

    await provider.submitVideoTask(
      base({ mode: "image-to-video", firstFrameUrl: "https://cdn.example/first.png", width: 1080, height: 1920 })
    );

    expect(bodies[0]).toMatchObject({ ratio: "adaptive" });
    expect(bodies[0].content).toEqual([
      { type: "text", text: "商品旋转" },
      { type: "image_url", image_url: { url: "https://cdn.example/first.png" }, role: "first_frame" },
    ]);
  });

  it("首尾帧生视频：带上 last_frame 时同样为 adaptive", async () => {
    const { provider, bodies } = captureProvider();

    await provider.submitVideoTask(
      base({
        mode: "image-to-video",
        firstFrameUrl: "https://cdn.example/first.png",
        lastFrameUrl: "https://cdn.example/last.png",
        width: 720,
        height: 1280,
      })
    );

    expect(bodies[0]).toMatchObject({ ratio: "adaptive" });
    expect(bodies[0].content).toContainEqual({
      type: "image_url",
      image_url: { url: "https://cdn.example/last.png" },
      role: "last_frame",
    });
  });

  it("参考生视频：只有参考素材（无首尾帧）仍按画幅发具体比例", async () => {
    const { provider, bodies } = captureProvider();

    await provider.submitVideoTask(
      base({
        mode: "video-to-video",
        referenceImageUrls: ["https://cdn.example/ref.png"],
        referenceVideoUrls: ["https://cdn.example/ref.mp4"],
        width: 1080,
        height: 1920,
      })
    );

    expect(bodies[0]).toMatchObject({ ratio: "9:16" });
    expect(bodies[0].content).toContainEqual({ type: "video_url", video_url: { url: "https://cdn.example/ref.mp4" }, role: "reference_video" });
  });

  it("尺寸缺失且无首尾帧时回退 adaptive", async () => {
    const { provider, bodies } = captureProvider();

    await provider.submitVideoTask(base({}));

    expect(bodies[0]).toMatchObject({ ratio: "adaptive" });
  });
});

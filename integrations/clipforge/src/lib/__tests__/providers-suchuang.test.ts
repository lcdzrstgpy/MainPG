import { rmSync, writeFileSync } from "node:fs";
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
      // 上游（Go）按 []string 反序列化，逗号拼接字符串会被判 unmarshal 失败（实测）
      images: ["https://cdn.example/ref.png"],
      videos: ["https://cdn.example/ref.mp4"],
      audios: ["https://cdn.example/ref.mp3"],
      generate_audio: true,
      ratio: "9:16",
      // duration 必须是字符串：传数字会被判 `cannot unmarshal number into …seconds of type string`（实测）
      duration: "8",
    });
  });

  it("MiniMax H3 走文档里的 video_minimax_h3 端点（写 minimax_h3 会 404）", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: 200, data: { id: "task-mini" } })));
    vi.stubGlobal("fetch", fetchMock);

    await provider().submitVideoTask({
      modelId: "video_minimax_h3",
      mode: "text-to-video",
      prompt: "商品展示",
      duration: 5,
      width: 720,
      height: 1280,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${baseUrl}/api/async/video_minimax_h3?key=${apiKey}`);
    expect(JSON.parse(init.body).duration).toBe("5");
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

  it("数字状态 + result 里的成图 URL 判为完成，且不把回显的参考图当产出", async () => {
    // 实测报文：status 是数字（2=已完成），request 会把我们提交的参考图原样回显
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 200,
      data: {
        task_id: "image_abc",
        status: 2,
        result: ["https://scapi.net/9d18ad2f.png"],
        request: { prompt: "静物摄影", urls: ["https://cos.example/ref.jpg?q-signature=deadbeef"] },
      },
    }))));

    await expect(provider().getTaskStatus("image_abc")).resolves.toEqual({
      taskId: "image_abc",
      status: "completed",
      result: { taskId: "image_abc", imageUrls: ["https://scapi.net/9d18ad2f.png"], modelId: "" },
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

  /**
   * 图片端点有两个分支，与 MainPG 媒体模块 `infrastructure/media.py` 的写法必须一致：
   * - image_gpt_2.5：urls 逗号拼接字符串 + 像素串 aspectRatio；传数组或传 size 上游会返 500；
   * - image_gpt：urls 传数组 + 比例串 size。
   */
  function imageSubmitBody(fetchMock: ReturnType<typeof vi.fn>) {
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/async/");
    return { url, body: JSON.parse(init.body), headers: init.headers };
  }

  function imageFetchMock() {
    return vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 200, data: { id: "task-img-1" } })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 200, data: { status: "success", image_url: "https://cdn.example/out.png" } })));
  }

  it("image_gpt_2.5 用逗号拼接的 urls + 像素串 aspectRatio（传数组会被上游判 500）", async () => {
    const fetchMock = imageFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    const result = await provider().generateImage({
      modelId: "image_gpt_2.5",
      mode: "image-to-image",
      prompt: "九宫格分镜",
      referenceImageUrls: ["https://cdn.example/a.png", "https://cdn.example/b.png"],
      width: 720,
      height: 1280,
    });

    expect(result.imageUrls).toEqual(["https://cdn.example/out.png"]);
    const { url, body, headers } = imageSubmitBody(fetchMock);
    expect(url).toBe(`${baseUrl}/api/async/image_gpt_2.5?key=${apiKey}`);
    expect(headers).toMatchObject({ Authorization: apiKey });
    expect(body).toEqual({
      prompt: "九宫格分镜",
      aspectRatio: "720x1280",
      urls: "https://cdn.example/a.png,https://cdn.example/b.png",
    });
    expect(body).not.toHaveProperty("size");
  });

  it("image_gpt 用数组 urls + 比例串 size", async () => {
    const fetchMock = imageFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    await provider().generateImage({
      modelId: "image_gpt",
      mode: "image-to-image",
      prompt: "九宫格分镜",
      referenceImageUrl: "https://cdn.example/ref.png",
      width: 2048,
      height: 2048,
    });

    const { url, body } = imageSubmitBody(fetchMock);
    expect(url).toBe(`${baseUrl}/api/async/image_gpt?key=${apiKey}`);
    expect(body).toEqual({
      prompt: "九宫格分镜",
      size: "1:1",
      urls: ["https://cdn.example/ref.png"],
    });
    expect(body).not.toHaveProperty("aspectRatio");
  });

  /**
   * 参考图中转：速创的 urls 由上游去抓，data URI 会被打回 500「转发请求失败」。
   * 因此本地图必须先发布成临时公网 URL（与 MainPG 媒体模块的 COS 中转一致）。
   */
  describe("参考图中转", () => {
    const cosConfigPath = "/tmp/suchuang-test-cos.local.json";

    function stubCosConfig() {
      writeFileSync(cosConfigPath, JSON.stringify({
        bucket: "demo-bucket-123",
        region: "ap-guangzhou",
        secret_id: "AKIDdemo",
        secret_key: "demo-secret",
        cos_prefix: "demo-prefix",
      }));
      process.env.WH_MEDIA_COS_CONFIG = cosConfigPath;
    }

    afterEach(() => {
      delete process.env.WH_MEDIA_COS_CONFIG;
      rmSync(cosConfigPath, { force: true });
    });

    function relayFetchMock() {
      return vi.fn(async (url: string, init: RequestInit = {}) => {
        const method = (init.method ?? "GET").toUpperCase();
        if (url.includes(".cos.")) return new Response(null, { status: 200 });
        if (method === "POST") return new Response(JSON.stringify({ code: 200, data: { id: "task-img-9" } }));
        return new Response(JSON.stringify({ code: 200, data: { status: "success", image_url: "https://cdn.example/out.png" } }));
      });
    }

    it("data URI 参考图先发布成公网 URL 再提交", async () => {
      stubCosConfig();
      const fetchMock = relayFetchMock();
      vi.stubGlobal("fetch", fetchMock);

      await provider().generateImage({
        modelId: "image_gpt",
        mode: "image-to-image",
        prompt: "九宫格分镜",
        referenceImageUrl: "data:image/jpeg;base64,QUJD",
        width: 720,
        height: 1280,
      });

      const uploadCall = fetchMock.mock.calls.find(([url]) => String(url).includes(".cos."))!;
      expect(String(uploadCall[0])).toContain("demo-bucket-123.cos.ap-guangzhou.myqcloud.com/demo-prefix/mainpg-clipforge/transient/");
      expect((uploadCall[1] as RequestInit).method).toBe("PUT");

      const submitCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/async/"))!;
      const body = JSON.parse((submitCall[1] as RequestInit).body as string);
      expect(body.urls).toHaveLength(1);
      expect(body.urls[0]).toMatch(/^https:\/\/demo-bucket-123\.cos\.ap-guangzhou\.myqcloud\.com\/.*q-signature=/);
      expect(body.urls[0]).not.toContain("data:image");
    });

    it("已经是公网 URL 的参考图原样透传，不碰对象存储", async () => {
      stubCosConfig();
      const fetchMock = relayFetchMock();
      vi.stubGlobal("fetch", fetchMock);

      await provider().generateImage({
        modelId: "image_gpt",
        mode: "image-to-image",
        prompt: "九宫格分镜",
        referenceImageUrls: ["https://cdn.example/a.png", "https://cdn.example/b.png"],
        width: 720,
        height: 1280,
      });

      expect(fetchMock.mock.calls.some(([url]) => String(url).includes(".cos."))).toBe(false);
      const submitCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/async/"))!;
      expect(JSON.parse((submitCall[1] as RequestInit).body as string).urls).toEqual([
        "https://cdn.example/a.png",
        "https://cdn.example/b.png",
      ]);
    });

    it("没有中转配置时明确报错，绝不发一个抓不到的地址", async () => {
      delete process.env.WH_MEDIA_COS_CONFIG;
      const fetchMock = relayFetchMock();
      vi.stubGlobal("fetch", fetchMock);

      await expect(provider().generateImage({
        modelId: "image_gpt",
        mode: "image-to-image",
        prompt: "九宫格分镜",
        referenceImageUrl: "data:image/jpeg;base64,QUJD",
        width: 720,
        height: 1280,
      })).rejects.toMatchObject({ code: "REFERENCE_RELAY_UNAVAILABLE" });

      // 连提交都没发生
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/api/async/"))).toBe(false);
    });

    it("视频任务的参考图同样中转（images 为字符串数组）", async () => {
      stubCosConfig();
      const fetchMock = relayFetchMock();
      vi.stubGlobal("fetch", fetchMock);

      await provider().submitVideoTask({
        modelId: "video_wan_3.0",
        mode: "video-to-video",
        prompt: "整片",
        referenceImageUrls: ["data:image/jpeg;base64,QUJD", "data:image/jpeg;base64,REVG"],
        duration: 30,
        width: 720,
        height: 1280,
      });

      const submitCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/async/"))!;
      const body = JSON.parse((submitCall[1] as RequestInit).body as string);
      expect(Array.isArray(body.images)).toBe(true);
      expect(body.images).toHaveLength(2);
      for (const url of body.images as string[]) {
        expect(url).toMatch(/^https:\/\/demo-bucket-123\.cos\.ap-guangzhou\.myqcloud\.com\/.*q-signature=/);
      }
    });
  });
});

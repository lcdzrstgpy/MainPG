import { describe, expect, it, vi } from "vitest";
import { VolcEngineProvider } from "@/lib/providers/volcengine";

/**
 * `request` 是 BaseProvider 的 protected 方法，测试里要用 spy 断言实际发出的请求参数。
 * 用一个显式类型的窄化视图把它交给 vi.spyOn：写成 `as never` 会让 spy 推断成 never，
 * `mockResolvedValue` 等 API 随之丢失类型（不改变被测代码，只是让 spy 的类型正确）。
 */
const requestView = (provider: VolcEngineProvider) =>
  provider as unknown as {
    request: (path: string, options?: { method?: "GET" | "POST"; timeout?: number }) => Promise<unknown>;
  };

describe("火山引擎生图超时", () => {
  it("九宫格等同步生图请求使用 120 秒超时", async () => {
    const provider = new VolcEngineProvider({ name: "volcengine", apiKey: "test-key", baseUrl: "https://example.com" });
    const request = vi
      .spyOn(requestView(provider), "request")
      .mockResolvedValue({ data: [{ url: "https://example.com/result.png" }] });

    await provider.generateImage({ modelId: "doubao-seedream-5-0-260128", mode: "text-to-image", prompt: "product" });

    expect(request).toHaveBeenCalledWith(
      "/images/generations",
      expect.objectContaining({ method: "POST", timeout: 120_000 })
    );
  });
});

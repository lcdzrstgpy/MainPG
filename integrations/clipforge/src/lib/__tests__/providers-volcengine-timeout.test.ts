import { describe, expect, it, vi } from "vitest";
import { VolcEngineProvider } from "@/lib/providers/volcengine";

describe("火山引擎生图超时", () => {
  it("九宫格等同步生图请求使用 120 秒超时", async () => {
    const provider = new VolcEngineProvider({ name: "volcengine", apiKey: "test-key", baseUrl: "https://example.com" });
    const request = vi.spyOn(provider as never, "request" as never).mockResolvedValue({ data: [{ url: "https://example.com/result.png" }] } as never);

    await provider.generateImage({ modelId: "doubao-seedream-5-0-260128", mode: "text-to-image", prompt: "product" });

    expect(request).toHaveBeenCalledWith(
      "/images/generations",
      expect.objectContaining({ method: "POST", timeout: 120_000 })
    );
  });
});

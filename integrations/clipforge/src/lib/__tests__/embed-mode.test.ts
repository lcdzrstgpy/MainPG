import { describe, expect, it } from "vitest";
import { resolveEmbedMode } from "@/lib/embed-mode";

/**
 * 内嵌判定的纯逻辑契约：决定首屏是否输出 MainPG 内嵌壳。
 * standalone 必须压过 cookie，否则内嵌过后就再也无法显式退出。
 */
describe("resolveEmbedMode", () => {
  it("?embed=mainpg 判定为内嵌", () => {
    expect(resolveEmbedMode("mainpg", null)).toBe("mainpg");
    expect(resolveEmbedMode("mainpg", "1")).toBe("mainpg");
  });

  it("无 query 时靠 session cookie 续航（内部路由不带 embed）", () => {
    expect(resolveEmbedMode(null, "1")).toBe("mainpg");
    expect(resolveEmbedMode("", "1")).toBe("mainpg");
  });

  it("?embed=standalone 压过 cookie", () => {
    expect(resolveEmbedMode("standalone", "1")).toBe("standalone");
    expect(resolveEmbedMode("standalone", null)).toBe("standalone");
  });

  it("都没有则返回 null（保持普通独立运行）", () => {
    expect(resolveEmbedMode(null, null)).toBeNull();
    expect(resolveEmbedMode("", null)).toBeNull();
    expect(resolveEmbedMode("", "")).toBeNull();
  });

  it("cookie 值不是 \"1\" 一律不算内嵌", () => {
    for (const value of ["0", "true", "mainpg", " 1", "1 "]) {
      expect(resolveEmbedMode(null, value), `cookie ${JSON.stringify(value)} 不应判为内嵌`).toBeNull();
    }
  });
});
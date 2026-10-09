import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "@/proxy";
import { EMBED_COOKIE_NAME, EMBED_HEADER_NAME } from "@/lib/embed-mode";

/**
 * proxy 契约（真实调用，非源码文本断言）：内嵌判定必须在服务端首屏完成。
 *
 * Next 16 用 `proxy.ts` 取代了 `middleware.ts`，所以内嵌标记与本地 CORS 共用 `proxy()`；
 * `NextResponse.next({ request: { headers } })` 会把改写后的请求头编码进响应：
 *   - 每个键 → `x-middleware-request-<key>`
 *   - 键清单 → `x-middleware-override-headers`
 * 后续这一跳的 root layout 通过 headers() 读到的正是这些值。
 */

const call = (url: string, headers?: Record<string, string>) =>
  proxy(new NextRequest(new URL(url), headers ? { headers } : undefined));

const overriddenHeader = (url: string, headers?: Record<string, string>) =>
  call(url, headers).headers.get(`x-middleware-request-${EMBED_HEADER_NAME}`);

describe("MainPG 内嵌 proxy", () => {
  it("?embed=mainpg：写内嵌请求头并下发 session cookie", () => {
    const res = call("http://localhost:3457/start?embed=mainpg");

    expect(res.headers.get(`x-middleware-request-${EMBED_HEADER_NAME}`)).toBe("1");
    expect(res.headers.get("x-middleware-override-headers")).toContain(EMBED_HEADER_NAME);

    const cookie = res.cookies.get(EMBED_COOKIE_NAME);
    expect(cookie?.value).toBe("1");
    expect(cookie?.path).toBe("/");
  });

  it("内部路由（不带 query）靠 cookie 续航", () => {
    expect(overriddenHeader("http://localhost:3457/projects", { cookie: `${EMBED_COOKIE_NAME}=1` })).toBe("1");
    expect(overriddenHeader("http://localhost:3457/project/clone", { cookie: `${EMBED_COOKIE_NAME}=1` })).toBe("1");
  });

  it("?embed=standalone：清掉请求头与 cookie", () => {
    const res = call("http://localhost:3457/start?embed=standalone", { cookie: `${EMBED_COOKIE_NAME}=1` });

    expect(res.headers.get(`x-middleware-request-${EMBED_HEADER_NAME}`)).toBeNull();
    expect(res.headers.get("x-middleware-override-headers") ?? "").not.toContain(EMBED_HEADER_NAME);
    expect(res.headers.get("set-cookie")).toContain(`${EMBED_COOKIE_NAME}=`);
    // 删除 = 空值 + 过期时间；两种表示都算已清除，取值一律视为空
    expect(res.cookies.get(EMBED_COOKIE_NAME)?.value ?? "").toBe("");
  });

  it("普通独立运行：不带任何内嵌标记", () => {
    const res = call("http://localhost:3457/start");

    expect(res.headers.get(`x-middleware-request-${EMBED_HEADER_NAME}`)).toBeNull();
    expect(res.headers.get("set-cookie")).toBeNull();
  });

  it("cookie 值非 \"1\" 不触发内嵌", () => {
    expect(overriddenHeader("http://localhost:3457/projects", { cookie: `${EMBED_COOKIE_NAME}=0` })).toBeNull();
  });
});

describe("本地 CORS 未被内嵌合并破坏", () => {
  it("内嵌请求与 CORS 回显共存（本地端口来源）", () => {
    const res = call("http://localhost:3457/api/health?embed=mainpg", { origin: "http://localhost:3800" });

    expect(res.headers.get("access-control-allow-origin")).toBe("http://localhost:3800");
    expect(res.headers.get(`x-middleware-request-${EMBED_HEADER_NAME}`)).toBe("1");
    expect(res.cookies.get(EMBED_COOKIE_NAME)?.value).toBe("1");
  });

  it("非本地来源仍然零 CORS 头（安全边界不变）", () => {
    const res = call("http://localhost:3457/api/health", { origin: "https://evil.example.com" });

    expect(res.headers.get("access-control-allow-origin")).toBeNull();
  });

  it("OPTIONS 预检仍是提前返回的 204，且不带内嵌头", () => {
    const res = proxy(
      new NextRequest(new URL("http://localhost:3457/api/health?embed=mainpg"), {
        method: "OPTIONS",
        headers: { origin: "http://localhost:3800", "access-control-request-headers": "content-type" },
      })
    );

    expect(res.status).toBe(204);
    expect(res.headers.get("access-control-allow-origin")).toBe("http://localhost:3800");
    expect(res.headers.get("x-middleware-request-x-mainpg-embed")).toBeNull();
  });
});
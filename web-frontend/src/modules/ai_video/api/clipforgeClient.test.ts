import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import {
  CF_PROXY_PREFIX,
  ClipForgeRequestError,
  cfBlob,
  cfHead,
  cfJson,
  cfUrl,
} from "./clipforgeClient.ts";

type FetchCall = { url: string; init: RequestInit };

const realFetch = globalThis.fetch;
let calls: FetchCall[] = [];

function stubFetch(handler: (url: string, init: RequestInit) => Promise<Response>) {
  calls = [];
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const resolved = init ?? {};
    calls.push({ url, init: resolved });
    return handler(url, resolved);
  }) as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = realFetch;
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("cfUrl prefixes every request with the same-origin proxy", () => {
  assert.equal(CF_PROXY_PREFIX, "/api/clipforge/proxy");
  assert.equal(cfUrl("/api/project"), "/api/clipforge/proxy/api/project");
  assert.equal(cfUrl("api/health"), "/api/clipforge/proxy/api/health");
});

test("cfJson serializes json bodies and parses JSON responses", async () => {
  stubFetch(async () => jsonResponse([{ id: "p1" }]));

  const projects = await cfJson<Array<{ id: string }>>("/api/project", {
    method: "POST",
    json: { name: "测试项目" },
  });

  assert.deepEqual(projects, [{ id: "p1" }]);
  assert.equal(calls[0].url, "/api/clipforge/proxy/api/project");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(calls[0].init.body, JSON.stringify({ name: "测试项目" }));
  assert.equal(new Headers(calls[0].init.headers).get("content-type"), "application/json");
});

test("cfJson keeps FormData bodies untouched so the browser sets the boundary", async () => {
  stubFetch(async () => jsonResponse({ paths: ["/api/files/p/1.png"] }));
  const form = new FormData();
  form.set("projectId", "p");

  await cfJson("/api/upload", { method: "POST", body: form });

  assert.equal(calls[0].init.body, form);
  assert.equal(new Headers(calls[0].init.headers).get("content-type"), null);
});

test("maps detail.message and stable codes from the backend", async () => {
  stubFetch(async () =>
    jsonResponse(
      { detail: { code: "CLIPFORGE_NOT_READY", message: "AI 视频服务未就绪" } },
      503,
    ),
  );

  await assert.rejects(cfJson("/api/health"), (error: unknown) => {
    assert.ok(error instanceof ClipForgeRequestError);
    assert.equal(error.status, 503);
    assert.equal(error.code, "CLIPFORGE_NOT_READY");
    assert.equal(error.message, "AI 视频服务未就绪");
    return true;
  });
});

test("falls back to the top-level error field", async () => {
  stubFetch(async () => jsonResponse({ error: "项目不存在" }, 404));

  await assert.rejects(cfJson("/api/project/missing"), (error: unknown) => {
    assert.ok(error instanceof ClipForgeRequestError);
    assert.equal(error.message, "项目不存在");
    assert.equal(error.status, 404);
    return true;
  });
});

test("uses a Chinese status fallback when the error body is not JSON", async () => {
  stubFetch(async () => new Response("boom", { status: 500 }));

  await assert.rejects(cfJson("/api/project"), /AI 视频请求失败（HTTP 500）/);
});

test("preserves the 409 style-conflict payload for the chooser prompt", async () => {
  const candidates = ["痛点型", "场景型", "对比型"];
  stubFetch(async () =>
    jsonResponse({ error: "请先选择脚本风格", code: "needs_explicit_style", reason: "no-data", candidates }, 409),
  );

  await assert.rejects(cfJson("/api/llm/script"), (error: unknown) => {
    assert.ok(error instanceof ClipForgeRequestError);
    assert.equal(error.code, "needs_explicit_style");
    assert.deepEqual((error.payload as { candidates: string[] }).candidates, candidates);
    return true;
  });
});

test("maps network failures to a Chinese message", async () => {
  stubFetch(async () => {
    throw new TypeError("Failed to fetch");
  });

  await assert.rejects(cfJson("/api/project"), (error: unknown) => {
    assert.ok(error instanceof ClipForgeRequestError);
    assert.equal(error.status, 0);
    assert.equal(error.message, "无法连接 AI 视频服务，请确认服务已启动");
    return true;
  });
});

test("forwards AbortSignal and rethrows cancellation untouched", async () => {
  const controller = new AbortController();
  stubFetch(async (_url, init) => {
    assert.equal(init.signal, controller.signal);
    const error = new Error("aborted");
    error.name = "AbortError";
    throw error;
  });

  await assert.rejects(cfJson("/api/project", { signal: controller.signal }), (error: unknown) => {
    assert.ok(!(error instanceof ClipForgeRequestError));
    assert.equal((error as Error).name, "AbortError");
    return true;
  });
});

test("treats a 2xx HTML body as a failure instead of empty data", async () => {
  stubFetch(
    async () =>
      new Response("<!doctype html><html><body>SPA</body></html>", {
        status: 200,
        headers: { "content-type": "text/html" },
      }),
  );

  await assert.rejects(cfJson("/api/project"), /返回了网页而不是数据/);
});

test("cfBlob streams binary responses and keeps request headers", async () => {
  stubFetch(async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 }));

  const blob = await cfBlob("/api/files/p/a.mp4", { headers: { Range: "bytes=0-1" } });

  assert.equal(blob.size, 3);
  assert.equal(calls[0].url, "/api/clipforge/proxy/api/files/p/a.mp4");
  assert.equal(new Headers(calls[0].init.headers).get("range"), "bytes=0-1");
});

test("cfHead issues a HEAD request and returns the response headers", async () => {
  stubFetch(
    async () =>
      new Response(null, {
        status: 200,
        headers: { "content-type": "video/mp4", "content-length": "1024" },
      }),
  );

  const response = await cfHead("/api/output/p/a.mp4");

  assert.equal(calls[0].init.method, "HEAD");
  assert.equal(response.headers.get("content-length"), "1024");
});

import { NextRequest, NextResponse } from "next/server";
import {
  EMBED_COOKIE_NAME,
  EMBED_COOKIE_ON,
  EMBED_HEADER_NAME,
  EMBED_HEADER_ON,
  resolveEmbedMode,
} from "@/lib/embed-mode";

/**
 * Local-tool CORS for /api/*.
 *
 * Browser pages served from OTHER local ports call our API cross-origin — the
 * first consumer is the infinite-canvas workbench running the ClipForge video
 * node plugin (canvas at :3800/:3000 → ClipForge at :3457). Without these
 * headers every such fetch dies at the browser wall.
 *
 * Security: only localhost/127.0.0.1/[::1] origins (any port) are reflected.
 * A remote malicious page's origin never matches, so the browser-side wall
 * against drive-by abuse of the local instance (which can trigger paid-model
 * spending) stays intact. Additional trusted origins can be granted explicitly
 * via CLIPFORGE_CORS_ORIGINS (comma-separated full origins).
 *
 * ---
 *
 * MainPG 内嵌模式标记（冻结协议）：
 * - 请求带 `embed=mainpg`：给请求头写 `x-mainpg-embed: 1`，并用 session cookie 记住内嵌状态；
 * - 请求带 `embed=standalone`：清除请求头与 cookie，回到 ClipForge 独立壳；
 * - 后续内部路由不带 query，只凭 cookie 也写入同一请求头 —— 否则点进项目详情就会丢掉
 *   MainPG 主题并重新闪出黑色独立壳。
 * 请求头写给同一跳的 root layout（`headers()` 首屏可读），cookie 写给后续所有请求。
 *
 * Next 16 已用 `proxy.ts` 取代 `middleware.ts`（两者并存会被 Next 判为冲突并直接报错），
 * 因此「本地 CORS」与「MainPG 内嵌标记」两件事共用这一个文件、这一个 matcher。
 */

const LOCAL_ORIGIN = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/;

function allowedOrigin(origin: string | null): string | null {
  if (!origin) return null;
  if (LOCAL_ORIGIN.test(origin)) return origin;
  const extra = (process.env.CLIPFORGE_CORS_ORIGINS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return extra.includes(origin) ? origin : null;
}

function corsHeaders(origin: string, req: NextRequest): Record<string, string> {
  return {
    "Access-Control-Allow-Origin": origin,
    Vary: "Origin",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    // echo whatever headers the preflight asks for (Content-Type today; future-proof)
    "Access-Control-Allow-Headers": req.headers.get("access-control-request-headers") || "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

export function proxy(req: NextRequest) {
  const origin = allowedOrigin(req.headers.get("origin"));

  // 内嵌判定：explicit query 优先，其次才是上一跳留下的 session cookie
  const mode = resolveEmbedMode(
    req.nextUrl.searchParams.get("embed"),
    req.cookies.get(EMBED_COOKIE_NAME)?.value ?? null
  );
  // 由原始请求头完整派生：先 delete 再 set，避免 standalone 时残留上一跳的内嵌标记
  const requestHeaders = new Headers(req.headers);
  requestHeaders.delete(EMBED_HEADER_NAME);
  if (mode === "mainpg") requestHeaders.set(EMBED_HEADER_NAME, EMBED_HEADER_ON);

  // answer preflights here — API routes have no OPTIONS handlers
  if (req.method === "OPTIONS" && origin) {
    return new NextResponse(null, { status: 204, headers: corsHeaders(origin, req) });
  }
  // 请求头靠 Next 的 x-middleware-override-headers 机制回放给本跳的 layout；对 /api/* 无副作用
  const res = NextResponse.next({ request: { headers: requestHeaders } });
  if (mode === "mainpg") {
    res.cookies.set(EMBED_COOKIE_NAME, EMBED_COOKIE_ON, { path: "/", sameSite: "lax" });
  } else if (mode === "standalone") {
    res.cookies.delete(EMBED_COOKIE_NAME);
  }
  if (origin) {
    for (const [k, v] of Object.entries(corsHeaders(origin, req))) res.headers.set(k, v);
  }
  return res;
}

export const config = {
  // "/api/:path*" 已被下面那条负向断言覆盖，两行并列只为显式表达两类职责
  matcher: [
    "/api/:path*",
    // 跳过 Next 内部资源与静态文件后缀；业务路由和 /api/* 一律保留
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|svg|gif|webp|ico|css|js|mjs|map|woff|woff2|ttf|otf)$).*)",
  ],
};
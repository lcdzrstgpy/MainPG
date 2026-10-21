import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// 本地工作台（workbench）在 8010；8011 是 customer-auth 独立服务，不是页面后端。
const BACKEND = "http://127.0.0.1:8010";

/**
 * 开发/预览态把法律页的干净 URL 映射到 public 下的 .html。
 *
 * 生产由本地后端提供 `/privacy` 路由；dev server 没有这层路由，`/privacy` 会被
 * SPA fallback 成 index.html（表现为点隐私政策却回到登录页）。这里在 fallback
 * 之前改写 URL，让开发态与生产行为一致。
 */
function legalPagesPlugin(): Plugin {
  const rewrite = (req: { url?: string }, _res: unknown, next: () => void) => {
    if (req.url === "/privacy" || req.url?.startsWith("/privacy?")) {
      req.url = "/privacy.html";
    }
    next();
  };
  return {
    name: "serve-legal-pages",
    configureServer(server) {
      server.middlewares.use(rewrite as never);
    },
    configurePreviewServer(server) {
      server.middlewares.use(rewrite as never);
    },
  };
}

// 开发模式下页面在 vite(5174) 而 API 在 8010，浏览器会带上 Origin=5174，
// 后端 require_same_origin（更新等接口）要求 Origin 与后端 base_url 同源，
// 否则返回 403。这里把代理转发请求的 Origin 重写为后端源，模拟"页面与 API 同源"的生产语义。
function sameOriginProxy() {
  return {
    target: BACKEND,
    changeOrigin: true,
    configure: (proxy: { on: (event: string, cb: (proxyReq: { setHeader: (name: string, value: string) => void }) => void) => void }) => {
      proxy.on("proxyReq", (proxyReq) => {
        proxyReq.setHeader("origin", BACKEND);
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), legalPagesPlugin()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/desktop": sameOriginProxy(),
      "/api": sameOriginProxy(),
      // 失联提示页的重试探活端点：后端 /health 必须在开发态可代理，
      // 否则 vite 会回 SPA 的 index.html(200)，让重试出现「假成功」。
      "/health": sameOriginProxy(),
      "/plugin": sameOriginProxy(),
      "/local": sameOriginProxy(),
      "/product-processing": sameOriginProxy(),
      // 组合套装模块 API 与本地图床（原图/成品图）。
      "/combo-kit": sameOriginProxy(),
      // 主题商店：后端 /themes/* 代理到 8010。
      "/themes": sameOriginProxy(),
      // 后端静态图床：生成图 / 组合主图以 /pp-media/ 相对 URL 返回，需代理到 8010
      // 才能在前端 <img> 加载（生产同源由 MainPG.exe 直接托管）。
      "/pp-media": sameOriginProxy(),
    },
  },
});

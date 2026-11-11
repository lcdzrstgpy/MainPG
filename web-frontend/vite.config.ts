import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// 本地工作台（workbench）在 8010；8011 是 customer-auth 独立服务，不是页面后端。
const BACKEND = "http://127.0.0.1:8010";

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
  // 子路径部署（如 https://域名/workbench/）时用 WH_WEB_BASE 注入；默认根路径。
  base: process.env.WH_WEB_BASE || "/",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/desktop": sameOriginProxy(),
      "/api": sameOriginProxy(),
      // 失联提示页的重试探活端点：后端 /health 必须在开发态可代理，
      // 否则 vite 会回 SPA 的 index.html(200)，让重试出现「假成功」。
      "/health": sameOriginProxy(),
      // 隐私政策：开发态把它转给本地后端（与打包版一致），后端再转发平台服务器页面
      "/privacy": sameOriginProxy(),
      "/privacy.md": sameOriginProxy(),
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

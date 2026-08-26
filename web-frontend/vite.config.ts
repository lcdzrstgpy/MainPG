import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = "http://127.0.0.1:8010";

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
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/desktop": sameOriginProxy(),
      "/api": sameOriginProxy(),
      "/plugin": sameOriginProxy(),
      "/local": sameOriginProxy(),
      "/product-processing": sameOriginProxy(),
    },
  },
});

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { startRuntimeHeartbeat } from "./transport/runtimeHeartbeat";
import "./shared/styles/global.css";
import "./shared/styles/themes.css";
import "./modules/product_processing/styles/product-processing.css";
import "./shared/styles/apple-workspace.css";

// 桌面端后端存活心跳：在 React 组件树渲染前启动，确保只要 JS 加载成功就上报，
// 业务组件渲染失败也不影响后端存活判定（避免"关页即停"看门狗误杀后端）。
startRuntimeHeartbeat();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

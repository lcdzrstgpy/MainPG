import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// 全局样式必须先于业务组件(App)注入。
// 下载主题(violet/dessert/diamond/quirky/chinese)的 CSS 由 useTheme 模块加载时
// 动态 appendChild 到 <head> 末尾；若此时 themes.css 的 :root 默认变量尚未注入，
// 下载主题的 [data-theme=xxx] 变量会排在其前、被 :root(同优先级 0,1,0) 覆盖，
// 导致焦糖等下载主题回退成经典蓝青。故所有 CSS import 统一提到 App 之前。
import "./shared/styles/design-tokens.css";
import "./shared/styles/global.css";
import "./shared/styles/themes.css";
import "./shared/styles/theme-personality.css";
import "./shared/styles/peach-garden.css";
import "./shared/styles/ink-tap.css";
import "./shared/styles/framework-flow.css";
import "./modules/product_processing/styles/product-processing.css";
import "./modules/ai_video/styles/aiVideoPage.css";
import "./shared/styles/apple-workspace.css";
import "driver.js/dist/driver.css";
import "./shared/styles/guide-tour.css";

import { App } from "./app/App";
import { startRuntimeHeartbeat } from "./transport/runtimeHeartbeat";

// 桌面端后端存活心跳：在 React 组件树渲染前启动，确保只要 JS 加载成功就上报，
// 业务组件渲染失败也不影响后端存活判定（避免"关页即停"看门狗误杀后端）。
startRuntimeHeartbeat();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

# AI 视频复用 ClipForge 前端并替换为 MainPG 视觉方案

> 状态：已确认，交由 DeepSeek 实施  
> 日期：2026-09-23  
> 取代：`2026-09-21-mainpg-native-ai-video-module.md` 中“逐页重做 MainPG 原生视频前端”的路线

## 目标

继续使用仓库中 `integrations/clipforge` 的完整 Next 前端、项目状态、73 个业务接口和媒体处理能力。MainPG 只提供唯一的外层工作台导航与品牌，ClipForge 以 MainPG 的亮色主题、无独立侧栏的内嵌模式运行。

用户在 MainPG 内不得看到 ClipForge 黑色背景、Logo、侧栏、移动顶栏或 iframe 重载闪烁。不得重做创建、脚本、素材、逐镜动态、整片生成、视频、导出、任务和设置页面。

## 已确定的架构

```text
MainPG WorkspaceShell（唯一全局导航、标签页、主题）
  └─ AI 视频模块 AiVideoPage
       └─ 常驻 iframe（ClipForge Next 前端，embed=mainpg）
            ├─ 无 ClipForge AppShell 导航
            ├─ MainPG 亮色设计令牌和页面覆盖样式
            └─ 原有 Next 页面、API、SQLite、FFmpeg、模型调用全部保留

local-runtime
  └─ ClipForgeService（维持当前 loopback sidecar 启动与关闭）
```

不采用“MainPG React 直接重写所有 AI 视频页面并直连视频后端”的路线。当前 MainPG 模块只有导航和 iframe，而 ClipForge 有 18 个页面、73 个 API 路由，并且使用独立的 React、Next 路由、SQLite 与本地媒体能力；直接迁移会重建一整套前端并扩大风险。

## 实施任务

### 1. 让内嵌模式在服务器首屏确定

修改 `integrations/clipforge/src/middleware.ts`、`src/app/layout.tsx`、`src/components/app-shell.tsx` 和嵌入壳测试。

- 新建或扩展 Next middleware：请求带 `embed=mainpg` 时，在请求头写入 `x-mainpg-embed: 1`，并以 session cookie 保存内嵌状态；请求带 `embed=standalone` 时清除该 cookie。后续内部路由不含 query 参数时，middleware 仍根据 cookie 写入同一请求头。
- Root layout 用 `headers()` 在服务端读取 `x-mainpg-embed`，首个 HTML 响应就给 `<html>` 添加 `mainpg-embedded`，并把 `embedded` 作为 prop 传入 `AppShell`。不再由 `useEffect` 在浏览器 mount 后识别 `location.search`，也不在 mount 后才添加/删除 class。
- `AppShell` 用传入的 `embedded` 决定是否输出独立 ClipForge 导航；嵌入时只输出 `mainpg-embed-content` 与页面内容。保留 `?embed=standalone` 作为上游独立运行的显式退出方式。
- 保留现有 standalone 黑色界面行为，但它只能在无内嵌 cookie 或 `embed=standalone` 时出现。

验收：直接打开 `/start?embed=mainpg` 的首帧就是 MainPG 浅色背景；在网络节流和硬刷新下无黑色背景、Logo 或侧栏短暂出现。

### 2. 一次完成所有内嵌页的 MainPG 主题

修改 `integrations/clipforge/src/app/globals.css`，只在 `.mainpg-embedded` 命名空间下调整样式；必要时仅修改 `src/app/start/page.tsx` 中内联的 `cf-*` 样式变量，不改变页面 JSX、表单字段、请求或事件处理。

- 在 `.mainpg-embedded` 设置 MainPG 的背景、文字、卡片、边框、主色、输入框、圆角和阴影令牌；`body`、`.grid-bg`、`.glass-card`、`.cf-root` 必须使用浅色变量。
- 覆盖首页 `cf-root` 的固定深色填充、深色卡片和紫色光晕，保持原有表单、引导、趋势、项目卡片和 loading 结构不变。
- 所有 ClipForge 页面的通用 Tailwind 令牌从根变量继承；逐页检查 `/start`、`/projects`、`/products`、`/presenters`、`/project/clone`、`/batch`、`/settings`、脚本、素材、视频、导出。只有确实写死黑色的局部 class 才添加受限覆盖。
- 不修改 MainPG `WorkspaceShell`、不复制 ClipForge 页面到 `web-frontend`、不引入新的 UI 库。

验收：AI 视频所有导航入口保持同一套 MainPG 亮色底、粉橙主色、卡片和输入控件；在 `embed=mainpg` 下搜索不到可见的 ClipForge logo 或侧栏。

### 3. 保持 iframe，不再因外层导航重建整个应用

修改 `web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx`、其测试，以及 `integrations/clipforge/src/components/app-shell.tsx` 中的轻量通信桥。

- 移除 iframe 的 `key={selected.href}`。外层二级导航不能销毁并重新创建 iframe。
- `AiVideoPage` 保存 iframe ref。点击“视频工作台 / 我的项目 / 商品素材 / 主播素材 / 爆款复刻 / 任务中心 / 设置”时，向已加载 iframe 发送 `{ type: "mainpg:ai-video-navigate", href }`；首次加载前的最后一个目标保存在 ref，收到 ready 后发送。
- 内嵌 `AppShell` 监听该消息，仅接受 `event.source === window.parent` 且 `href` 属于允许的模块入口；使用 Next router 跳转并保留内嵌状态。完成加载和自身路径变化时向父窗口发送 `{ type: "mainpg:ai-video-location", pathname }`。
- MainPG 根据 location 消息更新导航高亮。项目详情路径按归属映射为“我的项目”；不把动态项目详情 URL 当作新的二级导航项。
- 所有 iframe 内部链接依赖 middleware session cookie 维持嵌入模式，避免点击项目、脚本、素材或导出时丢失主题和重新出现独立壳。

验收：外层七个导航切换不创建新 iframe、不触发 sidecar 页面整页 reload；从项目列表进入脚本、素材、视频、导出并返回时，页面始终无黑色闪屏，外层当前项正确。

### 4. 保留运行边界与数据边界

不改 `local-runtime/wh_local/modules/clipforge/service.py` 的 loopback sidecar 管理方式，也不迁移 ClipForge API、数据库、项目数据目录、FFmpeg、TTS 或模型请求到 MainPG。

- `ClipForgeService` 继续启动独立 Next standalone 服务；MainPG 通过既有 `/api/clipforge/status` 和 `/api/clipforge/start` 获取地址。
- 现有项目、上传素材、任务、密钥配置和历史成片数据保持原位置、原字段和原接口。
- 当前 AI 视频业务只允许通过 MainPG 的 iframe 入口访问；独立运行仅保留给开发和上游维护，并通过 `?embed=standalone` 明确启用。

## 必须新增的测试

1. ClipForge middleware/layout 测试：`embed=mainpg` 首次请求把 `mainpg-embedded` 写入服务端 HTML；cookie 续航的内部路由仍为内嵌模式；`embed=standalone` 清除状态。
2. AppShell 测试：内嵌渲染不依赖 `useEffect` 或 `document.documentElement.classList`；不渲染 ClipForge 侧栏、Logo、移动顶栏。
3. MainPG 页面测试：iframe 没有 route key；点击外层导航发送正确消息；收到 location 消息时更新高亮；初次 ready 前的导航会在 ready 后下发。
4. 端到端人工验证：启动 MainPG 和 ClipForge sidecar，硬刷新、快速切换七个入口、进入项目详情四步、浏览器后退前进各一次；录屏确认首帧与跳转期间没有黑色壳。
5. 回归：`pnpm test`、`pnpm build`（ClipForge）；`npm run build`（MainPG）；现有 sidecar pytest；用当前 FFmpeg/FFprobe 环境跑既有媒体测试。

## 完成标准

- MainPG 用户操作 AI 视频时只看到一套 MainPG 导航和视觉。
- ClipForge 的业务 UI、API、数据库和媒体编排仍完整可用，未被复制重写。
- 切换标题页和进入任意项目子页面都不会卸载 iframe 或显示黑色界面。
- `embed=standalone` 仍可用于独立开发和上游维护。

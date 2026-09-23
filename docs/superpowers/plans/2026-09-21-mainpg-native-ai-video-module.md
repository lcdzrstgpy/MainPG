# MainPG 原生 AI 视频模块 Implementation Plan

> 已废弃：请执行 [2026-09-23-ai-video-clipforge-reuse-reskin.md](2026-09-23-ai-video-clipforge-reuse-reskin.md)。当前决定是复用 ClipForge 全部页面与业务能力，只替换内嵌壳层和视觉，不重写 MainPG 原生视频页面。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 AI 视频以 MainPG 原生页面呈现，同时继续使用内置 ClipForge 的视频制作、任务与资产能力。

**Architecture:** MainPG `WorkspaceShell` 保持唯一的全局导航与主题。`AiVideoPage` 提供 MainPG 风格的模块二级导航和容器，并将目标 ClipForge 页面以 `embed=mainpg` 参数载入。ClipForge 检测该模式后移除自身全局导航、顶栏和深色根样式，使用一组受限于嵌入模式的粉色工作台设计令牌；业务页、请求与路由不改动。

**Tech Stack:** React 18 + Vite + TypeScript（MainPG）；Next.js 16 + React 19 + Tailwind CSS（ClipForge）；Node test runner / Vitest。

## Global Constraints

- 只改前端模块壳层与视觉适配；不可改变火山引擎、速创、任务轮询、视频合成或存储请求。
- MainPG 是唯一的全局导航与品牌；ClipForge 在嵌入模式中不得渲染 Logo、桌面侧栏或移动端顶栏。
- 嵌入模式必须通过显式 `embed=mainpg` 参数启用，独立运行 ClipForge 时维持原有界面。
- 不引入新的第三方依赖。

---

### Task 1: 建立 MainPG AI 视频模块容器与二级导航

**Files:**
- Modify: `web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx`
- Modify: `web-frontend/src/modules/ai_video/pages/AiVideoPage.test.ts`
- Create: `web-frontend/src/modules/ai_video/styles/aiVideoPage.css`
- Modify: `web-frontend/src/main.tsx`（若全局样式入口需要导入模块样式）

**Interfaces:**
- Consumes: `ClipForgeStatus` 的 `{ state, url, message }` 与 `getClipForgeStatus()` / `startClipForge()`。
- Produces: `AiVideoPage`，其 iframe 地址为 `${status.url}${separator}embed=mainpg`，并提供 `AiVideoModuleNav` 定义的路由入口。

- [ ] **Step 1: 写出失败的 MainPG 嵌入页契约测试**

在 `AiVideoPage.test.ts` 增加对模块外壳、嵌入参数与全部入口的断言：

```ts
test("AI video is hosted as a MainPG module and requests ClipForge embedded mode", () => {
  assert.match(page, /className="ai-video-page"/);
  assert.match(page, /embed=mainpg/);
  assert.match(page, /视频工作台/);
  assert.match(page, /我的项目/);
  assert.match(page, /商品素材/);
  assert.match(page, /主播素材/);
  assert.match(page, /爆款复刻/);
  assert.match(page, /任务中心/);
  assert.match(page, /设置/);
});
```

- [ ] **Step 2: 运行测试，确认当前页面尚未满足契约**

Run: `node --test src/modules/ai_video/pages/AiVideoPage.test.ts`

Expected: FAIL，缺少 `ai-video-page` 与 `embed=mainpg`。

- [ ] **Step 3: 用最小状态模型实现 MainPG 二级导航**

在 `AiVideoPage.tsx` 定义以下唯一数据源，并通过 `selected.href` 构造 iframe URL；`status.url` 已存在查询串时使用 `&`，否则使用 `?`：

```ts
const AI_VIDEO_NAV = [
  { label: "视频工作台", href: "/start", icon: "✦" },
  { label: "我的项目", href: "/projects", icon: "▣" },
  { label: "商品素材", href: "/products", icon: "◇" },
  { label: "主播素材", href: "/presenters", icon: "♙" },
  { label: "爆款复刻", href: "/project/clone", icon: "↗" },
  { label: "任务中心", href: "/batch", icon: "◌" },
  { label: "设置", href: "/settings", icon: "⚙" },
] as const;

function embeddedClipForgeUrl(baseUrl: string, href: string) {
  const origin = new URL(baseUrl);
  origin.pathname = href;
  origin.searchParams.set("embed", "mainpg");
  return origin.toString();
}
```

渲染 `section.ai-video-page`、标题区、`nav.ai-video-module-nav` 和 `iframe.ai-video-frame`。服务未就绪时保留原启动逻辑，但将提示放在 `ai-video-service-state` 卡片中。

- [ ] **Step 4: 添加模块样式并在样式入口导入**

创建 `aiVideoPage.css`。使用 MainPG 令牌（`--theme-module-surface`、`--theme-module-border`、`--theme-primary`、`--theme-text-primary`），确保：

```css
.ai-video-page { display: grid; gap: 18px; min-width: 0; }
.ai-video-module-nav { display: flex; gap: 8px; overflow-x: auto; padding: 7px; border: 1px solid var(--theme-module-border, #f3cbd6); border-radius: 16px; background: var(--theme-module-surface, rgba(255,255,255,.74)); }
.ai-video-frame { width: 100%; min-height: clamp(680px, calc(100vh - 255px), 1120px); border: 1px solid var(--theme-module-border, #f3cbd6); border-radius: 18px; background: #fff8f8; }
```

在 `@media (max-width: 800px)` 中令页面间距与 iframe 圆角缩小，导航保持横向滚动；不得给 iframe 外层添加固定宽度或黑色背景。

- [ ] **Step 5: 运行测试和类型检查**

Run: `node --test src/modules/ai_video/pages/AiVideoPage.test.ts && npm run build`

Expected: 测试 PASS，TypeScript 与 Vite build 成功。

- [ ] **Step 6: 提交该独立可验证改动**

```bash
git add web-frontend/src/modules/ai_video/pages/AiVideoPage.tsx web-frontend/src/modules/ai_video/pages/AiVideoPage.test.ts web-frontend/src/modules/ai_video/styles/aiVideoPage.css web-frontend/src/main.tsx
git commit -m "feat(ai-video): host video tools in MainPG module shell"
```

### Task 2: 给 ClipForge 添加明确的 MainPG 嵌入模式和主题令牌

**Files:**
- Modify: `integrations/clipforge/src/components/app-shell.tsx`
- Modify: `integrations/clipforge/src/app/globals.css`
- Create: `integrations/clipforge/src/components/__tests__/app-shell-embed.test.ts`

**Interfaces:**
- Consumes: URL 查询参数 `embed=mainpg`。
- Produces: `useMainPGEmbedMode(): boolean`；嵌入模式下仅输出 `<main className="mainpg-embed-content">{children}</main>`，并给 `document.documentElement` 添加 `mainpg-embedded`。

- [ ] **Step 1: 写出失败的 ClipForge 嵌入壳层测试**

新增 `app-shell-embed.test.ts`，读取 `app-shell.tsx` 与 `globals.css` 并断言明确接口与视觉限制：

```ts
test("embedded mode removes the independent ClipForge navigation shell", () => {
  assert.match(shell, /searchParams\.get\("embed"\) === "mainpg"/);
  assert.match(shell, /mainpg-embed-content/);
  assert.match(shell, /classList\.add\("mainpg-embedded"\)/);
  assert.match(css, /\.mainpg-embedded/);
  assert.match(css, /--background: #fff7f8/);
});
```

- [ ] **Step 2: 运行测试，确认当前独立壳层不满足契约**

Run: `pnpm exec vitest run src/components/__tests__/app-shell-embed.test.ts`

Expected: FAIL，找不到嵌入模式分支和主题令牌。

- [ ] **Step 3: 实现嵌入模式检测与无导航渲染分支**

在 `app-shell.tsx` 使用 `useSearchParams`。仅在浏览器端根据查询参数判断：

```tsx
const embedded = useSearchParams().get("embed") === "mainpg";

useEffect(() => {
  document.documentElement.classList.toggle("mainpg-embedded", embedded);
  return () => document.documentElement.classList.remove("mainpg-embedded");
}, [embedded]);

if (embedded) {
  return <main className="mainpg-embed-content">{children}</main>;
}
```

保留原有 `AppShell` 的完整 JSX 作为非嵌入路径，确保单独运行 ClipForge 不变。

- [ ] **Step 4: 为嵌入模式添加受限主题覆盖**

在 `globals.css` 最后新增 `.mainpg-embedded` 作用域，不改动 `.dark` 默认令牌：

```css
.mainpg-embedded {
  --background: #fff7f8;
  --foreground: #49343b;
  --card: rgba(255,255,255,.9);
  --card-foreground: #49343b;
  --primary: #d65778;
  --primary-foreground: #fff;
  --secondary: #fff0f3;
  --muted: #fff4f5;
  --muted-foreground: #8d6e78;
  --accent: #ffe7ee;
  --accent-foreground: #a23556;
  --border: #f0ccd6;
  --input: #efc9d3;
  --ring: #dc7892;
  --sidebar: transparent;
  --radius: 1rem;
}
.mainpg-embedded body { background: #fff7f8; color: #49343b; }
.mainpg-embedded .grid-bg { background-color: #fff7f8; background-image: radial-gradient(circle at 80% 0, rgba(255,183,202,.28), transparent 42%); }
.mainpg-embedded .glass-card { border-color: #f0ccd6; background: rgba(255,255,255,.88); box-shadow: 0 12px 30px rgba(158,72,101,.09); }
```

必要时仅为 `dark` 专用选择器增加 `.mainpg-embedded` 的等权重覆盖，禁止使用全局 `!important`，并确保嵌入内容最小高度为 `100%`、页面背景不再是近黑色。

- [ ] **Step 5: 运行新增测试和 ClipForge 构建**

Run: `pnpm exec vitest run src/components/__tests__/app-shell-embed.test.ts && pnpm build`

Expected: 测试 PASS，Next.js build 成功。

- [ ] **Step 6: 提交该独立可验证改动**

```bash
git add integrations/clipforge/src/components/app-shell.tsx integrations/clipforge/src/app/globals.css integrations/clipforge/src/components/__tests__/app-shell-embed.test.ts
git commit -m "feat(clipforge): add MainPG embedded shell mode"
```

### Task 3: 端到端视觉与功能回归

**Files:**
- Modify: `docs/superpowers/specs/2026-09-21-mainpg-native-ai-video-design.md`（仅当验收行为与设计不一致时记录调整）

**Interfaces:**
- Consumes: Task 1 的 MainPG iframe URL 与 Task 2 的 `embed=mainpg` 渲染分支。
- Produces: 已在 MainPG 浏览器中验证的 AI 视频模块，无重复导航、黑边或双滚动条。

- [ ] **Step 1: 构建 ClipForge standalone 运行时**

Run: `pnpm build && pnpm bundle:standalone`

Expected: standalone 输出更新，MainPG 本地运行时可启动改造后的 ClipForge。

- [ ] **Step 2: 重新启动 MainPG 前后端并确认端口归属**

先使用 `lsof -nP -iTCP:5173 -sTCP:LISTEN`、`lsof -nP -iTCP:8010 -sTCP:LISTEN` 确认旧进程；只终止这些明确 PID。再启动 `python run_workbench.py` 与 `npm run dev`，确认 5173 只由一套 Vite 服务、8010 只由一套 MainPG 后端监听。

- [ ] **Step 3: 在浏览器完成视觉回归**

在 MainPG 打开“AI 视频”，依次点击二级导航的：视频工作台、我的项目、商品素材、主播素材、爆款复刻、任务中心、设置。每一项必须：

1. MainPG 顶部栏、标签页、左侧栏始终只显示一次；
2. ClipForge Logo、左侧栏和移动顶栏不出现；
3. 内容区域使用粉白 MainPG 主题，无黑色矩形画布、无白边、无双滚动条；
4. 页面刷新后仍保持可用，并可创建或打开既有项目。

- [ ] **Step 4: 在窄屏完成视觉回归**

以 800px 及以下视口检查：二级导航可以横向滚动，卡片不溢出，iframe 边框与圆角缩小，创建视频表单可以操作。

- [ ] **Step 5: 运行完整验证并提交验收调整**

Run: `npm run build && pnpm exec vitest run src/components/__tests__/app-shell-embed.test.ts && node --test src/modules/ai_video/pages/AiVideoPage.test.ts`

Expected: 全部 PASS；若视觉验收迫使修改设计文档，单独提交：

```bash
git add docs/superpowers/specs/2026-09-21-mainpg-native-ai-video-design.md
git commit -m "docs: record AI video module acceptance details"
```

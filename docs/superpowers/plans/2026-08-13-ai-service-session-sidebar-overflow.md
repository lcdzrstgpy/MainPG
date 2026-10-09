# AI 会话侧栏溢出修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在低高度窗口中让 AI 服务会话列表独立滚动，避免覆盖本地保存说明。

**Architecture:** 保持会话数据与 JSX 不变，只在现有样式表中明确历史区域的纵向 flex 收缩边界，并将滚动限制在列表本身。一个 Node 静态回归检查验证必要 CSS 声明，Vite 构建验证类型与打包。

**Tech Stack:** React、TypeScript、CSS、Node.js、Vite。

## Global Constraints

- 只修改 AI 服务侧栏布局，不变更文案、会话数据或点击行为。
- 底部“会话与附件仅保存到本机”说明必须保持可见。
- 不添加运行时依赖。

---

### Task 1: 约束会话历史区的溢出

**Files:**

- Create: `web-frontend/scripts/check-ai-session-sidebar-overflow.mjs`
- Modify: `web-frontend/src/modules/ai_service/styles/aiService.css`

**Interfaces:**

- Consumes: `.ai-history-section`、`.ai-history-list` 和 `.ai-local-note` 的现有 CSS 选择器。
- Produces: 历史区作为可收缩列布局，列表承担垂直滚动。

- [x] **Step 1: Write the failing test**

创建 `web-frontend/scripts/check-ai-session-sidebar-overflow.mjs`，读取 CSS 并断言：

```js
assert.match(css, /\\.ai-history-section\\s*\\{[^}]*display:\\s*flex;[^}]*flex-direction:\\s*column;/s);
assert.match(css, /\\.ai-history-list\\s*\\{[^}]*min-height:\\s*0;[^}]*overflow-y:\\s*auto;/s);
```

- [x] **Step 2: Run test to verify it fails**

Run: `node scripts/check-ai-session-sidebar-overflow.mjs`

Expected: failure because neither scoped layout rule currently exists.

- [x] **Step 3: Write minimal implementation**

在 `aiService.css` 的基础会话区规则后加入：

```css
.ai-history-section { display: flex; flex-direction: column; }
.ai-history-list { min-height: 0; overflow-y: auto; }
```

- [x] **Step 4: Run test to verify it passes**

Run: `node scripts/check-ai-session-sidebar-overflow.mjs`

Expected: exit code 0 with the regression-check success message.

- [x] **Step 5: Build the frontend**

Run: `npm run build`

Expected: TypeScript check and Vite build both succeed.

- [ ] **Step 6: Commit**

```bash
git add web-frontend/src/modules/ai_service/styles/aiService.css web-frontend/scripts/check-ai-session-sidebar-overflow.mjs docs/superpowers/plans/2026-08-13-ai-service-session-sidebar-overflow.md
git commit -m "fix(ai-service): prevent session list overlap"
```

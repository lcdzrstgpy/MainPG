# 图搜重新执行按钮悬浮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除图搜队列文案，并在“重新图搜”原按钮被顶部栏遮挡时显示同功能的右下角浮动入口。

**Architecture:** 交互保持在 `SourcingPanel` 内，以原按钮 DOM 的视口位置判断是否被顶部栏遮挡。组件同时监听窗口和应用内容容器的滚动；仅在已有预览结果、原按钮不可见且按钮可执行时渲染浮动副本。两个入口共用 `onStart`、`busy` 与现有 `sourceCount` 禁用规则。

**Tech Stack:** React 18、TypeScript、CSS、Vite。

## Global Constraints

- 不修改图搜 API、候选数据、后端逻辑或全局返回顶部箭头。
- 移除的仅是“待图搜 N 个 SKC”显示元素；`sourceCount` 保留用于既有首次执行文案和禁用判断。
- 浮动入口只在已有 `preview` 时使用“重新图搜”文案，且必须复用原按钮的执行状态和禁用规则。
- 右下角浮动入口不得遮挡 `.scroll-to-top`；桌面端置于其左侧，窄屏端移至其上方。

---

### Task 1: 为货源关联面板添加遮挡感知的重搜入口

**Files:**
- Modify: `web-frontend/src/modules/price_verification/components/SourcingPanel.tsx:1, 108-120, 265-273, 389`
- Modify: `web-frontend/src/modules/price_verification/styles/priceVerificationSource.css:38-64`
- Test: `web-frontend/package.json` 的 `npm run build`（项目未配置单元测试运行器；本任务不新增依赖）

**Interfaces:**
- Consumes: `preview: SourcePreview | null`、`busy: boolean`、`sourceCount?: number`、`onStart: () => void`。
- Produces: 标题操作区的原图搜按钮，以及条件渲染的 `.pv-source-refloat-button`，二者都调用 `onStart()`。

- [ ] **Step 1: 写出可验证的交互检查清单**

在实施前记录以下手工验收用例，避免以静态样式替代滚动行为：

```text
1. 无 preview：不显示“待图搜”文案；只显示原“执行图搜（N）”按钮；不显示浮动按钮。
2. 有 preview 且原按钮在可视区：只显示标题栏“重新图搜”。
3. 有 preview 且原按钮被顶部栏遮挡：显示右下角“重新图搜”，且不遮挡返回顶部箭头。
4. busy 或 sourceCount 为 0：两个重搜入口均不可触发；滚回原位置时浮动入口消失。
5. 分别滚动浏览器窗口和 `.content-card`：第 2、3 项均成立。
```

- [ ] **Step 2: 先运行当前类型与构建校验**

Run: `npm run build`

Expected: 退出码为 `0`；若因现有工作区未提交改动失败，记录错误并在本任务中只修复由本次改动产生的问题。

- [ ] **Step 3: 在 `SourcingPanel` 实现可见性状态和共用禁用条件**

将 React 导入改为包含 `useRef`，在组件内部定义原按钮引用、浮动显示状态以及共用条件；加入下列 effect，监听实际存在的两个滚动来源：

```tsx
const sourceRunButtonRef = useRef<HTMLButtonElement>(null);
const [showRefloatButton, setShowRefloatButton] = useState(false);
const canRunSourceSearch = !busy && (sourceCount ?? 0) > 0;
const canRefloatSourceSearch = Boolean(preview) && canRunSourceSearch;

useEffect(() => {
  const contentCard = document.querySelector<HTMLElement>(".content-card");
  const updateRefloatVisibility = () => {
    const button = sourceRunButtonRef.current;
    if (!button || !canRefloatSourceSearch) {
      setShowRefloatButton(false);
      return;
    }
    const topbarBottom = document.querySelector<HTMLElement>(".topbar-card")?.getBoundingClientRect().bottom ?? 0;
    setShowRefloatButton(button.getBoundingClientRect().bottom <= topbarBottom + 8);
  };

  window.addEventListener("scroll", updateRefloatVisibility, { passive: true });
  contentCard?.addEventListener("scroll", updateRefloatVisibility, { passive: true });
  window.addEventListener("resize", updateRefloatVisibility);
  updateRefloatVisibility();
  return () => {
    window.removeEventListener("scroll", updateRefloatVisibility);
    contentCard?.removeEventListener("scroll", updateRefloatVisibility);
    window.removeEventListener("resize", updateRefloatVisibility);
  };
}, [canRefloatSourceSearch, preview]);
```

给原按钮添加 `ref={sourceRunButtonRef}`，并用 `disabled={!canRunSourceSearch}` 取代内联条件。删除 `<span className="pv-source-queue">…</span>`。在 `section` 结束前插入：

```tsx
<button
  type="button"
  className={`pv-source-refloat-button ${showRefloatButton ? "is-visible" : ""}`}
  onClick={onStart}
  disabled={!canRunSourceSearch}
  aria-label="重新图搜"
>
  {busy ? "图搜执行中…" : "重新图搜"}
</button>
```

- [ ] **Step 4: 编写浮动按钮样式并删除无用队列样式**

删除 `.pv-source-queue` 及其 `strong` 规则，并添加：

```css
.pv-source-refloat-button {
  position: fixed;
  z-index: 79;
  right: 92px;
  bottom: 28px;
  border: 0;
  border-radius: 999px;
  padding: 12px 18px;
  color: #fff;
  background: linear-gradient(105deg, #087bf5, #14c8c0);
  box-shadow: 0 12px 28px rgba(8, 123, 245, .30);
  font-weight: 850;
  font-size: .86rem;
  opacity: 0;
  visibility: hidden;
  transform: translateY(14px) scale(.94);
  pointer-events: none;
  transition: opacity .2s ease, visibility .2s ease, transform .2s ease, box-shadow .2s ease;
}
.pv-source-refloat-button.is-visible { opacity: 1; visibility: visible; transform: translateY(0) scale(1); pointer-events: auto; }
.pv-source-refloat-button:hover:not(:disabled) { transform: translateY(-3px) scale(1.03); box-shadow: 0 16px 32px rgba(8, 123, 245, .38); }
.pv-source-refloat-button:focus-visible { outline: 3px solid rgba(24, 169, 218, .26); outline-offset: 3px; }
.pv-source-refloat-button:disabled { cursor: not-allowed; opacity: .55; }
@media (max-width: 700px) { .pv-source-refloat-button { right: 20px; bottom: 86px; } }
```

- [ ] **Step 5: 运行构建验证类型与 CSS 引用**

Run: `npm run build`

Expected: 退出码为 `0`，输出含 `built in`；TypeScript 不报告未使用变量、`ref` 或 effect 依赖错误。

- [ ] **Step 6: 按交互清单在开发服务器中验证**

Run: `npm run dev`

Expected: Vite 在 `http://127.0.0.1` 启动。打开核价模块并执行第 1 步的五项检查；确认浮动按钮出现时位于返回顶部箭头左侧，窄屏时位于其上方。

- [ ] **Step 7: 提交实现**

```bash
git add web-frontend/src/modules/price_verification/components/SourcingPanel.tsx \
  web-frontend/src/modules/price_verification/styles/priceVerificationSource.css
git commit -m "feat(price-verification): refloat source search action"
```

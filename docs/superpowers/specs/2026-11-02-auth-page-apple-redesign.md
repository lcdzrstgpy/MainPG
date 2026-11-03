# 登录页重做：Apple 式中性排版 + Logo 共享元素过渡

状态：**P0 + P1 已实现并自测通过**（2026-11-02）
日期：2026-11-02
目标读者：前端实现者、验收人
目标基线：`web-frontend/src/modules/customer/pages/AuthPage.tsx`、`web-frontend/src/shared/styles/global.css`

> 实现说明（2026-11-02）
> - 版式、logo FLIP、登录 / 注册 / 找回三态、字段级错误、密码可见均已落地；接口与校验逻辑零改动。
> - 实测：1440×900 首屏 logo 208×122、按钮 220×48、表单列 340；点击「登录」后 logo 逐帧收敛 132→119→97→83→76→72（502 宽视口下命中移动断点），同一 `img` 节点、无跳位。
> - `npm run build`、`npx tsc --noEmit`、`npm test` 通过；仓库内 `ProfitActivityProductsPage.test.ts` 的 1 项失败为改动前既有（断言 `link.images`，与登录页无关）。
> - 已知小瑕疵：在 logo 620ms 过渡途中立刻点「返回」，反向动画会从目标位起步，不追当前中间位；不影响落点。
> - 2026-11-02 修复：表单子项的入场动画（`fill: both`）各自生成层叠上下文，导致账号浮层被下面的行压住——账号块抬到更高层级；并去掉输入框自身的 `:focus-visible` 描边，焦点只由输入组的 `focus-within` 光圈表达。
> - 2026-11-02 移除：按用户要求删掉**应用内账号记忆**（已保存账号浮层、记住密码勾选、`loginCredentials*` 模块），账号密码改由浏览器密码管理器接管——账号 `autocomplete="username"`、登录密码 `current-password`、注册/找回仍为 `new-password`。本地服务端 `/api/login-credentials` 接口保留未动，目前无调用方。

> - 2026-11-03 背景（**当前生效：A 方案**）：恢复原版的「主题渐变 + 两团光斑 + 斜向流光」——`--theme-auth-gradient` 打底、压一层 16% 暗色蒙层保证浅色主题上白字可读，光斑改用 radial-gradient（不用 `blur()`，省实时模糊），`.auth-page::before/::after` 承担光斑与流光；logo 反白（`brightness(0) invert(1)`）；表单重新包一张 `--theme-auth-card-bg` 半透明卡，否则标签/协议在深底上看不清。首屏按钮改为白底墨字。
> - 2026-11-03 备份：先做的「宣纸暖白 + SVG 噪点」B 方案已撤下，原始 CSS 与恢复步骤存在 `.trae/backup/2026-11-03-auth-paper-bg.md`（该目录不入库）。当时评测：色带干净、正文 4.67:1，但整体仍偏白，用户判定不好看。
> - 2026-11-03 修正：禁用态按钮 `#a1a1a6 on #e8e8ed`（约 2.1:1）看不清，改为 `#86868b on #e6e6eb`。
> - 2026-11-03 一屏摆放：注册页 6 个字段在 1610×927 下原本超出 3px、900 高时超出 30px（要滚一下才能看到「已有账号？登录」）。表单区纵向间距改 `clamp()` 随视口高度收缩，页面底部留白也改成流体；再按 900/840/780/740/660 五档收紧导航、品牌、标题与按钮。实测 1610 宽、660–1047 高全部无滚动；登录页同样一屏。
> - 2026-11-03 「← 返回」位置：按用户要求整体上移 40px、右移 50px（`top: -38px`、`left: … + 50px`，仍锚在表单面板左上角）。注意面板高度随步骤变化，所以登录/注册两步里它的绝对高度不同（面板越高，按钮越靠上）。
> - 2026-11-03 配色 A 方案（解决「白块在暖色背景上发蓝」）：卡片不再用固定的冷白 `--theme-auth-card-bg`，改为 `#fffdfb` 打底 + 主题光斑色 46% 的薄涂层；输入区从「纯白块」改成比卡片略深的同色系**输入井**（主题强调色 7% 叠在卡片上），只保留 1px 描边与分隔线；链接、焦点环、禁用按钮改用新增的 `--theme-auth-accent`（每套主题一个暗调值，近白卡片上 ≥4.5:1），不再用苹果蓝 `#0066cc`。深色主题 diamond 因此也拿到了浅色卡片，顺带修掉「深色卡片上标题不可见」（对比 1.01:1）的 bug。
> - 2026-11-03 修复自动填充蓝底：Chrome/Safari 会在自动填充的输入框上画一层不透明底色（Chrome 是 `#e8f0fe`），原先靠 `box-shadow: 0 0 0 60px var(--auth-field-bg) inset` 盖住；输入井改用半透明后盖不住，露出了矩形蓝块。现在每套主题多一个**不透明**的 `--theme-auth-well`（由「卡片色 + 强调色 7%」取样得到），输入井和自动填充遮盖都用它，视觉与半透明版本一致。
> - 2026-11-03 切步动画重排：面板高度随步骤变化时（登录 2 字段 ↔ 注册 6 字段），卡片在布局跳变的瞬间就整块不透明地盖上去，而 logo 的 FLIP 是从旧位置往新位置飞，路径正好穿过卡片顶部——白色书法在白色卡片里彻底看不见。现在：① `.auth-panel-body` 重新挂 `key={step}`，卡片底色（`::before` 的 `auth-card-in`）每次切步都重播，不再「一直不透明」；② 卡片渐显延后到 0.14s 起、0.48s 完成，logo 的 620ms FLIP 大部分发生在卡片还半透的时候；③ 表单内容 0.3s 起、每项 +0.04s、每段 0.4s，注册页最后一项约 0.94s 落位（原为 0.34s 一口气弹完）；④ `.auth-logo-slot` 抬到 `z-index: 3`，任何时刻都压在卡片之上。
> - 2026-11-03 密码可见性按钮：文字「显示/隐藏」换成 18px 描边小眼睛（内联 SVG，眼睛 / 划掉的眼睛两态），保留 `aria-pressed` + `aria-label`（显示密码 / 隐藏密码）与 `title` 提示；字段加 `is-eye` 类，输入框右内边距从 96px 收到 44px；图标改用 `--auth-field-pad-b` 反推垂直位置，中心与输入文字对齐（实测偏差 0.5px）。
> - 2026-11-03 移除导航里的「需要帮助？」：它原本就绑在 `goToStep("forgot")` 上，误点会直接进找回密码流程。现在顶部导航只剩「隐私政策」；找回密码仍从登录页的「忘记密码 ↗」进入。
> - 2026-11-03 Windows 适配复查：① 字体栈补 `"Microsoft YaHei"`（Windows 没有苹方，中文靠系统回退，度量不可控）；② `.auth-page` 改成整页滚动容器（`height:100vh; overflow-y:auto; scrollbar-gutter:stable`），Windows 的 17px 实心滚动条出现时不再让居中布局横向跳；③ `.auth-stage` 用 `justify-content: safe center`，内容高于视口时从顶部开始排而不是向上溢出被裁；④ 删掉卡片上已无视觉意义、但持续吃 GPU 的 `backdrop-filter: blur(20px)`；⑤ 新增 ≤620 / ≤580 两档：标题 20px、副标隐藏、行高 18px、按钮 36/34px，注册页在 620/600/560 高都能一屏放下（1280×630 从溢出 21px 变为 0）；⑥ 「← 返回」在 ≤780 改为 `top:-10px`、≤620 改为 `0`，避免贴到导航条里。

---

## 0. 一句话

把现在的「左品牌区 + 右毛玻璃卡片」双栏登录页，改成 Apple 账户页那种**单列、居中、无卡片、靠留白分级**的中性版式；首屏是「大 logo + 大标题 + 登录按钮」，点登录后 **同一个 logo 元素**缩到 0.44 倍并上移，标题与表单依次淡入，全程只动 `transform / opacity`。

---

## 1. 调研：Apple 登录页到底怎么排的

依据：用户提供的 4 张 `account.apple.com` 截图（1024×615 捕获）。下列尺寸是从截图量出的近似值（±2px），不是 Apple 官方数值；但比例与层级关系是准确的。

### 1.1 结构：三层，没有卡片

| 层 | 内容 | 高度 | 视觉处理 |
|---|---|---|---|
| 全局导航 | 商店 / Mac / iPad … | ≈30px | 12px 黑字，居中 980px |
| 局部导航 | 左「Apple 账户」，右「登录 / 创建你的账户 / 常见问题解答」 | ≈60px | 左 21px/600，右 12px 灰链，**通栏 1px 底分割线** |
| 内容区 | logo + 标题 + 副标 + 表单 | 撑满 | 直接落在 `#fbfbfd` 近白底上，**无卡片、无阴影、无背景图** |

这是最重要的一条：**Apple 的登录页没有卡片**。层级由「中轴对齐 + 字号跳变 + 垂直留白」建立，而不是靠表面材质。

### 1.2 两态排版

| 元素 | 首屏（落地页） | 登录页 |
|---|---|---|
| logo 宽 | ≈210px | ≈100px（0.48 倍） |
| logo 垂直中心 | y≈240 / 615 | y≈187 / 615（**上移 53px**） |
| 主标题 | 40px / 1.15 / 600，「一个账户尽享 Apple 所有服务」 | 32px / 1.2 / 600，「Apple 账户」 |
| 副标 | 19px / 1.55 / `#6e6e73` | 17px / 1.5 / `#6e6e73` |
| 主按钮 | 小胶囊（≈68×34），居中 | 与表单同宽，≈44 高 |
| 表单宽 | — | ≈320px |

### 1.3 表单控件（登录页第二步）

- **输入框是一个「组」**：邮箱 + 密码共用外框，中间只有 1px 分隔线，圆角只在外侧两角（≈12px）。
- **标签常驻**：字段内左上角 11–12px 灰标签，下面 17px 值；不是 placeholder，也不做浮动动画。
- 聚焦：外框变蓝 + 2px 蓝边；未聚焦 `#d2d2d7`。
- 主按钮胶囊（`border-radius: 999px`），禁用态是**降饱和的浅色**而非纯灰。
- 「忘记密码？」右对齐在密码框下方，12px 链接。
- 法务说明是表单下方一整段 **12px 灰字 + 一个线性图标**（蓝色双人），左对齐、与表单同宽。
- 次要登录方式（通行密钥）是并排的黑色胶囊，下方配 12px 补充说明。
- 全页无分割线、无投影，只有垂直间距。

### 1.4 Logo「平滑移动」的真相

Apple 这两屏是**两次独立页面加载**（`account.apple.com` → `/sign-in`），并没有真正的共享元素动画。它看起来连续，是因为三件事同时成立：

1. 中轴完全一致；
2. logo 是同一个图形、同一颜色；
3. 两屏的视觉重心在相近位置，缩放比 0.48、位移仅 53px。

**我们在单页内可以做得比 Apple 更连续**：保留同一个 DOM 节点，用 FLIP 在多步之间做真正的几何插值。这是本方案的技术核心（第 5 节）。

---

## 2. 目标与非目标

### 2.1 目标

| 编号 | 目标 |
|---|---|
| R1 | 单列居中版式，彻底去掉左右分栏、毛玻璃卡片、光斑、按钮流光 |
| R2 | 首屏 = 大 logo + 40px 标题 + 副标 + 主 CTA；登录步 = 小 logo + 28px 标题 + 表单 |
| R3 | logo 在首屏与表单之间**同一元素**平滑缩放上移，无跳变、无重排抖动 |
| R4 | 表单控件改为 Apple 式「输入组 + 常驻标签 + 胶囊按钮」 |
| R5 | 错误从「整块红条」升级为**字段级错误**（红边 + 字段下方 12px 红字），服务端错误仍保留整块提示 |
| R6 | 全部既有业务能力零回归：登录 / 注册 / 找回 / 强制登录 / 隐私记忆 / POD 协议标记 / 消息同步 |
| R7 | 与 9 套主题、`data-ui-mode="apple"`、性能模式、`prefers-reduced-motion` 共存 |

### 2.2 非目标

- ❌ 不做通行密钥 / 扫码登录 / 第三方 OAuth（没有服务端能力，不放假按钮）。
- ❌ 不引入 Tailwind、动画库、路由库；保持 React 18 + 原生 CSS。
- ❌ 不改任何接口、字段、隐私版本号、密码记忆策略（`autoComplete="new-password"` 的应用内接管策略保持现状）。
- ❌ 不动工作台、侧栏、顶栏的样式。
- ❌ 不做深浅色自动切换（本期固定宣纸白；深色版留作后续）。

---

## 3. 已确认决策

| 编号 | 决策 | 来源 |
|---|---|---|
| D1 | 布局走 **A 单列两步**（首屏 Hero → 表单），每次打开都停在首屏 | 2026-11-02 用户确认 |
| D2 | 配色走 **宣纸白 + 墨色**（`#fbfbfd` / `#1d1d1f`），登录页与主题解耦 | 2026-11-02 用户确认 |
| D3 | 主按钮用 **墨黑胶囊**（`#1d1d1f` + 白字） | 2026-11-02 用户确认 |

---

## 4. 信息架构

### 4.1 状态机

```
welcome（首屏）──点「登录」──▶ login ──「创建账户」──▶ register
      ▲                          │                        │
      │                          └──「忘记密码？」──▶ forgot
      └────────「返回」───────────┴────────────────────────┘
```

- 新增 `step: "welcome" | "login" | "register" | "forgot"`，替换现在的 `mode`。
- **logo 只在 welcome ↔ 表单 之间位移**；login / register / forgot 三者互切时 logo 不动，只做内容交叉淡入（约 180ms）。避免每次切 tab 都甩一次 logo。
- 返回 welcome 的入口：表单态左上角一个 12px 的「← 返回」，**与导航栏左缘对齐**（和上方品牌名同一条竖线），用户主动切步后才显示；不要贴着 logo 放，两者挤在一起很难看。

### 4.2 冷启动落在哪一态

**每次打开都落在 welcome 首屏**，不播过渡（首帧直接是最终态）。

> 曾尝试「本机登录过就直达表单」（`mainpg.auth.seen`），2026-11-02 按用户要求撤掉：打开就应该是首屏，而不是莫名停在表单。
> 会话失效（`wh_login_hint`）的提示仍保留，会在用户进入表单后显示在标题下方。

---

## 5. Logo 过渡（核心）

### 5.1 几何目标

桌面（≥1024 宽，≥780 高）：

| | welcome | 表单态 | 变化 |
|---|---|---|---|
| logo 宽 | `clamp(200px, 24vh, 264px)` | 88px | `scale ≈ 0.33–0.44` |
| logo 高（原图 1070×630） | 122px | 52px | |
| logo 顶边 y | 由整块垂直居中决定（1440×900 实测约 236） | 约 174 | 上移 ≈ 60px |
| 水平位置 | 视口中轴 | 视口中轴 | 不变 |

logo 原图是**墨色书法 + 一笔橄榄绿**，白底上直接用原色（删除现有的 `filter: brightness(0) invert(1)`），这是「宣纸白 + 墨色」成立的关键。

### 5.2 实现：单节点 + FLIP

不做两个 `<img>` 互相淡入淡出——那会看起来像「换了个 logo」，而不是「logo 移过去了」。

```
.auth-stage                     position: relative；承载两态的公共坐标系
  .auth-logo-slot              欢迎态占位：208×122；表单态占位：88×52（只有当前态的那个在流内）
  img.auth-logo                position: absolute；正好填满「当前活跃 slot」，由 FLIP 产生位移
```

每次 `step` 变化时：

1. 在上一次布局提交后，记录活跃 slot 的 `getBoundingClientRect()` 到 `prevRectRef`；
2. `useLayoutEffect` 里读取新 slot 的 rect；
3. 先无过渡地把 logo 反演到旧 rect：`translate(dx, dy) scale(prevW / nextW)`（`transform-origin: top left`，避免二次换算）；
4. 下一帧恢复 `transition` 并清空 transform，由浏览器插值到新 rect；
5. `transitionend` 后移除 `will-change`。

这一套与项目已有的滑块配方（`.trae/rules/ui.md` §2）同源：**先测量、首帧不播、只动合成属性**。

纯函数抽出来便于测试：

```ts
// invertRect(prev, next) -> { dx, dy, scale }
export function invertRect(prev: { left: number; top: number; width: number }, next: { left: number; top: number; width: number }) {
  return {
    dx: prev.left - next.left,
    dy: prev.top - next.top,
    scale: next.width === 0 ? 1 : prev.width / next.width,
  };
}
```

### 5.3 时间线（welcome → login，正向）

| t (ms) | 元素 | 属性 | 时长 / 曲线 |
|---|---|---|---|
| 0 | logo | translate + scale | **620ms** `cubic-bezier(.22,1,.36,1)` |
| 0 | 首屏文案（标题/副标/CTA） | opacity 1→0，y 0→-6 | 140ms `ease-out`（先退场） |
| 90 | 表单标题 + 副标 | opacity 0→1，y 8→0 | 340ms 同上 |
| 140 | 输入组 | 同上 | 340ms |
| 180 | 记住账号 / 忘记密码 行 | 同上 | 340ms |
| 220 | 隐私勾选行 | 同上 | 340ms |
| 260 | 主按钮 | 同上 | 340ms |
| 300 | 底部「创建账户」 | 同上 | 340ms |

- 总时长 ≈ **640ms**；曲线只用一条 `--ease-apple: cubic-bezier(.22, 1, .36, 1)`（临界阻尼、无回弹，对应 SwiftUI `Animation.default`）。
- 反向（login → welcome）：logo 520ms，表单先退 120ms，首屏文案 300ms 延迟 60ms 进入。退场永远比入场快。
- 三态互切（login ↔ register ↔ forgot）：logo 不动；旧内容 120ms 淡出、新内容 200ms 淡入 + 6px 位移。

### 5.4 必须不播过渡的场景

| 场景 | 处理 |
|---|---|
| 首帧渲染（首屏） | `data-enter="false"` → 所有 `transition/animation: none`，用户主动切步后才打开 |
| `prefers-reduced-motion: reduce` | 全部时长置 0，只保留最终态 |
| `html[data-perf-mode="on"]`（性能模式） | 同上 |
| 组件卸载 / 切到工作台 | 不做退场动画，直接切（避免多等 600ms 才进工作台） |

### 5.5 性能约束

- 只动 `transform` / `opacity`；不动 `width` / `top`。
- logo 用固定像素容器 + `object-fit: contain`，带 `width`/`height` 属性，避免 CLS。
- 图片 `decode()` 后再允许首次过渡（否则第一次会闪一帧空白）。
- 过渡期间临时 `will-change: transform`，结束后移除，避免长期占用合成层。
- **不做** `filter: blur()` 转场（低端 Windows 集成显卡上会掉帧）。

---

## 6. 版式与控件规格

### 6.1 尺寸表（桌面基准）

| 项 | 值 |
|---|---|
| 页面底色 | `#fbfbfd` |
| 顶部导航 | 高 64px，左右 padding 32px；左「界野」15px/600，右「帮助 · 隐私政策」12px/`#6e6e73` |
| 表单列宽 | **340px**（Apple 用 320；中文标签 + 隐私长句需要多一点） |
| 首屏文案列宽 | 480px |
| 水平对齐 | 全部中轴居中 |
| 垂直 | 两态都**整块垂直居中**（`.auth-stage` 居中 + 3vh/4vh 底部偏置，让内容略高于几何中心）。早期把首屏三段拉到视口上下边缘、表单锚在顶部，结果窗口一高就「下面空一大片」，已废弃 |
| logo | 见 5.1 |
| H1（首屏） | 40px / 1.15 / 600 / `-0.02em` |
| H1（表单） | 28px / 1.2 / 600 / `-0.01em` |
| 副标 | 17px（表单 15px）/ 1.55 / 400 / `#6e6e73` |
| 输入组外框 | 圆角 12px，边框 1px，组内分隔 1px |
| 单个字段高 | 56px（标签 11px + 值 17px） |
| 主按钮 | 340×48px，`radius: 999px`，17px/500 |
| 首屏 CTA | 220×46px，居中 |
| 链接 | 13px，`#0066cc`，hover 下划线 |
| 法务/提示小字 | 12px / 1.5 / `#6e6e73` |
| 间距节奏 | 4/8/12/16/24/32/40，只用这几档 |

### 6.2 输入组

```html
<div class="auth-field-group" data-invalid="false">
  <label class="auth-field">
    <span class="auth-field-label">账号（用户名或邮箱）</span>
    <input />
  </label>
  <label class="auth-field">
    <span class="auth-field-label">密码</span>
    <input type="password" />
    <button class="auth-field-action" aria-label="显示密码">显示</button>
  </label>
</div>
```

- 标签**常驻**，不做浮动动画；placeholder 只用于「name@example.com」这类示例值，字号与值一致。
- 聚焦：整组 `box-shadow: 0 0 0 3px rgba(0,113,227,.16)` + 边框 `#0071e3`。
- 报错：该字段边框 `#d70015`，组下方 12px 红字，`role="alert"`。
- 密码可见切换是新增的小能力（UX 检查项「Password Visibility」）；不影响现有 `autoComplete` 策略。
- 原「已保存账号」下拉已按用户要求移除，账号密码交给浏览器密码管理器（见文首实现说明）。

### 6.3 按钮

| 状态 | 样式 |
|---|---|
| 默认 | `#1d1d1f` 底 / 白字 |
| hover | `#000` 底 |
| active | `scale(.985)`（不改变布局盒） |
| disabled | `#e8e8ed` 底 / `#a1a1a6` 字 / `cursor: not-allowed` |
| busy | 文案变「登录中…」+ 左侧 14px 转圈；按钮宽度锁定，不跳动 |

去掉现有的 `::after` 流光扫过效果（与 Apple 的克制感冲突）。

### 6.4 隐私与法务

保留现有强勾选逻辑（不勾不能提交），但排版改成 Apple 的法务块形态：

- 登录态：`□ 我已阅读并同意《界野隐私政策》` 与 `忘记密码 ↗` **同一行左右分布**（协议 12px，忘记密码 12px + 右上箭头，对应 Apple 的「忘记了密码？↗」外链语义）；注册 / 找回态协议独占一行。链接照旧新窗口打开 `/privacy`。
- 未勾选时按钮禁用 + 下方 12px 灰字「勾选后才能登录」；不再用红色报错条。
- 版本号、记忆逻辑、撤回逻辑全部不动。

### 6.5 会被删掉的既有元素

| 删除 | 原因 |
|---|---|
| `.auth-brand-card` 左侧品牌区 | 改单列 |
| `.auth-form-card` 毛玻璃卡片 | 无卡片 |
| `.auth-halo` 两团光斑 + `auth-shine` 流光 | 视觉噪音 |
| `.auth-tabs` 登录/注册分段 | 改为文字链接切换（Apple 没有分段控件） |
| `eyebrow`（JIEYE ECOMMERCE PLATFORM / WELCOME BACK） | Apple 无 eyebrow，副标承担说明 |
| `.primary-button::after` 扫光 | 与克制感冲突 |
| `.auth-screen::before/::after` 内描边 + 径向光 | 无背景层 |

---

## 7. 设计 token

新增到 `design-tokens.css`（统一以 `--auth-` 前缀，避免与主题变量混淆）：

```css
:root {
  --auth-bg: #fbf8f3;           /* 宣纸暖白（B 方案遗留，A 方案下仅作卡片/回退底） */
  --auth-ink: #1d1d1f;          /* 主文字 / 主按钮底 */
  --auth-ink-secondary: #6e6e73;/* 副标、标签、法务 */
  --auth-ink-tertiary: #86868b; /* 仅装饰性小字 */
  --auth-line: #d2d2d7;         /* 输入框边框 */
  --auth-line-strong: #6e6e73;  /* prefers-contrast: more 时替换 */
  --auth-focus: #0071e3;
  --auth-link: #0066cc;
  --auth-danger: #d70015;
  --auth-field-bg: #ffffff;
  --auth-cta-bg: var(--auth-ink);
  --auth-cta-fg: #ffffff;
  --ease-apple: cubic-bezier(.22, 1, .36, 1);
}
```

### 7.1 对比度（按 `#fbfbfd` 底计算）

| 组合 | 比值 | 结论 |
|---|---|---|
| `#1d1d1f` on `#fbfbfd` | 16.5:1 | AAA |
| `#6e6e73` on `#fbfbfd` | 5.0:1 | AA（正文/标签可用） |
| `#0066cc` on `#fbfbfd` | 5.4:1 | AA |
| 白字 on `#1d1d1f` | 17.3:1 | AAA |
| `#d70015` on `#fbfbfd` | 5.2:1 | AA |
| `#d2d2d7` 边框 on `#fbfbfd` | 1.5:1 | **不满足 1.4.11 的 3:1** |

最后一条是 Apple 自己也没过的项。处理方式：

- 默认保留 `#d2d2d7` 的观感（输入框还有白色填充与页面底色的差）；
- 加 `@media (prefers-contrast: more)` 把 `--auth-line` 切成 `#6e6e73`（5.6:1）；
- 在实现里把「边框」和「焦点环」分开，焦点环必须 ≥3:1（用 `#0071e3`，4.5:1）。

> 刻意**不**为了达标把边框改成 `#8e8e93`——那会让整页变成「表格框」质感，与方案的气质冲突。

---

## 8. 响应式

| 断点 | 调整 |
|---|---|
| ≥1440 宽 | 首屏 logo 208px，标题 40px，间距取上限 |
| 1024–1439 | 不变，表单仍 340px |
| 681–1023 | logo 176px / 表单态 80px；标题 34px |
| ≤680 宽 | logo 132px / 表单态 72px；标题 28px；副标 15px；表单宽 = 100% − 40px；导航右侧只留「隐私政策」 |
| 高度 ≤900 | 表单态纵向间距全部走 `clamp()`（随 vh 收缩）；标题 25px，品牌 76px，按钮 46px |
| 高度 ≤840 | 导航 56px，标题 23px，按钮 44px |
| 高度 ≤780 | 导航 52px，按钮 42px，输入 16px；首屏 logo 160px、标题 34px、顶部间距 56px |
| 高度 ≤740 | 表单态隐藏副标题、品牌缩到 56px，把高度让给字段 |
| 高度 ≤660 | 导航 48px、按钮 38px、字段内边距再收 1px；仍保证一屏 |
| 移动横屏 | 同上，不裁切 |

- 全页禁止横向滚动（`overflow-x: clip` 已有，保留）。
- **目标从「允许滚动」改成「一屏放下」**：注册/找回这类多字段步必须整页可见，不出现滚动条。1610 宽实测 660–1047 高 `scrollHeight === innerHeight`；窄到 630 高才需滚动（极端小窗，不优化）。
- 表单态在 390×844 下必须能滚到主按钮，且底部留 24px 安全边（移动端字段多、字号大，允许滚动）。

---

## 9. 无障碍

- `main` 语义 + 每步只有一个 `h1`（卸载而非隐藏未激活步骤，避免重复标题）。
- logo `alt=""`（相邻 `h1` 已说明品牌，重复朗读无意义）；若首屏没有文字品牌名则用 `alt="界野"`。
- 错误：字段级 `aria-describedby` 指向错误文案，文案容器 `role="alert"`；提交失败后焦点回到第一个出错字段（现有逻辑保留）。
- 按钮 `aria-busy`；密码可见按钮 `aria-pressed` + `aria-label`。
- 键盘：Tab 顺序 = 导航 → logo 返回 → 账号 → 密码 → 显示密码 → 记住 → 忘记 → 隐私 → 提交 → 创建账户；焦点环 3px、offset 2px、对白底可见。
- 输入框字号 17px（≥16px，避免 iOS 聚焦缩放）。
- 所有动画受 `prefers-reduced-motion` 控制。
- 不阻止粘贴（现有代码本来也没拦）。

---

## 10. 与主题 / UI 模式的兼容

| 场景 | 行为 |
|---|---|
| 9 套主题 | 登录页外观**不再随主题变化**；工作台主题照旧。理由：登录页是入口，品牌一致性优先于主题一致性；Apple 自己也是中性页。 |
| `data-ui-mode="apple"` | 无差异（本来就是中性页）。 |
| 下载主题包 | `diamond/theme.css` 里有 `.auth-screen` / `.auth-form-card` 覆盖；根类改名为 `.auth-page` 后自动失效，不影响下载主题本身。 |
| `themes.css` 的 `--theme-auth-*` | **保留不删**（下载主题包仍在引用），只是登录页不再消费。后续做深色版时可复用。 |
| 性能模式 | 关闭 logo 过渡与入场动画（`perf-mode.css` 选择器同步更新）。 |

---

## 11. 文件改动清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `web-frontend/src/modules/customer/pages/AuthPage.tsx` | 重写 JSX + 状态 | 网络/校验/记忆逻辑保持不变；`mode` → `step`，新增字段级错误 |
| `web-frontend/src/modules/customer/pages/authLogoTransition.ts` | 新增（~70 行） | `invertRect` 纯函数 + `useLogoFlip` hook |
| `web-frontend/src/modules/customer/pages/authLogoTransition.test.ts` | 新增 | 测 `invertRect` 的位移/缩放/零宽兜底 |
| `web-frontend/src/shared/styles/auth-apple.css` | 新增（~420 行） | 全部新样式，`auth-` 命名空间 |
| `web-frontend/src/shared/styles/global.css` | 删除 | 第 20–73 行旧 `.auth-*` 块 + 第 617 行里的 `.auth-*` 响应式片段 |
| `web-frontend/src/shared/styles/perf-mode.css` | 修改 | `.auth-form-card` / `.auth-halo` 等选择器换成 `.auth-panel` |
| `web-frontend/src/shared/styles/design-tokens.css` | 追加 | 第 7 节的 `--auth-*` 与 `--ease-apple` |
| `web-frontend/src/main.tsx` | 追加一行 | import `auth-apple.css`（放在 `global.css` 之后） |
| `web-frontend/index.html` | 可选 | `<link rel="preload" as="image" href="/brand/jieye-logo.png">` |

**完全不改**：`privacyConsent.ts`、`transport/http/client.ts`、`App.tsx`、所有接口路径与字段。
（`loginCredentials.ts` / `loginCredentialsModel.ts` 及其测试已随账号记忆一起删除，本地服务端接口未动。）

---

## 12. 验收标准

功能回归（与改造前逐项对照）：

- [ ] 登录成功进入工作台；被顶号时出现「强制登录」并能成功
- [ ] 注册（含邀请码、邮箱验证码 60s 冷却）成功切回登录
- [ ] 忘记密码全流程可用，重置成功后切回登录
- [ ] 浏览器密码管理器可识别：账号 `autocomplete="username"`、登录密码 `current-password`
- [ ] 隐私勾选：首次必须手勾、勾过记住、取消即撤回、版本变更重新征求
- [ ] 会话失效提示正常显示且只消费一次

视觉/动效：

- [ ] 点击「登录」时 logo 无跳变：过渡起点与上一帧位置一致（≤1px）
- [ ] 过渡总时长 620ms ± 30ms；无回弹
- [ ] 首屏文案先退、表单后进，无同屏重叠超过 1 帧
- [ ] 快速连点「登录 / 返回」5 次，无残影、无重复动画、无卡在中间态
- [ ] 1440×900、1024×768、390×844 三档下均无横向滚动，主按钮可见
- [ ] `prefers-reduced-motion: reduce` 与性能模式下完全无动画
- [ ] 登录页不随主题切换变化；工作台主题切换正常
- [ ] 键盘 Tab 可走完全流程，焦点环始终可见
- [ ] `npm run build`、`npm run test`、`npm run verify` 通过

---

## 13. 风险与取舍

| 风险 | 影响 | 处理 |
|---|---|---|
| 登录页与工作台主题脱节 | 主题党会觉得入口「不是我的主题」 | 已确认接受；若后续要跟主题，只需替换 `--auth-*` 一组变量，版式不动 |
| 首屏多一次点击 | 高频用户嫌慢 | 已确认接受：打开即首屏，不再跳过 |
| FLIP 在字体加载前测量会偏 | logo 落点偏移几像素 | 等 `document.fonts.ready` 后再允许首次过渡；并加 `ResizeObserver` 兜底重测 |
| 旧 `.auth-*` 样式被其他模块引用 | 布局塌陷 | 已确认仅 `AuthPage.tsx` 引用；`team-web` 是独立工程，不受影响 |
| 注册/找回字段多，768 高笔记本要滚动 | 体验略打折 | 已改为按视口高度分级压缩，768 高也能一屏放下 |
| 墨色书法 logo 缩放后笔画变细 | 小尺寸发虚 | 源图 1070 宽，88px 显示仍有 12× 余量；`image-rendering: auto` 即可 |

---

## 14. 分阶段落地

| 阶段 | 范围 | 可独立验收 |
|---|---|---|
| P0 | 版式骨架 + logo FLIP + 登录步 + 响应式 | 是（注册/找回暂时沿用旧表单样式也能跑） |
| P1 | 注册 / 找回 / 字段级错误 / 密码可见 | 是 |
| P2 | 无障碍收尾（焦点顺序、prefers-contrast）、首帧防闪、性能模式 | 是 |

建议一次做完 P0+P1，P2 跟在同一轮里收尾；不要分三次上线。

---

## 15. 待确认

1. 首屏文案已定稿（2026-11-02 用户确认）：`h1` = 品牌标语「界无界，野有径」（40px / 0.01em），副标 = 「商品、店铺与订单数据全部留在本机，敏感信息不出电脑」（18px / 列宽上限 500px，桌面单行约 450px）；原「界野电商平台」不再作为大标题，品牌名只保留在导航与 logo 上。
2. 首屏是否保留英文 eyebrow「JIEYE ECOMMERCE PLATFORM」——方案默认删除
3. 表单态左上角「← 返回」是否需要——方案默认保留（仅从首屏进入时显示）

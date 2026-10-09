# AI 视频出片策略选中反馈设计

## 目标

把旧分支 `16cab57a` 中清晰的选中反馈移植到当前 `OutputStrategyPanel`，让用户能立即看出已选择的出片策略和音频策略，同时保持现有 `outputStrategy` 数据模型、业务回调与生成流程不变。

## 方案选择

采用组件内显式状态样式：新增一个小型展示辅助模块，为卡片和胶囊选项返回统一 class，并提供装饰性的 `SelectionCheck`。当前面板根据既有 `active` 布尔值调用辅助模块。

不采用以下方案：

- 直接 cherry-pick `16cab57a`：该提交依赖已经分叉的 `output-scheme` 架构，会把无关旧实现和冲突带进当前分支。
- 通过全局 CSS 匹配 `aria-checked`：虽然改动少，但作用域脆弱，容易影响其它单选按钮，也难以用现有测试约束。

## 范围

本次只修改当前创建表单中的 `OutputStrategyPanel`：

- 出片策略卡片：选中时使用实色主色背景、前景色文字、边框、阴影、轻微放大和右上角 ✓。
- 音频策略胶囊：选中时使用同一套主色反馈，并在文字前显示 ✓。
- 键盘焦点：卡片和胶囊均保留明显的 `focus-visible` 聚焦环。
- 未选中项：保持中性背景，并保留 hover 提示。

不修改输入源、叙事、模板、角色等其它创建选项；不修改任何标识符、默认值、点击处理、ARIA 状态、持久化协议、策略门禁、服务端路由或分镜生成流程。

## 组件边界

新增 `option-state-styles.tsx`：

- `optionCardClass(selected)`：生成大型单选卡片的展示 class。
- `optionChipClass(selected)`：生成胶囊选项的展示 class。
- `SelectionCheck`：只提供视觉确认，不承载语义，设置 `aria-hidden="true"`。

`OutputStrategyPanel` 仍然拥有并计算 `active`，辅助模块不读取状态，也不改变数据流。

## 测试

先新增失败测试，验证：

- 选中样式包含实色主色背景和前景色。
- 未选中样式不包含实色选中背景。
- 两种控件都提供键盘焦点反馈。
- ✓ 是装饰性元素。
- `OutputStrategyPanel` 的两类选项都接入辅助模块，并保持现有 `aria-checked` 与处理函数。

然后运行项目创建组件定向测试、lint 和 ClipForge 构建。若全量测试仍出现已知 FFmpeg 环境失败，将单独如实报告，不把它归因于本次 UI 改动。

# AI 服务会话侧栏溢出修复设计

## 目标

在低高度桌面窗口中，会话列表不得覆盖“会话与附件仅保存到本机”说明卡片；用户仍可浏览全部本地会话。

## 根因

`.ai-history-section` 是可收缩的 flex 子项，但其中的 `.ai-history-list` 没有受限的滚动区域。侧栏高度不足时，列表仍按内容高度绘制，末项会渲染到后续说明卡片的区域中。

## 方案

仅调整 `web-frontend/src/modules/ai_service/styles/aiService.css`：

- 将 `.ai-history-section` 设为纵向 flex 容器，保留其现有的 `flex: 1` 和 `min-height: 0` 收缩能力。
- 将 `.ai-history-list` 设为可收缩且纵向滚动的区域。
- 不改变会话条目、说明文案、数据加载和点击行为。

## 验收标准

- 侧栏在截图所示高度下，最后一个会话条目不会绘制在说明卡片内。
- 会话超出可用高度时，仅会话列表出现垂直滚动；说明卡片保持可见。
- 前端 TypeScript 检查和生产构建成功。

## 测试

为 CSS 规则添加静态回归检查，断言历史区具有纵向 flex 收缩约束、列表具有 `min-height: 0` 与 `overflow-y: auto`。随后运行前端生产构建。

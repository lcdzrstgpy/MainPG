# Combo Kit 缺失页面修复设计

## 目标

恢复前端构建，并保留“商品组合套装”下已有的“提示词模板预设”和“历史组合套装”导航入口。

## 根因

`WorkspaceShell` 与导航配置引用了 `ComboKitPromptPresetPage`、`ComboKitHistoryPage`，但提交中没有加入这两个页面文件，导致 Vite 在模块解析阶段失败。

## 方案

新增两个页面，均放在 `web-frontend/src/modules/combo_kit/pages/`：

- `ComboKitPromptPresetPage`：展示现有 `presetTemplates` 中的模板，支持切换当前激活模板；该选择沿用已有模板状态机制，不新增后端接口。
- `ComboKitHistoryPage`：通过既有 `listSets` 接口加载历史套装，提供名称搜索；点击一项调用 `onOpenSet(setId)` 回到“组合生图”继续编辑。

不调整 `WorkspaceShell` 的路由、导航 ID 或现有组合生图页。

## 可靠性与验证

- 先添加回归测试，断言两个页面模块存在并能被路由导入。
- 为模板切换与历史项目打开分别添加行为测试。
- 运行相关前端测试及 `npm run build`；构建成功是本次修复的完成条件。

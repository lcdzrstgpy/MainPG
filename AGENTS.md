# MainPG 项目记忆

## 工作区职责边界（2026-10-23 确认）

- 本工作区只负责 **客户端**：桌面端运行时 `local-runtime/`、前端 `web-frontend/`，以及客户端对远端计费/账号服务的**契约调用方**。
- **不要查看、修改或 SSH 登录服务器上的服务端实现**（包括 `wh`（111.170.9.65）的 `/opt/wh-workbench/...`、远端 auth-api 的 `auth_server.py` / `billing.py` 等）。这些不属于本工作区职责。
- 需要确认服务端行为时，以客户端契约（`local-runtime/wh_local/customer/remote_client.py`、`local-runtime/wh_local/modules/pod_customization/billing_contract.py`）、接口响应和 `docs/` 下的对接文档为准；无法确认的写成「待服务端确认」，由用户转达，不自行登录服务器核实。
- `local-runtime/wh_local/billing.py` 是服务端计费引擎的本地镜像/参考实现，**不作为服务端事实来源**，只用于理解契约与本地测试。
- 评审、排查和结论只覆盖客户端代码；不要把服务器侧实现细节写进项目文档或结论。

## Git 提交与推送（2026-11-03 确认）

- 所有优化都提交到分支 **`deepseek/POD-change`**（本地已在该分支上开发）。
- **默认只 `git commit`，不 `git push`**。「交到这个分支」「提交到这个分支」只代表本地提交，**不代表授权推送**；必须等用户明确说「推一下 / push」才执行推送。
- 远端有两个，都不许擅自推：
  - `upstream` = `git@github.com:lcdzrstgpy/MainPG.git`（用户指定的仓库）
  - `origin` = `https://github.com/Alon237925/MainPG.git`
- 任何会改写远端历史的操作（`push --force`、`push --force-with-lease`、删除远端分支）都必须先说明影响并单独征得确认。
- 推送前先报告「将要推哪个 remote、哪个分支、哪几个提交」，用户确认后再执行。

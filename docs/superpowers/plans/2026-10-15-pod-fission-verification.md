# POD 爆款裂变：实施验收与发布依赖

日期：2026-10-15。工作分支：`deepseek/POD-change`。代码保留在当前工作区，未创建分支、提交或部署。

## 已实现范围

裂变独立 API、A/B 上传角色、一次联合分析及最多一次契约修复、只读摘要确认、1–52 款、独立构图模板池、自动词表/权重编排、冻结方案、全局能力与规则版本学习均已实现。确认建批次与版本发布使用同一 SQLite 事务；用户未确认时不发布候选。

生成复用公共 POD worker、四宫格拆图、现有 ONNX 超分及 fallback、标题、规格卡、发布和导出。款级实时状态从公共 item/title 记录投影，裂变领域数据保存不可变方案和版本快照。支持部分成功即时查看/导出、失败款重试、暂停/继续/取消和历史批次。

计费画像为 `pod_fission_v1`，本地单款定价复用 `pod_random_v1`；流水显示「POD 爆款裂变」。结算以完整商品化交付的成功款投影，首次结算结果冻结，后续重试单独结算；原始 provider 调用结果保留用于审计。

同一能力/规则版本对的连续三个款级图片生成或产物校验终态失败会回滚，成功款打断连续失败；授权、取消和基础设施错误不计数。历史批次继续使用其冻结快照。自动回滚已拒绝的版本对不会因重复候选发布重新激活，后续回滚也会跳过这些版本对。

## 自动化验证证据

| 验证 | 结果 |
| --- | --- |
| POD 后端完整回归（含裂变、既有模式、计费、迁移、请求限制、应用注册） | 705 passed，3 subtests passed |
| 前端 `npm run verify`（API 路径、Node tests、TypeScript） | 373 passed，exit 0 |
| 前端 `npm run build` | exit 0，Vite 305 modules |
| 独立后端复核 | 回滚版本对、拒绝内容防复活及 `(1,2)` / `(1,3)` 回滚验证通过 |
| `git diff --check` | exit 0 |

后端命令：

```bash
cd /Users/Zhuanz/Desktop/MainPG/local-runtime
WH_LOCAL_DATABASE_PATH=/private/tmp/pod-fission-app-verification.sqlite3 \
  .venv/bin/python -m pytest -p no:capture \
  wh_local/modules/pod_customization/tests \
  tests/test_pod_customization_contracts.py \
  tests/test_pod_customization_billing_flow.py \
  tests/test_pod_customization_billing_contract.py \
  tests/test_pod_customization_migrations.py \
  tests/test_pod_request_limits.py \
  tests/test_pod_customization_remote_billing.py \
  tests/test_pod_customization_app_registration.py -q --tb=short
```

测试使用临时应用数据库；当前环境启用 pytest 默认捕获会退出 139，故显式禁用捕获。仍有一条 FastAPI/Starlette 测试客户端依赖弃用警告。旧规格卡审计测试原先强制开启 logger 传播；当前 pytest 已向该 logger 安装捕获器，同一 LogRecord 会被记录两次。本轮将测试捕获器显式限制为一次，保留每款一条开始/完成日志的原断言，完整回归通过。

## 远端计费服务发布依赖

`RemotePodBillingCoordinator` 复用通用 `freeze_batch_points` / `settle_batch_points` / `batch_freeze_status` 协议。本 checkout 未执行目标远端服务部署，以下事项须由该服务发布时完成：

1. 冻结及结算 profile 白名单接受 `pod_fission_v1`；价格、倍率和随机单价策略与 `pod_random_v1` 保持一致，按款冻结。
2. 接受既有 `pod.title` 与 `pod.image` 调用，以及 `title` / `four_grid` scope；联合分析预留两个 title 调用，未使用项为 `no_return`，沿用既有 title 零增量合同。
3. 冻结状态返回原始 `billing_profile`，授权恢复仍保留 `pod_fission_v1`；账单服务分类及管理端显示识别该画像。
4. 在测试账号验证 1 款全成功、2 款部分失败退款、失败款重试、暂停恢复和重复请求幂等；核对冻结、实扣、退款及标签。
5. 服务端支持及上述联调完成前，不向生产用户发布包含裂变创建入口的客户端。客户端不能绕过远端白名单。

## 尚待测试环境验收

未执行真实豆包/生图调用或实际扣费。自动化测试使用假 provider 和测试授权，不能证明生产模型的视觉质量或远端定价配置。

浏览器验收尝试受环境限制：已安装的 Tabbit CLI 返回 exit 69，未列出可用实例，尚未完成页面视觉和交互验收。

- 上传一组 A/B，确认角色/预览正确；重新上传任一图片后旧摘要失效；摘要不可编辑，不显示内部词表、权重、能力或提示词。
- 分别创建 1 款、多款和 52 款；检查整批上架字段、独立模板池、四图、超分、标题、规格卡和下载/导出。
- 人为使一款失败，确认成功款可立即查看/导出；失败款单独重试不改变其他款和旧方案；核对部分退款和重试结算。
- 检查历史批次、标题修改/重生成、导出选择、暂停/继续/取消、规格卡重印及重新进入后的草稿恢复。
- 用可控测试 provider 连续返回三个图片终态失败，核对学习事件和当前版本指针回退；新批次使用有效版本，旧批次快照不变。

原有 ClipForge 测试/处理代码及 POD `title_runtime.py` 工作区改动已保留，未作为裂变实现的一部分提交。

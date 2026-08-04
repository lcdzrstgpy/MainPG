# 每日选品后端模块

本目录提供宿主无关的数据采集后端：1688 关键词/参考图/相似链接采集、Temu 链接浏览器插件采集、候选规范化与评分、SQLite 批次快照、反馈、确认交接和受控图片读取。模块不注册宿主应用、不创建前端页面，也不写入草稿池或产品库表。

## 安全配置与宿主接线

密钥由宿主的安全设置模块管理。推荐由部署环境向宿主提供下列环境变量，再由 `provider_config_resolver` 转成 `OneBound1688Provider` 的配置映射；每日选品模块本身不直接读取环境变量：

| 环境变量 | Provider 配置键 | 说明 |
| --- | --- | --- |
| `ONEBOUND_1688_API_KEY` | `api_key` | 必填，只在进程内注入 |
| `ONEBOUND_1688_API_SECRET` | `api_secret` | 必填，只在进程内注入 |
| `ONEBOUND_1688_BASE_URL` | `base_url` | 必填 HTTP(S) API 根地址，生产必须使用 HTTPS；不得含 userinfo、query、fragment 或密钥 |
| `ONEBOUND_1688_ENABLED` | `enabled` | 可选布尔值，默认启用 |
| `ONEBOUND_1688_TIMEOUT_SECONDS` | `timeout_seconds` | 可选正数，默认 10 秒 |
| `ONEBOUND_1688_IMAGE_MAX_BYTES` | `image_max_bytes` | 可选正整数，默认 5 MiB |

不要把真实密钥写入本 README、源码、测试、fixture、SQLite、日志、错误信息或版本控制文件，也不要用 `.env` 文件提交密钥。Provider 的 `safe_summary()` 不返回密钥；审计、候选原始载荷、批次 criteria/metadata 会过滤或脱敏敏感字段。生产宿主应从密钥库或受保护的进程环境读取凭据，并通过 `DailySelectionRouteDependencies.provider_config_resolver` 按当前 actor/workspace 解析配置。

宿主创建 `APIRouter`，注入 actor/workspace 解析器、Provider 配置解析器、Provider factory 和 SQLite 路径，然后调用：

```python
register_daily_selection_routes(router, dependencies)
```

`DailySelectionRouteDependencies` 也支持注入已有 repository、预算实现、图片缓存和 run ID factory。模块不会 import 宿主 `app.py`。

## 路由

所有路由均要求宿主提供已认证的 `DailySelectionActor(actor_id, workspace_id)`，批次读写按 workspace 隔离。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/desktop/daily-selection/preview` | 校验条件，采集、筛选、评分并保存批次快照 |
| `POST` | `/desktop/daily-selection/preview-from-1688-link` | 输入 `source_url`，先调用 `item_get`，再以主图优先、标题兜底执行 1688 相似商品采集 |
| `GET` | `/desktop/daily-selection/runs` | 列出当前 workspace 的批次摘要 |
| `GET` | `/desktop/daily-selection/runs/{run_id}` | 回读完整批次与候选快照 |
| `POST` | `/desktop/daily-selection/runs/{run_id}/feedback` | 保存反馈并把候选标记为 rejected |
| `POST` | `/desktop/daily-selection/runs/{run_id}/confirm` | 把候选标记为 confirmed，并幂等创建 handoff |
| `GET` | `/desktop/daily-selection/image?run_id=...&url=...` | 通过宿主注入的安全图片缓存读取已记录 URL |
| `POST` | `/desktop/data-collection/plugin-sessions` | 为当前 workspace 建立浏览器插件会话，返回一次性 session token |
| `POST` | `/desktop/data-collection/temu-link/collect` | 使用 `session_id` 和 Temu 商品链接创建 `temu_link_capture` 命令 |
| `GET` | `/desktop/data-collection/plugin/poll` | 插件以 `session_token` 领取待执行命令 |
| `POST` | `/desktop/data-collection/plugin/results` | 插件回传 `running`、`succeeded` 或 `failed` 结果 |
| `GET` | `/desktop/data-collection/plugin-commands/{command_id}` | 当前 workspace 查询命令及回传结果 |

Temu 链接不会交给 OneBound：它只允许受控浏览器插件在已登录 Temu 页面执行。插件需要识别 `temu_link_capture`，从 payload 的 `source_url` 打开/读取商品详情后，向结果接口回传脱敏的 JSON 数据。当前服务端已实现和原 Demo 一致的 `queued → sent → running → succeeded/failed` 数据流；待拿到插件源码后只需对接这一条命令，不需要迁移 Demo 的用户、权限或其它业务模块。

关键词预览至少提供 1 个、最多 5 个 `keywords`。参考图预览设置 `collection_mode: "image"` 和一个 HTTP(S) `reference_image_url`；图片模式中的 `keywords` 只是描述标签，不会触发第二次关键词搜索。`upload_img` 只为图搜取得图片 ID，不发布商品，也不向 1688 写入商品数据。

## 图片 URL 语义

模块仅保存来源 URL，不把下载图片字节写入候选、审计、SQLite 或 handoff：

- `criteria.reference_image_url`：用户参考图，只存在于图片模式请求和批次条件中。
- `candidate.main_image_url`：候选主图。
- `candidate.source_image_urls`：商品图集；可能包含与主图相同的第一项，消费者应按 URL 去重。
- `candidate.source_detail_image_urls`：商品详情描述图。
- `candidate.source_variant_records[*].image_url`：SKU/规格图，和对应 `sku_id`、属性、价格、MOQ 同属一条记录。

图片读取路由只接受当前 workspace 所属批次中已保存的参考图、主图、商品图、详情图或 SKU 图 URL。宿主图片缓存必须在初始连接和每次重定向前执行传入的目标校验；非 HTTP(S)、带凭据 URL、localhost、私网/回环/链路本地地址及未记录的任意 URL 都会被拒绝。

## SQLite 表归属

每日选品模块当前拥有八张物理表（六张核心表 + 两张插件队列表）。

迁移 `migrations/001_daily_selection.sql` 创建下列五张核心表：

- `daily_selection_runs`：workspace 范围的批次状态、条件、元数据和候选计数。
- `daily_selection_candidates`：完整候选 JSON 快照及用于查询的关键列。
- `daily_selection_feedback`：人工拒绝反馈。
- `daily_selection_provider_budgets`：迁移预留的预算表；默认路由接线当前不读写此表。
- `daily_selection_handoffs`：给下游草稿池/产品库消费者的待处理确认单。

默认路由接线使用 `SQLiteDailyApiBudget`，它会额外创建并实际读写第六张核心表 `daily_selection_api_budget`，按 workspace、Provider 凭据指纹和上海日期保存调用上限与已用次数；两张预算表都不保存原始凭据。

迁移 `migrations/002_data_collection_plugin_queue.sql` 创建两张插件队列表：

- `data_collection_plugin_sessions`：浏览器插件会话（actor_id、workspace_id、一次性 session_token、状态、心跳）。
- `data_collection_plugin_commands`：插件命令队列（`temu_link_capture`，`queued → sent → running → succeeded/failed` 数据流），关联插件会话并支持级联删除。

宿主迁移、备份、恢复和清理时必须把这八张表一起纳入。`daily_selection_provider_budgets` 与 `daily_selection_api_budget` 的并存是当前实现事实；在另行完成生产迁移和数据兼容方案前，不得把两者当作可互换表或删除其中之一。

每日选品模块不拥有、创建或写入 `product_drafts`。宿主与下游模块不得复用上述表保存其他业务对象。

## Handoff 消费契约

确认接口以 `workspace_id + run_id + candidate_id` 幂等；重复确认返回同一条 `DailySelectionHandoff`，数据库最多保留一条记录。新记录状态为 `pending`，`idempotency_key` 是稳定摘要。`payload_json` 是 UTF-8 JSON，包含：

- `candidate`：来源平台、offer、标题、店铺、价格和 MOQ 等身份信息；
- `images`：`main`、`gallery`、`detail`、`sku` URL；
- `skus`：SKU ID、属性、SKU 图、价格和 MOQ；
- `attributes`：来源商品属性；
- `source_evidence`：脱敏 API 审计证据；
- `selection_metadata`：分数、原因、风险、状态、字段完整性和评分组成。

下游消费者必须按 workspace 读取 `pending` 记录，在自己的事务/幂等边界内创建草稿或产品，再把记录更新为 `consumed`；失败可标记为 `failed` 并保留原始 payload 供诊断。消费者应以 `handoff_id` 或 `idempotency_key` 防止重放，并独占 `product_drafts` 等下游表的创建与写入职责。不要通过修改 handoff payload 回写每日选品候选。

## 验收记录

此前经用户授权的低额度真实 API 单次验收已分别验证：关键词搜索与商品详情成功，以及参考图上传后图搜成功。真实响应兼容性包括顶层 `items.item`，图片流程为参考图下载、`upload_img`、`item_search_img`，详情结果可补齐商品图、详情图、SKU 和属性。本任务不重复消耗额度；自动化验收只使用 Fake Provider、临时 SQLite 和 FastAPI `TestClient`，并封锁 DNS/连接入口，零外部网络访问。

真实验收只用于确认供应商协议；回归测试必须继续使用脱敏 fixture/Fake Provider。任何时候都不要为“复现”把 API key、secret、Authorization、Cookie、响应中的敏感值或图片二进制写入文件。

# 数据采集模块（后端 / 数据库交接版）

本目录提供宿主无关的数据采集后端：1688 关键词/参考图/相似链接采集、Temu 链接浏览器插件采集、候选规范化与评分、批次快照、反馈、确认交接和受控图片读取。模块不注册宿主应用、不创建前端页面，也不写入草稿池或产品库表。

本文档用于后端和数据库同事接手。当前默认实现使用 SQLite，业务边界与表结构可原样迁移到项目的正式数据库；不要把 Demo 的整套工作台用户、订单、商品草稿或插件表直接并入本模块。

## 当前项目进度（2026-08-05）

- 已完成 1688 关键词采集、1688 商品链接相似采集，以及 OneBound 图搜的完整链路：安全下载参考图、`upload_img`、`item_search_img`、候选规范化和 1688 商品详情链接生成。
- 图片下载已具备公网 IP 固定连接、重定向限制、5 MiB 上限、MIME 与文件魔数校验；当本机 DNS 返回失败或非公网地址时，会通过固定 TLS 校验的 DoH 回退解析。对于 CDN 明确要求的 `format/avif` 转码，会受控改为 JPEG 后再执行同样校验与上传。
- 已完成 Temu 浏览器插件命令队列、会话隔离、结果回传和脱敏入库接口；浏览器插件源码随本分支发布，负责读取已登录页面并回传商品结构化数据。
- 已完成 SQLite 采集批次、候选、反馈、handoff、调用预算和插件队列表的迁移与接线。前端框架与正式数据库适配由各自模块继续对接；本分支只发布采集后端、浏览器插件和本 README，不包含 Demo、测试或规划文档。

## 三条采集数据流

| 方向 | 调用入口 | 数据来源 | 落库结果 | 与 Demo 的复用关系 |
| --- | --- | --- | --- | --- |
| 1688 图搜 | `POST /desktop/daily-selection/preview`，`collection_mode=image` | OneBound：`upload_img` → `item_search_img` → `item_get` | `daily_selection_runs`、`daily_selection_candidates`，必要时 handoff | 复用 OneBound 图搜调用语义；不复用 Demo 的全局 service/db |
| 1688 相似链接 | `POST /desktop/daily-selection/preview-from-1688-link` | OneBound：链接解析 offerId → `item_get` → 主图图搜，缺图时标题关键词搜 | 同上，种子链接和详情审计写入 `runs.metadata_json` | 复用 Demo 的 1688 链接/offerId 解析思路，调用当前 Provider |
| Temu 商品链接 | `POST /desktop/data-collection/temu-link/collect` | 已登录 Temu 的浏览器插件 | 插件会话和命令结果分别写入 `data_collection_plugin_sessions`、`data_collection_plugin_commands` | 复用 Demo 的命令队列状态机，不迁移 Demo 用户体系 |

```mermaid
flowchart LR
  A["1688 图片 / 链接"] --> B["OneBound Provider"] --> C["候选规范化和评分"] --> D["daily_selection_runs / candidates"]
  E["Temu 商品链接"] --> F["data_collection_plugin_commands"] --> G["已登录 Temu 浏览器插件"] --> H["result_json"]
  D --> I["daily_selection_handoffs"]
```

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
| `POST` | `/desktop/data-collection/plugin-sessions` | 为当前 workspace 建立浏览器插件会话，返回会话 token |
| `POST` | `/desktop/data-collection/temu-link/collect` | 使用 `session_id` 和 Temu 商品链接创建 `temu_link_capture` 命令 |
| `GET` | `/desktop/data-collection/plugin/poll` | 插件以 `session_token` 领取待执行命令 |
| `POST` | `/desktop/data-collection/plugin/results` | 插件回传 `running`、`succeeded` 或 `failed` 结果 |
| `GET` | `/desktop/data-collection/plugin-commands/{command_id}` | 当前 workspace 查询命令及回传结果 |

Temu 链接不会交给 OneBound：它只允许受控浏览器插件在已登录 Temu 页面执行。插件需要识别 `temu_link_capture`，从 payload 的 `source_url` 打开/读取商品详情后，向结果接口回传脱敏的 JSON 数据。当前服务端已实现和原 Demo 一致的 `queued → sent → running → succeeded/failed` 数据流；待拿到插件源码后只需对接这一条命令，不需要迁移 Demo 的用户、权限或其它业务模块。

## 后端与数据库接手范围

后端同事需要保留以下边界：

- API 层只解析当前登录态为 `DailySelectionActor(actor_id, workspace_id)`；所有读取和写入必须带 `workspace_id` 条件。
- 1688 凭据仅由宿主的密钥管理或环境注入；数据库、命令 payload、审计日志和 API 响应不得保存 `api_key`、`api_secret`、Cookie、token。
- Temu 插件仅能领取其自身 `session_token` 对应的命令；创建/查询命令必须同时校验 `actor_id + workspace_id`。
- `result_json` 是插件原始脱敏回传，当前不直接写产品草稿。产品库或草稿池消费方应通过单独的业务接口/事件消费，不能反向修改采集快照。
- `daily_selection_handoffs` 是唯一允许下游产品模块消费的确认出口，必须按 `idempotency_key` 幂等。

### 正式数据库替换清单

当前 SQLite 访问集中在三个类，替换为 MySQL/PostgreSQL/项目统一 ORM 时应保持同名业务方法和事务语义：

| 当前实现 | 责任 | 正式数据库替换要求 |
| --- | --- | --- |
| `DailySelectionRepository` | 批次、候选、反馈、handoff | 所有更新使用同一事务；`workspace_id + run_id` 为读写隔离条件；确认 handoff 保留唯一幂等约束 |
| `SQLiteDailyApiBudget` | 每 workspace/凭据指纹/日期的调用预算 | 用原子 `INSERT ... ON CONFLICT/ON DUPLICATE KEY UPDATE` 或行锁实现 reserve/release，不能先读后写 |
| `DataCollectionPluginQueue` | Temu 插件会话、命令入队、领取、回传 | 领取命令必须原子变更 `queued → sent`；可使用 `FOR UPDATE SKIP LOCKED` 或等价的 CAS 更新 |

推荐把 SQLite 的时间文本改为正式库的 `TIMESTAMP WITH TIME ZONE`（或统一 UTC 时间戳），JSON 文本改为 `JSON/JSONB`。迁移时保留 JSON 字段名称，避免改动插件和前端契约。

### 表、主键和约束

| 表 | 主键 / 唯一约束 | 必须保留的索引或约束 | 数据库负责人 |
| --- | --- | --- | --- |
| `daily_selection_runs` | `(workspace_id, run_id)` | `(workspace_id, created_at DESC, run_id)` | 采集模块 |
| `daily_selection_candidates` | `(workspace_id, run_id, candidate_id)` | FK 到 runs，按 workspace/run 查询索引 | 采集模块 |
| `daily_selection_feedback` | `feedback_id` | FK 到候选；按 workspace/run/candidate 查询索引 | 采集模块 |
| `daily_selection_handoffs` | `handoff_id`；`idempotency_key` 唯一；`(workspace_id, run_id, candidate_id)` 唯一 | 下游消费必须以 idempotency key 去重 | 采集模块创建，下游消费 |
| `daily_selection_api_budget` | `(workspace_id, provider_fingerprint, budget_date)` | 原子递增/释放 | 采集模块 |
| `data_collection_plugin_sessions` | `id`；`session_token` 唯一 | `(workspace_id, actor_id, last_seen_at)` 建议索引 | 采集模块 |
| `data_collection_plugin_commands` | `id` | `(session_id, status, id)`；FK 到插件会话 | 采集模块 |

`daily_selection_provider_budgets` 是早期迁移遗留表，默认代码不读写；正式数据库迁移时先核对现网是否有数据，再决定保留归档或迁移删除，不能把它和 `daily_selection_api_budget` 当成同一张表。

### Temu 插件对接契约

插件领取到的最小命令如下；插件源码到位后只需实现该命令，不需要接入 Demo 的其它命令。

```json
{
  "command_id": 42,
  "command_type": "temu_link_capture",
  "payload": {"source_url": "https://www.temu.com/..."},
  "status": "sent"
}
```

插件应先回传 `running`，完成后回传 `succeeded` 或 `failed`。成功结果至少包含 `source_url`、`title`、`main_image_url`、`price`、`currency`、`variants`、`captured_at`；失败结果至少包含安全的 `error_code` 与 `message`。不得回传登录 Cookie、Authorization、页面完整 HTML、图片二进制或任意密钥。

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

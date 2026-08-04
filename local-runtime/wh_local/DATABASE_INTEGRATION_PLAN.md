# W-H 智能电商工作台数据库整合总文档

本文档用于统一项目各后端模块的数据库字段、表归属、模块边界和后续迁移方向。当前阶段以 SQLite 作为本地开发数据库基座，后续可按相同表结构和字段语义迁移到 MySQL。

## 1. 当前数据库整合目标

当前项目各成员分别负责左侧功能栏中的不同业务模块。数据库整合工作的目标不是替每个模块重写业务逻辑，而是统一以下内容：

1. 统一账号、用户、工作区、店铺等基础主数据。
2. 统一各模块表名、字段类型、状态字段和时间字段。
3. 统一模块之间的数据交接方式，避免直接互相改表。
4. 统一 SQLite 初始化和迁移机制，后续方便迁移到 MySQL。
5. 保证所有业务数据都能按 `workspace_id` 做工作区隔离。

## 2. 总体数据库原则

### 2.1 一个 SQLite 基座，多个模块表

当前阶段建议使用一个统一 SQLite 数据库文件承载本地开发数据。各模块可以保留自己的 repository/service 结构，但建表和迁移应逐步纳入统一初始化流程。

统一数据库基座负责：

- 创建基础表；
- 执行模块迁移；
- 记录迁移版本；
- 管理数据库连接参数；
- 开启 WAL、外键、busy timeout 等 SQLite 配置。

### 2.2 `workspace_id` 是最重要的隔离键

所有与业务数据相关的表，都应尽量包含 `workspace_id`。

原因：

- 不同团队/公司/工作台的数据不能混在一起；
- 登录后用户身份需要和业务数据绑定；
- 后续 MySQL 多租户迁移时，`workspace_id` 可以继续作为隔离字段；
- 产品处理、每日选品、利润活动等模块都需要按工作区查询和写入。

### 2.3 模块只写自己拥有的表

模块之间不要直接互相修改对方的核心业务表。

推荐方式：

- 上游模块通过 handoff / task / queue / API 暴露结果；
- 下游模块消费结果后写入自己的表；
- 如需回写状态，必须通过上游提供的 ACK 接口或状态更新接口完成。

例子：

- 每日选品模块拥有 `daily_selection_*` 表；
- 产品处理模块消费 `daily_selection_handoffs`，但不直接修改每日选品候选表；
- 利润活动模块可以消费产品库或产品处理结果，但不应该直接篡改产品处理草稿表。

## 3. 当前基础表

以下表属于数据库基座或账号登录模块，是其他模块后续对接的基础。

| 表名 | 归属 | 作用 |
| --- | --- | --- |
| `schema_migrations` | 数据库基座 | 记录哪些 SQL/模块迁移已经执行，避免重复建表或重复改表 |
| `workspaces` | 账号/工作区 | 工作区/团队表，作为业务数据隔离基础 |
| `customer_users` | 账号/登录 | 本地用户表，保存用户身份、角色、状态、所属工作区 |
| `customer_sessions` | 账号/登录 | 登录会话表，只保存 token 哈希，不保存明文 token |
| `stores` | 店铺基础信息 | 店铺表，后续可与店铺配置、平台账号绑定 |
| `workbench_settings` | 系统配置 | 普通系统配置项 |
| `secret_values` | 系统配置 | 密钥类配置项，只记录配置状态，不应明文暴露 |
| `action_logs` | 审计日志 | 记录用户在各模块的关键操作 |

### 3.1 建议统一公共字段

后续新增业务表时，建议优先使用以下公共字段：

| 字段 | 建议类型 | 说明 |
| --- | --- | --- |
| `workspace_id` | TEXT / VARCHAR | 工作区隔离键 |
| `created_by` | TEXT / VARCHAR | 创建人用户 ID |
| `created_by_username` | TEXT / VARCHAR | 创建人显示名，可用于列表页减少联表 |
| `status` | TEXT / VARCHAR | 业务状态 |
| `created_at` | TEXT / DATETIME | 创建时间 |
| `updated_at` | TEXT / DATETIME | 更新时间 |
| `metadata_json` | TEXT / JSON | 元数据 |
| `raw_payload_json` | TEXT / JSON | 原始载荷或未稳定字段 |

SQLite 阶段 JSON 可以先使用 `TEXT` 保存 UTF-8 JSON；迁移到 MySQL 后可改为 `JSON` 类型。

## 4. 模块表归属与字段汇总

## 4.1 账号登录模块

当前已完成 SQLite 登录态落库能力，主要涉及：

| 表名 | 作用 |
| --- | --- |
| `workspaces` | 保存工作区信息 |
| `customer_users` | 保存用户信息、角色、状态、所属工作区 |
| `customer_sessions` | 保存登录会话、过期时间、撤销时间、token 哈希 |

当前登录接口返回的核心字段：

| 字段 | 说明 |
| --- | --- |
| `user_id` | 用户 ID |
| `username` | 用户名 |
| `role` | 用户角色，如 `admin`、`operator` |
| `workspace_code` | 工作区编码 |
| `workspace_name` | 工作区名称 |
| `token` | 本地会话 token，前端后续请求接口时放入 Authorization |
| `expires_at` | token 过期时间 |

第二阶段已将 mock 账号服务升级为真实 SQLite 账号服务，新增：

| 表名 | 作用 |
| --- | --- |
| `auth_accounts` | 真实账号主表 |
| `auth_password_credentials` | 密码哈希、盐、算法版本 |
| `auth_login_logs` | 登录成功/失败日志 |

密码不明文保存，当前使用 PBKDF2-HMAC-SHA256，并保存独立 salt、算法名和迭代次数；后续可替换为 bcrypt/argon2。

## 4.2 每日选品 / 数据采集模块

模块目录：`local-runtime/wh_local/data_collection`

当前状态：已纳入统一 SQLite 初始化，包含核心采集表和 Temu 插件队列表。

该模块负责：

- 1688 关键词采集；
- 1688 图搜采集；
- 1688 相似链接采集；
- Temu 浏览器插件采集；
- 候选商品快照；
- 人工反馈；
- 确认交接给下游产品处理模块。

核心表：

| 表名 | 作用 |
| --- | --- |
| `daily_selection_runs` | 每次选品/采集批次 |
| `daily_selection_candidates` | 每个批次下的候选商品 |
| `daily_selection_feedback` | 人工拒绝或反馈记录 |
| `daily_selection_provider_budgets` | 早期迁移预留的 Provider 调用预算表 |
| `daily_selection_api_budget` | 当前实际使用的 API 调用预算表 |
| `daily_selection_handoffs` | 已确认候选商品的下游交接单 |
| `data_collection_plugin_sessions` | 浏览器插件会话 |
| `data_collection_plugin_commands` | 插件命令队列 |

关键字段：

| 表 | 关键字段 |
| --- | --- |
| `daily_selection_runs` | `workspace_id`、`run_id`、`status`、`criteria_json`、`metadata_json`、`candidate_count` |
| `daily_selection_candidates` | `workspace_id`、`run_id`、`candidate_id`、`offer_id`、`source_platform`、`source_url`、`source_title`、`main_image_url`、`price_cny`、`selection_score`、`status`、`raw_candidate_json` |
| `daily_selection_feedback` | `feedback_id`、`workspace_id`、`run_id`、`candidate_id`、`reason`、`details_json` |
| `daily_selection_handoffs` | `handoff_id`、`run_id`、`candidate_id`、`workspace_id`、`payload_json`、`status`、`idempotency_key` |
| `data_collection_plugin_sessions` | `actor_id`、`workspace_id`、`session_token`、`capabilities_json`、`status`、`last_seen_at` |
| `data_collection_plugin_commands` | `session_id`、`command_type`、`payload_json`、`status`、`result_json` |

对接规则：

- 每日选品模块是采集事实的拥有方；
- 下游产品处理模块应消费 `daily_selection_handoffs`；
- 不建议下游直接修改 `daily_selection_candidates`；
- handoff 应按 `idempotency_key` 保持幂等，避免重复生成产品草稿。

## 4.3 产品处理模块

模块目录：`local-runtime/wh_local/modules/product_processing`

当前状态：已新增版本化 SQL 迁移 `modules/product_processing/migrations/001_product_processing.sql`，并纳入统一 SQLite 初始化。

该模块负责：

- 消费每日选品 handoff；
- 建立产品草稿池；
- 草稿编辑、预检、批量处理；
- 来源图片登记；
- 任务历史、结果下载和失败原因记录。

核心表：

| 表名 | 作用 |
| --- | --- |
| `product_processing_drafts` | 产品处理草稿 |
| `product_processing_tasks` | 产品处理任务 |
| `product_processing_task_items` | 任务中的单个商品处理项 |
| `product_processing_daily_selection_intakes` | 旧整批每日选品接收快照 |
| `product_processing_prompts` | 产品处理提示词配置 |
| `product_processing_source_images` | 草稿或任务关联的来源图片 |
| `product_processing_handoff_receipts` | 消费每日选品 handoff 后的回执 |

关键字段：

| 表 | 关键字段 |
| --- | --- |
| `product_processing_drafts` | `id`、`workspace_id`、`source_type`、`source_ref`、`candidate_id`、`selection_run_id`、`handoff_id`、`handoff_idempotency_key`、`skc`、`sku`、`product_name`、`title`、`description`、`image_url`、`image_path`、`cost`、`declared_price`、`status`、`raw_payload_json` |
| `product_processing_tasks` | `id`、`workspace_id`、`title`、`status`、`preflight_only`、`total_count`、`success_count`、`failed_count`、`skipped_count`、`settings_json`、`idempotency_key`、`output_file`、`error_report_file`、`video_manifest_file` |
| `product_processing_task_items` | `task_id`、`product_draft_id`、`skc`、`spu`、`title`、`image_url`、`status`、`reason`、`result_json` |
| `product_processing_handoff_receipts` | `handoff_id`、`idempotency_key`、`workspace_id`、`run_id`、`candidate_id`、`product_draft_id`、`source_status`、`consumer_status`、`payload_sha256` |
| `product_processing_source_images` | `product_draft_id`、`task_id`、`kind`、`url`、`local_path` |

唯一约束建议：

- `product_processing_drafts`: `(workspace_id, candidate_id)`
- `product_processing_tasks`: `(workspace_id, idempotency_key)`
- `product_processing_source_images`: `(product_draft_id, url)`
- `product_processing_handoff_receipts`: `handoff_id`、`idempotency_key`

对接规则：

- 产品处理消费每日选品 handoff，不直接修改每日选品核心表；
- 生成草稿后，在自己的回执表中记录消费结果；
- 在上游 ACK 接口没有正式落地前，不应伪造修改 `daily_selection_handoffs.status`。

## 4.4 利润活动模块

模块目录：`local-runtime/wh_local/modules/profit_activity`

该模块负责：

- 单品利润计算；
- 利润参数保存；
- 产品利润归档；
- 在档产品查询、编辑、删除；
- Excel 产品资料导入；
- 活动报名 Excel 筛选。

核心表：

| 表名 | 作用 |
| --- | --- |
| `profit_activity_settings` | 利润参数设置 |
| `profit_activity_records` | 在档产品及利润快照 |
| `profit_activity_runs` | 活动筛选运行记录 |
| `profit_activity_decisions` | 活动筛选单品判定结果 |
| `profit_activity_import_sessions` | 产品 Excel 导入预览会话 |
| `profit_activity_import_tasks` | 产品导入任务 |
| `profit_activity_filter_tasks` | 活动过滤任务 |

关键字段：

| 表 | 关键字段 |
| --- | --- |
| `profit_activity_settings` | `revision`、`save_root`、`domestic_fee`、`shipping_subsidy`、`refund_rate`、`us_first_mile_rate`、`us_first_mile_fixed`、`co_first_mile_rate`、`co_first_mile_fixed`、`ec_domestic_fee`、`ec_shipping_subsidy`、`ec_shipping_subsidy_price_limit`、`ec_first_mile_rate`、`ec_first_mile_fixed`、`ec_end_fee`、`ec_refund_rate`、`activity_min_net_profit`、`activity_profit_rate_threshold`、`rule_version` |
| `profit_activity_records` | `site_code`、`skc`、`visibility`、`created_by`、`created_by_username`、`image_path`、`source_image_path`、`source_groups_json`、`source_url`、`note`、`selling_price`、`cost_price`、`weight_kg`、`domestic_fee`、`shipping_subsidy`、`refund_rate`、`shipping_cost`、`end_fee`、`total_cost`、`gross_profit`、`net_profit`、`profit_rate`、`calculation_hash`、`settings_revision`、`revision` |
| `profit_activity_runs` | `site_code`、`rule_version`、`minimum_net_profit`、`minimum_profit_rate`、`retained_count`、`excluded_count` |
| `profit_activity_decisions` | `run_id`、`record_id`、`decision`、`reason_code` |
| `profit_activity_import_sessions` | `import_id`、`original_filename`、`site`、`rows_json` |
| `profit_activity_import_tasks` | `import_id`、`status`、`result_json` |
| `profit_activity_filter_tasks` | `status`、`result_json` |

当前风险点：

- 当前利润活动模块唯一约束为 `site_code + skc`；
- 后续多工作区/多公司场景应调整为 `workspace_id + site_code + skc`；
- `created_by` 当前为本地默认值，后续应接入账号登录模块提供的真实用户 ID；
- 图片字段当前保存本地路径，后续可逐步抽象为统一资产表。

## 5. 跨模块数据流

当前推荐的数据流如下：

```mermaid
flowchart LR
    A["用户登录"] --> B["获得 token、user_id、workspace_id"]
    B --> C["每日选品 / 数据采集"]
    C --> D["daily_selection_runs / candidates"]
    D --> E["用户确认候选"]
    E --> F["daily_selection_handoffs"]
    F --> G["产品处理消费 handoff"]
    G --> H["product_processing_drafts"]
    H --> I["产品处理任务"]
    I --> J["利润活动读取产品/SKC/价格/成本/重量"]
    J --> K["profit_activity_records"]
    K --> L["活动过滤结果"]
```

## 6. 当前数据库负责人需要推进的事项

### 6.1 已完成

- 已建立 SQLite 基础表；
- 已建立账号登录会话持久化；
- 已建立本地真实 SQLite 账号服务；
- 已新增 `auth_accounts`、`auth_password_credentials`、`auth_login_logs`；
- 已接入 `workspace_id`、用户、会话等基础结构；
- 已将每日选品核心采集表纳入统一初始化；
- 已将每日选品 Temu 插件队列表纳入统一初始化；
- 已将产品处理 7 张表纳入统一初始化；
- 已确认利润活动模块字段说明文档。

### 6.2 待推进

1. 将利润活动模块当前 SQLAlchemy 自建表方式纳入统一数据库文件。
2. 为利润活动表补充 `workspace_id` 设计方案。
3. 统一 `created_by`、`workspace_id`、权限上下文，让各模块都能从登录态获得当前用户。
4. 与尚未上传字段说明的模块负责人确认字段，例如：
   - 每日运营；
   - 精致作图；
   - 核价及货源；
   - 员工管理；
   - 店铺配置。

## 7. SQLite 到 MySQL 的迁移方向

当前 SQLite 字段设计应尽量保持 MySQL 可迁移。

| SQLite 阶段 | MySQL 阶段 |
| --- | --- |
| `TEXT` 保存 JSON | `JSON` |
| `TEXT` 保存时间 | `DATETIME` 或 `TIMESTAMP` |
| `TEXT` 保存金额 | `DECIMAL(12,4)` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT AUTO_INCREMENT` |
| `schema_migrations` | 继续保留迁移记录表 |
| `workspace_id` 文本隔离 | 可继续使用 VARCHAR，或改为 BIGINT 外键 |

迁移时应优先保证业务字段语义不变，再考虑类型优化。

## 8. 对组员的字段对齐要求

各模块负责人补充功能说明文档时，至少需要提供：

1. 模块负责的业务范围；
2. 模块拥有的表；
3. 每张表的字段、类型、是否必填；
4. 主键、唯一约束、索引；
5. 是否需要 `workspace_id`；
6. 是否需要 `created_by`；
7. 模块会消费哪些上游表或接口；
8. 模块会输出哪些下游接口或 handoff；
9. 哪些字段当前先放入 JSON，哪些字段需要单独建列。

## 9. 当前结论

当前项目已经具备建立统一 SQLite 数据库基座的条件。数据库负责人后续应重点推进三件事：

1. 统一基础身份：用户、工作区、店铺、登录态。
2. 统一模块迁移：把各模块表结构纳入版本化迁移。
3. 统一跨模块交接：每日选品通过 handoff 给产品处理，产品处理输出草稿/任务，利润活动消费产品和 SKC 利润数据。

短期以 SQLite 跑通本地完整链路；中期把各模块表结构统一；长期迁移到 MySQL 时保持接口和字段语义不变。

# SQLite 数据库基座说明

## 1. 当前目标

本文件说明本地运行时 SQLite 数据库的基础设计。当前阶段先统一数据库入口、公共基础表和模块迁移执行方式，后续各业务模块再按自己的功能文档补充字段和接口。

数据库基座负责：

- 统一 SQLite 数据库文件；
- 统一连接参数；
- 统一初始化入口；
- 统一工作区、用户、登录会话、店铺等公共表；
- 统一记录模块迁移执行状态；
- 为每日选品、利润活动、系统配置等模块预留对接边界。

## 2. 数据库文件位置

默认数据库路径来自 `wh_local.config.default_config()`：

```text
outputs/wh-local/workbench.sqlite3
```

也可以通过环境变量覆盖：

```text
WH_LOCAL_DATABASE_PATH
WH_LOCAL_DATA_DIR
```

当前连接统一启用：

- `PRAGMA journal_mode=WAL`
- `PRAGMA foreign_keys=ON`
- `PRAGMA busy_timeout=30000`

## 3. 初始化入口

统一初始化函数：

```python
from wh_local.db import init_db

init_db(database_path)
```

主应用入口 `local-runtime/wh_local/app/main.py` 已在创建 FastAPI 应用时调用 `init_db(db_path)`。

## 4. 公共基础表

### `schema_migrations`

记录已执行的模块迁移，避免重复执行 SQL 文件。

| 字段 | 说明 |
| --- | --- |
| `migration_id` | 迁移唯一编号 |
| `module` | 所属模块 |
| `applied_at` | 执行时间 |

### `workspaces`

工作区/团队表。后续所有业务数据建议优先通过 `workspace_id` 隔离。

| 字段 | 说明 |
| --- | --- |
| `workspace_id` | 工作区主键 |
| `workspace_code` | 工作区编码 |
| `workspace_name` | 工作区名称 |
| `status` | 状态 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

初始化时会自动创建一个默认工作区：

```text
workspace_id = default
workspace_code = local-demo
workspace_name = 本地演示工作区
```

### `customer_users`

用户表，承接注册登录模块，也给其他模块提供 `created_by`、`owner_user_id` 等关联字段。

| 字段 | 说明 |
| --- | --- |
| `user_id` | 用户主键 |
| `remote_customer_id` | 远端账号 ID，暂可为空 |
| `username` | 用户名 |
| `email` | 邮箱 |
| `password_hash` | 密码哈希，本地账号模式使用 |
| `role` | 角色，建议 `admin` / `operator` |
| `workspace_id` | 所属工作区 |
| `account_status` | 账号状态 |
| `remote_session_expires_at` | 远端登录态过期时间 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

### `customer_sessions`

登录会话表。注意：只保存 `token_hash`，不保存明文 token。

| 字段 | 说明 |
| --- | --- |
| `session_id` | 会话主键 |
| `user_id` | 用户 ID |
| `token_hash` | 本地 token 的哈希值 |
| `expires_at` | 过期时间 |
| `revoked_at` | 注销时间 |
| `last_used_at` | 最近使用时间 |
| `created_at` | 创建时间 |
| `user_agent` | 客户端标识 |
| `client_ip` | 客户端 IP |

当前实现文件：

```text
local-runtime/wh_local/customer/db_store.py
```

登录成功后会：

1. 写入或更新 `workspaces`；
2. 写入或更新 `customer_users`；
3. 把本地 `wh_local_xxx` token 哈希后写入 `customer_sessions`；
4. `/api/customer/me` 根据 Bearer token 查询当前用户；
5. `/api/customer/logout` 将 session 标记为已注销。

### `stores`

店铺表。每日运营、产品处理、利润活动、核价及货源等模块都可以通过 `store_id` 或 `workspace_id` 关联。

| 字段 | 说明 |
| --- | --- |
| `store_id` | 店铺主键 |
| `workspace_id` | 所属工作区 |
| `store_name` | 店铺名称 |
| `platform` | 平台 |
| `site_code` | 站点编码 |
| `status` | 状态 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 5. 已纳入统一初始化的模块

### 系统配置

当前系统配置使用以下表：

- `workbench_settings`
- `secret_values`
- `action_logs`

其中 `action_logs` 已补充：

- `workspace_id`
- `module`

方便后续按工作区、模块审计操作。

### 每日选品 / 数据采集

当前已将以下 SQL 迁移纳入 `init_db()`：

```text
local-runtime/wh_local/data_collection/migrations/001_daily_selection.sql
```

执行后创建：

- `daily_selection_runs`
- `daily_selection_candidates`
- `daily_selection_feedback`
- `daily_selection_provider_budgets`
- `daily_selection_handoffs`

说明：每日选品文档中还提到 `daily_selection_api_budget` 由运行时预算类额外创建，目前本基座未强行接管该运行时动态表。

## 6. 暂未强行合并的模块

### 利润活动

利润活动模块当前有自己的 SQLAlchemy 数据库入口：

```text
local-runtime/wh_local/modules/profit_activity/infrastructure/database.py
```

默认数据库：

```text
real-workbench/employee_workbench/data/profit_activity.db
```

原因：利润活动模块已经有完整 ORM、仓储和测试约定。当前数据库基座先不强行改它，避免破坏组员模块。后续整合时可以由数据库负责人和利润活动负责人确认是否改成统一：

```text
WH_LOCAL_DATABASE_PATH / outputs/wh-local/workbench.sqlite3
```

## 7. 后续模块接入规范

各业务模块新增表时建议遵守：

- 表名使用模块前缀，例如 `product_processing_tasks`；
- 每张业务主表建议包含 `workspace_id`；
- 涉及店铺的数据建议包含 `store_id`；
- 涉及用户创建/修改的数据建议包含 `created_by` / `updated_by`；
- 状态字段统一使用 `status`；
- 时间字段统一使用 ISO 字符串，字段名为 `created_at`、`updated_at`；
- 大块不稳定结构可先用 `payload_json`、`metadata_json`、`result_json` 过渡；
- 敏感信息不保存明文，密钥继续走 `secret_values`。

建议每个模块负责人提交字段时按以下格式说明：

```text
模块名：
表名：
字段名：
字段类型：
是否必填：
是否关联 workspace/user/store：
状态流转：
是否需要历史记录：
是否需要批次表：
```

## 8. 当前待办

- 与前端确认是否直接启用 `/api/customer/login`、`/api/customer/me`、`/api/customer/logout`；
- 配置正式账号服务地址 `WH_LOCAL_CUSTOMER_AUTH_BASE_URL`；
- 与利润活动负责人确认是否迁移到统一 SQLite；
- 与产品处理、精致作图、核价及货源负责人确认字段；
- 给每个模块补充独立迁移 SQL；
- 补充数据库初始化测试。

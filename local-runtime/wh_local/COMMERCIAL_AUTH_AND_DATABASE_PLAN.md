# W-H 智能电商工作台商业化账号与数据库规划

本文档用于对齐 W-H 智能电商工作台在“打包交付给电商客户”场景下的账号体系、租户隔离、数据库基座、安全要求和后续演进路线。

当前项目已经具备 SQLite 数据库基础、账号注册登录基础链路、远端账号服务基础版和临时前端演示页。但若作为可售卖产品交付给电商客户，还需要补齐多租户隔离、角色权限、邮箱找回、授权许可、部署运维和审计能力。

## 1. 商业化定位

本项目不是单一内部工具，而是面向电商团队交付的工作台产品。账号与数据库设计需要支持以下使用场景：

- 一个客户公司拥有一个独立工作区；
- 一个工作区内存在多个员工账号；
- 不同员工按角色访问不同模块；
- 不同客户公司的数据必须严格隔离；
- 系统可以本地部署，也可以后续升级为云端部署；
- 数据库初期使用 SQLite，后续可以迁移到 MySQL/PostgreSQL；
- 系统具备授权控制，避免安装包被无限复制；
- 系统具备安全审计，便于客户排查操作风险。

## 2. 当前已完成能力

### 2.1 SQLite 数据库基座

已完成统一 SQLite 初始化和模块迁移机制，当前基础库包含：

- `schema_migrations`：迁移记录；
- `workspaces`：工作区/租户；
- `customer_users`：本地工作台用户镜像；
- `customer_sessions`：本地工作台登录会话；
- `auth_accounts`：远端平台账号；
- `auth_password_credentials`：账号密码凭证；
- `auth_platform_sessions`：远端账号服务会话；
- `auth_password_reset_tokens`：密码重置凭证；
- `auth_security_events`：账号安全事件；
- `permissions`：权限点；
- `roles`：正式角色字典；
- `role_permissions`：角色权限；
- `user_roles`：用户-角色关联；
- `user_permission_overrides`：用户权限覆盖；
- `auth_email_verifications`：邮箱验证凭证；
- `account_invitations`：员工邀请；
- `license_state`：商业授权状态；
- `license_activation_logs`：授权激活日志；
- `stores`：店铺；
- `workbench_settings`：工作台配置；
- `secret_values`：敏感配置；
- `action_logs`：操作日志。

同时已陆续整合每日选品、产品处理、核价及货源、利润活动等业务模块的 SQLite 表结构。

### 2.2 账号登录链路

已完成以下接口：

| 接口 | 状态 | 说明 |
|---|---|---|
| `POST /api/customer/register` | 已完成 | 注册账号 |
| `POST /api/customer/login` | 已完成 | 账号密码登录 |
| `GET /api/customer/me` | 已完成 | 查询当前登录态 |
| `POST /api/customer/logout` | 已完成 | 退出登录 |
| `POST /api/customer/change-password` | 已完成 | 已登录用户修改密码 |
| `POST /api/customer/forgot-password` | 开发版 | 生成一次性重置凭证 |
| `POST /api/customer/reset-password` | 已完成 | 使用一次性凭证重置密码 |

### 2.3 远端平台账号服务

当前已具备独立远端账号服务：

- 服务进程：`wh-customer-auth.service`
- 对外路径：`https://workbench.haocoming.top/auth-api`
- 本地数据库：`/opt/wh-workbench/data/customer-auth.sqlite3`
- token 前缀：`wh_auth_*`
- 数据库只保存 token hash，不保存明文 token。

工作台后端通过：

```text
WH_LOCAL_CUSTOMER_AUTH_BASE_URL=https://workbench.haocoming.top/auth-api
```

调用远端账号服务，并在本地生成工作台业务会话：

- 本地 token 前缀：`wh_local_*`
- 本地数据库：`/opt/wh-workbench/data/workbench.sqlite3`
- 本地会话只保存 token hash。

### 2.4 临时前端演示页

当前临时前端已拆成接近真实用户流程的页面：

- `/dev/auth/login`
- `/dev/auth/register`
- `/dev/auth/forgot-password`
- `/dev/auth/reset-password`
- `/dev/auth/dashboard`
- `/dev/auth/change-password`

该页面仅用于阶段演示，不应作为正式前端长期开放。

## 3. 商业化必须补齐的能力

### 3.1 多租户隔离

商业化交付最重要的安全边界是租户隔离。所有客户公司之间的数据必须通过 `workspace_id` 隔离。

要求：

- 所有业务表必须包含 `workspace_id`；
- 所有新增数据必须写入当前登录用户所属 `workspace_id`；
- 所有查询必须带 `workspace_id` 条件；
- 所有更新/删除必须校验当前用户是否属于该 `workspace_id`；
- 禁止前端直接传入任意 `workspace_id` 并被后端信任；
- 后端应从登录 session 中解析当前用户和工作区。

推荐所有业务表基础字段：

| 字段 | 说明 |
|---|---|
| `workspace_id` | 当前客户公司/工作区 |
| `created_by` | 创建人用户 ID |
| `updated_by` | 更新人用户 ID |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |
| `deleted_at` | 软删除时间，按需 |

### 3.2 正式角色权限体系

当前已有权限点基础表，但商业化需要明确角色模型。

建议默认角色：

| 角色 | 用途 |
|---|---|
| `owner` | 客户老板/超级管理员 |
| `admin` | 管理员 |
| `operator` | 日常运营 |
| `product_specialist` | 产品处理人员 |
| `designer` | 精致作图人员 |
| `pricing_specialist` | 核价及货源人员 |
| `finance` | 利润活动/成本利润人员 |
| `viewer` | 只读观察员 |

建议权限命名：

```text
module.action
```

示例：

| 权限点 | 说明 |
|---|---|
| `daily_selection.read` | 查看每日选品 |
| `daily_selection.write` | 发起选品采集 |
| `product_processing.read` | 查看产品处理 |
| `product_processing.write` | 生成产品资料 |
| `price_verification.read` | 查看核价及货源 |
| `price_verification.write` | 采集核价与货源 |
| `profit_activity.read` | 查看利润活动 |
| `profit_activity.write` | 维护利润活动 |
| `settings.read` | 查看系统配置 |
| `settings.write` | 修改系统配置 |
| `employee.manage` | 管理员工账号 |
| `store.manage` | 管理店铺 |

当前已具备：

- `roles` 角色字典表；
- `user_roles` 用户-角色关联表；
- 默认角色种子；
- `owner` 默认拥有全部权限；
- `admin` 默认拥有全部权限；
- `viewer` 默认拥有只读权限；
- 普通自注册用户强制落为 `operator`。

后续需要补：

- 管理员分配角色接口；
- 各业务接口统一鉴权依赖。

### 3.3 正式邮箱找回密码

当前 `forgot-password` 是开发演示版，会把 `reset_token` 返回给前端。商业化产品不能这样做。

正式流程应为：

```text
用户输入邮箱
系统生成 reset token
数据库保存 token_hash
系统发送重置链接到邮箱
用户点击链接进入 /reset-password?token=xxx
用户设置新密码
token 标记 used_at
旧 session 全部失效
```

需要新增/完善：

- 邮箱服务配置表；
- 邮件发送适配器；
- 邮箱模板；
- 重置链接域名配置；
- 请求频率限制；
- 重置 token 过期策略；
- 重置成功后安全通知邮件。

### 3.4 注册与邀请机制

商业化产品不建议任何用户自行注册为 `admin`。

建议：

- 首个 owner 由安装初始化脚本创建；
- 普通员工由 owner/admin 邀请；
- 员工通过邀请链接设置密码；
- 注册页默认只能创建普通 `operator`，或仅用于 SaaS 申请试用；
- 管理员角色不得由前端普通注册页面自选。

建议新增表：

```sql
account_invitations
```

字段建议：

| 字段 | 说明 |
|---|---|
| `invitation_id` | 邀请 ID |
| `workspace_id` | 所属工作区 |
| `email` | 被邀请邮箱 |
| `role` | 预设角色 |
| `token_hash` | 邀请 token 哈希 |
| `expires_at` | 过期时间 |
| `accepted_at` | 接受时间 |
| `created_by` | 邀请人 |
| `created_at` | 创建时间 |

### 3.5 授权许可 License

如果项目要打包卖给电商客户，必须考虑授权控制。

建议新增表：

```sql
license_state
```

字段建议：

| 字段 | 说明 |
|---|---|
| `license_id` | 授权 ID |
| `license_key_hash` | 授权码哈希 |
| `company_name` | 客户公司 |
| `workspace_limit` | 工作区数量限制 |
| `user_limit` | 用户数量限制 |
| `enabled_modules_json` | 可用模块 |
| `expires_at` | 到期时间 |
| `machine_fingerprint` | 机器指纹 |
| `domain` | 绑定域名 |
| `status` | active/expired/disabled |
| `activated_at` | 激活时间 |
| `last_checked_at` | 最近校验时间 |

License 校验应覆盖：

- 系统启动时；
- 用户登录时；
- 新增员工时；
- 使用高级模块时；
- 定期后台检查。

### 3.6 操作审计

商业化客户需要知道“谁在什么时候做了什么”。

当前已有 `action_logs` 和 `auth_security_events`，后续应统一审计规范。

建议审计事件：

- 登录成功/失败；
- 退出登录；
- 修改密码；
- 重置密码；
- 新增/禁用员工；
- 修改角色；
- 修改系统配置；
- 导入 Excel；
- 导出数据；
- 删除数据；
- 批量采集；
- 批量生成内容；
- 核价采集；
- 利润活动导出。

## 4. 数据库商业化目标结构

建议后续形成以下核心表分组。

### 4.1 租户与组织

- `workspaces`
- `stores`
- `workspace_members`
- `workspace_settings`

### 4.2 账号与权限

- `auth_accounts`
- `auth_password_credentials`
- `auth_platform_sessions`
- `auth_password_reset_tokens`
- `auth_email_verifications`
- `account_invitations`
- `roles`
- `permissions`
- `role_permissions`
- `user_roles`
- `user_permission_overrides`

### 4.3 商业授权

- `license_state`
- `license_activation_logs`

### 4.4 安全审计

- `auth_login_logs`
- `auth_security_events`
- `action_logs`

### 4.5 业务模块

- 每日运营；
- 每日选品；
- 产品处理；
- 精致作图；
- 利润活动；
- 核价及货源；
- 系统配置；
- 员工管理；
- 店铺配置。

所有业务模块均应通过 `workspace_id` 与当前登录用户关联。

## 5. SQLite 到 MySQL/PostgreSQL 的兼容要求

当前使用 SQLite 是合理的，适合单客户本地部署、MVP、课程/项目演示和小团队试用。

为后续迁移，需要注意：

- 主键使用文本 ID，避免依赖 SQLite 自增；
- 时间字段统一 ISO 字符串或后续迁移为 datetime；
- JSON 字段先用 TEXT 保存，后续迁移为 JSON 类型；
- 避免使用 SQLite 独有语法作为核心逻辑；
- 所有建表脚本走统一 migration；
- 业务代码不直接拼 SQL，应逐步收敛 repository/service 层。

## 6. 部署与打包要求

商业化交付应提供标准部署方式。

建议至少提供：

```text
install.sh
start.sh
stop.sh
backup.sh
restore.sh
.env.example
systemd service templates
nginx template
```

部署包应明确：

- Python 版本；
- 依赖安装；
- 数据库初始化；
- 默认管理员初始化；
- License 激活；
- Nginx 反向代理；
- 日志目录；
- 数据备份目录；
- 升级步骤。

## 7. 当前阶段结论

当前账号与数据库模块已经具备：

- 可运行的真实账号密码登录链路；
- SQLite 账号与会话落库；
- 远端账号服务基础版；
- 本地工作台业务 token；
- 密码哈希存储；
- token hash 存储；
- 修改密码；
- 忘记密码/重置密码基础流程；
- 临时前端演示页；
- 多模块 SQLite 表结构初步整合。

但若作为商业化产品交付，还必须补齐：

- 邮箱找回密码；
- 管理员邀请员工；
- 角色权限正式落地；
- 所有业务接口 workspace 隔离审计；
- License 授权；
- 部署安装脚本；
- 数据备份恢复；
- 登录风控；
- 正式前端替换 `/dev/auth/*` 临时页面。

## 8. 推荐下一阶段工作优先级

### P0：商业化安全底座

1. 检查所有业务表是否包含 `workspace_id`；
2. 检查所有业务查询是否按当前用户 `workspace_id` 过滤；
3. 补 `roles`、`user_roles`、正式权限校验；
4. 注册页已去掉 admin 自选，后续继续补初始化 owner + 邀请员工；
5. 接入邮箱找回密码。

### P1：交付能力

1. 编写 `.env.example`；
2. 编写安装/启动/备份脚本；
3. 编写 systemd/nginx 模板；
4. 建立数据库备份恢复流程；
5. 编写客户部署说明。

### P2：商业授权

1. 设计 license 表；
2. 实现 license 激活接口；
3. 登录时校验 license；
4. 根据 license 控制用户数、模块数、到期时间。

### P3：正式前端

1. 将 `/dev/auth/*` 临时页面迁移为正式前端路由；
2. 补账号设置页；
3. 补员工管理页；
4. 补角色权限配置页；
5. 补系统配置页。

## 9. 对外说明建议

可以对团队说明：

> 当前账号登录和 SQLite 数据库基座已经可以支持真实链路演示与后端联调。注册、登录、退出、修改密码、忘记密码、重置密码等基础能力已完成，远端账号服务也已部署验证。下一阶段将从“项目可运行”升级到“商业化可交付”，重点补齐多租户隔离、正式角色权限、邮箱找回密码、授权 License、部署脚本和全模块数据审计。

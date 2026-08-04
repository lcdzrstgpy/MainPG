# 用户注册登录模块交接文档

## 1. 模块定位

本模块负责本地工作台的用户注册登录、登录态持久化、统一认证入口。

当前目标是先把后端基础能力搭好，供后续正式前端和各业务模块接入：

```text
前端登录页
  -> 本地工作台 /api/customer/login
  -> 本地 SQLite 账号服务，或正式远端账号服务
  -> 本地工作台写入 SQLite
  -> 返回 wh_local_xxx token
  -> 后续接口通过 Authorization: Bearer wh_local_xxx 识别当前用户
```

本次提交不包含正式前端页面，也不包含临时测试工具。

## 2. 当前已完成内容

### 2.1 用户登录接口骨架

目录：

```text
local-runtime/wh_local/customer/
```

文件说明：

```text
contracts.py          数据结构约定
remote_client.py      远端账号服务适配器
auth_service.py       本地 SQLite 真实账号服务
local_session.py      本地 session 服务
routes.py             FastAPI customer 路由
db_store.py           SQLite 存储实现
SQLITE_STORE.md       SQLite 存储说明
HANDOFF.md            当前交接文档
```

### 2.2 SQLite 存储实现

实现文件：

```text
local-runtime/wh_local/customer/db_store.py
```

登录成功后会写入或更新：

```text
workspaces
customer_users
customer_sessions
```

安全约定：

```text
前端拿到明文 wh_local_xxx token
SQLite 只保存 token_hash
数据库不保存明文 wh_local_xxx
```

### 2.3 统一认证入口

实现文件：

```text
local-runtime/wh_local/session.py
```

当前同时兼容：

```text
Authorization: Bearer dev-admin-token
Authorization: Bearer wh_local_xxx
```

说明：

```text
dev-admin-token        保留给本地开发管理员
wh_local_xxx           customer 登录后生成的本地工作台 token
```

后续其他模块可统一通过 `actor_from_authorization` 获取当前用户。

### 2.4 本地 SQLite 真实账号服务

实现文件：

```text
local-runtime/wh_local/customer/auth_service.py
```

当前在未配置 `WH_LOCAL_CUSTOMER_AUTH_BASE_URL` 时，系统默认使用本地 SQLite 账号服务；如果配置了远端账号地址，则继续走远端账号服务。

本地 SQLite 账号服务已支持：

```text
注册账号
账号密码登录
账号激活
本地邮箱验证码占位响应
密码重置
登录成功/失败日志
```

密码安全约定：

```text
不保存明文密码
使用 PBKDF2-HMAC-SHA256
每个账号使用独立 salt
保存算法名和迭代次数，便于后续升级
```

## 3. 接口清单

路由文件：

```text
local-runtime/wh_local/customer/routes.py
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/customer/login` | 登录 |
| POST | `/api/customer/register` | 注册 |
| POST | `/api/customer/activate` | 激活 |
| POST | `/api/customer/email-code` | 邮箱验证码 |
| POST | `/api/customer/password-reset` | 密码重置 |
| GET | `/api/customer/me` | 查询当前用户 |
| POST | `/api/customer/logout` | 退出登录 |

## 4. 数据库表说明

### 4.1 `workspaces`

工作区/团队表，用于后续业务数据隔离。

核心字段：

```text
workspace_id
workspace_code
workspace_name
status
created_at
updated_at
```

### 4.2 `customer_users`

用户表，保存本地工作台识别用户所需信息。

核心字段：

```text
user_id
remote_customer_id
username
email
password_hash
role
workspace_id
account_status
remote_session_expires_at
created_at
updated_at
```

### 4.3 `customer_sessions`

登录会话表，保存本地 token 的哈希、过期时间、注销状态。

核心字段：

```text
session_id
user_id
token_hash
expires_at
revoked_at
last_used_at
created_at
user_agent
client_ip
```

注意：

```text
token_hash 是 SHA-256 哈希
不会保存明文 wh_local_xxx
```

### 4.4 `auth_accounts`

真实账号主表，保存注册账号信息。

核心字段：

```text
account_id
username
email
display_name
role
workspace_id
account_status
email_verified_at
created_at
updated_at
```

### 4.5 `auth_password_credentials`

密码凭据表，只保存密码哈希和算法参数。

核心字段：

```text
account_id
password_hash
salt
algorithm
iterations
updated_at
```

### 4.6 `auth_login_logs`

登录日志表，用于记录登录成功、失败和失败原因。

核心字段：

```text
id
account_id
username
email
success
failure_reason
created_at
```

## 5. 其他模块对接方式

后续其他模块不要自己实现登录判断，统一使用请求头：

```http
Authorization: Bearer wh_local_xxx
```

后端依赖：

```python
from wh_local.session import actor_from_authorization, require_admin
```

可获取：

```text
actor.id
actor.username
actor.role
actor.workspace_id
actor.workspace_code
actor.workspace_name
actor.is_admin
```

管理员接口：

```python
require_admin(actor)
```

业务数据建议保存：

```text
workspace_id
created_by
updated_by
```

## 6. 远端账号服务对接

当前本地阶段不依赖远端账号服务。未配置 `WH_LOCAL_CUSTOMER_AUTH_BASE_URL` 时，注册和登录会直接使用 SQLite 账号表。

本地工作台通过环境变量配置远端账号服务地址：

```text
WH_LOCAL_CUSTOMER_AUTH_BASE_URL
```

登录时本地工作台会调用：

```text
POST {WH_LOCAL_CUSTOMER_AUTH_BASE_URL}/api/customer/login
```

注册、激活、验证码、密码重置同理转发到远端账号服务。

正式远端账号服务需要提供字段：

```text
customer_id
username
email
role
account_status
workspace_code
workspace_name
token
expires_at
```

## 7. 当前暂未处理事项

```text
1. 正式远端账号服务部署方式，或是否长期保留本地 SQLite 账号服务
2. 正式注册、验证码、找回密码是否需要真实邮件服务
3. workspace_id 最终是否使用 workspace_code、UUID 或公司编号
4. 正式前端登录页如何存 token、续期 token、退出登录
5. data_collection / profit_activity 等业务模块由对应负责人确认依赖后继续集成
```

## 8. 提交范围说明

本次提交应包含：

```text
local-runtime/wh_local/db.py
local-runtime/wh_local/session.py
local-runtime/wh_local/config.py
local-runtime/wh_local/app/main.py
local-runtime/wh_local/customer/auth_service.py
local-runtime/wh_local/customer/db_store.py
local-runtime/wh_local/customer/SQLITE_STORE.md
local-runtime/wh_local/customer/HANDOFF.md
local-runtime/wh_local/DATABASE_FOUNDATION.md
local-runtime/requirements.txt
```

不提交：

```text
.vscode/
local-runtime/outputs/
__pycache__/
*.sqlite3
*.sqlite3-wal
*.sqlite3-shm
```

# 账号登录模块交接说明

本文档用于给其他模块同学对接账号登录能力。当前目标是先达到“账号能注册、登录、退出、改密码、忘记/重置密码，并能支撑工作台使用”的状态，不展开 License、完整员工邀请、正式邮箱服务等远期商业化能力。

## 1. 当前模块范围

账号登录模块当前负责：

- 用户注册；
- 用户登录；
- 查询当前登录态；
- 退出登录；
- 修改密码；
- 忘记密码；
- 重置密码；
- 远端账号服务；
- 工作台本地登录会话；
- SQLite 账号、密码、token、登录状态落库；
- 给其他业务模块提供当前用户、角色、工作区信息。

## 2. 当前完成状态

| 功能 | 状态 | 说明 |
|---|---|---|
| 注册 | 已完成 | 注册后写入 SQLite 账号表和密码凭证表 |
| 登录 | 已完成 | 远端账号服务校验账号密码，工作台后端生成本地 token |
| 查询当前用户 | 已完成 | 根据本地 token 返回当前用户和工作区 |
| 退出登录 | 已完成 | 本地 token 置为失效 |
| 修改密码 | 已完成 | 需要旧密码，成功后旧远端 session 失效 |
| 忘记密码 | 当前阶段可用 | 生成一次性 reset token；正式邮箱服务后改为邮件发送 |
| 重置密码 | 已完成 | reset token 只能使用一次 |
| 密码安全 | 已完成基础版 | 不保存明文密码，仅保存 PBKDF2 哈希和盐 |
| token 安全 | 已完成基础版 | 数据库只保存 token 的 SHA-256 哈希 |
| 临时前端 | 已完成演示版 | `/dev/auth/*` 多页面跳转式演示，不作为最终正式前端 |

## 3. 运行架构

当前登录链路分两层：

```text
前端页面
  -> 工作台后端 /api/customer/*
    -> 远端账号服务 /auth-api/api/customer/*
      -> customer-auth.sqlite3
    -> workbench.sqlite3 保存本地工作台 session
```

token 分为两种：

| token | 签发方 | 用途 |
|---|---|---|
| `wh_auth_*` | 远端账号服务 | 代表平台账号登录态 |
| `wh_local_*` | 工作台后端 | 代表本地工作台业务登录态，业务模块使用它鉴权 |

其他业务模块主要认 `wh_local_*`。

## 4. 核心接口

工作台后端统一提供 `/api/customer/*` 接口。前端和业务模块原则上不直接调用远端账号服务。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/customer/register` | 注册账号 |
| `POST` | `/api/customer/login` | 登录 |
| `GET` | `/api/customer/me` | 查询当前登录态 |
| `POST` | `/api/customer/logout` | 退出登录 |
| `POST` | `/api/customer/change-password` | 修改密码 |
| `POST` | `/api/customer/forgot-password` | 忘记密码，生成重置凭证 |
| `POST` | `/api/customer/reset-password` | 使用重置凭证设置新密码 |

### 4.1 注册

请求：

```json
{
  "username": "demo_user",
  "email": "demo_user@example.com",
  "password": "Demo123456"
}
```

说明：

- 当前公开注册账号默认角色为 `operator`；
- 不允许普通注册用户自选 `admin`；
- 管理员/老板账号后续应通过初始化或邀请机制补齐。

### 4.2 登录

请求：

```json
{
  "username": "demo_user",
  "password": "Demo123456"
}
```

成功返回核心字段：

```json
{
  "ok": true,
  "user_id": "cust_xxx",
  "token": "wh_local_xxx",
  "expires_at": "...",
  "account": {
    "customer_id": "cust_xxx",
    "username": "demo_user",
    "email": "demo_user@example.com",
    "role": "operator",
    "workspace_code": "wh_demo",
    "workspace_name": "真实服务器演示工作区"
  }
}
```

其他业务模块要从这里关注：

- `token`：后续请求放到 `Authorization: Bearer wh_local_xxx`；
- `user_id` / `account.customer_id`：当前用户；
- `account.role`：当前角色；
- `account.workspace_code`：当前工作区；
- `account.workspace_name`：工作区名称。

### 4.3 查询当前用户

请求头：

```text
Authorization: Bearer wh_local_xxx
```

返回当前用户、角色、工作区。业务模块后续可通过同样方式识别当前用户。

### 4.4 修改密码

请求：

```json
{
  "username": "demo_user",
  "current_password": "Demo123456",
  "new_password": "NewDemo123456"
}
```

说明：

- 必须验证旧密码；
- 修改成功后，旧远端账号 session 会失效；
- 用户需要用新密码重新登录。

### 4.5 忘记密码与重置密码

当前阶段：

```text
forgot-password -> 返回开发演示用 reset_token
reset-password -> 使用 reset_token 设置新密码
```

正式阶段应改为：

```text
forgot-password -> 发送邮箱重置链接
reset-password -> 用户从邮箱链接进入并设置新密码
```

当前接口可用于演示完整逻辑，但还没接真实邮箱服务。

## 5. SQLite 相关表

账号模块主要使用以下表：

| 表名 | 用途 |
|---|---|
| `auth_accounts` | 远端平台账号 |
| `auth_password_credentials` | 密码哈希、盐、算法参数 |
| `auth_platform_sessions` | 远端账号服务 session |
| `auth_password_reset_tokens` | 忘记密码的一次性重置凭证 |
| `auth_security_events` | 修改密码、重置密码等安全事件 |
| `customer_users` | 工作台本地用户镜像 |
| `customer_sessions` | 工作台本地业务 session |
| `workspaces` | 工作区/租户 |
| `roles` | 角色字典 |
| `user_roles` | 用户-角色关联 |
| `permissions` | 权限点 |
| `role_permissions` | 角色权限关系 |

当前注册用户会写入：

- `auth_accounts`
- `auth_password_credentials`
- `user_roles`

登录成功会写入/更新：

- `auth_platform_sessions`
- `customer_users`
- `customer_sessions`

## 6. 其他模块如何对接

其他业务模块后续对接时应遵守：

1. 前端登录后保存 `wh_local_*`；
2. 调业务接口时带：

```text
Authorization: Bearer wh_local_xxx
```

3. 后端业务模块通过当前 session 获取：

```text
user_id
username
role
workspace_code
workspace_name
```

4. 所有业务数据必须按当前用户所属工作区隔离；
5. 不要信任前端直接传入的 `workspace_id`；
6. 后续统一鉴权依赖完成后，各模块应统一接入权限校验。

## 7. 临时前端演示地址

服务器演示入口：

```text
https://workbench.haocoming.top/dev/auth/login?api=/local-api
```

页面包括：

- 登录；
- 注册；
- 忘记密码；
- 重置密码；
- 登录后首页；
- 修改密码。

说明：

- 这是临时演示页，不是最终正式前端；
- 正式前端后续应迁移到项目统一页面；
- `/dev/auth/*` 后续上线前应关闭或限制访问。

## 8. 测试方式

本地测试命令：

```bash
cd local-runtime
python -m unittest discover -s tests/customer -v
```

已覆盖：

- 注册登录；
- 远端 token hash 存储；
- 本地 token hash 存储；
- 忘记密码；
- 重置密码；
- reset token 只能使用一次；
- 修改密码必须验证旧密码；
- 商业化账号基础表和角色种子存在。

当前最近测试结果：

```text
Ran 4 tests
OK
```

## 9. 当前限制

当前阶段暂不展开以下能力：

- 正式邮箱发送服务；
- 员工邀请完整流程；
- owner 初始化脚本；
- License 授权激活；
- 完整角色权限管理页面；
- 登录失败限流/账号锁定；
- 正式前端页面。

这些内容后续可以继续做，但当前账号模块已经可以支撑基础注册、登录和工作台使用。

## 10. 交接结论

当前账号登录模块已经达到基础可用状态：

- 可以注册；
- 可以登录；
- 可以退出；
- 可以查询当前用户；
- 可以修改密码；
- 可以忘记/重置密码；
- 可以给其他模块提供当前用户、角色和工作区；
- 数据已落 SQLite；
- 远端账号服务与工作台本地 session 已打通。

后续业务模块对接时，统一使用 `Authorization: Bearer wh_local_xxx` 识别当前登录用户。


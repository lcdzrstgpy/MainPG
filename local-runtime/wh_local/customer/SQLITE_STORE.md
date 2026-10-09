# 用户登录模块 SQLite 存储说明

## 1. 实现文件

```text
local-runtime/wh_local/customer/db_store.py
```

该文件实现了 `local_session.py` 中定义的 `CustomerSessionStore` 协议。

## 2. 使用方式

主应用启动时创建：

```python
customer_sessions = LocalSessionService(SQLiteCustomerSessionStore(db_path))
```

然后挂载 customer 路由：

```python
app.include_router(create_customer_router(customer_auth, customer_sessions))
```

## 3. 落库流程

登录成功后：

1. 远端账号服务返回账号信息；
2. 本地运行时写入或更新 `workspaces`；
3. 本地运行时写入或更新 `customer_users`；
4. 本地生成 `wh_local_xxx` token；
5. 数据库只保存 token 的 SHA-256 哈希到 `customer_sessions`；
6. 前端后续请求带 `Authorization: Bearer wh_local_xxx`；
7. `/api/customer/me` 根据 token 哈希查询当前用户；
8. `/api/customer/logout` 将 session 标记为已注销。

## 4. 涉及表

```text
workspaces
customer_users
customer_sessions
```

这些表由统一数据库初始化入口创建：

```text
local-runtime/wh_local/db.py
```

## 5. 安全约定

- 明文 token 只在登录接口返回给前端一次；
- SQLite 中只保存 `token_hash`；
- 密码字段当前预留为 `password_hash`，不保存明文密码；
- 远端账号服务返回的 `remote_token` 当前不单独落库。

## 6. 环境变量

远端账号服务地址：

```text
WH_LOCAL_CUSTOMER_AUTH_BASE_URL
```

如果没有配置该地址，`/api/customer/login` 会提示账号服务未配置，这是开发阶段的正常状态。

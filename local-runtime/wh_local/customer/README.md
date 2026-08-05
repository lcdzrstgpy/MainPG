# Customer registration/login module

本目录是用户注册登录板块的基础代码框架，目标是先把团队整合需要的边界搭好，后续再按远端控制面、数据库、前端组件的最终合约填充细节。

## 负责范围

1. 调用远端平台账号服务完成注册、登录、激活、验证码、密码重置。
2. 登录成功后创建本地工作台会话。
3. 把远端账号映射成本地用户身份，给后续商品、店铺、任务、报表模块提供统一 `user_id / role / workspace`。
4. 保持业务模块只依赖本地会话，不直接依赖远端账号 token。

## 文件说明

- `contracts.py`：注册登录模块对外暴露的数据结构和异常。
- `remote_client.py`：远端平台账号服务适配层，后续远端字段变化主要改这里。
- `local_session.py`：本地会话服务和存储接口；当前提供内存实现，后续替换为 SQLite/Postgres。
- `routes.py`：FastAPI 路由工厂，提供 `/api/customer/*` 接口。

## 接口骨架

- `POST /api/customer/login`
- `POST /api/customer/register`
- `POST /api/customer/activate`
- `POST /api/customer/email-code`
- `POST /api/customer/password-reset`
- `GET /api/customer/me`
- `POST /api/customer/logout`

## Standalone platform auth service

Phase 3 adds a standalone customer auth server:

```powershell
cd local-runtime
python devtools/run_customer_auth_server.py --port 8011 --database outputs/auth/platform-auth.sqlite3
```

Alternatively, for uvicorn import-string startup:

```powershell
uvicorn "wh_local.customer.auth_server:create_default_auth_app" --factory --host 127.0.0.1 --port 8011
```

Then start the workbench runtime with:

```powershell
set WH_LOCAL_CUSTOMER_AUTH_BASE_URL=http://127.0.0.1:8011
uvicorn wh_local.app.main:app --host 127.0.0.1 --port 8010
```

The platform auth service owns account/password verification and issues
`wh_auth_*` tokens. The workbench runtime exchanges the normalized account
result for a local `wh_local_*` session used by business modules.

For Linux server deployment, see `REMOTE_AUTH_DEPLOYMENT.md`.

## 待团队整合项

1. 确认远端认证服务地址和字段名。
2. 用正式数据库实现替换 `MemoryCustomerSessionStore`。
3. 前端注册页补充公司名、邀请码、套餐码等最终字段。
4. 和权限/工作区模块确认 `role`、`workspace_code` 的最终枚举。

# 真实服务器账号密码服务部署方案

适用场景：第三阶段把账号密码登录从本地演示升级为真实服务器业务链路。

当前服务器信息：

- 系统：Linux
- 域名：`https://workbench.haocoming.top/`
- 数据库：先使用 SQLite，后续可迁移 MySQL
- 服务：FastAPI + Uvicorn

## 一、真实业务链路

```text
用户前端登录
  -> 工作台后端 POST /api/customer/login
  -> 工作台后端调用远端账号服务
  -> 远端账号服务校验账号密码
  -> 远端账号服务返回账号、角色、工作区、远端 wh_auth_* token
  -> 工作台后端生成本地 wh_local_* token
  -> 后续业务模块统一使用 wh_local_* token 鉴权
```

两个 token 的职责不同：

| Token | 签发方 | 用途 | 入库方式 |
|---|---|---|---|
| `wh_auth_*` | 远端账号服务 | 代表平台账号登录态 | 只保存 SHA-256 哈希到 `auth_platform_sessions` |
| `wh_local_*` | 工作台后端 | 代表本地业务模块登录态 | 只保存 SHA-256 哈希到 `customer_sessions` |

业务模块不直接依赖远端 token，只认工作台本地 token。

## 二、服务器是否必须安装 Python？

如果使用当前 FastAPI 实现，是必须的。

推荐版本：Python 3.11+。

检查命令：

```bash
python3 --version
```

如果没有 Python，需要安装：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

如果服务器不是 Ubuntu/Debian，请按对应 Linux 发行版安装 Python。

## 三、推荐部署目录

```bash
sudo mkdir -p /opt/wh-workbench
sudo chown -R $USER:$USER /opt/wh-workbench
cd /opt/wh-workbench
```

拉取项目：

```bash
git clone https://github.com/Alon237925/MainPG.git
cd MainPG
git checkout dev
git pull origin dev
```

创建虚拟环境：

```bash
cd local-runtime
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 四、启动远端账号服务

开发验证启动：

```bash
cd /opt/wh-workbench/MainPG/local-runtime
source .venv/bin/activate
python devtools/run_customer_auth_server.py \
  --host 127.0.0.1 \
  --port 8011 \
  --database /opt/wh-workbench/data/customer-auth.sqlite3
```

验证：

```bash
curl http://127.0.0.1:8011/health
```

预期返回：

```json
{"ok": true, "service": "customer-auth"}
```

## 五、Nginx 反向代理建议

不建议直接暴露 `8011` 端口。推荐由 Nginx 把：

```text
https://workbench.haocoming.top/auth-api
```

代理到：

```text
http://127.0.0.1:8011
```

示例 Nginx 配置片段：

```nginx
location /auth-api/ {
    proxy_pass http://127.0.0.1:8011/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

注意 `proxy_pass` 末尾有 `/`，用于把 `/auth-api/` 前缀转发时剥掉。

这样工作台调用：

```text
https://workbench.haocoming.top/auth-api/api/customer/login
```

实际进入账号服务：

```text
http://127.0.0.1:8011/api/customer/login
```

## 六、工作台后端配置

工作台后端需要配置：

```bash
export WH_LOCAL_CUSTOMER_AUTH_BASE_URL=https://workbench.haocoming.top/auth-api
```

然后启动工作台后端：

```bash
cd /opt/wh-workbench/MainPG/local-runtime
source .venv/bin/activate
uvicorn wh_local.app.main:app --host 127.0.0.1 --port 8010
```

此时工作台 `/api/customer/login` 会自动调用真实远端账号服务。

## 七、systemd 常驻服务示例

账号服务：

```ini
[Unit]
Description=W-H Customer Auth Service
After=network.target

[Service]
WorkingDirectory=/opt/wh-workbench/MainPG/local-runtime
Environment=WH_LOCAL_DATABASE_PATH=/opt/wh-workbench/data/customer-auth.sqlite3
ExecStart=/opt/wh-workbench/MainPG/local-runtime/.venv/bin/python devtools/run_customer_auth_server.py --host 127.0.0.1 --port 8011 --database /opt/wh-workbench/data/customer-auth.sqlite3
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

工作台后端：

```ini
[Unit]
Description=W-H Workbench Backend
After=network.target

[Service]
WorkingDirectory=/opt/wh-workbench/MainPG/local-runtime
Environment=WH_LOCAL_CUSTOMER_AUTH_BASE_URL=https://workbench.haocoming.top/auth-api
Environment=WH_LOCAL_DATABASE_PATH=/opt/wh-workbench/data/workbench.sqlite3
ExecStart=/opt/wh-workbench/MainPG/local-runtime/.venv/bin/uvicorn wh_local.app.main:app --host 127.0.0.1 --port 8010
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## 八、账号接口

远端账号服务提供：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 服务健康检查 |
| `POST` | `/api/customer/register` | 注册账号 |
| `POST` | `/api/customer/login` | 账号密码登录 |
| `GET` | `/api/customer/me` | 根据远端 token 查询账号 |
| `POST` | `/api/customer/logout` | 注销远端 token |
| `POST` | `/api/customer/activate` | 激活账号 |
| `POST` | `/api/customer/email-code` | 邮箱验证码占位 |
| `POST` | `/api/customer/password-reset` | 重置密码 |

## 九、安全注意事项

当前阶段已具备：

- 密码不明文存储，使用 PBKDF2-SHA256 + salt；
- 远端 token 不明文存储，只保存 `token_hash`；
- 本地工作台 token 不明文存储，只保存 `token_hash`；
- 账号服务建议只监听 `127.0.0.1`，由 Nginx 统一 HTTPS 对外。

后续生产增强建议：

- 增加登录失败次数限制；
- 增加验证码/邮箱验证真实发送；
- 增加管理员后台创建/禁用账号；
- SQLite 定期备份；
- 若并发和用户量变大，迁移到 MySQL。

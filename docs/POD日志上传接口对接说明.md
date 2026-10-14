# POD 日志上传接口 · 对接说明（交接服务器端开发）

> 状态：**待服务器端实现**。客户端（桌面本地工作台）已实现完成，只等这个接口上线。
> 客户端版本：`wh_local`（`local-runtime/wh_local/pod_log_upload.py`）
> 最后更新：2026-10-12

---

## 1. 背景与目标

桌面端本地工作台会写一份 POD 业务日志 `pod_processing.log`（POD 批次全过程：生图提交/轮询/失败重试、
规格卡合成、清晰化耗时等）。用户遇到 POD 出图问题时，我们自己排障只能让用户手动发文件。

现在改成**客户端自动上报**：每天把 `pod_processing.log` 传一份到平台，后台按用户/时间查看。

设计约束（产品方已拍板）：

- **客户端零常驻**：不启动任何守护线程/定时器/轮询循环，不做"占用用户后台"的常驻进程；
- **不复用启动器的日志接口**：启动器上报 `runtime.log` 走的是 `/api/customer/log-upload`，
  POD 日志**单独走本接口**，方便后台区分来源与权限。

---

## 2. 接口契约

### 2.1 基本信息

| 项 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/customer/pod-log`（挂在现有账号服务前缀下，即完整地址 `{customer_auth_base_url}/api/customer/pod-log`） |
| 鉴权 | `Authorization: Bearer <远端用户 token>`（与现有 `/api/customer/*` 同一套 token） |
| 请求头 | `Content-Type: application/json`、`Accept: application/json` |
| 请求体 | JSON（见 2.2） |
| 成功响应 | 任意 2xx + JSON（见 2.4） |

> **路径已做成客户端可配置**：客户端读环境变量 `WH_LOCAL_POD_LOG_UPLOAD_PATH`，缺省 `/api/customer/pod-log`。
> 如果你们最终想用别的路径，无需重发桌面版本，改配置即可；但请告知最终值以便默认值对齐。

### 2.2 请求体

```json
{
  "log_name": "pod_processing.log",
  "content_b64": "MjAyNi0xMC0xMiAxMzowMDoxMiBJTkZPICAgIFt3aF9sb2NhbF0gUElEIEJBVENIIFNUQVJUCg==",
  "app_version": "1.5.2",
  "platform": "windows"
}
```

| 字段 | 类型 | 必有 | 说明 |
| --- | --- | --- | --- |
| `log_name` | string | 是 | 固定 `pod_processing.log`（≤255 字符）。用于区分日志类型，建议作为筛选维度 |
| `content_b64` | string | 是 | 日志**原始字节**的 base64（标准 base64，非 URL-safe） |
| `app_version` | string | 是 | 客户端应用版本（如 `1.5.2`，可能为空串） |
| `platform` | string | 是 | `windows` / `macos` / `linux`，可能为空串 |

### 2.3 服务端处理要求

1. **鉴权**：与现有 `/api/customer/log-upload` 完全一致——用 `_required_account(db_path, authorization)`
   校验 Bearer，取出 `account_id` / `username` / `workspace_id`；无效 token 返回 **401**。
2. **解码**：
   ```python
   raw = base64.b64decode(content_b64, validate=True)   # 解码失败 → 400
   text = raw.decode("utf-8", errors="replace")          # 文本日志，逐行 "时间 级别 [logger] 消息"
   ```
3. **体积限制**：**解码后**字节数 ≤ **16MB**，超出返回 **413**。
   （客户端已按 15MB 自截断，正常不会触发。）
4. **截断标记**：若日志正文首行是 `……（日志过大，仅上传末尾内容）`，表示客户端只保留了末尾片段
   （在行边界切开），**不是**日志损坏。
5. **落库建议**：与 `launcher_log_uploads` 同构，但**单独建一张表**（如 `pod_log_uploads`），
   字段可直接照搬：`upload_id / account_id / username / workspace_id / app_version /
   platform / log_name / log_size / content_b64 / client_ip / created_at`，
   并加 `(account_id, created_at)`、`(created_at)` 两个索引。
   `log_size` 存**解码后**字节数；`content_b64` 存原始 base64（与现有表口径一致）。
6. **不需要服务端做去重**：客户端保证同一账号同一自然日最多成功上传一次（见 §4）。
   但后端仍建议允许重复插入（重装/多机场景可能重复），后台按时间排序查看即可。

### 2.4 响应约定

成功（建议）：

```json
{
  "ok": true,
  "upload_id": "podlog_xY3f...",
  "log_name": "pod_processing.log",
  "log_size": 801223,
  "created_at": "2026-10-12T05:04:11Z"
}
```

> 客户端**只判断"没抛异常"**，不解析响应体，所以 200 / 201 / 202 均可，字段可自行增减。

错误：

| HTTP | 场景 | 客户端行为 |
| --- | --- | --- |
| 401 | token 无效/过期 | 记 warning 放弃本轮；当天仍算"未上传"，下次登录会再试 |
| 400 | `content_b64` 缺失或非法 | 放弃本轮 |
| 413 | 解码后 > 16MB | 放弃本轮（客户端已截断，正常不该出现） |
| 5xx / 超时 | 服务端异常 | 放弃本轮，等下次触发 |

**客户端不做重试风暴**：一次触发失败就结束，不循环重试；下个触发点再试。
客户端 HTTP 超时 8 秒。

---

## 3. 客户端何时上传（供后台理解数据节奏）

- **应用启动时**检查一次；
- **每次登录成功后**再检查一次（由 FastAPI `BackgroundTasks` 在登录响应之后执行）；
- 满足「**今天已过 12:00** 且 **今天还没成功上传**」才真正发起请求，否则直接跳过。

⚠️ 由于客户端**零常驻**：若用户早上启动后一整天不重启、也不重新登录，则**当天不会上传**，
下次启动或登录（且已过 12:00）时补传。客户端上传时刻可配置（`upload_hour`，默认 12）。

---

## 4. 幂等与状态

- 客户端按**本地自然日**去重：成功一次后，当天不再发起；
- 状态落在日志目录的 `pod_log_upload_state.json`（内容形如 `{"last_success_date": "2026-10-12"}`），
  不含任何凭据；进程重启后依然生效；
- **只有"成功"才标记当天完成**；401/网络失败等都不会标记，所以当天后续仍会重试。

---

## 5. 参考实现（FastAPI，与现有 `customer/auth_server.py` 风格一致）

```python
@app.post("/api/customer/pod-log")
def customer_pod_log_upload(
    payload: dict[str, Any],
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """POD 日志上报：复用账号 token，按用户/时间落库供后台查看。"""
    account = _required_account(db_path, authorization)

    content_b64 = str(payload.get("content_b64") or "")
    if not content_b64:
        raise HTTPException(status_code=400, detail="log content is required")
    try:
        decoded = base64.b64decode(content_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="log content is invalid") from exc
    if len(decoded) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="log content is too large")

    log_name = str(payload.get("log_name") or "pod_processing.log").strip()[:255] or "pod_processing.log"
    app_version = str(payload.get("app_version") or "").strip()[:64]
    platform = str(payload.get("platform") or "").strip()[:64]

    upload_id = f"podlog_{secrets.token_urlsafe(18)}"
    now = _utc_now()
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pod_log_uploads (
                upload_id, account_id, username, workspace_id, app_version,
                platform, log_name, log_size, content_b64, client_ip, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                upload_id,
                str(account["account_id"]),
                str(account.get("username") or ""),
                str(account.get("workspace_id") or "default"),
                app_version,
                platform,
                log_name,
                len(decoded),
                content_b64,
                request.client.host if request.client else "",
                now,
            ),
        )
    return {"ok": True, "upload_id": upload_id, "log_name": log_name,
            "log_size": len(decoded), "created_at": now}
```

建表 SQL：

```sql
CREATE TABLE IF NOT EXISTS pod_log_uploads (
    upload_id    TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL DEFAULT '',
    username     TEXT NOT NULL DEFAULT '',
    workspace_id TEXT NOT NULL DEFAULT '',
    app_version  TEXT NOT NULL DEFAULT '',
    platform     TEXT NOT NULL DEFAULT '',
    log_name     TEXT NOT NULL DEFAULT '',
    log_size     INTEGER NOT NULL DEFAULT 0,
    content_b64  TEXT NOT NULL DEFAULT '',
    client_ip    TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pod_log_uploads_account_time ON pod_log_uploads (account_id, created_at);
CREATE INDEX IF NOT EXISTS idx_pod_log_uploads_time         ON pod_log_uploads (created_at);
```

---

## 6. 联调步骤

**服务端自测**

```bash
# 造 content_b64
printf 'line1\nline2\n' | base64

curl -X POST https://workbench.haocoming.top/auth-api/api/customer/pod-log \
  -H "Authorization: Bearer <有效 token>" \
  -H "Content-Type: application/json" \
  -d '{"log_name":"pod_processing.log","content_b64":"bGluZTEKbGluZTI=","app_version":"1.5.2","platform":"macos"}'
```

预期：2xx + `{"ok": true, ...}`，库里 `pod_log_uploads` 多一行，`base64.b64decode(content_b64)` 得到日志原文。

**客户端侧验证**

1. 正常启动本地工作台并登录（登录后即触发一次检查）；
2. 检查日志目录（`storage_root()`，源码运行即 `local-runtime/`）出现 `pod_log_upload_state.json`，
   内容为 `{"last_success_date": "<今天>"}` 即成功；
3. 若没产生，说明当次返回的是 `skipped:*`（未过 12:00 / 当天已传 / 无 token / 无日志），
   可在客户端日志里搜 `POD 日志上传` 查看原因。

> 提示：本地想立刻联调又不想等到 12:00，可临时把 `PodLogUploadTask(upload_hour=0)` 起在测试里，
> 或直接调用 `PodLogUploadTask.run_if_due()`。

---

## 7. 待确认（服务器端回复即可）

1. 最终路径是否就用 `/api/customer/pod-log`？若不同请给出准确值（客户端改配置即可）。
2. 成功响应是否需要特定字段（客户端目前不解析响应体，字段自由）。
3. 是否需要额外维度：如 `workspace_id` 是否由服务端从 token 推导（客户端**不**上报该字段）。
4. 落库保留期（是否与既有日志一样按 90 天清理）。

---

## 附：客户端已改动文件（供交叉对照）

| 文件 | 改动 |
| --- | --- |
| `local-runtime/wh_local/pod_log_upload.py` | 新增：`PodLogUploadTask` / `build_payload` / `read_log_tail` |
| `local-runtime/wh_local/customer/remote_client.py` | 新增 `upload_pod_log()`（走本接口，非 launcher 的 log-upload） |
| `local-runtime/wh_local/customer/routes.py` | `create_customer_router(..., on_login_success=...)`，login 挂 BackgroundTasks |
| `local-runtime/wh_local/app/main.py` | 构建任务、启动时检查一次、注入登录回调 |
| `local-runtime/tests/test_pod_log_upload.py` | 新增 15 条单测（契约/去重/跳过/异常隔离） |

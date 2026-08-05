# 核价及货源模块

本模块在本地工作台和 Edge 浏览器插件之间建立只读连接，用于采集 Temu 核价证据、匹配 1688 货源，并生成核价与货源快照。所有业务数据按工作空间隔离；模块绝不接受 Temu 报价、修改价格、创建订单、加入购物车或触发任何平台写操作。

## 主要能力

- 通过 Edge 插件采集 Temu 页面网络响应与弹窗 DOM 核价证据。
- 保存不可变核价批次，提供预览、Excel 导出和证据报告。
- 人工逐条选择“保留/拒绝”官方报价链接；只有保留项才能按主图创建 1688 图搜任务。
- 每个保留链接生成一个独立任务，即使多个报价属于同一 SKC 也不会合并。
- 复用现有利润活动引擎计算候选成本和利润，不复制利润公式。
- 支持 OneBound 1688 图片检索、独立调用预算和失败项重试。

## 启动本地服务

服务仅可运行在本机回环地址，并使用本地信任的 TLS 证书；不得暴露到局域网或公网。

```bash
cd local-runtime
/Applications/anaconda3/bin/python3.12 -m uvicorn wh_local.app.main:app \
  --host 127.0.0.1 --port 8000 \
  --ssl-keyfile /绝对路径/loopback-key.pem \
  --ssl-certfile /绝对路径/loopback-cert.pem
```

插件只接受 `https://127.0.0.1/*` 或 `https://localhost/*` 的本地桥接地址。请先在 Edge 中信任本地证书；不得填写远程地址或 HTTP 地址。

## 复用已连接的数据采集插件

核价及货源不再注册第二套根级插件接口，也不再单独配对。它复用数据采集模块已经跑通的唯一连接：

- `POST /plugin/connect`
- `POST /plugin/poll`
- `POST /plugin/result`

核价命令类型为 `temu_price_quote_discovery`，货源图搜命令类型为
`source_browser_image_search`。同一个已连接会话需要声明对应 capability。旧接口
`POST /api/v1/price-verification/plugin/pairing-codes` 只返回 `409`，用于明确提示调用方改用共享连接。

业务流程固定为：采集 Temu 官方链接报价 → 保存核价快照 → 人工逐条保留或拒绝 →
仅将当前保留项冻结为货源任务 → 插件按每个保留链接的主图执行图搜。后续人工修改核价决定不会改写已经排队或已经落库的货源批次。

## 只读边界

插件只读取 Temu 已展示的网络和页面证据，人工确认报价仍在 Temu 平台内完成。本模块和插件不得点击确认、提交、保存、创建、删除或调用任何会改变 Temu/1688 平台状态的接口。

货源部分同样只采集搜索与候选证据。平台凭据仅应由本地服务进程配置，不能粘贴到插件、日志、导出文件或采集载荷中。

## SQLite 数据库变更交接

模块使用本地工作台注入的 SQLite `database_path`。本次需要数据库同事纳入两个 migration，执行顺序如下（均为幂等建表，可重复执行）：

1. 先执行 `data_collection/migrations/003_plugin_command_requests.sql`。
2. 再执行 `price_verification/migrations/002_retained_link_sourcing.sql`。

应用本地启动时也会自动执行上述 migration，但正式环境仍应由数据库发布流程显式管理。

### 变更一：共享插件命令幂等映射

文件：`local-runtime/wh_local/data_collection/migrations/003_plugin_command_requests.sql`

```sql
CREATE TABLE IF NOT EXISTS data_collection_plugin_command_requests (
    workspace_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    command_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, command_type, idempotency_key),
    FOREIGN KEY (command_id) REFERENCES data_collection_plugin_commands(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_data_collection_plugin_command_requests_command
    ON data_collection_plugin_command_requests(command_id);
```

用途：让核价和货源通过现有 `data_collection_plugin_commands` 队列投递命令，同时保留按工作空间、命令类型和业务幂等键去重的能力。

### 变更二：人工决定与货源输入快照

文件：`local-runtime/wh_local/price_verification/migrations/002_retained_link_sourcing.sql`

```sql
CREATE TABLE IF NOT EXISTS price_verification_quote_decisions (
    decision_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    quote_run_id TEXT NOT NULL,
    quote_key TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('retained', 'rejected')),
    decided_by TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL,
    decided_at TEXT NOT NULL,
    UNIQUE (workspace_id, quote_run_id, quote_key, revision),
    FOREIGN KEY (workspace_id, quote_run_id, quote_key)
        REFERENCES price_verification_quote_items(workspace_id, run_id, quote_key)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_verification_quote_decisions_current
    ON price_verification_quote_decisions(workspace_id, quote_run_id, quote_key, revision DESC);

CREATE TABLE IF NOT EXISTS price_verification_sourcing_run_quotes (
    workspace_id TEXT NOT NULL,
    sourcing_run_id TEXT NOT NULL,
    quote_run_id TEXT NOT NULL,
    quote_key TEXT NOT NULL,
    official_link_url TEXT NOT NULL,
    main_image_url TEXT NOT NULL,
    selected_price_cny TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, sourcing_run_id, quote_key),
    FOREIGN KEY (workspace_id, sourcing_run_id)
        REFERENCES price_verification_sourcing_runs(workspace_id, run_id)
        ON DELETE CASCADE,
    FOREIGN KEY (workspace_id, quote_run_id, quote_key)
        REFERENCES price_verification_quote_items(workspace_id, run_id, quote_key)
);

CREATE INDEX IF NOT EXISTS idx_price_verification_sourcing_run_quotes_quote
    ON price_verification_sourcing_run_quotes(workspace_id, quote_run_id, quote_key);
```

`price_verification_quote_decisions` 采用追加式 revision，保留人工操作历史；查询当前决定时取每个 `quote_key` 的最大 revision。`price_verification_sourcing_run_quotes` 保存排队时的不可变输入，至少包含官方链接、主图和人工选中的人民币报价；重试必须继续使用该快照，不能重新读取后来已变化的决定。

核价快照 `price_verification_quote_items.snapshot_json` 新增/使用以下 JSON 字段，不需要增加实体列：

- `quote_key`：单条报价稳定标识。
- `official_link_url`：Temu 网页版官方商品链接；只有 goods id 时规范化为 `https://www.temu.com/goods.html?goods_id=...`。
- `adjusted_declared_price_cny`、`new_declared_price_cny` 或 `original_declared_price_cny`：按此优先级得到 `selected_price_cny`。
- `main_image_url`、`sku_id`、`skc_id`、`spu_or_goods_id`、`product_title`：图搜及人工复核证据。

### 兼容边界

原 `001_price_verification.sql` 中的 `price_verification_plugin_sessions`、`price_verification_plugin_commands` 和 `price_verification_pairing_codes` 暂时保留，避免旧数据和旧代码读取失败；新核价、货源命令不再写入这些表。当前唯一活跃插件传输表是 `data_collection_plugin_sessions` 与 `data_collection_plugin_commands`。

原有核价及货源业务表继续保留：

- `price_verification_pairing_codes`：一次性配对码摘要，不保存明文。
- `price_verification_plugin_sessions`、`price_verification_plugin_commands`：插件会话、命令租约、脱敏载荷与结果。
- `price_verification_provider_budgets`：按工作空间、凭据指纹和上海日期统计的 Provider 调用预算。
- `price_verification_quote_runs`、`price_verification_quote_items`：不可变 Temu 核价快照。
- `price_verification_sourcing_runs`、`price_verification_source_candidates`：货源匹配批次、候选和员工侧决策。

所有读写必须带 `workspace_id` 范围。共享 SQLite 连接需要开启 WAL、外键和忙等待；不得保存平台凭据、配对码明文、插件会话令牌或未脱敏原始插件载荷。

### 上线后校验 SQL

```sql
PRAGMA foreign_keys;

SELECT name
FROM sqlite_master
WHERE type = 'table'
  AND name IN (
    'data_collection_plugin_command_requests',
    'price_verification_quote_decisions',
    'price_verification_sourcing_run_quotes'
  )
ORDER BY name;

PRAGMA foreign_key_check;
```

预期 `PRAGMA foreign_keys` 为 `1`，表查询返回 3 行，`PRAGMA foreign_key_check` 返回 0 行。

### 回滚边界

应用回滚时不要直接删除上述三张表；保留它们不会影响旧版本运行，且人工决定和已冻结货源输入属于审计数据。若后续确认永久下线，应先备份，再按外键依赖顺序删除 `price_verification_sourcing_run_quotes`、`price_verification_quote_decisions`、`data_collection_plugin_command_requests`，并由数据库负责人单独审批执行。

# 核价及货源模块

本模块在本地工作台和 Edge 浏览器插件之间建立只读连接，用于采集 Temu 核价证据、匹配 1688 货源，并生成核价与货源快照。所有业务数据按工作空间隔离；模块绝不接受 Temu 报价、修改价格、创建订单、加入购物车或触发任何平台写操作。

## 主要能力

- 通过 Edge 插件采集 Temu 页面网络响应与弹窗 DOM 核价证据。
- 保存不可变核价批次，提供预览、Excel 导出和证据报告。
- 按主图与 SKC 创建 1688 图搜任务，归一化候选并给出推荐、复核或 SKU 验证结论。
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

## 安装并连接 Edge 插件

1. 在已登录的本地工作台调用 `POST /api/v1/price-verification/plugin/pairing-codes` 获取一次性配对码。
2. 打开 `edge://extensions`，开启“开发人员模式”，选择“加载解压缩的扩展”。
3. 选择目录 `local-runtime/wh_local/price_verification/plugin/extension`。
4. 打开“核价及货源只读连接器”，填写本地 HTTPS 地址和配对码后连接。

配对码有效期为十分钟且只能使用一次。连接失败或过期时请重新获取配对码；不要将其替换为业务登录令牌。

## 只读边界

插件只读取 Temu 已展示的网络和页面证据，人工确认报价仍在 Temu 平台内完成。本模块和插件不得点击确认、提交、保存、创建、删除或调用任何会改变 Temu/1688 平台状态的接口。

货源部分同样只采集搜索与候选证据。平台凭据仅应由本地服务进程配置，不能粘贴到插件、日志、导出文件或采集载荷中。

## SQLite 数据库协作

模块使用本地工作台注入的 SQLite `database_path`。核价数据不能写入 `daily_selection_*` 或 `data_collection_plugin_*` 表，因为批次状态、工作空间隔离和安全要求不同。

`PriceVerificationRepository` 首次构造时会执行 `migrations/001_price_verification.sql`。数据库初始化或迁移负责人需要在创建、升级工作台数据库时纳入此 migration。该模块拥有以下 8 张表：

- `price_verification_pairing_codes`：一次性配对码摘要，不保存明文。
- `price_verification_plugin_sessions`、`price_verification_plugin_commands`：插件会话、命令租约、脱敏载荷与结果。
- `price_verification_provider_budgets`：按工作空间、凭据指纹和上海日期统计的 Provider 调用预算。
- `price_verification_quote_runs`、`price_verification_quote_items`：不可变 Temu 核价快照。
- `price_verification_sourcing_runs`、`price_verification_source_candidates`：货源匹配批次、候选和员工侧决策。

所有读写必须带 `workspace_id` 范围。共享 SQLite 连接需要开启 WAL、外键和忙等待；不得保存平台凭据、配对码明文、插件会话令牌或未脱敏原始插件载荷。

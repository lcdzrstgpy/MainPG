# 核价及货源模块设计

**日期：** 2026-08-04
**目标模块：** `local-runtime/wh_local/price_verification`
**参考实现：** `/Users/Zhuanz/Desktop/绿地/real-workbench` 与 `W-H本地演示版-交付`
**状态：** 已确认设计方向，待用户复核

## 1. 目标

在新的本地运行时中实现独立的“核价及货源”后端模块，并提供与现有演示页面及浏览器插件兼容的接口。完整业务链路包括：

1. 工作台向浏览器插件下发只读 Temu 核价采集命令；
2. 插件在员工已经登录的 Temu Seller Central 页面读取网络接口证据和 DOM 补充证据；
3. 本地运行时脱敏、归一化、校验并保存不可变核价快照；
4. 系统从完整核价记录生成特卖数据或 1688 图片搜索任务；
5. 插件或 OneBound 1688 Provider 返回候选货源；
6. 系统归一化候选，完成同款、SKU、成本、运费、重量和利润判断；
7. 工作台展示推荐、待复核、缺失和失败结果，并支持只读导出。

模块不得自动接受核价、驳回核价、修改平台价格、创建采购单、加入购物车、发布商品或执行任何其他平台写操作。

## 2. 范围

### 2.1 本次包含

- Temu “价格待确认”列表及批量核价弹窗的只读采集；
- 网络证据优先、DOM 证据补充的核价归一化；
- SKC、SKU、站点、状态、原申报价、调整后申报价、标题和主图等字段；
- 核价完整度、来源可信度、真实性状态、缺失字段和冲突字段；
- 按 workspace 保存核价批次与核价条目；
- 从完整核价生成特卖数据后台图搜任务；
- 使用现有 OneBound 1688 Provider 进行可选的 1688 图片搜索和详情补采；
- 货源候选归一化、去重、同款判断、SKU 绑定、MOQ、采购价、国内运费、重量和利润预览；
- 自动推荐、待人工复核、待 SKU 验证、无可靠货源和失败等业务结论；
- 浏览器插件配对、会话、命令轮询、进度回传和最终结果回传；
- 核价 Excel 与证据报告导出；
- 与现有演示前端所使用响应字段兼容的过渡接口。

### 2.2 本次不包含

- Temu Open Platform 正式授权接入；
- 淘宝 API 货源查询；
- 自动确认或驳回 Temu 核价；
- 自动下单、采购、加购或联系供应商；
- 自动把货源写入商品库或产品草稿；
- 迁移旧工作台全部插件能力；
- 店小秘导入、老品分析、视频补传、下架等无关插件命令；
- 前端页面重设计。

## 3. 方案选择

采用“兼容协议 + 选择性迁移”方案。

不整包复制旧工作台。核价解析器的纯业务行为按原实现迁移；货源发现只迁移本模块需要的模型、任务构建、归一化、判定和排序逻辑；插件重新裁剪；认证、数据库和路由按照新本地运行时边界实现。

该方案的关键目标是保持业务结果兼容，而不是保持旧文件结构兼容。新代码运行时不得从 `/Users/Zhuanz/Desktop/绿地` 或交付包目录 import 文件。

## 4. 总体架构

```text
React 工作台
  │
  ├── 业务 API：/api/v1/price-verification/*
  └── demo 兼容 API：/local/*、/plugin/*
                │
                ▼
price_verification
  ├── Quote Service
  ├── Sourcing Service
  ├── Plugin Command Service
  ├── SQLite Repository
  ├── Profit Adapter ───────► 现有 profit_activity.domain.engine
  └── 1688 Adapter ─────────► 现有 data_collection.provider
                ▲
                │ 仅命令、进度和脱敏结果
裁剪后的 MV3 浏览器插件
  ├── Temu 核价页面采集
  └── 特卖数据/1688 只读图搜
```

`price_verification` 是业务聚合边界。现有 `data_collection` 只提供可复用的 1688 Provider，不共享每日选品表、批次或 handoff。现有 `profit_activity` 只提供利润公式，不由核价模块复制公式。

## 5. 模块结构

```text
local-runtime/wh_local/price_verification/
├── __init__.py
├── contracts.py
├── quote_normalizer.py
├── quote_service.py
├── repository.py
├── routes.py
├── exports.py
├── sourcing/
│   ├── __init__.py
│   ├── contracts.py
│   ├── task_builder.py
│   ├── normalizer.py
│   ├── identity.py
│   ├── ranking.py
│   ├── costs.py
│   ├── profit_adapter.py
│   └── service.py
├── plugin/
│   ├── __init__.py
│   ├── contracts.py
│   ├── repository.py
│   ├── service.py
│   ├── routes.py
│   └── extension/
│       ├── manifest.json
│       ├── popup.html
│       ├── popup.js
│       ├── tenant_context.js
│       ├── background.js
│       ├── network_probe_utils.js
│       └── page_probe.js
└── migrations/
    └── 001_price_verification.sql
```

每个文件只承担一个稳定职责。特别是不得把旧版 4628 行的 `source_discovery.py` 原样复制成新的单文件。

## 6. 可复用代码与适配规则

### 6.1 核价解析

从旧 `price_quote_discovery.py` 迁移以下已验证行为：

- 只选择当前批次的主要核价接口记录；
- 网络证据优先，批量核价弹窗 DOM 作为补充；
- 弹窗未确认打开时，不把列表 DOM 行误认为调整后申报价；
- SKU 嵌套结构展开；
- SKC/SKU/商品维度去重和证据合并；
- 金额单位识别；
- 主图和轮播图清洗；
- 完整度、缺失、冲突、来源可信度和真实性标记；
- 敏感字段递归脱敏；
- 阻断包含平台写动作的采集结果。

输出字段名称继续兼容现有前端，包括 `quotes`、`counts.complete_quotes`、`counts.review_quotes`、`confidence_counts` 和 `authenticity_status_counts`。

### 6.2 货源发现

从旧货源实现迁移以下行为：

- 完整核价到图搜任务的转换；
- 同一 SKC 下 SKU 折叠和 `source_quote_keys` 保留；
- 特卖数据候选归一化；
- 候选 URL、offer ID、标题、主图、SKU 矩阵、价格、MOQ、运费和重量提取；
- 候选去重、视觉/文本同款证据、规格数量差异和 SKU 绑定状态；
- 推荐、待复核、待 SKU 验证和无可靠货源分类；
- 批次下一步行动摘要。

旧 `source_identity.py` 只迁移当前货源匹配实际调用的身份规则，不把无关商品处理规则一起带入。

### 6.3 利润计算

新 `profit_adapter.py` 调用：

`wh_local.modules.profit_activity.domain.engine.calculate_profit`

适配器负责把浮点或字符串输入转换成 `Decimal`、解析站点、取得设置快照，并把 `ProfitPreview` 转换为前端兼容字典。核价模块不得保存另一份利润阶梯或运费公式。

### 6.4 OneBound 1688

新模块通过注入的 Provider factory 使用：

`wh_local.data_collection.provider.OneBound1688Provider`

1688 查询使用独立的核价/货源调用预算与审计记录，不读写 `daily_selection_*` 表。Provider 只负责安全请求；候选转换和核价业务判断归 `price_verification.sourcing` 所有。宿主通过 `PriceVerificationRouteDependencies` 注入 actor resolver、Provider factory、Provider 配置 resolver、利润设置 resolver、SQLite 路径和输出目录。

## 7. 数据模型

所有业务表使用 `price_verification_` 前缀，所有业务记录必须包含 `workspace_id`。

### 7.1 插件配对与命令

- `price_verification_pairing_codes`：一次性连接码摘要、workspace、过期时间、使用状态；不保存连接码明文。
- `price_verification_plugin_sessions`：随机会话 token 摘要、浏览器、插件版本、能力、状态和最后心跳；不保存 token 明文。
- `price_verification_plugin_commands`：命令类型、幂等键、载荷、状态、脱敏结果、租约与时间戳。
- `price_verification_provider_budgets`：workspace、Provider 凭据指纹、上海日期、调用上限和已用次数；不保存原始凭据。

命令状态为：`queued`、`leased`、`running`、`succeeded`、`failed`、`cancelled`。初始租约为 120 秒，插件至少每 30 秒回传一次运行进度并续租 120 秒；租约超时后可安全重新领取。相同 workspace、命令类型和幂等键只允许一条有效命令。

### 7.2 核价快照

- `price_verification_quote_runs`：批次、来源命令、状态、计数、适配器版本和采集时间。
- `price_verification_quote_items`：SKC/SKU 身份、价格、标题、图片、证据摘要、完整度、真实性、缺失和冲突。

一次成功采集创建一个新快照。重新采集不得覆盖历史批次。原始插件结果先脱敏，再以受限证据 JSON 保存；Cookie、Authorization、Token、密码和会话字段不得落库。

### 7.3 货源快照

- `price_verification_sourcing_runs`：对应核价批次、来源模式、状态、任务数和计数。
- `price_verification_source_candidates`：候选货源、SKU、成本、运费、重量、匹配证据、利润预览和决策状态。

候选以 `workspace_id + sourcing_run_id + quote_key + candidate_key` 唯一。员工确认状态属于本地决策，不触发平台写入。

## 8. API 设计

### 8.1 正式业务 API

- `POST /api/v1/price-verification/plugin/pairing-codes`
- `GET /api/v1/price-verification/plugin/sessions`
- `GET /api/v1/price-verification/plugin/package`
- `GET /api/v1/price-verification/plugin/download`
- `POST /api/v1/price-verification/quote-runs`
- `GET /api/v1/price-verification/quote-runs/{run_id}`
- `GET /api/v1/price-verification/quote-runs/{run_id}/items`
- `POST /api/v1/price-verification/quote-runs/{run_id}/exports`
- `POST /api/v1/price-verification/sourcing-runs`
- `GET /api/v1/price-verification/sourcing-runs/{run_id}`
- `POST /api/v1/price-verification/sourcing-runs/{run_id}/retry`

正式业务 API 使用宿主注入的已认证 actor，并从 actor 得到 `actor_id` 和 `workspace_id`。

### 8.2 插件桥接 API

为兼容裁剪后的插件，宿主在根路径注册：

- `POST /plugin/connect`
- `POST /plugin/poll`
- `POST /plugin/result`

连接时使用至少 128 位熵、10 分钟内有效的一次性配对码；连接成功后只返回至少 256 位熵的随机插件会话 token。数据库只保存二者的 SHA-256 摘要。轮询和结果接口只接受插件会话 token，不接受工作台管理员 token。会话连续 10 分钟无心跳后标记为离线。

### 8.3 演示前端兼容 API

过渡期保留：

- `GET /plugin/sessions`
- `POST /plugin/sessions/{session_id}/commands`
- `GET /plugin/commands/{command_id}`
- `GET /plugin/latest-command`
- `GET /plugin/recent-commands`
- `GET /plugin/package`
- `GET /plugin/download`
- `POST /local/price-quote-discovery/preview`
- `POST /local/price-quote-discovery/export`
- `POST /local/source-discovery/browser-search/payload`
- `POST /local/source-discovery/browser-search/preview`
- `POST /local/source-discovery/onebound-search/preview`

兼容路由必须委托同一 service，不允许维护第二套业务逻辑。

## 9. 插件设计

插件使用 Manifest V3，只保留：

- 配对和本地工作台地址设置；
- `/plugin/poll` 轮询和 `/plugin/result` 回传；
- `temu_price_quote_discovery`；
- `source_browser_image_search`；
- Temu 核价网络探针和 DOM 补采；
- 特卖数据后台图搜；
- 1688 候选详情及 SKU 只读验证。

删除店小秘、老品分析、订单/流量分析、视频补传、商品入池和下架命令。权限仅保留 `storage`、`scripting`、`alarms`、`tabs` 以及实现所需最小权限。Host permissions 仅允许本机工作台、Temu、特卖数据、1688/Alibaba 和对应静态资源域名。

插件不得读取或回传 Cookie、Authorization header、Local Storage token 或页面密码。网络探针只保留允许的核价查询响应，禁止记录写接口请求体。

## 10. 安全与错误处理

- 只允许命令类型 `temu_price_quote_discovery` 和 `source_browser_image_search`；
- 后端和插件都维护平台写动作黑名单，任一侧命中即失败；
- 配对码单次使用、短时有效；插件 session token 只保存摘要；
- 工作台 token 与插件 token 完全分离；
- 所有查询按 workspace 隔离，跨 workspace 返回 404；
- 请求、结果和日志统一递归脱敏；
- 插件单次结果 JSON 上限为 16 MiB、最大嵌套深度为 20，拒绝二进制内容；
- 插件离线、页面不正确、弹窗未打开、登录失效、验证码、接口变化、部分超时分别使用稳定错误码；
- 部分货源结果可以保存，未完成条目标记为可重试；
- 导出路径只允许位于宿主配置的数据目录内；
- 外部图片请求沿用现有 SSRF 防护和大小限制。

## 11. 运行时与依赖

- 项目声明 Python `>=3.14`，测试和打包必须使用兼容解释器；
- 终端默认 Python 3.9 不作为验收运行时；
- `openpyxl>=3.1,<4` 加入统一项目依赖，用于核价 Excel 导出；
- 不新增浏览器自动化框架，网页读取由 MV3 插件在员工现有登录会话中完成；
- 测试不得访问真实 Temu、特卖数据或 OneBound 网络。

## 12. 测试策略

### 12.1 单元测试

- 网络证据、DOM 证据和混合证据核价解析；
- 当前批次筛选、金额单位、SKU 展开、去重和冲突；
- 敏感字段脱敏和平台写动作阻断；
- 完整核价到图搜任务转换；
- 候选归一化、去重、SKU 绑定、MOQ、运费和重量；
- 利润适配器与现有利润引擎结果一致；
- 推荐、待复核和失败分类；
- 配对码、会话摘要、命令租约和幂等性。

### 12.2 集成测试

- FastAPI `TestClient` 完整模拟：配对、插件连接、创建核价命令、轮询、回传、预览和导出；
- workspace A 不能读取 workspace B 的会话、命令或快照；
- 使用 Fake OneBound Provider 验证 1688 图搜链路；
- 重启 repository 后仍可恢复命令、核价和货源批次；
- 演示兼容接口与正式接口返回同一批业务结果。

### 12.3 插件测试

- Node 测试核价响应过滤器和敏感字段排除；
- Node 测试特卖数据候选卡和 1688 SKU 提取；
- 测试错误页面、未登录、验证码和空结果；
- 测试 manifest 不含无关站点和无关权限；
- 使用保存的脱敏 HTML/JSON fixture，不依赖实时网页。

### 12.4 人工验收

自动测试通过后，由员工在 Edge 中完成一次受控只读验收：

1. 安装裁剪插件并使用一次性配对码连接本地工作台；
2. 登录 Temu Seller Central 并打开价格待确认页面；
3. 启动批量采集，确认生成核价预览且没有平台写操作；
4. 对一条完整核价启动特卖数据或 1688 图搜；
5. 确认候选、SKU、成本、运费、利润和决策状态可见；
6. 导出核价文件；
7. 检查 SQLite 和日志中不存在 Cookie、Token 或 Authorization 内容。

## 13. 验收标准

实现完成必须同时满足：

1. 新代码全部位于 `wh_local.price_verification`，仅宿主注册和统一依赖声明允许在模块外做小范围修改；
2. Temu 核价采集和货源搜索均通过插件命令闭环运行；
3. 现有演示前端所需兼容接口可正常使用；
4. 核价和货源结果在重启后仍可恢复；
5. 所有记录按 workspace 隔离；
6. 插件只包含核价及货源相关能力；
7. 平台写动作在插件和后端双重阻断；
8. 自动测试不访问外部网络且全部通过；
9. 人工只读验收完成；
10. 没有凭据、Cookie、Token 或未脱敏原始响应进入源码、fixture、SQLite、导出或日志。

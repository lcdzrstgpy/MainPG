# POD、整店采集与新版后端底座集成设计

日期：2026-08-21
状态：已确认
目标基线：`feature/client-processing-auto-repair@dac01dbb`（2026-08-21 刷新的远端最新版）

## 1. 背景与目标

目标分支是当前上线最新版，已经包含远端账号、租户权限、API 管理、密钥保险箱、积分钱包、版本化定价、批量冻结/结算、商品处理失败项自动补跑、每日选品空采集重试和增量更新。

当前工作区另有尚未接入该底座的功能：

- 1688 按店铺搜索并自动进入商品处理草稿池；
- 新版 POD 定制工作台；
- 豆包标题处理；
- POD 店小秘导出；
- POD 与商品处理的独立 AI runtime；
- 浏览器采集插件的新增命令链路。

本次目标是在不覆盖新版后端底座的前提下，把这些能力作为新增模块接入，并让新版 POD 完整复用远端积分、定价、短期密钥和审计能力。

## 2. 已确认的产品决策

1. 采用目标基线上的定点移植，不整体合并旧功能分支。
2. 1688 整店采集和自动入池不扣积分；草稿后续进入商品处理时按商品处理规则计费。
3. 整店采集详情成功后自动入商品处理草稿池，不增加人工确认步骤。
4. 新版 `pod_customization` 是唯一 POD 模块；旧 `ai_service` POD 不保留、不迁移历史。
5. POD 复用“积分冻结 -> 短期密钥发放 -> 本地 worker 直连 provider -> 结果结算”的新版底座。
6. POD 使用独立动态定价项：`pod.title` 和 `pod.image`。
7. POD 模板在同一 workspace 内共享；批次、图片结果和导出记录仅创建人可见。
8. POD 标题、图片成功调用扣费；失败或无返回退款；用户重试只为重试调用重新冻结。
9. POD 导出不扣积分。
10. 商品处理继续使用服务端动态定价。目标代码当前默认五项合计为 45 积分；客户端不得把 40 或 45 硬编码为权威价格。
11. 除 `pod_customization` 与已确认的 1688 整店采集入口外，所有前端文件、导航、角色过滤、登录态和页面展示以远端目标分支为唯一权威版本。
12. 用户工作台不得重新暴露远端目标分支未展示的 `AI 服务` 或 `系统配置` 导航；POD 定价管理仍通过现有服务端管理员底座提供，不恢复本地 API Key 配置入口。

## 3. 集成原则

### 3.1 目标底座优先

以下目标版文件和能力是不可覆盖边界：

- `local-runtime/wh_local/app/main.py` 中的 PatchManager、管理员代理、远端会话和双 Product Processing 路由；
- `local-runtime/wh_local/db.py` 中的十分之一积分迁移、计费 schema、定价种子和 `BEGIN IMMEDIATE`；
- `local-runtime/wh_local/billing.py`；
- `local-runtime/wh_local/customer/*` 的认证、远端 API、凭据保险箱和管理员代理；
- Product Processing 的 router、service、provider、server AI context、batch billing 和自动补跑状态机；
- 系统管理、个人中心和增量更新能力。

共享文件只接受最小增量修改，不采用旧分支的整文件版本。前端同样执行这一规则：先恢复远端目标分支文件，再仅添加 POD 导航/页面和 Daily Selection 内的整店采集增量。

### 3.2 模块边界

#### `shop_collection`

负责店铺识别、列表分页、详情补齐、批次持久化、worker 租约、失败重试和自动入池。它通过窄接口调用 Product Processing，不拥有商品处理任务、AI 计费或自动补跑逻辑。

#### `pod_customization`

负责模板、标定、风格、批量出图、标题处理、结果管理、发布字段和店小秘导出。它使用独立 worker 和 runtime，但认证、积分、定价、凭据发放与审计全部来自目标底座。

#### `ai_runtime_core`

只提供可注入的线程池、HTTP session、provider semaphore 和限速器。它不决定 API key 来源，不允许把 Product Processing 或 POD 改回本地长期 key。

## 4. 整店采集设计

### 4.1 输入与身份解析

入口接受：

- 1688 商品链接；
- 可解析的 1688 店铺链接；
- 店铺 SID。

商品链接先通过 `item_get` 解析店铺身份。无法解析出合法 SID 时，批次以可读错误结束，不启动列表采集。

OneBound 凭据继续通过远端 collect-key 服务领取，不从本地配置回退。日志和数据库不得包含 key、secret 或远端 token。

collect-key 和 POD freeze/regrant 的指定凭据响应使用现有加密信封契约；客户端只在内存中解密。除这类指定加密信封外，任何普通 API 响应不得包含凭据材料。

整店列表和详情请求复用现有 workspace/account 维度的 OneBound API 预算，在每次真实请求前原子占用额度；列表页和详情分别记录 operation。该预算用于限额和并发治理，不产生积分账单。

### 4.2 采集与自动入池

1. 创建 workspace 隔离的持久化 shop batch。
2. 顺序调用 `item_search_shop`，最多 100 页，并对 offer 去重。
3. 商品详情使用全局最多 3 路并发，避免与每日选品的 `item_get` 形成失控并发。
4. 规范化详情后调用 `ProductProcessingService.intake_shop_candidate`。
5. 草稿和 V2 media assets/bindings 在同一事务中创建或刷新。
6. 详情成功立即自动入池；详情失败不创建残缺草稿，并留在原批次支持重试。

重采语义：

- 草稿状态：刷新来源信息和媒体绑定；
- processing/processed：跳过，不覆盖处理中或已完成结果；
- deleted：恢复草稿；
- 同一 workspace、同一 1688 offer 保持唯一；
- 不同 workspace 可分别拥有同一 offer。

### 4.3 并发、租约和取消

- 同一 workspace、同一店铺只允许一个活动批次。
- batch 和 item 都使用 lease token 与 fencing。
- 过期租约允许新 worker 恢复，旧 worker 的迟到写入必须被拒绝。
- 支持暂停、继续、取消和仅重试失败项。
- 取消后不接受迟到详情或迟到入池。
- worker 生命周期挂载在目标版应用 lifespan 中，不改变其他后台任务。

### 4.4 与每日选品和插件的关系

整店采集作为每日选品页面中的独立工作区接入，但不得覆盖目标版已有的 preview task、中断、真实进度、空采集重试和 SKU repull。

同时修复插件闭环：

- `/plugin/poll` 的响应结构在后端与扩展之间统一；
- 扩展在已有命令之外支持后端实际发布的 `temu_link_capture` 和 `temu_flux_accel`；
- running/succeeded/failed 回传契约一致；
- 必要的商品抓取结果能够进入现有草稿入口。

空采集后台重试成功后必须重新触发 SKU repull，避免候选原位写回但 SKU 永久缺失。

## 5. 新版 POD 设计

### 5.1 替换旧 POD

新版 `pod_customization` 替代旧 `ai_service` POD：

- 删除旧 POD 前端模式和导航入口；
- 停止注册旧 POD API；
- 停止旧 POD 中断任务恢复；
- 不迁移旧 POD 历史数据；
- 本次不执行破坏性的旧表删除，升级后应用不再读写旧表。

`ai_service` 的非 POD 能力是否继续展示，保持目标基线现状；本次只移除其 POD 子能力。

### 5.2 权限和可见性

- 模板查询按 `workspace_id`，同 workspace 成员共享。
- 模板新增、编辑、标定使用 `pod_customization.template_manage`。
- 批次、图片结果、发布字段和导出记录同时约束 `workspace_id + owner_user_id`。
- 创建、重试、导出等动作必须由登录 Actor 发起。

### 5.3 AI runtime 与 provider

POD 使用与 Product Processing 物理隔离的 executor、HTTP session、限速器和图片并发许可。目标版 `ProductImageProcessor` 和 `DoubaoArkClient` 增加可注入 transport/runtime 的能力，但默认 key 来源仍是服务端上下文。

POD worker 不依赖请求线程 ContextVar 自动传播。每个实际执行线程显式建立包含远端 token、freeze id、granted keys 和 usage identity 的上下文。

禁止：

- 读取 `ARK_API_KEY` 作为生产回退；
- 把字面量 `server-managed` 当作真实 provider key；
- 在数据库、侧车文件或日志中持久化 token/key；
- 让 POD 与 Product Processing 共用长任务线程池。

### 5.4 标题、图片与导出

- 标题使用豆包，并支持结构化响应约束。
- 图片使用短期 Wuyin grant 本地直连。
- 每次 provider 调用具有稳定幂等键。
- 只允许重试 failed/interrupted 调用，成功调用不可重复重试。
- 店小秘导出读取批次最终标题、图片和发布字段，导出动作不产生积分事件。

## 6. POD 积分与 API 设计

### 6.1 单一权威

POD 复用现有：

- wallet；
- hash-chain ledger；
- pricing rules/items/changelog；
- batch freeze/items；
- credential vault/key grants；
- admin proxy 和系统管理页；
- TTL 释放、幂等结算和版本绑定。

新增定价键：

- `pod.title`；
- `pod.image`。

生产启用 POD 前，这两个定价键必须由系统管理配置。缺少任一需要使用的价格时，freeze 失败关闭，不以 0 元或客户端默认值继续处理。

### 6.2 API 边界

商品处理现有 batch freeze/settle 路由保持兼容。POD 增加以下独立路由契约，但底层调用共享 billing service，不复制钱包或账本实现：

- `POST /api/customer/billing/pod/freeze`；
- `POST /api/customer/billing/pod/settle`；
- `GET /api/customer/billing/pod/{freeze_id}`。

POD freeze 请求包含调用计划：标题调用数、图片调用数和稳定调用标识。服务端按冻结时的 rule version 计算最大冻结额并返回：

- freeze id；
- rule version；
- frozen points；
- 短期 Ark/Wuyin grants；
- server expires_at。

POD settle 只上报每个调用的 `success` 或 `no_return` 结果。金额由服务端按冻结版本计算。重复 settle 返回相同结果。

### 6.3 结算规则

- 成功标题调用：扣 `pod.title`；
- 成功图片调用：扣 `pod.image`；
- provider 失败、超时或无返回：对应调用全退；
- 未开始调用：全退；
- 用户重试失败项：为新的重试调用单独 freeze，不重复扣成功调用；
- 导出：不扣积分。

调用计划与实际结算明细必须一一对应，服务端拒绝多报、漏报、重复 call id 或未知 feature。

## 7. 数据库迁移

迁移坚持 forward-only，不改写已执行 marker 对应的历史 SQL。

### 7.1 POD

按顺序注册：

1. `001_pod_customization`；
2. `002_direct_listing_trials`；
3. `003_style_grid_v2`；
4. `004_style_grid_publications`；
5. `005_dianxiaomi_exports`；
6. `006_pod_titles`；
7. 新增 `007_requested_count_upgrade`。

001 保持发布时语义，007 负责把旧数据库的 requested_count 限制升级为 `1–200`。新库也顺序执行 001–007，保证新旧库最终 schema 一致。

### 7.2 整店采集与商品处理

- 注册 shop collection 005/006；
- 注册租约/fencing 所需升级；
- 注册 `004_shop_candidate_uniqueness` 条件唯一索引；
- 保留 Product Processing 现有媒体资产迁移和 direct billing 迁移。

### 7.3 目标底座修复

补注册已经存在但遗漏的 Price Verification 007/008。不得删除或重建目标计费表，不得移除十分之一积分升级和 `BEGIN IMMEDIATE`。

迁移测试覆盖：

- 全新数据库；
- 目标版数据库；
- 仅执行过 POD 001 的数据库；
- 已执行 POD 001–006 的开发数据库；
- 重复启动和重复迁移。

## 8. 错误恢复与安全

### 8.1 POD 状态

- freeze 失败：不启动任务；
- provider 失败：记录 `no_return` 并退款；
- settle 网络状态不确定：进入 `settlement_pending`，使用同一幂等键重试；
- 进程重启且无 token/key：进入 `billing_auth_required`；
- 用户重新登录后，可为原有效 freeze 重新领取短期 grant；
- grant 过期时只允许在 freeze 有效、账号仍有权限时重新发放；
- 远端账号停用或权限撤销后，不再发放新 grant，未开始项暂停。

本地只持久化 freeze id、调用计划、调用结果和非敏感状态，不持久化任何 token/key。

### 8.2 保密要求

Authorization、远端 token、OneBound key/secret、Ark key 和 Wuyin key 不得出现在：

- SQLite；
- JSON 侧车；
- 应用日志；
- 异常消息；
- 指定 freeze/regrant/collect-key 加密信封之外的 API 响应；
- 测试快照。

所有跨 workspace/owner 访问返回无泄露的 404 或权限错误。

## 9. 测试策略

### 9.1 后端

- shop provider、解析、分页、去重、预算和详情并发；
- repository、lease、fencing、暂停、取消、恢复和失败重试；
- direct shop intake 的 create/refresh/skip/revive 和事务原子性；
- POD contracts、repository、worker、runtime 隔离、标题、图片、导出；
- POD freeze/settle 的余额不足、全部成功、部分失败、全部失败、重复请求和断网恢复；
- workspace 与 owner 隔离；
- fresh/upgrade/repeated migrations；
- 目标商品处理自动补跑和现有计费回归。

### 9.2 前端与插件

- POD 页面模型、API 契约、模板共享、结果私有、重试和导出；
- ShopCollectionPanel、轮询、暂停、取消、失败重试和自动入池状态；
- 目标 DailySelectionPage 的 preview task、中断、空重试和 SKU repull 不退化；
- 导航权限与旧 POD 入口移除；
- 插件 connect/poll/result 契约和命令类型；
- TypeScript 测试与生产构建。

### 9.3 打包与真实 smoke

- 本地 runtime 启动和 health；
- Windows workbench 打包启动；
- 低额度真实链路：1688 店铺 -> 自动入草稿池；
- 低额度真实链路：POD freeze -> 豆包标题/出图 -> settle -> 店小秘导出；
- 运行后检查日志和数据库无敏感凭据。

## 10. 实施与上线顺序

1. 从目标提交建立隔离集成分支，保留当前脏工作区并运行目标基线测试。
2. 移植 shop provider、repository、worker、窄 intake 和数据库迁移。
3. 手工把 Shop UI 嵌入目标版 DailySelectionPage，并完成无计费自动入池回归。
4. 移植 POD 模块、runtime 隔离、标题和导出；移除旧 POD 入口。
5. 扩展共享 billing service、远端 API、pricing items、grants 和管理员页面。
6. 接入 POD worker 的显式 billing context、freeze/settle 和重启恢复。
7. 修复插件闭环和空采集重试后的 SKU repull。
8. 运行完整后端、前端、插件、迁移、打包和低额度真实 smoke。
9. 输出最终差异报告，确认目标认证、计费、管理员代理、更新和商品处理自动补跑未被覆盖。

## 11. 完成标准

满足以下条件才视为完成：

- 所有自动化测试和生产构建通过；
- 全新数据库和历史数据库均可升级且重复执行安全；
- 1688 整店采集可自动入草稿池，失败项可恢复且不重复入池；
- 新版 POD 是唯一入口，旧 POD 不再运行；
- POD 模板 workspace 共享，结果和导出 owner 私有；
- POD 所有 AI 调用均经过远端 freeze、短期 grant 和 settle；
- 失败退款、重复请求、断网恢复和进程重启行为可验证；
- 商品处理现有动态计费、自动补跑、系统管理和更新功能无回归；
- API、日志和数据库中没有敏感凭据。

## 12. 非目标

- 不对 1688/OneBound 采集收取积分；
- 不迁移旧 `ai_service` POD 历史；
- 不在本次删除旧 POD 数据表；
- 不建立第二套认证、钱包、账本或独立侧车服务；
- 不修改商品处理现有定价，只修正客户端硬编码或说明不一致；
- 不进行与本次接入无关的大范围重构。

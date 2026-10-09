# 最终修改计划书：服务端定价引擎 + 客户端直连 AI + 人工干预管理

日期：2026-08-20
状态：已确认（v2.1：四类全直连 / 草案定价 8/8/7/12/10 / 自报结算一期 / 图片单 key、文本多 key）
版本：v2.1

---

## 一、背景与目标

当前 AI 调用 100% 走服务端网关中转（文本/视觉 → `/api/customer/ai/chat`，图片 → `/api/customer/ai/image`），存在两个核心问题：

1. **高并发瓶颈**：服务端单进程 uvicorn + 同步长轮询（图片最多等 620s），100 用户 × 单机 4 并发图片 ≈ 120+ 在途请求，远超 32~40 线程承载 → 502/504 拥堵。
2. **定价不可管**：虽有 `billing_pricing_rules` 单行表 + `PUT /api/admin/billing/pricing`，但无子项粒度、无历史版本、无变更日志、无管理界面。

**本次目标架构**：

```
客户端（本地直连第三方）                    服务端 auth-api（仅密钥保险箱 + 计费）
  文本/识图 → 直连火山方舟                      密钥池（多 key 轮转 + 6h 时效下发）
  四宫格/详情图 → 直连无印                     批次冻结/结算计费
  每条上报子项状态                             可配置定价规则引擎
                                              人工干预管理界面 + 审计日志
```

---

## 二、现状盘点（已核实）

### 2.1 客户端直连代码现状

| 项 | 现状 | 证据 |
|---|---|---|
| 无印图片直连 | **存在但被屏蔽**：`_request_wuyin_image`（media.py L1303）完整可用 | `_providers`（media.py L1007-1008）一旦存在 `server-managed-wuyin` 就剔除其余 base_url |
| 火山文本直连 | `doubao_ark.py` 的 `API_URL` 直连常量还在（L13），但 `complete` 走网关 | `DoubaoArkClient` 读 `remote_token()`+`usage_id` 打网关 |
| 配置硬编码 | `provider_config.py` 固定 `base_url="server-managed-wuyin"`、`api_key=""` | L132-139 注释"不得把已保存的上游 AI key 接回调用链" |
| 计费上下文 | `server_ai_proxy.py` 的 ContextVar（token + usage_id） | 随线程传播，直连改造可复用 |

**结论：切直连是"配置级 + 装配级"改动，非重写。**

### 2.2 服务端计费/定价现状

| 项 | 现状 | 缺口 |
|---|---|---|
| 定价表 | `billing_pricing_rules` 单行（rule_id=1），字段 text/image reserve+charge | 无子项粒度（title/desc/尺寸/四宫格/详情图） |
| 改价入口 | `PUT /api/admin/billing/pricing`（需 admin/owner） | 无前端界面 |
| 版本 | `rule_version` 每次 +1 | 无历史表，旧版本无法回溯 |
| 日志 | 仅 `updated_by` 列 | **无变更日志表、无 Python logging** |
| reserve/settle | `reserve_ai_usage` / `settle_ai_usage_success/failure` 完整（幂等 + 快照 + 账本哈希链） | 结算按 feature 单值，需改为按子项明细 |

### 2.3 前端现状

| 项 | 现状 |
|---|---|
| 管理入口 | 无。`basic_settings` 模块是空壳（API 齐全但 UI 未接入，测试断言 PersonalCenter 含它但实际没有） |
| 权限 | 无任何 role/admin 判断，侧边栏全量开放 |
| 可参考 UI | `ProfitActivityTestPage` 的站点费率编辑弹窗（L960-992） |
| 计费前端 API | `personal_center/api/personalCenterApi.ts`（summary 含 pricing 只读字段） |

---

## 三、定价引擎设计（服务端实现，重点）

### 3.1 数据模型（新增表，db.py 迁移）

**表 A：`billing_pricing_items`（子项单价，当前生效版本）**

```sql
CREATE TABLE billing_pricing_items (
    rule_version   INTEGER NOT NULL,          -- 关联 billing_pricing_rules.rule_version
    feature_key    TEXT NOT NULL,             -- title / description / product_dimensions / four_grid / detail_images
    charge_points  INTEGER NOT NULL,          -- 成功全价
    intercept_refund_ratio  REAL NOT NULL DEFAULT 0.5,  -- 质量门拦截退一半
    no_return_refund_ratio  REAL NOT NULL DEFAULT 1.0,  -- 上游无返回全退
    effective_at   TEXT NOT NULL,
    PRIMARY KEY (rule_version, feature_key)
);
```

**表 B：`billing_pricing_changelog`（变更审计）**

```sql
CREATE TABLE billing_pricing_changelog (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_version  INTEGER NOT NULL,           -- 变更后版本
    changed_by    TEXT NOT NULL,              -- 操作人 account_id / username
    change_reason TEXT NOT NULL DEFAULT '',   -- 操作原因（必填）
    before_json   TEXT NOT NULL,              -- 变更前完整定价 JSON
    after_json    TEXT NOT NULL,              -- 变更后完整定价 JSON
    created_at    TEXT NOT NULL
);
```

**表 C：`billing_key_grants`（密钥发放审计）**

```sql
CREATE TABLE billing_key_grants (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT NOT NULL,
    freeze_id     TEXT NOT NULL,
    provider      TEXT NOT NULL,              -- wuyin / ark
    key_label     TEXT NOT NULL,              -- 密钥标识（不含明文）
    granted_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL               -- 6h 后过期
);
```

### 3.2 定价规则（按用户已确认的决策）

| 规则 | 取值 |
|---|---|
| 子项单价 | 草案：title=8 / description=8 / dimensions=7 / four_grid=12 / detail_images=10（总和 45，可后台改） |
| 冻结 | 批次冻结 = N 条 × 45（按最大范围预扣） |
| 成功子项 | 扣全价 |
| 上游无返回（拒绝/超时） | 退全价（ratio=1.0） |
| 有返回但质量门拦截 | 退半价（ratio=0.5） |
| 单条扣费 | Σ(成功价 + 拦截价×0.5)，落在 0~45 |

### 3.3 结算接口（新增，服务端）

```
POST /api/customer/billing/batch/freeze
    {link_count, scope} → {freeze_id, keys[], frozen_points, expires_at}

POST /api/customer/billing/batch/settle
    {freeze_id, items:[{link_idx, subitems:[{feature, status: success|intercept|no_return}]}]}
    → {charged_points, refunded_points}

GET  /api/customer/billing/batch/{freeze_id}   → 查询冻结状态（启动对账用）
```

### 3.4 管理接口（新增，服务端）

```
GET  /api/admin/billing/pricing           → 当前版本定价（含子项明细）
PUT  /api/admin/billing/pricing           → 修改子项单价（rule_version+1，写 changelog）
GET  /api/admin/billing/pricing/versions  → 历史版本列表
GET  /api/admin/billing/pricing/changelog → 变更审计日志
GET  /api/admin/billing/keys/grants       → 密钥发放记录（脱敏）
```

---

## 四、人工干预管理界面（前端）

### 4.1 新增「系统管理」模块（前端）

- 复用 `modules/basic_settings` 的 API 基建，新增页面：
  - **定价管理**：编辑 5 个子项单价（保存前校验总和、必填原因）、查看历史版本、查看变更日志
  - **密钥发放记录**：只读展示 key 发放审计（脱敏）
  - **冻结/结算记录**：批量冻结查询、异常释放操作
- 技术选型：复用现有纯状态 Tab 工作台（`modules.ts` + `WorkspaceShell.renderTab`），参考 `ProfitActivityTestPage` 的站点费率弹窗

### 4.2 权限控制（前后端双向）

- **后端**：沿用 `_require_billing_admin`（admin/owner 角色，auth_server.py L818-822）→ 新路由全部挂 `_require_billing_admin`
- **前端**：`GET /api/customer/me` 返回 role 后，`WorkspaceShell` 按 `role ∈ {admin, owner}` 才渲染「系统管理」入口（注册表加 `adminOnly` 标记）

### 4.3 前后端数据同步与一致性（设计原则）

1. **单一权威**：定价规则、余额、账本全部以服务端 SQLite 为唯一权威。前端定价页只读展示，改价走 `PUT /api/admin/billing/pricing` 后重新拉取最新版本，不允许本地缓存定价参与任何金额计算。
2. **结算金额只由服务端计算**：客户端仅上报子项状态（`success / intercept / no_return`），扣费/退款金额由服务端 `compute_batch_charge` 计算并返回，两端不存在独立算价，杜绝算出差价。
3. **规则版本绑定在途批次**：`freeze` 响应返回 `rule_version`，客户端 `settle` 原样带回，服务端按冻结时版本结算——中途改价不影响已在途批次，避免价格突变。
4. **幂等 + 双端对账**：`freeze/settle` 均幂等（同 `freeze_id` 重放返回已结算结果）；客户端启动时 `GET /api/customer/billing/batch/{freeze_id}` 拉取服务端状态，未结算批次自动补 `settle`；服务端 TTL 兜底释放。
5. **前端展示一致性**：余额/冻结展示一律以服务端账本返回为准（`/api/customer/me`），客户端本地镜像仅作缓存，关键操作（freeze/settle）后强制刷新。

---

## 五、客户端直连改造

### 5.0 四类 AI 能力全部切本地直连（本次确认项）

| AI 能力 | 上游服务 | 客户端直连模块 | 改造方式 |
|---|---|---|---|
| 文本（标题/描述/变种翻译） | 火山方舟文本模型 | `doubao_ark.py` | `complete` 恢复直连 `API_URL`，key 从下发上下文取；prompt 契约/语言校验复用客户端 `language_contract.py` |
| 识图（豆包视觉：主体识别/初步标题/尺寸/复核） | 火山方舟视觉模型 | `doubao_ark.py`（vision 分支） | 同样直连 |
| 四宫格图 | 无印图片编辑 API | `media.py _request_wuyin_image` | 启用已存在直连路径（提交→轮询 detail） |
| 详情图 | 无印图片编辑 API | `media.py _request_wuyin_image` | 启用已存在直连路径 |

**服务端 auth-api 只保留**：密钥保险箱（freeze 时下发 6h 密钥）+ 计费（freeze/settle）+ 密钥池轮转 + 管理界面。客户端直连期间服务端不做任何第三方请求转发。

### 5.1 直连装配（客户端）

| 文件 | 改动 |
|---|---|
| `provider_config.py` | `resolve_ai_provider` 改为从 `server_ai_context` 取下发密钥构造 provider（base_url=api.wuyinkeji.com / ark 域名，api_key=下发值），移除 server-managed 硬编码 |
| `media.py` | 启用 `_request_wuyin_image` 直连路径（提交→轮询 detail）；`_providers` 过滤逻辑保留但对直连 base_url 放行 |
| `doubao_ark.py` | `complete` 恢复直连 `API_URL`（ark.cn-beijing.volces.com），key 从上下文取 |
| `server_ai_proxy.py` | ContextVar 扩展：`granted_keys`（provider→key 映射，随 freeze 下发） |
| `service.py` | `_process` 批次流程：freeze → server_ai_context(token, keys) → 直连 → 逐条上报 → settle |

### 5.2 批次计费流程（客户端）

```
提交批次 → POST /batch/freeze（冻结 N×45）
   → 拿到 {freeze_id, keys[6h 时效]}
   → server_ai_context(token, keys) 内直连第三方处理全部商品
   → 每条每子项记录 status（success/intercept/no_return）
   → POST /batch/settle（上报明细，服务端算扣费/退款）
   → 失败/断网 → 启动时自动对账补 settle（POST /batch/{freeze_id} 查状态）
```

### 5.3 防永久冻结（已确认决策）

- 服务端 TTL：freeze 后 7 天未 settle → 自动释放全部冻结（后台定时任务）
- 客户端启动对账：发现本地未结算 freeze → 自动补 settle

---

## 六、安全风险清单与对策（重点评审）

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| 1 | **密钥下发后客户端被反编译提取** → 时效内盗刷 | 高 | 6h 时效 + 密钥池轮转 + 发放审计 + 异常用量熔断 + 领前查余额 |
| 2 | **客户端虚报成功/失败逃费**（自报结算） | 中高 | 固定单价下收益有限；服务端抽样复核（对比 key 实际用量）+ 黑名单；二期加上报签名 |
| 3 | **上游主 key 泄露给非授权用户**（共享 key） | 高 | 仅 freeze 时对余额充足账号下发；6h 作废；发放记录绑定 account |
| 4 | **批量冻结后客户端崩溃/断网 → 积分永久锁定** | 高 | 服务端 TTL 7 天自动释放 + 客户端启动对账补 settle |
| 5 | **定价被越权修改** | 高 | 全部 admin 路由挂 `_require_billing_admin`；前端 adminOnly 隐藏；changelog 强制操作人+原因 |
| 6 | **改价无审计、无法回溯** | 中 | `billing_pricing_changelog`（before/after JSON），禁删只追加 |
| 7 | **单 key 单点故障（无印 502/上游抖动）** | 中 | 图片侧照搬文本侧密钥池多 key + 失败熔断 + 冷却 |
| 8 | **密钥泄露到日志/请求体** | 中 | 日志脱敏（沿用 BILLING_SECURITY.md）；明文只经服务端↔客户端加密通道，入库 Fernet 加密 |
| 9 | **固定单价与真实成本偏离 → 亏损** | 中 | 定价引擎可配 + 成本监控报表（每日实际消耗 vs 扣费） |
| 10 | **中间人截获下发密钥/篡改请求** | 中 | 已走 HTTPS；二期加下发响应签名 + settle 请求 HMAC 签名 |
| 11 | **客户端直连绕过语言契约/质检** | 低中 | 契约校验逻辑客户端复用（`language_contract.py`）；服务端抽样质检（二期） |
| 12 | **充值回调未验签** | 低 | 当前 fail-closed（未配置验签不入账），保持不变 |
| 13 | **freeze 滥用（反复冻结不结算占额度，拖到 TTL）** | 中 | 每账号在途冻结额度上限（如 ≤2×余额）+ 冻结频率限制 + TTL 缩短至 7 天 |
| 14 | **密钥池耗尽（6h 时效 + key 数量少，冻结高峰领空）** | 中 | 密钥池余量监控告警；余量不足时 freeze 接口返回可读错误而非硬失败 |
| 15 | **某把上游 key 被封禁/欠费 → 全量中断** | 中 | 多 key 隔离 + 自动摘除失败 key + 冷却重试，不拖垮整池 |
| 16 | **管理接口被爆破/滥用** | 中 | 登录限流（沿用现有）+ 管理操作审计；admin 路由不改动默认禁 CORS |
| 17 | **管理界面输入 XSS**（改价原因等） | 低中 | 前端渲染转义 + 后端 Pydantic 长度/字符白名单校验 |
| 18 | **二改客户端**（改代码直接取 key / 逃费） | 中 | 时效短 + 黑名单 + 用量异常检测；后续可加安装包签名校验 |
| 19 | **客户端本地时钟不可信** | 低 | 时效判定一律用服务端时间（freeze 返回服务端 `expires_at`），客户端只透传 |
| 20 | **结算数据与冻结批次不一致**（多报/漏报子项） | 中 | 服务端校验 settle 明细条数与 link_count 一致；账本为唯一权威；rule_version 绑定在途批次 |

---

## 七、修改文件清单（按层）

### 服务端（HK ECS）
| 文件 | 改动 |
|---|---|
| `wh_local/db.py` | 新增 3 张表（pricing_items / changelog / key_grants）+ 迁移 |
| `wh_local/billing.py` | 定价引擎：`active_pricing` 返回子项明细、`update_active_pricing` 写 changelog、新增 `compute_batch_charge`、`freeze_batch_points` / `settle_batch_points` / TTL 释放 |
| `wh_local/customer/auth_server.py` | 新增 batch/freeze、batch/settle、batch/{id} 路由；扩展 admin pricing 路由（GET 子项 / PUT 带原因 / versions / changelog / keys grants）；`_server_provider_secret` 改造为多 key 池 + 6h 发放 |
| `wh_local/customer/credential_vault.py` | 图片侧多 key 支持（enabled_secrets 已有，补充轮转取用） |

### 客户端（MainPG 本地）
| 文件 | 改动 |
|---|---|
| `wh_local/modules/product_processing/provider_config.py` | 解除 server-managed 硬编码，从上下文取下发密钥 |
| `.../infrastructure/media.py` | 启用直连 `_request_wuyin_image` |
| `.../doubao_ark.py` | `complete` 直连火山方舟 |
| `.../server_ai_proxy.py` | ContextVar 扩展 granted_keys |
| `.../service.py` | 批次 freeze→settle 流程 + 启动自动对账 |

### 前端（web-frontend）
| 文件 | 改动 |
|---|---|
| `src/modules/basic_settings/` | 实现定价管理页面（定价编辑/历史/日志/密钥记录） |
| `src/app/navigation/modules.ts` | 注册「系统管理」模块（adminOnly） |
| `src/app/layout/WorkspaceShell.tsx` | admin 角色才渲染入口 + renderTab 分支 |
| `src/modules/personal_center/api/personalCenterApi.ts` | 新增 admin 定价管理 API |

---

## 八、技术选型

| 项 | 选型 | 理由 |
|---|---|---|
| 定价存储 | SQLite（现有） | 单机规模足够，零新增依赖 |
| 后端定时任务 | auth_server 内线程定时器 | 现有无任务队列，TTL 释放低频（每小时） |
| 密钥加密 | 沿用 Fernet vault | 已在用 |
| 直连 HTTP | requests（客户端）/ 现有 `_SESSION` | 已有代码 |
| 前端状态管理 | 复用现有纯 state Tab 工作台 | 无 react-router，保持一致 |
| 结算可信度 | 客户端自报 + 服务端抽样复核 | 固定单价下成本可控（详见风险#2） |

---

## 九、实施顺序与验证

```
阶段 1：服务端定价引擎 + 计费改造
  ① db.py 建表迁移 → ② billing.py 引擎 → ③ auth_server.py 路由 + 密钥池
  验证：单测（freeze/settle 边界、TTL 释放、changelog）+ mock 联调

阶段 2：前端管理界面
  ④ basic_settings 定价页 + 权限控制 → ⑤ 路由注册
  验证：admin 角色可编辑/历史/日志；operator 不可见

阶段 3：客户端直连 + 批次计费
  ⑥ provider_config/media/doubao_ark 切直连 → ⑦ service 批次流程 → ⑧ 启动对账
  验证：mock provider（不触发真实付费调用）+ 全量单测

阶段 4：安全加固 + 联调
  ⑨ 密钥发放审计 / 脱敏 / TTL 验证 → ⑩ 全链路 mock 联调 → ⑪ 灰度内测
```

---

## 十、决策确认记录（2026-08-20 用户已确认）

0. **四类 AI 全部切本地直连** → ✅ 已确认（文本/识图 → 火山方舟；四宫格/详情图 → 无印）
1. **子项单价草案 8/8/7/12/10（总和 45）** → ✅ 已确认
2. **结算可信度：客户端自报 + 固定单价一期，二期加上报签名/抽样复核** → ✅ 已确认
3. **服务端部署：只改 auth-api（8011）计费/密钥部分，不动 workbench（8010）网关** → ✅ 已确认（沿用此前选择）
   - **微调说明（阶段 2 实施时）**：管理界面要从工作台同源访问 admin API，workbench（8010）新增一个**纯 HTTP 透传路由** `/api/admin/billing/*`（`customer/admin_proxy.py`，本地会话→远端 token 解析 + 路径白名单 billing/*），不改任何业务网关逻辑。已用单测覆盖透传鉴权/路径白名单/401/403。
4. **密钥池**：图片侧无印目前只有 **1 把 key** → 先按单 key 实施（多 key 架构保留，后续加 key 自动生效）；文本侧火山方舟已有**多个子 key** → 文本侧密钥池即刻生效
5. **灰度策略**：老客户端仍走服务端网关（旧路由保留），新客户端走直连，双轨并存 → ✅ 默认接受

# POD 计费与对账：服务端重构方案

状态：**方案 v1.1**（2026-10-23，已回客户端核对清单，17 条逐条定论）
输入基线：[POD 四模块计费账本与对账设计 v0.8](./2026-10-23-pod-billing-ledger-reconciliation-design.md) 第 8 节「目标契约」
目标读者：服务端研发、运维、计费运营

---

## 0. 一页结论

1. **不改造旧结构，重建一个计费内核**：`freeze → settle/close/ttl → ledger` 一条主链路，一张冻结表、一个幂等命名空间、一本钱包账。四模块的差异只存在于「画像适配层」（定价 + 每单元判定），不进入内核。
2. **服务端是唯一权威**：单价快照、金额、逐单元明细、释放金额全部由服务端计算并返回；客户端只记账、只做告警。
3. **钱与状态在同一个事务里**：任何时刻不存在「钱扣了没单号」「单号在钱没扣」「已释放却仍可结算」的中间态。响应丢包靠同键重放取回，不靠客户端补偿。
4. **接口收敛为一个写入口 + 一个读入口**：`POST /v2/pod/freezes`（含半定制、裂变）、`GET /v2/pod/freezes/{id}`。半定制的 `item_count = link_count × 4` 下沉到画像校验，不再保留独立路由。
5. **新增网关计量作为结算上限**：结算金额不得超过网关实际记录的调用，否则「报成功其实没调」无法发现。
6. **旧接口不删除、只转译**：灰度期内 v1 请求转译进 v2 内核，影子记账比对通过后切流，最后下线。

本次改造**不改单价算法**，只改冻结、重放、收口、计量、记账的语义。

### 0.1 v1.1 决策（对应客户端核对清单）

| # | 决策 | 章节 |
|---|---|---|
| D1 | TTL 固定 30 分钟（本期不分画像）；新增 `settle_deadline = expires_at + 10min`，宽限内结算合法，只有超过它才算过期 | 4.2 / 5.2 / 7 |
| D2 | 价目表只有**单一价**，不设区间、不做随机；POD 四模块固定价：全定制/复刻 A/裂变 45、半定制 32/组、列式与复刻 B 180 | 3.2 / 3.3 / 附录 E |
| D3 | close 在 S2 可用，但 S2 只按客户端已上报判定（未上报 = `no_return`）；S3 计量上线后切 metered | 5.5 / 12 |
| D4 | `request_hash` 改为语义字段白名单，排除 `app_version` 等 | 4.4 |
| D5 | `link_idx` 从 0 起（0..N-1），v1 转译层负责 ±1 | 5.4 / 附录 D |
| D6 | `unit_ids` v2 必填；v1 由转译层按固定纯函数规则合成 | 附录 D |
| D7 | `scope` v2 只收对象；`four_grid → image` 由转译层映射 | 附录 D |
| D8 | `rule_version` 统一不透明字符串，只做相等比较 | 5.1 / 附录 D |
| D9 | 幂等键以头为准，body 可选镜像，不一致 400，永不 500 | 5.1 / 附录 A |
| D10 | 余额不足统一 400 `insufficient_balance`，不再使用 402 | 5.2 / 附录 A |
| D11 | 可重试只有 429 / 5xx / 网络超时 | 5.1 / 附录 A |
| D12 | 零计费统一 `pod_zero_v1`；判定准则是 `held == 0`，不看画像名 | 3.3 |
| D13 | 取消 `retry_premium_points`，v1 回执固定回 0 | 3.4 / 5.4 / 8.1 |
| D14 | 重试模型：每次重试开新冻结；需要「无论成败都扣」时用画像级 `charge_policy=attempt` | 3.4 / 5.4 / 14 |
| D15 | open 列表返回 `device_id` / `workspace_id` | 5.7 |
| D16 | 单写者约束与压测结论进 S0 验收 | 1.4 / 12 |

---

## 1. 范围与原则

### 1.1 目标

| # | 目标 | 验收方式 |
|---|---|---|
| 1 | 每笔冻结有唯一单号，从「打算花钱」到「最终扣/退」可追溯 | 任一 `freeze_id` 可查到完整事件链与账本流水 |
| 2 | 断网/崩溃/重启后自动收口，不依赖人工 | 杀掉进程后重启，未结冻结在 TTL 内闭环 |
| 3 | 四模块差异收敛到画像层 | 内核代码无 `if module == ...` 分支 |
| 4 | 每日可出与服务端流水逐笔对平的表 | 日终恒等式差异恒为 0 |

### 1.2 非目标

- 不改单价公式与价格阶梯本身；价格来自配置，本方案只负责**固化快照**与**按快照结算**。
- 不覆盖 `combo_kit`、`product_processing`、充值/活动/人工调整；它们是账本的**外部流水**，在日报中单独列示。
- 不做客户端本地账本（由客户端方案负责），不做客户端展示。
- 不引入新的营销策略；裂变并入统一计费是既定口径。

### 1.3 设计原则

1. **单一主链路**：只允许一个创建入口、一个结算入口、一套状态机。任何模块不得绕过内核直接改余额。
2. **账本只追加**：`ledger` / `events` / `settlements` 三张表只 INSERT，不 UPDATE、不 DELETE。状态列只做受控 CAS 迁移。
3. **金额整数化**：内部统一用 0.1 积分的整数（decipoint）运算；API 出口转成最多 1 位小数的 JSON number。**全链路无浮点**。
4. **先落账后外呼**：任何网络调用（密钥拉取、上游下发）不得位于事务内，事务内只做本地读写。
5. **契约先于实现**：错误码、字段、状态值由本文固定，实现不得自行扩展语义。
6. **可回滚**：每一步都能通过配置开关回到旧链路，且旧链路不依赖新表。

### 1.4 技术基线

| 项 | 取值 | 说明 |
|---|---|---|
| 语言/框架 | Python 3.10+ / FastAPI | 与现有服务一致 |
| 存储 | SQLite（WAL，`BEGIN IMMEDIATE` 单写者） | 计费服务**单进程单 worker**；禁止多 worker 同时写同一库文件 |
| 缓存/锁 | Redis（可选） | 仅在多实例演进时启用；单实例以内进程锁为准 |
| 前置 | nginx | 计费接口单独 location，独立限流与超时 |
| 部署 | systemd 单服务 | 计费内核与鉴权同进程，避免跨服务分布式事务 |

> **硬约束**：如果未来计费服务要多实例，必须先迁 PostgreSQL，再谈水平扩容。SQLite 下多实例写入不满足第 4 节的原子性要求。该约束是 **S0 的验收项**：S0 必须给出单进程写入的压测结论（并发创建/结算 p99、写队列深度、事务最长占用），不达标不得进入 S1。

---

## 2. 领域模型

### 2.1 术语

| 术语 | 含义 |
|---|---|
| 结算单元 unit | 一次可独立计费的最小对象：款（style）/ 组（group）/ 面板（panel） |
| 画像 profile | 一组「单位、单价、倍率、scope、判定规则」的命名集合，如 `pod_random_v1` |
| 规则版本 rule_version | 画像配置的不可变版本号；冻结时固化，结算时使用固化值 |
| 占用 hold | 冻结从可用余额划入冻结余额 |
| 实扣 charge | 结算后真正离开账户的钱 |
| 退还 refund | 结算后回到可用余额的钱 |

### 2.2 表清单（新建命名空间 `pod_billing_*`）

| 表 | 作用 | 写入方式 |
|---|---|---|
| `pod_billing_freezes` | 冻结主表（意图 + 快照 + 状态） | INSERT + CAS UPDATE |
| `pod_billing_freeze_units` | 每个结算单元一行，含单价快照与实扣/退还 | INSERT 后由结算 UPDATE 一次 |
| `pod_billing_settlements` | 一次结算/收口一行（`freeze_id` 唯一） | 只 INSERT |
| `pod_billing_settlement_units` | 逐单元结算明细 | 只 INSERT |
| `pod_billing_ledger` | 钱包流水（占用/释放/实扣/退还/调整） | 只 INSERT |
| `pod_billing_events` | 冻结状态机事件链（审计） | 只 INSERT |
| `pod_billing_call_records` | 网关调用计量 | 只 INSERT |
| `pod_billing_call_outbox` | 计量写失败缓冲（临时表） | INSERT + 成功即删 |
| `pod_billing_anomalies` | 异常台账（人工处理队列） | INSERT + 人工关单 |
| `pod_billing_recon_daily` | 日终对账结果 | 每日重算覆盖当日行 |
| `billing_wallet_accounts` | 账户余额三桶：可用/冻结/已花 | CAS UPDATE + 流水校验 |
| `billing_price_rules` | 画像 × 规则版本 × 单价快照 | 运营写入，只增不删 |

### 2.3 核心 DDL（SQLite）

```sql
CREATE TABLE billing_wallet_accounts (
  account_id          TEXT PRIMARY KEY,
  available_decipoints INTEGER NOT NULL CHECK (available_decipoints >= 0),
  held_decipoints      INTEGER NOT NULL CHECK (held_decipoints >= 0),
  spent_decipoints     INTEGER NOT NULL CHECK (spent_decipoints >= 0),
  updated_at           TEXT NOT NULL
);

CREATE TABLE pod_billing_freezes (
  freeze_id           TEXT PRIMARY KEY,              -- = idempotency_key，全局唯一
  account_id          TEXT NOT NULL,
  workspace_id        TEXT,
  device_id           TEXT,
  module              TEXT NOT NULL,                 -- pod_custom|pod_semi|pod_replica|pod_fission|pod_zero
  billing_profile     TEXT NOT NULL,
  unit_kind           TEXT NOT NULL CHECK (unit_kind IN ('style','group','panel')),
  link_count          INTEGER NOT NULL CHECK (link_count > 0),
  item_count          INTEGER,                       -- 组类画像必填
  rule_version        TEXT NOT NULL,
  multiplier_percent  INTEGER NOT NULL DEFAULT 100,
  held_decipoints     INTEGER NOT NULL CHECK (held_decipoints >= 0),
  status              TEXT NOT NULL CHECK (status IN ('frozen','settled','released')),
  release_reason      TEXT CHECK (release_reason IN ('normal','no_return_close','ttl')),
  released_decipoints INTEGER,
  retained_decipoints INTEGER,
  request_hash        TEXT NOT NULL,                 -- 同键不同载荷 → 409
  request_json        TEXT NOT NULL,
  rule_snapshot_json  TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  expires_at          TEXT NOT NULL,                 -- 名义到期：客户端提示 / 上游密钥有效期
  settle_deadline     TEXT NOT NULL,                 -- 权威过期点 = expires_at + grace（默认 10min）
  closed_at           TEXT
);

CREATE INDEX ix_freezes_open ON pod_billing_freezes(account_id, status, expires_at);
CREATE INDEX ix_freezes_scan ON pod_billing_freezes(account_id, created_at, freeze_id);

CREATE TABLE pod_billing_freeze_units (
  freeze_id          TEXT NOT NULL REFERENCES pod_billing_freezes(freeze_id),
  unit_idx           INTEGER NOT NULL,
  unit_id            TEXT NOT NULL,
  price_decipoints   INTEGER NOT NULL CHECK (price_decipoints >= 0),
  charge_decipoints  INTEGER NOT NULL DEFAULT 0,
  refund_decipoints  INTEGER NOT NULL DEFAULT 0,
  unit_status        TEXT,                           -- success|no_return|not_started|expired
  PRIMARY KEY (freeze_id, unit_idx)
);

CREATE TABLE pod_billing_settlements (
  settlement_id       TEXT PRIMARY KEY,
  freeze_id           TEXT NOT NULL UNIQUE REFERENCES pod_billing_freezes(freeze_id),
  source              TEXT NOT NULL CHECK (source IN ('settle','close','ttl')),
  charged_decipoints  INTEGER NOT NULL,
  refunded_decipoints INTEGER NOT NULL,
  premium_decipoints  INTEGER NOT NULL DEFAULT 0,
  rule_version        TEXT NOT NULL,
  release_reason      TEXT NOT NULL,
  response_json       TEXT NOT NULL,
  created_at          TEXT NOT NULL
);

CREATE TABLE pod_billing_settlement_units (
  settlement_id       TEXT NOT NULL REFERENCES pod_billing_settlements(settlement_id),
  link_idx            INTEGER NOT NULL,
  unit_id             TEXT NOT NULL,
  unit_status         TEXT NOT NULL,
  charged_decipoints  INTEGER NOT NULL,
  refunded_decipoints INTEGER NOT NULL,
  PRIMARY KEY (settlement_id, link_idx)
);

CREATE TABLE pod_billing_ledger (
  entry_id               TEXT PRIMARY KEY,
  account_id             TEXT NOT NULL,
  freeze_id              TEXT,
  settlement_id          TEXT,
  entry_type             TEXT NOT NULL CHECK (entry_type IN
                          ('hold','hold_release','charge','refund','ttl_release','adjust')),
  delta_available        INTEGER NOT NULL,
  delta_held             INTEGER NOT NULL,
  delta_spent            INTEGER NOT NULL,
  balance_after_json     TEXT NOT NULL,
  idempotency_key        TEXT NOT NULL UNIQUE,        -- 每笔流水的唯一来源
  created_at             TEXT NOT NULL
);
CREATE INDEX ix_ledger_account ON pod_billing_ledger(account_id, created_at);

CREATE TABLE pod_billing_events (
  event_id    TEXT PRIMARY KEY,
  freeze_id   TEXT NOT NULL,
  event_type  TEXT NOT NULL,        -- created|replayed|settled|closed|ttl_released|anomaly
  from_status TEXT,
  to_status   TEXT,
  actor       TEXT NOT NULL,        -- user|system|admin:<id>
  payload_json TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX ix_events_freeze ON pod_billing_events(freeze_id, created_at);

CREATE TABLE pod_billing_call_records (
  call_id      TEXT PRIMARY KEY,       -- 服务端 UUIDv7
  account_id   TEXT NOT NULL,
  freeze_id    TEXT NOT NULL,
  link_idx     INTEGER,
  feature      TEXT NOT NULL,          -- image|title|replica_analysis|...
  attempt_seq  INTEGER NOT NULL DEFAULT 1,
  provider     TEXT,
  result       TEXT NOT NULL,          -- success|failure|no_return
  created_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_calls_unit ON pod_billing_call_records(freeze_id, link_idx, feature, attempt_seq);
CREATE INDEX ix_calls_freeze ON pod_billing_call_records(freeze_id, link_idx, feature);

CREATE TABLE pod_billing_call_outbox (
  outbox_id    TEXT PRIMARY KEY,
  payload_json TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  next_try_at  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE INDEX ix_call_outbox_due ON pod_billing_call_outbox(next_try_at, attempts);

CREATE TABLE pod_billing_anomalies (
  anomaly_id   TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  freeze_id    TEXT,
  account_id   TEXT,
  detail_json  TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('open','resolved','ignored')),
  created_at   TEXT NOT NULL,
  resolved_at  TEXT,
  resolved_by  TEXT
);
CREATE INDEX ix_anomalies_open ON pod_billing_anomalies(status, kind, created_at);
```

### 2.4 不变量（任何时刻都必须成立）

| # | 不变量 | 校验点 |
|---|---|---|
| I1 | `available + held + spent` 每个账户恒定，只随充值/调整变化 | 每笔流水写完后 |
| I2 | 一个 `freeze_id` 最多一条 `settlements` | `UNIQUE(freeze_id)` |
| I3 | `Σ units.price = held` 且 `Σ units.charge + Σ units.refund = held` | 创建时与结算时各校验一次 |
| I4 | `settlements.charged = Σ settlement_units.charged`，`refunded` 同理 | 结算事务内断言 |
| I5 | `status = settled/released` 时 `closed_at` 必非空 | 迁移语句同时写两列 |
| I6 | `ledger` 每笔的 `idempotency_key` 全局唯一 | 唯一索引 |
| I7 | 不存在 `expires_at < now` 且 `status = frozen` 超过 90 秒的记录 | TTL 巡检告警 |

---

## 3. 金额与计价

> **价格结构（新版，一句话）**：每个画像只有一个固定单价 `unit_price_points`，乘一个整数百分比 `multiplier_percent`，冻结时快照成 `link_prices` 数组。**没有区间、没有随机、没有动态定价**；客户端 `expected_points = Σ link_prices`。

### 3.1 单位与精度

| 层 | 表示 | 规则 |
|---|---|---|
| API | JSON number，最多 1 位小数（`45`、`40.5`） | 入参解析为 decipoint 整数（`Decimal(str(x)) * 10`，`ROUND_HALF_UP`）；出参 `decipoints / 10` |
| 内部计算 | `int` decipoint | 禁止 `float`、禁止除不尽的比例运算 |
| 报表 | decipoint 聚合后统一转出 | 合计必须在 decipoint 层面对平 |

倍率用整数百分比（`multiplier_percent = 400` 表示 4 倍），避免浮点。倍率作用于单价，不作用于总额，且**在冻结时固化**。

单价与舍入规则（固定价的前提）：

- 价目表里每个画像只有一个 `unit_price_points`，**没有 min/max，也没有冻结时随机取值**。
- 生效单价 = `ROUND_HALF_UP(unit_price_points × multiplier_percent / 100)`，落到 0.1 积分（decipoint），最多 1 位小数。
- 同一冻结内 `link_prices` 各项**完全相等**；客户端 `expected_points = Σ link_prices`，可以提前算，不需要等回执。

### 3.2 画像与规则版本

画像 = 配置行，不是代码分支：

```json
{
  "billing_profile": "pod_semi_v1",
  "rule_version": "2026-10-01.3",
  "module": "pod_semi",
  "unit_kind": "group",
  "items_per_unit": 4,
  "features": ["image"],
  "charge_rule": "any_image_success",
  "unit_price_points": 32.0,
  "price_source": "billing_price_rules@2026-10-01.3",
  "multiplier_percent": 100,
  "freeze_ttl_seconds": 1800,
  "zero_billed": false
}
```

规则：

1. `rule_version` 单调递增，发布后不可修改；改价 = 新增版本。
2. 冻结时把画像配置整段写入 `rule_snapshot_json`，并把单价写进 `freeze_units.price_decipoints`。**结算永远读快照，不读最新配置**。
3. 画像校验（如半定制的 `item_count == link_count × 4`）写在画像的 `validate()` 里，不写在路由里。这是删除 `/pod-semi/*` 独立入口的前提。
4. 未知画像或画像已下线 → `400 profile_unknown`，不落任何资金。
5. **单价只能来自价目表**：`billing_price_rules` 由运营维护、计费负责人签字后发布。文档与代码中不得出现硬编码示例价（v1.0 曾出现的半定制 `160.0` 是占位值，已删除，不是价目）。冻结快照落库后不可修改，因此每次价格发布必须先按**附录 E** 逐画像核对，核对通过才允许开启写入。
6. **价目表形态是单一价，不是区间**。配置层只有 `unit_price_points` 一个值，没有 `min/max`，冻结时也不会在区间内抽值。理由三条：客户端要能提前算 `expected_points`；日报要能与服务端逐笔对平；审计要能回答「这单为什么是 45」，而不是「这次抽到了 45」。
7. 倍率保留，但只做确定性缩放，不引入随机：`生效单价 = ROUND_HALF_UP(unit_price_points × multiplier_percent / 100)`，精度 0.1 积分；同一冻结内所有 `link_prices` 相同。

### 3.3 画像清单（目标态）

| profile | unit_kind | 固定单价（积分/单元） | 单元数来源 | 判定 | 备注 |
|---|---|---|---|---|---|
| `pod_random_v1` | style | **45**（基准值，原 45~50 随机已取消） | `link_count` | 任一图成功 | 全定制按款；列式由 group 画像承担 |
| `pod_random_grid_v1` | group | **180**（= 45 × 4） | `link_count`，`item_count = link_count × 4` | 主图角色成功 | 列式；不靠模块名推断单位 |
| `pod_semi_v1` | group | **32**（= 8/款 × 4 款） | `link_count`，`item_count = link_count × 4` | 任一图成功 | 入口级校验下沉到画像 |
| `pod_replica_v1_a` | style | **45** | `link_count` | 任一图成功 | 与全定制同价 |
| `pod_replica_v1_b` | style | **180**（= 45 × 4） | `link_count` | 任一图成功 | `multiplier_percent = 400`，客户端不乘 |
| `pod_fission_v1` | style | **45** | `link_count` | 任一图成功；标题不参与计费 | 与 `pod_random_v1` 完全同构 |
| `pod_zero_v1` | style | **0** | `link_count` | 不计费 | `brief/composition/analysis/trial` 等 |

> 45 为定案基准价（全定制 / 复刻 A / 裂变），列式与复刻 B 按 4 款折算 180。改价只改 `billing_price_rules` 一行，不改代码、不改契约。

#### 零计费画像对照

| 动作 | module | billing_profile | scope |
|---|---|---|---|
| `brief` 简报 | `pod_zero` | `pod_zero_v1` | `{"title": true}` |
| `composition` 图文合成 | `pod_zero` | `pod_zero_v1` | `{"composition": true}` |
| 复刻识图 | `pod_replica` | `pod_zero_v1` | `{"replica_analysis": true}` |
| 复刻构图 | `pod_replica` | `pod_zero_v1` | `{"replica_composition": true}` |
| 裂变分析 | `pod_fission` | `pod_zero_v1` | `{"fission_analysis": true}` |
| 结构分析 | `pod_zero` | `pod_zero_v1` | `{"structure_analysis": true}` |
| 试用 | `pod_zero` | `pod_zero_v1` | `{"trial": true}` |

服务端判定零计费的**唯一准则**是 `held_decipoints == 0`，不看画像名。因此即使客户端沿用 `pod_random_v1` + 纯 title scope（`link_prices = [0, 0]`），也会被正确标记为零计费，不需要额外开关；`pod_zero_v1` 只是让零计费动作在报表里可归类。

> 裂变收敛是本方案的硬要求：`pod_fission_v1` 必须是普通画像，代码里不得有 `fission` 专有分支、不得写「可计费款」固化表、不得在结算时反查 `public_url` 或标题完整性。

### 3.4 判定结果 → 金额

单元判定只有四种结果，映射固定：

| unit_status | charge | refund |
|---|---|---|
| `success` | `price` | 0 |
| `no_return` | 0 | `price` |
| `not_started` | 0 | `price` |
| `expired` | 0 | `price` |
| 任意结果 + `charge_policy = attempt` | `price` | 0 |

`charge_policy` 是**画像级**配置，冻结时固化：

- `outcome`（默认）：按上表判定，未成功退该单元。
- `attempt`：该单元只要被发起过（有 `started` 或网关调用记录），无论成败都扣满单价，`refunded = 0`。用于「付费重试、无论成败都计费」的口径。

**取消 `retry_premium_points`**（D13/D14）：重试一律开新冻结，费用来自新冻结的 hold 与 `charge_policy`，不存在「同一冻结内加价」。v2 回执不再返回该字段；v1 转译层固定回 `0.0` 兼容老客户端。这样 `charged + refunded = held` 对所有冻结无条件成立，L3/L4 对账不会破。

---

## 4. 状态机与原子性

### 4.1 状态机

```text
                 POST /freezes（同一事务）
        ┌───────────────────────────────────────┐
        │                                       ▼
   (无记录) ──请求失败/回滚──► (无记录)        frozen ──────────────┐
        │                                       │  │               │
        │                                       │  │               │
        │                          POST /settle │  │ POST /close    │ TTL 到期
        │                                       ▼  ▼               ▼
        │                                   settled  settled     released
        │                                       │  │               │
        └───────────────────────────────────────┴──┴───────────────┘
                                              终态，不可再迁移
```

对外只有三个状态：`frozen` / `settled` / `released`。`close` 与 TTL 在语义上都是「关账」，区别只在 `release_reason`（`no_return_close` / `ttl`）。

### 4.2 迁移必须 CAS

```sql
-- 结算（settle_deadline 之前都合法，含 expires_at 之后的宽限窗口）
UPDATE pod_billing_freezes
   SET status='settled', release_reason=?, released_decipoints=?, retained_decipoints=?, closed_at=?
 WHERE freeze_id=? AND status='frozen' AND settle_deadline >= :now
 RETURNING status;
-- 0 行 = 已被别人结算 / 已过宽限被 TTL 释放 → 走「返回首次结果」或 409 分支

-- TTL 释放（只在宽限期结束后执行）
UPDATE pod_billing_freezes
   SET status='released', release_reason='ttl', released_decipoints=?, retained_decipoints=?, closed_at=?
 WHERE freeze_id=? AND status='frozen' AND settle_deadline < :now
 RETURNING status;
```

**过期定义只有一个**：`now > settle_deadline`。`expires_at` 只表示「冻结额度的名义到期」，用于客户端提示与上游密钥有效期，**不参与结算准入判断**。`settle_deadline = expires_at + grace`：冻结 TTL 固定 30 分钟（本期不按画像放宽，实测最长批次可在此窗口内跑完），`grace` 固定 10 分钟且必须 ≥ 客户端探活宽限（5 分钟）。

禁止的写法：无条件 `UPDATE ... SET status='settled'`；先查后写；在事务外判断状态。所有写路径（用户结算、TTL 任务、人工收口）都走同一段 CAS 逻辑，先到者生效，后到者读到终态。

### 4.3 事务边界

**创建冻结（一个事务）**

```text
BEGIN IMMEDIATE
  1. 校验画像、参数、金额、幂等键冲突
  2. INSERT pod_billing_freezes
  3. INSERT pod_billing_freeze_units (N 行)
  4. UPDATE billing_wallet_accounts  SET available-=H, held+=H  WHERE account_id=? AND available>=H
  5. INSERT pod_billing_ledger (entry_type='hold', idempotency_key='hold:'+freeze_id)
  6. INSERT pod_billing_events (event_type='created')
COMMIT
-- 事务外：拉取上游密钥（失败不影响冻结，只影响 keys 字段）
```

**结算（一个事务）**：CAS 迁移 → INSERT settlements → INSERT settlement_units → 更新 freeze_units → UPDATE 钱包（held 减、available/spent 增）→ INSERT ledger（charge + ttl/refund 两笔或合并一笔）→ INSERT events。

**禁止项**：事务内做 HTTP 请求、读远端配置、写日志到慢设备、sleep 重试。上游密钥下发一律在事务外。

### 4.4 幂等

| 场景 | 服务端行为 |
|---|---|
| 同键重放，`status=frozen`，载荷哈希一致 | 200 返回同一份冻结（附 `Idempotent-Replay: true`），不重复扣钱 |
| 同键重放，`status` 已是终态 | **409** `freeze_not_active`，body 带 `status` / `release_reason` / `closed_at`，客户端改走读接口 |
| 同键不同载荷（哈希不同） | **409** `idempotency_key_reused`，提示换键 |
| 同键属于其他账号 | **409** `idempotency_key_conflict`（不是 500） |
| 结算重放 | 200 返回首次结算结果（同 `settlement_id`），不重复扣退 |

`request_hash` = **语义字段白名单**的规范化 JSON 的 SHA-256，落库比对。

| 参与哈希 | 不参与哈希 |
|---|---|
| `module`、`billing_profile`、`link_count`、`item_count`、`unit_ids`、规范化后的 `scope` | `app_version`、`client_version`、`device_id`、`workspace_id`、时间戳、`idempotency_key` 本身、任何未识别字段 |

规范化顺序：先做 scope 别名归一（`four_grid → image`）与键排序，再对 `unit_ids` 保序序列化，最后整体 `json.dumps(sort_keys=True, separators=(",", ":"))`。目的很直接：客户端升级、换设备、附带遥测字段都不能让一次合法的崩溃恢复被误判成「换载荷」。

---

## 5. 接口契约 v2

统一前缀 `/api/customer/billing/v2`。鉴权沿用远端 token → `account_id`，所有资源操作都带归属校验（非本人一律 404，不泄露存在性）。

### 5.1 通用约定

| 项 | 约定 |
|---|---|
| 金额字段 | JSON number，最多 1 位小数，单位积分 |
| 时间 | ISO-8601 UTC，带 `Z` |
| 幂等键 | 以 `Idempotency-Key` 头为准；body 的 `idempotency_key` 为可选镜像。两者同时存在但不一致 → 400 `idempotency_key_mismatch`；都没有 → 400 `invalid_payload`。**永不 500** |
| 版本号 | `rule_version` 为不透明字符串，只做相等比较，禁止序数解析或大小判断 |
| 分页 | `cursor` 为不透明串（`created_at|freeze_id` 的 base64），`limit` ≤ 100 |
| 错误体 | `{"error":{"code":"...","message":"...","retryable":false,"status":"frozen"}}` |
| 重试语义 | **只有 429 / 5xx / 网络超时**可重试；其余 4xx（含 400 / 404 / 409）一律不可重试 |

### 5.2 创建冻结

`POST /v2/pod/freezes`

```json
{
  "idempotency_key": "a3f1c2d4-...-9b7e",
  "module": "pod_custom",
  "billing_profile": "pod_random_v1",
  "link_count": 6,
  "item_count": null,
  "unit_ids": ["sku_1001", "sku_1002", "...", "sku_1006"],
  "scope": {"image": true, "title": true},
  "workspace_id": "ws_xx",
  "device_id": "dev_xx"
}
```

```json
{
  "freeze_id": "a3f1c2d4-...-9b7e",
  "status": "frozen",
  "billing_profile": "pod_random_v1",
  "unit_kind": "style",
  "link_count": 6,
  "item_count": null,
  "frozen_points": 270.0,
  "link_prices": [45.0, 45.0, 45.0, 45.0, 45.0, 45.0],
  "unit_ids": ["style-1001", "style-1002", "style-1003", "style-1004", "style-1005", "style-1006"],
  "multiplier_percent": 100,
  "rule_version": "2026-10-01.3",
  "expires_at": "2026-10-23T11:20:00Z",
  "settle_deadline": "2026-10-23T11:30:00Z",
  "created_at": "2026-10-23T10:50:00Z",
  "keys": {"openai": ["sk-..."], "doubao": []},
  "keys_error": null
}
```

关键约束：

1. **冻结成功与密钥无关**。`keys` 可以为空、`keys_error` 可以非空，HTTP 仍是 200、`status` 仍是 `frozen`。
2. 任何导致冻结未落库的失败都必须回滚，绝不出现「钱扣了没单号」。
3. 余额不足 → **400** `insufficient_balance`（不可重试、不重放；全链路不再使用 402）。
4. 响应丢包 → 同键重放取回，返回 200。
5. 回执同时给 `expires_at` 与 `settle_deadline`：前者用于界面与密钥有效期提示，后者是**唯一**可用于判断「还能不能结算」的时间。客户端探活宽限应直接读 `settle_deadline`，不要自行 `expires_at + 5min`。
6. 回执**回显 `unit_ids`**，顺序与 `link_prices`、`link_idx` 一一对应；结算时服务端以此快照逐位比对，客户端不需要自行推导。
7. 回执**默认携带 `keys`**（best-effort）：拿到即用；为空或 `keys_error` 非空时走 `POST /keys` 补领。两条路径语义一致，且都不影响冻结成功。
8. 固定价下 `link_prices` 各项相同，客户端可直接 `expected_points = link_count × link_prices[0]`；服务端仍返回完整数组，避免客户端按画像名反推单价。

### 5.3 查询单笔

`GET /v2/pod/freezes/{freeze_id}`（全模块通用，不按画像分支）

```json
{
  "freeze_id": "...",
  "status": "released",
  "billing_profile": "pod_fission_v1",
  "module": "pod_fission",
  "link_count": 4,
  "frozen_points": 180.0,
  "charged_points": 90.0,
  "refunded_points": 90.0,
  "released_points": 90.0,
  "retained_points": 90.0,
  "release_reason": "ttl",
  "rule_version": "2026-10-01.3",
  "expires_at": "2026-10-23T11:20:00Z",
  "settle_deadline": "2026-10-23T11:30:00Z",
  "closed_at": "2026-10-23T11:20:03Z",
  "items": [
    {"link_idx": 0, "unit_id": "sku_1", "status": "success", "charged_points": 45.0, "refunded_points": 0.0}
  ]
}
```

`frozen` 状态返回 `charged_points = 0`、`items[].status = "pending"`；`settled` / `released` 必须返回完整金额（A4/A5）。

`items[].link_idx` 一律**从 0 起**（0..N-1），与冻结回执 `link_prices` 的下标一一对应。v1 转译层负责把老客户端的 1..N 转成 0..N-1，并在返回时加回 1（见附录 D）。

### 5.4 结算

`POST /v2/pod/freezes/{freeze_id}/settle`

```json
{
  "idempotency_key": "settle:a3f1c2d4-...",
  "units": [
    {"link_idx": 0, "unit_id": "sku_1001",
     "features": {"image": "success", "title": "no_return"}}
  ],
  "client_rule_version": "2026-10-01.3"
}
```

```json
{
  "settlement_id": "stl_...",
  "freeze_id": "...",
  "status": "settled",
  "charged_points": 225.0,
  "refunded_points": 45.0,
  "links": [
    {"link_idx": 0, "unit_id": "sku_1001", "status": "success",
     "charged_points": 45.0, "refunded_points": 0.0}
  ],
  "rule_version": "2026-10-01.3",
  "release_reason": "normal",
  "settled_at": "2026-10-23T11:02:41Z"
}
```

校验规则（任一不满足即 400，禁止「尽力而为」）：

1. `units` 数量与 `link_idx` 集合必须与冻结一致，`link_idx` 取值范围固定 0..N-1（`units_mismatch` / `unknown_unit`）。
2. `unit_id` 必须与冻结时快照逐位一致（`unit_mismatch`）。
3. `features` 必须 ⊆ 画像 scope（`scope_violation`）；未上报的功能按 `no_return` 处理。
4. `charge_policy = attempt` 的冻结，单元只要被发起过就全额计费，`refunded = 0`；`outcome` 的冻结按判定表计费。结算入参**不再接受** `paid_retry`，出现即 `400 invalid_payload`（重试请开新冻结）。
5. 逐单元 `charge ≤ price`，且 `Σ link.charged == charged_points`、`Σ link.refunded == refunded_points`（`amount_overflow`）。

### 5.5 主动收口

`POST /v2/pod/freezes/{freeze_id}/close`

```json
{"idempotency_key": "close:...", "reason": "user_cancel"}
```

行为：按「已发生调用计费 + 未上报部分退还」结算，`release_reason = "no_return_close"`，返回与结算同构。已发出密钥不可撤回，close 只关账；**不接受全额退**（否则等于用完密钥再白嫖）。

**阶段限制（D3）**：S2 阶段网关计量尚未上线，close 的判定口径降级为「只按客户端已上报的 unit 状态计费，未上报一律 `no_return`」，并明确接受该阶段「用完密钥再 close」的套利风险；S3 计量上线后，同一接口按 `close_policy=metered` 切到网关口径，无需改客户端。口径切换只动配置，不改接口。

### 5.6 密钥下发

`POST /v2/pod/freezes/{freeze_id}/keys` → `{"providers": ["openai"], "scopes": ["image","title"]}`

- 仅 `frozen` 且未过期可发（否则 409 `freeze_not_active`）。
- key 有效期跟随冻结 `expires_at`；按 scope 只发需要的 provider。
- **不允许「重放冻结续领密钥」**：重放在终态返回 409，密钥只能走本接口。
- 密钥不落日志、不落回执明文；库内加密存储，下发后 60 秒内可重复取同一批 key（缓存），不做无限次重发。

### 5.7 未结清冻结列表

`GET /v2/pod/freezes?status=open&module=&billing_profile=&limit=50&cursor=`

```json
{
  "items": [{"freeze_id":"...","billing_profile":"pod_random_v1","module":"pod_custom",
             "workspace_id":"ws_xx","device_id":"dev_xx",
             "frozen_points":270.0,"expires_at":"...","settle_deadline":"...","created_at":"..."}],
  "next_cursor": "eyJjcmVhdGVkX2F0Ijoi..."
}
```

`status=open` 等价于 `status=frozen`（含已过 `expires_at` 但仍在 `settle_deadline` 内的正常冻结，以及巡检窗口内的过期行）。这是「本地没记录、服务端有冻结」的唯一发现途径（A6）。列表按账号返回，并返回 `device_id` / `workspace_id`：客户端据此区分「本机待续跑」与「其他设备待认领」，不必靠 `freeze_id` 猜。服务端不做设备归属判断。

### 5.8 运维与审计接口（内部专用，需管理员鉴权）

| 接口 | 用途 |
|---|---|
| `GET /v2/pod/freezes/{id}/events` | 完整事件链 |
| `GET /v2/pod/freezes/{id}/ledger` | 该冻结的全部资金流水 |
| `GET /v2/pod/anomalies?status=open` | 异常队列 |
| `POST /v2/pod/freezes/{id}/admin-close` | 人工收口（需双人复核 token） |
| `POST /v2/pod/adjustments` | 人工调账（写 `adjust` 流水，必须带工单号） |

### 5.9 日报接口

`GET /v2/pod/reports/daily?date=2026-10-22&tz=Asia/Shanghai`

```json
{
  "date": "2026-10-22",
  "timezone": "Asia/Shanghai",
  "unit": "point",
  "freeze":        {"count": 812, "points": 36540.0},
  "settle":        {"count": 764, "charged_points": 33810.0, "refunded_points": 2610.0},
  "close":         {"count": 12,  "charged_points": 540.0,  "refunded_points": 0.0},
  "ttl_release":   {"count": 9,   "released_points": 405.0, "retained_points": 45.0},
  "zero_billed":   {"count": 130, "points": 0.0},
  "anomaly":       {"amount_mismatch": 0, "over_claim": 3, "under_claim": 1, "orphan": 0},
  "open_freezes":  {"count": 21,  "points": 945.0},
  "recon":         {"status": "ok", "diff_points": 0.0, "generated_at": "2026-10-23T02:10:04Z"}
}
```

| 字段 | 口径 |
|---|---|
| `freeze` | `created_at` 落在当日、`held > 0` 的冻结（零计费不计入） |
| `settle` | `source='settle'` 的结算，按 `closed_at` 归属；`charged + refunded = held` |
| `close` | `source='close'`，独立列示，不与正常结算混算 |
| `ttl_release` | `source='ttl'`，金额取服务端实值 |
| `zero_billed` | `held = 0` 的冻结，单独口径 |
| `anomaly` | 当日新开的异常台账计数（按 kind） |
| `open_freezes` | 截至日终仍 `frozen` 的冻结；明细走 `GET /freezes?status=open`，客户端自行区分本机/他机待认领 |
| `recon` | 当日 L1–L4 校验结果，`diff_points != 0` 时报表标记不可信 |

配套接口：

- `GET /v2/pod/reports/daily/detail?date=&kind=settle|close|ttl|zero|anomaly&cursor=`：逐笔下钻，行内带 `freeze_id` / `settlement_id` / 金额 / `release_reason`，用于和客户端日报逐笔核对。
- `POST /v2/pod/reports/daily/rebuild?date=`：管理员幂等重算（覆盖当日 `pod_billing_recon_daily` 行），用于补偿或口径修正。

报表只读账本与结算表，不触发任何资金动作；`combo_kit` / `product_processing` / 充值 / 活动 / 人工调整不在本报表内，需要时单独接口，避免口径混用。

---

## 6. 结算算法

### 6.1 主流程

```python
def settle(freeze_id, payload, actor):
    with tx():                                    # BEGIN IMMEDIATE
        fz = load_freeze_for_update(freeze_id, payload.account_id)   # 无 → 404
        if fz.status != 'frozen':
            if fz.status == 'settled':  return replay_result(fz)      # 幂等重放
            raise Conflict('freeze_not_active')                       # released
        units = validate_units(payload, fz)        # 条数/idx/unit_id/scope
        priced = [judge(u, fz.rule_snapshot, call_records(fz)) for u in units]
        charged  = sum(p.charge for p in priced)
        refunded = fz.held_decipoints - charged
        assert charged + refunded == fz.held_decipoints
        cas_settle(freeze_id, charged, refunded)   # 0 行 → 冲突，回滚
        insert_settlement(..., links=build_links(priced))
        update_freeze_units(priced)
        move_wallet(fz.account_id, held=-fz.held, available=+refunded, spent=+charged)
        insert_ledger(...)
        insert_event(...)
    return build_response()
```

### 6.2 判定来源优先级

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 网关 `call_records` | 计量上线后用于封顶；上报成功但网关无记录 → 不扣（记异常） |
| 2 | 客户端上报 | 计量未覆盖的功能（如标题）暂时只以此为准 |
| 3 | 默认 | 未上报 → `no_return`，退该单元 |

封顶规则：`charge = min(price, price × 已计量成功次数>0)`，即「单元内至少有一次网关成功记录才可扣该单元」。异常入 `pod_billing_anomalies(kind='over_claim')`，**不阻断结算**，但会计入日报的异常笔数。

### 6.3 结算必须返回逐单元明细

`Σ links[].charged_points == charged_points`，且落库时逐单元金额直接来自本次计算，不做二次聚合。要求客户端「金额只取回执」，服务端就要给到单元级，否则客户端只能推算（A3）。

### 6.4 网关计量（S3 交付物，不是"以后再说"）

目标：让「调用到底发生没有」有服务端证据，并把结算金额封顶在真实调用上。

#### 6.4.1 覆盖范围

所有 POD 上游调用必须经统一 AI 网关，并在请求 metadata 中透传 `freeze_id` / `link_idx` / `feature`：

| feature | 来源 | 是否计费关联 |
|---|---|---|
| `image` | 出图（全定制/半定制/复刻/裂变/列式） | 是 |
| `title` | 标题生成、标题重生、补标题 | 否（不单独计价，但要计量，用于判定"是否发生"与免费额度滥用） |
| `replica_analysis` | 复刻识图 | 否（零计费，仅观测） |
| `replica_composition` | 复刻构图 | 否 |
| `fission_analysis` | 裂变分析 | 否 |
| `structure_analysis` | 结构分析 | 否 |

上线前逐条核对网关入口清单：出图 provider、标题模型、识图、构图。**不经网关的调用不计量**，清单外的调用一律视为缺口，先补齐再切 `enforce`。

#### 6.4.2 写入路径

```text
上游响应返回（成功/失败）→ 构建 call_record → 同请求内写 pod_billing_call_records
                                        └─ 写失败 → 写 pod_billing_call_outbox（next_try_at=+5s）
后台 job（每 5s）：flush outbox → 成功删除；3 次仍失败 → 记 anomaly(kind='meter_write_failed')
结算/close/TTL 读计量前：先 flush 该 freeze_id 的 outbox，保证"刚跑完就结算"不漏计
```

- 每次上游调用**一行**：`call_id`（服务端 UUIDv7，客户端不可指定）、`freeze_id`、`link_idx`、`feature`、`provider`、`result`。
- 唯一约束 `UNIQUE(freeze_id, link_idx, feature, attempt_seq)`，防网关重试造成重复计数；`attempt_seq` 由调用方在冻结内自增，缺省 1。
- 写计量失败**不能**影响上游响应；计量是旁路，不是同步依赖。
- 结算事务内只读 `call_records`，不写；计量写入永远在结算事务之外，避免把网络/磁盘抖动带进资金事务。

#### 6.4.3 与结算、close、TTL 的衔接

| 场景 | 计量用法 |
|---|---|
| 正常结算 | 单元封顶：该单元至少一条 `result='success'` 的 `image` 记录才可扣；否则即使客户端报 `success` 也按 `no_return` |
| `close` | `close_policy=metered` 时按「有记录的单元计费、无记录全退」；`reported` 时按客户端上报 |
| TTL 释放 | `ttl_policy=metered` 时，`retained = 有记录的单元价之和`，`released = held - retained` |
| 差异 | 上报成功但无记录 → `anomaly(kind='over_claim')`，不扣；有记录但报失败 → `anomaly(kind='under_claim')`，按记录扣 |

#### 6.4.4 灰度开关

`meter_policy = off | shadow | enforce`：

| 值 | 行为 | 用途 |
|---|---|---|
| `off` | 完全不看计量 | S0–S2 默认 |
| `shadow` | 计量与上报都算，差异只记 anomaly，不影响金额 | 上线后至少跑 2 周 |
| `enforce` | 以上限为准 | 差异收敛后切换 |

`ttl_policy` / `close_policy` 与 `meter_policy` 解耦：前者决定"没结算时怎么收口"，后者决定"金额要不要被计量限制"。三者切换都只改配置，不改接口。

#### 6.4.5 数据保留与容量

`call_records` 只在冻结生命周期内参与结算，热表保留 90 天；之后按日归档到 `pod_billing_call_records_archive`（同构表 + 日期后缀），归档不影响对账报表（报表按日汇总落 `recon_daily`）。以每批次 8 款、每款 7 次调用估算，单账号日增约百行量级，SQLite 单文件可承载；超过 90 天阈值前必须先评估归档节奏。

---

## 7. TTL 与收口

### 7.1 定时任务

| 任务 | 周期 | 行为 |
|---|---|---|
| `ttl_sweeper` | 60s | 取 `status=frozen AND settle_deadline <= now - 30s`，限量 200 笔，逐笔独立事务：先读网关记录 → 计算 retained/released → CAS 迁移 → 写 settlement（`source='ttl'`）→ 钱包与流水。单笔失败不阻塞整批 |
| `open_alert` | 5min | 存在 `settle_deadline < now` 仍 `frozen` 的记录 → 告警（I7） |
| `recon_daily` | 每日 02:10 | 见第 8 节 |

`settle_deadline - 30s` 只是扫描偏移，避免同一秒内与客户端结算请求抢同一笔；服务端以 `settle_deadline` 为唯一事实，宽限期是冻结时固定的 `grace`，不是运行时兜底。

### 7.2 计费口径

- 目标口径：**按已发生调用计费，无调用全退**。
- 计量上线前：以客户端最后一次上报为准；完全没有上报 → 视为无调用，全退。这条规则由配置 `ttl_policy` 控制（`reported` / `metered`），切换时点见第 14 节。
- 释放比例不再由「按比例释放」实现，避免出现无法解释的固定损失（C12）。

### 7.3 与客户端的关系

客户端在 `settle_deadline` 之前续跑并结算。服务端保证：

1. **`expires_at` 之后、`settle_deadline` 之前的结算完全合法**，按正常结算计价并返回 `status=settled`，不返回 409。409 只出现在 `settle_deadline` 之后（此时已被 TTL 释放）。
2. TTL 释放的金额立即可读，`GET` 返回 `released_points` / `retained_points` / `release_reason`。
3. 释放是原子迁移，不存在「`released` 但 `closed_at` 为空」或「已过期仍是 `frozen` 且永不被扫」的稳定状态；巡检窗口最长 60 秒，窗口内结算依然成功。
4. 客户端探活宽限（5 分钟）必须落在服务端 `grace`（10 分钟）之内，且客户端应直接使用回执的 `settle_deadline`，不再自行加 5 分钟。两边对「过期」只有一个定义：`now > settle_deadline`。

---

## 8. 服务端对账

### 8.1 四层校验（日终）

| 层 | 校验 | 差异处理 |
|---|---|---|
| L1 单元 | `Σ price == held`；`Σ charge + Σ refund == held` | 立即异常，冻结进入 `anomalies` |
| L2 冻结 | 一笔冻结最多一条 settlement；终态必有 `closed_at` | 报数据损坏，停止当日报表 |
| L3 账户 | `available + held + spent` 相对期初只随充值/调整变化；`Σ ledger == 余额变化` | 逐账户下钻到 `freeze_id` |
| L4 外部 | POD 实扣之和 ≤ 钱包当日净减少；差额必须等于已知非本账流水（其他模块/充值/活动/调整） | 无解释差额 → 按 `freeze_id` 逐笔下钻并告警 |

### 8.2 差异分类与处置

| kind | 含义 | 处置 |
|---|---|---|
| `over_claim` | 客户端报成功，网关无记录 | 不扣费；连续出现则限制该设备 |
| `under_claim` | 网关有成功，客户端报失败 | 按网关计费；通知客户端升级 |
| `unit_mismatch` | 明细与冻结快照不符 | 拒绝结算（400），要求重发正确载荷 |
| `amount_mismatch` | 聚合 ≠ 明细之和 | 契约违约，异常告警，**不修正任何一边** |
| `orphan_freeze` | 有冻结、无结算、已过期未释放 | 巡检告警，人工介入 |

### 8.3 日报口径与生成

- 归属：冻结按 `created_at`，结算 / 收口 / 释放按 `closed_at`；日切时区默认 `Asia/Shanghai`，接口可传 `tz`。
- 范围：只统计 POD（`module LIKE 'pod_%'`），零计费单独列；`combo_kit` / `product_processing` / 充值 / 活动 / 人工调整不在本账。
- 数据源：`pod_billing_freezes`（笔数、held）、`pod_billing_settlements`（charged / refunded）、`pod_billing_ledger`（资金侧交叉验证）、`pod_billing_anomalies`（异常计数）。
- 三方校验：日报每个金额必须同时等于「结算表汇总」与「账本流水汇总」，任一侧不等即把当日标记 `recon.status=diff`，并停止对外发布。
- 生成：每日 02:10 落 `pod_billing_recon_daily`；支持管理员按日幂等重算（`POST /reports/daily/rebuild`）。查询优先读该表，缺行时实时计算并写回。
- 计量差异：`meter_policy != off` 时额外输出 `over_claim` / `under_claim` 的计数与金额，供运营判断是否收紧上报。

---

## 9. 安全与限流

| 面 | 措施 |
|---|---|
| 身份 | 远端 token → `account_id`；所有 freeze 操作校验归属，越权统一 404 |
| 幂等键 | 全局唯一；跨账号撞键返回 409，不返回 500，也不泄露他人 `freeze_id` 内容 |
| 金额 | 服务端唯一计算权威；客户端上报金额字段一律忽略 |
| 密钥 | 加密存储、按 scope 下发、不落日志与回执明文、有效期绑定冻结 |
| 限流 | 创建冻结按账号 + 设备双维度；结算按 `freeze_id` 串行；鉴权失败单独计数 |
| 审计 | 事件链不可改；人工调账需工单号 + 双人复核 |
| 防刷 | 零计费动作单独配额（避免免费额度被刷）；`charge_policy=attempt` 的冻结按账号限频 |

---

## 10. 可观测性

| 指标 | 用途 |
|---|---|
| `freeze_created_total{profile}` / `freeze_failed_total{code}` | 入口健康度 |
| `freeze_replay_total` | 客户端重放频率，异常升高说明回包链路有问题 |
| `settle_latency_p99` / `settle_failed_total{code}` | 结算质量 |
| `open_freezes{age}` / `expired_not_closed_total` | TTL 是否卡住（I7） |
| `ttl_released_points_total` | 白损规模 |
| `anomaly_open_total{kind}` | 人工队列水位 |
| `wallet_hold_ratio` | 冻结占余额比例，异常高说明客户端不收口 |

日志一律带 `freeze_id`，禁止打印密钥与完整 token。

---

## 11. 迁移与灰度

### 11.1 数据

- 新表全新建，不动旧计费表结构。
- 历史冻结：只迁 `frozen` 的未结冻结（保留原金额与到期时间，补单价快照与 `rule_version`），历史终态数据保持只读归档，不反推、不重算。
- 迁移脚本幂等，可按 `freeze_id` 重跑。

### 11.2 接口

- v1（`/batch/*`、`/pod-semi/*`、`/pod-fission/*`）保留为**转译层**：解析成 v2 请求 → 调用内核 → 按 v1 形状返回。转译层不写任何业务判断。
- 灰度期同时写影子记录（不落钱），逐笔比对 v1/v2 的金额与状态，差异为 0 后再切流。

### 11.3 顺序

1. 建表 + 内核 + 契约测试（影子只读）→ 不影响线上。
2. 新客户端接入 v2，小流量（内部账号 + 白名单设备）。
3. 计价一致性核对通过后逐步放量。
4. 全量后冻结 v1 写入，只留读。
5. 观察一个完整结算周期后下线 v1。

### 11.4 回滚

- 每阶段有独立开关：`billing_kernel=v2|v1`、`shadow_write=on|off`、`ttl_policy=reported|metered`。
- 回滚 v1 时，v2 期间产生的冻结仍由 v2 内核负责收口（内核不随开关下线），避免出现无人认领的冻结。
- 数据库迁移单向，不做逆向降级；回滚只回代码路径。

---

## 12. 实施阶段与验收

| 阶段 | 内容 | 验收断言（必须自动化） |
|---|---|---|
| **S0 内核** | 表结构、创建冻结、钱包三桶、流水、事件、契约测试骨架；**价目表只存单一价**（无区间、无随机） | ① 1000 并发同键创建 → 1 条冻结、1 笔 hold、余额只扣一次；② 同键不同载荷 → 409；③ 部分失败回滚后无冻结、无流水；④ `kill -9` 后重启，冻结与余额一致；⑤ **单进程单 worker 压测结论**：并发创建+结算 p99、写队列深度、事务最长占用达标（见 1.4）；⑥ 同一画像连续冻结 1 万次，`link_prices` 全部相同且 = 价目表值 × 倍率 |
| **S1 原子与语义** | CAS 结算、重放 409、错误码体系、TTL 原子迁移 | ① 终态重放 409 且带 `status`；② 并发两路结算只产生 1 条 settlement；③ TTL 到点迁移后无 `frozen` 残留、`closed_at` 非空；④ 已释放结算固定 409；⑤ 宽限边界：`settle_deadline - 1s` 结算成功、`+1s` 返回 409 |
| **S2 契约完整** | 逐单元明细、释放三项金额、open 列表、keys、close（reported 口径）、**日报接口与下钻** | ① `Σ links == 聚合`（随机 1e4 组）；② `released` 必带三项金额且求和守恒；③ 游标翻页无重无漏；④ keys 接口对终态返回 409；⑤ close 只按已上报判定，未上报 = `no_return`，且 close 后不可再结算；⑥ 日报金额 = 结算表汇总 = 账本流水汇总（三方一致） |
| **S3 计量与收口口径** | 网关中间件 + outbox、结算封顶、`meter_policy` 灰度、日终四层校验、`reported → metered` 切换 | ① 无网关记录的「成功」不扣费并落 `over_claim`；② outbox 积压 5s 内自动补写，结算前强制 flush；③ `shadow` 连续 14 天差异率 < 0.1% 才允许 `enforce`；④ 切换前后同一批冻结的金额可解释；⑤ 任意一天可按 `freeze_id` 下钻到流水 |
| **S4 收尾** | v1 转译下线、灰度完成、运营台 | ① v1 流量清零后接口返回 410；② 影子比对连续 7 天 0 差异 |

阶段依赖：S1 依赖 S0 的幂等闸门；S2 依赖 S1 的 CAS；S3 依赖网关改造排期；S4 依赖 S2/S3 全绿。

---

## 13. 与客户端 v0.8 契约的逐条落点

| 客户端要求 | 本方案落点 | 阶段 |
|---|---|---|
| A1 冻结原子性 | 第 4.3 节单事务；失败即回滚；响应丢包靠同键重放 | S0 |
| A2 重放语义收敛 | 第 4.4 / 5.2 节：终态重放 409 + `status` | S1 |
| A3 结算逐 link 明细 | 第 5.4 / 6.3 节 `links[]` 且求和校验 | S2 |
| A4 释放精确金额 | 第 5.3 / 7.1 节 `released/retained/release_reason` | S2 |
| A5 状态语义固定 | 第 4 节三值 + CAS + 404/409/400 语义表 | S1 |
| A6 未结清列表 | 第 5.7 节 `GET /freezes?status=open` | S2 |
| A7 裂变收敛 | 第 3.3 节降为普通画像，无专有分支 | S0 |
| B8 密钥与冻结解耦 | 第 5.6 节独立 keys 接口，冻结成功不依赖密钥 | S2 |
| B9 主动收口 | 第 5.5 节 close，按已发生调用计费 | S2 |
| B10 错误码不漂移 | 附录 A 错误码表 + 契约测试 | S1 |
| C11 网关计量 | 第 6.4 节 `call_records` + outbox + 封顶 + 灰度 | S3 |
| C12 释放口径 | 第 7.2 节「按实调用、无调用全退」 | S3 |
| C13 账单口径固定 | 第 8.3 / 5.9 节日终表与日报接口 | S2（日报）/ S3（计量差异列） |

---

## 14. 口径状态（v1.1）

### 14.1 已定，不再讨论

| # | 原问题 | 结论 |
|---|---|---|
| 1 | `paid_retry` 语义 | 取消「同冻结加价」。每次重试开**新冻结**；需要「无论成败都扣」时用画像级 `charge_policy=attempt`。`retry_premium_points` 字段废弃（D13/D14） |
| 2 | 列式生成算几个单元 | 1 个 `group` 单元，`item_count = link_count × 4` |
| 3 | 半定制是否合并到统一入口 | 合并；`item_count` 校验进画像，`/pod-semi/*` 转译后下线 |
| 4 | `ttl_policy` 切换时点 | 计量覆盖稳定 2 周后，在 S3 内从 `reported` 切 `metered`，只改配置 |
| 5 | 冻结 TTL | 固定 30 分钟，`settle_deadline = expires_at + 10min`；实测最长批次可在此窗口内跑完，不分画像 |
| 6 | 零计费动作 | 统一 `pod_zero_v1`；判定准则 `held == 0`，不依赖画像名 |
| 7 | 半定制单价 | 32 积分/组（= 8/款，4 款一组），业务已确认 |
| 8 | 复刻 B 倍率 | 400%，单价 = `pod_replica_v1_a` × 4 |
| 9 | 价目表形态 | **单一价，不支持区间与随机**；四模块固定价，生效价 = 单价 × 整数百分比倍率 |

### 14.2 仍待输入（不阻塞 S0/S1）

| # | 问题 | 需要谁给 | 影响 |
|---|---|---|---|
| 1 | 零计费动作的日配额 | 运营 | 防刷阈值 |
| 2 | 列式在日报「单元数」里是否再拆成 4 款 | 运营（口径问题，不影响资金） | 报表展示 |

---

## 15. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| SQLite 单写者成为瓶颈 | 高峰期结算排队 | 结算事务极小化；写入串行队列；预留 Postgres 迁移路径 |
| 客户端大面积重放 | 冻结/结算压力 | 重放不重建、不加锁等待；指标单独观测 |
| 计量与上报不一致 | 金额争议 | 封顶策略 + 异常台账 + 日报逐笔下钻 |
| 灰度期双写不一致 | 账目分叉 | 影子只读比对，差异 0 才切流 |
| 历史冻结无快照 | 无法按新规则结算 | 迁移时按当前规则补快照并标记 `migrated=true`，单独口径统计 |

---

## 附录 A：错误码表

| HTTP | code | 可重试 | 含义 |
|---|---|---|---|
| 400 | `invalid_payload` | 否 | 字段缺失/类型错误 |
| 400 | `profile_unknown` | 否 | 画像不存在或已下线 |
| 400 | `item_count_mismatch` | 否 | 组类画像 `item_count != link_count × 4` |
| 400 | `units_mismatch` | 否 | 结算单元数量/索引与冻结不符 |
| 400 | `unknown_unit` | 否 | `link_idx` 越界 |
| 400 | `unit_mismatch` | 否 | `unit_id` 与快照不符 |
| 400 | `scope_violation` | 否 | 上报了画像 scope 外的功能 |
| 400 | `idempotency_key_mismatch` | 否 | `Idempotency-Key` 头与 body 不一致 |
| 400 | `insufficient_balance` | 否 | 可用余额不足（冻结与收口同码） |
| 400 | `amount_overflow` | 否 | 单元扣费超过单价 |
| 404 | `freeze_not_found` | 否 | 不存在或不属于当前账号 |
| 409 | `freeze_not_active` | 否 | 已结算/已释放，body 带 `status` |
| 409 | `idempotency_key_reused` | 否 | 同键不同载荷 |
| 409 | `idempotency_key_conflict` | 否 | 同键被其他账号占用 |
| 429 | `rate_limited` | 是（退避） | 限流 |
| 503 | `billing_unavailable` | 是 | 存储/依赖不可用，事务已回滚 |
| 500 | `internal_error` | 是 | 未预期异常，事务已回滚 |

补充约定：

- **不使用 402**；余额不足在冻结与收口两条路径上都是 400 `insufficient_balance`。
- `retryable` 只在 429 / 5xx / 网络超时为 `true`；所有 4xx（含 400 / 404 / 409）为 `false`，客户端可直接按该字段决定是否重试。
- 幂等键相关错误（`idempotency_key_mismatch` / `reused` / `conflict`）永远不返回 5xx。

## 附录 B：契约测试清单（服务端必须提供）

1. 幂等：同键 1000 并发、跨账号同键、同键换载荷、终态重放。
2. 原子：在每个事务语句后注入异常，验证无「钱动账没落」。
3. 状态：frozen/settled/released 的全组合矩阵，含 TTL 与并发结算的竞态。
4. 金额：随机画像 × 随机单元结果，断言 `charge + refund == held` 且 `Σ links == 聚合`。
5. 画像：半定制 `item_count` 校验、裂变无专有分支（断言无 `fission` 特例代码路径）。
6. 释放：TTL 三种计量来源（无上报/部分上报/全上报）下的 retained/released。
7. 列表：游标稳定性（并发插入时无重无漏）。
8. 密钥：终态不可领、scope 过滤、日志脱敏。
9. 对账：构造 L1–L4 各类差异，验证分类正确且不修正数据。
10. 哈希稳定：同一语义请求在 `app_version` / `device_id` / 遥测字段变化后重放，仍返回 200 且不判 `idempotency_key_reused`。
11. 宽限窗口：`expires_at` 后 1 分钟结算成功；`settle_deadline` 前 1 秒成功、后 1 秒 409；TTL 不早于 `settle_deadline` 迁移。
12. 索引起点：v2 的 `link_idx` 恒为 0..N-1；v1 转译层入参 1..N 时映射正确，返回仍为 1..N。
13. 转译一致性：v1 与 v2 对同一批次合成的 `unit_ids` 完全相同（同函数、同输入）。
14. 重试模型：`charge_policy=attempt` 的冻结在单元失败时仍全额扣、`refunded=0`；结算入参出现 `paid_retry` 返回 400 `invalid_payload`。

## 附录 C：运维要点

- 备份：SQLite 在线备份 `VACUUM INTO` 每日一次，保留 14 天；`pod_billing_*` 单独校验和。
- 归档：`pod_billing_call_records`、`pod_billing_events` 按月分区表（SQLite 用归档表 + 删除策略），账本与结算表永久保留。
- 索引：以 `(account_id, status, expires_at)` 与 `(account_id, created_at, freeze_id)` 为主，避免全表扫。
- 巡检：I1–I7 每 10 分钟抽检，异常直接进告警，不自动修数。

## 附录 D：v1 → v2 转译层规范

转译层只做**形状转换与常量映射**，不做业务判断；任何金额、判定、状态都来自内核。

### D.1 入口映射

| v1 入口 | v2 内核调用 | 备注 |
|---|---|---|
| `POST /batch/freeze` | `POST /v2/pod/freezes` | 含裂变（`billing_profile` 照传） |
| `POST /pod-semi/freeze` | 同上 | `module=pod_semi`，`item_count` 由转译层按 `link_count × 4` 补齐 |
| `POST /pod-fission/freeze` | 同上 | 退役为别名，行为与 `/batch` 完全一致 |
| `POST /batch/settle`、`/pod-semi/settle`、`/pod-fission/settle` | `POST /v2/pod/freezes/{id}/settle` | |
| `GET /batch/{id}`、`/pod-semi/{id}` | `GET /v2/pod/freezes/{id}` | |
| 无 | `POST /v2/pod/freezes/{id}/keys`、`/close` | v1 无对应能力，只有新客户端可用 |

### D.2 `unit_ids` 合成（老客户端不发时）

必须是**纯函数**，冻结与结算调用同一个实现，禁止引入时间戳、随机数或自增序列：

```python
def synth_unit_ids(unit_kind: str, link_count: int, unit_hints: list[str] | None) -> list[str]:
    out = []
    for i in range(link_count):
        hint = (unit_hints or [None] * link_count)[i]
        if hint:
            digest = hashlib.sha1(hint.encode("utf-8")).hexdigest()[:12]
            out.append(f"style-{digest}" if unit_kind != "group" else f"group-{digest}")
        else:
            out.append(f"style-{i}" if unit_kind != "group" else f"group-{i}")
    return out
```

- 按款：`style-{i}`；按组：`group-{g}`，`g` 从 0 起。
- 老客户端若带 SKU/款号，用 `style-{sha1(sku)[:12]}`，保证跨设备可复现。
- 结算时转译层用同一函数重建并与快照逐位比对，不一致即 `400 unit_mismatch`（老客户端因此不会因为 ID 规则漂移而 400）。

### D.3 其余字段映射

| 维度 | v1 | v2 | 转换方向 |
|---|---|---|---|
| `link_idx` | 1..N | 0..N-1 | 入参加 `-1`，出参加 `+1` |
| `scope` | `["four_grid","title"]` | `{"image": true, "title": true}` | 数组 → 对象 |
| feature 名 | `four_grid` | `image` | 别名表；`title` 不变 |
| feature 名 | `image_gen` / `image` | `image` | 归一 |
| `rule_version` | 整数 | 字符串 | v2 出字符串；v1 出旧整数（服务端维护映射表） |
| `retry_premium_points` | 有 | 无 | v1 固定回 `0.0` |
| 错误体 | `{"detail": ...}` | `{"error": {...}}` | v1 平铺 `detail`，并在 header 带 `X-Billing-Code` |
| `paid_retry` | 请求字段 | 不接受 | v1 转译时丢弃并忽略（不因它报错） |

### D.4 拆分与下线

- 转译层必须与被转译的 v1 语义**同时存在**，不允许「v2 先改语义、v1 慢慢跟上」。
- v1 下线判据：连续 7 天无 v1 写入流量 + 影子比对 0 差异；之后返回 410 并保留读接口一个结算周期。

## 附录 E：正式价目表（单一价，无区间列）

> 本表只有「单价」一列，**没有 min/max、没有区间、没有随机**；新版设计不定义动态定价。

| profile | unit_kind | 单价（积分/单元，固定） | 倍率 | 来源版本 | 签字 |
|---|---|---|---|---|---|
| `pod_random_v1` | style | **45**（基准价，已定） | 100% | 2026-10-23 | 已确认 |
| `pod_random_grid_v1` | group | **180**（= 45 × 4） | 100% | 2026-10-23 | 已确认 |
| `pod_semi_v1` | group | **32**（= 8/款 × 4 款） | 100% | 2026-10-23 | 已确认 |
| `pod_replica_v1_a` | style | **45**（与全定制同价） | 100% | 2026-10-23 | 已确认 |
| `pod_replica_v1_b` | style | **180**（= 45 × 4） | **400%** | 2026-10-23 | 已确认 |
| `pod_fission_v1` | style | **45**（与全定制同价） | 100% | 2026-10-23 | 已确认 |
| `pod_zero_v1` | style | **0** | — | 2026-10-23 | 已确认 |

红线：本表全部已确认，`billing_price_rules` 仍按版本化发布（一次发布一个 `rule_version`），S2 验收通过后才允许打开生产写入开关。冻结快照不可修改，错一次就是永久错账。

> 若运营决定按 50 收而不是 45：只改上表与配置，不改代码、不改接口、不改客户端算法；`expected_points` 仍由回执 `link_prices` 推导，不会因为改价而对不上。

## 变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-10-23 | v1.0 | 首版：单一内核、新表、CAS、接口契约、对账、分阶段 |
| 2026-10-23 | v1.1 | 回应客户端核对清单 17 条：引入 `settle_deadline` 与宽限窗口、删除占位价、close 阶段口径、哈希白名单、`link_idx` 0 起、`unit_ids` 合成规范、scope/feature 映射、`rule_version` 字符串、幂等键头优先、余额不足统一 400、可重试面收敛、零计费对照表、取消 `retry_premium`、重试开新冻结、open 列表补设备字段、单写者进 S0 验收；新增附录 D 转译规范、附录 E 价目表 |
| 2026-10-23 | v1.2 | 补齐此前只写「差什么」的两块：新增 6.4 网关计量落地设计（覆盖清单、写入路径与 outbox、与结算/close/TTL 衔接、`meter_policy` 灰度、归档），新增 5.9 日报接口完整契约与 8.3 生成口径；日报提前到 S2 交付。半定制 32/组、复刻 B 400%；TTL 固定 30 分钟；冻结回执回显 `unit_ids` 并默认携带 `keys` |
| 2026-10-23 | v1.3 | 明确价格结构：**单一固定价 × 整数百分比倍率**，无区间、无随机、无动态定价；四模块固定价写入 3.3 与附录 E；`expected_points = Σ link_prices` 与日报口径随之收敛 |
| 2026-10-23 | v1.4 | 价格全部定案：全定制 45/款、复刻 A 45/款、裂变 45/款、半定制 32/组、列式 180/组、复刻 B 180/款；价格不再是待输入项，S2 只等验收 |

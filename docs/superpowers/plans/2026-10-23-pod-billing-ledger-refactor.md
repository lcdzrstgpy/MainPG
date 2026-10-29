# POD 计费账本重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 POD 四模块的计费从「冻结 → 干活 → 上报 → 结算」的裸调用，改造成一本可对平的账：先写意图再冻结、重放可接管、按 `settle_deadline` 收口、金额只取服务端回执、账本永不随批次删除，并支持与服务端日报逐笔对平。

**Architecture:** 客户端保留现有 `PodCallPlan` / `PodBillingRun` / repository 三层，在适配层对接服务端 v2 契约（`/v2/pod/freezes` 一套入口）。新增 `billing_settlements` / `billing_settlement_items` / `billing_recon_runs` 三张表，改造 `billing_runs` 承载意图账与冻结快照。所有服务端差异（字段形状、索引起点、画像名）收敛到一个适配模块；v1 仅在服务端转译层在线期作为兼容路径存在。

**Tech Stack:** Python 3.10+、FastAPI、SQLite（WAL）、pytest、现有 POD repository/worker/service。

**设计基线：** [POD 四模块计费账本与对账设计 v0.11](../specs/2026-10-23-pod-billing-ledger-reconciliation-design.md)

---

## Global Constraints

- 本工作区只改客户端；不登录、不修改服务端实现。
- 不改计价算法与价格；单价一律取冻结回执的 `link_prices`，客户端不换算、不取整、不乘倍率。
- 每一笔资金动作必须先落本地再调服务端；同一个 `action_key` 可以重放，服务端只认第一次。
- 冻结、结算、收口的金额字段以**服务端回执**为唯一事实，本地推导只允许出现在 `expected_points`。
- 「还能不能结算」只认 `settle_deadline`；v1 兼容期没有该字段，才回退到 `expires_at`（见 Task 5）。
- v2 接口在服务端 S1/S2 上线前不可用，所有 v2 调用必须藏在开关后，默认走 v1。
- 任何会改变用户可见价格或退款结果的改动（裂变统一）必须单独提交，便于回滚。
- 开始实现前先跑 `git status`，确认工作区已有的用户改动不被混入。
- 每完成一个 Task 跑一次相关测试；不建分支、不提交，除非用户明确要求。

---

## 文件地图

### 迁移与持久化

- `local-runtime/wh_local/modules/pod_customization/migrations/027_billing_ledger_v2.sql`（新建）：重建 `pod_customization_billing_runs`，新增 `billing_settlements` / `billing_settlement_items` / `billing_recon_runs`。
- `local-runtime/wh_local/pod_migrations.py`：新增 `027_billing_ledger_v2` 的 `MigrationEffect` 契约与重建逻辑。
- `local-runtime/wh_local/modules/pod_customization/repository.py`：run 新列读写、状态 CAS、结算/明细/对账表 CRUD、删除保护查询。

### 计费契约与适配

- `local-runtime/wh_local/modules/pod_customization/billing_contract.py`：`PodCallPlan` 增加 `unit_ids`、`module`、`unit_kind`；列式改用 `pod_random_grid_v1`；裂变计划与全定制同构。
- `local-runtime/wh_local/modules/pod_customization/remote_billing.py`：v2 适配（字段映射、`link_idx` 0 基、`scope` 对象、`keys` 数组、错误码分类）；v1 兼容路径。
- `local-runtime/wh_local/customer/remote_client.py`：v2 HTTP 方法（freeze / settle / close / keys / get / list / reports），`Idempotency-Key` 头。
- `local-runtime/wh_local/config.py`：`billing_contract_version` 开关（`v1` / `v2`），默认 `v1`。

### 记账与收口

- `local-runtime/wh_local/modules/pod_customization/service.py`：`_freeze_action` 改为先写 intent；`settle_stuck_billing_runs` 改为按 `settle_deadline` 与错误码；新增 close 入口。
- `local-runtime/wh_local/modules/pod_customization/worker.py`：`PodBillingRun.settle()` 的 CAS、错误码映射、回执落库。
- `local-runtime/wh_local/modules/pod_customization/reconciliation.py`（新建）：启动对账、定时对账、日报对照。

### 测试

- `local-runtime/tests/test_pod_billing_ledger_migration.py`（新建）：迁移全新建库与从 008 升级两条路径。
- `local-runtime/tests/test_pod_billing_v2_contract.py`（新建）：冻结/结算/close 载荷与回执的契约快照。
- `local-runtime/tests/test_pod_billing_reconciliation.py`（新建）：启动对账四条分支、多设备待认领、日报差额。
- 扩展 `local-runtime/tests/test_pod_remote_client.py`、`local-runtime/tests/test_pod_column_billing.py`、`local-runtime/modules/pod_customization/tests/test_worker.py`。

### 文案

- `docs/操作答疑FAQ清单.md`：付费重试「无论成败都扣」改为「失败退款」。
- `docs/价格倍率与积分扣费整理_2026-09-09.md`：每款「40–50 随机」改为「固定 45」。

---

## Task 1: 账本表结构迁移（027）

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/migrations/027_billing_ledger_v2.sql`
- Modify: `local-runtime/wh_local/pod_migrations.py`
- Test: `local-runtime/tests/test_pod_billing_ledger_migration.py`

**目标：** 在不影响现有行为的前提下，让 `billing_runs` 能承载意图先行与冻结快照，并新增结算/明细/对账三张表。

- [x] 编写 027 SQL：新建 `pod_customization_billing_runs_v3`，`freeze_id` 允许 NULL 且保留唯一约束，`status` 扩为 `intent / authorized / settling / settlement_pending / settled / abandoned / expired_released / orphaned / anomaly`，新增 `module`、`unit_kind`、`billing_profile`、`link_count`、`item_count`、`frozen_points`、`link_prices_json`、`unit_ids_json`、`multiplier_percent`、`settle_deadline`、`device_id`、`freeze_raw_json`、`release_reason`、`zero_billed`、`batch_deleted_at`。
- [x] 数据搬迁：`INSERT INTO ..._v3 SELECT ...`，旧行 `module` 由 `action_type` 推导，`settle_deadline` 留空，状态原样保留；随后 `DROP` 旧表并 `ALTER ... RENAME`。
- [x] 新增 `pod_customization_billing_settlements`（`freeze_id` 唯一、`source`、`charged_points`、`refunded_points`、`released_points`、`retained_points`、`release_reason`、`rule_version`、`raw_response_json`）、`..._settlement_items`（`settlement_id + link_idx` 主键，`link_idx` 0 基）、`..._recon_runs`。
- [x] 在 `pod_migrations.py` 增加 `027_billing_ledger_v2` 的 `MigrationEffect`，并复用 008 已有的重建流程。
- [x] 测试：全新库直接建表；从 008 状态的库升级；旧行金额与状态不丢；`freeze_id = NULL` 可插多行；非法 status 被拒绝。

**Verification:**
```bash
python -m pytest local-runtime/tests/test_pod_billing_ledger_migration.py local-runtime/tests/test_pod_customization_migrations.py -q
```

---

## Task 2: Repository 层（意图、CAS、结算、删除保护）

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Test: `local-runtime/modules/pod_customization/tests/test_billing_ledger_state.py`（新建）

**目标：** 把状态迁移与金额落库收敛为带前置条件的原子操作，杜绝并发覆盖。

- [x] 新增 `create_billing_intent(...)`：只写 intent 行与 outcome 计划，不写 `freeze_id`。
- [x] 新增 `attach_billing_freeze(action_key, ...)`：CAS 从 `intent` → `authorized`，一次性写入全部冻结快照。
- [x] 把 `mark_billing_settled` 改为 `CAS(status IN ('settling','settlement_pending') → 'settled')`，并同时写 `release_reason`、`settle_deadline` 不变。
- [x] 新增 `terminate_billing_run(action_key, to_status, reason)`，只接受 `orphaned / expired_released / anomaly / abandoned` 四个目标状态。
- [x] 新增 `insert_settlement(...)` + `insert_settlement_items(...)`，同一事务；`freeze_id` 唯一冲突视为已结算并返回既有行。
- [x] 新增 `expire_stale_intents(now - 24h)`：`intent` 且从未冻结成功 → `abandoned`。
- [x] `delete_batch`：删除前检查该批次是否存在非终态 run，存在则抛 409「批次仍在结算中」；删除时不再 `DELETE billing_runs`，改为写 `batch_deleted_at`。
- [x] 测试：并发两路 settle 只有一条成功；CAS 不匹配返回 0 行；删除保护；24h 作废。

**Verification:**
```bash
python -m pytest local-runtime/modules/pod_customization/tests/test_billing_ledger_state.py -q
```

---

## Task 3: v2 契约适配层（开关默认 v1）

**Files:**
- Modify: `local-runtime/wh_local/customer/remote_client.py`、`local-runtime/wh_local/modules/pod_customization/remote_billing.py`、`local-runtime/wh_local/modules/pod_customization/billing_contract.py`、`local-runtime/wh_local/config.py`
- Test: `local-runtime/tests/test_pod_billing_v2_contract.py`、扩展 `local-runtime/tests/test_pod_remote_client.py`

**目标：** 让四模块的计划能无损映射到 v2 载荷，并正确解析回执；开关关闭时行为与今天完全一致。

- [x] `PodCallPlan` 增加 `module`、`unit_kind`、`unit_ids`；生成计划时按 `style-{i}` / `group-{g}`（0 基）写入并序列化进 `plan_json`。
- [x] 列式计划改用 `pod_random_grid_v1`；裂变计划改为与全定制同构（`for_batch` + 画像），不再有专属工厂。
- [x] `remote_client` 增加 v2 方法，统一带 `Idempotency-Key` 头；解析 `{"error": {"code", "retryable"}}` 为结构化异常。
- [x] `remote_billing` 增加载荷映射：`scope` 数组 → 对象、`four_grid` → `image`、`link_idx` 1 基 → 0 基、`rule_version` 转字符串、`links[]` → 明细。
- [x] `_grant` 支持 `keys` 为 `provider → 数组`；数组取第一个非空值；空密钥不再抛错，改为返回无密钥的 grant 并标记 `keys_missing`。
- [x] 配置开关 `billing_contract_version`，默认 `v1`；`v2` 分支未启用时不影响现有调用。
- [x] 测试：golden payload 快照（冻结/结算/close）；`keys` 数组解析；0 基与 1 基不混用；错误体解析。

**Verification:**
```bash
python -m pytest local-runtime/tests/test_pod_billing_v2_contract.py local-runtime/tests/test_pod_remote_client.py -q
```

---

## Task 4: 先写意图再冻结

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`（`_freeze_action`）、`repository.py`、`worker.py`
- Test: 扩展 `local-runtime/modules/pod_customization/tests/test_worker.py`、`local-runtime/tests/test_pod_column_billing.py`

**目标：** 消灭「冻结已创建、本地没记录」，崩溃后能用同一个 key 接管。

- [x] `_freeze_action` 顺序改为：`create_billing_intent` → `coordinator.freeze` → `attach_billing_freeze`。
- [x] 冻结抛 4xx：保留 intent + `error_message`，异常上抛；用户重发时复用同一行同一键。
- [x] 冻结抛 5xx / 网络 / 契约破坏：同样保留 intent，由启动对账重放。
- [x] 回填失败（写库异常）：保留 intent，不再执行「全部 no_return 结算」补偿；由对账重放接管。
- [x] 重放前校验返回 `status == frozen`；非 frozen 走 Task 5 的终态映射。
- [x] 测试：模拟 freeze 成功但落库失败 → 行仍为 intent；用同一 key 重放 → 变 authorized；重复调用不产生第二条冻结。

**Verification:**
```bash
python -m pytest local-runtime/modules/pod_customization/tests/test_worker.py local-runtime/tests/test_pod_column_billing.py -q
```

---

## Task 5: 错误码分流、close 与时间口径

**Files:**
- Modify: `service.py`、`worker.py`、`remote_billing.py`、`repository.py`
- Test: 扩展 `local-runtime/modules/pod_customization/tests/test_worker.py`、`local-runtime/tests/test_pod_billing_v2_contract.py`

**目标：** 让每种失败都有确定终态，不再无限重试。

- [x] 统一错误映射：`400` → `anomaly`；`409 freeze_not_active` → 探活后转 `expired_released` 或补记 `settled`；`409` 键复用/键冲突 → `anomaly`；`404` → `orphaned`；`429 / 5xx / 网络` → 重试。
- [x] `settle_stuck_billing_runs` 改用 `settle_deadline` 判断（v2）；v1 兼容期回退到 `expires_at`，不自行加宽限。
- [x] `started` 收口：`now <= settle_deadline` 不动；到期后先 `GET` 探活，只有 `frozen` 才把 `started` 改写 `no_return` 并结算。
- [x] 新增 `close_billing_run`：用户主动放弃走 `POST /close`；v1 兼容期降级为「全 no_return 结算」。
- [x] 删除批次前若存在非终态 run，接口提示并提供「放弃并结算」入口（调用 `close_billing_run` 后再删）。
- [x] 测试：400 不重试；409 三分支；429 重试；deadline 前后 1 秒的行为；close 后不可再结算。

**Verification:**
```bash
python -m pytest local-runtime/modules/pod_customization/tests/test_worker.py local-runtime/tests/test_pod_billing_v2_contract.py -q
```

---

## Task 6: 裂变统一与账本保留

**Files:**
- Modify: `billing_contract.py`、`repository.py`、`service.py`
- Test: `local-runtime/tests/test_fission_billing.py`、`local-runtime/modules/pod_customization/tests/test_replica_billing.py`

**目标：** 删掉裂变的专属计费逻辑，并让账本比批次活得久。

- [x] 删除 `project_fission_settlement` 与 `fission_billable_styles` 固化；结算按 `image = success` 判定。
- [x] 裂变计划改用统一工厂，画像仍为 `pod_fission_v1`，`link_count` = 款数。
- [x] 删除结算前对 `public_url` 与标题完整性的反查。
- [x] `delete_batch` 不再删账本（Task 2 已改），补一条测试断言：删除批次后 run 仍在且 `batch_deleted_at` 非空。
- [x] 测试：标题失败但图成功 → 扣款；图失败 → 退款；补标题不产生新冻结。

**Verification:**
```bash
python -m pytest local-runtime/tests/test_fission_billing.py local-runtime/modules/pod_customization/tests/test_replica_billing.py -q
```

---

## Task 7: 启动对账与日报对平

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/reconciliation.py`
- Modify: `service.py`、`repository.py`、`router.py`
- Test: `local-runtime/tests/test_pod_billing_reconciliation.py`

**目标：** 启动时把没关单的账逐笔收敛，并能和服务端日报对平。

- [x] 启动对账（登录拿到远端凭证后跑一次，之后每 10 分钟）：只捞非终态 run，按 `GET /v2/pod/freezes/{id}` 四分支处理（`frozen` / `settled` / `released` / 404）。
- [x] `intent` 行用 `action_key` 重放冻结接管。
- [x] 未结清列表：`GET /v2/pod/freezes?status=open`，只自动处理 `device_id` 为本机且本地有 run 的冻结，其余进「待认领」。
- [x] 日报：本地出账 + 与服务端 `/reports/daily` 逐项比对，差额用 `/daily/detail` 下钻，结果写 `billing_recon_runs`。
- [x] 暴露只读接口供前端/排查使用（不新增页面）。
- [x] 测试：四分支、多设备待认领、差额下钻、`errored` 不自动修数。

**Verification:**
```bash
python -m pytest local-runtime/tests/test_pod_billing_reconciliation.py -q
```

---

## Task 8: 文案与价格说明同步

**Files:**
- Modify: `docs/操作答疑FAQ清单.md`、`docs/价格倍率与积分扣费整理_2026-09-09.md`

- [x] 付费重试：改为「失败也会退款；重试是新的独立扣费」。
- [x] 价格：每款「40–50 随机」改为「固定 45」；补列式 180/组、半成品 32/组、复刻 B 180/款。
- [x] 全库检索「随机」「40~50」「无论成败」，确认没有残留旧口径。

**Verification:**
```bash
rg -n "40~50|40–50|无论成败" docs/ || true
```

---

## 阶段顺序与开关

| 顺序 | Task | 行为变化 | 依赖 |
|---|---|---|---|
| 1 | T1 迁移 | 无 | — |
| 2 | T2 Repository | 无（仅新增能力） | T1 |
| 3 | T3 v2 适配 | 无（开关默认 v1） | T1 |
| 4 | T4 先记账 | 有（崩溃语义改善） | T2 |
| 5 | T5 错误分流 / close | 有 | T2、T4 |
| 6 | T6 裂变统一 | **有（用户可见退款口径变化）** | T2、T4 |
| 7 | T7 对账 | 无用户可见变化，新增内部能力 | T3、T5、服务端 S2 |
| 8 | T8 文案 | 无代码变化 | T6 |

- `billing_contract_version` 默认 `v1`，T7 验收通过后再评估切 `v2`。
- T6 单独提交，出问题时可以只回滚这一项。
- 服务端 S1 未上线前，T4/T5 只用 v1 的既有语义验证；`settle_deadline` 分支用假客户端单测覆盖。

---

## 未决依赖（不阻塞 T1–T6，阻塞 T7 验收）

| 项 | 影响 |
|---|---|
| provider 名对照（`wuyin`/`ark` vs `openai`/`doubao`） | T3 的 `keys` 解析只能先用测试桩；真实联调前必须拿到对照表 |
| 服务端 `/v2/pod/freezes?status=open` 与 `/reports/daily` 上线（S2） | T7 只能先写逻辑与单测，不能真实验收 |
| v1 下线时间表 | 决定 `billing_contract_version` 的切换与回滚窗口 |
| 复刻 B 价目写法（180×400% 还是 45×400%） | 服务端问题，不影响客户端；但联调时若看到 720/款，按错账处理 |

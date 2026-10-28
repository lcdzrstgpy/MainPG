# POD 四模块计费账本与对账设计

状态：**结构已定 v0.11**（2026-10-23，已对齐服务端方案 v1.4，尚未开始编码）

目标读者：POD 四模块前后端实现者、计费与验收人员

范围：客户端为落地实现；服务端部分只记录「目标契约」与「已对齐的接口」，实现细节由服务端负责。

---

## 1. 一句话

把 POD 四个模块的「冻结 → 干活 → 上报 → 结算」过程记成一套可核对的账，做到：**钱不白锁、账不丢、差异查得到**。

---

## 2. 背景

- 现有模式：客户端干活并上报每次调用的结果，服务端按上报结果扣费；冻结到期未结算时按比例释放作为兜底。
- 这套兜底只解决「积分不被永久锁死」，**不解决对账**：它回答不了该退多少、退给谁、有没有漏结。
- 四个模块的调用方式、入口路由、上报字段、成功判定各不相同，导致同一套账本没法直接套用。
- 本次重构建的是**目标契约**：账本要能逐笔对平，所以契约里必须有精确金额和完整状态，不能靠客户端推算。当前实现与目标的差距只在第 11 节列一次，用于评估改造量，不作为设计约束。
- 已发现的四个问题（附录 A）里，前三条由「账本 + 补单 + 收口 + 删除保护」解决；第四条随裂变并入统一计费模式一并解决。

---

## 3. 目标与非目标

### 3.1 目标

1. 每一笔冻结都有唯一单号，从「打算花钱」到「最终扣/退」全程可追溯。
2. 断网、崩溃、重启后能自动补单和收口，不依赖人工。
3. 四个模块的差异收敛到「适配层」，账本结构统一。
4. 每天能和服务端日报逐笔对平。

### 3.2 非目标

- 本工作区不写服务端代码；服务端配套改造以契约与能力要求的形式提出（第 8 节），实现由服务端负责。
- 不改服务端的计费公式本身；需要调整的是冻结/重放/收口/计量这些**语义**，不是单价算法。
- 不覆盖 POD 以外的扣费：`combo_kit`、`product_processing` 与充值/活动/人工调整都不在本账本内，日报需显式标注。
- 不引入新的价格策略。裂变并入统一计费模式（见 4.2）是既定口径。

### 3.3 已定的结构决策

| 决策 | 结论 |
|---|---|
| 账本范围 | 只覆盖 POD 四模块 |
| 落地方式 | 改造现有两张表，新增三张；不新建 `billing_*` 命名空间 |
| 记账顺序 | 先写意图账，再调冻结 |
| 接口版本 | 新客户端直接对接 v2（`/api/customer/billing/v2`）；v1 仅作为服务端转译层，供老客户端过渡 |
| 时间口径 | 「会不会过期」只以服务端返回的 `settle_deadline` 为准；客户端**不再**自行 `expires_at + 5min` |
| 金额核对 | 本地算 expected，只用于异常告警，必须带 `rule_version` |
| 收口方式 | 到期 → 探活后收口；用户主动放弃 → `POST /close` |
| 账本生命周期 | 永不随批次删除；删批次只记 `batch_deleted_at` |
| 删除保护 | 存在非终态 run 时直接拒绝删除 |
| 零计费动作 | 入账，`held = 0` 即零计费；报表用 `module = pod_zero` 归类 |
| 裂变 | 按款冻结、按款结算，走统一入口 + 画像；不单独路由，取消「首次结算固化可计费款」 |
| 单元标识 | 计划生成时生成 `unit_id` 并持久化；冻结与结算都发送，且必须逐位一致 |
| 索引起点 | `link_idx` 在 v2 统一 0..N-1；本地也存 0 基，1 基只出现在 v1 转译层 |
| 幂等键 | `freeze_id = idempotency_key`，全局唯一；以 `Idempotency-Key` 头为准，body 镜像必须一致 |
| 付费重试 | 用普通画像，**失败也退款**（不做 `charge_policy=attempt`）；重试仍是独立冻结。操作答疑里「无论成败都扣」的文案要同步改 |
| 定价形态 | 服务端已定：**单一固定价 × 整数百分比倍率**，无区间、无随机。全定制 / 复刻 A / 裂变 45/款，半定制 32/组，列式 180/组，复刻 B 180/款。客户端只读回执的 `link_prices`，不参与定价 |
| 错误分流 | 按错误码而不只按 HTTP：400 → `anomaly`；409 分 `freeze_not_active` / `idempotency_key_reused` / `idempotency_key_conflict`；404 → `orphaned`；429/5xx/网络 → 重试 |
| 空意图 | 24 小时未拿到 `freeze_id` 且从未冻结成功 → 自动 `abandoned` |
| 多设备 | 对账只自动处理「本机 `device_id` + 本地有 run」的冻结；其余进「待认领」 |

---

## 4. 四模块现状对比

| 维度 | 全定制 | 半定制 | 爆款复刻 | 爆款裂变 |
|---|---|---|---|---|
| 计费单位 | 款 | **组**（4 款 = 1 组） | 款（A 档）；款（B 档按 4 倍价） | 款 |
| 冻结画像 | `pod_random_v1` | `pod_semi_v1` | `pod_replica_v1_a` / `_b` | `pod_fission_v1` |
| 冻结上报 | `link_count` = 款数；`scope` = 图 + 标题 | `link_count` = 组数；`item_count` = 款数；`scope` = 仅图 | `link_count` = 款数；`scope` = 图 + 标题 | `link_count` = 款数；`scope` = 图 + 标题 |
| 每款/组调用 | 每款 2 次图 + 最多 5 次标题 | 每组 2 次图（无标题） | A：每款 2 次图；B：每款 8 次图（4 面板 × 2） | 每款 2 次图 + 最多 5 次标题 |
| 成功判定 | 任一次图成功 → 扣该款；标题失败不影响 | 任一次图成功 → 扣该组 | 任一次图成功 → 扣该款（B 档 4 倍价） | 任一次图成功 → 扣该款；标题失败不影响（本次统一） |
| 重试单位 | 款 / 整批 / 标题 | 组 | 款 / 整批 | 款 / 整批 / 标题 |
| 免费操作 | 标题重生、补标题 | 无 | 识图、构图（仅标题 scope，0 元） | 标题重生、补标题 |

补充说明：

- **列式生成**是全定制里的一种形态：4 款拼成一组。服务端为它单独出了画像 `pod_random_grid_v1`（`unit_kind = group`，`item_count = link_count × 4`），客户端不再用 `pod_random_v1` 上报列式。
- **不存在「独立计费通道」**：四个模块共用同一张冻结表、同一个幂等命名空间、同一套结算规则。现状 `/pod-semi/*`、`/pod-fission/*` 只是同一套账上的路由入口；本轮把裂变收敛回统一入口，半定制在服务端把 `item_count` 校验下沉到画像后也会收敛。
- 复刻 B 档（高清直出）是否对用户开放，以实现现状为准；账本按「1 款 1 个结算单元」记，价格由服务端决定，客户端不自行乘。
- **定价已从「每款随机 40–50」改为「固定 45」**（列式 180/组、半定制 32/组、复刻 B 180/款）。平均价不变，但用户看到的每款价格不再浮动；相关的价格说明文档要同步改。
- 四模块的共同点：冻结都带单号、金额、到期时间；结算都按「每个 link 一条结果 + 功能子项状态」上报。**差异在计费单位、画像、成功判定，不在账本形状。**

### 4.1 各模块的「结算单元」

| 模块 | 一个结算单元是什么 | 单元内看什么 | `unit_id` 形态 |
|---|---|---|---|
| 全定制（按款） | 1 款 | 任意一次图成功 | `style-{i}` |
| 全定制（列式） | 1 组（4 款） | 主图角色成功 | `group-{g}` |
| 半定制 | 1 组（4 款） | 任意一次图成功 | `group-{g}` |
| 复刻 A | 1 款 | 任意一次图成功 | `style-{i}` |
| 复刻 B | 1 款 | 任意一次面板成功 | `style-{i}` |
| 裂变 | 1 款 | 任意一次图成功 | `style-{i}` |

账本只记「单元、每个功能的结果、金额」，不记「这个模块是怎么判的」；判定规则放在适配层，随模块演进，不影响账本结构。

`unit_id` 在**生成本次计划时**确定并写进 `plan_json`，之后冻结、续跑、重试、结算都用同一份，不允许结算时重新生成。

### 4.2 决策：裂变并入统一计费模式（已定）

服务端已确认：`pod_fission_v1` 与 `pod_random_v1` 的计价方式完全一致——**按 link 计价，`image = success` 即扣整条 link 价，title 失败不影响退款**。裂变现在的「标题失败整款退」不是服务端规则，而是客户端上报 `no_return` 造成的。

**结论：裂变按「款」冻结、按「款」结算，与其他模块完全同构，不再有任何专属逻辑，也不再单独路由。**

- 冻结：走统一入口 + `billing_profile = pod_fission_v1`，`link_count` = 款数。
- 结算：`image = success` 即扣该款；标题失败不影响扣费，标题本身不单独计价。
- 删除「首次结算固化」：不再写 `fission_billable_styles`，不再反查 `public_url` 与标题完整性。
- 补标题保持免费，但因为不再退款，不存在「先退后白补」的套利。

对用户的影响只有一条：标题失败的款不再整款退款。这是本次重构的既定口径。

---

## 5. 账本结构

账本落在客户端本地库。**改造现有两张表，新增三张**，不另起命名空间。

### 5.1 总览

| 表 | 作用 | 与早前草案的对应 |
|---|---|---|
| `pod_customization_billing_runs`（改造） | 意图账 + 冻结快照 | 草案 5.1 + 5.2 合并 |
| `pod_customization_billing_outcomes`（沿用） | 逐调用执行明细 | 草案 5.3 的底层证据 |
| `pod_customization_billing_settlements`（新增） | 一次结算/收口一行 | 草案 5.3 主表 |
| `pod_customization_billing_settlement_items`（新增） | 每个结算单元一行 | 草案 5.3 明细表 |
| `pod_customization_billing_recon_runs`（新增） | 对账流水 | 草案 5.4 |

意图账与冻结账合并成一张：两者一对一，拆开只增加 join，不增加审计价值。唯一动作键 `action_key` 就是幂等键，在调服务端之前就已经确定；冻结成功后它**等于** `freeze_id`（服务端确认 `freeze_id = idempotency_key`），所以崩溃后直接用 `action_key` 重放即可接管。

### 5.2 `billing_runs`（改造）

已有：`run_id`、`action_key`（唯一）、`action_type`、`target_id`、`batch_id`、`workspace_id`、`owner_user_id`、`freeze_id`（唯一）、`rule_version`、`grant_expires_at`、`plan_json`、`action_payload_json`、`status`、`result_status`、`error_message`、`created_at`、`updated_at`、`settled_at`。

调整与新增（字段名尽量与冻结回执一致，减少映射）：

| 字段 | 说明 |
|---|---|
| `freeze_id` 放宽为可空 | 意图行先落库，此时还没有单号。SQLite 的 UNIQUE 允许多个 NULL |
| `status` 扩枚举 | 见 5.6 |
| `module` | `pod_custom` / `pod_semi` / `pod_replica` / `pod_fission` / `pod_zero`，与 v2 请求字段一致 |
| `billing_profile` | 从 `plan_json` 提升为列，可查询、可按画像过滤 |
| `unit_kind` | `style` / `group`（复刻面板仍按款计，记 `style`） |
| `link_count` / `item_count` | 结算单元数 / 款数（组类画像才有 `item_count`） |
| `frozen_points` | 冻结金额，**REAL，最多 1 位小数** |
| `link_prices_json` | 每个结算单元的单价快照，数组，长度 = `link_count` |
| `unit_ids_json` | 与 `link_prices` 一一对应的单元 ID 列表，冻结回执会回显，必须逐位一致 |
| `multiplier_percent` | 冻结时的倍率快照 |
| `settle_deadline` | **权威过期点**，由服务端返回；本地所有「还能不能结算」的判断只认它 |
| `device_id` | 发起冻结的本机标识，多设备对账用 |
| `freeze_raw_json` | 冻结回执原文 |
| `release_reason` | `normal` / `no_return_close` / `ttl` |
| `zero_billed` | 零计费动作标记（等价于 `frozen_points = 0`） |
| `batch_deleted_at` | 批次被删除的时间。账本行本身不删 |

`grant_expires_at` 保留但只作展示（上游密钥有效期），不再参与结算准入判断。

### 5.3 `billing_outcomes`（沿用）

字段维持 `run_id`、`call_id`、`feature`、`status`（`planned` / `started` / `success` / `no_return`）、`updated_at`。

**不压缩成 link 级。** 逐调用是「这次调用到底发生没有」的唯一证据，也是超时收口、防作弊和事后追责的依据；link 级只是它在钱上的汇总。两层并存，不要在结算时把调用行删掉或改写。

不新增 `started_at`：收口按 `settle_deadline` 判断（见 7.2），`updated_at` 在启动那一刻已经记录。

### 5.4 `billing_settlements` / `billing_settlement_items`（新增）

结算主表：

| 字段 | 说明 |
|---|---|
| `settlement_id` | 本地主键（服务端回执的 `settlement_id` 同值存储） |
| `freeze_id` | 唯一，一笔冻结最多一条结算 |
| `source` | `settle` / `close` / `ttl`，与服务端口径一致 |
| `expected_points` | 本地用 `link_prices` × 适配层判定算出的应扣，仅用于告警 |
| `charged_points` / `refunded_points` | 服务端回的实际扣/退（聚合值） |
| `released_points` / `retained_points` | 释放类结算的精确金额，`settle` 时为空 |
| `release_reason` | `normal` / `no_return_close` / `ttl` |
| `rule_version` | TEXT，不透明字符串，只做相等比较 |
| `raw_response_json` | 结算回执原文 |
| `created_at` / `settled_at` | 时间 |

`retry_premium_points` 已由服务端废弃（重试开新冻结），本地不再新增该列；v1 回执固定回 `0.0`，读到即忽略。

结算明细（每个结算单元一行）：`settlement_id`、`link_idx`（0 基）、`unit_id`、`feature_status_json`、`charged_points`、`refunded_points`。

**这些金额必须来自结算回执的 `links[]`，不允许客户端推算。** 落库时标注 `source = server`；本地推导值只允许出现在 `expected_points` 一个字段里。

聚合值必须等于明细之和；不等即为契约违约，记异常而不是修正任何一边。

### 5.5 `billing_recon_runs`（新增）

每次对账一行：`run_id`、对账时间、范围（启动 / 定时 / 每日）、检查笔数、修复笔数、损失笔数、损失金额、与服务端日报的差额、备注。

### 5.6 客户端状态机

```text
intent ──冻结成功──► authorized ──开始干活──► settling ──结算失败──► settlement_pending
  │                     │                        │                      │
  │                     └────────结算成功────────┴──────────────────────┴──► settled
  │
  ├──用户放弃（close）──────────────────────────────────────────────────► settled（release_reason=no_return_close）
  ├──空意图 24h─────────────────────────────────────────────────────────► abandoned
  ├──到期被释放────────────────────────────────────────────────────────► expired_released
  ├──服务端查不到──────────────────────────────────────────────────────► orphaned
  └──不可重试错误（400 / 键冲突）──────────────────────────────────────► anomaly
```

- **非终态**：`intent` / `authorized` / `settling` / `settlement_pending`。启动对账只捞这四个状态。
- **终态**：`settled` / `abandoned` / `expired_released` / `orphaned` / `anomaly`。
- **收口不单独占状态**：`close` 与 `ttl` 在服务端都是 `settled` 或 `released`，本地用 `source` + `release_reason` 区分。
- 已有的 `resume_claimed` 保留为 `settlement_pending → settle` 的抢占标记；`auth_required` 已是 `settlement_pending` 的别名，不再新增。
- **`intent` 超时作废**：超过 24 小时仍没拿到 `freeze_id`、且从未成功冻结过的行，自动转 `abandoned`。
- **错误必须按码分流**，不能只按 HTTP 状态：
  - `400` 系列（`units_mismatch` / `unit_mismatch` / `scope_violation` / `amount_overflow` / `invalid_payload`）→ `anomaly`，不重试；
  - `409 freeze_not_active` → 探活后转 `expired_released` 或补记 `settled`；
  - `409 idempotency_key_reused` / `idempotency_key_conflict` → `anomaly`（前者是客户端换了载荷，后者是键撞了别人的）；
  - `404` → `orphaned`；
  - `429` / `5xx` / 网络超时 → 重试（按 `retryable` 字段判断更稳）。

状态流转必须带前置条件（CAS），不能无条件 UPDATE。当前 `mark_billing_settled` 是无条件更新，而结算入口至少有三条（用户重试、启动扫描、关机前收尾），并发下会出现后写覆盖先写。

### 5.7 金额与单位

- API 金额字段是 **JSON number，最多 1 位小数**（如 `45`、`40.5`），单位是积分。**不是 0.1 积分整数**。
- 服务端内部才用 0.1 积分单位；客户端**原样存 API 值，不换算、不取整、不乘除**。
- 字段类型用 REAL，不要用 INTEGER。
- 比较 expected 与 actual 时按 0.1 精度容差，避免浮点尾差误报。
- `rule_version` 存 TEXT，**只做相等比较**，禁止解析成数字或比较大小。
- 冻结回执、结算回执整段落库（`freeze_raw_json` / `raw_response_json`）。出差异时这是客户端唯一能自证的材料。
- 本地 `expected_points` 只能由「冻结回执的 `link_prices` × 适配层判定」算出，**不得**引用本地价目表或模块常量。

### 5.8 零计费动作

`brief`、`composition`、复刻识图、复刻构图、裂变分析、结构分析、试用都走同一套冻结 → 结算，但 `held = 0`。

- 服务端判定零计费的唯一准则是 `held == 0`，不看画像名，所以客户端沿用现有画像 + 纯 title scope 也能被正确识别。
- 本地的 `zero_billed` 标记以「回执 `frozen_points == 0`」为准，不靠画像字符串推断。
- 报表归类用 `module = pod_zero`；日报里「冻结笔数」「结算笔数」只统计非零计费，零计费单列。

### 5.9 迁移成本

SQLite 不支持直接修改 `NOT NULL` 与 `CHECK` 约束，`freeze_id` 放宽和 `status` 扩枚举都需要**重建表**。仓库里已有同类先例（`pod_customization_billing_runs_v2` 的迁移）。这一步必须在 P0 完成，否则意图账无法落地。

历史 run 没有单价快照、单元 ID、`settle_deadline`，迁移时留空，不反推；这类历史行在启动对账里按「无快照」单独统计，不参与 expected 比对。

---

## 6. 数据流

### 6.1 正常流程

```text
用户点开始
  │ ① 生成唯一动作键 K 与 unit_ids，写 runs（status=intent，module + 画像 + 计划单元）
  ▼
POST /v2/pod/freezes
  │ ② 服务器回：freeze_id(=K)、status=frozen、frozen_points、link_prices、
  │    unit_ids(回显)、expires_at、settle_deadline、rule_version、keys、keys_error
  ▼
校验 status == frozen，回填 runs（status=authorized，全部快照 + settle_deadline）
  │ ③ 干活；每完成一次调用写一条 outcome
  ▼
汇总 outcome → POST /v2/pod/freezes/{id}/settle
  │ ④ 服务器回：charged_points / refunded_points / links[]（逐单元金额与状态）
  ▼
写 settlements + settlement_items → runs.status=settled
```

铁律四条：

1. **先写本地，再调服务器。**
2. **同一个动作键可以重复调，服务器只认第一次**；但重放返回的可能是已终态记录（服务端已改为 409 + `status`），所以**每次重放都必须校验返回的 `status`**，只有 `frozen` 才能继续。
3. 动作键必须**全局唯一**（服务端 `freeze_id` 是全局主键）。现有 key 都内嵌 uuid 批次号，满足；新增动作不得使用时间戳、自增数或用户可控字符串。
4. **`unit_ids` 一次生成、全程复用**：冻结、续跑、重试、结算都用同一份，结算时不允许重新推导。

### 6.2 异常分支

| 断点 | 现象 | 处理 |
|---|---|---|
| ① 之后、② 之前 | 有 intent 行，没有 freeze_id | 用同一 K 重放冻结；服务器只认第一次，返回原冻结 |
| ② 回包丢失 / 解析失败 | 服务器已建冻结，客户端没拿到单号 | 同上，**绝不能判为「不存在」** |
| ② 冻结已落库但服务端 5xx | 服务端约定任何未落库的失败都回滚，因此 5xx 只可能来自「已提交但响应没送到」 | 用同一 K 重放取回；若返回 404 才判未创建 |
| ② 服务器明确拒绝（400） | 冻结未创建，未动钱 | 留 `intent` + `error_message`，允许用户重发 |
| ② 同键不同载荷（409 `idempotency_key_reused`） | 键复用错误 | 转 `anomaly`，不复用旧键，重新生成新键 |
| ② 同键属于其他账号（409 `idempotency_key_conflict`） | 键撞了别人的 | 转 `anomaly`，重新生成新键 |
| ③ 中途崩溃 | 部分调用有结果，部分停在 started | 重启后先探活（见 7.2），能续跑续跑，不能续跑按 `settle_deadline` 收口 |
| ④ 结算失败（5xx / 网络 / 429） | 结果已定，钱没结 | 记 `settlement_pending`，按 `retryable` 退避重试（结算幂等） |
| ④ 结算返回 400 | 载荷或契约错误（条数、`link_idx`、`unit_id`、scope 不匹配） | **不可重试**，转 `anomaly`，保留原记录等人工处理 |
| ④ 结算返回 409 `freeze_not_active` | 已终态（TTL 已释放或已被结算） | **不能重试**，立即探活并转 `expired_released` 或补记 `settled` |
| ④ 结算返回 404 | 不存在或非本人 | 转 `orphaned`，进日报待人工 |
| 服务器先到期 | 已过 `settle_deadline`，被 TTL 释放 | 探活发现后记 `expired_released`，金额取回执实值 |
| 用户主动放弃 | 还在冻结中 | 调 `POST /close`，按已发生调用计费，`release_reason = no_return_close` |
| 用户删除批次 | 账还没结 | 有非终态 run 时**拒绝删除**，提示「批次仍在结算中」，并提供「放弃并结算」入口 |

冻结失败必须按错误类型决定去留，不能笼统 catch：

| 错误类型 | 含义 | 处理 |
|---|---|---|
| 4xx 业务拒绝 | 服务器收到了并明确拒绝 | 留 `intent` + `error_message`；用户重发时复用同一行同一键（充值或频控解除后可直接成功） |
| 429 / 服务不可达 | 限流、网络、超时 | 留 `intent`，退避重试，**不作废** |
| 回包契约破坏 | 响应读不出来 | 留 `intent`，用同一键重放，**不作废** |
| 用户主动放弃 | 客户端决定 | 标 `abandoned` |

进程在「冻结成功 → 回填 runs」之间崩溃时，本地只留 intent 行。启动对账用同一键重放即可接管：服务端返回同一笔冻结，`freeze_id` 就是 K。

### 6.3 入口与接口落点

新客户端只用 v2，一个写入口、一个读入口：

| 客户端动作 | v2 接口 |
|---|---|
| 冻结（四模块统一） | `POST /v2/pod/freezes` |
| 结算 | `POST /v2/pod/freezes/{id}/settle` |
| 主动放弃 | `POST /v2/pod/freezes/{id}/close` |
| 补领密钥 | `POST /v2/pod/freezes/{id}/keys` |
| 探活 / 查单笔 | `GET /v2/pod/freezes/{id}` |
| 未结清列表 | `GET /v2/pod/freezes?status=open` |
| 日报对照 | `GET /v2/pod/reports/daily` + `/daily/detail` |

v1（`/batch/*`、`/pod-semi/*`、`/pod-fission/*`）只由服务端转译层承接老客户端，新客户端不调用；裂变的 `/pod-fission/*` 在服务端退役为别名。

---

## 7. 对账流程

### 7.1 第一层：每笔结算后立刻对

本地 `expected_points` vs 服务端 `charged_points`。不一致时：

- 两边 `rule_version` 相等 → 记异常（单价、范围或明细有一边算错了）；
- 版本不同 → 只记录、不告警（服务端改规则是正常情况）。

异常只记录，**不阻断结算**。

### 7.2 第二层：启动时对（最重要）

范围：当前 `workspace_id + owner_user_id`，在拿到远端凭证之后立即跑一次，之后每 10 分钟只查仍未关单的。

本地非终态 run 逐个查 `GET /v2/pod/freezes/{id}`，按返回的 `status` 处理：

| 服务器状态 | 本地处理 |
|---|---|
| `frozen` | `now <= settle_deadline` → 能续跑就续跑，不能就按未完成结算（全部 `no_return`） |
| `settled` | 补记 settlements（回执字段齐全），关单 |
| `released` | 记 `expired_released`，金额取 `released_points` / `retained_points`，不推算 |
| 404 | 记 `orphaned`，计数进日报，等待人工 |

**`started` 收口**：`now <= settle_deadline` 时不动；到点后先探活再结算——只有服务端确实还是 `frozen` 才把 `started` 改写为 `no_return` 并结算。先探活是必须的：直接结算在已释放时返回 409，且会把「实际成功但结果没回来」的调用错判成失败。

**`intent` 收口**：没有 `freeze_id` 的行，用 `action_key` 重放冻结（`Idempotency-Key` 头 = `action_key`）。返回 `frozen` → 接管；返回 409 `freeze_not_active` → 探活后按上表处理；400 → 记错误等用户重发。

**本地没有记录、服务端有冻结**：靠 `GET /v2/pod/freezes?status=open` 列出。列表是按账号的，不是按设备的。

**多设备规则**：只有「`device_id` = 本机 **且** 本地有对应 run」的冻结才自动处理；其余一律进「待认领」，只展示、不自动结算。否则 B 机会把 A 机正在用的冻结提前收掉。

**时间口径**：只认服务端返回的 `settle_deadline`，客户端**不再**自行 `expires_at + 5min`。服务端 `grace` 已固定 10 分钟且大于客户端探活间隔。

### 7.3 第三层：每日报表

本地先出账，再和服务端日报逐项对：

- 冻结：笔数、金额（不含零计费）
- 正常结算：笔数、实扣、退还
- 收口结算：笔数、实扣（`source = close`）
- 过期释放：笔数、释放金额、保留金额（取服务端实值）
- 孤儿 / 待认领：笔数
- 不可重试异常（400 / 键冲突）：笔数
- 零计费动作：笔数（单独口径）
- 单价告警：expected 与 actual 不一致的笔数

### 7.4 第四层：与服务端日报逐笔对平

`GET /v2/pod/reports/daily?date=&tz=Asia/Shanghai` 拿服务端汇总，和本地第三层逐项比对：

| 项 | 期望 |
|---|---|
| 冻结笔数 / 金额 | 相等（本地可能有尚未回填的历史行，差异需按 `freeze_id` 下钻） |
| 结算笔数 / 实扣 / 退还 | 相等 |
| 收口 / 释放笔数与金额 | 相等 |
| 零计费笔数 | 相等 |
| `open_freezes` | 本地非终态数 ≤ 服务端（本地可能缺记录，缺的部分进「待认领」） |

差额 ≠ 0 时用 `/reports/daily/detail` 按 `freeze_id` 下钻；解释不了的差额记 `recon_runs` 并告警，**不自动修数**。

### 7.5 以谁为准

钱在服务器，**以服务器为准**。本地账是「过程记录 + 追责线索」，不参与最终定价。

---

## 8. 目标契约（账本成立的前提）

这一节不是「建议补的能力」，是重构后账本能逐笔对平的**前提条件**。A 级不满足，账本不成立；B 级不满足，明细对不上；C 级不满足，只能防君子不能防小人。表结构、任务调度、部署形态由服务端定，本文只定契约。

### 8.1 分级

| 级别 | 含义 | 客户端阶段 |
|---|---|---|
| A 契约 | 缺一项，账本就有对不上的路径 | P0 / P1 / P2 |
| B 出口 | 缺一项，只能人工兜底或留安全口子 | P2 / P3 |
| C 可观测 | 缺一项，「调用是否发生」仍只能听客户端说 | P3 |

### 8.2 A 级：账本成立的前提

1. **冻结原子性**：`POST /v2/pod/freezes` 单事务；失败即回滚；冻结成功必返 `freeze_id`，且不受密钥下发影响；响应丢包靠同键重放取回。

2. **重放语义收敛**：同一幂等键在终态重放必须明确报错（409 `freeze_not_active` + `status`），而不是返回旧记录。

3. **结算返回逐单元明细**：`links[] = {link_idx, unit_id, status, charged_points, refunded_points}`，且 `Σ charged = charged_points`、`Σ refunded = refunded_points`。

4. **释放返回精确金额**：`released` 状态必返 `released_points`、`retained_points`、`release_reason`。释放口径是「按已发生调用计费、无调用全退」，不是固定比例。

5. **时间语义唯一**：`settle_deadline` 是唯一的过期判断点；`expires_at` 只用于展示与密钥有效期；`settle_deadline` 之前的结算必须合法。

6. **未结清冻结列表**：`GET /v2/pod/freezes?status=open`，返回 `freeze_id / billing_profile / module / workspace_id / device_id / frozen_points / expires_at / settle_deadline / created_at`，支持游标与按模块过滤。

7. **单元标识与索引**：冻结、结算、回执三处的 `unit_id` 必须逐位一致；`link_idx` 统一 0..N-1。

8. **裂变收敛**：`pod_fission_v1` 是普通画像，无专有分支、无「可计费款」固化、不反查标题完整性。

### 8.3 B 级：出口与错误码

9. **密钥与冻结解耦**：`POST /v2/pod/freezes/{id}/keys`，仅 `frozen` 且未过期可发；key 有效期跟随冻结；按 scope 只发需要的 provider；`keys` 为 `provider → 数组`，且需给出与客户端 provider 名（`wuyin` / `ark`）的对照。
10. **主动收口**：`POST /v2/pod/freezes/{id}/close`，按已发生调用计费 + 未上报退还；不接受全额退；已发密钥不可撤回，close 只关账。
11. **错误码不可漂移**：`retryable` 只在 429 / 5xx / 网络为 `true`；幂等键相关错误永不 5xx；400 与 409 的 `code` 必须能被客户端无歧义区分。

### 8.4 C 级：可观测与防作弊

12. **网关调用计量**：服务端记录 `call_id / 账号 / freeze_id / link_idx / feature / result`，结算以计量为上限；差异落异常台账，不阻断结算。
13. **账单口径固定**：日报按日归档，支持逐笔下钻；金额三方一致（结算表 / 账本流水 / 日报）。

### 8.5 兼容与灰度

- 新增字段可空、新增接口独立，老客户端行为不变；
- 不允许要求客户端在同一版本里同时依赖新旧两套语义；
- 客户端切换顺序：先接 v2 只读（列表 / 日报 / 探活），再切写（冻结 / 结算 / close）。

---

## 9. 待定口径

1. **复刻 B 的「单价 × 倍率」重复计算**。附录 E 写「单价 180 + 倍率 400%」，按其 3.1「倍率作用于单价」的口径，生效价会变成 720/款。必须二选一：单价 45 + 400%，或单价 180 + 100%。**这条要在改代码前纠正**，否则冻结快照一落库就是永久错账。
2. **provider 名对照**：客户端用 `wuyin` / `ark`，服务端示例是 `openai` / `doubao`。需要一张对照表，客户端做映射即可。
3. **客户端切 v2 的时点**：v1 转译层在线期间，新客户端何时从 v1 切到 v2，以及切换失败时的回滚判据（服务端已给出下线判据：7 天无写入 + 影子 0 差异，但没有日期）。
4. **`device_id` 的来源**：本机标识用哪一个（现有机器码 / 首次启动生成并持久化），需要避免重装后变新设备。
5. **列式在日报「单元数」里是否再拆成 4 款**（展示口径，不影响资金）。

---

## 10. 分阶段落地

服务端分 S0–S4，客户端对齐如下：

- **P0（依赖 S0/S1）**：表结构迁移（放宽 `freeze_id`、扩状态、加列）+ 冻结前记账 + 单元 ID 生成 + 重放校验 `status` + 删除保护 + 零计费标记 + 裂变统一 + 12.1 / 12.2 / 12.3 / 12.7；同步改文案：`docs/操作答疑FAQ清单.md` 的「付费重试无论成败都扣」，以及 `docs/价格倍率与积分扣费整理_2026-09-09.md` 的「每款 40–50 随机」。
- **P1（依赖 S1）**：按 `settle_deadline` 收口 + 错误码分流（12.4）+ close 入口。
- **P2（依赖 S2）**：启动对账 + 本地日报 + 与服务端日报对平 + 12.7。
- **P3（依赖 S3）**：计量封顶后的口径切换观察；异常台账接入。

阶段依赖：P0/P1 依赖 8.2 的冻结原子性、重放语义、时间语义；P2 依赖 8.2 第 3、4、6 条；P3 依赖 8.4。任一 A 级契约未落地，对应阶段只能「能跑但对不上账」。

---

## 11. 契约对齐状态（服务端 v1.2）

### 11.1 已对齐

| 项 | 服务端现状 |
|---|---|
| 过期判断 | 新增 `settle_deadline = expires_at + 10min`，宽限内结算合法 |
| 逐单元金额 | 结算回执带 `links[]`，且求和校验 |
| 释放金额 | `released_points` / `retained_points` / `release_reason` 可读 |
| 未结清列表 | `GET /v2/pod/freezes?status=open`，含 `device_id` / `workspace_id` |
| 终态重放 | 409 `freeze_not_active` + `status` |
| 幂等哈希 | 语义字段白名单，排除 `app_version` / `device_id` 等 |
| 裂变 | 降为普通画像，无专有分支 |
| 重试模型 | 每次重试开新冻结，与原实现一致 |
| 价格硬编码 | 占位价已删除，价格只来自价目表且需签字 |
| 定价形态 | 单一固定价 × 整数百分比倍率，无区间无随机；四模块单价已定案 |

### 11.2 仍待服务端输入

| 项 | 影响 |
|---|---|
| 复刻 B 的价目写法（180×400% 还是 45×400% 还是 180×100%） | 写错就是四倍错账，冻结后不可改 |
| provider 名对照表 | 密钥取不到，批次会全部判为无授权 |
| v1 下线时间表 | 决定客户端 v2 切换节奏 |

---

## 12. 本次重构必须处理的客户端缺陷

已定位到代码，不修则账本无法闭环。

1. **`_grant` 在 `keys` 为空时直接抛错**（`remote_billing.py` 的 `_grant`）。服务端明确「密钥发不出来时冻结依然成功」，客户端却当成失败抛出，于是冻结已创建、本地没有记录——附录 A 第 1 条的真因。必须允许空密钥，并把缺密钥交给执行阶段；同时适配 v2 的 `keys` 形状（provider → 数组），现在会把数组 `str()` 成全字面量。
2. **冻结先于记账**（`service.py` 的 `_freeze_action`）。要倒成先写 intent 再冻结，原有「写库失败就全 no_return 结算」保留作快路径。
3. **`mark_billing_settled` 无条件 UPDATE**，多路径并发结算会互相覆盖。改为带前置状态的 CAS。
4. **结算错误码一律被当成可重试**。现在任何异常都 `mark_billing_pending`。要按 `code` 分流：400 → `anomaly`；409 `freeze_not_active` → 探活；409 键冲突 → `anomaly`；404 → `orphaned`；429/5xx/网络 → 重试。
5. **裂变的 `project_fission_settlement` 特例**（反查 `public_url` + 标题完整 + 固化 `fission_billable_styles`）。按 4.2 整体删除。
6. **删除批次裸删账本**（`repository.py` 的 `delete_batch`）。改为不删账本，记 `batch_deleted_at`；有非终态 run 时拒绝删除。
7. **`settlement_grant` / `regrant` 不校验返回状态**。已释放的冻结也会被当作可结算，随后 409。补 `status` 校验，并改用 `settle_deadline`。
8. **计费计划里没有 `unit_id`**，列式还在用 `pod_random_v1`。计划生成时要产 `unit_id` 与 `unit_ids_json`，列式改用 `pod_random_grid_v1`。
9. **请求字段与 v2 不符**：`scope` 仍是数组、feature 仍是 `four_grid`、`rule_version` 仍是整数、幂等键只在 body、`link_idx` 是 1 基。全部要在适配层转换，且转换只做一次、只在一个地方。
10. **没有 `device_id`**，也不随冻结上报，多设备对账无法区分本机与他机。

---

## 附录 A：已确认的四个问题与处理

| # | 问题 | 处理 | 阶段 |
|---|---|---|---|
| 1 | 冻结调用在本地记账之前，回包丢失或密钥缺失时拿不到单号，无法结算，只能等过期 | 先写意图账 + 同键重放 + 允许空密钥（12.1 / 12.2） | P0 |
| 2 | 只要有一次调用停在「进行中」，结算就永远被拒；取消也解不开 | 按 `settle_deadline` 先探活再收口（12.4） | P1 |
| 3 | 删除终态批次时会无条件删掉计费记录，未结清的冻结没人管 | 账本永不随批次删除；有非终态 run 拒绝删除（12.6） | P0 |
| 4 | 裂变按「完整交付」计费，标题失败整款退款；标题可免费补，补完等于白拿图 | 裂变按款冻结/结算，走统一入口 + 画像，取消固化（4.2 / 12.5） | P0 |

---

## 变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-10-23 | v0.1 | 起稿：账本结构、数据流、对账流程、四模块差异、待讨论问题 |
| 2026-10-23 | v0.2 | 增加服务端设计：目标表、接口清单、冻结/结算/收口时序、服务端对账、信任模型、服务端待讨论问题 |
| 2026-10-23 | v0.3 | 结构决策定稿：账本范围收敛到 POD、改现有表而非新建、先写意图再冻结、expected 仅告警、按到期收口、账本永不删、删除拒绝、零计费标记、裂变并入统一口径；服务端章节收敛为合同假设；合并意图账与冻结账 |
| 2026-10-23 | v0.4 | 核对服务端现状并修正：查询统一走 `/batch/{id}`（可读全部模块）、`freeze_id = idempotency_key`、金额是 1 位小数而非 0.1 整数、结算无逐 link 金额、释放不返回退还金额、裂变 title 不单独计价；新增第 12 节客户端缺陷清单，重排第 8 节能力需求与第 9 节待确认项 |
| 2026-10-23 | v0.5 | 纠正「独立通道」表述：四个模块共用一张冻结表与一个幂等命名空间，`/pod-semi`、`/pod-fission` 只是路由入口；补充「写侧能否统一走 `/batch/*` + 画像」的待确认项 |
| 2026-10-23 | v0.6 | 第 8 节从「补能力」升级为「服务端配套改造」，按 A/B/C 分级；补阶段依赖与灰度顺序 |
| 2026-10-23 | v0.7 | 裂变口径定稿：按款冻结/结算、不单独路由，取消「待产品确认」；`/pod-fission/*` 退役为别名 |
| 2026-10-23 | v0.8 | 去掉对现状的迁就：第 8 节改写为「目标契约」，逐 link 金额与释放金额升为 A 级必返；第 11 节降级为差距表；新增 400 不重试、空意图 24h 作废、多设备只处理本地有记录的冻结 |
| 2026-10-23 | v0.9 | 对齐服务端方案 v1.2：改用 v2 接口与 `settle_deadline`、按 `unit_id` 与 0 基 `link_idx` 记账、`scope` 改对象与 `image` 特征名、`rule_version` 改字符串、幂等键走头、主动放弃改走 close、错误按 `code` 分流、列式改 `pod_random_grid_v1`、新增第四层「与服务端日报对平」、第 11 节改为契约对齐状态、第 12 节补 4 条缺陷 |
| 2026-10-23 | v0.10 | 付费重试定案：用普通画像、失败也退款，不做 `charge_policy=attempt`，FAQ 文案随 P0 同步；新增待确认项「价目表是单一价还是区间」（现价为随机 40–50） |
| 2026-10-23 | v0.11 | 对齐服务端 v1.4：定价改为单一固定价 × 整数倍率（全定制/复刻 A/裂变 45、半定制 32/组、列式 180/组、复刻 B 180/款），随机定价取消；新增待纠正项「复刻 B 单价 180 与倍率 400% 重复计算，生效价会变 720」；P0 增加价格说明文档同步 |

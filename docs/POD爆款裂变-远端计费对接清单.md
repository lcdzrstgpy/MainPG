# POD 爆款裂变 · 独立计费通道对接清单

面向：远端计费服务（`workbench.haocoming.top` 系列）的维护者
来源：本地客户端 `local-runtime/wh_local/` 的实际契约
日期：2026-10-16

---

## 1. 背景与现状

本地已上线「POD 爆款裂变」模块。用户点「确认并生成 N 款」时创建请求被计费服务挡下：

```
POST /api/pod-customization/fission/batches   →   400 Bad Request
前端提示：「计费服务拒绝了这次请求，请稍后重试；一直失败就把提示发给我们」
```

无扣款、无批次创建。链路其余环节均正常：同一账号在本地的**分析冻结**（画像 `pod_random_v1`）刚刚成功结算；与能跑通的全定制批次相比，批次冻结报文**唯一差异是 `billing_profile`**。

**结论与决策**：不再把 `pod_fission_v1` 塞进通用 `/batch/*` 通道的白名单，而是**为裂变单开一条独立计费通道**，与现有半定制 `pod-semi` 通道同构。这样裂变可以有独立的定价/倍率与账单归类，且不影响通用通道语义。

---

## 2. 独立通道接口

| 用途 | 方法 | 路径（拟） |
| --- | --- | --- |
| 冻结 | POST | `/api/customer/billing/pod-fission/freeze` |
| 结算 | POST | `/api/customer/billing/pod-fission/settle` |
| 冻结状态 | GET | **沿用通用** `/api/customer/billing/batch/{freeze_id}` |

> **路径按上表约定**。若你们的命名规范不同（例如 `pod_fission` 下划线风格），把最终路径告诉我，我改本地代码对齐即可——客户端路径是硬编码的，必须双方一致。

> **状态查询刻意不另开接口**：本地 `RemotePodBillingCoordinator` 的「继续 / 恢复 / 失败款重试」在冻结和结算两条通道之外，统一用通用的 `GET /batch/{freeze_id}` 读冻结状态（半定制目前也是这样，客户端里那个 `pod-semi/{id}` 方法实际未被调用）。**因此请确保独立通道创建的 freeze，能被通用状态接口查到。**

### 2.1 冻结报文

```json
{
  "idempotency_key": "pod:batch:<batch_id>:initial",
  "link_count": 2,
  "scope": ["title", "four_grid"],
  "billing_profile": "pod_fission_v1"
}
```

与通用通道的报文**结构完全一致**，差别只在路径与该路径下固定按裂变计价：

- **一款 = 一条 link**；`link_count` 即本次生成款数（裂变范围 **1–10**）。
- 每款内部会预留 **2 次** `pod.image` 与 **5 次** `pod.title` 尝试，但这些只是**本地重试额度**：不计费、不上报、不产生额外 link。一条 link 只按 `four_grid` / `title` 两个子项各计一次。
- 冻结时**不发送**图片、提示词等业务内容。
- 不需要半定制那种 `item_count` 字段（裂变没有「组」的概念）。

### 2.2 结算报文

```json
{
  "freeze_id": "<freeze_id>",
  "items": [
    { "link_idx": 1, "subitems": [ { "feature": "four_grid", "status": "success" },
                                   { "feature": "title",     "status": "success" } ] },
    { "link_idx": 2, "subitems": [ { "feature": "four_grid", "status": "no_return" },
                                   { "feature": "title",     "status": "no_return" } ] }
  ]
}
```

- `feature` 只有 `four_grid`（单款四宫格图）与 `title`（标题）；`status` 只有 `success` / `no_return`。
- `items` 条数必须等于冻结时的 `link_count`；每条 link 的子项必须**恰好覆盖**冻结的 `scope`。
- 与通用通道的结算报文结构一致。

### 2.3 重冻结报文（失败款重试 / 暂停后继续 / 会话恢复）

```json
{
  "idempotency_key": "<原 freeze_id>",
  "link_count": 2,
  "scope": ["title", "four_grid"],
  "billing_profile": "<来自冻结状态查询回显的画像>"
}
```

打到**同一个** `/pod-fission/freeze`。注意 `billing_profile` 是从 `GET /batch/{freeze_id}` 读回来的，所以第 4 条要求很关键。

---

## 3. 远端改造清单

### ✅ 必须做

**1) 新增两个接口并接受 `pod_fission_v1`**

- `POST /api/customer/billing/pod-fission/freeze`
- `POST /api/customer/billing/pod-fission/settle`

两个接口都接受 `billing_profile: "pod_fission_v1"`。

**2) 定价：按款，价格与全定制一致**

- 单价 / 倍率策略与 `pod_random_v1`（全定制）**一致**，**一款一条 link**。
- 冻结响应中的 `link_prices` / `frozen_points` 是权威金额，客户端以它为准。
- 若后续想给裂变单独调价，只改这条通道的倍率配置即可，不影响全定制。

**3) 结算子项校验与部分成功语义**

逐 link 校验子项恰好覆盖冻结 scope：

| 场景 | four_grid | title | 计费结果 |
| --- | --- | --- | --- |
| 图与标题都成功 | success | success | 全额扣费 |
| 图成功、标题失败 | success | no_return | 只扣图，标题部分退款 |
| 整款失败 | no_return | no_return | 全额退款 |

**4) 独立通道的 freeze 必须能被通用状态接口查到**

`GET /api/customer/billing/batch/{freeze_id}` 要能返回该 freeze，且 `freeze.billing_profile` 必须是原始值 `pod_fission_v1`。

客户端在「继续 / 恢复 / 失败款重试」时靠它选择重冻结通道；**缺失会退回默认的 `pod_random_v1`，价格口径就串了**。

**5) 冻结响应必须包含以下字段**（客户端强校验，缺任一项即报错中断）

| 字段 | 要求 | 缺失后果 |
| --- | --- | --- |
| `freeze.freeze_id` | 非空 | 报 invalid grant |
| `freeze.keys` | 至少一个 provider 的 key：`[{provider, api_key}]` 或 `{provider: api_key}` | 报「did not grant provider access」 |
| `freeze.expires_at` | 或每个 key 的 `expires_at`（客户端取最早） | 报 invalid grant |
| `freeze.rule_version` | 整数 | 取 0，可能影响后续结算口径 |

**6) 幂等**

同一 `idempotency_key` 重复冻结必须幂等返回**同一个 freeze**。客户端有 `client_request_id` 幂等 + 版本冲突重试 3 次，但服务端也需保证。

**7) 账单分类与展示**

流水 / 管理端能识别 `pod_fission_v1`，展示名 **「POD 爆款裂变」**（与前端映射一致）。

### 🔍 确认即可（预计无需改动）

- `scope: ["title", "four_grid"]` —— 通用通道已在使用这两个 scope；
- `pod.title` / `pod.image` 的既有语义 —— 裂变完全复用，标题调用不额外增加图片款数。

### ❌ 明确不需要做

- 不需要为裂变改造通用 `/batch/freeze`、`/batch/settle` 的白名单；
- 不需要处理分析阶段的计费 —— 裂变 A/B 联合分析走的是 `pod_random_v1` + 2 次 `pod.title`，**当前已能正常冻结与结算**；
- 不需要「组」概念与 `item_count`（那是半定制的 4 款一组）；
- 客户端不会上传图片或提示词。

---

## 4. 画像速查（便于配置）

| 画像 | 归属功能 | 通道 | 计价单位 |
| --- | --- | --- | --- |
| `pod_random_v1` | 全定制、商品处理、**裂变的分析调用** | 通用 `/batch/*` | 按款 |
| `pod_semi_v1` | 半定制 | `/pod-semi/*` | 按组（4 款/组） |
| `pod_replica_v1_a` / `pod_replica_v1_b` | 爆款复刻（普通 / 高清） | 通用 `/batch/*` | 按款 |
| **`pod_fission_v1`** | **爆款裂变（批次生图）** | **新增 `/pod-fission/*`** | **按款，价格同 `pod_random_v1`** |

---

## 5. 本地侧状态：**已完成**（等远端接口上线即生效）

按上表拟定的 `/pod-fission/*` 路径，本地已改好并自测通过：

1. **`wh_local/customer/remote_client.py`** 新增两个方法
   - `freeze_pod_fission_points(remote_token, payload)` → `POST /api/customer/billing/pod-fission/freeze`
   - `settle_pod_fission_points(remote_token, freeze_id, payload)` → `POST /api/customer/billing/pod-fission/settle`
2. **`wh_local/modules/pod_customization/remote_billing.py`**：`freeze()` / `settle()` / `regrant()` 各加一个分流分支——`billing_profile == "pod_fission_v1"` 时走新方法；状态查询仍走通用 `GET /batch/{freeze_id}`。
3. **`tests/test_pod_customization_remote_billing.py`** 新增 `test_fission_batch_uses_its_own_billing_channel_end_to_end`，断言裂变全程（冻结 / 结算 / 重冻结）走 `pod-fission` 通道、载荷含 `billing_profile: pod_fission_v1` 与 `link_count`、每条 link 覆盖 `title`+`four_grid` 两个子项，且**不会落回通用通道**。

回归：POD 后端 706 passed + 3 subtests passed。

**结论：远端两个接口一上线，裂变创建入口即可用；若远端最终路径与上表不同，只需改本地那两行 URL，其余不动。**


---

## 6. 联调验收用例（测试账号）

按顺序逐条核对，每条都要核对**冻结额 / 实扣 / 退款 / 流水标签**四项：

1. **1 款全成功** —— 冻结 1 条 link，结算全额扣费，标签显示「POD 爆款裂变」。
2. **2 款部分失败** —— 让 1 款的图失败，结算应为：成功款全额扣、失败款全额退；前端 `partial` 状态可查看/导出成功款。
3. **失败款单独重试** —— 走重冻结通道，画像必须仍为 `pod_fission_v1`，不得退化成 `pod_random_v1`。
4. **暂停 → 继续** —— 继续时先读冻结状态再重冻结，画像回显正确、金额不变。
5. **重复提交同一 `client_request_id`** —— 幂等返回，不重复冻结、不重复扣费。
6. **多款（如 10 款）** —— 核对 `link_count` 与逐款子项结果一一对应，无因条数不匹配被 400 拒绝。
7. **与全定制并存** —— 同一账号先跑一个全定制批次再跑裂变批次，两边流水与标签互不串。

---

## 7. 改完如何确认（本地侧验证）

1. 工作台 → POD 定制 → 爆款裂变，上传 A/B → 分析素材 → 确认数量 → 点「确认并生成 N 款」；
2. 不再出现「计费服务拒绝了这次请求」；
3. 后端日志出现 `POST /api/pod-customization/fission/batches HTTP/1.1" 200 OK`；
4. 本地库 `local-runtime/outputs/wh-local/workbench.sqlite3` 中 `pod_customization_fission_batches` 出现记录；
5. 个人中心积水流水中出现「POD 爆款裂变」标签的冻结与结算记录。

若仍失败，请提供该次请求的 `idempotency_key`（形如 `pod:batch:<32位hex>:initial`）、`link_count`、`billing_profile` 以及服务端返回的 4xx 原文——客户端出于安全考虑会把上游错误信息替换成固定文案，需要服务端日志里的原始 detail 才能定位。

# POD 智能前置优化层设计（模糊输入 → 结构化业务字段）

状态：**待确认**（2026-09-12）。代码未改动。
目标读者：POD 前端 / 运行时 / 计费链路开发，产品方。
目标基线：当前 `web-frontend` + `local-runtime` 的 `pod_customization` 模块。

---

## 0. 一句话

在现有业务信息卡片顶部新增**单一模糊输入框**：用户只写一句主题/需求，后端调用豆包把这句话转成 10 个结构化业务字段，生成完成后**直接写入对应字段**，用户在此基础上继续人工编辑；生成历史挂进现有草稿，与业务字段**同存同清**。

---

## 1. 背景与目标

现有 `PodCustomizationPage.tsx` 让用户直接面对 10 个业务字段，其中 6 项必填（`product_name / product_category / target_market / design_theme / style_planning / style_keywords`，见 `podCustomizationModel.ts:113-136`）。`主题整批统一风格`、`样式规划`、`元素关键词` 属于专业字段，用户不知道"写什么、写多少、什么格式"，导致填写成本高、内容质量参差。

**目标**

| 编号 | 目标 |
| --- | --- |
| G1 | 允许用户仅输入模糊主题/核心需求/简单描述即可开始 |
| G2 | 豆包 AI 把模糊输入转换为结构化 POD 业务字段 |
| G3 | 自动填充用户难以理解、不知如何填写的专业字段 |
| G4 | AI 结果之上保留完整人工编辑与调整能力 |
| G5 | 清晰引导流程，降低使用门槛 |
| G6 | 提高生成结果的格式合规性与内容质量 |

**非目标**

- ❌ 不生成上架字段（价格/类目/SKU）与规格卡（见 §2 D2）。
- ❌ 不新增后端持久化存储（见 §2 D4）。
- ❌ 不引入多步向导或快捷标签 chips（见 §2 D1）。
- ❌ 不改动生图、标题生成链路。
- ❌ 不触碰旧 `ai_service` POD。

---

## 2. 已确认决策

| 编号 | 决策 |
| --- | --- |
| D1 | 输入形态：**单一模糊输入框**（textarea + 生成按钮），不引入快捷标签或分步向导 |
| D2 | 生成范围：**仅业务字段 10 项**；上架字段与规格卡仍人工填写 |
| D3 | 计费：新增计价项 **`pod.brief`，单价 0**（免费但保留计费/审计机制） |
| D4 | 生成历史：**复用现有草稿生命周期**，不新增存储介质、不新增后端表 |
| D5 | 「样式规划」改为**用户二选一**（全覆盖 / 半覆盖），默认不选、必填拦截；AI **不代填**该项 |

### 2.1 计费通道澄清（基于代码核实）

**已核实事实**

| 事实 | 依据 |
| --- | --- |
| POD 标题调用走 grant 直连：`_required_ark_key(grant)` → provider | `pod_customization/title_runtime.py:258`、`:315` |
| POD 计费特征被限定为两个字面量 | `pod_customization/billing_contract.py:10` |
| POD 不使用产品处理的聊天网关；该网关只放行 `product_processing.text/vision` 并要求已预留 usage | `customer/auth_server.py:1604-1621` |
| "批次之前的一次性调用"已有先例 | `billing_contract.py:91-102`（`PodCallPlan.for_trial`） |

**结论**：前置层走 **POD 原生 grant 通道**，但**不新增 `pod.brief` 特征**——只冻结纯 `title` scope，服务端对该 scope 显式零计费。

- 未新增 `PodFeature` 字面量：`pod_billing.py` 的 `billing_pod_calls` 与迁移 008 的 `pod_customization_billing_outcomes` 都有 `CHECK (feature IN ('pod.title','pod.image'))` 硬约束，新增特征需重建多张表，收益不抵风险。
- 新增 `PodCallPlan.for_brief(brief_id)`（`billing_contract.py`）：只产生 `pod.title` 调用，派生的冻结载荷为 `scope=["title"] / link_count=1`。
- **免费依据**：`freeze_batch_points` 对 POD 画像的 `set(normalized_scope) == {"title"}` 分支直接给出 `link_price_units = [0]`（`billing.py:2024-2027`）→ 冻结 0 积分。
- 冻结 → grant → 直连 provider → 结算机制完全不变，仍保留幂等键、调用计划与审计。

**理由**：既落实 D3 的"免费"，又不放弃计费底座与防重放；同时避免为一次 pre-batch 调用重建计费表。若后续确需独立计价项，再单独做该迁移。

---

## 3. 总体架构与数据流

```
┌─ 单一模糊输入框 (textarea 1..500 字 + "智能生成") ─────────────┐
│  用户只写：主题 / 需求 / 一句话描述                            │
└───────────────┬──────────────────────────────────────────────┘
                │ ① 前端预处理：trim → 空白归一 → 长度校验
                ▼
  POST /api/pod-customization/brief/fields   (新增, 权限 pod_customization.create)
  ② 后端：鉴权 → 0 价冻结 pod.brief → grant → 豆包 json_schema 严格输出
     → 对齐 BusinessFields(extra=forbid) → 逐字段校验/清洗 → settle
                │
                ▼
  ③ 前端把结果直接写入 businessFields（保留"高级：本批次创意编辑"）
                │
                ▼
  ④ 用户继续人工编辑与调整
                │
                ▼
  ⑤ 生成历史写入草稿（生命周期 = 业务字段，同存同清）
```

---

## 4. 输入界面设计

- **位置**：`pod-business-editor`（业务信息卡片）顶部新增可折叠"智能填写"区。
- **元素**：`textarea`（**不设占位符文本**，保持输入框干净）、字数计数（`n/500`）、`智能生成` 按钮。
- **输入引导（2026-09-12 补充）**：引导只放在折叠栏副标题与输入框下方提示里，明确要求**「产品名 + 风格」**格式，并提示可再补目标人群/卖点。
  - 原因：用户只写主题（如"圣诞节"）时，模型无从得知产品品类，会自行编造（实测把绗缝旅行包猜成了钥匙扣），导致 `product_name` 错误。
  - 折叠栏副标题："写「产品名 + 风格」，AI 自动填好下方业务字段"；输入框下方提示："请务必带上产品名，格式：产品名 + 风格（可再补目标人群、卖点）"。
  - 曾用占位符承载示例，后按用户要求移除（框内文字易被误认为已填内容）。
  - **删除「改为手动填写」按钮（2026-09-12 修订）**：该按钮原先只做"收起面板"，与面板顶部折叠开关重复；且请求进行中点击并不能中止生成，事后仍会回填字段，与按钮文案的承诺不符，属误导。故移除；降级出口由"下方字段始终可编辑 + 顶部折叠开关 + 失败时重试"承担。
- **生成中**：按钮 loading、输入框禁用，阶段化提示"正在理解需求… / 正在组织字段…"。
- **生成完成**：**直接写入对应业务字段**，用户可继续在表单里人工修改。
- **校验**：空输入或超长即禁用按钮并给出即时提示。
- **降级**：AI 失败时提供「重试」，绝不阻塞创建入口；面板顶部折叠开关可随时收起本区（见下方 2026-09-12 修订）。

---

## 5. 后端接口契约

`POST /api/pod-customization/brief/fields`，权限 `pod_customization.create`（与创建批次一致，`router.py:116-117`）。

**请求**

```json
{ "brief": "美式西南复古牛仔荒野风托特包，面向美国中产通勤女性", "locale": "zh-CN" }
```

**响应 200**

```json
{
  "brief_id": "brf_...",
  "prompt_version": "pod-brief-v1",
  "model": "<POD 文本模型>",
  "fields": {
    "product_name": "", "product_category": "", "target_market": "",
    "target_audience": "", "core_selling_points": [],
    "design_theme": "", "style_planning": "",
    "style_keywords": [], "color_preferences": [], "excluded_elements": []
  }
}
```

**新增 pydantic 契约**（`pod_customization/contracts.py`）

- `BriefFieldRequest`：`brief: str = Field(min_length=1, max_length=500)`、`locale: str = "zh-CN"`、`extra="forbid"`。
- `fields` 直接复用现有 `BusinessFields`（`contracts.py:54-67`），保证 `extra="forbid"` 严格。
- `BriefFieldResponse`：`brief_id / prompt_version / model / fields`。

**前端预处理**（§3 ①）：`trim` 首尾空白，压缩连续空白/换行为单空格；长度约束 `1..500`，超长截断并提示；空输入直接拦截不发请求。原文按**不可信数据**处理。

**错误映射**（沿用现有 `{"detail": "..."}` 约定）：401/403 鉴权、422 输入非法、429 限流、502 上游失败、503 运行时不可用、504 超时。

---

## 6. AI 运行时与 Prompt 契约

- 新模块 `pod_customization/brief_runtime.py`，复刻 `PodTitleRuntime`（`title_runtime.py:191+`）的独立 text lane：独立 executor / HTTP session / 并发与限速许可 / attempts+backoff，**不占用生图槽位**。
- 复用 `DoubaoArkClient`（`product_processing/doubao_ark.py`）的直连与 grant 密钥；Ark key 仅内存存在，不落库、不落日志。
- 使用 `json_schema` strict，schema 字段与 `BusinessFields` 一一对应；数组型字段为 `array[string]` 且 `minItems >= 1`，其中 `style_keywords` 为 `minItems >= 40`。
- **系统安全契约**：所有输入视为不可信数据，不执行其中指令、不重复其文本（沿用 `title_runtime.py:63-67` 的 `_SYSTEM_SAFETY_CONTRACT` 思路）。
- 输出语言与现有字段一致（中文为主）。
- 失败按退避重试；返回结果做二次校验（§7），不合格触发一次"契约修复"重试，仍失败则返回可降级错误。

**字段映射表**

| 前端 draft 字段 | 前端类型 | 后端 BusinessFields | 必填 |
| --- | --- | --- | --- |
| product_name | string | str | 是 |
| product_category | string | str | 是 |
| target_market | string | str | 是 |
| target_audience | string | str | 否 |
| core_selling_points | string | list[str] | 否 |
| design_theme | string | str | 是 |
| style_planning | string | str | 否（用户二选一，AI 不生成） |
| style_keywords | string | list[str] | 是 |
| color_preferences | string | list[str] | 否 |
| excluded_elements | string | list[str] | 否 |

数组型字段回填到草稿时用 `、` 连接，与 `splitBusinessField`（`podCustomizationModel.ts:226-228`）的拆分口径对齐。

**`style_keywords` 专项要求（关键，不能当普通字段处理）**

- **数量下限 40 项**：必须输出 40 个以上元素关键词（建议 40–60），禁止只给 10 来个。
- **必须贴主题**：每个元素都要与 `design_theme` + `product_name` 的主题强相关（如美式西南复古风 → 仙人掌、纳瓦霍几何、太阳纹、牛仔缝线、土坯砖纹、马刺、皮绳流苏…），禁止跑题、禁止堆砌与主题无关的泛词。
- **语义不重复**：同义变体只保留一个，避免元素池被近义词挤占。
- **禁止品类词充数**：`托特包`、`手提包` 这类产品本体词不算元素，不能用来凑数。
- **必须是具体可绘制的事物**（2026-09-12 补充）：形容词/风格词（大胆、俏皮、美式乡村风）、配色属性（高饱和度配色、高对比色彩、撞色设计）、表现手法（波普色块拼接、线条、质感）、场景与人群类别（海滩元素、健身场景元素、休闲度假元素）**一律不算元素**；配色只写入 `color_preferences`。
  - 服务端用高信号词做**确定性过滤**（`brief_runtime.is_concrete_style_element`：命中「元素/符号/质感/配色/色彩/色调/设计/线条/拼接/图案/场景/氛围/风格/气质/饱和度/撞色/高对比」或以「风/感」结尾即判非元素）；
  - 过滤后若不足 40 项，则把被剔除的条目作为修复反馈回传模型，触发一次契约修复重试；若已 ≥40 项则直接剔除保留，不额外重试。

理由：系统按款式随机分配主打/辅主/点缀（前端提示见 `PodCustomizationPage.tsx:123-128`），元素池越大、语义越分散，跨款图案差异化越明显；这也是 §12 中"每款必须不同图案"能否成立的前提。

> 前端提示文案已同步（`PodCustomizationPage.tsx` 的「元素关键词」ⓘ）：说明每项须为具体事物、不要形容词/风格词/配色/「xx元素」，并改为建议 40 种以上。

**`color_preferences` / `excluded_elements` 专项要求（2026-09-12 补充）**

两个字段都要求"尽量多"：

- **`color_preferences` 下限 10 个**（建议 10–14）：覆盖主色/辅色/点缀色/背景色；必须是具体颜色名，禁止「高饱和度」「撞色」这类抽象词。
  - 理由：每款的强调色从配色表按款式轮换抽取（`prompts.py:_brief_color_preferences`），颜色越多跨款差异越明显。
- **`excluded_elements` 下限 10 项**（建议 12–20），且必须显式覆盖四类封号高危项：
  - ① 侵权类：品牌 logo、商标标识、球队或联盟标识、影视动漫游戏角色、卡通 IP 形象、名人肖像或签名、奢侈品牌老花图案、平台水印或标识、受版权保护的海报封面；
  - ② 危险违禁类：武器弹药、管制刀具、爆炸物、毒品或吸毒工具、赌博筹码或老虎机、烟草或电子烟、酒精饮品；
  - ③ 违法与敏感类：钞票或货币图样、身份证件样式、暴力血腥画面、恐怖或仇恨符号、纳粹标志、宗教敏感符号、政治标志或政党标识、国旗或国徽、成人或色情内容、裸露人体、虐待动物画面；
  - ④ 其他封号高危类：二维码或条形码、真人照片或可识别个人信息。
- **确定性安全清单**：服务端内置 30 项 `_SAFETY_EXCLUDED_BASELINE`，**无论模型是否输出都会补齐**（大小写不敏感去重），不依赖模型自觉；结果在表单里可见、可编辑。
  - 实测：模型给 12 项主题相关禁用项 → 合并后 42 项，安全清单 30 项全部在位且无重复。
- 两个下限在 `json_schema`（`minItems`）与服务端二次校验双重把关；不足则触发契约修复重试。

---

## 7. 校验、回填与人工编辑

**服务端二次校验**（拒绝并触发一次修复重试）

- 字段长度上限；必填字段非空；
- 命中禁用词表（复用 `title_runtime._PROHIBITED_TERMS` 的品牌/极限/医疗/儿童类词）即判不合格；
- 数组字段去空、去重；`style_keywords` 少于 40 项、或与主题明显无关、或含品类词充数时判不合格，触发一次契约修复重试。

**前端回填**

- 生成成功后把 9 个字段**直接写入** `setBusinessFields`，覆盖同名字段的当前值；数组型字段用 `、` 连接（`join("、")`）；
- **不含「样式规划」**：该项由用户在页面上二选一，AI 结果不回填、不覆盖用户已选值（`PodBriefFieldsDraft` 显式排除该键）；
- 不做逐字段采用/忽略，不弹预览面板；写入后用户仍可像往常一样在表单里人工修改；
- 若"本批次创意编辑"已是自定义，回填后沿用既有 `creativePromptSyncStatus`（`podCustomizationModel.ts:213-225`）标记为 `custom-stale`，提示业务字段已更新，不静默覆盖用户已写的创意文本。

---

## 8. 加载与错误处理

- 阶段化 loading 文案，配合超时阈值；
- 失败：toast + 「重试」；用户可直接编辑下方业务字段，或用面板顶部的折叠开关收起本区；
- 防抖 + `brief_id` 幂等，避免重复点击重复计费（0 价也保留幂等）。

---

## 9. 生成历史

- **不提升草稿版本号**：沿用本仓库对"后续新增草稿键"的既有做法（`spec_card` / `style_planning` 同样如此），`POD_CUSTOMIZATION_DRAFT_VERSION` 保持 `3`，新增 `brief_history: BriefHistoryItem[]`（`podCustomizationDraft.ts`）。
- `BriefHistoryItem = { id, input, fields, created_at }`。
- 上限 20 条（LRU），相同 `input + fields` 去重。
- **生命周期与业务字段完全一致**（D4）：
  - 持久化：同一 localStorage key（account + workspace）；
  - 清除：切换模板时随 `setBusinessFields(EMPTY)` 一并清空（`PodCustomizationPage.tsx:441-457`）；
  - 损坏：随整份草稿一起清除。
- **迁移**：不升版本；缺失 `brief_history` 视为 `[]`，不丢弃旧草稿（参照现有 `style_planning` / `spec_card` 的归一化写法）。
- **UI**：输入区下方"最近生成"列表，点击条目直接把该次字段写入表单。

---

## 10. 计费

- **不新增定价项**：复用 `pod.title`。前置层冻结载荷为 `scope=["title"] / link_count=1`，服务端对 POD 画像的纯 title scope 零计费（`billing.py:2024-2027`）。
- `billing_contract.py` 新增 `BRIEF_ATTEMPTS` 与 `PodCallPlan.for_brief(brief_id)`；`PodFeature` 字面量保持不变。
- 系统管理页**无需改动**（`POD_FEATURE_KEYS` 不变，不新增 `pod.brief`）。
- 失败/unreturn 退款逻辑复用现有分支，不新增特例。

---

## 11. 测试计划

**前端**

- `podCustomizationDraft` 缺失 `brief_history` 时归一化为 `[]` 且不丢弃旧草稿、历史去重/上限、清空节点；
- 输入预处理与长度边界；
- 直接写入：10 个字段全部落到表单、数组 join 正确；
- 组件：loading / 错误降级。

**后端**

- `BriefFieldRequest` / `BriefFieldResponse` 严格性；
- brief runtime 的 JSON 解析、契约修复重试、禁用词校验；
- `style_keywords` 的 40+ 数量下限与主题相关性校验（不足/跑题时触发修复重试）；
- 端点鉴权（`pod_customization.create`）、422/502/503/504 映射；
- `PodCallPlan.for_brief` 与 0 价冻结/结算；
- 运行时隔离（不占用生图并发槽位）。

---

## 12. 风险与边界

| 风险 | 缓解 |
| --- | --- |
| AI 输出质量不稳定 | 仅覆盖业务字段；二次校验 + 前端必填拦截兜底；失败可手动填写 |
| 安全（提示注入/密钥泄漏） | 输入按不可信数据处理；不持久化任何密钥；Ark key 仅内存 |
| 老草稿兼容 | v3→v4 迁移，缺失键归一化为空，零丢失 |
| 性能抢占生图并发 | 独立 text lane，单次调用，不与生图共享线程池/许可 |

---

## 13. 交付文件清单

**新增**

- `web-frontend/src/modules/pod_customization/components/PodBriefInput.tsx`
- `web-frontend/src/modules/pod_customization/data/podBrief.ts`（纯函数：输入归一、数组→「、」、直接覆盖合并、历史去重/上限）
- `web-frontend/src/modules/pod_customization/data/podBrief.test.ts`
- `local-runtime/wh_local/modules/pod_customization/brief_runtime.py`
- `local-runtime/wh_local/modules/pod_customization/tests/test_brief_runtime.py`

**修改**

- `web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.tsx`
- `web-frontend/src/modules/pod_customization/data/podCustomizationDraft.ts`（新增 `brief_history`，保持版本号 3）
- `web-frontend/src/modules/pod_customization/data/podCustomizationDraft.test.ts`
- `web-frontend/src/modules/pod_customization/api/podCustomizationApi.ts`（新端点）
- `web-frontend/src/modules/pod_customization/types/index.ts`
- `web-frontend/src/modules/pod_customization/styles/podCustomization.css`
- `local-runtime/wh_local/modules/pod_customization/contracts.py`
- `local-runtime/wh_local/modules/pod_customization/router.py`
- `local-runtime/wh_local/modules/pod_customization/billing_contract.py`
- `local-runtime/wh_local/modules/pod_customization/service.py`
- `local-runtime/wh_local/app/main.py`（装配 `PodBriefRuntime`）

**无需改动**：系统管理页与定价种子（复用 `pod.title`，不新增计价项）。

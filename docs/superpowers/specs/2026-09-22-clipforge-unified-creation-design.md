# ClipForge 统一创作入口迁移设计

> 状态：已确认设计，待编写实施计划  
> 日期：2026-09-22  
> 适用范围：MainPG 内置 `integrations/clipforge` AI 视频模块

## 1. 目标

将当前的“视频工作台”与“新建带货项目”合并为一个创作入口：保留工作台作为 MainPG 的模块落地页，但所有项目创建均以新建项目页的完整配置为准。

迁移完成后，同一项目从创建到导出必须满足：

1. 用户能够在创建前表达商品、受众、平台、人物/处境、场景、语言/语气、脚本风格、画面约束、时长、模板与角色。
2. 用户必须看见并选择本次的出片策略，系统不得将“AI 成片”静默降级成静态图片拼接。
3. 同一份项目级创作信息同时驱动脚本、图片、视频、配音与质量检查。
4. 已有项目、历史成片、运行中的旧流水线和旧深链接可以继续读取和恢复。
5. 用户界面、默认引导和设置只面向火山/豆包与速创；不再出现 Atlas Cloud 的一键接入和推荐路径。

本次不重写模型适配器、FFmpeg 合成器或存量项目数据；重点是统一入口、数据合同和编排语义。

## 2. 已核实的现状

### 2.1 两个创建入口实际上是两套业务逻辑

| 维度 | 工作台 `/start` | 新建项目 `/project/new` |
|---|---|---|
| 默认出片模式 | `free`（免费快剪） | 仅生成脚本，后续由用户决定 |
| 默认时长 | 固定 30 秒 | 15 / 30 / 60 秒 |
| 风格控制 | 自动、主播口播、剧情、图文混剪四个预设 | 完整脚本风格、广告模板、脚本模板 |
| 受众/平台/价格带 | 不支持 | 支持 |
| 创作要求 | 无人物处境、场景、语言/语气字段 | 部分由模板间接支持，仍未形成项目级合同 |
| 自动执行 | `?auto=1` 自动启动免费流水线 | 无自动合成 |
| 请求体 | 简化、固定 `other` 品类 | 完整请求体，包含受众、平台、优势、模板等 |

相关文件：

- `integrations/clipforge/src/app/start/page.tsx`
- `integrations/clipforge/src/app/project/new/page.tsx`
- `integrations/clipforge/src/app/api/llm/script/route.ts`

### 2.2 自动流水线不是生视频链路

工作台默认路径会启动 `judge → stock_fill → compose`。它只匹配素材，再通过 FFmpeg 进行合成；没有逐镜 `image-to-video` 或整片原生视频任务阶段。

相关文件：

- `integrations/clipforge/src/lib/pipeline-stages.ts`
- `integrations/clipforge/src/lib/pipeline-runner.ts`
- `integrations/clipforge/src/app/api/project/[id]/compose/route.ts`

### 2.3 项目级控制已存在，但未参与创建

数据库已有 `creativeIntent`、`visualBible` 和 `productionWorkflow` 字段，且制作控制台已经能读写它们；但创建项目 API 与主创建表单没有初始化这些字段。它们因此成为素材阶段之后才发现的“第二份创作设置”。

相关文件：

- `integrations/clipforge/src/lib/db/schema.ts`
- `integrations/clipforge/src/lib/production-system.ts`
- `integrations/clipforge/src/app/api/project/[id]/production/route.ts`
- `integrations/clipforge/src/app/project/[id]/production/page.tsx`

## 3. 方案比较与取舍

### 方案 A：工作台直接跳转至新建项目页

优点：改动少，能够快速消灭一个表单。

缺点：MainPG 的 AI 视频入口失去首页上下文、商品链接/话题切换、最近项目和趋势入口；且“新建项目”仍要承担首页与高级页两种页面职责。该方案不采用。

### 方案 B：抽取共享创作简报，工作台承载完整表单

工作台保留为唯一主 URL；将新建项目页的完整字段、校验、上传、链接导入和脚本请求抽为共享组件。工作台提供完整表单及折叠式分区，项目页、商品库、爆款复刻等入口只负责将数据预填到这份简报。

优点：最大化保留定制能力，同时只有一份状态、默认值和请求体；符合 MainPG “AI 视频是一个模块”的定位。

缺点：需要拆分现有大页面，并新增兼容适配层。**本设计采用该方案。**

### 方案 C：重新开发第三套向导式创作页

优点：视觉和交互最自由。

缺点：短期会产生第三套字段和第三条流程，正好重复当前问题。该方案不采用。

## 4. 目标信息架构

```text
MainPG
└─ AI 视频
   └─ 视频工作台 /start（唯一项目创建入口）
      ├─ 来源：上传商品图 / 商品链接 / 一句话主题 / 商品库预填 / 爆款复刻预填
      ├─ 创作简报
      │  ├─ 商品：名称、品类、卖点、价格、素材
      │  ├─ 投放：受众、平台、时长、使用利益点
      │  ├─ 叙事：脚本风格、人物与处境、场景、动作、语言、语气
      │  ├─ 视觉：角色、模板、画面一致性、商品约束、禁忌、镜头偏好
      │  └─ 输出：出片策略、视频模型、音频策略、质检策略
      └─ 创建项目并生成脚本

项目详情
├─ 脚本：查看/编辑/按创作简报重生
├─ 素材与动态：逐镜画面、I2V、镜头控制
├─ 制作设置：视觉圣经、模型路由、质量与修复
└─ 成片与导出：按本次策略显示主版本与变体
```

`/project/new` 在兼容期内继续可访问，但渲染同一个共享组件；稳定后改为重定向到 `/start?entry=advanced`。不再保留第二套创建状态和第二套请求体。

## 5. 统一数据合同

### 5.1 新增 `CreationBrief`

新增一个项目级 JSON 字段 `creationBrief`，只保存当前表中没有、但需要跨阶段复现的创作决策。商品基本信息继续使用既有 `projects` 字段，避免重复存储。

```ts
type InputMode = "upload" | "link" | "topic" | "product-library" | "clone";
type OutputStrategy = "draft" | "controlled-motion" | "native-film";
type AudioStrategy = "volcengine-tts" | "native-audio" | "mute";
type StyleSource = "explicit" | "template" | "performance-recommendation";

interface CreationBrief {
  version: 1;
  inputMode: InputMode;
  targetDuration: 15 | 30 | 60;
  styleType: string;
  styleSource: StyleSource;
  targetAudience: string[];
  platforms: string[];
  priceRange?: string;
  usageAdvantage?: string;
  narrative?: {
    situation?: string;
    language?: string;
    tone?: string;
  };
  outputStrategy: OutputStrategy;
  audioStrategy: AudioStrategy;
  templateId?: string;
  characterId?: string;
}
```

项目已有的 `creativeIntent` 与 `visualBible` 不复制到 `CreationBrief`：

- `creativeIntent` 保存人物、动作、场景、灯光、画面构图、镜头、动态、连续性和负向约束；
- `visualBible` 保存人物/商品/服饰/环境/灯光锚点和禁止变化；
- `productionWorkflow` 保存实际启用的阶段与付费/免费属性。

创建时这三份数据同时初始化，后续任意页只能经由同一 API 更新，确保脚本、素材和合成读到的为同一版本。

### 5.2 策略与工作流的确定映射

| 出片策略 | `productionWorkflow` | 允许素材 | 音频默认值 | 主成片 |
|---|---|---|---|---|
| `draft` 免费草稿 | `motion=false`、`voice=true` | 商品图/授权素材 | Edge 免费音色或静音 | FFmpeg 常规合成 |
| `controlled-motion` 导演可控动态 | `keyframes=true`、`motion=true`、`voice=true` | 每镜图片 + I2V 视频片段 | 火山语音 V3 | 逐镜动态合成 |
| `native-film` 原生整片 | `motion=true`、`voice=false`、`compose=true` | 分镜网格/参考图 | 模型原生音频 | `storyboard-film` 原生整片 |

无论哪种策略，都必须在创建后的脚本确认页展示：模型、预估调用次数、音频来源、是否使用静态素材。`draft` 不得以“AI 生视频”或“AI 正在出片”命名。

### 5.3 明确化“智能推荐”

`styleType="auto"` 不再在无数据时自动归一化为 `pain_point`。

- 若历史数据达到推荐阈值，接口返回候选风格、依据和 `styleSource="performance-recommendation"`；用户确认后才生成。
- 若无足够数据，接口返回“无法推荐”，前端要求用户显式选择一个风格。
- 模板覆盖风格时记录 `styleSource="template"`；手选时记录 `explicit`。

## 6. 页面与组件边界

### 6.1 新组件结构

```text
src/components/project-creation/
├─ creation-brief-form.tsx       # 唯一表单状态、校验和创建回调
├─ creation-brief-types.ts       # CreationBrief、表单 DTO、策略枚举
├─ creation-brief-defaults.ts    # 无隐式降级的默认值和选项
├─ input-source-panel.tsx        # 上传/链接/主题/预填来源
├─ narrative-panel.tsx           # 风格、人物处境、语言、语气
├─ visual-control-panel.tsx      # 场景、视觉圣经、模板、角色
├─ output-strategy-panel.tsx     # 三种出片策略、成本与音频说明
└─ creation-brief-summary.tsx    # 脚本与项目详情中使用的只读摘要
```

该目录只解决“创建时收集与展示创作简报”，不直接调用具体模型、不直接执行 FFmpeg。

### 6.2 页面职责

| 页面 | 迁移后职责 |
|---|---|
| `/start` | MainPG 工作台主入口；渲染完整 `CreationBriefForm`，并保留最近项目/趋势作为次级内容 |
| `/project/new` | 兼容旧链接；同样渲染 `CreationBriefForm`，稳定后 308/客户端替换为 `/start?entry=advanced` |
| `/project/[id]/script` | 显示简报摘要；允许修改并重新生成脚本；不再因 `?auto=1` 启动默认快剪 |
| `/project/[id]/assets` | 根据已选 `outputStrategy` 引导素材生成；受控动态才显示逐镜 I2V 主操作 |
| `/project/[id]/production` | 仍保留为高级生产设置，但编辑的是同一份项目级约束，不再是创建后孤立配置 |
| `/project/[id]/video` | 读取策略和音频来源，显示实际 TTS/原生音频/静音状态及逐镜降级情况 |
| `/project/[id]/export` | 将“原生整片、受控动态成片、免费草稿”按策略分组，默认选择本项目的主版本 |

## 7. API 与持久化设计

### 7.1 数据库迁移

新增 drizzle 迁移，向 `projects` 添加：

```sql
ALTER TABLE `projects` ADD `creation_brief` text;
```

在 `schema.ts` 中映射为 JSON 类型。旧项目字段为 `null`，读接口将其归一化为兼容的只读摘要；不得批量重写既有项目。

### 7.2 新建项目 API

`POST /api/project` 接收并校验：

```ts
{
  name: string;
  productName?: string;
  productCategory?: string;
  productDescription?: string;
  productImages?: string[];
  videoMode: "product_closeup" | "graphic_montage" | "scene_demo" | "live_presenter";
  characterId?: string;
  creativeIntent: CreativeIntent;
  visualBible: VisualBible;
  productionWorkflow: WorkflowStagePlan[];
  creationBrief: CreationBrief;
}
```

服务端必须依据 `outputStrategy` 校验 workflow：

- `controlled-motion` 必须启用 `motion`，且验证至少一个视频模型可用；
- `native-film` 必须验证所选模型支持整片/参考视频能力；
- `volcengine-tts` 必须验证火山语音配置完整，但仅在正式提交前做连接测试；
- `draft` 允许无付费视频模型，但界面必须明确“静态草稿”。

### 7.3 脚本 API

`POST /api/llm/script` 从项目读取 `creationBrief`、`creativeIntent` 与 `visualBible`，而不是相信多个页面各自拼出的不一致字段。前端仅提交本次需要覆盖的明确字段。

脚本提示词将新增统一的“叙事要求”和“视觉约束”段落；`customRequirements` 仅作为模板补充，不再是唯一承载创作要求的隐式通道。

### 7.4 自动流水线 API

保留现有 `/api/project/[id]/pipeline`，但仅接受 `creationBrief.outputStrategy="draft"` 的新项目自动启动。

- 旧 `?auto=1` 项目保留原行为，以便断点恢复；
- 新项目不得通过 URL 参数隐式启动；
- `controlled-motion` 创建脚本后进入明确的“生成动态镜头”确认页；
- `native-film` 创建脚本后进入整片预览与计费确认页。

## 8. 兼容与迁移策略

### 阶段 0：建立观测基线

在改入口前，为创建来源、实际选择的策略、脚本风格来源、提交视频任务数、音频来源和最终 composition ID 写入项目事件/日志。没有这层观测，无法判断迁移后是否仍有静默降级。

### 阶段 1：只抽组件，不改变用户行为

从 `project/new/page.tsx` 抽取完整表单及创建请求构建器。`/project/new` 原路由继续使用它，自动化测试确认请求体与改造前等价。

### 阶段 2：工作台改接共享表单

`/start` 改为使用共享表单；商品图、链接和话题转换为 `InputMode`，不再使用独立的 `FORM_PRESETS`、`genMode`、`?auto=1` 或 Atlas 一键接入。

在这个阶段只允许用户显式选择 `draft`、`controlled-motion` 或 `native-film`，并将选择持久化。

### 阶段 3：让项目详情消费简报

脚本、素材、制作控制台、视频和导出页增加 `CreationBriefSummary`。其中脚本重生与素材生成必须从持久化数据取值；用户编辑简报后要生成新的脚本版本，而不覆盖旧版本。

### 阶段 4：收口默认值与降级

- 移除 `auto → pain_point` 的无数据回退；
- 仅将免费流水线用于显式 `draft` 策略；
- 合成时记录每镜实际音源和 TTS 失败；默认不允许安静地跳过语音；
- 在导出页隐藏不属于项目主策略的普通合成版本，放入“其他版本”。

### 阶段 5：迁移次级入口

商品库、主播库、爆款复刻、媒体解构、话题成片和批量出片只输出预填 `CreationBrief` 或批量变量，而不是创建独立语义的项目。批量出片复用同一策略与项目执行器。

### 阶段 6：清理 Atlas 用户面

删除工作台和设置页的 Atlas 一键接入、默认推荐、引导链接、文案与相关测试。底层 Provider 是否继续保留作为非公开兼容能力不属于本设计；无论是否保留，UI 与默认配置不得再引用它。

## 9. 错误处理与可解释性

1. 创建前验证缺失的 LLM、视频模型或火山语音配置，并定位到具体策略/字段；不得将错误延后到合成阶段。
2. 所有付费任务在提交前显示模型、调用数、时长/成本预估，并明确需要确认。
3. 合成记录必须保存：策略、视频素材类型（图片/视频）、模型、音频来源、逐镜 TTS 结果和降级原因。
4. 音频失败默认标记任务“待处理”；只有用户选择“允许免费音色回退”时才能继续导出。
5. 旧流水线若在服务重启后中断，继续使用既有 `pipeline_runs` 断点恢复逻辑；不能将其转换为新策略任务。

## 10. 验收标准

### 入口与数据

- 从 `/start` 和 `/project/new` 创建同一份商品简报时，发出的创建 DTO 和脚本 DTO 相同。
- 新项目记录中同时存在 `creationBrief`、`creativeIntent`、`visualBible`、`productionWorkflow`。
- 商品链接、商品库、主播库和爆款复刻能预填统一表单，用户可在提交前查看和修改所有字段。

### 策略与成片

- `draft` 项目只出现“免费草稿”主版本，并说明静态素材来源。
- `controlled-motion` 项目每一个入选镜头均有视频任务或明确的人工上传视频；若不满足，不能开始主合成。
- `native-film` 项目只在模型和计费确认通过后提交整片任务。
- 导出页默认展示项目策略对应的主成片，而不是按创建时间盲选普通合成记录。

### 音频与风格

- 选择火山语音后，成片详情可看到每个镜头实际使用的音色/音频任务；失败不再静默。
- “智能推荐”无足够历史数据时不会创建痛点种草脚本，且用户必须显式选择风格。
- 项目详情任意页可查看本次风格、来源、受众、平台、人物/场景、视频策略和音频策略。

### 兼容

- 旧项目能打开、导出和恢复现有流水线；不要求补填新字段。
- 旧 `/project/new` 深链接仍能访问统一表单。
- 主项目启动、ClipForge sidecar 启动和已有数据库自动迁移均不失败。

## 11. 非目标

- 不迁移或重写历史成片文件；
- 不修改豆包、速创、火山语音的底层 HTTP 协议；
- 不在本次把爆款复刻/媒体解构的算法改为另一种算法；
- 不让“免费草稿”承担动态生视频承诺。


# AI 视频统一出片方案设计

> 状态：已确认设计，待用户审阅文档后编写实施计划  
> 日期：2026-09-23  
> 范围：`integrations/clipforge` 的 AI 视频创建、脚本、素材与整片生成流程

## 1. 目标

将当前重叠的“智能生产方案”和“出片策略”合并为一次、明确且项目级的 **出片方案** 选择。用户在创建项目时选一次方案；项目随后从脚本到成片只沿该方案对应的技术链路执行，不在脚本页出现第二次竞争选择。

默认选择 **原生整片**。火山语音 TTS 保留为可用能力，但原生整片始终采用视频模型原生音频，不发起 TTS 请求。

## 2. 已核实的现状与问题

现有代码把两个不同层级的决策拆开：

- `ProductionProfilePicker` 写入全局 Zustand 设置（`activeProductionProfile`、分辨率、逐镜时长、运镜、接缝、Look）；它不写项目，也不直接决定业务工作流。
- `CreationBrief.outputStrategy` 决定 `draft`、`controlled-motion`、`native-film` 三条工作流，但默认是 `draft`，并要求用户显式再点一次。
- `/project/[id]/script` 同时显示“策略说明/预览确认”和“免费快剪/AI 生成成片”两个入口；原生整片、导演动态、免费草稿因此在创建后仍需重新理解与选择。
- `script/page.tsx` 与 `assets/page.tsx` 在生成时读取全局 `imageParams`、`videoParams`、`motionIntensity`、`motionRealism`、`chainMode`、`visualLook`。修改今日的全局方案会改变旧项目后续生成结果。

这造成术语重复、链路重复决策，以及项目结果不稳定。

## 3. 方案比较与取舍

### A. 仅移动“智能生产方案”组件

优点：改动最少。

缺点：继续依赖全局设置，脚本页仍有第二次策略选择，不能解决核心问题。不采用。

### B. 保留两层选择，只把方案 ID 保存到项目

优点：比 A 稳定，改动中等。

缺点：用户仍需先选“方案”再选“策略”；之后修改同 ID 的预设会影响历史项目，不是快照。不采用。

### C. 统一出片方案，并保存完整项目快照

一个用户可理解的方案同时决定：技术链路、质量/成本档、逐镜参数、音频来源和工作流。创建时写入项目；所有执行页优先读取项目快照，全局设置仅服务旧项目兼容与高级设置。

优点：决策一次、链路确定、历史项目稳定。缺点：需替换现有全局 picker 依赖并补回归测试。**本设计采用。**

## 4. 创建表单信息架构

`CreationBriefForm` 是唯一的创建状态与请求体构造器，严格按以下顺序展示：

1. 图片来源
2. 商品信息：名称、卖点、品类
3. 脚本要求：风格、人物/处境、语言、语气
4. 投放与画面约束：受众、平台、时长、价格带、用法优势、画面形态、角色、场景、动作、镜头、灯光、色调、商品约束与禁忌
5. 出片方案
6. 音频策略（仅当所选方案允许覆盖时展示；原生整片显示固定“模型原生音频”）
7. 开始生成

原独立于 `/start` 表单外侧的 `ProductionProfilePicker` 移除；不会再写入全局 store。

## 5. 用户可选的五种出片方案

| 方案 ID | 用户文案 | 实际链路 | 音频 | 预设快照 |
|---|---|---|---|---|
| `draft` | 免费草稿 | LLM → 商品图/静态素材 → 可选 TTS → FFmpeg | 火山 TTS 或静音，由用户选择 | 不调用 I2V；静态合成 |
| `controlled-rapid` | 导演动态·极速试片 | LLM → 逐镜关键帧 → 逐镜 I2V → 本地合成 | 可选 TTS/静音 | 720p、4 秒、轻运镜、无接缝 |
| `controlled-balanced` | 导演动态·智能均衡 | LLM → 逐镜关键帧 → 逐镜 I2V → 本地合成 | 可选 TTS/静音 | 720p、5 秒、中运镜、钉帧衔接、`daylight_clean` |
| `controlled-cinematic` | 导演动态·品牌大片 | LLM → 逐镜关键帧 → 逐镜 I2V → 本地合成 | 可选 TTS/静音 | 1080p、8 秒、强运镜、尾帧续拍、`studio_product` |
| `native-film` | 原生整片 | LLM → 九宫格参考图 → 一次整片视频模型调用 | 视频模型原生音频，且不可改为 TTS | 以“智能均衡”的质量基线生成参考图和整片请求，原生整片不使用逐镜接缝 |

新项目默认 `native-film`，其选择本身有效；表单不再维护“用户是否额外点击了策略”的门槛。导演动态默认落点为 `controlled-balanced`，但只有用户主动选择导演动态时才会进入这条链路。

## 6. 项目级数据合同

在 `CreationBrief` 中新增以下字段（字段名可在实现中按现有命名惯例微调，语义不可改变）：

```ts
type OutputSchemeId =
  | "draft"
  | "controlled-rapid"
  | "controlled-balanced"
  | "controlled-cinematic"
  | "native-film";

interface OutputSchemeSnapshot {
  id: OutputSchemeId;
  outputStrategy: "draft" | "controlled-motion" | "native-film";
  resolution: "720p" | "1080p";
  shotDuration: number;
  motionStrength: number;
  motionIntensity: "subtle" | "normal" | "strong";
  motionRealism: "constraints" | "auto" | "off";
  chainMode: "pin" | "tail" | "off";
  visualLook: string;
  audioStrategy: "volcengine-tts" | "native-audio" | "mute";
}
```

`CreationBrief` 存储 `outputScheme` 这个快照，同时为旧代码保留派生的 `outputStrategy` 与 `audioStrategy`，直到所有消费者迁移完成。

服务端 `sanitizeCreationBrief` 必须：

- 验证方案 ID；非法或缺失的新建请求归一化为原生整片快照；
- 对历史 `creationBrief` 缺少快照的项目，从既有 `outputStrategy` 推导兼容快照，而不改写数据库；
- 原生整片强制归一化为 `audioStrategy: "native-audio"`，忽略客户端提交的 TTS；
- 仅接受白名单中的快照字段和范围，丢弃未知字段。

这样，项目创建后全局方案、模型默认参数或 UI 选择的变化都不会改变该项目的快照。

## 7. 页面和执行职责

### 7.1 `/start`

- 渲染完整创建表单与单一“出片方案”选择器。
- “开始生成”只创建项目、保存简报/创作意图/视觉约束/方案快照，并调用脚本生成。
- 不自动提交图片、I2V、TTS、FFmpeg 或原生整片视频任务。

### 7.2 `/project/[id]/script`

脚本生成完成后只提供：

- **查看脚本**：查看、编辑、重新生成口播稿；
- **一键出片**：按 `creationBrief.outputScheme` 启动唯一正确的链路。

移除：

- “出片策略：……／整片预览确认后开始花钱”说明和按钮；
- “免费快剪成片 / AI 生成成片”这两个竞争按钮；
- 用 URL `?auto=1` 暗中改变新项目策略的行为。

一键出片应显示该项目的方案、当前阶段、实际模型、音频来源、预计调用次数和开始付费前的确认信息。原生整片仍可保留计费确认，但它必须是“一键出片”的一个必经确认步骤，而不是新的策略入口。

### 7.3 `/project/[id]/assets` 与 `/project/[id]/production`

- `controlled-*` 方案才将“逐镜关键帧 / 逐镜动态镜头”作为主操作；现有导演模式入口迁移并收敛到此方案。
- `draft` 走静态素材与 FFmpeg；不创建 I2V 任务。
- `native-film` 走九宫格参考图与 `storyboard-film`；不展示或启动逐镜 I2V 任务。
- 各页读取项目 `OutputSchemeSnapshot` 覆盖当前全局生成参数。高级设置可供显式编辑该项目的快照，但不能改全局后悄悄影响项目。

## 8. 执行参数解析

增加一个纯函数，将 `CreationBrief.outputScheme` 解析为项目级生成参数。调用点包括：

- `script/page.tsx`：参考图与原生整片请求；
- `assets/page.tsx`：关键帧、逐镜 I2V、接缝、Look 和合成；
- 任何后续重试、恢复、预览或批量操作。

解析优先级固定为：

```text
项目 outputScheme 快照
  → 历史项目由旧 outputStrategy 推导的兼容快照
  → 旧项目当前全局设置（仅兼容没有 creationBrief 的项目）
```

模型 ID 继续遵循项目/服务当前配置，不把 API Key 或模型凭据写入简报。日志记录实际选择的模型与请求的时长、分辨率、帧数和平台返回用量。

## 9. 兼容、错误和计费

- 历史项目不进行批量数据迁移；首次读取时只在内存中派生兼容快照。
- 已经运行的旧项目按它们存量 workflow 恢复，不被本次修改中断。
- 若原生整片模型不可用，一键出片必须报明确的模型/配置错误，不能静默降级为免费草稿或 TTS 合成。
- 若导演模式选择 TTS 但 TTS 配置无效，任务停在音频阶段并报告配置问题；不得静默改为模型原生音频。
- 计费展示区分：免费草稿不计视频模型费用；导演模式按关键帧和 I2V 镜头计；原生整片按整片模型时长、分辨率和平台报价计。

## 10. 验收与测试

先写失败测试，再实现。至少覆盖：

1. 默认新建 `CreationBrief` 为 `native-film + native-audio`，无需额外策略点击。
2. 五种出片方案分别映射到唯一策略、音频限制和完整快照。
3. 创建 API 仅保存白名单快照字段；恶意字段被丢弃；原生整片强制原生音频。
4. `script/page` 的一键出片只依据项目快照选择链路：原生整片不发 TTS/I2V，导演模式发逐镜 I2V，免费草稿不发视频模型任务。
5. 修改全局 `activeProductionProfile` 后，已有项目的图片、视频、镜头衔接与 Look 请求参数不变。
6. 脚本页不存在两个竞争成片按钮或第二个策略选择；“查看脚本 / 一键出片”分别具有唯一职责。
7. 旧项目的 `creationBrief` 缺失或仅有旧策略时仍可打开、重生脚本和恢复原有流程。

验证范围包括相关 Vitest 单测、全量 `vitest run`（注入本地 FFmpeg 路径）、`tsc --noEmit` 和 `next build`。

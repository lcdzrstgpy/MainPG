# POD 爆款复刻最终架构

## 1. 目标

爆款复刻接收两类图片：

- **原素材图 A**：包含需要复刻的图案，可能同时包含原产品、人物、房间、道具和水印。
- **目标产品白底图 B**：用户自己的待复刻产品，可能是包、服饰、美甲、海报、3C 配件或其他 POD 产品。

系统需要把 A 中的图案应用到 B 的可印区域，生成目标产品的电商展示图，同时保持产品结构、材质、部件数量和图案关系正确。

系统由三类能力协作完成：

1. **多模态豆包**：分析图片、识别产品事实、生成摄影构图参数。
2. **本地系统代码**：标准化事实、匹配能力、推导规则、选择模板、编译 Prompt、执行任务和保存版本。
3. **图片生成模型**：根据最终 Prompt 和参考图生成图片。

复刻不要求用户编写 Prompt，也不把复刻任务变成重新设计图案。

## 2. 总体流程

```text
上传原素材图 A 和目标产品图 B
        ↓
豆包视觉分析 A、B
        ↓
系统标准化分析结果
        ↓
系统匹配能力库
        ↓
系统推导复刻规则
        ↓
选择全定制构图模板
        ↓
豆包生成摄影构图参数
        ↓
系统校验构图参数并冻结 ReplicaPlan
        ↓
系统编译最终 Prompt
        ↓
图片生成模型生成图片
        ↓
拆分四图并持久化结果
        ↓
用户查看结果或手动重新生成
```

分析结果、能力组合、规则、构图参数、最终 Prompt 和生成参数都必须保存，确保任务可以复现和追溯。

## 3. 核心概念

### 3.1 SourceArtworkSpec：原素材事实

描述 A 中哪些内容是需要迁移的图案，哪些内容必须丢弃。

```text
source_asset_id
source_product_name
artwork_motifs
artwork_colors
artwork_layout
artwork_density
artwork_repeat_pattern
artwork_text
artwork_print_region
source_product_structure
source_background_elements
source_people_and_props
source_watermarks_and_labels
artwork_extraction_confidence
analysis_model_version
analysis_schema_version
```

关键约束：

- `artwork_motifs`、`artwork_colors`、`artwork_layout` 是复刻主体。
- 原素材图中的产品形状不自动迁移到目标产品。
- 原素材图中的背景、人物、家具、道具、标签和水印默认排除。
- 图案内已有的文字可以保留；不允许系统新增文字。
- 每个字段都应带置信度或可判定状态，不能把模型猜测当成硬事实。

### 3.2 TargetProductSpec：目标产品事实

描述 B 的产品结构、材质、部件和可印区域。

```text
target_asset_id
product_name
product_type
component_count
component_structure
material
surface_texture
printable_regions
non_printable_regions
inner_outer_structure
openings_and_hardware
product_orientation
supported_usage_modes
occlusion_risk
product_confidence
alternative_hypotheses
analysis_model_version
analysis_schema_version
```

产品事实示例：

```text
product_type: nail_tip_set
component_count: 5
component_structure: five_separate_nail_tips
material: gel
printable_regions: front_surface
supported_usage_modes: hand_worn
inner_outer_structure: none
```

每个事实字段都包含：

```text
value
confidence
source        # model / user / system
```

豆包可以返回候选事实，但只有经过标准化和校验的字段才能进入规则推导。

### 3.3 CapabilitySet：能力组合

能力是系统支持的通用处理动作，不是产品类别名称。

初始能力集合包括：

```text
flat_print          平面印刷
local_print         局部印花
surface_wrap        立体表面包裹
multi_piece         多部件排列
standalone_display  独立摆放
hand_worn           手部佩戴
body_worn           人体穿着
wall_mounted        墙面悬挂
macro_detail        微距细节
white_background    白底素材
fabric_material     布料材质
paper_material      纸张材质
plastic_material    塑料材质
metal_material      金属材质
gel_material        凝胶材质
```

能力由标准化事实和用户选择推导，使用稳定 ID，不使用自由文本作为规则条件。

示例：

```text
美甲：multi_piece + gel_material + macro_detail；用户选择佩戴展示时再增加 hand_worn
海报：flat_print + wall_mounted + paper_material + white_background
托特包：local_print + standalone_display + fabric_material + white_background
手机壳：surface_wrap + standalone_display + plastic_material + macro_detail
服饰：local_print + body_worn + fabric_material + white_background
```

同一产品可以拥有多个能力；同一个能力可以被多个产品复用。

每个能力 ID 背后都必须有一份能力定义，不能只保存一个标签：

```text
capability_id
capability_type
required_facts
optional_facts
activation_conditions
generated_constraints
forbidden_conditions
compatible_templates
incompatible_capabilities
generation_strategy
prompt_slots
version
```

例如 `local_print` 的能力定义：

```text
能力类型：印刷能力
需要事实：printable_regions
触发条件：存在明确的局部可印区域
生成约束：图案只能覆盖声明的可印区域
禁止条件：不能覆盖缝线、五金、内衬和其他不可印区域
可用模板：标准商品图、白底素材图
```

豆包分析目标产品后提供当前产品的具体参数：

```text
printable_regions = front_panel
non_printable_regions = handle, seam, lining, hardware
```

系统将能力定义和产品事实组合成当前款的具体规则：

```text
启用能力：local_print
实际印花区域：front_panel
实际禁止区域：handle、seam、lining、hardware
```

豆包返回的建议标签不能直接作为系统能力。建议标签必须经过标准化映射、置信度判断和能力注册表校验；无法映射的标签进入 `unknown_capability`，由用户确认或采用保守方案。

### 3.4 ReplicaRuleSet：复刻规则集合

规则是系统根据事实、能力和用户选择推导出的约束集合，分为三类：

#### 硬约束

违反后结果不可接受：

```text
只能复制 A 的图案内容。
不能复制 A 的背景、人物、家具、道具和水印。
不能新增图案元素、颜色、文字、Logo 或贴纸。
必须保留 B 的产品轮廓、结构、材质和部件数量。
图案只能出现在 B 的 printable_regions。
```

#### 展示要求

展示要求不是由能力标签单独决定的。能力标签先说明产品支持哪些展示方式；最终哪些内容必须出现在四个画面中，由用户选择、构图模板和本次冻结的 `CompositionPlan` 共同决定。只有产品结构约束属于默认硬要求：

```text
multi_piece → 至少一个画面展示全部部件，避免套装部件被遗漏
hand_worn + 本次构图计划选择手部展示 → 对应画面展示手部使用
wall_mounted + 本次构图计划选择墙面展示 → 对应画面展示墙面使用
macro_detail + 本次构图计划选择微距细节 → 对应画面展示材质或图案微距
white_background + 本次构图计划要求白底素材 → 对应画面提供完整白底素材
```

因此，能力标签本身不等于必拍画面。`hand_worn` 首先表示目标产品支持手部使用；只有用户选择手部展示，或选中的构图模板明确要求手部展示时，系统才把手部机位加入本次 `CompositionPlan`。如果用户选择白底、独立摆放或不使用手模，则不生成手部画面。

#### 生成约束

```text
multi_piece → 生成时要求展示全部部件，不能主动省略部件
gel_material → 生成时要求保留凝胶光泽、边缘和表面质感
local_print → 生成时要求图案不越过可印区域
hand_worn（已被本次构图计划选用）→ 生成时要求体现正确的手部关系和佩戴关系
wall_mounted（已被本次构图计划选用）→ 生成时要求产品处于墙面展示状态
```

规则必须带有：

```text
rule_id
rule_version
priority
trigger
effect
generation_constraints
source_reason
```

规则由产品事实、生产限制、电商展示要求和已验证失败样本共同确定。新增规则必须经过样本验证后发布；用户不能直接提交任意自然语言作为系统硬规则。

### 3.5 CompositionTemplate：构图模板

构图模板复用 POD 全定制的构图模板能力，负责摄影表达，不负责产品事实和复刻硬约束。

模板描述：

```text
template_id
template_version
supported_capabilities
panel_slots
camera_options
lighting_options
background_options
composition_options
required_slots
forbidden_combinations
```

模板只定义可用的摄影结构和参数范围。例如：

```text
ecommerce_standard_v1
- 主图
- 图案细节图
- 结构或使用图
- 白底素材图

ecommerce_wearable_v1
- 使用状态主图
- 局部细节图
- 另一使用角度
- 白底素材图

ecommerce_multi_piece_v1
- 全套部件图
- 单部件细节图
- 使用或结构图
- 整套白底图
```

模板不直接绑定“美甲”“托特包”等品类，而是绑定能力要求。

## 4. 豆包调用边界

### 4.1 图片事实分析

输入：A、B 图片和明确的图片角色。

输出：结构化 `SourceArtworkSpec` 和 `TargetProductSpec`。

豆包不能在这一步输出最终 Prompt，也不能替系统决定规则。它只回答“图中有什么”和“哪些内容可能是图案或产品结构”。

A、B 可以在一次调用中分析，但请求必须明确：

```text
图片 A 只分析原始图案和需要排除的环境内容。
图片 B 只分析目标产品结构、材质和可印区域。
```

系统仍分别保存 A、B 的独立分析结果，以便单独重试和审计。

### 4.2 构图参数生成

输入：

- `SourceArtworkSpec`
- `TargetProductSpec`
- `CapabilitySet`
- `ReplicaRuleSet`
- 可用构图模板
- 用户的构图偏好

输出：结构化 `CompositionPlan`，而不是自由文本 Prompt。

```text
template_id
template_version
panels
camera
lighting
background
composition_notes
user_overrides
```

豆包只能从系统提供的模板和参数范围内选择，不得创建不存在的机位、能力或规则。

## 5. 确定性规则引擎

规则引擎接收标准化事实、能力和用户选择，输出 `ReplicaRuleSet`。

示例：

```text
输入：component_count = 5
输出：启用 multi_piece

输入：supported_usage_modes 包含 hand_worn，且用户或构图模板选择手部展示
输出：启用 hand_worn

输入：supported_usage_modes 包含 hand_worn，但用户选择白底或独立摆放
输出：不启用 hand_worn，不生成手部机位

输入：printable_regions = front_surface
输出：print_region = front_surface_only

输入：material = gel
输出：启用 gel_material 和 gel_material_rendering
```

同一份标准化输入、同一版本规则和同一用户选择，必须得到同一份规则结果。

规则引擎不能直接处理低置信度或冲突事实：

```text
高置信度 → 自动推导
中置信度 → 使用默认方案并提示用户确认，或采用保守规则
低置信度 → 暂停提交，要求用户选择
事实冲突 → 不自动生成，要求修正
```

用户可以覆盖展示偏好，但不能通过覆盖项违反产品安全和印刷硬约束。

## 6. 构图计划生成和冻结

构图计划不是全局永久固定的，也不是每次重新生成时随机变化的。

生命周期：

1. **草稿阶段**：系统根据能力推荐模板和构图参数，用户可以修改。
2. **提交阶段**：冻结本次任务的 `CompositionPlan`、规则版本和用户覆盖项。
3. **生成阶段**：所有初次生成、暂停恢复、进程重启和服务端失败重试都使用冻结计划。
4. **用户重新选择**：用户修改产品事实、展示方式或构图偏好后，创建新的任务版本，保留旧结果和旧计划。

## 7. 最终 Prompt 编译

最终 Prompt 由系统代码编译，不由豆包自由改写。

固定为四个部分：

### PART 1：全局复刻契约

包含：

```text
2x2 或独立面板输出格式
参考图顺序及角色
只能复制原素材图案
不得带入原素材背景和原产品
不得新增文字、Logo、水印、贴纸
所有画面使用同一目标产品和同一套图案
```

### PART 2：产品事实和确定性规则

由 `TargetProductSpec`、`CapabilitySet` 和 `ReplicaRuleSet` 编译：

```text
目标产品的名称、类型、材质、部件数量
可印区域和不可印区域
必须展示的部件和使用关系
必须保留的结构
禁止出现的区域和元素
生成约束对应的提示词要求
```

### PART 3：摄影构图方案

由冻结后的 `CompositionPlan` 编译：

```text
每个画面的角色
主体是否完整
镜头距离和角度
光线和阴影
背景和环境
产品摆放或使用方式
材质和表面细节表现
```

### PART 4：输出格式和最终禁止项

包含：

```text
四格位置和顺序
画面边界要求
不允许的重复、缺失和裁切
不允许的新增元素
本次生成的额外约束项
```

编译器必须对字段进行转义、长度控制和空值处理，避免用户输入或模型分析文本破坏硬规则。

## 8. 生成策略

系统根据 `ReplicaPlan` 选择生成策略：

### grid

一次生成 2x2 四宫格，再由系统拆分四图。

适用于：

- 普通箱包
- 服饰
- 手机壳
- 结构简单且四个机位关联性强的产品

### independent_panels

四个机位分别生成，再绑定到同一款产品。

适用于：

- 美甲
- 多部件饰品
- 海报和墙面装饰
- 需要不同场景或不同画幅的产品
- 四宫格一次生成容易串位的产品

无论哪种策略，四张结果都必须共享同一个冻结的图案事实、目标产品事实、规则和构图计划。

## 9. 生成状态和失败处理

第一期不设置视觉质量门禁，不调用豆包判断图片是否合格。图片生成模型成功返回、图片下载成功、四图拆分成功并完成结果落库后，任务即可标记为生成成功。

系统仍处理工程层面的失败：

```text
上传失败 → 标记对应资产失败并允许重新上传
参考图发布失败 → 不提交图片生成调用
图片模型超时或服务端错误 → 按现有策略自动重试
图片下载或拆图失败 → 标记本次生成失败并允许款级重新生成
任务暂停或进程重启 → 使用冻结 ReplicaPlan 恢复
```

用户查看结果后，可以手动重新生成整款或指定面板。手动重新生成默认复用原来的事实、规则和构图计划；用户修改方案后创建新的计划版本。

## 10. 持久化和版本

每个复刻款保存：

```text
source_artwork_spec_json
target_product_spec_json
capability_set_json
rule_set_json
composition_plan_json
replica_plan_json
prompt_snapshot
analysis_model_version
rule_version
template_version
image_model_version
generation_attempts
```

`prompt_snapshot` 必须保存实际提交给图片生成模型的完整 Prompt，而不是空对象或仅保存模板 ID。

版本关系：

```text
analysis_schema_version
capability_registry_version
rule_version
composition_template_version
prompt_compiler_version
image_model_version
```

任务提交后冻结这些版本。后续系统发布新规则不能改变正在执行或已经完成的任务。

## 11. 用户交互

用户不需要填写 Prompt，只需要确认系统识别结果和展示偏好。

目标产品确认区显示：

```text
识别产品：美甲
部件数量：5 个
材质：凝胶
印花区域：甲片正面
展示方式：手部佩戴
推荐模板：佩戴型多部件商品图
识别置信度：91%
```

用户可修改：

```text
产品类型
部件数量
展示方式
是否使用手模或人体模特
是否生成生活场景
是否只生成白底图
```

用户不能修改：

```text
不得新增 Logo 和水印
不得复制原素材背景
图案必须来自 A
图案只能出现在可印区域
```

## 12. 规则和能力库维护

系统维护四类注册内容：

```text
FactSchemaRegistry       事实字段和标准值
CapabilityRegistry       能力定义、触发条件和适用范围
RuleRegistry             硬约束、展示要求和生成约束
CompositionTemplateRegistry 构图模板和参数范围
```

新增产品不要求新增一整套品类 Prompt。维护流程是：

```text
发现已有能力可以覆盖 → 配置事实映射
出现新的展示行为 → 新增能力
出现新的硬约束 → 新增规则
出现新的摄影表达 → 新增或扩展构图模板
```

每次新增能力或规则必须同时提供：

```text
触发条件
不适用条件
生成要求
生成约束
示例图片
离线评测样本
版本号
```

## 13. 最终调用链

推荐实现为：

```text
调用 1：豆包分析 A、B，输出 SourceArtworkSpec 和 TargetProductSpec
系统处理：标准化事实、匹配能力、推导规则、选择模板
调用 2：豆包根据事实、规则和全定制构图模板输出 CompositionPlan
系统处理：校验并冻结 ReplicaPlan，编译最终 Prompt
调用 3：图片生成模型生成四图或独立面板
系统处理：保存生成结果，支持用户查看和手动重新生成
```

最终 Prompt 不依赖额外的豆包文本改写。它由系统编译器按照固定四段式生成，确保事实、规则、构图参数和禁止项不会在最后一步丢失或被改写。

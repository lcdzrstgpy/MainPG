# POD 爆款复刻 Implementation Plan — DeepSeek 接手版

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户指定由 DeepSeek 接手；本文仅交付实施计划，不代表功能已实现。

**Goal:** 在 POD 定制下新增「爆款复刻」，把一张 POD 商品样图中的图案，尽量忠实地应用到用户上传的一个或多个白底产品上；每个产品独立填写商品信息，生成四张商品图和标题，批量导出。

**Architecture:** 复用现有 POD 批次、四宫格出图、标题、规格卡、计费、重试和导出底座，增加 `replica` 模式及按款保存的目标产品快照。一次批次只有一个图案来源，但每款拥有不同的白底图、产品信息、SKU 和尺寸。

**Tech Stack:** React 18、TypeScript、Node test runner、FastAPI、Pydantic v2、SQLite、Pillow、pytest、openpyxl。后端 Python 版本遵循当前 `local-runtime/pyproject.toml`（当前要求 >=3.14），不要照搬旧计划中的版本。

## 1. 已确认需求与边界

### 1.1 用户已经确认

- 导航：在「POD 定制」分组下，与「全定制」「半定制」并列新增「爆款复刻」。
- 输入：**一张别人的 POD 样图 + 一张或多张其他产品白底图**，不是多张样图套同一个模板。
- 示例：带图案的包作为来源，用户上传抱枕、收纳篮白底图，将包上的图案分别迁移到这些产品。
- 最低输入为一张样图和一张白底图，每张白底图对应一款。
- 图案尽量忠实复刻，保留元素、颜色及排列关系，不做灵感拓款。
- 用户不用写指示词；直接生成商品图，不先提取平面图案让用户确认。
- 每个目标产品**独立填写**尺寸、SKU、售价、申报价、重量等，复用全定制的店小秘信息编辑板块。
- 输出沿用全定制：每款四张商品图、自动标题、规格卡、上架导出；第一版不提供独立印刷文件。

### 1.2 实施默认值

- 每批 1–200 个目标产品，生成数量由白底图数量决定，不增加花色数量选择器。
- 一款一次四宫格生图，四张拆分图的角色、排序、规格卡位置及发布方式沿用当前全定制实现。
- 计费采用全定制 `pod_random_v1` 画像，每个目标产品计为一款；估算和真实结算都复用当前契约，不另加图案识别费用。
- 同一批次不增加多来源、多方案、手工可印区域编辑、商品链接抓取或独立图案下载。
- 样图支持单张粘贴、拖拽、上传；目标图支持多文件拖拽、上传，以及逐张粘贴。仅消费剪贴板中的图片文件，不新增任意远程 URL 抓取。
- 样图文字若属于图案本身可以保留；背景、界面文字、拍摄水印和其他无关内容不能迁移。
- 保留目标产品的形状、材质、结构，图案应用到外部可印表面；内衬及非可印部件沿用全定制约束。
- 照片遮挡、褶皱和不可见部分允许模型推断，产品文案不能承诺像素级一致。

### 1.3 工作区注意事项

本计划编写时，POD 的 `contracts.py`、`service.py`、`worker.py`、`export.py`、标题运行时、规格卡组件及相关测试已有未提交改动，涉及规格卡厘米/英寸等工作；另外还有非本功能改动及删除文件。

接手先运行 `git status --short` 和阅读相关 diff，以**当前文件**为依据。不要 reset、checkout 覆盖、清理其他改动或把不相关文件加入提交。先记录基线失败；既有失败不得描述为本功能通过。

## 2. 已核实的代码事实与复用入口

| 子系统 | 入口 | 对实施的影响 |
| --- | --- | --- |
| 导航 | `web-frontend/src/app/navigation/modules.ts`、`web-frontend/src/app/layout/WorkspaceShell.tsx` | 已有 `pod_workflow` 和两个子模块，需要注册第三个模块及页面映射 |
| 全定制页面 | `web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.tsx` | 现有店小秘编辑区内嵌在页面，适合抽成受控共享组件，保持全定制原行为 |
| SKU/尺寸 | `podCustomizationModel.ts`、`SpecCardDrawer.tsx`、`SpecCardTableEditor.tsx` | 已有 SKU 校验、尺寸行同步及单位能力；复刻按产品复用，不能维护第二套尺寸规则 |
| 后端入口 | `local-runtime/wh_local/modules/pod_customization/{router,contracts,service,repository}.py` | 同一模块已有全定制和半定制，增加复刻路由及 mode，不新建重复 worker |
| 当前批次 | `repository.py`、`migrations/001_pod_customization.sql` | 批次只有一个模板快照、一套业务字段和上架信息；不能直接用现有批次级字段处理所有目标 |
| 模式隔离 | `migrations/014_semi_customization.sql`、`service.list_batches()` | `mode` 已存在，旧批次默认 `full`，全定制历史已按 `mode="full"` 过滤 |
| 当前提示词 | `prompts.py` | 明确禁止复制模板图案，且会改变元素、配色和密度；复刻必须独立构造提示词，不能只隐藏输入框 |
| 多图输入 | `ai_runtime.py`、`runtime_contracts.py` | 当前 POD 请求只有一张模板图，需要扩展为有角色、有顺序的参考图 |
| 已有多图适配 | `product_processing/infrastructure/media.py::_request_wuyin_image` | 普通模型 `urls` 使用数组；`image_gpt_2.5` 使用逗号连接字符串，不能传数组 |
| 素材 | `assets.py` | 已有内容寻址、JPEG/PNG/WebP 校验、20 MB 和 64,000,000 像素限制；复用实际常量及授权预览端点 |
| 标题、规格卡、导出 | `worker.py`、`title_runtime.py`、`export.py` | 当前消费批次级字段，要统一从当前款的快照取值，初始生成与重试均要覆盖；现有规格卡重印支持 style_index，但保存配置仍更新整个 batch，需要修正复刻分支 |
| 计费 | `billing_contract.py`、`service.py`、`remote_billing.py` | 一批 N 款已有调用计划、冻结和结算能力，不拆成 N 个独立批次 |

上表路径在 POD 前端或后端目录内的简称需按对应子系统解析。实施前重新核实迁移编号及实际函数位置。

## 3. 页面与交互

新增前端模块 `web-frontend/src/modules/pod_replica/`，导航 ID 为 `pod_replica`。

1. 顶部「POD 样图」：单图预览、粘贴/上传/替换入口。多张样图一起粘贴时提示只允许一张，不静默选择最后一张。
2. 中部「目标产品」：白底图卡片列表，显示名称、缩略图、信息是否完整；支持新增、删除，按添加顺序显示。
3. 点击卡片打开商品信息编辑抽屉，字段包含产品名称、商品类目及全定制店小秘信息。产品名称、类目必填；图片风格、元素关键词、设计提示词均不出现。
4. 从全定制抽出 `PodListingFieldsEditor` 受控组件，共用售价、标题模式、SKU、申报价、重量及规格卡入口。校验与尺寸-SKU 同步调用现有 helper。
5. 支持「复制其他产品信息」：深拷贝上架信息、SKU、规格卡；保留当前产品的名称、类目、图片和卡片 ID。复制后两份数据互不关联。
6. 提交按钮显示「开始复刻 N 个产品」及沿用全定制口径的费用估算。任何产品有错误时不提交，定位第一个问题产品，保留其他产品已经填写的信息。
7. 结果按产品展示四图、标题、状态；复用图库、放大查看、标题编辑、导出勾选及款级重新生成。不得出现创作指示词输入框。
8. 批次历史保留样图、目标产品映射；支持暂停、取消、恢复、失败重试。运行中改变草稿不会改变当前任务快照。
9. 草稿按账号及工作区存储，使用独立版本/key；仅保存资产 ID 和字段，不把原图字节或大段 base64 塞进 localStorage。恢复后校验资产是否仍存在，缺失的要求重新上传。
10. 正在编辑名称、SKU 等文本框时，粘贴文字仍按默认行为处理；图片导入只在明确图片区域或非文本编辑场景发生。释放临时 object URL。

## 4. 数据及接口契约

### 4.1 公共类型和 API

新增 `ReplicaImageUploadResponse`、`ReplicaTargetCreate`、`ReplicaBatchCreate`、`ReplicaBatch`。已有 batch mode 的相关枚举/类型统一接受 `full | semi | replica`，旧响应缺少 mode 时保留当前兼容行为。

接口均位于 `/api/pod-customization`：

| 方法与路径 | 请求/行为 |
| --- | --- |
| `POST /replica/images` | multipart：`file`、`role=source|target`；每次上传一图；返回 `asset_id`、`role`、宽高及授权本地预览路径 |
| `POST /replica/batches` | JSON 创建下述复刻批次，服务端用 targets 数量确定款数 |
| `GET /replica/batches` | 按已有 limit/offset 分页，只返回 replica 模式 |
| `GET /replica/batches/{batch_id}` | 返回共有批次结果与 `source`、有序 `targets`；每个 target 带稳定 `style_index`、白底图预览及自己的字段 |

暂停、取消、恢复、删除、款级重新生成、标题编辑/重生、导出选择、失败重试、店小秘/妙手导出，复用已有通用 `/batches/{batch_id}/...` 端点及调用语义。规格卡重印复用 `POST /batches/{batch_id}/spec-card/reprint`，复刻请求必须带现有的 `style_index` 字段，否则返回 422；仅更新该目标快照及对应图片，不更新整批配置。新建复刻专属端点核实 mode，通用操作按现有授权与状态门槛执行。复刻模式不接受非空 `creative_prompt`，避免从 API 绕过图案锁定规则。

创建请求结构：

```json
{
  "client_request_id": "7a6bbba3-5084-4657-8b8f-7406367c034f",
  "source_asset_id": "asset_source",
  "title": "包图案迁移到抱枕",
  "targets": [
    {
      "target_asset_id": "asset_cushion",
      "product_name": "抱枕",
      "listing_fields": {
        "title_mode": "long",
        "suggested_price_usd": 19.99,
        "category_name": "抱枕",
        "skus": [
          {"name": "40cm", "declared_price": 6.0, "weight_g": 300.0}
        ],
        "spec_card": {
          "enabled": true,
          "style": "light",
          "corner": "bottom-right",
          "display_unit": "cm",
          "cells": [
            ["SKU", "Length", "Width", "Height"],
            ["40cm", "40", "40", "10"]
          ]
        }
      }
    }
  ]
}
```

上面资产 ID 是示例，创建时使用上传接口返回的真实 ID。`listing_fields` 复用现有 `ListingFields`，不建立另一套 schema；`client_request_id` 由前端生成，并在一次提交及网络重试之间保持不变。`business_fields.product_name` 从产品名称派生，`product_category` 从 `listing_fields.category_name` 派生；其他可选业务字段采用现有空默认值。不调用基于文字主题的智能填写，不自动扩充风格元素或配色。

- `targets` 长度 1–200，保持输入顺序；校验名称、类目、SKU 和规格卡，复用现有长度/金额等约束。
- 样图和目标资产必须属于当前 actor/workspace，角色符合上传标记；空资产、无效图片、缺少 SKU/尺寸等返回明确错误，不开始扣费或生成。
- 上传失败逐图显示，可重试；未上传完的图不能参与批次提交。
- `client_request_id` 在 actor/workspace 内唯一，同 ID 同规范化请求返回已有批次，同 ID 不同请求返回 409；前端显式重新创建任务时使用新 ID。
- 图片大小、像素、multipart 体积和上传关闭逻辑沿用当前 POD 限制。错误脱敏，不返回提供方 key。

### 4.2 存储选择

新增一份增量迁移，接手时取实际下一编号（当前最后一份为 014，预期为 `015_replica_customization.sql`）。不重建历史批次表。

- `pod_customization_replica_batches`：`batch_id` 主键及批次外键、`workspace_id`、`owner_user_id`、`source_asset_id` 资产外键、`client_request_id`、`request_hash`。唯一键为 `(workspace_id, owner_user_id, client_request_id)`。
- `pod_customization_replica_targets`：主键 `(batch_id, style_index)`；保存目标 `asset_id`、`business_fields_json`、`listing_fields_json`，引用父批次和资产。名称、类目在业务字段快照内，不再保存可分叉的第二份值。
- 父批次 `mode=replica`、`requested_count=len(targets)`；一款仍创建四个结果行和一个标题行。
- 兼容现有模板非空外键：为该复刻批次创建以**第一张目标图**为资产的内部模板及快照，设置隐藏状态，不进入用户模板库；只作为旧批次结构的锚点。复刻不要求用户手工标定，也不调用额外 AI 标定。
- 父批次旧的模板/业务/上架字段可镜像首款用于结构兼容；**复刻的所有实际消费者必须通过按款解析器取值，不得回退成首款数据**。少一款快照是明确失败，不能悄悄套用别款信息。
- 在一个本地事务里写批次、内部模板及快照、来源关联、全部目标、结果和标题占位；复刻不生成用于原创设计的元素分配记录。
- 批次删除和现有资产清理规则需计入样图、目标图、内部模板的引用；相同内容可能被多个任务复用，不直接删除共享文件。

新增集中解析器 `replica_context.py`，接口固定为：

```python
def style_product_context(batch: dict, style_index: int) -> dict:
    """返回 template、business_fields、listing_fields；replica 取当前款冻结目标，其他模式返回既有批次上下文。"""
```

`replica` 的 template 由目标资产元数据构造，只包含该款白底图；解析器不做磁盘 I/O、不修改 batch。仓库加载 targets 时以整数 `style_index` 建内部映射，公共响应转为有序数组。此解析器由 worker、标题、详情、规格卡、导出共享。

## 5. 生图、恢复和导出实现要求

### 5.1 有序参考图和提示词

在 `runtime_contracts.py` 新增不可变 `ListingReferenceImage(role, content, content_type)`，`DirectListingGridRequest` 增加 `reference_images: tuple[ListingReferenceImage, ...] = ()`。

- 复刻固定传两张，顺序 `[pattern_source, target_product]`。
- 全定制继续兼容旧 `template_image/template_content_type`，仍一张参考图；半定制仍零参考图。
- `ai_runtime.py` 逐图内容寻址发布，复用已有公网 URL 校验；两张 URL 准备好才提交提供方；`GeneratedMedia.reference_count` 反映实际数量。
- 复用现有提供方轮询、超时、下载、错误分类；不另做一套客户端。
- 普通模型提交 `urls=[source_url, target_url]`；2.5 提交 `urls=source_url + ',' + target_url`。保持全定制单图、半定制无图请求兼容。

新增 `replica_prompts.py::build_replica_listing_prompt(fields: BusinessFields, *, attempt: int) -> str`：

- 明确第 1 图只提供图案，第 2 图只提供目标产品，生成画面只能出现目标产品。
- 颜色、图案元素、排列关系尽量来自样图；按目标表面做合理透视、缩放和重复适配，不能随意变色、换元素或重新设计。
- 四格是同一个目标产品、同一图案的四种既有电商展示角色，不是四种花色，也不是四个目标产品。
- 使用既有全定制四格位置和镜头要求；允许图案自带文字，禁止新增标注、水印、拼接边框或样图背景。
- attempt 2 只强化图案与产品一致性，不加入原创花色配方。

### 5.2 必须接入的消费路径

- 初始生成、自动第二次尝试、手动款级重新生成、选中失败款重试、暂停恢复、进程重启恢复，都读取同一款目标快照与同一份来源资产。
- 初始 worker 不再把整批同一个模板字节传给全部复刻款；每款构造请求时解析自己的白底图。
- 标题上下文中的产品名、类目及限制来自目标款；商品名称不能取样图产品。
- 规格卡初始合成、预览及重印使用目标款配置，包括当前厘米/英寸能力；不更改其他款的尺寸或图片。
- 图库、详情及导出资格分析使用当前款字段；不能只修生成而遗留导出/重试使用批次首款。
- 不将复刻模式传入 `build_style_listing_prompt`、`assign_style_elements` 或原创多样性规则；不因跨款图案相似而拒绝正常复刻。
- 不移除全定制和半定制现有规则，也不套用历史已停用的 pattern/scene optimization 流程。

### 5.3 计费与一致性

- 复刻一次任务仍一批 N 款，调用计划复用现有 full 批次，含图及标题、每款已有重试额度。
- 先完成全部目标校验和持久化，再按已有 create_batch 的冻结、状态更新及入队顺序处理；冻结失败和进程中断沿用既有补偿/恢复机制。
- 用 `client_request_id` 关联稳定 batch ID，数据库唯一约束处理重复请求；只允许一个提交者执行冻结/入队，竞争方读取已有任务。不要用新的本地锁替代现有跨进程 claim/fencing。
- 保留已有 `execution_epoch`、租约、调用 ID、结果已返回的计费区分、结算恢复，不能因为上传两张参考图而重复计为两次生图。

### 5.4 导出

- 保留原店小秘与妙手列结构、图片角色及可导出判断。
- 将 SKU 展开移入每款循环，通过 `style_product_context` 读取该款的 SKU、申报价、重量、尺寸、售价和类目。
- 一个文件容纳不同产品；商品和 SKU 编码继续用既有批次/款号规则，款间不合并 SKU，不从样图继承名称。
- 标题和四图满足现有要求的勾选款可以导出，部分失败可导出成功款；沿用导出数量/跳过数量响应头及历史记录。

## 6. 按顺序实施的任务

每项先写针对实际行为的失败测试，再实现并运行到通过；使用现有 fixture/fake runtime，避免测试请求真实提供方。每项结束检查 diff，不提交用户的其他改动。

### Task 1：契约、资产与持久化

**Files:** 新增 `replica_context.py`、下一编号增量迁移和 `tests/test_replica_contracts.py`、`tests/test_replica_persistence.py`；修改 POD 的 `contracts.py`、`repository.py`、`service.py`、`router.py` 及迁移注册入口。

**Consumes:** 当前 `ListingFields`、`PodAssetStore`、Actor 授权及批次仓库。

**Produces:** 第 4 节 API、冻结的来源/目标映射、统一按款解析器和模式隔离。

- [ ] 测试 0/1/200/201 个目标、无效字段、角色错误、跨账号资产、重复请求、缺失目标快照、增量迁移与事务回滚。
- [ ] 实现独立图片上传和复刻创建；不要调用全定制创建后再非事务地修改 mode/目标。
- [ ] 新批次、历史查询和公共响应携带所有独立目标，旧 full/semi payload 保持兼容。
- [ ] 运行：`cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_replica_contracts.py wh_local/modules/pod_customization/tests/test_replica_persistence.py -q`。

### Task 2：双参考图与图案锁定

**Files:** 新增 `replica_prompts.py` 和 `tests/test_replica_runtime.py`、`tests/test_replica_prompts.py`；修改 `runtime_contracts.py`、`ai_runtime.py`。

**Consumes:** 目标业务字段、来源与目标资产字节。

**Produces:** 有序双参考图请求及独立复刻提示词，零图/单图仍兼容。

- [ ] 用 fake session 捕获提供方请求，验证普通模型数组和 2.5 字符串、顺序、reference_count、发布失败时不提交生图。
- [ ] 验证 full 一张、semi 零张、replica 两张，提示词不混入原创图案/随机配色规则。
- [ ] 实现第 5.1 节接口，引用现有模型解析与 URL 发布代码，不变更全局图像模型选择。
- [ ] 运行：`cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_replica_runtime.py wh_local/modules/pod_customization/tests/test_replica_prompts.py -q`。

### Task 3：worker、标题、规格卡及计费

**Files:** 修改 `worker.py`、`service.py`、`repository.py` 的标题上下文路径；新增 `tests/test_replica_worker.py`、`tests/test_replica_billing.py`，复用现有 worker/billing 测试工具。

**Consumes:** Task 1 的按款快照与 Task 2 的双图请求。

**Produces:** 完整四图/标题/规格卡链路及批次生命周期。

- [ ] 两个目标分别使用不同图片、名称、SKU、尺寸；fake runtime 捕获每款双图和标题/规格卡上下文。
- [ ] 覆盖第二次尝试、单款再生成、失败重试、恢复、重启和指定产品规格卡重印，断言其他款内容不变。
- [ ] 覆盖 N 款冻结/结算、余额不足、重复提交、取消/暂停及部分失败，确认复用现有随机画像和调用 ID。
- [ ] 实现所有消费路径，保留 bounded concurrency、claim、fencing 和结算恢复。
- [ ] 运行：`cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_replica_worker.py wh_local/modules/pod_customization/tests/test_replica_billing.py -q`。

### Task 4：按产品导出

**Files:** 修改 `export.py` 及相关 service 详情/导出读取；新增 `tests/test_replica_export.py`。

**Consumes:** 已生成图/标题、按款上架字段和导出选择。

**Produces:** 店小秘、妙手合并文件及正确的导出计数。

- [ ] 构造目标 A 有 2 个 SKU、目标 B 有 1 个 SKU，断言行数 3，各行图片、标题、价格、重量和尺寸属于对应产品。
- [ ] 覆盖单位不同、类目不同、部分失败、未勾选产品、无可导出产品以及原 full/semi 兼容行为。
- [ ] 把 `_export_skus` 和字段读取放到每款展开边界，规格卡单位转换复用当前代码。
- [ ] 运行：`cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests/test_replica_export.py wh_local/modules/pod_customization/tests/test_dianxiaomi_export.py -q`。

### Task 5：前端页面、共享编辑器与导航

**Files:** 新增 `web-frontend/src/modules/pod_replica/{pages,components,data,api,styles}` 及 `types.ts`；新增共享 `PodListingFieldsEditor.tsx`；修改 `PodCustomizationPage.tsx`、导航注册与 `WorkspaceShell.tsx`。

**Consumes:** 第 4 节接口及原全定制 SKU/规格卡 helper。

**Produces:** 第 3 节页面，完整的粘贴/上传、逐产品编辑、结果/历史/导出交互。

- [ ] 增加 `podReplicaModel.test.ts`、`podReplicaDraft.test.ts` 和 `podReplicaApi.test.ts`，验证数量/顺序、字段校验、复制深拷贝、草稿隔离和请求结构。
- [ ] 抽出受控上架编辑器，先保证全定制页面的输入、校验、SKU 增删、尺寸映射与单位选择保持原行为。
- [ ] 实现复刻页面和模块，复用已有图库与操作；若图库假设单一产品名称，增加按 style_index 提供产品上下文的入口。
- [ ] 目标列表使用稳定 client ID，删除/复制/选中不会因数组下标改变而串用编辑状态；服务器 style_index 在提交后按输入顺序冻结。
- [ ] 上传错误、局部字段错误、轮询、暂停恢复和导出错误用现有中文错误处理；不要为复刻露出指示词输入。
- [ ] 更新 POD 导航测试：第三项「爆款复刻」，页面注册及权限配置沿用 POD 能力；原两个入口继续可用。
- [ ] 运行：`cd web-frontend && node --test --experimental-strip-types src/modules/pod_replica/data/podReplicaModel.test.ts src/modules/pod_replica/data/podReplicaDraft.test.ts src/modules/pod_replica/api/podReplicaApi.test.ts src/app/navigation/modules.test.ts src/app/navigation/podCustomizationNavigation.test.ts`。
- [ ] 运行：`cd web-frontend && npm run build`。

### Task 6：回归与实图验收

- [ ] 后端回归：`cd local-runtime && .venv/bin/python -m pytest wh_local/modules/pod_customization/tests tests/test_pod_customization_contracts.py tests/test_pod_customization_billing_flow.py tests/test_pod_customization_migrations.py tests/test_pod_request_limits.py -q`。
- [ ] 前端回归：`cd web-frontend && node --test --experimental-strip-types src/modules/pod_customization/data/*.test.ts src/modules/pod_customization/api/*.test.ts src/modules/pod_customization/pages/*.test.ts src/modules/pod_semi_customization/pages/*.test.ts src/app/navigation/*.test.ts`。
- [ ] 若有基线失败，区分原有失败和新回归，记录命令与证据；不通过静默删断言或改预期掩盖失败。
- [ ] 使用可用于测试的清晰样图做实图验收：样图为包，目标为抱枕、收纳篮；分别设置不同类目、SKU、价格、尺寸和单位。
- [ ] 人工检查元素/配色/排列的保真、目标结构、四图一致性及无样图背景混入；下载工作簿逐项核对两产品与三个 SKU 的对应关系。
- [ ] 验证一张样图＋一张目标的最低流程，多目标局部失败后仍能导出成功产品，重试仅影响指定产品。
- [ ] 最终汇报完成任务、测试结果、实图验收结果及限制。实图验收若未运行需明确标为未验证，不能仅凭 fake runtime 宣称复刻质量已达标。

## 7. 接手指令与完成标准

可以直接将以下内容交给 DeepSeek：

> 请实施 `docs/superpowers/plans/2026-10-06-pod-bestseller-replica.md`。按任务顺序完成，不改变已经确认的「一个来源图案应用到多个目标产品」及「每个产品独立 SKU/尺寸」需求。先阅读当前工作区 diff，保护其他未提交改动；参考现有 POD 全定制和半定制，复用底座。实现必要测试，完成前端构建和后端回归，并对实图效果与店小秘导出做验收。不要重新要求用户填写设计提示词，也不要擅自改为灵感拓款或先提取图案流程。

完成标准：用户能在「爆款复刻」上传一张 POD 样图和多个白底产品，分别填写上架信息，一键获得各产品四图与标题，按产品查看、重试和下载同一份正确的上架工作簿；全定制与半定制继续正常工作。

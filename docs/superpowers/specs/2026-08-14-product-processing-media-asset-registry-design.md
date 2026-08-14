# 产品处理统一图片资产中心设计

**状态：已确认**

**范围：** 仅覆盖本设计上线后新入池的商品；不回填、不迁移、不修复已有草稿、历史任务、历史画布和旧图片记录。

## 1. 问题与设计目标

当前链路同时使用远程 URL、预览 URL、/pp-media 展示路径、任务输出绝对路径和画布路径来表示同一张图片。下游通过字符串形态推测存储位置，导致已下载的来源图没有被尺寸画布复用；SKU 图也没有进入来源图同步范围。

新设计的目标是让主图、轮播图、详情图、SKU 图、AI 生成图、人工上传图和尺寸图都以 asset_id 作为唯一身份。URL 只是一种后端投影出的短期展示值，绝不是存储权威。

## 2. 不变量

1. 新链路业务对象只能持有 asset_id 或 binding_id；不得以 URL、服务端路径或 /pp-media 路径作为图片身份。
2. 可渲染资产必须处于 ready 状态，并同时具备受管本地文件、content_hash、content_type、width 与 height。
3. 远程来源图未物化前只能处于 pending、materializing、retryable 或 failed，不能进入尺寸画布渲染。
4. 一张物理图片可以绑定多个业务角色；业务归属由绑定记录表达。
5. 尺寸画布保存独立的本地编辑快照，不能直接读取预检目录、任务输出目录或外部 URL。
6. 所有内容读取必须校验 workspace 与产品归属；客户端不能提交文件路径。
7. 历史草稿固定保持 media_contract_version=1；新草稿写入 2。

## 3. 数据模型

### 3.1 草稿版本

product_processing_drafts 增加 media_contract_version INTEGER NOT NULL DEFAULT 1。

选品 handoff 消费得到的新草稿设为 2。新资产恢复任务只扫描新资产表，所以不会意外触碰历史草稿。

### 3.2 图片资产实体

新增 product_processing_media_assets。该表代表图片字节或一个有待物化的远程来源。

| 字段 | 说明 |
| --- | --- |
| id | UUID，asset_id，唯一资产身份 |
| workspace_id | 工作区隔离 |
| origin | remote_source、ai_generated、preview_upload、dimension_rendered |
| source_url | 仅记录远程来源审计信息 |
| source_identity_hash | 规范化来源 URL 的 SHA-256，用于物化前去重 |
| content_hash | 成功物化后图片字节的 SHA-256 |
| managed_path | 后端私有受管路径，永不返回前端 |
| content_type、byte_size、width、height | 已验证图片元数据 |
| status | pending、materializing、ready、retryable、failed |
| attempt_count、claim_token、claimed_at、next_retry_at | 持久化物化租约与重试 |
| error_code、error_message | 有限错误摘要 |

同一 URL 在同一工作区可以复用待下载资产记录。不同 URL 即使下载出相同内容，也可保留不同资产行以保留来源审计；物理文件以 content_hash 内容寻址，避免重复写盘。

### 3.3 图片业务绑定

新增 product_processing_media_bindings。该表描述哪个草稿、预检任务、SKU 或轮播槽位正在使用资产。

| 字段 | 说明 |
| --- | --- |
| id | UUID，binding_id |
| workspace_id、asset_id | 工作区与资产关联 |
| product_draft_id | 必填，草稿归属 |
| task_id、task_item_id | 可选，预检任务归属 |
| role | main、gallery、detail、sku、carousel、preview_upload、dimension、generated_detail |
| slot_id | 例如 carousel.hero、carousel.dimension_background |
| sku_id、variant_label | SKU 图专属归属 |
| sort_order | 稳定展示次序 |
| binding_key | 草稿、角色、SKU、槽位、来源、序号构成的稳定哈希 |
| active | 替换后旧绑定置为 0，保留审计 |

binding_key 在 workspace 内唯一，保证 handoff 重放、来源图重试和前端重复提交不会生成重复绑定。

### 3.4 兼容桥接

product_processing_preview_image_assets 增加可空 media_asset_id。新预检清单保留该表的任务和发布语义，但图片字节从 media_asset_id 指向的统一资产读取。旧预检行的 media_asset_id 为空，继续按原 managed_path/source_url 读取。

product_processing_dimension_assets 增加 source_media_asset_id。画布行保留自己的 managed_path，但它是导入时从统一资产复制出的不可变编辑快照。

## 4. 存储与状态

新目录：

    product-processing-assets/
      media-assets/
        workspaces/{workspace-hash}/
          {content-hash-prefix}/{content-hash}.{suffix}

新资产中心不复用 source-image-library、outputs/task_x/images、outputs/preview-assets 或 outputs/dimension-canvas；这些目录继续服务旧来源库、历史任务输出、旧预检兼容和画布快照。

状态机：

    pending -> materializing -> ready
                            -> retryable
                            -> failed
    retryable -> materializing

物化器使用 claim_token 与 claimed_at 做租约。启动恢复会重新领取 pending、到期 retryable 和租约过期的 materializing 行。

网络、连接、429、5xx、可恢复下载超时进入 retryable；非法 URL、非图片响应、超过字节上限、无法解码和内容校验失败进入 failed。ready 资产不得原地覆盖；来源变化时必须创建新资产和新绑定。

## 5. 安全和非目标

后端根据 asset_id 生成临时展示 URL。内容接口必须校验资产属于当前 workspace、资产有活跃 binding、binding 对应的草稿或任务属于当前调用者、请求签名未过期。

不得通过解析 /api、/pp-media 或绝对路径反推服务端文件。这一规则同时避免路径穿越、展示 URL 过期和不同存储目录混用。

本设计不处理 AI 供应商调度、模型超时、OCR 修复策略或任务并发。AI 产物仅需在生成成功后登记为 origin=ai_generated 的统一资产。

# 产品处理图片资产流转与接口契约

**依赖设计：** 2026-08-14-product-processing-media-asset-registry-design.md

**范围：** 新入池商品的选品确认、来源图物化、预检、AI 产物、尺寸画布、审核回写和最终发布。

## 1. 新链路

    每日选品确认
      -> durable handoff
      -> 草稿 + 资产 + 绑定 + handoff receipt
      -> 来源图物化
      -> 预检展示与 AI 产物登记
      -> 尺寸画布本地快照
      -> 审核回写 dimension binding
      -> 最终发布快照与导出

daily_selection_handoffs 与产品处理事务继续使用 outbox 风格：先创建持久 handoff；消费者在一个产品处理事务内创建草稿、资产、绑定和 receipt；成功后上游 handoff 才标记 consumed。重放相同 handoff 必须命中 receipt，不得产生重复草稿或绑定。

## 2. 选品确认到资产绑定

DailySelectionHandoff 的 images.main、images.gallery、images.detail 和 skus[].image_url 都必须被消费。

产品处理新增 create_draft_with_media。在同一个 SQLAlchemy session 中执行：

1. 创建 product_processing_drafts，media_contract_version=2；
2. 对每个远程 URL 调用 register_remote_asset；
3. 创建 main、gallery、detail、sku binding；
4. 写 product_processing_handoff_receipts。

| 输入 | role | sku_id | variant_label |
| --- | --- | --- | --- |
| candidate.main_image_url | main | 空 | 空 |
| source_image_urls[i] | gallery | 空 | 空 |
| source_detail_image_urls[i] | detail | 空 | 空 |
| source_variant_records[i].image_url | sku | source_variant_records[i].sku_id | 规格属性拼接值 |

同一 URL 被多个角色引用时，复用 asset_id、创建不同 binding。不能因 URL 去重而丢失 SKU 归属。

## 3. 物化与预检展示

MediaAssetService.materialize_pending 使用现有 fetch_public_image 下载远程图。下载后的字节必须执行图片格式、最大字节数、content_hash、content_type、width 和 height 校验后写入统一内容寻址目录。

新增接口：

    GET /api/product-processing/drafts/{draft_id}/media

新草稿返回：

    {
      "contract_version": 2,
      "draft_id": 301,
      "groups": {
        "main": [],
        "gallery": [],
        "detail": [],
        "sku": [],
        "carousel": [],
        "dimension": []
      }
    }

每个图片项：

    {
      "binding_id": "uuid",
      "asset_id": "uuid",
      "role": "sku",
      "slot_id": "",
      "sku_id": "5385255968977",
      "variant_label": "规格: 20mm 小麻将牌尺 4 根",
      "sort_order": 0,
      "status": "ready",
      "preview_url": "/api/product-processing/media-assets/uuid/content?...",
      "width": 1200,
      "height": 1200,
      "content_type": "image/jpeg",
      "error_code": "",
      "error_message": ""
    }

前端仅以 asset_id/binding_id 作为业务键。preview_url 仅供 img 标签加载，不能写回任务结果或当作画布来源。

| 状态 | 预检 UI | 尺寸画布 |
| --- | --- | --- |
| pending/materializing | 显示同步中 | 不允许导入 |
| retryable | 显示重试与错误摘要 | 不允许导入 |
| failed | 显示失败与人工上传入口 | 不允许导入 |
| ready | 可预览、替换、导入画布 | 允许导入 |

## 4. AI 生成产物

AI 生成成功的 GeneratedMedia 不再以任务输出路径或预览 URL 作为结果身份。服务端调用 register_generated_media，写入 origin=ai_generated 的 ready 资产，再创建 carousel、generated_detail 等 binding。

新任务结果写入：

    {
      "media_contract_version": 2,
      "image_manifest_v2": {
        "main_asset_id": "uuid",
        "carousel_asset_ids": ["uuid-a", "uuid-b", "uuid-c", "uuid-d"],
        "detail_asset_ids": ["uuid-e"],
        "semantic_asset_ids": {
          "carousel.hero": "uuid-a",
          "carousel.dimension_background": "uuid-d"
        }
      }
    }

过渡期可保留旧 URL 字段，但这些字段只能由 V2 asset_id 临时投影生成，业务逻辑不得读取它们。

## 5. 尺寸画布

画布导入只读取 image_manifest_v2 与关联 binding：

1. 读取 task_id、task_item_id、draft_id；
2. 查询 V2 manifest 的 ready 统一资产；
3. 用 MediaAssetService.read_ready_asset 读取受管资产字节；
4. 用 save_dimension_asset 在画布工作区生成内容寻址快照；
5. 记录 source_media_asset_id 与快照的哈希、尺寸、类型；
6. 只向浏览器返回画布快照 URL。

不得解析预览 URL；不得将 preview-assets 或任务输出目录直接写到画布 managed_path；不得由浏览器直连来源图。

尺寸渲染审核通过后：

1. 将渲染 bytes 登记为 origin=dimension_rendered；
2. 创建 role=dimension、slot_id=carousel.dimension_background binding；
3. 停用当前活跃的同槽位 dimension binding；
4. 更新 image_manifest_v2；
5. 递增 draft.preview_revision；
6. 已编辑但 source_preview_revision 旧于草稿版本的画布项目标记 conflict。

## 6. 最终发布与重试

发布快照保存 asset_ids、content_hashes 与 preview_revision。发布器从统一资产读取 ready 字节，按 content_hash 去重上传 COS，再在导出表格时生成最终公开 URL。

若草稿 preview_revision 或任一 asset content_hash 与快照不一致，发布任务标记 stale，不得导出混合版本图片。

新增接口：

    GET  /api/product-processing/media-assets/{asset_id}/content
    POST /api/product-processing/media-assets/{asset_id}/retry

retry 只允许 retryable 或 failed 的新资产。接口将其状态重置 pending、清空旧错误、递增 attempt_count，之后由物化器重新领取。

## 7. 混合版本

| 草稿版本 | 来源图、预检、画布路径 |
| --- | --- |
| 1 | 完整保留现有 source_images、preview assets、URL 与画布逻辑 |
| 2 | 仅使用统一资产中心与 V2 manifest |

旧草稿不得在访问、启动恢复、预检打开或画布导入时被自动升级。

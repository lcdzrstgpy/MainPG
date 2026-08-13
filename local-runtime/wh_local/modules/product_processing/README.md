# 产品处理后端模块

本模块是本地运行时中的独立业务模块，消费 `data_collection` 的每日选品交接，也支持浏览器采集、人工录入和 Excel/CSV 导入。它负责草稿池、预检与处理、来源图片登记、任务历史、恢复和结果下载。

## 目录职责

```text
product_processing/
├─ api/
│  ├─ router.py          # FastAPI 路由、请求头隔离、上传下载和 HTTP 错误映射
│  ├─ dimension_canvas_router.py  # 尺寸画布、批量、审核与通知路由
│  └─ schemas.py         # HTTP 请求模型
├─ domain/
│  ├─ physical_dimensions.py # 商品本体尺寸及证据来源；与物流包裹尺寸隔离
│  ├─ image_slots.py     # 语义轮播槽位与单槽覆盖
│  ├─ preview_images.py  # 稳定图片资产 ID、主图/轮播/详情完整清单与快照哈希
│  ├─ models.py          # 正式数据采集合同的输入兼容模型
│  ├─ handoff.py         # 正式 handoff payload_json 转换器
│  └─ workbooks.py       # Excel/CSV 读取及结果工作簿生成
├─ infrastructure/
│  ├─ database.py        # SQLAlchemy 引擎、SQLite WAL 与数据目录
│  ├─ orm.py             # 草稿、任务、交接回执、接收快照、源图和提示词表
│  ├─ repository.py      # 带 workspace 隔离的数据访问
│  ├─ preview_image_orm.py # 预审资产、内容哈希发布回执和最终化任务
│  ├─ preview_image_repository.py # CAS 保存、幂等键、发布/最终化租约
│  ├─ preview_image_files.py # JPEG/PNG/WebP 大小、像素与单帧质量门
│  ├─ dimension_canvas_repository.py # 画布版本、渲染快照和审核变更集
│  ├─ dimension_renderer.py # 无 AI 的 2000×2000 确定性尺寸图渲染
│  └─ assets.py          # 图片、来源图清单和任务输出文件的受控存储
├─ dimension_canvas_service.py # 导入、自动保存、最多 3 路渲染及冲突安全交回
├─ preview_image_service.py # 本地资产登记、延迟 COS 发布和版本安全工作簿
├─ service.py            # 用例、状态流转、幂等处理和跨模块字段映射
├─ requirements.txt      # 模块独立依赖清单
└─ 产品处理模块-需求字段与接口规范.md
```

依赖方向为 `api -> service -> domain/infrastructure`。HTTP 层不直接操作 ORM，文件和数据库基础设施也不反向引用 API。

## 跨模块入口

正式每日选品交接入口：

```http
POST /product-processing/intake/daily-selection/handoffs
X-Workspace-ID: <workspace_id>
```

旧整批 run 接口 `/intake/daily-selection` 仅为本地页面和早期调用兼容。正式字段、兼容裁决及当前上游 ACK 缺口见《产品处理模块-需求字段与接口规范.md》。

## 本地数据位置

- SQLite：`real-workbench/employee_workbench/product_processing/product_processing.sqlite3`
- 草稿图片：`real-workbench/employee_workbench/product_processing/draft-images/`
- 来源图片登记：表 `product_processing_source_images`
- 来源图清单：`real-workbench/employee_workbench/product_processing/source-image-library/`
- 任务结果：`real-workbench/employee_workbench/product_processing/outputs/task_<id>/`
- 尺寸画布：`real-workbench/employee_workbench/product_processing/outputs/dimension-canvas/workspaces/<workspace_hash>/`

SQLite 连接启用 WAL、外键、`synchronous=NORMAL` 和 30 秒 busy timeout。

## 尺寸画布

尺寸画布只消费已完成的产品处理结果，不增加模型调用。商品本体长宽高必须带明确单位和轴语义，包裹尺寸、估算值或冲突值不会自动用于标注。用户点击完成后才在本地渲染 2000×2000 图片，最多并行 3 张；渲染器按最终字体边界把有效标签做最小像素内移，确保完整文字留在 5% 安全区内。审核接受只覆盖 `carousel.dimension_background`，标题、描述及其他轮播图保持不变。

相关接口位于 `/product-processing/dimension-canvas`。资产、项目、批次、变更集与通知均按 `X-Workspace-ID` 隔离；编辑、渲染和交回审核只使用本地受管文件，不调用 COS 或模型。审核接受后，尺寸图进入统一预审图片清单，和其他最终保留图片一起在预审完成时发布。

## 预审图片与最终导出

AI 生成图、尺寸画布图和用户上传图首先只登记为本地稳定资产，不在生成或编辑阶段调用 COS。预审通过 `image_manifest_v2` 一次保存主图、轮播图、详情图和语义槽位；空详情图数组也会原样保留，不回退旧图片。

`POST /product-processing/tasks/<task_id>/preview/finalize` 是唯一最终化入口。它先用 preview revision 做整批 CAS，再只对清单内仍保留的内容哈希发布 COS；相同内容按工作区复用发布回执，失败重试不会重新上传已成功图片。只有全部 URL 都通过配置桶、公网 HTTPS 和匿名可读校验后，才生成该最终化任务专属的店小秘工作簿并开放下载。

本地预览资产使用工作区绑定的短时签名读取地址，不把受管绝对路径或通用静态目录当作授权。发布、素材化和工作簿任务都使用数据库租约与 owner token，迟到 worker 不能覆盖新任务结果。

## 测试

```powershell
$env:PYTHONPATH = 'local-runtime'
python -X utf8 -m pytest local-runtime/tests -k product_processing -q
```

测试覆盖页面主要接口、Excel/单品处理、正式 `data_collection` handoff、图片清单、显式空列表、COS 延迟发布、幂等重放、租约 fencing 和工作区隔离。

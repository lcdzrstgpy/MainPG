# 产品处理后端模块

本模块是本地运行时中的独立业务模块，消费 `data_collection` 的每日选品交接，也支持浏览器采集、人工录入和 Excel/CSV 导入。它负责草稿池、预检与处理、来源图片登记、任务历史、恢复和结果下载。

## 目录职责

```text
product_processing/
├─ api/
│  ├─ router.py          # FastAPI 路由、请求头隔离、上传下载和 HTTP 错误映射
│  └─ schemas.py         # HTTP 请求模型
├─ domain/
│  ├─ models.py          # 正式数据采集合同的输入兼容模型
│  ├─ handoff.py         # 正式 handoff payload_json 转换器
│  └─ workbooks.py       # Excel/CSV 读取及结果工作簿生成
├─ infrastructure/
│  ├─ database.py        # SQLAlchemy 引擎、SQLite WAL 与数据目录
│  ├─ orm.py             # 草稿、任务、交接回执、接收快照、源图和提示词表
│  ├─ repository.py      # 带 workspace 隔离的数据访问
│  └─ assets.py          # 图片、来源图清单和任务输出文件的受控存储
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

SQLite 连接启用 WAL、外键、`synchronous=NORMAL` 和 30 秒 busy timeout。

## 测试

```cmd
E:\study\python\python3.12.2\python.exe -m unittest local-runtime\tests\product_processing\test_product_processing_api.py -v
```

测试覆盖页面主要接口、Excel/单品处理、正式 `data_collection` handoff、幂等重放和工作区隔离。

# Profit Activity（利润活动）模块

## 模块职责

- 按 US、CO、EC 站点计算商品利润；费用梯度与旧工作台保持一致。
- 维护可版本化的利润配置；并发更新通过 `expected_revision` 防止覆盖。
- 对计算结果生成哈希，归档时拒绝“参数或配置已变化”的旧预览。
- 归档商品利润快照，并按“净利润 **或** 利润率达标”输出活动筛选结果。

## 文件职责与提交范围

```text
local-runtime/wh_local/modules/profit_activity/
├── __init__.py                       # 模块公开入口
├── README.md                         # 模块说明
├── SUBMISSION.md                     # Git/PR 提交说明
├── service.py                        # 用例编排、哈希校验、冲突处理
├── api/
│   ├── schemas.py                    # FastAPI/Pydantic 请求契约
│   └── router.py                     # APIRouter 与 HTTP 错误映射
├── domain/
│   ├── models.py                     # 不依赖框架的领域数据结构
│   └── engine.py                     # 利润公式与活动筛选规则
└── infrastructure/
    ├── database.py                   # SQLite WAL 连接工厂
    ├── orm.py                        # SQLAlchemy 表模型
    └── repository.py                 # 数据读写与设置版本控制
```

本次只提交上述模块路径与 `local-runtime/tests/profit_activity/` 下的测试；不要修改其他业务模块，也不要提交运行生成的 `.db`、`.db-wal`、`.db-shm` 文件。

## 主应用集成契约

由 `app` 负责人在应用装配处创建并挂载：

```python
from wh_local.modules.profit_activity import (
    create_profit_activity_router,
    create_profit_activity_service,
)

profit_activity_service = create_profit_activity_service()
app.include_router(create_profit_activity_router(profit_activity_service), prefix="/api/v1")

# 在 FastAPI lifespan 的 shutdown 段调用：
profit_activity_service.close()
```

默认数据库位于 `real-workbench/employee_workbench/data/profit_activity.db`；也可使用环境变量 `PROFIT_ACTIVITY_DATABASE_URL` 覆盖。SQLite 每个连接均启用 WAL、外键与 5 秒忙等待。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/profit-activity/settings` | 获取配置与版本号 |
| PUT | `/api/v1/profit-activity/settings` | 乐观锁更新完整配置 |
| POST | `/api/v1/profit-activity/calculate` | 获取利润预览和 `calculation_hash` |
| POST | `/api/v1/profit-activity/records` | 校验预览后归档利润记录 |
| GET | `/api/v1/profit-activity/records` | 查询归档记录 |
| POST | `/api/v1/profit-activity/filter-runs` | 生成活动筛选批次 |
| GET | `/api/v1/profit-activity/filter-runs/{run_id}` | 查询筛选批次及判定明细 |

运行依赖：Python 3.12+、FastAPI、Pydantic 2、SQLAlchemy 2、Uvicorn。

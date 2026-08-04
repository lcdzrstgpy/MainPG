# 数据采集模块目录迁移设计

## 目标

将每日选品后端完整模块改名并集中到 `data_collection`，便于与其他项目模块合并；迁移只改变代码组织和 Python 导入路径，不改变现有业务 HTTP 接口、数据库表或交接契约。

## 目标目录

```text
local-runtime/
├── wh_local/modules/data_collection/
│   ├── __init__.py
│   ├── contracts.py
│   ├── criteria.py
│   ├── provider.py
│   ├── normalizer.py
│   ├── budget.py
│   ├── collector.py
│   ├── filtering.py
│   ├── scoring.py
│   ├── repository.py
│   ├── handoff.py
│   ├── service.py
│   ├── routes.py
│   ├── migrations/001_daily_selection.sql
│   └── README.md
└── tests/data_collection/
    ├── fixtures/
    └── test_*.py
```

## 迁移规则

- 所有 Python 导入由 `wh_local.modules.daily_selection` 改为 `wh_local.modules.data_collection`。
- 测试目录由 `tests/daily_selection` 改为 `tests/data_collection`，夹具随测试目录迁移。
- 宿主可继续通过 `register_daily_selection_routes` 注册路由；HTTP 前缀保持 `/desktop/daily-selection`。
- SQLite 表及交接表保持 `daily_selection_*` 命名，确保已有数据、草稿池消费者和数据库迁移兼容。
- 迁移完成后删除旧 `daily_selection/` 源码与测试目录，不保留双实现或兼容转发目录。
- 文档、测试、README 中的模块路径全部更新；真实 OneBound 协议与密钥安全逻辑不修改。

## 验收

- `rg 'wh_local\.modules\.daily_selection' local-runtime` 无匹配。
- `daily_selection/` 源码和测试目录不再存在，`data_collection/` 完整包含模块与测试。
- `conda run -n base python -m pytest local-runtime/tests/data_collection -q` 通过。
- 路由前缀、数据库表名、交接表名和真实 API 行为不变。

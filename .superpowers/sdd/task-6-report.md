# 任务 6 实施报告：批次持久化与草稿交接记录

## 状态

已完成。实现范围仅包含任务指定的 migration、repository、handoff、测试与本报告；未修改前端、`product_drafts`、宿主应用或其他业务模块。

## 交付内容

- `local-runtime/wh_local/modules/daily_selection/migrations/001_daily_selection.sql`
  - 创建 `daily_selection_runs`、`daily_selection_candidates`、`daily_selection_feedback`、`daily_selection_provider_budgets`、`daily_selection_handoffs`。
  - 使用复合主键、外键、交接唯一约束，以及 workspace/run 查询索引。
  - SQLite 启用外键与 WAL。
- `local-runtime/wh_local/modules/daily_selection/repository.py`
  - 实现 `save_run`、`list_runs`、`get_run`、`record_feedback`、`confirm_candidates`。
  - 写操作使用 `BEGIN IMMEDIATE`，读快照使用显式事务；所有查询均绑定 workspace。
  - 完整候选保存至 `raw_candidate_json`，摘要字段拆列；Pydantic 声明型 Decimal 与动态映射中的 Decimal 均可等价回读。
  - 支持文件型临时库和 `:memory:` SQLite；共享内存 keeper 保证多连接事务可见同一 schema。
  - criteria、metadata、feedback details 在持久化前递归脱敏，拒绝二进制和非有限数字。
  - 反馈将候选标记为 `rejected`，但保留原始图片、SKU、属性和来源证据。
- `local-runtime/wh_local/modules/daily_selection/handoff.py`
  - 定义 Pydantic `DailySelectionHandoff`。
  - 交接载荷包含主图、商品图、详情图、SKU 图、SKU、属性、来源证据、来源标识与选品元数据。
  - Decimal 以精确字符串写入 JSON；幂等键由 workspace/run/candidate 的无歧义身份摘要生成。
- `local-runtime/tests/daily_selection/test_repository_handoff.py`
  - 覆盖迁移表/索引、内存 SQLite、保存与等价回读、Decimal 来源键冲突、workspace 列表隔离、反馈、脱敏、跨 workspace 拒绝、确认幂等、原子回滚、重存快照一致性及已有交接保护。

## TDD 证据

1. 首次 RED：`repository` 尚不存在，测试收集以 `ModuleNotFoundError` 失败。
2. 首次 GREEN 迭代发现动态映射中的 Decimal 被普通 JSON 降级为字符串；增加类型标记后候选模型等价回读。
3. 新增反馈状态与通用元数据脱敏测试，确认 RED 后补最小实现。
4. 新增重存批次快照一致性测试，确认旧候选残留的 RED 后实现事务内替换，并保护已有反馈/交接不被覆盖。
5. 独立审查发现 `:memory:` schema 生命周期和 Decimal 哨兵键冲突；分别补 RED 后改为共享内存 URI 与顶层 Decimal 路径清单编码。

## 验证

- 环境：Conda `base`，Python 3.12。
- 定向：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_repository_handoff.py -q` → `12 passed`。
- 完整每日选品：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `103 passed`。
- 编译：`conda run -n base python -m compileall -q .../repository.py .../handoff.py` → 退出码 0。
- 全部测试使用临时 SQLite 与本地数据；本任务实现没有网络客户端、外部请求或宿主依赖。
- 生产代码未引用或创建 `product_drafts`；测试显式断言数据库中不存在该表。

备注：Conda base 未安装 `ruff`，因此未把 lint 作为完成依据；编译与完整测试均已通过。

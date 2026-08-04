# 任务 5：筛选和评分，但不丢弃来源证据

## 范围

- 新增本地 `filtering.py`：按平台/商品 ID 去重，ID 缺失时按规范来源链接去重；价格、MOQ、风险和缺主图硬筛选；所有被筛候选保留来源、图片、SKU、证据及精确原因。
- 新增本地 `scoring.py`：确定性的 100 分 `Decimal` 评分，包含 `supply`、`match`、`evidence`、`freshness`，并拆分主图、图集、详情图、属性、完整 SKU 的证据加分。
- 新增无网络测试，覆盖重复、价格/MOQ、五类风险词、缺主图、风险不可确认、评分稳定性和有序输出。

## TDD RED 证据

在生产模块不存在时，先执行：

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_filtering_scoring.py -q
```

结果：预期的 `ModuleNotFoundError: No module named 'wh_local.modules.daily_selection.filtering'`。

在已有基础实现后，新增组合排序接口的失败测试，再执行同一命令：

```text
ImportError: cannot import name 'filter_and_score_candidates'
```

随后新增“已确认风险候选降级”与“重复风险候选保留风险审计”回归，均先各出现一项精确断言失败，再以最小实现转绿。

## GREEN 与验证证据

- 定向：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_filtering_scoring.py -q` → `9 passed in 0.07s`
- 编译：`conda run -n base python -m compileall -q local-runtime/wh_local/modules/daily_selection/filtering.py local-runtime/wh_local/modules/daily_selection/scoring.py` → 退出码 0
- 全量每日选品：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `90 passed in 0.13s`

所有验证均为本地 Python/fixture 测试，未调用 OneBound 或其他外部 API。

## 实现说明

- `FilteringResult` 同时公开通过、筛掉和可确认的候选；筛掉条目以 `status="filtered"` 保留完整原始 Pydantic 候选字段，并仅追加原因和风险标签。
- `exclude_risks=False` 时风险候选可供人工审核，但绝不会出现在 `confirmable`；若输入状态已是 `confirmed`，会降为 `candidate` 并留下 `risk_not_confirmable` 原因。
- 评分不读取当前时间或外部状态；`captured_at` 只作为固定输入证据，因此相同输入始终产生相同 `Decimal` 分数和排序。

## 提交

将使用精确路径提交以下文件，避免纳入工作区中既有的 staged 删除和无关文件：

- `local-runtime/wh_local/modules/daily_selection/filtering.py`
- `local-runtime/wh_local/modules/daily_selection/scoring.py`
- `local-runtime/tests/daily_selection/test_filtering_scoring.py`
- `.superpowers/sdd/task-5-report.md`

## 复审修复：真实 offer ID 与 URL 回退身份

### RED 证据

新增“一个记录有真实 `offer_id`，另一个以相同规范来源链接作为 ID 缺失回退”回归后执行：

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_filtering_scoring.py -q
```

结果：`1 failed, 9 passed`。回退记录错误地留在通过列表，证明先前的单一身份键无法将真实 ID 记录登记的来源链接与 URL 回退记录关联。

### 修复与验证

- 去重同时维护 `source_platform + real offer_id` 与 `source_platform + canonical source_url` 两套集合；真实 ID 仍优先报告 `duplicate_source_offer`，URL 回退或同来源链接报告 `duplicate_source_url`。
- 测试夹具现在为不同 `offer_id` 生成不同来源链接，避免把本应独立的商品误构造成来源链接相同的重复项；刻意的重复用例仍显式使用共享链接。
- 定向 GREEN：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_filtering_scoring.py -q` → `10 passed in 0.09s`
- 完整回归：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `91 passed in 0.14s`；`compileall filtering.py` 退出码 0。

# 任务 4：有预算约束的采集编排报告

## 范围

- 新增 SQLite 日预算账本：以工作空间、单向 Provider 凭据指纹和上海日期作为主键，并以 `BEGIN IMMEDIATE` 原子预留调用额度。
- 新增注入式采集编排：精确关键词、版本化本地发散词、图片采集、候选去重/详情补全、错误审计、调用统计和状态判定。
- 新增临时 SQLite 与 Fake Provider 测试；所有测试均无网络调用。

## RED 证据

在生产模块尚未创建时执行：

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_budget.py local-runtime/tests/daily_selection/test_collector.py -q
```

结果：预期失败，`ModuleNotFoundError: No module named 'wh_local.modules.daily_selection.budget'`。这证明并发预算与采集编排测试先于实现存在。

## GREEN 与验证证据

- 定向：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_budget.py local-runtime/tests/daily_selection/test_collector.py -q` → `8 passed in 0.06s`
- 全部每日选品回归：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `52 passed in 0.07s`

定向覆盖：同日同工作空间/Provider 并发扣减、精确关键词、发散审计、上传后图搜、参考图关联、空结果、部分 Provider 失败、详情失败保留候选、去重后详情上限和图片操作前预算耗尽。

## 提交

- 实现与测试：`12d083d feat(daily-selection): orchestrate bounded collection`
- 采用 `git commit --only -- <四个任务文件>`，避免把工作区中已有的无关 staged 删除纳入提交。

## 疑虑 / 后续注意

- 图搜由现有 Provider 的 `search_by_image` 封装下载、上传和检索；编排层按这三个已审计 HTTP 操作预留预算。若未来 Provider 拆分或增加该流程的外部调用，应同步调整预算成本常量。
- `OneBound1688Provider` 不公开凭据指纹，因此其宿主需要在创建 collector 时传入配置凭据或指纹；测试 Fake Provider 提供同等的单向指纹。账本不写入明文凭据。

## 审查修复（任务 4 补充）

### RED 证据

在修复前新增回归后执行：

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_budget.py local-runtime/tests/daily_selection/test_collector.py -q
```

结果：`7 failed, 8 passed`。失败精确覆盖：预算状态缺少预留结果且允许非 SHA-256 指纹、图片下载/上传失败仍把图搜记为已调用且账本预扣 3 次未结算、反序图片审计未被拒绝、原始凭据可作为账本键，以及候选详情仍按输入顺序而非分数。

### 修复内容

- `BudgetState` 增加 `reservation_granted`，使本次预留成功与“还有下一次可用额度”语义分离；当余额为零时 `allowed=False`。
- 预算账本仅接受并规范化 SHA-256 十六进制摘要；collector 也在构造时拒绝原始凭据值。
- 图片、关键词和详情调用均按审计条目数结算预留额度；图片下载、上传、图搜失败会释放未实际发生的预留额度，结果 `api_calls` 与账本已用量一致。
- 图片审计必须是 `download_reference_image → upload_img → item_search_img` 的合法前缀；成功图搜必须具备完整序列，反序记录被拒绝。
- 详情候选按 Pydantic 契约的 `selection_score` 降序排序；同分保持来源顺序。

### GREEN 与完整验证

- 定向：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_budget.py local-runtime/tests/daily_selection/test_collector.py -q` → `15 passed in 0.10s`
- 全量每日选品：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `74 passed in 0.12s`

这些测试只使用本地 Fake Provider 与临时 SQLite，未进行外部网络/API 调用。

# 任务 1：固定领域契约与采集条件报告

## 实现结果

- 新增 `DailySelectionCriteria`，覆盖关键词与图片采集模式、平台、范围、价格、MOQ、数量和 API 预算校验。
- 新增候选、图片引用、SKU、API 证据和结构化错误契约；商品主图、图集、详情图、SKU 图、来源属性、评分、理由和证据均为独立字段。
- 契约仅接受图片 URL 与元数据；二进制数据会被拒绝。映射中的 API Key、Secret、Token、Cookie、Session 和 Authorization 类字段会被移除。

## TDD 证据

### RED（生产模块不存在）

命令：

```sh
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_contracts.py local-runtime/tests/daily_selection/test_criteria.py -q
```

结果：退出码 1；测试收集失败，两个预期错误均为 `ModuleNotFoundError`：

- `wh_local.modules.daily_selection.contracts`
- `wh_local.modules.daily_selection.criteria`

另补充结构化错误模型时，先运行同一命令，退出码 1；预期错误为：

```text
ImportError: cannot import name 'DailySelectionError'
```

### GREEN

命令：

```sh
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_contracts.py local-runtime/tests/daily_selection/test_criteria.py -q
```

结果：`11 passed in 0.03s`，退出码 0。

额外验证：

```sh
conda run -n base python -m compileall -q local-runtime/wh_local/modules/daily_selection
```

退出码 0。

## 本任务文件

- `local-runtime/wh_local/modules/daily_selection/contracts.py`
- `local-runtime/wh_local/modules/daily_selection/criteria.py`
- `local-runtime/tests/daily_selection/test_contracts.py`
- `local-runtime/tests/daily_selection/test_criteria.py`
- `.superpowers/sdd/task-1-report.md`

## 自审

- 需求中列出的所有 `DailySelectionCriteria` 与 `DailySelectionCandidate` 字段均已包含。
- 关键词、参考图、价格区间、API 预算与采集范围的失败路径均由测试覆盖。
- 未创建或修改 `web_frontend/` 文件。
- 未将用户已有的暂存删除或其他未跟踪文件纳入本任务暂存范围。

## 疑虑

无。

## 提交

实现：`4e29a0b feat: add daily selection contracts and criteria`。

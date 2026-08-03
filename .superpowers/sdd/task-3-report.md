# 任务 3：1688 搜索与详情标准化报告

## 范围

- 新增 `local-runtime/wh_local/modules/daily_selection/normalizer.py`：纯本地的搜索响应标准化、详情补全和递归脱敏。
- 新增 `local-runtime/tests/daily_selection/test_normalizer.py`：仅读取已有脱敏 1688 JSON 夹具；未执行真实网络请求。

## TDD 证据

1. RED（实现文件不存在）：

   ```text
   conda run -n base python -m pytest local-runtime/tests/daily_selection/test_normalizer.py -q
   ModuleNotFoundError: No module named 'wh_local.modules.daily_selection.normalizer'
   ```

2. GREEN（最小实现后）：

   ```text
   conda run -n base python -m pytest local-runtime/tests/daily_selection/test_normalizer.py -q
   4 passed in 0.02s
   ```

## 验证

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection -q
42 passed in 0.04s
```

覆盖的行为包括：搜索 ID/规范链接/核心字段抽取，详情字段和证据合并，URL 去重与 HTTP(S) 限制及图片上限，缺失字段记录，以及递归移除敏感字段与二进制值。

## 提交

已创建 `feat(daily-selection): normalize 1688 offers` 提交，仅包含本报告、normalizer 和对应测试；最终提交标识由交付状态提供。

## 疑虑 / 后续注意

- OneBound 的真实字段命名可能存在供应商版本差异；实现为常见别名提供了兼容路径，但任何新增字段仍应先补充脱敏夹具和 RED 测试。
- 详情缺少时保持搜索字段和原有 API evidence；不生成猜测值。source_title 是候选契约的必填字段，因此无标题的搜索项会被跳过。

## 审查修复（第 2 轮）

- URL 域名检查已收紧为仅允许 `1688.com` 或 `*.1688.com`；`not1688.com` 这类后缀伪装域名不再被标准化为来源链接。
- 详情补全的最终 `price_cny` 和 `min_order_quantity` 已参与 `missing_capture_fields` 重算，因此搜索阶段缺失、详情阶段补齐后不会继续标记为缺失。

### TDD 证据

RED：新增两条回归测试后，使用 Conda base 执行指定命令得到 `2 failed, 4 passed`：一条暴露 `not1688.com` 被接受，另一条暴露详情价格/MOQ 已存在但仍在缺失字段中。

GREEN：

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_normalizer.py -q
6 passed in 0.03s
```

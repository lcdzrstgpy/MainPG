# 每日选品契约纠偏实施报告

日期：2026-08-04  
分支：`mo`  
范围：仅 `local-runtime/wh_local/modules/daily_selection/`、对应 daily-selection 测试与本报告。

## 结论

已完成已确认的三项契约纠偏：

1. 脱敏规则改为精确字段名与明确凭据语法，不再因为普通英文包含 `key`、`token`、`session`、`cookie`、`secret` 等子串而误删商品数据。
2. `contracts.py` 与 `criteria.py` 的领域契约迁移到 Pydantic v2；价格、运费、选品评分和 SKU 价格均使用 `Decimal`，并通过 `model_dump(mode="json")` 稳定序列化为十进制字符串。
3. 搜索和详情原始回包分别保存在 `raw_payload["search_payload"]` 与 `raw_payload["detail_payload"]`；详情补全不再覆盖搜索快照，两份快照都递归脱敏且不保留二进制。

任务 4 的预算与采集编排保持兼容；collector 仅将 dataclass 的 `__dict__` 复制方式换成 Pydantic 的 `model_dump(mode="python")`，并在只验证详情限制的测试中显式指定 `selection_scope="exact"`，避免计划默认值 `divergent` 改变该测试的关注点。

## 根因调查

### 脱敏误伤

- `contracts.py` 与 `normalizer.py` 使用 `marker in normalized_field_name` 判断敏感字段。
- `provider.py` 同时使用字段子串匹配和 `_SENSITIVE_VALUE.search(text)`；只要文本出现普通英文单词就把整段替换为 `[redacted]`。
- 因此 `monkey`、`secretary`、`tokenizer`、`sessional`、`cookie jar` 等合法字段或文本会被误删。

### 数值契约偏差

- 领域对象是 frozen dataclass，缺少 Pydantic v2 的统一嵌套校验和 JSON dump 契约。
- `normalizer._number_value()` 显式调用 `float()`，金额字符串 `19.90` 在进入模型前已丢失十进制表达与精确算术语义。

### 原始快照覆盖

- 搜索标准化时 `raw_payload=cleaned_search_response`。
- 详情补全时重新构造候选并赋值 `raw_payload=cleaned_detail_response`，搜索原始回包因此不可恢复。

### Criteria 与计划不一致

- 原默认值为 `target_count=20`、`max_api_calls=60`、`detail_count=20`，且 `selection_scope="exact"`。
- `collection_mode` 还接受未在计划中的 `keywords` 别名，`exclude_risks` 与 `site` 也未采用计划契约。

## TDD 证据

### RED

先只修改测试，再执行：

```text
conda run -n base python -m pytest \
  local-runtime/tests/daily_selection/test_contracts.py \
  local-runtime/tests/daily_selection/test_criteria.py \
  local-runtime/tests/daily_selection/test_normalizer.py \
  local-runtime/tests/daily_selection/test_provider.py -q
```

结果：`13 failed, 39 passed`。

失败原因均为目标行为尚未实现：模型仍是 dataclass、金额仍是 float、普通英文被脱敏、计划默认值/字面量未生效、搜索快照被详情覆盖、Provider 把普通英文整段替换为 `[redacted]`。失败不是导入、拼写或夹具错误。

提交前审查又补充了裸 `key=value` 用例，并先运行：

```text
conda run -n base python -m pytest \
  local-runtime/tests/daily_selection/test_contracts.py::test_contract_sanitization_preserves_ordinary_english_and_redacts_explicit_credentials \
  local-runtime/tests/daily_selection/test_normalizer.py::test_recursive_sanitization_uses_exact_field_names_and_preserves_ordinary_text -q
```

结果：`2 failed`。失败明确显示 `key=must-not-escape` 仍存在，随后才把裸 `key` 加入显式凭据片段匹配。

同一审查还补充了“实际配置的 `api_key` 出现在 `base_url` path”用例。定向测试先得到 `1 failed`（Provider 未拒绝，`safe_summary()` 存在泄漏可能），随后才调整初始化顺序，让 URL 校验同时接收实际 `api_key`/`api_secret`，回跑得到 `1 passed`。

只读代码审查指出 Mapping 可接受任意 Python 对象以及 URL 仅检查 scheme/netloc 两项缺口。为此先增加不支持 JSON 的 `object()`/`set`、含空格主机和非法端口的回归测试，定向运行得到 `6 failed`；随后才限制递归映射值为安全 JSON 标量/有限 `Decimal`，并通过 Pydantic `HttpUrl` 适配器统一校验 URL。相同定向测试回跑为 `6 passed`。

### GREEN

第一轮同组回归测试：`52 passed in 0.09s`。  
裸 `key=value` 最小修复后定向测试：`2 passed in 0.07s`。  
最新完整 daily-selection 测试：

```text
conda run -n base python -m pytest local-runtime/tests/daily_selection -q
```

结果：`67 passed in 0.12s`。

另执行：

```text
conda run -n base python -m compileall -q \
  local-runtime/wh_local/modules/daily_selection \
  local-runtime/tests/daily_selection
```

结果：退出码 0，无编译错误。

所有测试使用本地夹具、Fake Provider 和本地 SQLite；未调用外部 API，也未进行网络访问。

## 实现细节

### 精确脱敏

- 精确敏感字段集合包括 `key`、`api_key`/`apikey`、`api_secret`、`secret`、`access_token`、`token`、`cookie`、`session`、`authorization`；仅对大小写和 `-`/`_` 做规范化后精确比较。
- 文本只替换显式 `key=value`/`key:value` 类凭据片段、`Bearer <value>` 和 Provider 实际配置的 `api_key`/`api_secret` 值。
- `cookie jar`、`tokenizer`、`sessional`、`secretary`、`monkey` 等字段和值保留。
- Provider URL 校验复用同一显式凭据语法判断，仍拒绝在 base URL path 中夹带 `api_key=...` 的配置。

### Pydantic v2 与 Decimal

- `DailySelectionError`、`ImageReference`、`SourceVariantRecord`、`ApiEvidence`、`DailySelectionCandidate`、`DailySelectionCriteria` 均为 frozen Pydantic v2 模型，禁止额外字段。
- 保留 HTTP(S) URL 校验、嵌套 SKU/evidence 模型解析、递归敏感字段清理与二进制拒绝。
- URL 通过 Pydantic `HttpUrl` 统一验证，拒绝空格主机、非法端口等仅靠 `urlparse().netloc` 无法拦截的畸形输入。
- Mapping 字段只接受能稳定 JSON dump 的递归值；任意对象、set、非有限数字会在契约入口被拒绝。
- `price_cny`、`freight_cny`、`selection_score`、`SourceVariantRecord.price_cny`、Criteria 的 `min_price`/`max_price` 为 `Decimal`。
- 验证了 `Decimal("0.1") + Decimal("0.2") == Decimal("0.3")`，SKU/运费/评分 JSON 输出为稳定十进制字符串。
- Criteria 采用计划契约：`selection_scope="divergent"`、`target_count=30`、`max_api_calls=50`、`detail_count=10`、`exclude_risks=True`、`site="US"`；平台仅 `1688`，采集模式仅 `keyword`/`image`，API 预算仍为 `1..60`。

### 双来源快照

- 搜索候选立即建立 `{"search_payload": ..., "detail_payload": None}`。
- 详情补全保留原 `search_payload` 并独立写入清洗后的 `detail_payload`。
- 两层快照测试分别加入只存在于搜索/详情的标记，确认互不覆盖；敏感字段与 bytes 不会进入任一快照。

## 修改文件

- `local-runtime/wh_local/modules/daily_selection/contracts.py`
- `local-runtime/wh_local/modules/daily_selection/criteria.py`
- `local-runtime/wh_local/modules/daily_selection/provider.py`
- `local-runtime/wh_local/modules/daily_selection/normalizer.py`
- `local-runtime/wh_local/modules/daily_selection/collector.py`
- `local-runtime/tests/daily_selection/test_contracts.py`
- `local-runtime/tests/daily_selection/test_criteria.py`
- `local-runtime/tests/daily_selection/test_provider.py`
- `local-runtime/tests/daily_selection/test_normalizer.py`
- `local-runtime/tests/daily_selection/test_collector.py`
- `.superpowers/sdd/contract-correction-report.md`

未修改 `budget.py`、`test_budget.py`、其他后端模块或前端。

## 工作区保护

开始前已确认工作区存在与本任务无关的 staged deletion 和同路径 untracked 恢复文件。实施期间没有执行 reset、clean、checkout、普通全量 add 或删除命令。提交采用精确 pathspec/`git commit --only`，不把无关 staged 内容带入本提交。

## 剩余疑虑

- `Decimal` 的 JSON 形式按 Pydantic v2 标准为字符串，而不是 JSON number；这是避免重新引入二进制浮点误差的有意契约选择。后续持久化/路由层应直接使用 `model_dump(mode="json")`，不要再转成 float。
- `raw_payload["detail_payload"]` 在尚未详情补全时为 `None`；补全后必为独立、已脱敏映射。调用方应按是否为 `None` 判断详情是否已采集。

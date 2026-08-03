# 任务 2：OneBound 1688 Provider 报告

## 范围

仅新增 `daily_selection` 的 OneBound 1688 Provider、六个 API fixture 与
`test_provider.py`。没有改动前端、淘宝逻辑、任务 1 的契约或其他模块。

## TDD 证据

### RED 1：Provider 行为

命令：

```bash
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_provider.py -q
```

实际结果：测试收集失败，`ModuleNotFoundError: No module named
'wh_local.modules.daily_selection.provider'`。这确认关键词搜索、图片上传后图搜、超时和限流测试在实现前均没有 Provider 可通过。

### GREEN 1：最小 Provider

同一命令实际结果：`11 passed in 0.04s`。随后加入对本地/私有图像 URL 的回归测试：

```bash
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_provider.py -q
```

实际 RED：`1 failed, 11 passed`，失败原因为 `127.0.0.1` 被错误当作上游失败，而不是在请求前拒绝。

实际 GREEN：`12 passed in 0.03s`。

### RED 2：审查后的安全与错误分类

同一命令实际结果：`3 failed, 13 passed`。失败项为：

1. 允许带 userinfo/query 的 `base_url`，可能由 `safe_summary()` 暴露凭据；
2. 字符串配置 `enabled="false"` 被当作真值；
3. HTTP 500 携带 `code=2000` 被错误归为无结果。

### GREEN 2：最终回归

```bash
conda run -n base python -m pytest local-runtime/tests/daily_selection/test_provider.py -q
git diff --check
```

实际结果：`16 passed in 0.03s`，且 `git diff --check` 无输出、退出码为 0。

## 变更

- `local-runtime/wh_local/modules/daily_selection/provider.py`
  - 注入式 HTTP 传输边界；生产默认使用标准库，测试通过 `FakeTransport`，不联网。
  - 只实现 1688 的 `item_search`、`upload_img`、`item_search_img`、`item_get`。
  - 返回脱敏响应、结构化错误和每次 HTTP 调用的 `ApiEvidence` 审计链。
  - 处理成功、`2000` 空结果、认证、参数、额度、限流、超时及未知上游错误。
  - 图片只在内存中限长读取、Base64 编码后上传，原始 bytes 立即删除；拒绝本地/私有 URL 和重定向。
  - `safe_summary()` 不输出凭据；拒绝含凭据/query/fragment 的 `base_url`，并要求 `enabled` 为布尔值。
- `local-runtime/tests/daily_selection/test_provider.py`
  - 覆盖关键词、图片完整流程、超时、限流、空结果、详情、错误映射、禁用状态、大小限制、私有地址、重定向与配置防泄露。
- `local-runtime/tests/daily_selection/fixtures/`
  - 新增六个 OneBound 成功/空结果/限流 fixture。

## 自审

独立只读审查首先指出 SSRF、`base_url` 脱敏、布尔配置和 HTTP 状态优先级问题；均已以失败测试复现并在最终 GREEN 中闭合。审计元数据与响应通过敏感键过滤，错误不回显请求凭据或图片字节。

## 提交

- `8d07872 feat(daily-selection): add OneBound 1688 provider`
- `01e7c43 fix(daily-selection): reject local reference images`
- `b8199d6 fix(daily-selection): harden provider configuration`

本报告由随后的精确提交纳入版本控制。

## 疑虑

无阻塞疑虑。标准库传输已禁止自动重定向；对于域名在 DNS 解析后指向私有地址的部署环境，建议在传输层增加组织网络策略或受控出站代理作为纵深防御。

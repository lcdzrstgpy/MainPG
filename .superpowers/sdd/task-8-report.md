# 任务 8 实施报告：端到端验收与交接文档

## 状态与范围

已完成任务 8 限定范围，只新增：

- `local-runtime/tests/daily_selection/test_end_to_end.py`
- `local-runtime/wh_local/modules/daily_selection/README.md`
- `.superpowers/sdd/task-8-report.md`

未修改生产 Python、宿主 `app.py`、前端、迁移、其他模块或任何真实 Provider 配置；未读取、写入或输出密钥，也未发起外部网络/API 请求。

## 端到端验收

`test_end_to_end.py` 通过测试 FastAPI app 注册真实六路由接线，使用 Fake Provider、临时文件型 SQLite 和 `TestClient`，并 monkeypatch `socket.create_connection`、`socket.getaddrinfo`、`socket.socket.connect` 与 `connect_ex`，任何 DNS/TCP 连接尝试都会直接失败。

关键词模式覆盖：

- 精确关键词搜索后调用商品详情；
- 预览经过路由、service、collector、normalizer、filter/score 和 repository；
- HTTP 返回与批次详情回读等价；
- 候选具备主图、商品图集、详情图、SKU ID/属性/图片/价格/MOQ；
- SQLite 保存一条 run 和一条完整 candidate 快照；
- 同一候选重复确认返回相同 handoff，表内始终只有一条记录；
- handoff payload 的 main/gallery/detail/SKU 图片和 SKU 记录与候选一致。

图片模式覆盖：

- Fake Provider 审计并断言参考图下载、上传、图搜、详情的固定顺序；
- 图片模式不触发关键词搜索，metadata 记录 `search_calls=0`、`image_search_calls=1`、`detail_calls=1`、`api_calls=4`；
- 参考图条件与图搜派生标题保存在批次快照；
- 候选主图、商品图、详情图和 SKU 字段写入 SQLite，并完整进入幂等 handoff；
- 重复确认仍只产生一条 `daily_selection_handoffs` 记录。

## 测试先行记录

任务 8 的验收测试先于任何任务 8 生产代码编写。首次加入关键词验收后执行定向测试得到 `1 passed`；补充图片验收后得到 `2 passed`。由于任务 7 已完成所需路由/服务接线，验收测试没有暴露生产缺口，因此没有制造人为 RED，也没有为追求失败而改动已正确的生产代码。任务 8 最终只新增验收与文档产物，符合 brief 的“当前接口契约已完成；验证完整流程”前提。

## README 交接内容

模块 README 记录：

- 建议的宿主环境变量到 OneBound 配置键映射，以及密钥只能来自安全环境/密钥库、不得写入文件；
- `register_daily_selection_routes` 的宿主注入边界和六条路由；
- 参考图、主图、商品图、详情图和 SKU 图的 URL-only 保存语义与图片代理安全要求；
- 迁移创建的五张表、默认预算适配器额外使用的 `daily_selection_api_budget`，以及不拥有 `product_drafts` 的边界；
- `DailySelectionHandoff` 幂等键、状态、payload 字段和下游消费者职责；
- 已授权低额度真实单次验收中关键词/详情和上传后图搜成功；本任务不重复真实调用。

独立审查发现迁移中的 `daily_selection_provider_budgets` 并非默认路由实际使用的预算账本；`SQLiteDailyApiBudget` 会另建并读写 `daily_selection_api_budget`。已修正 README，并在两条端到端测试中分别断言真实账本已用次数为 2 和 4，同时证明迁移预留表存在但未被默认链路写入。任务范围禁止修改生产模块，因此本提交如实记录这两张表并存的现状，供后续生产迁移统一处理。

## 验证证据

环境：Conda `base`，Python 3.12。

- 定向（新增关键词验收）：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_end_to_end.py -q` → `1 passed in 0.22s`。
- 定向（完整关键词+图片及预算表事实验收）：同一命令 → `2 passed in 0.24s`。
- 每日选品全量：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `121 passed in 0.42s`。
- 编译：`conda run -n base python -m compileall -q local-runtime/wh_local/modules/daily_selection local-runtime/tests/daily_selection` → 退出码 0。

所有自动化验证均使用本地 Fake/fixture，不调用 OneBound、DNS、HTTP 客户端或其他外部服务。

## 提交

将只精确暂存并提交本报告开头列出的三个新增文件，避免纳入工作区中其他任务的已暂存删除、未跟踪文件、缓存或无关变更。提交信息：

```text
test: verify daily selection backend workflow
```

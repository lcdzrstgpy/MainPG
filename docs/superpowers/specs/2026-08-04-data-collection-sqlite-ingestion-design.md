# 数据采集 SQLite 入库链路设计

## 目标

将现有数据采集模块产生的新数据按既定字段写入数据库成员已经完成的统一 SQLite 数据库。保持现有接口、表结构和字段语义不变，不导入 `api-cache` 历史调试缓存。

## 数据流

### 1688

关键词采集、图片采集和相似链接采集继续使用同一条链路：Provider 返回原始响应，Normalizer 转换为 `DailySelectionCandidate`，筛选评分完成后由 `DailySelectionService` 调用 `DailySelectionRepository.save_run()`。一次事务写入 `daily_selection_runs` 和 `daily_selection_candidates`；候选的稳定查询字段写入独立列，完整规范化对象写入 `raw_candidate_json`。

### Temu

浏览器插件采集结果通过 `DataCollectionPluginQueue.receive_result()` 接收。结果脱敏后，在同一事务中更新 `data_collection_plugin_commands.status`、`result_json` 和 `updated_at`。Temu 结果不转换为 1688 候选，也不新增表。

## 应用接线

统一数据库初始化、1688 Repository、API 预算和 Temu 插件队列必须使用宿主传入的同一个 `database_path`。本任务只修复实际检查中发现的断点，不重构数据库基座或采集领域模型。

## 错误与事务

- 1688 规范化、筛选或保存失败时，请求失败，不返回未落库的成功结果。
- 批次与全部候选在同一事务内写入，避免只写入批次或部分候选。
- Temu 非法状态、无权会话或非法结果继续使用现有校验；终态结果保持幂等。
- 所有持久化数据继续执行现有敏感字段过滤和文本脱敏。

## 验收

使用 Fake Provider、FastAPI `TestClient` 和临时 SQLite，禁止外部网络访问。分别验证：

1. 1688 关键词采集完成后，批次与完整候选字段可从 SQLite 回读。
2. 1688 图片采集和相似链接采集走同一 Repository 入库链路。
3. Temu 插件成功回传后，状态和脱敏结果写入 `result_json`。
4. 应用初始化、采集服务和插件队列使用同一个数据库文件。
5. 现有数据库迁移和相关测试无回归。

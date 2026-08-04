# 任务 7 实施报告：宿主无关 FastAPI 路由

## 状态

已完成。实现范围仅包含 `service.py`、`routes.py`、包导出、路由测试与本报告；未修改宿主 `app.py`、前端、Provider、预算表、任务 6 repository/handoff 或任何真实网络配置。

## 交付内容

- `local-runtime/wh_local/modules/daily_selection/service.py`
  - 新增 `DailySelectionService`，作为预览、列表、详情、反馈、确认和图片读取的唯一编排入口。
  - 预览流程依次校验 `DailySelectionCriteria`、解析注入的 Provider 配置、用注入 Provider 采集、执行本地筛选与 Decimal 评分、保存并回读完整批次快照。
  - 不保存 Provider 凭据；未知宿主配置异常不回显到 HTTP 响应。
  - 图片读取只授权当前 workspace 已保存批次中的参考图、主图、商品图、详情图和 SKU 图 URL。
  - 公网目标校验拒绝 `file://`/非 HTTP(S)、URL 凭据、localhost、回环/私网/链路本地/非全局 IP、旧式数字环回地址；图片缓存适配器必须在初始解析及每次重定向前调用同一校验器，返回后还会复验最终 URL 和解析地址。
- `local-runtime/wh_local/modules/daily_selection/routes.py`
  - 新增 `register_daily_selection_routes(router, dependencies)`，注册 `/desktop/daily-selection` 下六个接口。
  - `DailySelectionRouteDependencies` 注入 Actor/workspace 解析、Provider 配置解析、Provider factory、SQLite repository/path、已有预算实现和可选图片缓存；模块不 import 宿主应用。
  - 跨 workspace 的读、反馈、确认和图片请求统一返回不泄露所有权信息的 404。
  - FastAPI response model 保持 Pydantic/Decimal JSON 精确字符串语义。
- `local-runtime/wh_local/modules/daily_selection/__init__.py`
  - 仅导出宿主注册需要的 Actor、依赖、服务、缓存结果和路由注册函数。
- `local-runtime/tests/daily_selection/test_routes.py`
  - 使用 FastAPI `TestClient`、任务 6 的 `DailySelectionRepository(":memory:")`、Fake Provider、内存预算 double 和 Fake 图片缓存；测试不打开 socket。
  - 覆盖预览、批次列表、详情、反馈证据保留、确认幂等、跨 workspace 拒绝、已记录图片代理、任意/file URL 拒绝、回环/私网目标、私网重定向、旧式数字环回地址及宿主配置异常脱敏。

## 预算与任务 6 兼容性

- 本任务没有新增、重命名或迁移任何预算表。
- 文件型数据库默认复用现有 `SQLiteDailyApiBudget`；内存 SQLite 测试显式注入预算 double，避免路由层介入任务间既有预算表命名差异。
- 反馈和确认直接调用任务 6 repository；确认仍由其保证 `workspace_id + run_id + candidate_id` 幂等，并只创建 `daily_selection_handoffs`，不写 `product_drafts`。

## TDD 证据

1. 首次 RED：生产路由模块不存在，`test_routes.py` 收集以 `ModuleNotFoundError: ...daily_selection.routes` 失败。
2. 首轮 GREEN：六接口及 workspace 隔离、图片授权测试通过；纠正 Fake Provider 详情夹具使其遵循现有 normalizer 的 `data` 载荷协议。
3. 安全 RED：`validate_public_image_target("http://127.1/...")` 未拒绝旧式数字主机；补充 `inet_aton` 静态解析后 GREEN。
4. 脱敏 RED：宿主配置解析器的 `ValueError` 被 422 原样回显；收窄为仅已知 criteria/contract/Pydantic 校验错误可返回 422，未知宿主异常保持通用 500 后 GREEN。

## 验证

- 环境：Conda `base`，Python 3.12。
- 定向：`conda run -n base python -m pytest local-runtime/tests/daily_selection/test_routes.py -q` → `14 passed`。
- 完整每日选品：`conda run -n base python -m pytest local-runtime/tests/daily_selection -q` → `119 passed`。
- 编译：`conda run -n base python -m compileall -q .../service.py .../routes.py .../__init__.py .../test_routes.py` → 退出码 0。
- 实现和测试均未调用真实 Provider、DNS、HTTP 客户端或宿主应用。

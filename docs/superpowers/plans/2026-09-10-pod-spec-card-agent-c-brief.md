# Agent C 简报：POD 规格卡后端集成（worker / repository / service / router）

> 用途：代理 A（`spec_card.py` + `contracts.SpecCardConfig`）落地后，用本简报启动集成代理 C。
> 状态：待启动。所有行号/事实均已由主会话核实（2026-09-10）。

## 前置依赖（C 启动前必须已存在）
- `local-runtime/wh_local/modules/pod_customization/spec_card.py`：
  - `SpecCardRenderError`、`SpecCardRequest(cells, style="light", corner="bottom-right")`、
    `SpecCardResult(jpeg_bytes, font_px, cell_count, dropped_rows=0, truncated_cells=0)`、
    `render_spec_card(base_content: bytes, request, *, font_loader=None) -> SpecCardResult`
- `contracts.py`：`SpecCardConfig`（pydantic BaseModel：`enabled/style/corner/cells`，`ConfigDict(extra="forbid")`、**不 strip 单元格文本**）+ `SpecCardConfig.from_mapping` + `spec_card_is_configured`。

## 已核实的事实（集成点）
| 事实 | 位置 |
| --- | --- |
| 发布循环（逐格 save → publish → 落库） | `worker.py:1409-1441`（`_process_style_grids`） |
| 资产入库助手 | `worker.py:1665` `_save_asset(batch, kind, filename, content)` |
| 结果落库（`public_url` 与 `pattern_asset_id` 可分离） | `repository.py:2434` `finish_style_grid_result(...)` |
| 发布表 upsert（可更新指针） | `repository.py:2478-2484`（`ON CONFLICT ... DO UPDATE`） |
| 批次快照的 listing_fields 加载 | `repository.py:518-520`（`listing_fields_json` → dict） |
| 批次创建时 listing_fields 落库 | `repository.py:376` `request.listing_fields.model_dump_json()` |
| ListingFields / BatchCreate 均 `extra="forbid"` | `contracts.py:83-104` → **必须给 ListingFields 加 `spec_card` 字段**，否则 422 |
| 路由前缀与既有端点 | `router.py:45` `APIRouter(prefix="/api/pod-customization")`；认证/会话模式照抄既有 `/batches` 端点 |
| 审计日志 | `business_logger("pod_processing")`（worker.py 同款用法） |
| 终态集合 | `completed` / `partial_failure` / `failed` |
| 母版字节读取 | `repository.get_asset(asset_id, workspace_id, owner_user_id)` → `assets.read(relative_path)` |
| 发布图片 | `ai_runtime.publish_listing_image(media, namespace=..., role=...)`（media 需 `.content/.content_type/.suffix`） |

## 任务

### 1. `contracts.py`
- `ListingFields` 增加 `spec_card: SpecCardConfig | None = None`（可选；`extra="forbid"` 下前端送 `spec_card` 即可通过）。
- 注意：不要动 `str_strip_whitespace` 等既有 config；SpecCardConfig 自己的 config 由代理 A 保证不 strip。

### 2. `worker.py` —— 伴随式合成 hook（仅 `role == "hero"`）
在 `_process_style_grids` 的发布循环内，对 `role == "hero"` 的格子：
1. 保持现有逻辑先 `_save_asset(batch, "direct_listing_panel", ...)`（干净母版，`pattern_asset_id`/`composite_asset_id` 仍指向它）。
2. 从 `batch["listing_fields"].get("spec_card")` 经 `SpecCardConfig.from_mapping` 读配置；若缺失/`enabled=False`/未配置 → 走现状（发布干净图）。
3. 已配置 → `render_spec_card(panel.content, SpecCardRequest(cells=..., style=..., corner=...))`。
4. 成功 → 派生资产：`_save_asset(batch, "direct_listing_panel_card", f"style-{style_index}-hero-card.jpg", result.jpeg_bytes)`；把卡片字节包成发布用 media（`GeneratedMedia(stage="spec_card", content=..., content_type="image/jpeg", suffix=".jpg", provider="local-spec-card", model="local")`，沿用 `wh_local.modules.product_processing.infrastructure.media.GeneratedMedia`——本模块既有惯例）→ `publish_listing_image(..., role="hero")` → `finish_style_grid_result(public_url=卡片URL, pattern_asset_id=干净母版ID, composite_asset_id=干净母版ID, ...)`。
5. 任何渲染异常（含 `SpecCardRenderError`）→ `business_logger("pod_processing")` 记 warning + 完全按现状发布干净图（回退路径与现在逐字节等价）。
6. 标题触发逻辑（`role == "lifestyle"`）不动。

### 3. `repository.py`
- 新增 `update_batch_spec_card(batch_id, spec_card_mapping) -> bool`：把新配置写回 `listing_fields_json` 的 `spec_card` 键，**保留其余键**；返回是否命中批次。
- 新增/复用「列出批次中已完成 hero 结果（style_index, pattern_asset_id）」的查询与「按 result_id+role 更新 public_url」的助手（后者可复用 2478 附近既有 upsert，缺就补一个 `set_style_grid_publication(result_id, role, public_url)`）。

### 4. `service.py`
- `preview_spec_card(config, base_content: bytes | None) -> bytes`：纯逻辑（base 为空时用 800×800 纯白底 + 淡灰虚线框示意），返回 JPEG 字节；抛 `ValueError`（配置非法）供路由转 400。
- `reprint_batch_spec_card(actor, batch_id, config, style_index=None) -> dict`：
  1. 读批次；状态不在终态集合 → 抛专用异常（路由转 409，消息"批次尚未完成，暂不能重新合成标注"）。
  2. `update_batch_spec_card` 保存配置；空配置 → 抛 ValueError（"规格卡至少需要一个非空单元格"）。
  3. 遍历目标款式（`style_index` 过滤）：读 hero 母版 → 渲染 → 发布 → `set_style_grid_publication`；**逐款独立**：单款失败保留现状并计入 errors，其余继续。
  4. 每款开始/结束写 `business_logger("pod_processing")` 审计（用户、批次、style_index、结果）。
  5. 返回 `{saved: True, reprinted, failed, errors: [{style_index, message}], needs_re_export: True}`。

### 5. `router.py`
- `POST /spec-card/preview`，body `{cells, style, corner, base_template_id?}` → 200 `{"image": "data:image/jpeg;base64,..."}`；`base_template_id` 有值时解析模板资产字节（无模板/解析失败 → 用空白底图，不报错）；400 配置非法。
- `POST /batches/{batch_id}/spec-card/reprint`，body `{cells, style, corner, style_index?}` → 200 汇总；409 非终态；400 空配置。认证/会话/错误包装照抄既有 `/batches` 端点模式。

### 6. 测试
- `tests/test_worker.py` 扩展：仅 hero 被合成（其它三格 public_url/资产不变）；`monkeypatch` `render_spec_card` 抛错 → 该款发布干净图且批次正常；母版 asset 未被改写；派生资产 `kind == "direct_listing_panel_card"`；整款重生成路径同样合成。
- 新增 `tests/test_spec_card_api.py`（或并入 test_router.py 风格）：preview 返回 data URL 且不落库/不计费；reprint 对非终态 409；对终态返回汇总且逐款独立（注入一个渲染失败的款式）；`style_index` 过滤只重印一款；审计日志有记录；空配置 400。
- 全量验证：`cd /Users/Zhuanz/Desktop/MainPG/local-runtime && .venv/bin/python -m pytest -q wh_local/modules/pod_customization/tests`

## 纪律
- 只动 `worker.py / repository.py / service.py / router.py / contracts.py` 与上述测试文件；不碰前端、不碰 `spec_card.py`（A 的领地，除非 A 的报告明确需要小修，改前先与主会话确认）。
- 不 commit、不起服务；报告精确 API、测试结果与偏差。

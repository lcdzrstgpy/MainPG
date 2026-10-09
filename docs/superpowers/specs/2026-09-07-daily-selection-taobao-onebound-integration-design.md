# 每日选品接入万邦淘宝接口设计

## 目标

让「每日选品」模块的**淘宝方向**像已实现的 1688 方向一样，通过万邦（OneBound）接口完成采集，并复用同一套候选规范化、批次快照、审计与入池确认链路。前端保留的「淘宝」采集方向从「预留不调用后端」变成真实可用的采集链路。

## 背景与现状

- 万邦接口是「平台 + 接口类型」同构的：请求根地址为 `https://api-gw.onebound.cn/{平台}/{接口}/`。
- 1688 方向已经完整实现，核心代码在 `local-runtime/wh_local/data_collection/`：
  - `provider.py` 的 `OneBound1688Provider`（`_provider_name = "onebound-1688"`，`_endpoint = f"{base_url}/{operation}/"`，`item_search / upload_img / item_search_img / item_get / item_search_shop`）。
  - `collector.py` 的 `DailySelectionCollector` 负责编排搜索/详情/预算。
  - `normalizer.py` 把万邦返回转成 `DailySelectionCandidate`（硬编码 `detail.1688.com`、`candidate_id="1688:{offer_id}"`）。
  - `routes.py` 暴露 `POST /desktop/daily-selection/preview` 与 `/preview-from-1688-link`。
- 前端 `web-frontend/src/modules/daily_selection/` 已有「1688 / 淘宝 / 1688+淘宝」三个下拉项，但 `DailySelectionPage.tsx` 在 `platform !== "1688"` 时只提示「后端尚未接入」而不发请求。

## 万邦淘宝接口事实（已核实文档）

- 请求地址模板：`https://api-gw.onebound.cn/taobao/{接口}/`。
- 公共参数：`key`、`secret`（必填，与 1688 相同账号，**复用同一套 key/secret**，仅替换 URL 平台段）。
- 淘宝 `item_search`：`q`、`page`、`page_size`、`start_price/end_price`、`sort`、`cat`、`nick`、`lang`、`filter`。
- 淘宝 `item_search_img`：`imgid`（由 `upload_img` 返回）、`page_size`。
- 淘宝 `upload_img`：POST `imgcode`（图片 base64），返回图片 id。
- 淘宝 `item_get`：`num_iid`、`is_promotion`；商品详情 URL 为 `https://item.taobao.com/item.htm?id=<num_iid>`。
- 淘宝店铺全量：`item_search_shop`（本设计暂不做，见「不在范围」）。
- 错误码：`0000` success、`2000` 无结果、`4000/4001/4002/4003` 等，与 1688 一致（现有 `_outcome_for_status` 已覆盖）。

## 范围（本次只做）

- 每日选品的淘宝关键词采集（`item_search`）。
- 每日选品的淘宝参考图采集（`upload_img` + `item_search_img`）。
- 每日选品的淘宝链接采集（`preview-from-link`：先 `item_get`，再以主图优先、标题兜底做相似采集）。
- 候选规范化、预算、批次快照、确认入池，全部复用 1688 链路，只是平台参数化。

## 不在范围（后续再做）

- 整店采集（`item_search_shop`，对应前端“整店采集”Tab）。
- 插件采集（`plugin_onebound_capture`，对应前端“插件采集”Tab）。
- 1688+淘宝组合采集（本期不实现组合语义；可后续在服务层做多平台并联）。
- 不调整产品处理草稿池表结构（`product_processing_drafts` 不变，用 `source_platform` 字段区分平台）。

## 核心抽象：平台参数化 Provider

把 `OneBound1688Provider` 泛化为带 `platform` 参数的 `OneBoundProvider`（保留 `OneBound1688Provider` 作为 `platform="1688"` 的别名，避免大规模改动既有引用）：

```
class OneBoundProvider:
    def __init__(self, config, *, platform: str = "1688", transport=None, ...):
        self._provider_name = f"onebound-{platform}"
        self._platform = platform
        # config["base_url"] 为 https://api-gw.onebound.cn/taobao 或 .../1688
        ...
    def _endpoint(self, operation: str) -> str:
        return f"{self._base_url}/{operation}/"
```

`search_keyword / upload_reference_image / search_by_image / get_item_detail / search_shop` 的请求参数完全复用，仅 URL 根地址随平台变化。重试、审计、脱敏逻辑保持平台无关。

### base_url 的推导

`_provider_config(actor)`（`wh_local/app/main.py`）当前从鉴权服务器拿到的 `base_url` 是 1688 的（`https://api-gw.onebound.cn/1688`）。平台化后，base_url 按 `criteria.collection_platform` 推导：

- 1688：保持服务端下发的 `base_url`（兜底 `https://api-gw.onebound.cn/1688`）。
- taobao：把服务端下发的 `base_url` 中平台段替换为 `taobao`（`.../1688` → `.../taobao`），或由服务端按平台下发。

建议在 `_provider_config` 增加一个 `platform` 入参，返回对应平台的 base_url，且 `key/secret` 共用同一账号。

## 逐文件改动

### 后端 `local-runtime/wh_local/data_collection/`

1. **`provider.py`**
   - 泛化 `OneBound1688Provider` → `OneBoundProvider(config, *, platform)`；保留别名。
   - `_provider_name = f"onebound-{platform}"`；`safe_summary()` 增加 `platform`。
   - `item_search` 的 `q= " ".join(keywords)`、`page_size=target_count` 沿用 1688 实现，淘宝接口接受相同参数。
   - `upload_img`/`item_search_img`/`item_get` 请求逻辑不变（万邦同构）。
   - 如淘宝返回 `code`/`msg` 规则与 1688 略有差异，微调 `_outcome_for_status`（已覆盖大部分）。

2. **`criteria.py`**
   - `collection_platform: Literal["1688", "taobao"] = "1688"`（现为仅 `"1688"`）。
   - 预校验逻辑不动；`keyword_tags` 不变。

3. **`contracts.py`**
   - `DailySelectionCandidate.source_platform: Literal["1688", "taobao"]`。
   - 其余字段不变。

4. **`normalizer.py`**
   - 把硬编码的 `_canonical_1688_url` / `candidate_id="1688:{offer_id}"` 参数化：
     - 新增 `_canonical_platform_url(platform, value, id)`：`taobao` 生成 `https://item.taobao.com/item.htm?id={num_iid}`，`1688` 生成 `https://detail.1688.com/offer/{offer_id}.html`。
     - `candidate_id` 前缀改为 `f"{platform}:{id}"`。
   - `normalize_search_response` / `enrich_candidate_with_detail` / `normalize_detail_response` 接收/读取 `collection_platform`，按平台映射字段（淘宝 `pic_url`、`item_url`、`price`、`seller_info` 与 1688 差异做兼容）。
   - 建议抽一张「平台字段映射」表（`_PLATFORM_FIELD_MAP`）而非散落 if，避免后续再改。
   - `detail_seed`（`link_collection.py`）按平台取 title/主图，逻辑与 1688 一致。

5. **`link_collection.py`**
   - 新增 `canonical_taobao_item_url(value)`：解析 `https://item.taobao.com/item.htm?id=<num_iid>` 得到 `(canonical_url, num_iid)`。
   - 把 `canonical_1688_offer_url` 泛化为按平台入口（`canonical_platform_url(platform, value)`），保留原 1688 函数名避免破坏现有引用。
   - `validate_shop_sid` 仅服务 1688 店铺，本设计的 link 采集不涉及店铺。

6. **`service.py`**
   - `preview()` 用 `criteria.collection_platform` 决定 base_url（经 `_provider_config(actor, platform)`）。
   - `preview_from_1688_link` 泛化为 `preview_from_link`，按 `collection_platform` 选择解析入口与 seed 采集（主图优先、标题兜底）。
   - 错误文案去掉写死的「1688 采集服务尚未配置」，改为平台化提示。

7. **`routes.py`**
   - 请求体接受 `collection_platform`，透传给 service。
   - `/preview-from-1688-link` 的语义泛化为「按平台链接采集」（URL 平台与 `collection_platform` 一致）。
   - 入池确认：仍写 `source_type="onebound_api"`，候选 `source_platform` 区分平台；不改草稿池表结构。

### 宿主 `local-runtime/wh_local/app/main.py`

8. `_provider_config(actor, *, platform="1688")`：按平台返回 base_url（taobao 替换平台段），`key/secret` 共用。
9. `_provider_factory(config, *, platform)`：构造 `OneBoundProvider(config, platform=platform)`；`register_daily_selection_routes` 的依赖注入携带 platform 选择逻辑。

### 前端 `web-frontend/src/modules/daily_selection/`

10. **`types.ts`**
    - `DailySelectionCriteria.collection_platform?: "1688" | "taobao"`。
    - `DailySelectionCandidate.source_platform: "1688" | "taobao"`。
    - `CollectionPlatform` 类型保持（已含 `taobao`）。

11. **`DailySelectionPage.tsx`**
    - `buildCriteria()`：`criteria.collection_platform = platform`。
    - `submitCollection()`：把 `platform !== "1688"` 的「暂不发送」拦截改为：`taobao` 走正常请求；`1688+taobao` 本期提示「组合采集暂未支持」或只发当前平台（按后续协商）。
    - 来源标签/确认文案按 `source_platform` 显示「淘宝/1688」。

12. 相关展示组件（候选卡来源、`PluginOneboundCapturePanel` 不涉及本期）按平台显示「淘宝来源」。

## 数据流

```
前端每日选品页 buildCriteria()  {collection_platform:"taobao", collection_mode, keywords, target_count...}
   ▼ POST /desktop/daily-selection/preview（或 preview-tasks 异步）
routes.py → service.preview()
   ▼ 解析 criteria → _provider_config(actor, platform="taobao") → OneBoundProvider(platform=taobao)
   ▼ collector.collect(criteria)
        keyword: provider.search_keyword(criteria)  → item_search（q,page_size）
        image:   provider.search_by_image(criteria)  → 下载参考图 → upload_img(imgcode) → item_search_img(imgid)
        detail:  provider.get_item_detail(offer_id)  → item_get(num_iid)
   ▼ normalizer 按 taobao 映射字段、生成 item.taobao.com 链接
   ▼ service 去重 → save_run（daily_selection_runs/candidates 审计快照）
   ▼ 确认入池 → product_processing（source_platform 区分）
```

## 测试与验证

1. 1688 现有测试全绿（确认无回归）。
2. 新增 `local-runtime/tests/test_taobao_onebound_*.py`：
   - 用 `FakeTransport`/Fake `OneBoundProvider` + 临时 SQLite + FastAPI `TestClient`，封锁 DNS/外网。
   - 覆盖淘宝三条链路：
     - 关键词采集：断言 `item_search` 调用参数 `q`、`page_size`，source_url 为 `item.taobao.com/item.htm?id=`。
     - 参考图采集：断言 `download_reference_image → upload_img → item_search_img` 顺序，`imgid` 传递。
     - 链接采集：断言 `item_get` 拿 `num_iid`，再以主图/标题做相似采集。
   - 断言候选 `source_platform="taobao"`、`candidate_id` 前缀 `taobao:`。
3. 前端 `npm run build` TypeScript 类型与打包无回归。
4. 若需核对真实字段协议，做一次受控真实验收（不重复消耗额度、不落盘密钥），仅用于确认淘宝 item_search/item_get 返回字段命名。

## 验收清单

- [ ] `collection_platform` 字段后端接受 `"taobao"`，前端下拉选「淘宝」可发起请求。
- [ ] 淘宝关键词采集可返回候选并落 `daily_selection_runs/candidates`。
- [ ] 淘宝参考图采集可完成 download→upload→item_search_img 全流程。
- [ ] 淘宝链接采集可 `item_get` 后做相似采集。
- [ ] 候选 `source_platform="taobao"`，source_url 指向 `item.taobao.com`。
- [ ] 确认入池走现有 `onebound_api` 草稿 + `handoff`，不重复建草稿。
- [ ] 1688 方向零回归（现有测试全绿）。
- [ ] 前端 build 通过，来源标签按平台显示。

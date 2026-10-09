# 产品处理图片质量与提示词收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新产品处理任务使用商品优先的四宫格提示词、确定性风险路由和逐槽来源回退，不增加图片中转主链路等待。

**Architecture:** 本地策略选择安全画像和 V3 提示词；每商品只发起一次四宫格远程调用。拆分后的硬质量门失败时，立即绑定可信来源资产，不同步重绘。所有结果经 V2 media asset registry 以 asset_id 进入预检和尺寸画布。

**Tech Stack:** Python 3, FastAPI/Pydantic, SQLAlchemy, Pillow/OCR, pytest, React/TypeScript。

## 全局约束

- [ ] 仅影响开关启用后的新任务；绝不迁移、清洗或重写历史任务、图片和绑定。
- [ ] 普通和精品任务每商品最多一次远程四宫格请求；禁止同步 1K 补图和整体重试。
- [ ] 默认不得要求人物、手、手指、脸或身体部位；人物模式只能显式开启。
- [ ] 质量门只能使用低延迟确定性检查；P0 不引入同步 VLM。
- [ ] 所有素材以 asset_id 为权威，不能从 preview URL 反推素材。

---

### Task 1: 定义确定性生成画像与开关

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/domain/image_generation_policy.py`
- Modify: `local-runtime/wh_local/modules/product_processing/provider_config.py`
- Test: `local-runtime/tests/test_product_processing_image_generation_policy.py`

**Consumes:** raw 商品数据、标题、类目、风险标签、来源图数量。

**Produces:** `GridGenerationPolicy(profile, reasons, prompt_version, reference_order, allow_human_lifestyle)`。

- [ ] **Step 1: 写失败测试。**

~~~python
def test_default_policy_is_product_safe_and_human_free() -> None:
    policy = choose_grid_policy({'title': 'Travel Mug'}, category='Drinkware', reference_count=1)
    assert policy.profile == 'product_safe'
    assert policy.allow_human_lifestyle is False
    assert policy.prompt_version == 'grid-v3'

def test_risky_product_uses_fragile_profile() -> None:
    policy = choose_grid_policy({'risk_tags': ['fragile']}, category='Display Stands', reference_count=1)
    assert policy.profile == 'fragile_product'
    assert 'fragile' in policy.reasons
~~~

- [ ] **Step 2: 确认测试失败。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_generation_policy.py -q`. Expected: FAIL，因为模块不存在。

- [ ] **Step 3: 最小实现。** 创建冻结数据类和 `choose_grid_policy()`。风险标签、细长/多组件/透明关键词、来源图不足命中 `fragile_product`；不确定时也选它。仅当请求显式 `allow_human_lifestyle` 且 `WH_PRODUCT_GRID_HUMAN_LIFESTYLE_ENABLED=1` 时选 `human_lifestyle`。校验 `WH_PRODUCT_GRID_PROMPT_VERSION=grid-v3|legacy` 与 `WH_PRODUCT_GRID_REFERENCE_ORDER=source_first|scaffold_first`，非法值回落到安全默认并写 note。

- [ ] **Step 4: 验证并提交。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_generation_policy.py -q`. Expected: PASS. Commit: `feat: add safe grid generation policy`。

### Task 2: 添加 V3 商品安全提示词

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/domain/prompts.py`
- Test: `local-runtime/tests/test_product_processing_image_quality.py`

**Consumes:** `GridGenerationPolicy.profile` 与既有 `listing_prompt_context()`。

**Produces:** `GRID_IMAGE_PROMPT_V3` 和 profile 对应 panel brief；A/B 保留为历史回切。

- [ ] **Step 1: 写失败测试。**

~~~python
def test_grid_v3_prioritizes_identity_and_disables_humans() -> None:
    assert 'exact source SKU identity' in GRID_IMAGE_PROMPT_V3
    assert 'no person, face, hand, finger' in GRID_IMAGE_PROMPT_V3
    assert 'When any styling instruction conflicts' in GRID_IMAGE_PROMPT_V3
    assert '68%-82%' not in GRID_IMAGE_PROMPT_V3
~~~

- [ ] **Step 2: 确认测试失败。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_quality.py -q`. Expected: FAIL，因为 V3 模板不存在。

- [ ] **Step 3: 最小实现。** V3 的第一优先级必须为“exact source SKU identity and complete visible product > safe margins > no added text > four independent panels > styling”。冲突时明确要求简化场景保留商品。非人物画像禁止所有人体部位。四格固定为 hero、detail、unmanned_lifestyle、dimension_background；detail 不准纯微距或 inset；fragile 商品占比 45%--60%，尺寸格留白 15%--20%，无复杂道具。

- [ ] **Step 4: 验证并提交。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_quality.py -q`. Expected: PASS. Commit: `feat: add product-safe grid prompt v3`。

### Task 3: 让真实商品图优先于布局脚手架

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Test: `local-runtime/tests/test_product_processing_reference_integration.py`

**Consumes:** `GridGenerationPolicy.reference_order`。

**Produces:** `MediaProcessor.generate()` 新增 `layout_scaffold_order='source_first'|'scaffold_first'` keyword-only 参数。

- [ ] **Step 1: 写失败测试。**

~~~python
def test_source_first_places_product_before_scaffold(processor) -> None:
    processor.generate(stage='grid_image', prompt='p', reference_values=['https://e.test/product.jpg'],
                       layout_scaffold=True, layout_scaffold_order='source_first')
    assert processor.last_reference_names[:2] == ['product.jpg', 'fixed-four-grid-layout.png']
~~~

- [ ] **Step 2: 确认失败、实现、验证。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_reference_integration.py -q`; expected initial FAIL。为 `generate()` 和 `_generate_with_limits()` 加 keyword-only `layout_scaffold_order`，V3 使用策略顺序，legacy 保持 scaffold-first。`GeneratedMedia.reference_count` 只计真实商品图。Run: `cd local-runtime && python -m pytest tests/test_product_processing_reference_integration.py tests/test_product_processing_image_generation_modes.py -q`; expected PASS。Commit: `feat: prioritize product references in grid generation`。

### Task 4: 用逐槽来源回退替换同步 1K 补图

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Test: `local-runtime/tests/test_product_processing_image_generation_modes.py`
- Test: `local-runtime/tests/test_product_processing_image_quality.py`

**Consumes:** V3 policy、可信 `source_image_urls`、现有 `GridImageOutput`。

**Produces:** `GridImageOutput.slot_decisions`；失败槽走来源资产，绝不再请求 `grid_image_1..4`。

- [ ] **Step 1: 以失败测试替换旧的 1K repair 测试。**

~~~python
def test_four_grid_uses_source_fallback_without_second_remote_call(monkeypatch) -> None:
    service, processor = _service(monkeypatch)
    monkeypatch.setattr(service_module, 'inspect_visible_text', lambda content: {
        'chinese': [], 'prominent': ['AI COPY'] if content == b'slot-3' else [],
    })
    output = service._generate_grid_images(
        1, 2, _raw(), 'Travel Mug', 'Drinkware',
        ['https://example.com/source.jpg'], 'en', 'US', image_generation_count=4,
    )
    assert len(processor.calls) == 1
    assert output.slot_decisions[2].decision == 'source_fallback'
    assert output.provider_status_class == 'degraded_source_fallback'
~~~

另加没有可信来源的测试：不能伪造四格成功，任务必须为 `attention_required`。

- [ ] **Step 2: 确认失败。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_generation_modes.py tests/test_product_processing_image_quality.py -q`. Expected: FAIL，因为当前代码仍同步 1K 重绘。

- [ ] **Step 3: 最小实现。** 在 service 中增加冻结 `GridSlotDecision(slot_id, decision, reason_codes=())`，扩展 `GridImageOutput.slot_decisions`。将 OCR、拆分、空槽、解码失败转为稳定 reason code。删除 `regenerate_grid_slot()` 和对应线程池；不得调用 `gpt-image-2-1k`。主图优先作为回退来源；同一来源可填多个槽，但每槽必须标记 `source_fallback`。

- [ ] **Step 4: 验证并提交。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_generation_modes.py tests/test_product_processing_image_quality.py tests/test_product_processing_reference_integration.py -q`. Expected: PASS. Commit: `feat: fall back to source assets for failed grid slots`。

### Task 5: 将决策写入 V2 资产和任务结果

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/media_asset_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media_asset_orm.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media_asset_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/database.py`
- Test: `local-runtime/tests/test_product_processing_media_assets.py`
- Test: `local-runtime/tests/test_product_processing_media_api.py`

**Consumes:** `GridImageOutput.slot_decisions` 和既有 V2 asset/binding API。

**Produces:** `result.image_generation` 与每个 carousel binding 的 decision/reasons。

- [ ] **Step 1: 写失败测试。**

~~~python
def test_draft_media_exposes_source_fallback_metadata(client, seeded_fallback_binding) -> None:
    response = client.get('/api/product-processing/drafts/1/media')
    assert response.status_code == 200
    media = response.json()['groups']['carousel']
    hero = next(x for x in media if x['slot_id'] == 'carousel.hero')
    assert hero['status'] == 'ready'
    assert hero['origin'] == 'remote_source'
    assert hero['generation_decision'] == 'source_fallback'
~~~

- [ ] **Step 2: 确认失败、实现、验证。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_media_assets.py tests/test_product_processing_media_api.py -q`; expected initial FAIL。给 `MediaBindingRow` 添加默认空的 `generation_decision` 和默认 `[]` 的 `generation_reason_codes_json`，启动迁移仅增列。扩展 `bind_asset()`、repository view、`MediaAssetService._media_view()`。`_process_one()` 写 `prompt_version`、profile/reasons、reference order/count、remote call count、slot decisions；生成资产为 `ai_generated`，来源回退为 `remote_source` + binding decision。无来源为 `attention_required`。Run: `cd local-runtime && python -m pytest tests/test_product_processing_media_assets.py tests/test_product_processing_media_api.py tests/test_product_processing_dimension_canvas.py -q`; expected PASS。Commit: `feat: persist grid generation decisions on media bindings`。

### Task 6: 在预检显示来源兜底，不伪装为生成成功

**Files:**
- Modify: `web-frontend/src/modules/product_processing/types/index.ts`
- Modify: `web-frontend/src/modules/product_processing/api/productProcessingApi.ts`
- Modify: `web-frontend/src/modules/product_processing/components/PrecheckImageManager.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/styles/ProductProcessingVerifyPage.css`
- Test: `web-frontend/src/modules/product_processing/data/draftMediaModel.test.ts`

**Consumes:** `origin`、`generation_decision`、`generation_reason_codes`。

**Produces:** 预检可区分“AI 生成”“来源图兜底”“待人工处理”；画布仍可导入 ready 的回退资产。

- [ ] **Step 1: 写失败测试。**

~~~ts
it('labels source fallback without presenting it as generated', () => {
  expect(mediaQualityLabel({ origin: 'remote_source', generation_decision: 'source_fallback' }))
    .toEqual({ text: '来源图兜底', tone: 'warning' });
});
~~~

- [ ] **Step 2: 确认失败、实现、验证。** Run: `cd web-frontend && npx tsx --test src/modules/product_processing/data/draftMediaModel.test.ts`; expected initial FAIL。扩展 `MediaBindingView` 三个字段并渲染标签/原因摘要。来源回退且 ready 的 asset 可导入画布。单格重生 endpoint 没有完成验证前，前端不得展示无效按钮。Run: `cd web-frontend && npx tsx --test src/modules/product_processing/data/draftMediaModel.test.ts && npm run build`; expected PASS。Commit: `feat: show grid source fallback decisions in precheck`。

### Task 7: 样本 A/B、灰度与回归

**Files:**
- Create: `docs/operations/product-processing-grid-v3-rollout.md`
- Modify: `local-runtime/tests/test_product_processing_image_generation_policy.py`
- Modify: `local-runtime/tests/test_product_processing_image_generation_modes.py`

**Consumes:** P0 结果数据和运行时开关。

**Produces:** 可复现的 A/B 记录、升档/回滚条件。

- [ ] **Step 1: 写回切测试。**

~~~python
def test_legacy_prompt_is_available_only_when_explicitly_selected(monkeypatch) -> None:
    monkeypatch.setenv('WH_PRODUCT_GRID_PROMPT_VERSION', 'legacy')
    assert choose_grid_policy({}, category='', reference_count=1).prompt_version == 'legacy'
~~~

- [ ] **Step 2: 写上线操作文档。** 固定 30--50 个跨类目样本比较 source-first 与 scaffold-first；灰度 0% 影子、10%、50%、100%，每档至少 30 个新商品且观察一个工作日。指标：P50/P95 grid_pipeline、remote_call_count、完成率、source_fallback 率、预检人工否决率、缺边/手部/背景问题标签率。P95 上升超过 10% 或 source_unavailable 超基线两倍立即回滚；回滚只改后续任务开关。

- [ ] **Step 3: 全量相关验证并提交。** Run: `cd local-runtime && python -m pytest tests/test_product_processing_image_generation_policy.py tests/test_product_processing_image_generation_modes.py tests/test_product_processing_image_quality.py tests/test_product_processing_reference_integration.py tests/test_product_processing_media_assets.py tests/test_product_processing_media_api.py tests/test_product_processing_dimension_canvas.py -q`; expected PASS。Run: `cd web-frontend && npm run build`; expected PASS。Run: `git diff --check`; expected no output。Commit: `docs: add grid v3 rollout checklist`。

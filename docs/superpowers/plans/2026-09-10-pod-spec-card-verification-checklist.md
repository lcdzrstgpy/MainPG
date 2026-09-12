# POD 规格卡 P0 —— 合并验收清单（2026-09-10）

> 用途：代理 A/B/C 全部落地后，主会话按此清单合并验证；全部通过才算目标完成。

## 0. 文件归属核对（先看谁动了什么）
```
git status --short
```
- A：`local-runtime/wh_local/modules/pod_customization/spec_card.py`（新增）、`contracts.py`（SpecCardConfig）、`tests/test_spec_card.py`（新增）
- B：`web-frontend/src/modules/pod_customization/` 下 components/SpecCard*.tsx（新增）、pages/PodCustomizationPage.tsx、api、types、data、styles、新测试
- C：`worker.py`、`repository.py`、`service.py`、`router.py`、`contracts.py`（ListingFields.spec_card 字段）、链路测试
- 检查：用户既有的改动（dashboardStats.css、PodCustomizationPage.test.ts、podCustomization.css）没有被重排/删除内容。

## 1. 后端测试
```
cd /Users/Zhuanz/Desktop/MainPG/local-runtime
.venv/bin/python -m pytest -q wh_local/modules/pod_customization/tests/test_spec_card.py
.venv/bin/python -m pytest -q wh_local/modules/pod_customization/tests
```

## 2. 前端类型与测试
```
cd /Users/Zhuanz/Desktop/MainPG/web-frontend
npx tsc --noEmit
node --experimental-strip-types --test src/modules/pod_customization/pages/PodSpecCardEntry.test.ts
node --experimental-strip-types --test src/modules/pod_customization/pages/PodCustomizationPage.test.ts
node --experimental-strip-types --test "src/modules/pod_customization/**/*.test.ts"
```

## 3. 契约核对（读代码断言，而非只信测试）
- `spec_card.py`：API 与简报契约一致；**无 `product_processing` import**；单元格文本不 strip/不改写。
- `contracts.py`：`ListingFields.spec_card: SpecCardConfig | None`；`SpecCardConfig` 自身 config 无 `str_strip_whitespace`；`spec_card_is_configured` 存在。
- `worker.py`：hero 合成 hook 在 `_process_style_grids` 发布循环内；失败回退干净图；`pattern_asset_id/composite_asset_id` 仍为干净母版；派生资产 `kind="direct_listing_panel_card"`。
- `router.py`：`POST /spec-card/preview`（不落库/不计费，返回 data URL）；`POST /batches/{batch_id}/spec-card/reprint`（409 非终态；逐款独立；审计日志；汇总含 `needs_re_export`）。
- 前端：入口按钮「批量添加尺寸（必填）」+ `<em>*</em>`；提交拦截文案；抽屉 `role="dialog" aria-modal`；**无翻译/单位/占位符/一键模板**；无系统注入的固定表头文案。

## 4. 真实图冒烟（可选但推荐）
用线上真实 hero 面板 + 一份示例 cells 调 `render_spec_card` 出一张图，肉眼确认卡片可读、落在所选角：
```
cd /Users/Zhuanz/Desktop/MainPG/outputs/pod-spec-card-samples
/Users/Zhuanz/Desktop/MainPG/local-runtime/.venv/bin/python - <<'PY'
# 调 spec_card.render_spec_card，底图取 workbench.sqlite3 + pod-customization-assets 里的真实 hero
PY
```

## 5. 方案要求逐条勾验
- R1 第 4 张仍是该款图案（卡片叠加在生成图上）✓/✗
- R2 原样呈现（无任何文本加工）✓/✗
- R3 位置 = hero（四宫格左上角）✓/✗
- R4 渲染失败回退干净图、不拖垮批次 ✓/✗
- R5 提交前预览 + 终态重新合成 ✓/✗
- R6 必填硬拦截（至少一个非空单元格）✓/✗
- 伴随式：每款拆分后立即合成，无批次收尾阶段 ✓/✗
- 全批一次性重印：终态可用、逐款独立、进度与汇总、幂等 ✓/✗

## 6. 汇总
全部通过 → 更新 goal 完成；列出"待用户验收项"（真实批次试跑、字体打包、抽屉宽度/素材图列等 §15 遗留决策）。

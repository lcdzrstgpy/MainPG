# POD 规格卡 P0 验收汇总（2026-09-10）

> 方案：docs/superpowers/specs/2026-09-10-pod-spec-card-plan.md（v2）
> 编排简报：docs/superpowers/plans/2026-09-10-pod-spec-card-agent-c-brief.md
> 验收清单：docs/superpowers/plans/2026-09-10-pod-spec-card-verification-checklist.md

## 交付内容

**后端（local-runtime/wh_local/modules/pod_customization/）**
| 文件 | 内容 |
| --- | --- |
| `spec_card.py`（新，434 行） | 纯 Pillow 卡片渲染：双配色、四角、字号自适应、截断/丢行统计、失败抛 `SpecCardRenderError`；无 product_processing 依赖 |
| `contracts.py` | `SpecCardConfig` 模型（单元格原样不 strip）+ `spec_card_is_configured` + 上限校验；`ListingFields.spec_card` 可选字段（`extra="forbid"` 兼容） |
| `worker.py` | 伴随式 hook：仅 `hero`，发布前渲染卡片（`build_spec_card_media`），失败回退干净母版；派生资产 `direct_listing_panel_card`；`pattern/composite` 指针始终指向干净母版 |
| `repository.py` | `update_batch_spec_card`（写回 listing_fields_json.spec_card，零迁移）+ hero 目标列表 + 发布指针更新 |
| `service.py` | `preview_spec_card`（空白底图兜底 + 模板底图）+ `reprint_batch_spec_card`（终态门禁 409、逐款独立、审计日志、汇总） |
| `router.py` | `POST /api/pod-customization/spec-card/preview`；`POST /api/pod-customization/batches/{batch_id}/spec-card/reprint` |
| 测试 | `test_spec_card.py`（渲染+模型）、`test_spec_card_api.py`（接口）、`test_worker.py` 扩展（hook 链路） |

**前端（web-frontend/src/modules/pod_customization/）**
| 文件 | 内容 |
| --- | --- |
| `pages/PodCustomizationPage.tsx` | SKU 预设下方入口「批量添加尺寸（必填）」+ 摘要；提交硬拦截；挂载抽屉（+38/-1 外科式改动） |
| `components/SpecCardDrawer.tsx` | 右侧抽屉：编辑/外观/预览 + 保存/恢复/清空 + 终态「保存并全批重印」（进度、汇总、需重新导出提示）+ 运行中冻结只读（含"解锁编辑仅用于下一批次"防死锁） |
| `components/SpecCardTableEditor.tsx` | 1–3 列 / 1–8 行自由表格、每格 120 字上限、末列右对齐提示；无翻译/单位/占位符/一键模板 |
| `components/SpecCardAppearanceControls.tsx` | 浅/深风格 + 四角可视化选择器 |
| `components/SpecCardPreview.tsx` | 同源预览（300ms 防抖、过期响应丢弃、重试） |
| `data/podCustomizationModel.ts` / `Draft.ts` / `api/...` / `types/index.ts` | 配置模型、草稿兼容（v1/v2/v3）、两个接口封装、类型 |
| `styles/podCustomization.css` | 末尾追加 118 行 `.pod-spec-card-*`，未动既有规则 |
| 测试 | `pages/PodSpecCardEntry.test.ts`（12 用例，读源码断言模式） |

## 验证证据（主会话独立复跑）

| 项 | 结果 |
| --- | --- |
| 后端全量 `pytest wh_local/modules/pod_customization/tests` | **333 passed / 0 failed**（含此前一条顺序依赖偶发，本次通过） |
| 前端 `npx tsc --noEmit` | exit 0 |
| 前端页面测试（新 12 + 既有 22） | **34/34 pass** |
| 前端全模块 `node --test **/*.test.ts` | 72 项：69 pass / **3 fail = 基线既有失败**（HEAD 版本复现 12/3，与本次改动无关） |
| 冒烟（真实 hero 面板 + 生产渲染器） | 幂等（同输入同字节）；卡片落在所选角（右下亮度 185→200）；font_px=30、无截断无丢行 |
| 契约抽查 | 端点路径、`ListingFields.spec_card`、`SPEC_CARD_ASSET_KIND`、审计日志调用点全部就位 |
| 已知既有无关失败 | `local-runtime/tests/test_pod_customization_contracts.py`（HEAD 同样失败）；前端 3 例（同上） |

## 方案要求逐条勾验

- R1 第 4 张仍是该款图案（叠加在生成图上）✅（hook 只改 hero 发布指针，母版保持）
- R2 原样呈现 ✅（渲染器不做任何文本加工；测试含前后空格保留）
- R3 位置 = hero（四宫格左上角）✅（`_process_style_grids` 内 role=="hero" 分支）
- R4 渲染失败回退干净图 ✅（专测 `test_style_grid_spec_card_render_failure_publishes_the_clean_panel`）
- R5 提交前预览 + 终态重新合成 ✅（preview 端点不落库；reprint 终态可用）
- R6 必填硬拦截 ✅（`isSpecCardConfigured` + 提交前 setError，前端测试覆盖）
- 伴随式（无批次收尾阶段）✅
- 全批一次性重印（终态、逐款独立、汇总、审计、幂等）✅（内容寻址幂等 + API 测试覆盖）

## 待用户验收 / 遗留决策（不阻塞交付）

1. **字体（已修复，2026-09-10 后续）**：`spec_card.py` 的字体候选已加入 CJK 系统字体（mac PingFang / Windows 微软雅黑 msyh / Linux Noto Sans CJK），并置于纯拉丁字体之前——中文不再渲染成方框（已验证：解析字体 = PingFang，字形与 .notdef 像素差 10.03，中文冒烟样张 `F/G_smoke_*.jpg` 已用中文表格重新生成）。**上线仍建议**把 OFL 授权字体（如 NotoSansSC）放进 `assets/fonts/` 随包分发（候选顺序已优先该目录），以摆脱对系统字体的依赖。
2. 后端联调烟测（真实服务跑一次创建批次 → 预览 → 重印；两代理均按要求未起服务）。
3. §15 遗留：抽屉宽度 760px、「产品素材图」列是否用带卡版（如需干净版要做双资产）、`per_style` 款式级覆盖（P1）、Panel 1 prompt 留白（可选）。
4. 冻结态"解锁编辑（仅用于下一批次）"按钮：防必填死锁的补充设计，如不想要可删除。
5. 重印进度为整批一次语义（0→n 跳变 + 动画兜底），后端如提供流式可升级。
6. 已清理杂项：`local-runtime/:memory:.ses`（SQLite 临时产物）已删除。

# POD 本地图片缓存与清理说明

面向「缓存清理功能」的实现同学。本文说明 POD 三个板块的图片**存在哪、展示靠什么、现在怎么清、哪些能删哪些绝不能删**。

---

## 0. 一句话结论

POD 图片是**本地内容寻址资产 + 图床公网链接**双存：本地目录是缓存（可回收），图床链接是交付物（不归本地清理管）。
但**三种板块的图床覆盖不一样**，所以「能不能删本地」必须逐条按下面的判定式来，不能一刀切。

- **自动清扫**（后台无感）：只有「已有公网链接」的图才能放弃本地副本 → **半定制永不被自动清**。
- **用户主动删批次**：三个板块都允许删；**半定制没有图床备份，必须弹专用确认窗提示「不可恢复，请先导出」**（详见 §6）。

---

## 1. 三个板块：本地存哪、有没有图床

| 板块 | mode | 本地资产 | 图床（COS）公网链接 | 实测数据 |
|---|---|---|---|---|
| 全定制 | `full` | 四格结果图 + 原始四宫格 + 规格卡合成图 | ✅ 每张结果图都发布 | 8960 条结果，7415 条有公网链接 |
| 爆款复刻 | `replica` | 同上 + 样图 + 各款目标白底图 | ✅ 与全定制同一条管线，同样发布 | 1 个批次，4/4 张都有公网链接 |
| 半定制 | `semi` | **只有本地** | ❌ **明确不接图床**（`worker.py` 里 `public_url=""`） | 44 条结果，0 条有公网链接 |
| 模板 | — | 模板图（个人/系统） | ❌ 只有本地 | 22 个模板资产 |

> 复刻的「样图 / 目标白底图」虽然批次已经上图床，但**这两类原图本身没有公网备份**，只在本地。

---

## 2. 存放位置与结构

统一存储层：`local-runtime/wh_local/modules/pod_customization/assets.py` 的 `PodAssetStore`，**内容寻址**（文件名 = 内容 sha256，天然去重）。

```
<workbench.sqlite3 所在目录>/pod-customization-assets/
    <scope>/<sha256 前 2 位>/<sha256>.<png|jpg|webp>
```

- `scope = sha256(workspace_id + "\0" + owner_user_id)[:24]` —— 一个账号一个目录。
- 本机实际路径：`local-runtime/outputs/wh-local/pod-customization-assets/b2cd257b8efa89791d61906c/...`
- 元数据落表：`pod_customization_assets`（`asset_id / workspace_id / owner_user_id / kind / relative_path / content_type / byte_size / sha256 / width / height`）。
- 写入是先写 `.<sha256>.xxxx.tmp` 再 `os.replace` 原子替换 —— **扫描孤儿时请跳过 `*.tmp`**。

### 本机现状（实测，供参照）

```
磁盘：1893 个文件 / 1238.7 MB
DB 登记：251 行
→ 磁盘上未登记（孤儿）：1655 个 / 1108.1 MB   ← 占 89%，现有机制没清掉
→ 登记了但文件缺失：13 个                    ← 前端会走图床兜底，已缓解
```

按 `kind`：

| kind | 数量 | 体积 | 说明 |
|---|---|---|---|
| `direct_listing_grid` | 34 | 87.3 MB | 供应商返回的原始 2×2 四宫格（中间产物） |
| `direct_listing_panel` | 181 | 46.0 MB | 切好的四格结果图（交付物） |
| `direct_listing_panel_card` | 24 | 5.1 MB | 规格卡合成图（hero 发布用） |
| `template` | 22 | 4.2 MB | 模板图 |
| `replica_source` / `replica_target` | 2 | 0.9 MB | 复刻样图 / 目标白底图 |

---

## 3. 展示靠什么（已改成「公网优先 + 本地兜底」）

前端统一入口：`web-frontend/src/modules/pod_customization/data/usePodAssetUrl.ts`

- `http(s)://` → 直接 `<img src>`（COS 外链，不需要 CORS）
- 本地相对路径 → 走 `/api/pod-customization/assets/{asset_id}` 拉 blob（**需要登录态**）
- **新增**：`PodAssetImage` 支持 `fallbackPath` —— 公网链接加载失败（被删/失效/被拦）会自动切到本地地址

展示顺序规则集中在 `data/podImageSource.ts` 的 `podImageSource(publicUrl, localUrl)`：
公网链接优先，本地作为回退；只有本地时直接用本地。

已接入的四处：批次四格画廊、上架图抽屉、右侧检查器、结果大图。

> 注意：hero（素材图）的公网链接发布的是**规格卡合成图**，本地兜底是**干净母版**。走回退时规格卡文字会消失，属预期。

---

## 4. 现行清理机制（3 条）

### ① 后台清扫线程（48 小时一轮）

- 位置：`service.py` 的 `_start_cache_sweeper` / `_run_cache_sweeper`，仅当 `start_workers=True` 时启动。
- 逻辑：`repository.reap_stale_local_cache(older_than_hours=48)`，只处理**超过 48 小时**的数据：
  1. 已发布到图床的风格结果 → 清空 `pattern_asset_id` / `composite_asset_id`（放弃本地指针）
  2. 已到终态的批次 → 清空 `generation_calls.grid_asset_id`、`pattern_candidates.pattern_asset_id`
  3. 扫 `pod_customization_assets` 中「超窗 + 没有任何表引用 + 非 template」的行 → 删行 → 返回 `relative_path` → `assets.remove()` 删文件
- 删文件后顺手删空的父目录；**模板永不参与**。

### ② 用户手动删批次

`DELETE /api/pod-customization/batches/{batch_id}` → `delete_batch`：
仅**已到终态**的批次可删；级联删业务行 + 收集本地路径删文件；复刻批次的内部模板/快照一并删。

### ③ 底层共用

`_delete_assets_and_collect_files`：删资产行，并只返回「已无任何资产行引用」的路径（内容寻址去重，同一路径可能被多行共享，不能重复删）。

> **目前没有任何手动/接口触发的清理入口**（只有上面 ① 的定时线程和 ② 的删批次）。你们的缓存清理功能可以从这里接。

---

## 5. 能删 / 不能删（重点）

清理有**两个来源**，规则不同，必须分开实现：

| 来源 | 触发方 | 规则 |
|---|---|---|
| **A. 自动清扫** | 本机定时线程（48h 一轮） | 只在「该图已发布到图床、有公网链接」时才允许放弃本地副本；**永远不会自动清半定制** |
| **B. 用户主动删除批次** | 用户在界面上删 | **三个板块都允许删**；不同板块的确认强度不同（半定制见 §6） |

### ✅ A. 自动清扫可以删

| 对象 | 前置条件 |
|---|---|
| `direct_listing_grid`（原始四宫格） | 批次已到终态（completed / partial_failure / failed / cancelled / settlement_pending） |
| `pattern_candidates` 的中间产物 | 同上 |
| 已发布结果图的本地副本（`direct_listing_panel`） | 该结果**已有可用公网链接**（`pod_customization_style_grid_publications.public_url <> ''`）**且**超窗 |
| 无任何表引用的孤儿文件 | 超窗 + 不在 DB 的 `relative_path` 集合里（且不是 `*.tmp`） |

### ✅ B. 用户主动删除批次可以删

| 板块 | 本地图 | 图床备份 | 删除时的要求 |
|---|---|---|---|
| 全定制 `full` | 可删 | ✅ 有 | 沿用现有确认即可 |
| 爆款复刻 `replica` | 可删 | ✅ 有（结果图） | 沿用现有确认即可 |
| **半定制 `semi`** | **可删** | ❌ **没有** | **必须用专用二次确认弹窗**（见 §6） |

> 半定制的图只保存在本机，删除即**永久丢失**。允许删，但**必须**让用户明确知道「不可恢复、请先导出」。

### ❌ 绝不能删

| 对象 | 原因 |
|---|---|
| `kind='template'` 的资产 | 模板图，被 `templates` / `template_snapshots` 引用；模板库必须永远能打开 |
| 未到终态批次的所有资产 | 生成 / 重试 / 暂停恢复还在用，删了会直接失败 |
| **自动清扫下唯一可展示来源的图**（无 `public_url`） | 后台无感清掉后就彻底没图可看（含 full 里没发布成功的款） |
| 图床链接对应的 COS 对象 | 那是交付物与上架图，不归本地清理管；导出 / 店小秘都读它 |

> 「无图床备份的图不能**自动**删」和「用户主动删可以」并不矛盾：自动清扫是无感的后台行为，用户删除是有明确确认的主动行为。半定制只能走后一条。

### 安全判定式

**A. 自动清扫**（必须同时满足）：

1. 不属于 `kind='template'`；
2. 所属批次已是终态；
3. 存在**非空且可用**的公网链接（复用 `public_url <> ''`，建议再做一次可达性校验）；
4. 超过保留窗口（当前 48h）；
5. 删除后该 `relative_path` 无其它资产行引用。

> 第 3 条天然把**半定制排除在自动清扫之外**（它永远没有 public_url）——这个性质必须保住。

**B. 用户主动删除**：只要求上面第 1、2 条（终态校验后端已强制），半定制再加 §6 的强确认弹窗。

---

## 6. 删除模块的需求（给实现同事）

### 6.1 入口一览

| 板块 | 入口 | 后端接口 | 现状 |
|---|---|---|---|
| 全定制 | 批次卡片删除 / 记录抽屉勾选删 | `DELETE /api/pod-customization/batches/{batch_id}` | 已实现 |
| 爆款复刻 | 记录抽屉勾选删 | 同上 | 已实现 |
| 半定制 | ① 批次卡片上的「删除」单删<br>② 记录抽屉勾选后批量删 | `DELETE /api/pod-customization/semi/batches/{batch_id}` | 已实现，但**确认强度不够** |

> 一个半定制批次 = 一组四格 = 四款图案；删除会连本地图片一起清理。

### 6.2 三类板块的确认要求

- **全定制 / 爆款复刻**：图已发布到图床，本地只是缓存副本，删除后仍能用公网链接查看。沿用现有确认即可，文案保留「本地图片将被清理，不可恢复」。
- **半定制**：**没有图床备份，本机文件是唯一副本**。必须换成**专用确认弹窗**，不能用一行 `window.confirm` 敷衍过去。

### 6.3 半定制删除确认弹窗（必须实现）

**触发点（两处都要接）**

1. 批次卡片上的「删除」按钮（单删）；
2. 批次记录抽屉里勾选后点「删除选中」（批量删）。

> 现状：单删入口（`control("delete")`）**当前没有任何确认**，点了直接删；批量删只有一行通用 confirm。两处都必须改成下面的专用弹窗。

**弹窗文案必须覆盖四点**

1. 半定制的图案**只保存在本机**；
2. **没有图床公网备份**；
3. 删除后本地图片会被清理，且**不可恢复**；
4. **请先确认已导出**（引导用「下载 ZIP」保存需要的图案）。

参考文案：

> **删除半定制批次**
> 半定制的图案只保存在本机，没有图床公网备份。删除该批次会把本地图片一并清理，**删除后不可恢复**。
> 请先确认需要的图案已通过「下载 ZIP」导出到本地，再执行删除。
>
> ［取消］　［我已导出，仍要删除］

**交互要求**

- 确认按钮用**危险色**（红），文案要表态（如「我已导出，仍要删除」），不要写「确定」；
- 默认焦点落在「取消」，不要落在危险按钮上；
- 点遮罩 / 按 Esc / 点 × 都等于取消，不执行删除；
- 删除进行中禁用按钮并给出进行中态；
- 批量删除时，弹窗里显示**将要删除的批次数**；
- 必须有「不可恢复」的明确字样，不能只写「确认删除？」。

**实现风格建议（与现有代码保持一致）**

- 参考 `web-frontend/src/modules/pod_customization/components/PodUnsavedTemplateConfirmDialog.tsx`：`createPortal` 挂到 body + 独立弹窗 + `role="alertdialog"`。
  （必须 portal 到 body：tab 面板的入场动画会创建层叠上下文，把 fixed 层压到顶栏下面。）
- 弹窗必须自己声明 `--pod-*` 主题变量，否则脱离页面作用域后背景会透明：半定制页的变量只声明在 `.pod-semi-customization-page` 上，需把弹窗根类一并加进那条声明（见 `web-frontend/src/modules/pod_semi_customization/styles/podSemiCustomization.css` 顶部的变量块）。

### 6.4 删除的行为约束（通用）

1. **只允许删终态批次**：completed / partial_failure / failed / cancelled / settlement_pending。进行中的先暂停或取消。
2. 删除 = 删业务行（级联）+ 删本地图片文件；**不能只删行留文件**，也不能只删文件留行。
3. 删除后前端要：清空当前选中、从列表移除、刷新计数；如果删的正是当前正在看的批次，退回空态。
4. **不要顺手删图床对象**：全定制 / 复刻的公网链接是交付物，另有生命周期管理。
5. 批量删除是**按单批次接口逐个记账**的，部分失败要提示已删哪些、失败原因是什么，不能整体回滚或不提示。

### 6.5 后端配合

- `DELETE /semi/batches/{id}` 已复用 `service.delete_batch`，级联删行 + 删本地文件，**无需新增接口**。
- 若前端要展示「本次将释放多少空间 / 多少张图」，需要额外的只读预检接口（可选，当前没有）。

---

## 7. 已知问题与风险（需要一起解决的）

1. **磁盘孤儿 1655 个 / 1108 MB**：说明「删行 → 删文件」之间会漏。可能是历史上先删行没删文件，或删文件失败只打了 warning 被吞。→ 需要一次**磁盘 ↔ DB 对账**回收。
2. **13 个登记了但文件缺失**：本地接口会 404。本次「公网优先」改动已让它走图床兜底，但没图床（半定制）的仍会空白。
3. **双失风险**：一旦本地指针被清、公网链接又被图床删掉，这张图就彻底没了。→ 建议放弃本地前**校验公网链接可达**（HEAD 200），而不是只看字段非空。
4. **保留窗口写死 48h**：`reap_stale_local_cache(older_than_hours=48)`，不可配置、不能手动触发。
5. **复刻的样图/目标图没有图床备份**：如果要更激进地清本地，这两类必须先补公网发布，否则不能动。

---

## 8. 给「缓存清理功能」的对接建议

1. **手动触发入口**（当前没有）：建议加 `POST /api/pod-customization/cache/sweep`，管理员权限，参数 `older_than_hours`，内部直接调 `service.reap_stale_local_cache_once(older_than_hours=...)`，返回删除条数与释放字节数。
2. **删文件一律走 `PodAssetStore.remove(relative_path)`**：不要自己拼路径——它带 `_require_managed` 越界保护（防止误删根目录之外的文件），并会顺手清理空目录。
3. **孤儿对账**：用 DB 的 `relative_path` 全量集合与磁盘全量对比，**只删「不在 DB 且 mtime 超过保留期」的文件，跳过 `*.tmp`**（写入中的临时文件）。
4. **不要删 `pod-customization-assets` 根目录本身**，也不要删 `scope` 一级目录（账号目录，可能只是暂时没有资产）。
5. **清理是删除本地缓存，不是删除业务数据**：请只清资产行与文件，不要删批次/结果/标题/导出记录——导出与上架都依赖它们。
6. 释放量统计建议按 `byte_size` 累加（更准），磁盘占用按实际文件大小核对。
7. **半定制的删除必须走 §6.3 的强确认弹窗**，不要复用全定制/复刻那套通用 `window.confirm`——它没有图床备份，删了就是永久丢失。

---

## 9. 关键代码位置索引

| 关注点 | 位置 |
|---|---|
| 本地存储与删文件 | `local-runtime/wh_local/modules/pod_customization/assets.py`（`PodAssetStore.save_image / remove`） |
| 定时清扫 + 手动清扫入口 | `.../service.py`（`_start_cache_sweeper`、`reap_stale_local_cache_once`） |
| 清扫 SQL 与删除范围 | `.../repository.py`（`reap_stale_local_cache`、`delete_batch`、`_delete_assets_and_collect_files`） |
| 用户删除入口（全定制/复刻） | `DELETE /api/pod-customization/batches/{batch_id}` |
| 用户删除入口（半定制） | `DELETE /api/pod-customization/semi/batches/{batch_id}`（复用同一个 `service.delete_batch`） |
| 半定制删除现状（待改造点） | `web-frontend/src/modules/pod_semi_customization/pages/PodSemiCustomizationPage.tsx`（`control("delete")` 无确认；`deleteSelectedBatches` 仅通用 confirm） |
| 弹窗实现参考 | `web-frontend/src/modules/pod_customization/components/PodUnsavedTemplateConfirmDialog.tsx` |
| 图床发布 | `.../worker.py` `_process_style_grids`（半定制在此跳过发布）；`.../ai_runtime.py` `publish_listing_image` |
| 公网链接落库 | 表 `pod_customization_style_grid_publications`（`result_id / role / public_url`） |
| 资产元数据表 | `pod_customization_assets` |
| 前端展示与回退 | `web-frontend/.../data/usePodAssetUrl.ts`、`data/podImageSource.ts` |
| 资产预览接口 | `GET /api/pod-customization/assets/{asset_id}`（需登录） |

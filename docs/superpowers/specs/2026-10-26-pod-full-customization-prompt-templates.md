# POD 全定制：当前提示词模板（现状快照）

状态：**现状快照**（2026-10-26），由当前代码实际渲染生成，未改动任何行为
目标读者：POD 后端 / 提示词维护者
目标基线：`local-runtime/wh_local/modules/pod_customization/`

> 本文所有提示词正文均由 `prompts.py` / `column_prompts.py` 的真实函数渲染得到，不是手抄或示意。
> 示例批次字段见附录 A；重新生成方式见附录 B。
> 改动任何模板文本都必须递增对应版本号，见 §7。

---

## 0. 一句话

全定制有两条生图链路：**legacy 四宫格**（一款一次 2×2）和 **列式 column_v1**（4 款一组、组内 4 轮）。
两条链路共享同一批业务字段、同一套硬锁常量与同一套「元素 Tier / 60-30-10 配色」轮转引擎，
但提示词的拼装方式和冻结点不同。

| 项 | 值 | 位置 |
| --- | --- | --- |
| 生图提示词版本 | `v25` | [prompts.py](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L67) |
| 列式提示词版本 | `v25` | [column_prompts.py](../../../local-runtime/wh_local/modules/pod_customization/column_prompts.py#L56) |
| 标题提示词版本 | `pod-title-v1` | [title_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/title_runtime.py#L24) |
| 智能填写提示词版本 | `pod-brief-v6` | [brief_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/brief_runtime.py#L45) |
| 构图提示词版本 | `pod-composition-v4` / 列式专用 `v5` | [composition_runtime.py](../../../local-runtime/wh_local/modules/pod_customization/composition_runtime.py#L53-L54) |
| 结构识图版本 | `pod-structure-v1` | [structure_analysis.py](../../../local-runtime/wh_local/modules/pod_customization/structure_analysis.py#L20) |

---

## 1. 链路、入口与冻结

### 1.1 建批时生成的批级底座

`PodCustomizationRepository.create_batch` 在同一个事务里生成 `prompt_snapshot`：

- `generation_layout = column_v1` → `build_column_base_prompt(business_fields, creative_prompt, structure_facts, reference_view_roles)`（[repository.py](../../../local-runtime/wh_local/modules/pod_customization/repository.py#L746-L755)）
- 其它 → `build_direct_listing_prompt(business_fields, creative_prompt, composition=en_panels, structure_facts, reference_view_roles)`（[repository.py](../../../local-runtime/wh_local/modules/pod_customization/repository.py#L756-L763)）

注意：第二个位置参数 `subject_anchor` 收到的就是批次表单里的 **`creative_prompt`（创意编辑）**，
所以运营写的创意内容会被渲染成「主体硬约束」，而不是泛泛的风格建议。

版本号以服务端为准：`service.create_batch` 会用 `PATTERN_PROMPT_VERSION` 覆盖客户端传值（[service.py](../../../local-runtime/wh_local/modules/pod_customization/service.py#L516-L518)）。

### 1.2 真正提交给 provider 的提示词

| 链路 | 拼装函数 | 冻结点 |
| --- | --- | --- |
| legacy 四宫格 | `build_style_listing_prompt(批级底座, 款号, attempt, 本款元素, seed)`（[worker.py](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L1512-L1521)） | 批次 `prompt_snapshot` + 每款 `generation_calls.prompt_snapshot` |
| 列式 | `build_column_round_prompt(批级底座, role, 四份配方, composition, art_direction, group_index, attempt, flat_product)`（[worker.py](../../../local-runtime/wh_local/modules/pod_customization/worker.py#L2160-L2186)） | 每组每轮 `group_sheets.prompt_snapshot`，**首次执行即冻结**，续跑沿用 |

### 1.3 参考图策略

- legacy 普通批次：只下发模板主图（`template_image`）
- 豆包 + 模板带 side/top 结构视图：改为下发多参考图，并渲染 `REFERENCE VIEWS CONTRACT`
- 速创模型：强制退回单主图（`model_supports_multi_view` 只对豆包为真）
- 列式：第 1 轮下发模板主图/多视图；第 2/3/4 轮改为下发该组 4 张被采纳的主图作为像素锚

### 1.4 提示词体积（示例批次实测）

| 产物 | 字符数 |
| --- | --- |
| legacy 批级底座 | 11200 |
| legacy 完整单款提示词 | 15836 |
| 列式批级底座 | 7581 |
| 列式第 1 轮 | 13937 |
| 列式第 1 轮（带用户构图） | 13850 |
| 列式派生轮（细节 A） | 5671 |
| 列式派生轮（素材图） | 5925 |

---

## 2. 链路 A：legacy 四宫格

调用关系：`批级底座` + `单款追加段` = 实际提示词。底座每次建批只生成一次，逐款复用它。

### 2.1 批级底座（完整）

```text
=== PART 1 · CORE CONTRACT (highest priority) — 2x2 four-panel format, batch-wide style, global hard rules ===
Create one square 2x2 ecommerce contact sheet with exactly four equal panels.
Product name: 复古花卉印花托特包.
Product category: 女士手提包.
Target market: 美国.
Target audience: 25-40 岁通勤女性.
Core selling points: 可放 13 寸笔记本, 加厚帆布肩带, 整面满印复古花卉, 可机洗.
Batch-wide theme & style: 美式西南复古荒野风.
Color preferences: 落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝.
Excluded elements: 品牌 logo, 商标标识, 球队或联盟标识, 影视动漫游戏角色, 卡通 IP 形象, 名人肖像或签名, 奢侈品牌老花图案, 平台水印或标识, 受版权保护的海报封面, 真人照片或可识别个人信息.

=== BATCH-SPECIFIC HARD RULES (operator instructions — HIGHEST PRIORITY, hard requirements, not hints) ===
硬性规则：
1. 每款正常只生成一次 2×2 成组图片；生成、拆分或去重失败时最多重试一次。
2. 四格顺序固定为主图、细节图 A、细节图 B、素材图。
3. 同一款四张图必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致。
4. 不得添加未授权品牌、商标、版权角色、文字或水印。
5. 带内饰/内衬的产品（如收纳筐、脏衣篓、束口袋内里）：内饰表面保持无花色的统一纯色（默认黑色），图案只印在产品外表面，严禁把外表面的花色、底纹延伸到内饰上。

Treat the supplied template only as a STRUCTURAL reference for this product: geometry, proportions, construction, material, printable surface location, and its true surface relief (quilting/padding grid if present, seams, topstitching, hardware).
GEOMETRY LOCK: the product's silhouette, seam lines, panel divisions, quilting grid (if present), hardware positions and strap/handle drop must match the template exactly. Do not stretch, squash, melt, bend, reshape or re-proportion any part, and do not add, merge or delete structural parts.
Do not copy the template's existing artwork, decoration, colour scheme, background, lighting or scene.
CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not decoration — preserve them exactly as shown; do not flatten the quilted surface into a smooth shell. Only the artwork, the colour scheme and the scene are to be replaced.
That restriction only forbids REUSING the template's own scene. It does NOT mean the background may be plain: a freshly styled, richly lit scene is required (see PART 4 ART DIRECTION).
Use the template as the exact structural base: keep its silhouette, thickness, edge profile, proportions and flatness unchanged. Replace ONLY the printed artwork, the colour scheme and the scene; never redraw or reshape the product itself.
PRODUCT FORM & SIZE LOCK — the product form and dimensions stated in the batch facts and batch hard rules are geometric facts and outrank the template's apparent proportions: if the product is flat (tablecloth, mat, towel, blanket, sticker, paper), it must read as a FLAT SHEET lying on the tabletop — never as a box, cube, slab, stack, cushion, tray, molded shell or other 3D block. Keep stated sizes and coverage literal (for example a square 80x80 tablecloth must stay square, cover the tabletop and have no overhang). Corners and edges must stay paper-thin and flat: no rounded bulge, no raised rim, no thick bevel, no table skirt, no drape and no hanging corner. Never thicken, pad, inflate or extrude a flat product to give it volume. Every panel must preserve the exact flat square silhouette of the template.
FLAT PRINT LOCK: every motif is a 2D printed graphic on the product surface. Coins, candles, stars, ornaments or any other motif must have no thickness, no emboss, no raised relief and no cast object shadow; never place them as separate 3D objects on top of the product.
NO QUADRANT LABELS: never write A, B, C, D, 1, 2, 3, 4 or any letter/number in the corners or near the four-panel divider. The four panels must be separated only by the clean divider; no text, captions, labels, stamps, signatures or watermarks anywhere in the image.
Keep the same exact product across all four panels: identical structure, material, proportions, base color, artwork, artwork scale and artwork placement.
ARTWORK LAYOUT LOCK — Panel 1 (top-left) defines the printed layout ONCE, and the other three panels must reproduce that exact same layout:
- The hero motif's position on the product surface (for example a tree centred on the front face), its size relative to the product face, its orientation, and its spacing from seams, edges and hardware form ONE fixed map. Every panel must show that same map: the same motif in the same place at the same scale, with the same repeat spacing.
- Panels 2-4 may change camera angle, distance and crop, but must NEVER re-arrange, move, rotate, re-centre or resize the artwork for a nicer composition. A motif that is centred in Panel 1 and appears near a side edge, at the top, in a corner or at a different size in another panel is a FAILED result.
- If a panel shows the product from another angle, the whole printed surface rotates with the product as one rigid sheet: a motif keeps the same spot on the same face. A new camera angle is never permission to lay the pattern out again.
- A detail panel is a literal zoom into the SAME layout (it may reveal another area of it), not a new version of the artwork. Panels may show different areas, but the overall layout and the hero motif's anchored position and relative scale must stay identical across all four.
ALL-OVER PRINT LOCK: the artwork is a full-surface all-over print — it repeats seamlessly edge to edge across every printable surface (front, sides, base, handles and bindings), with no blank ground areas and never a single isolated motif floating on a plain shell.
Do not invent another product, extra accessories attached to the product, text, captions, logos, labels, watermarks, collages or borders; keep the four-panel divider clean and centered. (Standalone scene props beside the product ARE required — see PART 4 ART DIRECTION.)
If the product has an interior or lining, keep the interior surface unprinted (one plain solid color, black by default) and never extend the outer artwork inside.
COLOR IMPACT — the result must look vivid, rich and inviting, never dull, grey or washed out:
- Max out saturation and luminosity inside the palette: bright clean highlights, clear mid-tones, deep rich shadows; dark colors must read jewel-like (lacquer, enamel, velvet), never muddy or faded. Warm the white balance toward sunlight — no cold blue-grey cast.
- Build the PRODUCT BODY itself on the palette's strongest, most saturated signature colour (the batch theme's hero colour) as its dominant base: the product must be the boldest colour statement in frame. A pale, white or neutral product shell with only faint motifs is a FAILURE; lighter palette colours may only fill pattern negative space, the interior/lining, or the background.
- TONAL SEPARATION — the product must separate clearly from its background through a strong difference in value and/or saturation. Never place a pale product on a pale background, and keep props and the scene colourful too: a muted beige/grey set or a washed-out frame is a FAILURE.
- 60-30-10 COLOR HARMONY (fixed ratio contract): about 60% DOMINANT BASE (the product's large-area body colour), about 30% STRUCTURAL ACCENT (handles, bindings, large patch blocks), about 10% POP HIGHLIGHT (gold thread, metal hardware, small charms). The three proportions must be clearly readable; an even, untiered spread is a FAILURE.
MATERIAL REALISM — reproduce the template's TRUE material and construction, never a flat spray-painted surface or a generic invented shell:
- Do NOT assume this is a sewn fabric product. Read the real material from the template and the batch facts, and match its true finish, thickness, edge profile and physical behaviour.
- TEXTILE REALISM (sewn fabric products only — canvas, cotton, quilted fabric, PU fabric): if its body is quilted/padded, keep the same quilt relief: regular uniform cells at the template's own cell size and stitch pitch, fine dashed topstitching along every grid line, each cell gently puffed with a soft crown highlight and a soft valley shadow. Never smooth it into a featureless hard shell, and never invent coarser, larger or irregular quilting, random bubbles or a puffy-duvet look. Show real woven fibre with a soft matte sheen, subtle edge fuzz, natural folds and tension lines, plus topstitching and a fabric zipper with tape. Printed/embroidered motifs must sit ON the weave and bend with the fabric's folds and curvature.
- Smooth or rigid materials (natural rubber, silicone, PVC, leather, paper, wood, ceramic, glass, metal): keep the true surface — a matte rubber sheet stays smooth, flat and matte, with the template's own edge profile and thickness. Never add fabric weave, quilting, stitching, padding, zipper tape, soft cloth folds or a puffy body that the template does not show.
- On every material, printed motifs must follow the real surface: they bend with fabric folds, or sit flush on a smooth rubber/rigid face — never look like flat stickers floating over a generic shell, and stay sharply readable.
FABRIC CRAFT (this batch): 绗缝 + 平车明线. Prefer these craft terms, but the template's TRUE construction always wins — never add a craft the template does not show (the CARVE-OUT above has the highest priority).
=== PART 2 · PANEL ROLES, FRAMING & CAMERA — the four fixed slots and how each is shot ===
Panel 1 — PRIMARY IMAGE (top-left): the complete product in one newly generated, commercially useful lifestyle setting, shot wide at a normal eye-level product angle with the full silhouette in frame. It must never be a close-up, macro, cropped, partial or three-quarter detail shot — those belong to Panels 2-3 and must not be repeated here. This is the marketplace primary image and the title reference.
Panel 2 — DETAIL IMAGE A (top-right): a tight high-resolution close-up of the newly invented artwork on this same product, making colour, edges, print/material texture and manufacturing detail easy to inspect. It is a literal zoom into Panel 1's layout: the same motif stays at the same spot on the product and at the same relative scale. Do not alter, move or re-arrange the artwork.
Panel 3 — DETAIL IMAGE B (bottom-left): a different close product detail or three-quarter view — a product-appropriate structural or material detail — with the artwork visibly identical to Panels 1-2. If the angle changes, the printed layout rotates rigidly with the product; the hero motif keeps its position on the same face and never drifts to a side edge, the top or a corner.
Panel 4 — MATERIAL IMAGE (bottom-right): the complete product on a newly generated clean neutral ecommerce background, filling most of the panel with the full design sharply visible. Use the same front-facing layout as Panel 1 — same motif in the same place at the same scale. Supporting material imagery, not the marketplace primary image.
Final check on the fixed order by position: top-left = complete product in a lifestyle scene, top-right = tight artwork close-up, bottom-left = a different close product detail, bottom-right = complete product on a neutral background. Do not swap, shift or duplicate panels, and do not re-lay-out the artwork in any panel.
FLAT SHEET PANEL LOCK: if the product is a flat mat / tablecloth / placemat, Panel 3 (detail B) must be a top-down macro of the FLAT printed surface and edge stitching, not a corner, rim, side profile or three-quarter view. All four panels must keep the product flat on the tabletop with no overhang, drape, thickness or 3D structure.
```

### 2.2 单款追加段（本款元素 + 美术指导）

```text
=== PART 3 · ELEMENT ROTATION (this style only) — per-style elements that guarantee cross-style variety ===
STYLE-SPECIFIC DIVERSITY CONTRACT:
Style creative signature: STYLE-001 | elements=hero:秃鹫 narrative:陶罐,套索 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈鼠尾草绿(60%) / structural≈砖红(30%) / highlight≈奶油白(10%)
图案排布方式（重复/错位/条带/拼贴/定位场景）由模型根据本款主题自由决定；不得因此改变相机、景别或四格角色。
本款印花主元素：秃鹫（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）
印花叙事层 Narrative：陶罐, 套索（Tier 2，尺度 0.55，与主元素交替出现）
印花点缀层 Rhythm（自选）：请从同主题中自选 2-3 个小纹样/纹理（如雪花、星星、铃铛、格纹）作为点缀填充，合计约占可印表面 16%，不得抢主元素尺寸。
以上所有元素都是印在产品表面的平面图案/纹样：禁止生成独立实物、摆件、餐具、食品、家具或场景道具，也不得把元素做成 3D 立体装饰。
视觉权重（本款硬要求）：主元素约占可印表面 56%，Narrative 合计约 32%，Rhythm 合计约 12%。主元素的线性尺寸至少是 Narrative 的 1.8 倍、Rhythm 的 3.6 倍；除主元素外，任何图案不得接近它的尺寸。图案可以重复，也可被接缝/褶皱自然裁切；但主元素必须始终是本款最大的图案，一眼可辨，不得被叙事层或背景纹样淹没。空白只能作为图案之间的细缝，不能出现比一个主元素还大的连续底色。
满印硬要求：这是 all-over print，不是局部印花。图案必须无缝、连续地铺满产品所有可印表面（正面、侧面、底部、提手/包边等），边到边、四角到四角，任何位置都不留大片纯色空白（任意大于手掌的连续空白底色即视为失败）；主元素、次级纹样、填充纹样按层级交织重复，不能只在局部放 1~2 个图案；同一组内的图案元素在题材或形态上必须互相呼应，避免把不相干的元素硬凑在一起。
清单中未分配给本款的元素在本款中禁止出现。
四格图案布局锁定：主图（左上）确定的图案在商品表面的位置、相对尺度与接花间距，在另外三格必须完全一致；其余三格只允许换机位/景别或裁切，禁止把主元素挪到侧边、顶部、角落或改变它相对商品的尺寸。换视角时整张图案随商品一起刚性旋转，不得重新排版。
主题整批统一风格必须保持；具体排布与工艺由模型自行发挥，但四款成品必须一眼可辨。
Create this as style 1. Its surface artwork must be visibly different from every other style in this batch.
素材可以在批次内复用，但每款必须拥有不同的视觉主角与花样：主打元素身份、尺度关系、色彩重心至少两项不同。禁止输出“把所有元素等权复制粘贴”的通用大杂烩图案。
本款配色 60-30-10（高定调色契约）：base≈鼠尾草绿(60%) / structural≈砖红(30%) / highlight≈奶油白(10%)。主基调色覆盖产品大面积底色，结构副色用于提手/包边/大面积拼接块，点缀色只用于金线刺绣/金属件/小坠饰；三档比例必须读得出来，避免均等无主次的配色。若主基调色是白色/珠光白/浅色，它只能用于图案留白或场景背景，产品主体改用色板中最饱和的颜色，避免白包白底、画面发灰。
All four panels in this one contact sheet must nevertheless use exactly this one new design; do not create four design variants inside the sheet.

=== PART 4 · ART DIRECTION — real textile craft + scene beauty. The scene's beauty MUST be derived from the PART 1 batch-wide theme together with THIS style's PART 3 elements ===
SET DESIGN — the lifestyle scene must look rich, warm and inviting; a plain studio wall or an empty outdoor field is a FAILURE (this is the main quality gap versus top-selling listings):
- Derive the scene's mood from the PART 1 batch-wide theme. The PART 3 elements are PRINTED motifs on the product surface, NOT physical props — do not turn them into 3D objects.
- Stage this ONE lived-in scene, adapted to that theme (this style's own starting point: 晨光里的木质厨房岛台，右侧窗户透进条状光).
- SUPPORTING PROPS (from the art direction, real objects only): 釉面陶瓷杯, 厚切木砧板, 黄铜壶尤加利叶, 亚麻餐巾.
- LIGHTING (from the art direction): 低角度暖色侧光 + 漫射环境补光，产品下方有柔和接触阴影.
- FILL THE FRAME — dress the set with 3-5 supporting props taken from this theme's real usage world (for a cosmetics bag: makeup brushes, bottles, a small mirror, folded linen), grouped around the product in clear near/mid/far layers. Never generic filler, never a duplicate of the product, and never a 3D copy of a printed motif. Colours from this style's palette (the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝)) so they harmonize with the artwork.
- LIGHTING — one clear warm directional key light (low warm window light, golden afternoon light, or a warm lamp) that puts defined bright highlights on the product, a soft contact shadow beneath it and natural falloff into the background; optionally add a warm rim/backlight to separate the product from the set. Flat, grey, evenly lit studio light with no visible light direction is a FAILURE.
- GLOW — the scene must contain at least ONE visible warm light source in frame (fairy lights, candle, lantern, warmly lit window or glowing lamp) reading as a bright warm highlight with a soft halo or bokeh; an evenly lit scene with no visible light source is a FAILURE.
- Camera and surfaces: shallow depth of field, soft background bokeh, one optional blurred foreground prop; tactile materials (wood, linen, lace, marble, woven mat, checkered cloth). The product stays the hero — centered, fully visible, sharply in focus; props only support it.
- The scene must change from style to style inside this batch — never reuse one background set across different styles.
- Panels 1-3 are ONE shoot: the detail panels keep the SAME set surface, the SAME light direction and the SAME shallow depth of field, only with tighter crops — they must never drop to a plain grey studio backdrop. The material panel (bottom-right) must stay a clean neutral ecommerce background with no props.
Panel positions and roles stay fixed: top-left = PRIMARY image, top-right = detail A, bottom-left = detail B, bottom-right = material image. Do not swap, shift, or duplicate panels.
```

> 追加段末尾的 `Panel positions and roles stay fixed...` 是每款提示词的收尾句。
> `attempt=2` 时在 PART 3 内追加两行 `RETRY ATTEMPT 2 OF 2`，不改变任何配方。

### 2.3 平铺产品：PART 2 整段替换

命中 `_FLAT_PRODUCT_KEYWORDS` 或「平铺 / 无下挂 / 无垂坠 / 全覆盖桌面」信号时，PART 2 不再使用默认四格机位，
改为纯俯拍的四格描述 + 覆盖声明：

```text
Panel 1 — PRIMARY IMAGE (top-left): straight top-down flat lay of the complete product lying flat on a styled tabletop; all four edges visible inside the frame, no table edge, no chairs, no drape, no hanging corner, no thickness.
Panel 2 — DETAIL IMAGE A (top-right): top-down macro of the same artwork layout shown in Panel 1 — the same motif at the same spot on the sheet and at the same relative scale.
Panel 3 — DETAIL IMAGE B (bottom-left): top-down macro of a different area of the same printed layout plus edge stitching; never a corner, rim, side profile or three-quarter view, and never a re-arranged or re-scaled version of the artwork.
Panel 4 — MATERIAL IMAGE (bottom-right): straight top-down of the complete flat product on a clean neutral ecommerce background, using the same layout as Panel 1 (same motif position and scale).
FLAT PRODUCT COMPOSITION OVERRIDE: this is a flat sheet product. Ignore any eye-level, three-quarter or lifestyle camera direction; all four panels are TOP-DOWN flat lays. The product must stay flat on the tabletop with no overhang, drape, table skirt, hanging corner or thickness.
FLAT SHEET PANEL LOCK: if the product is a flat mat / tablecloth / placemat, Panel 3 (detail B) must be a top-down macro of the FLAT printed surface and edge stitching, not a corner, rim, side profile or three-quarter view. All four panels must keep the product flat on the tabletop with no overhang, drape, thickness or 3D structure.
```

### 2.4 用户构图：PART 2 替换为「用户指令最高优先级」

批次存在生效中的构图模板时，`composition` 的四个 `en` 面板会替换 PART 2：

```text
=== PART 2 · PANEL ROLES, FRAMING & CAMERA — the four fixed slots and how each is shot ===
USER-SPECIFIED SHOOTING DIRECTIONS — HIGHEST PRIORITY, HARD REQUIREMENTS (not hints): shoot each panel exactly as its own direction says. These OVERRIDE the default shot implied by the panel role name, and never reuse another panel's framing. Read each direction literally (e.g. 'vertically top-down' = shot straight down from overhead; 'macro close-up' = the surface fills the frame). Never average the four directions into one compromise framing. A shooting direction changes the CAMERA ONLY — the printed artwork layout stays exactly as Panel 1 defines it (see ARTWORK LAYOUT LOCK).
Panel 1 (top-left — role: primary image; user direction): Slightly low 45-degree oblique medium-full shot, full product in frame.
Panel 2 (top-right — role: detail image A; user direction): Direct 90-degree top-down overhead shot, product filling 90% of frame.
Panel 3 (bottom-left — role: detail image B; user direction): Extreme macro on the surface texture, shallow depth of field.
Panel 4 (bottom-right — role: material image; user direction): Centered top-down shot with even flat lighting on a clean neutral background.
MANDATORY DIFFERENCE CHECK — the four quadrants must show clearly different elevations (high / eye-level / low) and distances (wide / close-up / macro), as each panel requests. If two quadrants share the same camera angle, re-shoot one of them: Four identical viewpoints is a failed result. Framing alone may differ: the artwork layout (motif position and relative scale on the product) must stay identical in all four.
Panel positions and roles stay fixed: top-left = PRIMARY image, top-right = detail A, bottom-left = detail B, bottom-right = material image. Do not swap, shift, or duplicate panels.
FLAT SHEET PANEL LOCK: if the product is a flat mat / tablecloth / placemat, Panel 3 (detail B) must be a top-down macro of the FLAT printed surface and edge stitching, not a corner, rim, side profile or three-quarter view. All four panels must keep the product flat on the tabletop with no overhang, drape, thickness or 3D structure.
```

### 2.5 结构事实 / 多视图附加块

只有模板上传了结构视图并识图成功时才渲染；普通批次提示词与不含这些块时完全一致。
下面是四个渲染器的真实输出（示例为 3 部件、2 层、每层 4 单元、透明亚克力、下发主/侧/俯三视图）：

```text
STRUCTURE FACTS — extracted from the uploaded multi-view reference set. Every line is a literal, measurable fact, not a suggestion:
- Separate parts: exactly 3.
- Vertical tiers: exactly 2.
- Repeated units on each tier: exactly 4.
- Overall height: 420 mm.
- Material thickness: 3 mm.
- Material: clear acrylic, high, glossy polished edge.
- Symmetry: radial.
- Connections between parts: interlocking slots.
- Printable surfaces: front panel, side panels.

PART COUNT & SIZE LOCK — count literally in every panel: this product has exactly 2 vertical tiers and exactly 4 repeated units on each tier and exactly 3 separate parts and 420 mm tall and 3 mm thick. Do not simplify, merge, duplicate, add or remove any part, and do not re-proportion the product. A panel with a different tier count, unit count, thickness or silhouette is a FAILED result, even if the artwork looks better.

TRANSPARENT MATERIAL LOCK — this product is genuinely see-through, and every panel must keep it so:
- Keep the body transparent: the background stays visible through it, edges stay crisp and polished, sheet thickness is visible, and bright specular highlights plus subtle edge refraction read as real clear material.
- Never turn it into frosted, milky, white, opaque plastic, painted metal or tinted glass; never add a colour tint the reference does not show.
- Never invent side walls, back panels, bases, lids, brackets or supports. Only the parts in STRUCTURE FACTS exist.
- Separate the product from its background with edge highlights, a soft contact shadow and slight refraction — never by making it opaque or darker.

REFERENCE VIEWS CONTRACT — the attached images are ONE orthographic reference set for ONE single product, not artwork to copy:
- Image 1 = PRIMARY white-background product photo — the single structural baseline.
- Image 2 = SIDE elevation of the same product.
- Image 3 = TOP elevation of the same product.
- Use the extra views ONLY to resolve geometry the primary image cannot show: part count, tier count, repeated units, thickness, connections and symmetry.
- Take STRUCTURE ONLY from these images. Never copy their background, backdrop colour, props, measurement lines, arrows, numbers, labels or watermarks; annotations are not part of the product and must never appear in the result.
- If two views seem to disagree, the PRIMARY image wins. Never average them, never merge parts from different views and never build a hybrid product.
- All four panels must show the same product reconstructed from this set. A panel with a different silhouette, part count or proportion is a FAILED result.
```

---

## 3. 链路 B：列式生成（column_v1）

语义是「**一轮四款**」而不是「一款四格」：
第 1 轮出 4 款主图；第 2/3/4 轮以这 4 张主图为锚，分别出 4 款的细节 A / 细节 B / 素材图。

### 3.1 批级底座（完整）

```text
=== PART 1 · CORE CONTRACT (highest priority) — 2x2 four-panel format, batch-wide style, global hard rules ===
Create one square 2x2 ecommerce contact sheet with exactly four equal panels.
The four panels are FOUR DIFFERENT STYLES of one collection printed on the SAME physical product.
Product name: 复古花卉印花托特包.
Product category: 女士手提包.
Target market: 美国.
Target audience: 25-40 岁通勤女性.
Core selling points: 可放 13 寸笔记本, 加厚帆布肩带, 整面满印复古花卉, 可机洗.
Batch-wide theme & style: 美式西南复古荒野风.
Color preferences: 落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝.
Excluded elements: 品牌 logo, 商标标识, 球队或联盟标识, 影视动漫游戏角色, 卡通 IP 形象, 名人肖像或签名, 奢侈品牌老花图案, 平台水印或标识, 受版权保护的海报封面, 真人照片或可识别个人信息.

硬性规则：
1. 每个二级批次（4 款）正常生成 4 轮，每轮一张 2×2 成组图片；失败最多重试一次。
2. 每轮四格固定对应组内 4 个款：左上=第 1 款、右上=第 2 款、左下=第 3 款、右下=第 4 款。
3. 同一款的四轮产物必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致；同一轮四格必须是四个不同款，禁止串色串图。
4. 不得添加未授权品牌、商标、版权角色、文字或水印。
5. 内饰/内衬保持无花色统一纯色，图案只印外表面。

Treat the supplied template only as a STRUCTURAL reference for this product: geometry, proportions, construction, material, printable surface location, and its true surface relief (quilting/padding grid if present, seams, topstitching, hardware).
GEOMETRY LOCK: the product's silhouette, seam lines, panel divisions, quilting grid (if present), hardware positions and strap/handle drop must match the template exactly. Do not stretch, squash, melt, bend, reshape or re-proportion any part, and do not add, merge or delete structural parts.
Do not copy the template's existing artwork, decoration, colour scheme, background, lighting or scene.
CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not decoration — preserve them exactly as shown; do not flatten the quilted surface into a smooth shell. Only the artwork, the colour scheme and the scene are to be replaced.
Keep the same exact product in every panel and every round: identical structure, material, proportions, base color and print surfaces.
ALL-OVER PRINT LOCK: each style's artwork is a full-surface all-over print — it repeats seamlessly edge to edge across every printable surface (front, sides, base, handles and bindings), with no blank ground areas and never a single isolated motif floating on a plain shell.
QUADRANT ISOLATION LOCK — the four panels are four DIFFERENT product designs (four styles of one collection) on the SAME physical product. Each quadrant's motifs, palette, scale rhythm and rendering are exclusive to that quadrant. Never copy, blend, bleed or swap any motif or colour across quadrants or across the divider. If two quadrants read as the same design, the result is FAILED.
NO QUADRANT LABELS: never write A, B, C, D, 1, 2, 3, 4 or any letter/number in the corners or near the four-panel divider. The four panels must be separated only by the clean divider; no text, captions, labels, stamps, signatures or watermarks anywhere in the image.
Do not invent another product, extra accessories attached to the product, text, captions, logos, labels, watermarks, collages or borders; keep the four-panel divider clean and centered. (Standalone scene props beside the product ARE required — see the art direction.)
If the product has an interior or lining, keep the interior surface unprinted (one plain solid color, black by default) and never extend the outer artwork inside.
PRODUCT FORM & SIZE LOCK — the product form and dimensions stated in the batch facts and batch hard rules are geometric facts and outrank the template's apparent proportions: if the product is flat (tablecloth, mat, towel, blanket, sticker, paper), it must read as a FLAT SHEET lying on the tabletop — never as a box, cube, slab, stack, cushion, tray, molded shell or other 3D block. Keep stated sizes and coverage literal (for example a square 80x80 tablecloth must stay square, cover the tabletop and have no overhang). Corners and edges must stay paper-thin and flat: no rounded bulge, no raised rim, no thick bevel, no table skirt, no drape and no hanging corner. Never thicken, pad, inflate or extrude a flat product to give it volume. Every panel must preserve the exact flat square silhouette of the template.
FLAT PRINT LOCK: every motif is a 2D printed graphic on the product surface. Coins, candles, stars, ornaments or any other motif must have no thickness, no emboss, no raised relief and no cast object shadow; never place them as separate 3D objects on top of the product.
COLOR IMPACT — the result must look vivid, rich and inviting, never dull, grey or washed out:
- Max out saturation and luminosity inside the palette: bright clean highlights, clear mid-tones, deep rich shadows; dark colors must read jewel-like (lacquer, enamel, velvet), never muddy or faded. Warm the white balance toward sunlight — no cold blue-grey cast.
- Build the PRODUCT BODY itself on the palette's strongest, most saturated signature colour (the batch theme's hero colour) as its dominant base: the product must be the boldest colour statement in frame. A pale, white or neutral product shell with only faint motifs is a FAILURE; lighter palette colours may only fill pattern negative space, the interior/lining, or the background.
- TONAL SEPARATION — the product must separate clearly from its background through a strong difference in value and/or saturation. Never place a pale product on a pale background, and keep props and the scene colourful too: a muted beige/grey set or a washed-out frame is a FAILURE.
- 60-30-10 COLOR HARMONY (fixed ratio contract): about 60% DOMINANT BASE (the product's large-area body colour), about 30% STRUCTURAL ACCENT (handles, bindings, large patch blocks), about 10% POP HIGHLIGHT (gold thread, metal hardware, small charms). The three proportions must be clearly readable; an even, untiered spread is a FAILURE.
MATERIAL REALISM — reproduce the template's TRUE material and construction, never a flat spray-painted surface or a generic invented shell:
- Do NOT assume this is a sewn fabric product. Read the real material from the template and the batch facts, and match its true finish, thickness, edge profile and physical behaviour.
- TEXTILE REALISM (sewn fabric products only — canvas, cotton, quilted fabric, PU fabric): if its body is quilted/padded, keep the same quilt relief: regular uniform cells at the template's own cell size and stitch pitch, fine dashed topstitching along every grid line, each cell gently puffed with a soft crown highlight and a soft valley shadow. Never smooth it into a featureless hard shell, and never invent coarser, larger or irregular quilting, random bubbles or a puffy-duvet look. Show real woven fibre with a soft matte sheen, subtle edge fuzz, natural folds and tension lines, plus topstitching and a fabric zipper with tape. Printed/embroidered motifs must sit ON the weave and bend with the fabric's folds and curvature.
- Smooth or rigid materials (natural rubber, silicone, PVC, leather, paper, wood, ceramic, glass, metal): keep the true surface — a matte rubber sheet stays smooth, flat and matte, with the template's own edge profile and thickness. Never add fabric weave, quilting, stitching, padding, zipper tape, soft cloth folds or a puffy body that the template does not show.
- On every material, printed motifs must follow the real surface: they bend with fabric folds, or sit flush on a smooth rubber/rigid face — never look like flat stickers floating over a generic shell, and stay sharply readable.
FABRIC CRAFT (this batch): 绗缝 + 平车明线. Prefer these craft terms, but the template's TRUE construction always wins — never add a craft the template does not show (the CARVE-OUT above has the highest priority).
```

> 列式底座刻意**不包含**元素轮转与美术指导：那两段属于第 1 轮，逐组拼装时再注入。

### 3.2 第 1 轮（主图，完整）

```text
=== PART 1 · CORE CONTRACT (highest priority) — 2x2 four-panel format, batch-wide style, global hard rules ===
Create one square 2x2 ecommerce contact sheet with exactly four equal panels.
The four panels are FOUR DIFFERENT STYLES of one collection printed on the SAME physical product.
Product name: 复古花卉印花托特包.
Product category: 女士手提包.
Target market: 美国.
Target audience: 25-40 岁通勤女性.
Core selling points: 可放 13 寸笔记本, 加厚帆布肩带, 整面满印复古花卉, 可机洗.
Batch-wide theme & style: 美式西南复古荒野风.
Color preferences: 落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝.
Excluded elements: 品牌 logo, 商标标识, 球队或联盟标识, 影视动漫游戏角色, 卡通 IP 形象, 名人肖像或签名, 奢侈品牌老花图案, 平台水印或标识, 受版权保护的海报封面, 真人照片或可识别个人信息.

硬性规则：
1. 每个二级批次（4 款）正常生成 4 轮，每轮一张 2×2 成组图片；失败最多重试一次。
2. 每轮四格固定对应组内 4 个款：左上=第 1 款、右上=第 2 款、左下=第 3 款、右下=第 4 款。
3. 同一款的四轮产物必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致；同一轮四格必须是四个不同款，禁止串色串图。
4. 不得添加未授权品牌、商标、版权角色、文字或水印。
5. 内饰/内衬保持无花色统一纯色，图案只印外表面。

Treat the supplied template only as a STRUCTURAL reference for this product: geometry, proportions, construction, material, printable surface location, and its true surface relief (quilting/padding grid if present, seams, topstitching, hardware).
GEOMETRY LOCK: the product's silhouette, seam lines, panel divisions, quilting grid (if present), hardware positions and strap/handle drop must match the template exactly. Do not stretch, squash, melt, bend, reshape or re-proportion any part, and do not add, merge or delete structural parts.
Do not copy the template's existing artwork, decoration, colour scheme, background, lighting or scene.
CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not decoration — preserve them exactly as shown; do not flatten the quilted surface into a smooth shell. Only the artwork, the colour scheme and the scene are to be replaced.
Keep the same exact product in every panel and every round: identical structure, material, proportions, base color and print surfaces.
ALL-OVER PRINT LOCK: each style's artwork is a full-surface all-over print — it repeats seamlessly edge to edge across every printable surface (front, sides, base, handles and bindings), with no blank ground areas and never a single isolated motif floating on a plain shell.
QUADRANT ISOLATION LOCK — the four panels are four DIFFERENT product designs (four styles of one collection) on the SAME physical product. Each quadrant's motifs, palette, scale rhythm and rendering are exclusive to that quadrant. Never copy, blend, bleed or swap any motif or colour across quadrants or across the divider. If two quadrants read as the same design, the result is FAILED.
NO QUADRANT LABELS: never write A, B, C, D, 1, 2, 3, 4 or any letter/number in the corners or near the four-panel divider. The four panels must be separated only by the clean divider; no text, captions, labels, stamps, signatures or watermarks anywhere in the image.
Do not invent another product, extra accessories attached to the product, text, captions, logos, labels, watermarks, collages or borders; keep the four-panel divider clean and centered. (Standalone scene props beside the product ARE required — see the art direction.)
If the product has an interior or lining, keep the interior surface unprinted (one plain solid color, black by default) and never extend the outer artwork inside.
PRODUCT FORM & SIZE LOCK — the product form and dimensions stated in the batch facts and batch hard rules are geometric facts and outrank the template's apparent proportions: if the product is flat (tablecloth, mat, towel, blanket, sticker, paper), it must read as a FLAT SHEET lying on the tabletop — never as a box, cube, slab, stack, cushion, tray, molded shell or other 3D block. Keep stated sizes and coverage literal (for example a square 80x80 tablecloth must stay square, cover the tabletop and have no overhang). Corners and edges must stay paper-thin and flat: no rounded bulge, no raised rim, no thick bevel, no table skirt, no drape and no hanging corner. Never thicken, pad, inflate or extrude a flat product to give it volume. Every panel must preserve the exact flat square silhouette of the template.
FLAT PRINT LOCK: every motif is a 2D printed graphic on the product surface. Coins, candles, stars, ornaments or any other motif must have no thickness, no emboss, no raised relief and no cast object shadow; never place them as separate 3D objects on top of the product.
COLOR IMPACT — the result must look vivid, rich and inviting, never dull, grey or washed out:
- Max out saturation and luminosity inside the palette: bright clean highlights, clear mid-tones, deep rich shadows; dark colors must read jewel-like (lacquer, enamel, velvet), never muddy or faded. Warm the white balance toward sunlight — no cold blue-grey cast.
- Build the PRODUCT BODY itself on the palette's strongest, most saturated signature colour (the batch theme's hero colour) as its dominant base: the product must be the boldest colour statement in frame. A pale, white or neutral product shell with only faint motifs is a FAILURE; lighter palette colours may only fill pattern negative space, the interior/lining, or the background.
- TONAL SEPARATION — the product must separate clearly from its background through a strong difference in value and/or saturation. Never place a pale product on a pale background, and keep props and the scene colourful too: a muted beige/grey set or a washed-out frame is a FAILURE.
- 60-30-10 COLOR HARMONY (fixed ratio contract): about 60% DOMINANT BASE (the product's large-area body colour), about 30% STRUCTURAL ACCENT (handles, bindings, large patch blocks), about 10% POP HIGHLIGHT (gold thread, metal hardware, small charms). The three proportions must be clearly readable; an even, untiered spread is a FAILURE.
MATERIAL REALISM — reproduce the template's TRUE material and construction, never a flat spray-painted surface or a generic invented shell:
- Do NOT assume this is a sewn fabric product. Read the real material from the template and the batch facts, and match its true finish, thickness, edge profile and physical behaviour.
- TEXTILE REALISM (sewn fabric products only — canvas, cotton, quilted fabric, PU fabric): if its body is quilted/padded, keep the same quilt relief: regular uniform cells at the template's own cell size and stitch pitch, fine dashed topstitching along every grid line, each cell gently puffed with a soft crown highlight and a soft valley shadow. Never smooth it into a featureless hard shell, and never invent coarser, larger or irregular quilting, random bubbles or a puffy-duvet look. Show real woven fibre with a soft matte sheen, subtle edge fuzz, natural folds and tension lines, plus topstitching and a fabric zipper with tape. Printed/embroidered motifs must sit ON the weave and bend with the fabric's folds and curvature.
- Smooth or rigid materials (natural rubber, silicone, PVC, leather, paper, wood, ceramic, glass, metal): keep the true surface — a matte rubber sheet stays smooth, flat and matte, with the template's own edge profile and thickness. Never add fabric weave, quilting, stitching, padding, zipper tape, soft cloth folds or a puffy body that the template does not show.
- On every material, printed motifs must follow the real surface: they bend with fabric folds, or sit flush on a smooth rubber/rigid face — never look like flat stickers floating over a generic shell, and stay sharply readable.
FABRIC CRAFT (this batch): 绗缝 + 平车明线. Prefer these craft terms, but the template's TRUE construction always wins — never add a craft the template does not show (the CARVE-OUT above has the highest priority).

=== PART 2 · SCENE & COMPOSITION — this round's single shared camera ===
ROUND-1 SHOOTING DIRECTION (applies to all four quadrants of this sheet; read it literally, do not average it into a compromise framing):
Natural eye-level 3/4 hero angle: the complete product with its full silhouette in frame, centred with generous negative space, shot wide rather than cropped.
SAME CAMERA, DIFFERENT ARTWORK — all four quadrants share one camera setup, one lens, one framing and one lighting direction (this round's direction). The four quadrants must differ by ARTWORK, never by camera angle. Four different elevations, crops or scene styles in one sheet = FAILED.

=== PART 3 · ELEMENT ROTATION — one exclusive recipe per quadrant ===
QUADRANT TOP-LEFT — style 1 (this quadrant only):
- 本款印花主元素：秃鹫（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）
- 印花叙事层 Narrative：陶罐, 套索（Tier 2，尺度 0.55，与主元素交替出现）
- 印花点缀层 Rhythm（自选）：请从同主题中自选 2-3 个小纹样/纹理（如雪花、星星、铃铛、格纹）作为点缀填充，合计约占可印表面 16%，不得抢主元素尺寸。
- palette = the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝)。
- 视觉权重（本款硬要求）：主元素约占可印表面 56%，Narrative 合计约 32%，Rhythm 合计约 12%。主元素的线性尺寸至少是 Narrative 的 1.8 倍、Rhythm 的 3.6 倍；除主元素外，任何图案不得接近它的尺寸。图案可以重复，也可被接缝/褶皱自然裁切；但主元素必须始终是本款最大的图案，一眼可辨，不得被叙事层或背景纹样淹没。空白只能作为图案之间的细缝，不能出现比一个主元素还大的连续底色。
- 满印密度（本款硬要求）：可印表面覆盖率 ≥ 90%，不允许出现比一个主元素还大的连续空白底色；三层必须按上述面积份额同时铺满整个可印表面，只在局部放几个图案视为失败。
- 排布方式（重复/错位/拼贴/条纹等）可自由选择，但不得降低上一条的密度；不得因此改变相机、景别或四格角色。
- 本款配方只作用于本象限：其他象限的元素、配色与尺度在本象限禁止出现。
Recipe signature: STYLE-001 | elements=hero:秃鹫 narrative:陶罐,套索 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈鼠尾草绿(60%) / structural≈砖红(30%) / highlight≈奶油白(10%) | weight=56/32/12
- 60-30-10 配色：base≈鼠尾草绿(60%) / structural≈砖红(30%) / highlight≈奶油白(10%)；主基调覆盖大面积底色，结构副色用于提手/包边，点缀色只用于金线/金属件/小坠饰。

QUADRANT TOP-RIGHT — style 2 (this quadrant only):
- 本款印花主元素：陶罐（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）
- 印花叙事层 Narrative：套索, 仙人掌（Tier 2，尺度 0.55，与主元素交替出现）
- 印花点缀层 Rhythm（自选）：请从同主题中自选 2-3 个小纹样/纹理（如雪花、星星、铃铛、格纹）作为点缀填充，合计约占可印表面 16%，不得抢主元素尺寸。
- palette = the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝)。
- 视觉权重（本款硬要求）：主元素约占可印表面 56%，Narrative 合计约 32%，Rhythm 合计约 12%。主元素的线性尺寸至少是 Narrative 的 1.8 倍、Rhythm 的 3.6 倍；除主元素外，任何图案不得接近它的尺寸。图案可以重复，也可被接缝/褶皱自然裁切；但主元素必须始终是本款最大的图案，一眼可辨，不得被叙事层或背景纹样淹没。空白只能作为图案之间的细缝，不能出现比一个主元素还大的连续底色。
- 满印密度（本款硬要求）：可印表面覆盖率 ≥ 90%，不允许出现比一个主元素还大的连续空白底色；三层必须按上述面积份额同时铺满整个可印表面，只在局部放几个图案视为失败。
- 排布方式（重复/错位/拼贴/条纹等）可自由选择，但不得降低上一条的密度；不得因此改变相机、景别或四格角色。
- 本款配方只作用于本象限：其他象限的元素、配色与尺度在本象限禁止出现。
Recipe signature: STYLE-002 | elements=hero:陶罐 narrative:套索,仙人掌 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈落日金橙(60%) / structural≈深棕(30%) / highlight≈靛蓝(10%) | weight=56/32/12
- 60-30-10 配色：base≈落日金橙(60%) / structural≈深棕(30%) / highlight≈靛蓝(10%)；主基调覆盖大面积底色，结构副色用于提手/包边，点缀色只用于金线/金属件/小坠饰。

QUADRANT BOTTOM-LEFT — style 3 (this quadrant only):
- 本款印花主元素：套索（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）
- 印花叙事层 Narrative：仙人掌, 牛仔帽（Tier 2，尺度 0.55，与主元素交替出现）
- 印花点缀层 Rhythm（自选）：请从同主题中自选 2-3 个小纹样/纹理（如雪花、星星、铃铛、格纹）作为点缀填充，合计约占可印表面 16%，不得抢主元素尺寸。
- palette = the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝)。
- 视觉权重（本款硬要求）：主元素约占可印表面 56%，Narrative 合计约 32%，Rhythm 合计约 12%。主元素的线性尺寸至少是 Narrative 的 1.8 倍、Rhythm 的 3.6 倍；除主元素外，任何图案不得接近它的尺寸。图案可以重复，也可被接缝/褶皱自然裁切；但主元素必须始终是本款最大的图案，一眼可辨，不得被叙事层或背景纹样淹没。空白只能作为图案之间的细缝，不能出现比一个主元素还大的连续底色。
- 满印密度（本款硬要求）：可印表面覆盖率 ≥ 90%，不允许出现比一个主元素还大的连续空白底色；三层必须按上述面积份额同时铺满整个可印表面，只在局部放几个图案视为失败。
- 排布方式（重复/错位/拼贴/条纹等）可自由选择，但不得降低上一条的密度；不得因此改变相机、景别或四格角色。
- 本款配方只作用于本象限：其他象限的元素、配色与尺度在本象限禁止出现。
Recipe signature: STYLE-003 | elements=hero:套索 narrative:仙人掌,牛仔帽 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈砖红(60%) / structural≈奶油白(30%) / highlight≈鼠尾草绿(10%) | weight=56/32/12
- 60-30-10 配色：base≈砖红(60%) / structural≈奶油白(30%) / highlight≈鼠尾草绿(10%)；主基调覆盖大面积底色，结构副色用于提手/包边，点缀色只用于金线/金属件/小坠饰。

QUADRANT BOTTOM-RIGHT — style 4 (this quadrant only):
- 本款印花主元素：仙人掌（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）
- 印花叙事层 Narrative：牛仔帽, 土狼（Tier 2，尺度 0.55，与主元素交替出现）
- 印花点缀层 Rhythm（自选）：请从同主题中自选 2-3 个小纹样/纹理（如雪花、星星、铃铛、格纹）作为点缀填充，合计约占可印表面 16%，不得抢主元素尺寸。
- palette = the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝)。
- 视觉权重（本款硬要求）：主元素约占可印表面 56%，Narrative 合计约 32%，Rhythm 合计约 12%。主元素的线性尺寸至少是 Narrative 的 1.8 倍、Rhythm 的 3.6 倍；除主元素外，任何图案不得接近它的尺寸。图案可以重复，也可被接缝/褶皱自然裁切；但主元素必须始终是本款最大的图案，一眼可辨，不得被叙事层或背景纹样淹没。空白只能作为图案之间的细缝，不能出现比一个主元素还大的连续底色。
- 满印密度（本款硬要求）：可印表面覆盖率 ≥ 90%，不允许出现比一个主元素还大的连续空白底色；三层必须按上述面积份额同时铺满整个可印表面，只在局部放几个图案视为失败。
- 排布方式（重复/错位/拼贴/条纹等）可自由选择，但不得降低上一条的密度；不得因此改变相机、景别或四格角色。
- 本款配方只作用于本象限：其他象限的元素、配色与尺度在本象限禁止出现。
Recipe signature: STYLE-004 | elements=hero:仙人掌 narrative:牛仔帽,土狼 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈深棕(60%) / structural≈靛蓝(30%) / highlight≈落日金橙(10%) | weight=56/32/12
- 60-30-10 配色：base≈深棕(60%) / structural≈靛蓝(30%) / highlight≈落日金橙(10%)；主基调覆盖大面积底色，结构副色用于提手/包边，点缀色只用于金线/金属件/小坠饰。

=== PART 4 · ART DIRECTION — one shared set for all four quadrants ===
ART DIRECTION — batch-wide set design, shared by every quadrant of this round:
- SCENE: 晨光里的木质厨房岛台，右侧窗户透进条状光
- LIGHTING: 低角度暖色侧光 + 漫射环境补光，产品下方有柔和接触阴影
- COLOR TEMPERATURE: 偏暖，2700-3200K
- MOOD: 温暖、从容、有生活痕迹
- PALETTE: 落日金橙, 鼠尾草绿, 奶油白
- BACKGROUND: 浅米色抹灰墙面，轻微虚化
- COMPOSITION: 产品居中偏右，左侧留白放道具
- PROPS (near / mid / far layers, at least two materials, never occlude the product):
  · 釉面陶瓷杯
  · 厚切木砧板
  · 黄铜壶尤加利叶
  · 亚麻餐巾
- AVOID: 冷白光, 纯白影棚背景, 塑料质感道具
- All four quadrants share this ONE set, this ONE lighting setup and this ONE colour temperature. Never build four different sets in one sheet.
ART DIRECTION QUALITY (always on top of the composition direction above):
- ONE clearly directional key light with a readable direction, defined highlights, a soft contact shadow under the product and natural falloff; flat, shadowless, evenly lit studio light is a FAILURE.
- At least one visible warm light source or a clearly readable warm/cool contrast in frame.
- Shallow depth of field with a soft background, plus a blurred foreground element when the composition allows it.
- The product stays the hero: fully visible, sharply in focus, never occluded by props.
- PRINTED MOTIFS ARE 2D ARTWORK: never turn a printed motif into a 3D prop or object.
FINAL CHECK — four equal panels, one clean divider, four DIFFERENT designs from one collection, one shared camera and one shared set, no text or labels.
```

### 3.3 派生轮（细节 A，完整）

```text
=== ROUND 细节图 A · A2 — hard output format + subject anchoring ===
This call produces the SAME four styles of the same contact sheet, seen from a clearly DIFFERENT camera than the reference images. The four attached images are the accepted round-1 result and the ONLY source of truth for product structure and printed artwork.
OUTPUT FORMAT LOCK (mandatory in rounds 2/3/4 as well) — every call returns ONE square 2x2 contact sheet with exactly four equal panels and one clean centred divider, same format as round 1. Never return a single full-frame image, a 1x4 or 4x1 strip, unequal panels, a collage with borders, or any number of panels other than four. "This call only needs the detail image" never means "return one image": the call still returns four detail panels, one per style.
ANCHOR BINDING — the four attached reference images ARE the accepted round-1 result. They are the FOUR different styles of this contact sheet, bound by position, not by content:
- reference image 1 = the product shown in the TOP-LEFT quadrant
- reference image 2 = TOP-RIGHT
- reference image 3 = BOTTOM-LEFT
- reference image 4 = BOTTOM-RIGHT
Each quadrant uses ONLY its own reference image. Never regenerate the product or the artwork from scratch, and never copy, blend or swap motifs, colours or layouts between quadrants.
RIGID ARTWORK LOCK — the printed artwork is a rigid sheet glued to the product. Changing the camera rotates the whole sheet with the product; it is never permission to re-layout, re-centre, rotate, rescale or re-space the artwork. The hero motif's position on the product face, its size relative to the face, its orientation and its distance from seams/edges must be identical to the anchor. A detail panel is a zoom into the SAME layout, never a new version of the artwork.
ANCHOR CONTINUITY — keep the anchor's IDENTITY, never its camera:
- MUST stay identical: product identity, structure, material, base colour, printed artwork, scene, set surface, light direction, colour temperature and contrast.
- MUST change with this round's direction: camera angle, shot distance, crop, framing and depth of field. A macro round is expected to have shallow focus even when the anchor is deep-focus.
- Never reproduce the reference camera: a panel that is the same shot as its reference image (same distance, same crop, same full-product view) is a FAILED result.
- If the previous round's scene cannot be reproduced from the anchor, keep the anchor's scene — never invent a new set.
QUADRANT ISOLATION LOCK — the four panels are four DIFFERENT product designs (four styles of one collection) on the SAME physical product. Each quadrant's motifs, palette, scale rhythm and rendering are exclusive to that quadrant. Never copy, blend, bleed or swap any motif or colour across quadrants or across the divider. If two quadrants read as the same design, the result is FAILED.
NO QUADRANT LABELS: never write A, B, C, D, 1, 2, 3, 4 or any letter/number in the corners or near the four-panel divider. The four panels must be separated only by the clean divider; no text, captions, labels, stamps, signatures or watermarks anywhere in the image.
GEOMETRY LOCK: keep the silhouette, seam lines, panel divisions, hardware positions and proportions of the reference exactly; never redraw, reshape or re-proportion the product.
PRODUCT FORM & SIZE LOCK — the product form and dimensions stated in the batch facts and batch hard rules are geometric facts and outrank the template's apparent proportions: if the product is flat (tablecloth, mat, towel, blanket, sticker, paper), it must read as a FLAT SHEET lying on the tabletop — never as a box, cube, slab, stack, cushion, tray, molded shell or other 3D block. Keep stated sizes and coverage literal (for example a square 80x80 tablecloth must stay square, cover the tabletop and have no overhang). Corners and edges must stay paper-thin and flat: no rounded bulge, no raised rim, no thick bevel, no table skirt, no drape and no hanging corner. Never thicken, pad, inflate or extrude a flat product to give it volume. Every panel must preserve the exact flat square silhouette of the template.
FLAT PRINT LOCK: every motif is a 2D printed graphic on the product surface. Coins, candles, stars, ornaments or any other motif must have no thickness, no emboss, no raised relief and no cast object shadow; never place them as separate 3D objects on top of the product.
Do not add text, captions, logos, labels, watermarks, collages or borders, and never copy one quadrant's artwork into another quadrant.

=== PART 2 · ROUND 细节图 A SCENE & COMPOSITION — camera only ===
ROUND SHOOTING DIRECTION (all four quadrants of this sheet share it):
A tight close-up of the printed artwork on the same product, revealing print texture and material detail.
Framing-only clause: the sentence above changes only the camera — angle, shot distance, crop, framing and depth of field (a macro round is expected to have shallow focus). Scene, set surface, light direction, colour temperature and contrast must stay identical to the four reference images.
CAMERA MUST DIFFER — this round's camera must be visibly different from the reference images' camera and from every other round: different shot distance and different framing. Reproducing the reference framing (same distance, same crop, same full-product view) is a FAILED result.

CAMERA REMINDER (last check) — shoot this round as: A tight close-up of the printed artwork on the same product, revealing print texture and material detail. The camera must be visibly different from the anchor's camera; copying the anchor's framing (same distance, same full-product view) is a FAILED result.
```

### 3.4 派生轮之间的差异

- `hero`（素材图）只多一行：
  `MATERIAL PANEL: unless the direction above specifies another background, use a clean neutral ecommerce background with no props; the full design must stay sharply visible.`
- 其余 `detail_b`、`hero` 与 `detail_a` 的差异仅在 `ROUND SHOOTING DIRECTION` 那一句
  （由构图模板对应 slot 的 `en` 文本决定，未配置时走 `_default_round_direction`）。
- `attempt=2` 只在结尾追加，不改配方、不改锚点绑定：

```text
RETRY ATTEMPT 2 OF 2:
The first result was invalid: it was not a usable four-panel sheet or a panel was blank. Return four valid panels, each still anchored to its own reference image; do not redesign the artwork.
```

### 3.5 用户构图与批级美术指导的注入位置

| 来源 | 注入点 | 生效范围 |
| --- | --- | --- |
| 构图模板 `panel_1`（en） | 第 1 轮 `B1 · ROUND-1 SHOOTING DIRECTION` | 该组四格共享 |
| 构图模板 `panel_2/3/4`（en） | 第 2/3/4 轮 `B2 · ROUND SHOOTING DIRECTION` | 对应轮四格共享 |
| `ArtDirection` | 第 1 轮 `D1 · ART DIRECTION`（场景 / 光影 / 道具） | 整批（所有组）共用 |
| `ArtDirection.scene_options` | 按组序轮换，取其中一组的 `scene` / `surface` / `props` | 逐组轮换 |
| 未配置 `ArtDirection` | 回退 `_column_set_design_block`（一组一景）或 `_flat_set_design_block`（平铺） | 逐组轮换 |

第 1 轮带用户构图时，PART 2 会变成：

```text
=== PART 2 · SCENE & COMPOSITION — this round's single shared camera ===
ROUND-1 SHOOTING DIRECTION (applies to all four quadrants of this sheet; read it literally, do not average it into a compromise framing):
Slightly low 45-degree oblique medium-full shot, full product in frame.
SAME CAMERA, DIFFERENT ARTWORK — all four quadrants share one camera setup, one lens, one framing and one lighting direction (this round's direction). The four quadrants must differ by ARTWORK, never by camera angle. Four different elevations, crops or scene styles in one sheet = FAILED.
```

---

## 4. 默认构图模板（预置）

### 4.1 四格默认模板（legacy / 复刻 / 裂变共用语义）

原始输入：`系统默认机位（与未配置构图时一致）`

**DEFAULT_COMPOSITION_PANELS**

| slot | zh | en |
| --- | --- | --- |
| `panel_1` | 平视自然机位的生活场景主图：完整产品置于真实使用场景中，可搭配少量低调衬托，光线柔和自然。 | Natural eye-level lifestyle hero shot: the complete product placed in a real, usable setting with a few subtle supporting props under soft natural light. |
| `panel_2` | 产品正面图案的高清微距特写，清楚呈现图案纹理与基底材质。 | Tight high-resolution macro close-up of the product's front surface artwork, clearly showing the pattern texture and base material. |
| `panel_3` | 另一处结构或材质细节的近景（如四分之三视角、边角或提手结构）。 | Close-up of a different structural or material detail (for example a three-quarter view, an edge, or the handle construction). |
| `panel_4` | 完整产品居中置于干净的中性纯白背景，规整正面拍摄，不要衬托。 | The complete product centered on a clean neutral pure-white background, neat straight-on framing with no supporting props. |

### 4.2 四轮默认模板（列式专用）

原始输入：`系统默认：四轮差异化机位（主图 45° 三分点 / 细节 A 90° 俯拍占九成 / 细节 B 极端微距出画 / 素材居中六成）`

**DEFAULT_COLUMN_COMPOSITION_PANELS**

| slot | zh | en |
| --- | --- | --- |
| `panel_1` | 略低机位、斜侧 45° 的中全景，主体完整入画、约占画面七成，落在画面三分点位置，另一侧留出大面积留白，采用低角度掠射侧光搭配漫射环境补光打出立体感与柔和投影，背景大幅虚化、仅保留柔和中性的模糊层次。 | Slightly low camera position, 45-degree oblique angle medium-full shot, the complete subject occupies about 70% of the frame, placed at the rule-of-thirds intersection, with large blank space on the other side; low-angle raking side light combined with diffused ambient fill creates dimensionality and soft shadows, the background is heavily blurred, retaining only soft neutral hazy layers. |
| `panel_2` | 正上方 90° 垂直俯拍，机位抬升至主体正上方，主体完整入画、四边几乎贴住画面边缘、占满约九成画面，画面内不出现任何边缘与衬托物，采用与主图同源的均匀柔和漫射顶光，背景处理保持同主图的虚化质感。 | Direct 90° top-down overhead shot, the camera is raised directly above the subject, the complete subject occupies about 90% of the frame with its edges nearly touching the frame borders, no edges or supporting objects appear in the frame; use even, soft diffused top light consistent with the main image, the background maintains the same blurred texture as the main image. |
| `panel_3` | 机位贴近主体表面做极端微距拍摄，画面仅保留约十分之一面积的局部纹理，主体完整轮廓与背景全部出画，浅景深效果使焦点精准落在纹理结构上，采用与主图同源的侧向柔光，无多余衬托元素入镜。 | Extreme macro shot with the camera positioned close to the subject surface, the frame only retains a local textured area occupying about one-tenth of the total image area, the full outline of the subject and the entire background are cropped out of frame; shallow depth of field places sharp focus on the textured structure, use side soft light consistent with the main image, no extra supporting elements enter the frame. |
| `panel_4` | 正上方垂直俯拍，主体居中放置、约占画面六成，四边等距留出均匀空白，采用均匀无阴影的平光照明，背景为纯净中性的干净处理，无任何多余环境元素。 | Direct top-down overhead shot, the subject is centered in the frame, occupying about 60% of the image area, with even equal margins of blank space on all four sides; use shadowless even flat lighting, the background is a clean, pure neutral surface with no extra environmental elements. |

### 4.3 两套模板不能互换

`DEFAULT_COMPOSITION_PANELS` 描述「一张图的四格怎么拍」；
`DEFAULT_COLUMN_COMPOSITION_PANELS` 描述「四次调用的四轮怎么拍」。
列式链路只读后者，复刻 / 裂变只读前者（[prompts.py](../../../local-runtime/wh_local/modules/pod_customization/prompts.py#L281-L349)）。

---

## 5. 动态片段与轮转规则

> v21 起代码不再维护任何池：元素来自智能填写；场景来自豆包返回的 `scene_options`；
> 配色来自智能填写；印花排布、疏密与工艺交给生图模型自由发挥。

### 5.1 元素 Tier

- 元素池只收「能单独放大成主图」的具象主体（人物 / 动物 / 建筑 / 器皿大物件 / 节日形象）；
  材质纹理、几何网纹、细碎点缀不进池，由生图模型在点缀层自选
- 数量：下限 12、建议 12-24，宁少勿滥；不再强制凑到 16
- 主题一致性：每个词必须能直接从 `design_theme` 推出；只属于季节或泛冬季的动物
  （北极熊、企鹅、海豹、雪鸮）不算节日元素
- 命名：元素名统一以主题词开头（节日优先，如「圣诞」），已有前缀不重复加；
  这是提示词软约束，代码不强制补前缀
- 去重：后缀包含的近义变体只保留基础名词（「企鹅」与「圣诞企鹅」→ 只留「企鹅」），
  由 `dedupe_style_elements` 确定性合并
- 智能填写默认**关闭**深度思考（`thinking=disabled`）：实测纯文本约 5 秒；
  开启后约 50 秒，同一份 brief 的元素质量接近，因此默认走快路径
- 切分：只按顿号 / 逗号 / 分号 / 换行切，不按空格切（避免拆散 `Seamless Pattern`）
- 洗牌：`sha256(batch_id)` 播种，同批可复现、跨批变化
- 分配：`hero` 按款序轮换取 1 个（相邻款必不同）；`narrative` 紧随其后 2 个；
  **`rhythm` v24 起不再从池分配**，提示词改为让模型按同主题自选 2-3 个小纹样/纹理
- 尺度：hero 1.0 / narrative 0.55 / rhythm 0.28（线性），面积按 `数量 × 尺度²` 归一化；
  rhythm 为空时按 3 个自选点缀预留面积
- 典型面积份额：1 主锚 + 2 叙事 + 3 自选点缀时约 56% / 32% / 12%；
  主元素线性尺寸 ≥1.8× narrative、≥3.6× rhythm
- 款内不重复；旧批次 `primary/co/accents` 自动降级映射

### 5.2 60-30-10 配色

- 色板只来自用户 / 智能填写；**没给颜色时不回退代码色板**，由模型按主题自行选择
- 色板 < 3 时不做 60-30-10 分配，不凭空造色
- `base` 按款序轮换、`structural` 与 `base` 至少隔 `len//3`、`highlight` 避开前两者

### 5.3 场景候选（scene_options）

- 豆包在一次视觉调用里返回 2-3 个候选场景，每个包含 `scene`（环境 / 地点 / 时间感）、
  `surface`（台面材质，平铺类必填）、`props`（3-5 件真实衬托道具）
- 生图时按款序（legacy）或组序（列式）轮换：`pick_scene_option`
- 候选之间要求「环境 / 台面 / 道具族 / 色温至少两项不同」，不能是同一场景的三种说法
- 旧数据没有候选时用中性兜底句，不再读取本地场景池

### 5.4 已删除的本地池

v21 起以下池全部删除，代码里不再维护：

- 布局池（pattern_layout）
- 渲染工艺池（rendering）
- 密度池（density）
- 配色池（palette）
- 场景池（scene）
- 平铺摄影背景池（flat-lay surface）

半定制的四格差异现在只由「元素层级 + 用户配色」承担；排布、疏密、工艺由模型自行决定。

---

## 6. 变体矩阵

| 条件 | 提示词变化 | 影响范围 |
| --- | --- | --- |
| 无用户构图 | 使用 `_DEFAULT_LISTING_PANEL_LINES` / `_default_round_direction` | PART 2 |
| 有用户构图 | 用户方向升为最高优先级 + 「四格必须互不相同」+ 固定角色 | PART 2 |
| 平铺类产品 | 四格全俯拍 + 平铺布景块 + `FLAT SHEET PANEL LOCK` | PART 2 / PART 4 |
| 模板有结构事实 | 追加 `STRUCTURE FACTS` + 部件数锁 + 透明材质锁 + 多视图契约 | PART 1 |
| 豆包 + 多结构视图 | 下发多参考图（主 / 侧 / 俯） | 请求参数 |
| 速创模型 | 强制单主图 | 请求参数 |
| `attempt=2` | 仅追加 retry 段，不重抽配方 | 末尾 |
| 列式第 1 轮 | 含四份配方 + 美术指导 | 每组一次 |
| 列式派生轮 | 只含格式锁 / 锚点绑定 / 机位 | 每组三次 |
| 列式续跑 / 重启 | 复用 `group_sheets.prompt_snapshot`，不重新拼装 | 整个轮次 |

---

## 7. 维护约定与已知风险

### 7.1 版本号

- 只要改动提示词**内容**（含常量池、硬锁文案、拼接顺序），必须递增对应 version 常量
- 版本号随批次/计划冻结，是事后区分「改前 / 改后」产出的唯一依据；只改文案不升版本会让新旧产出无法归因

### 7.2 已知耦合

1. v21 起代码里不再有任何布局 / 渲染 / 密度 / 配色 / 场景 / 平铺背景池；再补差异只能走「美术指导字段」或模型自由发挥
2. `PATTERN_PROMPT_VERSION` 与 `COLUMN_PROMPT_VERSION` 是两个独立常量，当前都是 `v25`，未来容易只升一个
3. 两套内置硬性规则（`_BUILTIN_HARD_RULES` / `_BUILTIN_COLUMN_RULES`）内容高度重叠，需要手工保持一致
4. `build_style_listing_prompt` 接受 `creative_prompt` 参数但函数体内未使用；创意编辑实际只通过建批时的 `subject_anchor` 进入底座
5. 列式第 1 轮是整条链路里最长的提示词（示例 13934 字符），硬锁堆叠依赖「开头 + 结尾」重复来压模型漂移
6. 场景候选只有 2-3 个，款数超过 3 时必然复用；款间差异需要靠元素层级与配色补足
7. 旧数据没有 `scene_options` 时用中性兜底句，不会报错，但也没有「豆包设计的场景」

---

## 附录 A：示例批次字段

| 字段 | 值 |
| --- | --- |
| product_name | 复古花卉印花托特包 |
| product_category | 女士手提包 |
| target_market | 美国 |
| target_audience | 25-40 岁通勤女性 |
| core_selling_points | 可放 13 寸笔记本 / 加厚帆布肩带 / 整面满印复古花卉 / 可机洗 |
| design_theme | 美式西南复古荒野风 |
| fabric_craft | 绗缝 + 平车明线 |
| color_preferences | 落日金橙 / 鼠尾草绿 / 奶油白 / 深棕 / 砖红 / 靛蓝 |
| excluded_elements | 机械零件（另有系统固定的 `IP_EXCLUDED_ELEMENTS` 自动补齐） |
| style_index | 1–4 |
| seed | `demo-batch-0001` |
| style_elements（智能填写产物，16 个具象主体） | 仙人掌 / 牛仔帽 / 牛仔靴 / 马蹄铁 / 土狼 / 秃鹫 / 响尾蛇 / 图腾柱 / 陶罐 / 马车轮 / 马刺 / 套索 / 篝火 / 帐篷 / 野马 / 水牛 |

示例配方签名（`rhythm:-` 表示本款点缀由生图模型自选）：

```text
STYLE-001 | elements=hero:秃鹫 narrative:陶罐,套索 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈鼠尾草绿(60%) / structural≈砖红(30%) / highlight≈奶油白(10%)
STYLE-002 | elements=hero:陶罐 narrative:套索,仙人掌 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈落日金橙(60%) / structural≈深棕(30%) / highlight≈靛蓝(10%)
STYLE-003 | elements=hero:套索 narrative:仙人掌,牛仔帽 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈砖红(60%) / structural≈奶油白(30%) / highlight≈鼠尾草绿(10%)
STYLE-004 | elements=hero:仙人掌 narrative:牛仔帽,土狼 rhythm:- | palette=the brief's specified colors (落日金橙, 鼠尾草绿, 奶油白, 深棕, 砖红, 靛蓝) | color_harmony=base≈深棕(60%) / structural≈靛蓝(30%) / highlight≈落日金橙(10%)
```

---

## 附录 B：重新生成本文

```bash
cd /Users/Zhuanz/Desktop/MainPG

local-runtime/.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "local-runtime")

from wh_local.modules.pod_customization.contracts import BusinessFields
from wh_local.modules.pod_customization.prompts import (
    assign_style_elements,
    build_direct_listing_prompt,
    build_style_listing_prompt,
)

fields = BusinessFields(
    product_name="复古花卉印花托特包",
    product_category="女士手提包",
    design_theme="美式西南复古荒野风",
    color_preferences=["落日金橙", "鼠尾草绿", "奶油白"],
)
keywords = [
    "仙人掌","牛仔帽","牛仔靴","马蹄铁","土狼","秃鹫","响尾蛇","图腾柱",
    "陶罐","马车轮","马刺","套索","篝火","帐篷","野马","水牛",
]
base = build_direct_listing_prompt(fields, "")
elements = assign_style_elements(keywords, 1, "demo-batch-0001")
print(build_style_listing_prompt(
    base,
    style_index=1,
    attempt=1,
    business_fields=fields.model_dump(),
    style_elements=elements,
    seed="demo-batch-0001",
))
PY
```

列式同理：`build_column_base_prompt` + `build_column_style_recipes` + `build_column_round_prompt`（第 1 轮用 `PRIMARY_ROLE`）。

# 产品标题与图片内容参考库设计

## 目标

在现有产品处理流程中增加两个只影响内容方向的离线参考库：

1. 标题提示词参考库：按已经确认的商品类目和真实属性，选择不同的标题组织方式。
2. 图片生成参考库：按已经确认的商品类目和真实属性，选择更相符的场景、背景、灯光和构图方向。

同一商品重复处理时选择稳定；不同商品即使同类目也可落到不同的已审核变体，减少千篇一律。运行期不联网、不新增 AI 调用，不修改商品类目、属性或店小秘导出字段。

## 已冻结边界

- 现有 `category`、`source_category_path`、`category_path`、`category_id`、`leaf_category_id` 和属性结果是权威输入。
- 不增加类目识别、候选排序、AI 类目判断、类目回写或类目纠错。
- 不修改店小秘模板匹配和导出映射。
- 不修改标题语言、字符上下限、真实性、敏感词等硬规则。
- 不修改图片四宫格、分割、OCR 中文重绘、详情图合成、发布与回退逻辑。
- 外部来源中的字符限制、平台规则、画幅、宫格数量、分辨率、广告承诺、认证、评价、销量和比较性话术一律不能进入运行期参考内容。
- 参考库没有匹配项或加载失败时，静默使用通用参考；仍失败时只使用现有提示词，不能影响产品处理成功率。

## GitHub 来源与许可

以下来源按固定提交版本研究和转化。仓库原文不会在运行期联网读取。

### 标题内容来源

| 来源 | 固定提交 | 许可 | 使用范围 |
| --- | --- | --- | --- |
| [xpaysh/ecommerce-prompts-mcp](https://github.com/xpaysh/ecommerce-prompts-mcp) | `f44451c37c869e330d61031351842d9a40695346` | MIT | 43 个商品类目、924 条提示中的类目属性维度和 151 条标题/SEO 相关结构 |
| [nexscope-ai/Amazon-Skills](https://github.com/nexscope-ai/Amazon-Skills) | `bdc556233805bbc3b5d8d865f3f3bd153864970f` | MIT | 商品类型、真实属性、关键词自然排序和图片角色思路 |
| [wei910622-cell/AMZ-Skill](https://github.com/wei910622-cell/AMZ-Skill) | `71c4c7656bc674ba17c930eaafb7f928ec1cf26c` | MIT | 商品事实优先、关键词溯源、父子变体标题一致性思路 |
| [coral870921-source/Ozon-Profit-Skills](https://github.com/coral870921-source/Ozon-Profit-Skills) | `4f883a1facf47496c2e7b8907fd8d11f8110100c` | MIT | 跨境商品标题的属性排序参考 |

### 图片内容来源

| 来源 | 固定提交 | 许可 | 使用范围 |
| --- | --- | --- | --- |
| [JeremyGDM/awesome-ai-product-photography-prompts](https://github.com/JeremyGDM/awesome-ai-product-photography-prompts) | `6815dab9c17ce20c5c554df4ecf28336fd0aef0a` | CC0-1.0 | 122 条商品摄影、食品饮料和海报样例中的通用视觉构件 |
| [buluslan/gpt-image2-ecommerce](https://github.com/buluslan/gpt-image2-ecommerce) | `4422c530b4fa873f5c398190fc8071a518bdd17e` | MIT | 25 个结构化电商场景模板和类目提示 |
| [skyiron999/product-photo-ai-workflows](https://github.com/skyiron999/product-photo-ai-workflows) | `75931104c59a484239335da2b7c1612999862771` | MIT | 商品锁定、材质保真、样式卡和质量检查方法 |
| [creatify-ai/ai-ad-prompt-guide](https://github.com/creatify-ai/ai-ad-prompt-guide) | `115b3988d89a9d21d1114e59611f82b4612ab660` | MIT | Subject/Lighting/Camera/Technical 的结构化图片描述方法 |
| [creatify-ai/static-ad-concept-generator](https://github.com/creatify-ai/static-ad-concept-generator) | `4d7e51ce58b7e90f5f0a7d861be7248678e574d5` | MIT | 通用视觉表现类型；所有广告承诺和虚构社会证明均剔除 |
| [yoyo-halo/cross-border-ecommerce-image-prompts](https://github.com/yoyo-halo/cross-border-ecommerce-image-prompts) | `d7d89f8582880bf7b2b8458a28178b354667df8d` | MIT | 跨境主图、场景图、背景替换和特征展示结构 |

没有明确开源许可证的仓库不复制、不随项目分发，只作为检索阶段的方向验证，不列入运行期来源。

## 参考库结构

新增一个纯 Python、只读、无 I/O 的 `content_reference_library.py`，与现有 `visual_planner.py` 分离，避免把内容多样性与类目判断混在一起。

### 类目档案

- 以 43 个已调研电商商品大类作为基础内容档案，并补充 MainPG 现有视觉族中缺失的 9 个专项档案（乐器、工具五金、家居收纳、桌布、软家纺、灯具电气、派对节庆、美容配件、包装袋）。
- 每个档案包含：匹配别名、标题属性优先级、标题组织变体、图片场景变体、适用的属性触发模块。
- 额外保留 `general` 通用档案，覆盖未命中的店小秘叶子类目。
- 类目匹配只读取最终类目 ID/路径和名称；匹配结果仅决定参考内容，绝不写回商品类目。
- 叶子路径词命中优先于大类词命中；没有命中时使用通用档案。

### 标题参考

标题参考只描述内容顺序，不带任何长度或平台硬规则。每条参考由以下部分组成：

- 精确商品类型放置位置；
- 本类目优先考虑的真实属性顺序；
- 一个稳定选择的结构变体；
- 最多两个由真实来源属性触发的补充角度；
- 明确要求缺少证据时省略槽位。

参考示意：`product type -> verified capacity/size -> verified material -> supported use -> real pack count`。所有槽位都必须从现有 `value_evidence`、标题或结构化属性中取值，不能由参考库生成具体事实。

### 图片参考

图片参考只描述内容方向，不带宫格数量、尺寸、分辨率或文字覆盖要求。每条参考包含：

- 主体展示重点；
- 四个可供现有四宫格规则使用的差异化场景角色；
- 背景、灯光、镜头/构图和材质表现建议；
- 最多两个由真实属性触发的视觉模块；
- 商品原图始终是唯一视觉事实来源。

现有 `GRID_IMAGE_PROMPT` 继续控制精确 2x2 四宫格；参考库只给四个面板“拍什么”的内容建议。现有 `DETAIL_IMAGE_PROMPT` 继续控制详情海报结构。

## 选择算法

1. 读取已有最终 `category_id`、`category_path`、`category` 和结构化属性。
2. 只在本地类目档案中选择内容档案。
3. 使用 `category_id | category_path | source_product_id/candidate_id/SKC | source title` 的 SHA-256 生成稳定种子。
4. 用稳定种子选择标题结构和图片场景变体。
5. 根据真实属性标签附加最多两个适用模块；没有真实值时不附加。
6. 输出有长度上限的参考文本和可观察的参考 ID。

该算法不使用随机数、网络、数据库或 AI；同一输入结果可复现。不同商品 ID 会自然分散到不同的审核变体。

## 提示词注入

- 保持 `DEFAULT_PROMPTS` 原文硬规则不变。
- 在现有提示词完成语言契约和变量渲染后，追加一个明确标记为 `CONTENT REFERENCE ONLY` 的短区块。
- 标题参考注入 `_generate_combined_text` 和 `_generate_title`。
- 图片参考注入 `_generate_grid_images` 和 `_generate_detail_images`。
- `size`、`variant_values`、OCR 修复、图片拆分和本地详情图合成不注入。
- 参考区块声明：与前文规则冲突时以前文规则为准；不得补造事实。
- 自定义提示词仍可使用；参考区块同样追加，避免管理员自定义模板导致类目参考失效。

## 性能与稳定性

- 参考库在模块导入时构造不可变常量，单次选择只做字符串标准化、有限别名匹配和哈希。
- 不新增 AI 调用，不新增并发任务，不访问文件、数据库或网络。
- 每个追加区块设置字符上限，避免提示词无限增长。
- 现有 AI 阶段缓存会因为最终提示词变化自然产生新版本缓存；相同商品后续仍稳定命中。
- 参考库异常时捕获并返回空参考，现有流程继续。

## 可观察性

- `ai_notes` 记录 `title_reference:<profile>/<variant>` 与 `image_reference:<profile>/<variant>`。
- 不把参考 ID 写入店小秘模板字段。
- 第三方来源、许可、固定提交和转化说明写入项目 NOTICE 文档。

## 验证标准

1. 43 个来源类目档案、9 个项目专项档案和通用档案均能加载，别名无重复冲突。
2. 同一商品多次选择结果完全一致；一批不同商品至少分布到多个变体。
3. 未知类目、空属性和库异常均回退通用或空参考，不中断流程。
4. 参考文本不含外部字符限制、平台名、宫格数量、分辨率、销量/评价/认证/折扣等硬控制或虚构内容。
5. 标题基础提示词原有长度、语言、真实性和敏感词规则仍存在。
6. 图片基础提示词原有精确四宫格、商品保真、OCR/分割链路仍存在。
7. 输入的类目 ID、类目路径和属性在选择前后完全相同；店小秘导出映射文件不修改。
8. 标题/图片生成调用次数不增加，参考选择不进行任何网络或文件访问。

## 交付文件

- `local-runtime/wh_local/modules/product_processing/domain/content_reference_library.py`
- `local-runtime/wh_local/modules/product_processing/domain/content_reference_sources.json`
- `local-runtime/wh_local/modules/product_processing/THIRD_PARTY_NOTICES.md`
- `local-runtime/wh_local/modules/product_processing/service.py` 的窄注入修改
- `local-runtime/tests/test_product_processing_content_references.py`
- `local-runtime/tests/test_product_processing_reference_integration.py`

不修改 `domain/workbooks.py`、类目结果生成逻辑、数据库迁移、API Schema 和前端。

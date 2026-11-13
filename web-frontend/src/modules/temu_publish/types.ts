export type TemuSettingsStatus = {
  configured: {
    yl_client: boolean;
    yl_token: boolean;
    access_token: boolean;
    ready: boolean;
  };
  source: Record<string, "database" | "environment" | "unset">;
  hints: Record<string, string>;
  /** 凭证归属：按登录账号隔离，personal=true 表示用的是自己保存的那份 */
  owner?: { account_id?: string; scope?: string; personal?: boolean };
  /** 已保存的店铺（可以多个）：换店点一下就切，不用重新贴 token */
  shops?: TemuShop[];
  /** 当前启用的店铺 id */
  active_shop_id?: string;
};

/** 类目是否需要尺码表（服装/鞋类需要；宠物用品等返回 false）。 */
export type TemuSizeChartInfo = {
  cat_id: number;
  /**
   * 这个类目**要不要**尺码表（由 `sizecharts.meta.get` 判定）。
   * 注意和 has_size_chart 的区别：required=true + has_size_chart=false 是「必须有但店里还没建」。
   */
  required?: boolean;
  has_size_chart: boolean;
  count: number;
  /**
   * 店里已有的尺码表（可以直接填进 sizeTemplateIds 的那种）。
   * 后端收敛成 `{id, name}`：网关字段名没确认，认不出 id 的行会被丢掉，所以
   * `count` 可能大于 `tables.length`。
   */
  tables?: Array<{ id: number; name: string }>;
  raw?: unknown;
  /** 该尺码组的测量项（肩宽/胸围全围/裙长…） */
  elements?: Array<{ id: number; name: string; necessary: boolean }>;
  /** 其中必填的测量项名称 */
  required_elements?: string[];
  /** 可选尺码名（S/M/L/one-size…），建表或选码时要参考 */
  sizes?: string[];
  allow_range?: boolean;
  /** 建表接口（sizecharts.create）参数结构还没打通，固定 false */
  can_create?: boolean;
  error?: string;
};

/** 一个已保存的店铺。token 只回掩码，原文不出后端。 */
export type TemuShop = {
  id: string;
  mall_name: string;
  masked_token: string;
  added_at: string;
  last_used_at: string;
  active: boolean;
};

export type TemuTokenInfo = {
  mall_name: string;
  expired_at: string;
  remaining_days: number | null;
  scope_count: number;
  scopes: string[];
  expiring_soon: boolean;
};

export type TemuCategory = {
  catId: number;
  catName: string;
  parentCatId: number;
  isLeaf: boolean;
  catLevel: number;
  isHidden?: boolean;
};

export type TemuCategoryPath = {
  cat1DTO?: TemuCategory | null;
  cat2DTO?: TemuCategory | null;
  cat3DTO?: TemuCategory | null;
  cat4DTO?: TemuCategory | null;
  cat5DTO?: TemuCategory | null;
  cat6DTO?: TemuCategory | null;
  cat7DTO?: TemuCategory | null;
  cat8DTO?: TemuCategory | null;
  cat9DTO?: TemuCategory | null;
  cat10DTO?: TemuCategory | null;
};

export type TemuPropertyValue = {
  vid: number;
  value: string;
  lang2Value?: string | null;
  /** 销售属性值对应的规格 id（多颜色/尺码必须用它，自己 createSpec 会被判「不合法的规格属性」） */
  specId?: number;
};

export type TemuProperty = {
  pid: number;
  /**
   * Temu 下发的原始 pid。
   *
   * 只在「同一个 pid 挂了多条属性」时出现（实测服装类目：面料纹理1/2 都是 pid 2054，
   * 面料克重1/2、里料克重都是 pid 2052）。这种条目的 `pid` 会被
   * `normalizeDuplicatePids()` 换成唯一的 refPid，原始 pid 存在这里，
   * 提交 payload 时用它还原 —— 网关只认原始 pid。
   */
  realPid?: number;
  name?: string;
  propertyName?: string;
  required: boolean;
  values: TemuPropertyValue[];
  refPid?: number;
  templatePid?: number;
  valueUnit?: string[];
  propertyValueType?: number;
  chooseMaxNum?: number;
  numberInputTitle?: string | null;
  /** true = 销售属性（颜色/尺码/型号…），它的 values 就是合法规格值 */
  isSale?: boolean;
  /** 销售属性对应的父规格 id */
  parentSpecId?: number;
  /** Temu 标记的「主销售属性」（决定 SKC） */
  mainSale?: boolean;
  /** 父属性的 templatePid：本属性是「联动子属性」时才有 */
  parentTemplatePid?: number | null;
  /** 联动规则：父属性取到 parentVidList 里任一值才显示本属性，且只允许 vidList 里的值 */
  templatePropertyValueParentList?: Array<{
    parentVidList: number[];
    vidList: number[];
  }> | null;
  /**
   * 显示条件（第二套联动规则，和上面那条不是一回事）：
   * `parentRefPid` 指的是**父属性的 refPid**，父属性取到 parentVids 里任一值才显示本属性。
   *
   * 实测 2026-11-03：桌布类目「厚度（mm）」只带这条 —— 材料选了 2512 才该出现。
   * 以前完全不认，界面照显示、payload 照发，Temu 就回「货品类目属性更新」。
   */
  showCondition?: Array<{
    parentRefPid: number;
    parentVids: number[];
  }> | null;
};

export type TemuAttributes = {
  properties: TemuProperty[];
  templateId?: number;
  inputMaxSpecNum?: number;
  chooseAllQualifySpec?: boolean;
};

export type TemuParentSpec = {
  parentSpecId: number;
  parentSpecName: string;
};

export type TemuFreightTemplate = {
  freightTemplateId: string;
  templateName: string;
};

export type TemuWarehouse = {
  warehouseId: string;
  warehouseName: string;
  warehouseDisable?: boolean;
  /**
   * 仓库类型。实测（2026-10-26 真调）：type=3 的仓（如「煊烊美东」）发部分商品会被拒
   * 「该商品不支持绑定发货仓，请替换为【三方仓/自营仓/家庭仓】」，type=1/0 可用。
   */
  managementType?: string;
};

export type TemuWarehouseGroup = {
  siteId?: number;
  validWarehouseList: TemuWarehouse[];
};

export type TemuSpecCreated = {
  specId?: number;
  specName?: string;
};

export type TemuSkuSpec = {
  parent_spec_id: number;
  parent_spec_name: string;
  spec_id: number;
  spec_name: string;
};

export type TemuSkuDraft = {
  ext_code: string;
  currency_type: string;
  site_id: number;
  supplier_price: number;
  suggested_price?: number | null;
  stock: number;
  warehouse_id: string;
  weight_mg: number;
  length_mm: number;
  width_mm: number;
  height_mm: number;
  thumb_url?: string | null;
  /** 多件装数量（4 Pack / 12Pcs）：>1 才发 productSkuMultiPackReq */
  pieces_per_pack?: number;
  /** 敏感货（带电/液体等）→ productSkuSensitiveAttrReq.isSensitive */
  is_sensitive?: boolean;
  specs: TemuSkuSpec[];
};

export type TemuSkcDraft = {
  ext_code: string;
  preview_image_urls: string[];
  color_image_url?: string | null;
  main_specs: TemuSkuSpec[];
  skus: TemuSkuDraft[];
};

export type TemuGoodsDraft = {
  /** 来源草稿条目 id：发布成功后后端据此把它从草稿库移除 */
  draft_id?: string;
  category_ids: number[];
  /** 类目路径名（和 category_ids 对应），只用于发布历史展示 */
  category_names?: string[];
  title: string;
  /** 英文标题（选填）；填了会覆盖自动生成的 en 标题 */
  title_en?: string;
  /** 站外产品链接，提交为 outerGoodsUrl */
  outer_goods_url?: string;
  carousel_image_urls: string[];
  outer_package_image_urls?: string[];
  /** 素材图；留空由后端取轮播第一张 */
  material_image_url?: string;
  detail_image_urls?: string[];
  /** 详情图（带真实尺寸）。Temu 按 width/height 校验实际图片，填错报 2000306 */
  detail_images?: Array<{ url: string; width: number; height: number }>;
  freight_template_id: string;
  shipment_limit_seconds?: number;
  properties: Array<{
    pid: number;
    vid: number;
    prop_name?: string;
    prop_value?: string;
    template_pid?: number;
    ref_pid?: number;
    /** 数值型属性的数值与单位（如 30 / cm），Temu 要求分开给 */
    number_input_value?: string;
    value_unit?: string;
  }>;
  skcs: TemuSkcDraft[];
  size_template_ids?: number[];
  show_size_template_ids?: number[];
  /** 定制/POD 商品 → 顶层 personalizationSwitch */
  personalization?: boolean;
  site_ids?: number[];
  goods_label_name?: string;
  validate_catalog?: boolean;
};

export type TemuPublishResult = {
  submitted: boolean;
  /** true = 提交时网络中断，靠「商品列表反查」确认已在店铺里创建（不是网关直接返回的成功） */
  confirmed_by_lookup?: boolean;
  /** 反查命中的商品信息（product_id / product_name / ext_code …） */
  matched_goods?: Record<string, unknown>;
  api_type: string;
  /** 网关返回的原始结果（含 Temu 侧商品标识，字段随网关版本可能不同） */
  result: Record<string, unknown>;
  summary: TemuBuildPayloadResult["summary"];
  warnings: string[];
  request_payload: Record<string, unknown>;
};

export type TemuBuildPayloadResult = {
  payload: Record<string, unknown>;
  warnings: string[];
  summary: {
    category_ids: number[];
    leaf_cat_id: number;
    skc_count: number;
    sku_count: number;
    site_ids: number[];
    warehouse_ids: string[];
    price_min: number;
    price_max: number;
  };
};

/** 草稿里的一条 SKU（AI 处理采集到的原始变体记录，一个颜色一条）。 */
export type TemuDraftVariant = {
  /** 上游 SKU id（能当货号用） */
  sku?: string;
  /** 第一规格值（颜色 / 型号 / 规格型号…） */
  color?: string;
  /** 第二规格值（尺码 / 规格…），没有第二规格时为空 */
  size?: string;
  /** 申报价（元，已按导出规则按成本算过）；null = 这条没有成本，用主价 */
  price?: number | null;
  /** 库存（上游 quantity）；null = 没给 */
  stock?: number | null;
  /** 这个变体自己的主图 */
  image?: string;
};

export type TemuDraftEntry = {
  source: "ai" | "pod";
  id: string;
  title: string;
  subtitle: string;
  status: string;
  images: string[];
  carousel_images?: string[];
  detail_images?: string[];
  main_image: string;
  updated_at: string;
  task_id?: number;
  draft_id?: number | null;
  batch_id?: string;
  style_index?: number;
  /** POD：同一款的变体数量（卡片已按款合并） */
  variant_count?: number;
  sku: string;
  description?: string;
  source_url?: string;
  /** 草稿自带的类目（AI 采集的类目路径 / POD 的 category_name），可能为空 */
  category_name?: string;
  spec_name_1?: string;
  spec_value_1?: string;
  spec_name_2?: string;
  spec_value_2?: string;
  /** 第一/第二规格的全部取值（去重）—— 发布页据此把变种一次铺满 */
  spec_values_1?: string[];
  spec_values_2?: string[];
  /** 每个 SKU 一行：颜色/尺码/申报价(元)/库存/自己的主图 */
  variants?: TemuDraftVariant[];
  /** 申报价（单位：元）。没有显式填过时按导出规则测算，见 declared_price_estimated */
  declared_price: number | string | null;
  /** true = 该价格是按成本算出来的（成本 × 4，下限 ¥150），不是人工填的 */
  declared_price_estimated?: boolean;
  suggested_price: number | string | null;
  stock: number | string | null;
  weight_g: number | string | null;
  length_cm: number | string | null;
  width_cm: number | string | null;
  height_cm: number | string | null;
};

export type TemuDraftLibrary = {
  entries: TemuDraftEntry[];
  warnings: string[];
  /** 全量计数（不随筛选变化）：筛选按钮上的数字要稳定 */
  counts: { ai: number; pod: number; total: number };
  /** 过滤后的总条数（= 总页数 × 每页条数的来源） */
  total: number;
  page: number;
  page_size: number;
  pages: number;
  /**
   * 还有没取出来的商品：某个处理批次里的商品数超过「每批一页（20）」时为 true。
   * 为 true 时界面显示「加载更多」，点一次把 item_offset 推进 20 再取一遍。
   */
  has_more?: boolean;
  /** 当前每个批次已经取到第几个（0 起）。界面用它显示「已加载到第 N 个 / 批」。 */
  item_offset?: number;
  source: "all" | "ai" | "pod";
  query: string;
};

export const TEMU_PUBLISH_PREFILL_KEY = "temu_publish_prefill_v1";

/**
 * 发布历史「去修这条」→ 草稿库自动打开指定草稿。
 * 历史条目记了来源草稿 id（ai:任务:项 / pod:批次:款式），草稿库条目 id 同源，
 * 切过去找到那条直接走「编辑」链路（整表预填进发布页），不用用户再翻。
 */
export const TEMU_LIBRARY_FOCUS_KEY = "temu_library_focus_draft_v1";

/**
 * 草稿库「用于发布」的事件通道。
 * 为什么需要两条通道：openModule 会**复用已存在的页签**，发布页已打开时不会再挂载，
 * 只读 sessionStorage 的挂载逻辑就不会执行（点了没反应）。所以挂载走 storage，
 * 已挂载走事件。
 */
export const TEMU_PUBLISH_PREFILL_EVENT = "mainpg:temu-publish-prefill";

/**
 * 草稿库 → 发布页的预填数据。
 * 字段口径对齐ERP导入模板（dxm_import_task_*.xlsx / pod_dxm_*.xlsx）：
 * 变种属性、申报价、尺寸重量、轮播图/素材图这些列在导出里是什么，这里就是什么。
 */
export type TemuPublishPrefill = {
  title: string;
  images: string[];
  detailImages?: string[];
  sku?: string;
  weightKg?: string;
  lengthCm?: string;
  widthCm?: string;
  heightCm?: string;
  sourceLabel?: string;
  /** 来源类型：ai=AI 处理，pod=POD 定制。两者的预填完整度不同，界面要分开提示。 */
  sourceKind?: "ai" | "pod";
  /** 申报价（单位：元，如 "12.99"；提交时由发布页换算成接口要的「分」） */
  supplierPrice?: string;
  /**
   * 变种级 SKU 覆盖（变种 key「颜色|尺码」→ 改过的字段）。
   *
   * 可按变种设：申报价 / 库存 / 重量 / 长宽高 / 货号 ——
   * 衣服鞋包按尺码、箱包按款式，长宽高重量本来就不同，不能整条一个数。
   * 只存改过的字段；没设的回落到整条值（supplierPrice / stock / weightKg…）。
   * 取值一律走 data/skuPricing.ts 的 resolveSkuField，别在别处再写一遍。
   */
  skuFields?: Record<string, Record<string, string>>;
  /** 建议售价（单位：元） */
  suggestedPrice?: string;
  stock?: string;
  /** POD 草稿在模板里是占位值（申报价/重量/尺寸），需要人核对后覆盖 */
  needsReview?: string[];
  /** ERP模板的变种属性：名称仅作参考展示，值用于填规格 */
  specName1?: string;
  specValue1?: string;
  specName2?: string;
  specValue2?: string;
  /** 全部规格值：发布页直接铺成「颜色列表 / 尺码列表」 */
  specValues1?: string[];
  specValues2?: string[];
  /** 每个 SKU 一行的数据：颜色、尺码、价格、库存、主图 */
  variants?: TemuDraftVariant[];
  description?: string;
  sourceUrl?: string;
  /** 来源草稿 id：发布页选完类目回写给它，草稿库下次直接显示类目路径 */
  draftId?: string;
  /** 这条草稿上次选过的 Temu 类目路径，带入时直接选中，不用重选 */
  categoryPath?: TemuCategory[];
  /**
   * 入草稿库时按类目估好的产品属性（pid → vid/text）。
   *
   * 是**估**出来的（没有依据时取第一个可用选项），不是人填的：
   * 发布页会把它们铺上，同时提示「产品属性（自动估）」要核对。
   */
  attrPicks?: Record<number, { vid: number; text: string }>;
  /**
   * attrPicks 属于哪个叶子类目。类目对不上就不能铺 —— 不同类目的属性 pid
   * 完全不同，硬铺等于给 Temu 塞一堆不存在的属性。
   */
  attrPicksCatId?: number;
  /**
   * attrPicks 里哪些 pid 是「猜的」（没有任何依据、取的是第一个可用选项）。
   * 发布页会在这些属性旁边标出来，让人先核对这几项。
   */
  attrGuessedPids?: number[];
};

export type TemuImageUploadResult = {
  imageUrl?: string;
  url?: string;
  image_url?: string;
  [key: string]: unknown;
};

/** 草稿库「编辑」可改的字段（对齐ERP模板列），只在本机生效。 */
export type TemuDraftEdit = {
  title?: string;
  sku?: string;
  declared_price?: string;
  suggested_price?: string;
  stock?: string;
  length_cm?: string;
  width_cm?: string;
  height_cm?: string;
  weight_g?: string;
};

/** 店铺里的商品（bg.glo.goods.list.get 的列表项，压成前端要的样子）。 */
export type TemuGoodsItem = {
  product_id: string;
  title: string;
  skc_ext_code: string;
  image: string;
  created_at?: number;
  site_status?: number;
  category?: string;
  sku_codes?: string[];
  /** 每个 SKU 的当前库存（列表接口的 virtualStock） */
  skus?: Array<{ sku_id: string; ext_code: string; stock: number; specs?: string }>;
  /** 全部 SKU 库存之和 */
  stock_total?: number;
  /** 库存为 0 的 SKU 个数 */
  zero_stock_sku_count?: number;
};

/** 待补库存清单（后端扫店铺商品得出，只读）。 */
export type TemuLowStockResult = {
  scanned: number;
  total: number;
  items: TemuGoodsItem[];
  zero_sku_count: number;
  only_zero: boolean;
  /** false = 改库存接口未开通，只能导出清单去后台改 */
  can_update_stock: boolean;
};



/** 一条发布记录（成功或失败）。后端 temu_publish_submissions 的一行。 */
export type TemuPublishHistoryItem = {
  id: number;
  title: string;
  sku_count: number;
  /** success / failed */
  status: string;
  goods_id: string;
  error: string;
  /** Temu 返回的错误码（6000002 等）。老记录没有，前端会从 error 文本里再试一次 */
  error_code?: string;
  /** 来源草稿条目 id（ai:任务:项 / pod:批次:款式），手填的为空 */
  draft_id: string;
  leaf_cat_id: number;
  /** 类目路径名，例如「美妆个护 > 化妆包 > 手拿包」。老记录为空，退回显示 cat id */
  cat_name?: string;
  /** 价格区间（人民币「分」）。0 表示没记 */
  price_min?: number;
  price_max?: number;
  /** 发布时启用店铺的名字 */
  shop_name?: string;
  image: string;
  created_at: string;
  /** 秒级时间戳，用来算「x 分钟前」和按天分组 */
  created_ts?: number;
};

export type TemuPublishHistory = {
  items: TemuPublishHistoryItem[];
  total: number;
  counts: { success: number; failed: number };
  page: number;
  page_size: number;
};

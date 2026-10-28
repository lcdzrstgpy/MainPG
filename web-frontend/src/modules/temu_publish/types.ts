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
  category_ids: number[];
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
  /** 申报价（单位：元）。没有显式填过时按导出规则推算，见 declared_price_estimated */
  declared_price: number | string | null;
  /** true = 该价格是推算出来的（成本 × 4，下限 ¥150），不是人工填的 */
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
  counts: { ai: number; pod: number; total: number };
};

export const TEMU_PUBLISH_PREFILL_KEY = "temu_publish_prefill_v1";

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
  /** 申报价（店铺币种，单位：分） */
  supplierPrice?: string;
  /** 建议售价（单位：分） */
  suggestedPrice?: string;
  stock?: string;
  /** POD 草稿在模板里是占位值（申报价/重量/尺寸），需要人核对后覆盖 */
  needsReview?: string[];
  /** ERP模板的变种属性：名称仅作参考展示，值用于填规格 */
  specName1?: string;
  specValue1?: string;
  specName2?: string;
  specValue2?: string;
  description?: string;
  sourceUrl?: string;
  /** 来源草稿 id：发布页选完类目回写给它，草稿库下次直接显示类目路径 */
  draftId?: string;
  /** 这条草稿上次选过的 Temu 类目路径，带入时直接选中，不用重选 */
  categoryPath?: TemuCategory[];
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

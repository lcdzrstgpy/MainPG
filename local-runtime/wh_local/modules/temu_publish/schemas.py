"""Temu 发布配套接口的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TemuSettingsUpdate(BaseModel):
    """服务商调用凭证与店铺 token；空字符串表示“不修改”。"""

    yl_client: str | None = None
    yl_token: str | None = None
    access_token: str | None = None
    clear_yl_client: bool = False
    clear_yl_token: bool = False
    clear_access_token: bool = False


class TemuShopCreate(BaseModel):
    """新增一家店铺：只填 access_token，店铺名由网关返回。"""

    access_token: str = Field(min_length=8, max_length=4096)
    mall_name: str = Field(default="", max_length=120)


class CategoryMatchRequest(BaseModel):
    search_text: str = Field(min_length=1, max_length=200)
    site_id: int = Field(default=100, ge=1)


class SpecCreateRequest(BaseModel):
    parent_spec_id: int = Field(ge=1)
    spec_name: str = Field(min_length=1, max_length=120)


class ImageUploadRequest(BaseModel):
    """商品图片上传；image 支持完整 Data URL 或纯 base64。"""

    image: str = Field(min_length=16)
    image_biz_type: int = Field(default=0, ge=0, le=1)
    size_mode: int = Field(default=0, ge=0, le=2)
    cate_id: int | None = Field(default=None, ge=1)


class ImageUploadUrlRequest(BaseModel):
    """服务端代下载公网图片后上传（用于 COS 等外链，规避浏览器跨域）。"""

    url: str = Field(min_length=8, max_length=2000)
    image_biz_type: int = Field(default=0, ge=0, le=1)
    size_mode: int = Field(default=0, ge=0, le=2)
    cate_id: int | None = Field(default=None, ge=1)


class GoodsPropertyInput(BaseModel):
    """类目属性（对应 productPropertyReqs 的一项）。"""

    pid: int = Field(ge=1)
    vid: int = Field(default=0, ge=0)
    template_pid: int | None = Field(default=None, ge=1)
    ref_pid: int | None = Field(default=None, ge=1)
    prop_name: str = ""
    prop_value: str = ""
    value_unit: str = ""
    number_input_value: str = ""
    value_group_id: int = 0
    value_group_name: str = ""


class SkuSpecInput(BaseModel):
    """SKU/SKC 规格（颜色、尺码等）。"""

    parent_spec_id: int = Field(ge=1)
    parent_spec_name: str = Field(min_length=1)
    spec_id: int = Field(ge=1)
    spec_name: str = Field(min_length=1)


class SkuInput(BaseModel):
    """单个 SKU；价格均为最小货币单位（如 USD 12.99 → 1299）。"""

    ext_code: str = Field(min_length=1, max_length=120)
    # 半托管店铺的供货价用人民币结算：实测传 USD 会被拒「请选择正确的币种：CNY」。
    currency_type: str = "CNY"
    site_id: int = Field(default=100, ge=1)
    supplier_price: int = Field(ge=1)
    suggested_price: int | None = Field(default=None, ge=1)
    stock: int = Field(default=0, ge=0)
    warehouse_id: str = Field(min_length=1)
    weight_mg: int = Field(ge=1, description="重量，毫克")
    length_mm: int = Field(ge=1, description="长度，毫米")
    width_mm: int = Field(ge=1, description="宽度，毫米")
    height_mm: int = Field(ge=1, description="高度，毫米")
    thumb_url: str | None = None
    # 多件装：一个 SKU 里装几件（标题里的 "4 Pack" / "12Pcs"）。>1 才会发
    # productSkuMultiPackReq，Temu 前台会显示「N 件装」。
    pieces_per_pack: int = Field(default=1, ge=1, le=1000)
    # 敏感货（带电/液体等）：落到 productSkuSensitiveAttrReq.isSensitive
    is_sensitive: bool = False
    specs: list[SkuSpecInput] = Field(min_length=1)

    @field_validator("warehouse_id", "ext_code")
    @classmethod
    def strip_required(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("不能为空")
        return text


class SkcInput(BaseModel):
    """一个 SKC（颜色/款式），下挂多个 SKU。"""

    ext_code: str = Field(min_length=1, max_length=120)
    preview_image_urls: list[str] = Field(min_length=1)
    color_image_url: str | None = None
    main_specs: list[SkuSpecInput] = Field(min_length=1)
    skus: list[SkuInput] = Field(min_length=1)

    @field_validator("ext_code")
    @classmethod
    def strip_ext_code(cls, value: str) -> str:
        text = (value or "").strip()
        if not text:
            raise ValueError("不能为空")
        return text


class DetailImageUploadRequest(BaseModel):
    """详情图上传：服务端统一缩放到 target_width 再传给 Temu。

    Temu 商详要求所有楼层图片等宽，1688 抓来的图宽度经常不一致
    （如 1392 和 800 混着），会报「Detail graphic article N floor image width is incorrect」。
    """

    # base64 会膨胀 4/3，28M 字符 ≈ 20MB 原图：超了在入参校验就挡掉
    image: str = Field(min_length=16, max_length=28_000_000)
    target_width: int = Field(default=800, ge=0, le=5000)
    cate_id: int | None = Field(default=None, ge=1)


class DetailImageInput(BaseModel):
    """详情图（商详楼层图）：必须带真实宽高。

    Temu 会按 width/height 校验实际图片尺寸，填错就报
    「Detail graphic article N floor image width is incorrect」。
    """

    url: str = Field(min_length=1)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)


class GoodsDraftInput(BaseModel):
    """发布商品草稿（只用于组装 goods.add 请求体，不提交）。"""

    category_ids: list[int] = Field(min_length=1, max_length=10)
    title: str = Field(min_length=1, max_length=255)
    # 英文标题（选填）：填了就作为 productI18nReqs 的 en 标题，不填则按标题内容自动判断。
    title_en: str = Field(default="", max_length=255)
    carousel_image_urls: list[str] = Field(min_length=1)
    outer_package_image_urls: list[str] = Field(default_factory=list)
    detail_image_urls: list[str] = Field(default_factory=list)
    # 带真实尺寸的详情图（优先于 detail_image_urls）；尺寸必须与图片实际一致
    detail_images: list[DetailImageInput] = Field(default_factory=list)
    # 「素材图」（Temu 报 Material Image cannot be empty 时缺的就是这个）。
    # 实测字段名是顶层 materialImgUrl（2026-10-26 真调确认），文档里没写。
    material_image_url: str = ""
    freight_template_id: str = Field(min_length=1)
    # 默认 7 天：1 天等于承诺 24 小时发货，POD/定制做不到（会被判延迟发货）
    shipment_limit_seconds: int = Field(default=7 * 86400, ge=1)
    properties: list[GoodsPropertyInput] = Field(default_factory=list)
    skcs: list[SkcInput] = Field(min_length=1)
    size_template_ids: list[int] = Field(default_factory=list)
    show_size_template_ids: list[int] = Field(default_factory=list)
    site_ids: list[int] = Field(default_factory=list)
    goods_label_name: str = ""
    # 站外产品链接，落到 productWhExtAttrReq.outerGoodsUrl
    outer_goods_url: str = ""
    origin_region_short_name: str = "CN"
    # 二级产地 id：实测传 0 会被拒「不合法的二级区域id」。默认值取自官方成功样例。
    origin_region2_id: int = 43000000000006
    # 定制/POD 商品：落到顶层 personalizationSwitch（开启前需在卖家中心配好定制模板）
    personalization: bool = False
    semi_managed_site_mode: int = 1
    semi_language_strategy: int = 1
    validate_catalog: bool = True

class DimensionCheckRequest(BaseModel):
    """尺寸图检测（bg.algo.dimension.image.check，CN 网关，异步）。

    mallId 是 Temu 店铺 id（卖家后台可见），接口必填，这里不做默认值猜测。
    """

    image_url: str = Field(min_length=8)
    mall_id: str = Field(default="", description="Temu 店铺 id；留空会报错")
    cat_id: int | None = Field(default=None, ge=1)
    class_id: int | None = Field(default=None, ge=1)


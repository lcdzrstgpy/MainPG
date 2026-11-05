"""Temu 商品 OpenAPI 网关客户端。

设计要点：
- 只保存调用方凭证（x-yl-client / x-yl-token）与店铺 access_token，不处理 Temu 签名。
- 业务 4xx/错误码不重试；网络异常、429、5xx 才退避重试（均为幂等查询）。
- 查询类结果带短 TTL 内存缓存，降低类目树递归与发布页反复取数的调用量。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

import requests

PRODUCT_ROUTER = "https://yingluan-api.qibang-global.vip/api/temu-product/openapi/router"

# 发布页常用的缓存时长（秒）。
# 类目树 6 小时太长：Temu 调整/下架类目后，最长要 6 小时才能看到，
# 期间用户可能把商品发到已废弃的类目上。1 小时是「基本不浪费请求」和
# 「改动当天能看到」的折中。
CACHE_TTL_CATEGORIES = 60 * 60
CACHE_TTL_ATTRIBUTES = 60 * 60

# 类目树 / 类目匹配 / 属性表是 **Temu 全网统一**的，跟哪家店、哪个账号无关。
# 这三类缓存放在模块级而不是实例级，原因很实在：清缓存的地方（router._handle）
# 拿不到「当前店铺的 client 实例」，只能拿到一个按默认凭证构造的实例 ——
# 实例级缓存下它清的是自己那份空缓存，真正在用的那份纹丝不动，
# 于是出现「点了刷新、错误照旧」的死循环（2026-11-03 实测踩到）。
# 注意：parent-specs / freight / warehouse 是**店铺级**的，绝不能共享（会串店）。
_SHARED_CATALOG_CACHE: dict[str, tuple[float, Any]] = {}
_SHARED_CATALOG_LOCK = Lock()
_SHARED_CATALOG_PREFIXES = ("cats:", "cat-match:", "attrs:")


def _is_shared_catalog_key(key: str) -> bool:
    return key.startswith(_SHARED_CATALOG_PREFIXES)


def clear_shared_catalog_cache() -> int:
    """清掉全网统一的类目/属性缓存，返回清掉的条数。任何时刻、任何实例调都生效。"""
    with _SHARED_CATALOG_LOCK:
        keys = [key for key in _SHARED_CATALOG_CACHE if _is_shared_catalog_key(key)]
        for key in keys:
            _SHARED_CATALOG_CACHE.pop(key, None)
    return len(keys)
CACHE_TTL_LOGISTICS = 30 * 60


class TemuGatewayError(RuntimeError):
    """上游网关 / Temu 返回的业务错误（已归一化）。"""

    def __init__(
        self,
        api_type: str,
        message: str,
        *,
        error_code: str = "",
        http_status: int | None = None,
    ) -> None:
        self.api_type = api_type
        self.error_code = str(error_code or "")
        self.http_status = http_status
        self.message = str(message or "")
        super().__init__(f"{api_type}: {self.message}")


class TemuCredentialMissing(RuntimeError):
    """缺少调用凭证或店铺 access_token。"""


@dataclass(frozen=True)
class TemuCredentials:
    client: str
    token: str
    access_token: str

    def complete(self) -> bool:
        return bool(self.client and self.token and self.access_token)


# 服务商网关是境内直连的域名。系统代理（Clash/V2Ray 之类）经常把它也劫持走，
# 结果是 TLS 握手被掐断：SSLEOFError / EOF occurred in violation of protocol。
# 所以默认**不读系统代理**；确实需要代理的环境用 WH_TEMU_PROXY 显式指定。
PROXY_ENV = "WH_TEMU_PROXY"


def _build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    proxy = os.environ.get(PROXY_ENV, "").strip()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


class TemuProductClient:
    """Temu 商品 OpenAPI 客户端（线程安全、带缓存）。"""

    def __init__(
        self,
        credentials: TemuCredentials,
        *,
        timeout: float = 40.0,
        max_attempts: int = 3,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self._timeout = timeout
        self._max_attempts = max(1, int(max_attempts))
        self._session = session or _build_session()
        self._sleep = sleep
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = Lock()

    # ---------- 基础调用 ----------

    def call(
        self,
        api_type: str,
        request: dict[str, Any] | None = None,
        *,
        gateway: str = "PARTNER",
        access_token: str | None = None,
        cache_key: str | None = None,
        cache_ttl: int = 0,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        """调用网关。

        max_attempts 默认沿用客户端配置；**建品这类非幂等写接口必须传 1**，
        否则网络抖动重试会在店铺里重复创建商品。
        """
        attempts = max(1, int(max_attempts if max_attempts is not None else self._max_attempts))
        if cache_key and cache_ttl > 0:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        token = self.credentials.access_token if access_token is None else access_token
        if not token:
            raise TemuCredentialMissing("Temu 店铺 access_token 未配置")

        body: dict[str, Any] = {
            "type": api_type,
            "gateway": gateway,
            "request": request or {},
            "access_token": token,
        }
        headers = {
            "Content-Type": "application/json",
            "x-yl-client": self.credentials.client,
            "x-yl-token": self.credentials.token,
        }

        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._session.post(
                    PRODUCT_ROUTER, json=body, headers=headers, timeout=self._timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    raise TemuGatewayError(
                        api_type, f"网关网络请求失败：{exc}", error_code="NETWORK_ERROR"
                    ) from exc
                self._sleep(min(2.0, 0.5 * (2**attempt)))
                continue

            status = int(response.status_code)
            # 限流：优先遵循 Retry-After，最多等 5 秒，避免长时间阻塞发布页。
            if status == 429:
                last_error = TemuGatewayError(
                    api_type, "网关调用频率超限（429）", error_code="RATE_LIMIT_EXCEEDED", http_status=429
                )
                if attempt + 1 >= attempts:
                    raise last_error
                retry_after = _retry_after_seconds(response)
                self._sleep(min(5.0, retry_after if retry_after is not None else 1.0 * (attempt + 1)))
                continue

            if status >= 500:
                last_error = TemuGatewayError(
                    api_type, f"上游服务异常（HTTP {status}）", error_code="UPSTREAM_ERROR", http_status=status
                )
                if attempt + 1 >= attempts:
                    raise last_error
                self._sleep(min(2.0, 0.5 * (2**attempt)))
                continue

            data = _response_json(response, api_type)
            if data.get("success") is True:
                result = data.get("result")
                value = result if isinstance(result, dict) else {"result": result}
                if cache_key and cache_ttl > 0:
                    self._cache_put(cache_key, value, cache_ttl)
                return value

            error_code = str(data.get("errorCode") or data.get("error_code") or "")
            message = str(
                data.get("errorMsg")
                or data.get("message")
                or data.get("detail")
                or f"网关接口调用失败（HTTP {status}）"
            )
            raise TemuGatewayError(api_type, message, error_code=error_code, http_status=status)

        raise TemuGatewayError(api_type, str(last_error or "网关接口调用失败"))

    # ---------- 商品发布配套查询 ----------

    def token_info(self) -> dict[str, Any]:
        return self.call("bg.open.accesstoken.info.get.global")

    def categories(self, parent_cat_id: int = 0) -> dict[str, Any]:
        return self.call(
            "bg.glo.goods.cats.get",
            {"parentCatId": int(parent_cat_id)},
            cache_key=f"cats:{int(parent_cat_id)}",
            cache_ttl=CACHE_TTL_CATEGORIES,
        )

    def match_category(self, search_text: str, site_id: int = 100) -> dict[str, Any]:
        return self.call(
            "bg.glo.goods.category.match",
            {"searchText": str(search_text), "siteId": int(site_id)},
            cache_key=f"cat-match:{site_id}:{search_text}",
            cache_ttl=CACHE_TTL_CATEGORIES,
        )

    def attributes(self, cat_id: int, supported_type: int = 1) -> dict[str, Any]:
        return self.call(
            "bg.glo.goods.attrs.get",
            {"catId": int(cat_id), "supportedType": int(supported_type)},
            cache_key=f"attrs:{int(cat_id)}:{int(supported_type)}",
            cache_ttl=CACHE_TTL_ATTRIBUTES,
        )

    def mandatory_config(
        self,
        leaf_cat_id: int,
        bind_site_ids: list[int],
        product_property_reqs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """类目必填配置校验（bg.glo.goods.catsmandatory.get）。

        发布前用它确认这个叶子类目还缺哪些必填属性 —— 文档 §7.4 的取数顺序里明确要求。
        传当前已组装的 productPropertyReqs，网关会告诉你缺什么。
        """
        body: dict[str, Any] = {
            "leafCatId": int(leaf_cat_id),
            "bindSiteIds": sorted({int(value) for value in bind_site_ids if int(value) > 0}),
            "configItems": [1, 2, 3],
        }
        # 同一个签名 bug：空数组会让网关算出的 sign 对不上，
        # 所以没有属性时整个字段不发（这个接口长期 `sign is invalid` 就是这个原因）。
        # 实测（2026-11-03）：空数组不发之后签名错误消失，但这类目会回
        # 「Does not currently support querying other category configurations」——
        # 接口只支持部分类目，调用方必须保留本地 attrs 兜底。
        if product_property_reqs:
            body["productPropertyReqs"] = list(product_property_reqs)
        return self.call("bg.glo.goods.catsmandatory.get", body)

    def parent_specs(self) -> dict[str, Any]:
        return self.call(
            "bg.glo.goods.parentspec.get", {}, cache_key="parent-specs", cache_ttl=CACHE_TTL_CATEGORIES
        )

    def create_spec(self, parent_spec_id: int, spec_name: str) -> dict[str, Any]:
        # 写接口：网络抖动重试会在类目里重复建同一条规格，所以只试一次
        result = self.call(
            "bg.glo.goods.spec.create",
            {"parentSpecId": int(parent_spec_id), "specName": str(spec_name).strip()},
            max_attempts=1,
        )
        # 新建规格后父规格列表就变了：立刻清掉读缓存，否则下拉里最长一个 TTL 看不到
        self._cache_drop("parent-specs")
        return result

    def invalidate_parent_specs(self) -> None:
        """新建规格后清掉父规格缓存，否则最长要等一个 TTL 才看得到新规格。"""
        self._cache_drop("parent-specs")

    def ensure_default_spec(
        self,
        spec_name: str = "",
        catalog: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """拿一个**真实存在**的规格值兜底（父规格「颜色」下的一条真实值），带缓存。

        为什么需要它（2026-11-03 实测）：

        桌布 / 桌旗 / 化妆包这一整片类目 `attrs.get` 返回的属性里 ``isSale`` 全是 false，
        而 Temu 又要求「规格列表不能为空」。以前发的是 id 全 0 的假规格 ``款式 / 默认``
        —— 规格校验能过，但 ``goods.add`` 会一直回

            6000002 货品类目属性更新，请刷新页面后重新创建货品

        而且**跟发了哪个属性无关**（12 个必填逐个单发，全是 6000002）。

        第二个坑：值不能随便起。类目把「颜色」当**商品属性**时（化妆包 30988 有 116 个
        预设值），规格值必须落在那份列表里，否则回

            属性[颜色]校验错误:关键词属性 默认 不是有效值。

        所以这里优先用 `spec_name`（调用方传商品自己的颜色值），不在合法列表里才退回
        列表第一个；类目没有「颜色」属性时才用「默认」。

        返回 ``{"parentSpecId", "parentSpecName", "specId", "specName"}``。
        """
        # ① 父规格：优先「颜色」
        parent_id, parent_name = 1001, "颜色"
        try:
            specs = self.parent_specs()
            items = specs.get("parentSpecDTOS") or specs.get("parentSpecList") or []
            hit = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and "颜色" in str(item.get("parentSpecName") or "")
                ),
                None,
            )
            if hit:
                parent_id = int(hit.get("parentSpecId") or parent_id)
                parent_name = str(hit.get("parentSpecName") or parent_name)
        except Exception:  # noqa: BLE001 - 拿不到就用默认的 1001/颜色
            pass

        # ② 值：类目把「颜色」当商品属性时，只能从它的预设值里取
        allowed: list[str] = []
        color_prop: dict[str, Any] | None = None
        for item in (catalog or {}).get("properties") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") == "颜色":
                color_prop = item
                allowed = [
                    str(value.get("value") or "")
                    for value in (item.get("values") or [])
                    if value.get("value")
                ]
                break

        name = str(spec_name or "").strip()
        if allowed and name not in allowed:
            name = allowed[0]
        if not name:
            name = allowed[0] if allowed else "默认"
        # 商品属性「颜色」存在时，规格也复用它自己的父规格 id（认不出就用 1001）
        if color_prop and int(color_prop.get("parentSpecId") or 0) > 0:
            parent_id = int(color_prop["parentSpecId"])

        cache_key = f"default-spec:v2:{parent_id}:{name}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict) and cached.get("specId"):
            return dict(cached)

        result = self.create_spec(parent_id, name)
        spec_id = int((result or {}).get("specId") or 0)
        if spec_id <= 0:
            raise RuntimeError("Temu 没有返回新建规格的 specId")
        spec = {
            "parentSpecId": parent_id,
            "parentSpecName": parent_name,
            "specId": spec_id,
            "specName": name,
        }
        # 规则值长期有效，缓存 30 天，避免每次发布都调一次写接口
        self._cache_put(cache_key, spec, 30 * 24 * 3600)
        return dict(spec)

    def invalidate_categories(self) -> int:
        """清掉类目树、类目匹配、类目属性的读缓存，返回清掉的条数。

        用途：Temu 后台调整/下架类目后，缓存最长要 1 小时才过期。给用户一个
        手动刷新入口，不用等 TTL。

        **包含 ``attrs:``**：属性表是按 catId 定义的，Temu 给某个类目加/改必填
        属性时，只清类目树的话用户点完刷新拿到的还是旧属性表（连必填判断一起
        是旧的），得等 1 小时或重启才有变化。类目和它的属性是同一条链路，
        一起失效才符合「我点了刷新」的预期。

        特意不碰 ``freight:`` / ``parent-specs`` / ``warehouse`` 这些：它们跟
        类目改动无关，一起清等于每次刷新都白打几轮网关。
        """
        # 类目/属性走共享缓存：必须清那份，否则「另一个实例清了、这个实例还在用旧表」
        dropped = clear_shared_catalog_cache()
        with self._cache_lock:
            keys = [key for key in self._cache if _is_shared_catalog_key(key)]
            for key in keys:
                self._cache.pop(key, None)
        return dropped + len(keys)

    def goods_list(self, **filters: Any) -> dict[str, Any]:
        """商品列表查询（bg.glo.goods.list.get）。

        用途：发布请求超时/网络中断时反查「到底建没建」，以及按 SKC 货号查重。
        常用过滤：skcExtCode / productName / productSkcIds / bindSiteIds / createdAtStart(秒)。
        """
        payload: dict[str, Any] = {"page": int(filters.pop("page", 1) or 1)}
        payload["pageSize"] = int(filters.pop("pageSize", 20) or 20)
        for key, value in filters.items():
            if value in (None, "", [], {}):
                continue
            payload[key] = value
        return self.call("bg.glo.goods.list.get", payload)

    def size_charts(self, cat_id: int, offset: int = 0, page_size: int = 20) -> dict[str, Any]:
        """类目尺码表查询（bg.glo.goods.sizecharts.get）。

        大多数非服装类目返回 `sizeSpecDataList: null` —— 也就是「这个类目不需要尺码表」。
        """
        return self.call(
            "bg.glo.goods.sizecharts.get",
            {"catId": int(cat_id), "offset": int(offset), "pageSize": int(page_size)},
            max_attempts=1,
        )

    def size_chart_class(self, cat_id: int) -> dict[str, Any]:
        """类目对应的尺码组（bg.glo.goods.sizecharts.class.get）。

        服装/鞋类是「有尺码组」的类目；宠物、家居这类类目调这个会返回空或报错。
        """
        return self.call(
            "bg.glo.goods.sizecharts.class.get", {"catId": int(cat_id)}, max_attempts=1
        )

    def size_chart_meta(self, cat_id: int) -> dict[str, Any]:
        """尺码表的测量项定义（bg.glo.goods.sizecharts.meta.get）。

        返回 `sizeSpecMeta.elementList`（肩宽/胸围全围/裙长…，带 necessary）与 `groupList`。
        类目没有尺码组时网关报「没有找到当前类目对应的尺码组」—— 这正是「不需要尺码表」的信号。
        """
        return self.call(
            "bg.glo.goods.sizecharts.meta.get", {"catId": int(cat_id)}, max_attempts=1
        )

    def size_chart_settings(self, cat_id: int) -> dict[str, Any]:
        """系统默认尺码映射（bg.glo.goods.sizecharts.settings.get）。

        返回 `sizeList`（该尺码组所有可选尺码名）+ `mappingContent`（每个尺码档的完整数值）。
        体积较大（20KB+），调用方只取需要的字段。
        """
        return self.call(
            "bg.glo.goods.sizecharts.settings.get", {"catId": int(cat_id)}, max_attempts=1
        )

    def goods_detail(self, product_id: int) -> dict[str, Any]:
        """商品详情（bg.glo.goods.detail.get）。注意：个别商品会返回「无权访问」。"""
        return self.call("bg.glo.goods.detail.get", {"productId": int(product_id)})

    def dimension_image_check(
        self,
        image_url: str,
        *,
        mall_id: str,
        cat_id: int | None = None,
        class_id: int | None = None,
    ) -> dict[str, Any]:
        """尺寸图检测提交（CN 网关方法，异步，返回 uniqueId 后轮询 result）。"""
        request: dict[str, Any] = {"imageUrl": str(image_url).strip(), "mallId": str(mall_id).strip()}
        if cat_id:
            request["catId"] = int(cat_id)
        if class_id:
            request["classId"] = int(class_id)
        return self.call("bg.algo.dimension.image.check", request, gateway="CN")

    def dimension_image_check_result(self, task_id: str, *, mall_id: str) -> dict[str, Any]:
        return self.call(
            "bg.algo.dimension.image.check.result",
            {"taskId": str(task_id).strip(), "mallId": str(mall_id).strip()},
            gateway="CN",
        )

    def accessories(self, page: int = 1, page_size: int = 20, fuzzy_value: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"page": int(page), "pageSize": int(page_size)}
        if fuzzy_value:
            payload["fuzzyValue"] = str(fuzzy_value)
        return self.call("bg.glo.goods.accessories.get", payload)

    def freight_templates(self, site_ids: list[int]) -> dict[str, Any]:
        normalized = sorted({int(value) for value in site_ids if int(value) > 0})
        return self.call(
            "bg.glo.logistics.template.get",
            {"siteIds": normalized},
            cache_key=f"freight:{','.join(map(str, normalized))}",
            cache_ttl=CACHE_TTL_LOGISTICS,
        )

    def warehouses(self, site_ids: list[int], supplier_id: int | None = None) -> dict[str, Any]:
        normalized = sorted({int(value) for value in site_ids if int(value) > 0})
        payload: dict[str, Any] = {"siteIdList": normalized}
        if supplier_id:
            payload["supplierId"] = int(supplier_id)
            payload["openApiUser"] = {"supplierId": int(supplier_id)}
        key = f"warehouse:{','.join(map(str, normalized))}:{supplier_id or ''}"
        return self.call(
            "bg.btg.goods.stock.warehouse.list.get",
            payload,
            cache_key=key,
            cache_ttl=CACHE_TTL_LOGISTICS,
        )

    def add_goods(self, payload: dict[str, Any]) -> dict[str, Any]:
        """提交商品到 Temu（bg.glo.goods.add）。

        ⚠️ 非幂等：同一个 extCode 重复提交可能产生重复商品或直接报冲突。
        因此这里强制 max_attempts=1 —— 网络异常时不自动重试，把错误抛给调用方，
        由人来确认上一单是否已创建成功。
        """
        return self.call("bg.glo.goods.add", payload, max_attempts=1)

    def upload_image(
        self,
        image: str,
        *,
        image_biz_type: int = 0,
        size_mode: int = 0,
        cate_id: int | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"sizeMode": int(size_mode)}
        if cate_id:
            options["cateId"] = int(cate_id)
        return self.call(
            "bg.goods.image.upload.global",
            {"image": str(image), "imageBizType": int(image_biz_type), "options": options},
        )

    # ---------- 缓存 ----------

    def _cache_get(self, key: str) -> Any | None:
        now = time.monotonic()
        store, lock = (
            (_SHARED_CATALOG_CACHE, _SHARED_CATALOG_LOCK)
            if _is_shared_catalog_key(key)
            else (self._cache, self._cache_lock)
        )
        with lock:
            item = store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                store.pop(key, None)
                return None
            return value

    def _cache_drop(self, key: str) -> None:
        """只清某一条缓存（写完数据后让对应读缓存立即失效）。"""
        store, lock = (
            (_SHARED_CATALOG_CACHE, _SHARED_CATALOG_LOCK)
            if _is_shared_catalog_key(key)
            else (self._cache, self._cache_lock)
        )
        with lock:
            store.pop(key, None)

    def _cache_put(self, key: str, value: Any, ttl: int) -> None:
        store, lock = (
            (_SHARED_CATALOG_CACHE, _SHARED_CATALOG_LOCK)
            if _is_shared_catalog_key(key)
            else (self._cache, self._cache_lock)
        )
        with lock:
            store[key] = (time.monotonic() + max(1, int(ttl)), value)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
        clear_shared_catalog_cache()


def _response_json(response: Any, api_type: str) -> dict[str, Any]:
    try:
        data = response.json()
    except (ValueError, UnicodeError) as exc:
        raise TemuGatewayError(
            api_type, "网关返回内容不是合法 JSON", error_code="INVALID_RESPONSE"
        ) from exc
    if not isinstance(data, dict):
        raise TemuGatewayError(api_type, "网关返回结构异常", error_code="INVALID_RESPONSE")
    return data


def _retry_after_seconds(response: Any) -> float | None:
    raw = getattr(response, "headers", {}) or {}
    value = raw.get("Retry-After") if hasattr(raw, "get") else None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

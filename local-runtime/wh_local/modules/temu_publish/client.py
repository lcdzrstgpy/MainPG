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
CACHE_TTL_CATEGORIES = 6 * 60 * 60
CACHE_TTL_ATTRIBUTES = 60 * 60
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
        return self.call(
            "bg.glo.goods.catsmandatory.get",
            {
                "leafCatId": int(leaf_cat_id),
                "bindSiteIds": sorted({int(value) for value in bind_site_ids if int(value) > 0}),
                "configItems": [1, 2, 3],
                "productPropertyReqs": list(product_property_reqs or []),
            },
        )

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
        with self._cache_lock:
            item = self._cache.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._cache.pop(key, None)
                return None
            return value

    def _cache_drop(self, key: str) -> None:
        """只清某一条缓存（写完数据后让对应读缓存立即失效）。"""
        with self._cache_lock:
            self._cache.pop(key, None)

    def _cache_put(self, key: str, value: Any, ttl: int) -> None:
        with self._cache_lock:
            self._cache[key] = (time.monotonic() + max(1, int(ttl)), value)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()


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

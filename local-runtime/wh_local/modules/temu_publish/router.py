"""Temu 商品发布配套 API（/api/temu-publish）。"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ...session import Actor, actor_from_authorization
from .client import TemuCredentialMissing, TemuGatewayError
from .submissions import DuplicateSubmission
from .library import collect_draft_library
from .schemas import (
    DetailImageUploadRequest,
    CategoryMatchRequest,
    GoodsDraftInput,
    ImageUploadRequest,
    ImageUploadUrlRequest,
    SpecCreateRequest,
    TemuSettingsUpdate,
)
from .service import TemuPublishService

logger = logging.getLogger("wh_local.temu_publish")

# 网关鉴权类错误：告诉用户到底该改哪一处，而不是只丢一句「client_id or token is invalid」。
_GATEWAY_HINTS = {
    "CLIENT_TOKEN_REQUIRED": (
        "盈銮网关凭据没填全：这里要填的是盈銮后台的 client_id / token，"
        "不是 Temu 店铺的 access_token"
    ),
    "CLIENT_TOKEN_INVALID": (
        "这是盈銮网关的 client_id / token 无效，跟店铺 access_token 无关（请求还没到 Temu）。"
        "让给凭据的人确认：client 和 token 是不是同一对、有没有被重置、本机公网 IP 在不在白名单"
    ),
    "CLIENT_PERMISSION_DENIED": (
        "盈銮网关拒绝了这个 client：多半是应用没开该接口权限，或本机 IP 不在白名单"
    ),
    "7000006": "这个 access_token 没和商务应用绑定：回店铺后台重新授权，再把新 token 填进来",
    "NETWORK_ERROR": (
        "连不上盈銮网关（TLS 握手被中断）。常见原因是系统代理把这条国内网关请求也劫走了；"
        "程序默认不走系统代理，确实需要走代理时用环境变量 WH_TEMU_PROXY 指定"
    ),
}


def _gateway_message(error_code: str, message: str) -> str:
    """把网关错误拼成「原因（错误码）+ 该怎么改」。"""
    reason = (message or "").strip() or "未返回原因"
    suffix = f"（错误码 {error_code}）" if error_code else ""
    hint = _GATEWAY_HINTS.get(str(error_code or "").upper())
    return f"Temu 拒绝了这次提交：{reason}{suffix}" + (f"。{hint}" if hint else "")


# 需要到盈銮/上游修配置的错误码，按网关错误（502）返回，便于前端区分“参数问题”。
_UPSTREAM_ERROR_CODES = {
    "CLIENT_TOKEN_REQUIRED",
    "CLIENT_TOKEN_INVALID",
    "CLIENT_PERMISSION_DENIED",
    "7000006",  # access_token and app_key are not mapping
    "UPSTREAM_ERROR",
    "NETWORK_ERROR",
    "INVALID_RESPONSE",
}


# 草稿库聚合结果短缓存：进页面/切页签回来不必重新扫 8 个任务 + 6 个批次。
# key = (账号, limit)；发布/刷新会带 refresh=1 绕过。
_LIBRARY_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_LIBRARY_CACHE_TTL = 60.0
_LIBRARY_CACHE_LOCK = threading.Lock()


def create_temu_publish_router(
    database_path: Path,
    *,
    product_processing: Any | None = None,
    pod_service_getter: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/temu-publish", tags=["temu-publish"])
    service = TemuPublishService(database_path)

    def _service(actor: Actor) -> TemuPublishService:
        """凭证按账号隔离：每次请求都换成当前登录账号的店铺上下文。"""
        return service.with_owner(actor.id)

    def _handle(exc: Exception) -> HTTPException:
        if isinstance(exc, DuplicateSubmission):
            # 409：前端靠 detail.message 里的「重复提交」前缀弹二次确认
            return HTTPException(
                status_code=409,
                detail={
                    "error_code": "TEMU_DUPLICATE_SUBMISSION",
                    "message": str(exc),
                    "previous_status": exc.previous_status,
                    "previous_at": exc.previous_at,
                },
            )
        if isinstance(exc, TemuCredentialMissing):
            return HTTPException(
                status_code=503,
                detail={"error_code": "TEMU_CREDENTIAL_MISSING", "message": str(exc)},
            )
        if isinstance(exc, ValueError):
            return HTTPException(
                status_code=422,
                detail={"error_code": "INVALID_ARGUMENT", "message": str(exc)},
            )
        if isinstance(exc, TemuGatewayError):
            if exc.http_status == 429:
                status = 429
            elif exc.http_status in (401, 403) or exc.error_code in _UPSTREAM_ERROR_CODES:
                status = 502
            else:
                status = 400
            # 把上游原话打进日志：access log 只有状态码，出问题时无从下手
            logger.warning(
                "Temu 拒绝请求 api_type=%s error_code=%s http=%s message=%s",
                exc.api_type,
                exc.error_code,
                exc.http_status,
                exc.message,
            )
            # 前端只对「看起来像英文」的未知错误兜底成「操作失败」，所以这里包成中文，
            # 让 Temu 的原话能直接显示给用户（否则永远只有一个状态码可看）。
            return HTTPException(
                status_code=status,
                detail={
                    "error_code": exc.error_code or "TEMU_GATEWAY_ERROR",
                    "message": _gateway_message(exc.error_code, exc.message),
                    "api_type": exc.api_type,
                },
            )
        logger.exception("temu_publish 未预期异常")
        return HTTPException(status_code=500, detail={"error_code": "INTERNAL", "message": str(exc)})

    # ---------- 凭证 ----------

    @router.get("/settings")
    def get_settings(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        """只返回当前账号的配置状态与脱敏提示，不返回密钥原文。"""
        return _service(actor).settings_status()

    @router.put("/settings")
    def save_settings(
        payload: TemuSettingsUpdate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).save_settings(payload, actor.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.delete("/settings")
    def clear_settings(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        """解绑：删掉当前账号名下保存的店铺凭证（不影响其他账号）。"""
        return _service(actor).clear_settings(actor.id)

    @router.post("/token/verify")
    def verify_token(
        refresh: bool = Query(default=False, description="true=跳过缓存，真去打接口"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).verify_token(refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - 统一转成结构化 HTTP 错误
            raise _handle(exc) from exc

    # ---------- 草稿库 ----------

    @router.get("/draft-library")
    def draft_library(
        limit: int = Query(default=60, ge=1, le=200),
        refresh: bool = Query(default=False, description="true=跳过 60 秒缓存，强制重扫"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        """聚合 AI 处理结果与 POD 成品，供发布页一键带入。

        结果缓存 60 秒（按账号 + limit）：草稿库切进切出不用重扫任务和批次。
        """
        key = (actor.id, limit)
        now = time.time()
        if not refresh:
            with _LIBRARY_CACHE_LOCK:
                hit = _LIBRARY_CACHE.get(key)
            if hit and now - hit[0] < _LIBRARY_CACHE_TTL:
                return hit[1]

        pod_service = pod_service_getter() if callable(pod_service_getter) else None
        result = collect_draft_library(
            product_processing=product_processing,
            pod_service=pod_service,
            actor=actor,
            limit=limit,
        )
        with _LIBRARY_CACHE_LOCK:
            _LIBRARY_CACHE[key] = (now, result)
        return result

    # ---------- 发布配套查询 ----------

    @router.get("/categories")
    def list_categories(
        parent_cat_id: int = Query(default=0, ge=0),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).categories(parent_cat_id)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/categories/match")
    def match_category(
        payload: CategoryMatchRequest,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).match_category(payload.search_text, payload.site_id)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.get("/categories/{cat_id}/attributes")
    def category_attributes(
        cat_id: int,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).attributes(cat_id)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.get("/parent-specs")
    def parent_specs(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        try:
            return _service(actor).parent_specs()
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/specs")
    def create_spec(
        payload: SpecCreateRequest,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).create_spec(payload.parent_spec_id, payload.spec_name)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.get("/accessories")
    def list_accessories(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        fuzzy_value: str = Query(default=""),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).accessories(page, page_size, fuzzy_value)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.get("/freight-templates")
    def freight_templates(
        site_ids: str = Query(default="100", description="逗号分隔的站点 ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).freight_templates(_parse_ids(site_ids))
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.get("/warehouses")
    def warehouses(
        site_ids: str = Query(default="100", description="逗号分隔的站点 ID"),
        supplier_id: int | None = Query(default=None, ge=1),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).warehouses(_parse_ids(site_ids), supplier_id)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    # ---------- 图片与发布请求体 ----------

    @router.post("/images/upload")
    def upload_image(
        payload: ImageUploadRequest,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            return _service(actor).upload_image(
                payload.image,
                image_biz_type=payload.image_biz_type,
                size_mode=payload.size_mode,
                cate_id=payload.cate_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/images/upload-detail")
    def upload_detail_image(
        payload: DetailImageUploadRequest,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        """详情图上传：服务端统一宽度后传 Temu（商详要求所有楼层等宽）。"""
        try:
            return _service(actor).upload_detail_image(
                payload.image,
                target_width=payload.target_width,
                cate_id=payload.cate_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/images/upload-url")
    def upload_image_url(
        payload: ImageUploadUrlRequest,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        """服务端代下载公网图片后上传 Temu（草稿库外链图用）。"""
        try:
            return _service(actor).upload_image_from_url(
                payload.url,
                image_biz_type=payload.image_biz_type,
                size_mode=payload.size_mode,
                cate_id=payload.cate_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/payload/build")
    def build_payload(
        payload: GoodsDraftInput,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        """组装 goods.add 请求体；本接口不会提交发布。"""
        try:
            return _service(actor).build_goods_payload(payload)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/mandatory-check")
    def check_mandatory(
        payload: GoodsDraftInput,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        """按类目必填配置校验当前表单（bg.glo.goods.catsmandatory.get）；不会提交商品。"""
        try:
            return _service(actor).check_mandatory(payload)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    @router.post("/publish")
    def publish_goods(
        payload: GoodsDraftInput,
        force: bool = Query(default=False, description="true=跳过「24 小时内重复提交」拦截"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        """真正提交到 Temu 店铺（bg.glo.goods.add）。

        ⚠️ 有外部副作用：会在店铺里创建商品，且接口非幂等 —— 客户端内部不重试。
        前端必须先让用户确认再调用；同一条草稿 24 小时内重复提交返回 409，
        用户确认要重复上架时带 force=true。
        """
        try:
            return _service(actor).publish_goods(payload, force=force)
        except Exception as exc:  # noqa: BLE001
            raise _handle(exc) from exc

    return router


def _parse_ids(raw: str) -> list[int]:
    values: list[int] = []
    for chunk in str(raw or "").split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError as exc:
            raise ValueError(f"站点 ID 不是整数：{text}") from exc
        if value > 0:
            values.append(value)
    return values or [100]

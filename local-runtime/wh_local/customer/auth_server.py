from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import html
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import requests
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from ..billing import (
    active_pricing,
    reserve_ai_usage,
    settle_payment_order,
    settle_ai_usage_failure,
    settle_ai_usage_success,
    update_active_pricing,
    usage_history,
)
from ..config import default_config
from ..db import init_db, transaction
from ..modules.product_processing.domain.policy import is_safe_external_url
from ..session import Actor
from .auth_service import SQLiteCustomerAuthService
from .credential_vault import CredentialVaultError, active_secret
from .contracts import CustomerAuthActionResult, CustomerAuthResult, CustomerAuthUnavailable
from .email_sender import TencentCloudSESEmailSender
from .alipay_gateway import (
    AlipayGatewayConfigurationError,
    AlipaySignatureError,
    build_page_payment_url,
    is_configured as alipay_is_configured,
    verify_callback as verify_alipay_callback,
)


REMOTE_SESSION_TTL = timedelta(hours=12)
BILLING_POINT_RATIO = 100
BILLING_TOPUP_PRODUCTS = {
    "points_10": {"amount_cents": 1000, "points": 1000, "label": "10 元积分包"},
    "points_30": {"amount_cents": 3000, "points": 3000, "label": "30 元积分包"},
    "points_100": {"amount_cents": 10000, "points": 10000, "label": "100 元积分包"},
}
# Amounts are immutable product amounts.  Their point value is calculated
# from the active server rule, never from this legacy display mapping.
TOPUP_PACKAGE_CENTS = {
    package_id: {"amount_cents": int(item["amount_cents"]), "label": str(item["label"])}
    for package_id, item in BILLING_TOPUP_PRODUCTS.items()
}
# Monetary amount and label are stable package metadata. Point quantity is
# calculated from the active server-side pricing rule at order creation time.
TOPUP_PACKAGE_CENTS = {
    package_id: {
        "amount_cents": int(product["amount_cents"]),
        "label": str(product["label"]),
    }
    for package_id, product in BILLING_TOPUP_PRODUCTS.items()
}
CUSTOM_TOPUP_MIN_CENTS = 100
CUSTOM_TOPUP_MAX_CENTS = 300_000
PAYMENT_PROVIDERS = {"wechat", "alipay"}
TEXT_CHAT_URL = "https://api.aicoming.top/v1/chat/completions"
TEXT_MODEL = "gpt-5.6-terra"
WUYIN_IMAGE_SUBMIT_URL = "https://api.wuyinkeji.com/api/async/image_gpt"
WUYIN_IMAGE_DETAIL_URL = "https://api.wuyinkeji.com/api/async/detail"
_TEXT_GATEWAY_SEMAPHORE = threading.BoundedSemaphore(value=4)
MAX_CHAT_REQUEST_BYTES = 12 * 1024 * 1024
MAX_CHAT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_CHAT_CONTENT_PARTS = 16
MAX_GATEWAY_RESPONSE_BYTES = 8 * 1024 * 1024
# One product item bundles one vision request and one listing-text request.
# Legacy image modes can issue four primary slot calls, four slot recoveries,
# one detail call, and up to four configured detail repair rounds.
GATEWAY_DISTINCT_REQUEST_LIMITS = {
    "product_processing.text": 2,
    "product_processing.image_grid_2k": 13,
}
# Text adapters retry an identical upstream request at most three times; the
# image generation adapter has a five-attempt outer budget.  Keep the server
# ledger at those same bounds so a reserved usage cannot be replayed forever.
GATEWAY_SAME_REQUEST_ATTEMPT_LIMITS = {
    "product_processing.text": 3,
    "product_processing.image_grid_2k": 5,
}
GATEWAY_LEASE_SECONDS = {
    "product_processing.text": 600,
    "product_processing.image_grid_2k": 900,
}
DEFAULT_ALIPAY_LOCAL_RETURN_URL = "http://127.0.0.1:8010/?module=personal_center&payment=success"


def _alipay_local_return_url() -> str:
    """Return only a local desktop-workbench URL after browser payment."""

    value = os.environ.get("ALIPAY_LOCAL_RETURN_URL", DEFAULT_ALIPAY_LOCAL_RETURN_URL).strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return DEFAULT_ALIPAY_LOCAL_RETURN_URL
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return DEFAULT_ALIPAY_LOCAL_RETURN_URL
    return value


@dataclass(frozen=True)
class _GatewayRequestClaim:
    cached_response: dict[str, Any] | None = None
    provider_task_id: str = ""
    submit_uncertain: bool = False


class _ImageProviderTerminalFailure(RuntimeError):
    """The provider confirmed that an image task cannot succeed."""


class _ImageSubmitUncertain(RuntimeError):
    """The upstream may have accepted an image submit without a durable task id."""


def _server_provider_secret(kind: str, environment_name: str) -> str:
    """Prefer the encrypted credential vault while retaining the legacy env fallback.

    The billed gateway must never expose provider credentials to desktop clients.
    Existing deployments may still be configured only through environment variables,
    so a missing vault is not itself a reason to interrupt an otherwise valid job.
    """
    legacy = str(os.environ.get(environment_name) or "").strip()
    try:
        vaulted = str(active_secret(kind) or "").strip()
    except CredentialVaultError as exc:
        if legacy:
            return legacy
        raise HTTPException(
            status_code=503,
            detail=f"server {kind} credential vault is unavailable",
        ) from exc
    return vaulted or legacy


def create_auth_app(database_path: Path | None = None) -> FastAPI:
    """Create the standalone platform customer-auth service.

    The local workbench can point WH_LOCAL_CUSTOMER_AUTH_BASE_URL at this app.
    This service owns platform accounts/passwords and returns normalized account
    data plus a remote wh_auth_* token. The local workbench still creates its
    own wh_local_* session for business modules.
    """

    config = default_config()
    db_path = database_path or config.database_path
    init_db(db_path)
    service = SQLiteCustomerAuthService(
        db_path,
        email_sender=TencentCloudSESEmailSender.from_env(),
        email_code_secret=os.environ.get("WH_EMAIL_CODE_SECRET", ""),
    )

    app = FastAPI(title="W-H Platform Customer Auth Service", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_path": str(db_path), "service": "customer-auth"}

    @app.post("/api/customer/register")
    def register(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.register, payload))

    @app.post("/api/customer/login")
    def login(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            customer = service.login(payload)
            remote_session = _issue_platform_session(
                db_path,
                customer.customer_id,
                user_agent=request.headers.get("user-agent", ""),
                client_ip=request.client.host if request.client else "",
            )
            return {
                "ok": True,
                "token": remote_session["token"],
                "expires_at": remote_session["expires_at"],
                "account": _account_payload(customer),
            }
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/customer/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer_token(authorization)
        account = _account_by_token(db_path, token)
        if account is None:
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return {"ok": True, "account": account}

    @app.get("/api/customer/billing/summary")
    def billing_summary(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        return _billing_summary(db_path, account)

    @app.get("/api/customer/billing/rules")
    def billing_rules(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _required_account(db_path, authorization)
        return {"ok": True, "pricing": active_pricing(db_path)}

    @app.get("/api/customer/billing/usage")
    def billing_usage_history(
        cursor: str = "",
        limit: int = 30,
        feature_key: str = "",
        usage_status: str = "",
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        return usage_history(
            db_path,
            account_id=str(account["account_id"]),
            cursor=cursor,
            limit=limit,
            feature_key=feature_key,
            usage_status=usage_status,
        )

    @app.post("/api/customer/billing/topup-orders")
    def create_billing_topup_order(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        return _create_topup_order(db_path, account, payload)

    @app.post("/api/customer/billing/usage/reserve")
    def reserve_billing_usage(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        feature_key = str(payload.get("feature_key") or "").strip()
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if feature_key not in {"product_processing.text", "product_processing.image_grid_2k"}:
            raise HTTPException(status_code=400, detail="unsupported billing feature")
        if not 16 <= len(idempotency_key) <= 200:
            raise HTTPException(status_code=400, detail="idempotency_key is required")
        pricing = active_pricing(db_path)
        supplied = payload.get("pricing_rule_version")
        try:
            supplied_rule_version = int(supplied) if supplied is not None else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=428, detail="billing_rules_sync_required") from exc
        # Older desktop builds do not send a rule version. Their reservation is
        # still safe because the server computes and snapshots the amount; new
        # builds receive a deterministic stale-rule response instead.
        if supplied_rule_version is not None and supplied_rule_version != int(pricing["rule_version"]):
            raise HTTPException(status_code=428, detail="billing_rules_sync_required")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return {
            "ok": True,
            "usage": reserve_ai_usage(
                db_path,
                _billing_actor(account),
                feature_key=feature_key,
                idempotency_key=idempotency_key,
                quantity=1,
                source_ref=str(payload.get("source_ref") or "")[:200],
                metadata=_safe_billing_metadata(metadata),
            ),
        }

    @app.get("/api/admin/billing/pricing")
    def admin_billing_pricing(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _require_billing_admin(db_path, authorization)
        return {"ok": True, "pricing": active_pricing(db_path)}

    @app.put("/api/admin/billing/pricing")
    def update_admin_billing_pricing(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        admin = _require_billing_admin(db_path, authorization)
        return {
            "ok": True,
            "pricing": update_active_pricing(
                db_path,
                payload=payload,
                updated_by=str(admin["account_id"]),
            ),
        }

    @app.get("/api/admin/billing/usage")
    def admin_billing_usage(
        account_id: str,
        cursor: str = "",
        limit: int = 50,
        feature_key: str = "",
        usage_status: str = "",
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_billing_admin(db_path, authorization)
        if not account_id.strip():
            raise HTTPException(status_code=400, detail="account_id is required")
        return usage_history(
            db_path,
            account_id=account_id.strip(),
            cursor=cursor,
            limit=limit,
            feature_key=feature_key,
            usage_status=usage_status,
        )

    @app.post("/api/customer/billing/usage/{usage_id}/succeed")
    def settle_billing_usage_success(
        usage_id: str,
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        _ensure_usage_owner(db_path, usage_id, str(account["account_id"]))
        feature_key = _usage_feature(db_path, usage_id)
        provider, model = _fixed_usage_provider(feature_key)
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return {
            "ok": True,
            "usage": settle_ai_usage_success(
                db_path,
                usage_id,
                provider=provider,
                model=model,
                provider_task_id=str(payload.get("provider_task_id") or "")[:240],
                input_tokens=_safe_int(payload.get("input_tokens")),
                output_tokens=_safe_int(payload.get("output_tokens")),
                total_tokens=_safe_int(payload.get("total_tokens")),
                metadata=_safe_billing_metadata(metadata),
            ),
        }

    @app.post("/api/customer/billing/usage/{usage_id}/fail")
    def settle_billing_usage_failure(
        usage_id: str,
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        usage = settle_ai_usage_failure(
            db_path,
            usage_id,
            error_message=str(payload.get("error_message") or "AI operation failed")[:500],
            expected_account_id=str(account["account_id"]),
            reject_gateway_activity=True,
        )
        return {
            "ok": True,
            "usage_id": usage_id,
            "status": str(usage.get("status") or "failed"),
            "usage": usage,
        }

    @app.post("/api/customer/ai/chat")
    def server_managed_ai_chat(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        usage_id = str(payload.get("usage_id") or "")
        _require_reserved_usage(
            db_path,
            usage_id=usage_id,
            account_id=str(account["account_id"]),
            feature_key="product_processing.text",
        )
        messages = _validated_chat_messages(payload.get("messages"))
        if str(payload.get("model") or TEXT_MODEL).strip() != TEXT_MODEL:
            raise HTTPException(status_code=400, detail="unsupported server-managed text model")
        request_hash = _gateway_request_hash(
            {"model": TEXT_MODEL, "messages": messages}
        )
        api_key = _server_provider_secret("text", "WH_TEXT_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="server text credential is not configured")
        claim = _claim_gateway_request(
            db_path,
            usage_id=usage_id,
            account_id=str(account["account_id"]),
            feature_key="product_processing.text",
            request_hash=request_hash,
        )
        if claim.cached_response is not None:
            return claim.cached_response
        try:
            response_payload = _sanitized_gateway_response(
                _server_text_chat(api_key, messages),
                secrets=(api_key,),
            )
            _complete_gateway_request(db_path, usage_id, request_hash, response_payload)
            return response_payload
        except Exception:
            _fail_gateway_request(db_path, usage_id, request_hash)
            raise

    @app.post("/api/customer/ai/image")
    def server_managed_ai_image(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        usage_id = str(payload.get("usage_id") or "")
        _require_reserved_usage(
            db_path,
            usage_id=usage_id,
            account_id=str(account["account_id"]),
            feature_key="product_processing.image_grid_2k",
        )
        prompt = str(payload.get("prompt") or "").strip()
        urls = payload.get("urls") or []
        size = str(payload.get("size") or "1:1").strip().lower()
        if not 1 <= len(prompt) <= 24_000 or not isinstance(urls, list) or len(urls) > 4:
            raise HTTPException(status_code=400, detail="image request is invalid")
        urls = [str(value).strip() for value in urls]
        if any(not _trusted_provider_reference_url(value) for value in urls):
            raise HTTPException(status_code=400, detail="image reference URL is invalid")
        if size not in {
            "auto", "1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4",
            "21:9", "9:21", "1:3", "3:1", "2:1", "1:2",
        }:
            raise HTTPException(status_code=400, detail="image size is invalid")
        request_hash = _gateway_request_hash(
            {"prompt": prompt, "size": size, "urls": urls}
        )
        api_key = _server_provider_secret("image", "WH_WUYIN_IMAGE_API_KEY")
        if not api_key:
            raise HTTPException(status_code=503, detail="server image credential is not configured")
        claim = _claim_gateway_request(
            db_path,
            usage_id=usage_id,
            account_id=str(account["account_id"]),
            feature_key="product_processing.image_grid_2k",
            request_hash=request_hash,
        )
        if claim.cached_response is not None:
            return claim.cached_response
        if claim.submit_uncertain:
            raise HTTPException(status_code=503, detail="server image submit outcome is uncertain")
        try:
            task_id = claim.provider_task_id
            if not task_id:
                _mark_gateway_submitting(db_path, usage_id, request_hash)
                task_id = _submit_server_wuyin(api_key, prompt, urls, size)
                try:
                    _record_gateway_provider_task(db_path, usage_id, request_hash, task_id)
                except Exception as exc:
                    # The provider may already have accepted the request.  Never
                    # turn a local persistence failure into an automatic resubmit.
                    raise _ImageSubmitUncertain() from exc
            result_url = _poll_server_wuyin(api_key, task_id)
            response_payload = {"ok": True, "task_id": task_id, "result_url": result_url}
            _complete_gateway_request(db_path, usage_id, request_hash, response_payload)
            return response_payload
        except _ImageProviderTerminalFailure as exc:
            _fail_gateway_request(
                db_path,
                usage_id,
                request_hash,
                phase="terminal_failed",
                clear_provider_task=True,
            )
            raise HTTPException(status_code=502, detail="server image provider task failed") from exc
        except _ImageSubmitUncertain as exc:
            _mark_gateway_submit_uncertain(db_path, usage_id, request_hash)
            raise HTTPException(
                status_code=503,
                detail="server image submit outcome is uncertain",
            ) from exc
        except Exception:
            if _gateway_provider_task_id(db_path, usage_id, request_hash):
                _pause_gateway_request(db_path, usage_id, request_hash)
            else:
                _fail_gateway_request(db_path, usage_id, request_hash)
            raise

    @app.post("/api/customer/billing/payment-callback/{provider}")
    async def billing_payment_callback(provider: str, request: Request) -> PlainTextResponse:
        """Accept a provider callback only after its official signature checks."""

        if provider.strip().lower() != "alipay":
            raise HTTPException(status_code=503, detail="payment callback provider is not configured")
        try:
            # Alipay sends an application/x-www-form-urlencoded callback.
            # Parsing its body directly avoids an extra multipart dependency.
            raw_payload = (await request.body()).decode("utf-8")
            payload = {
                str(key): str(value)
                for key, value in parse_qsl(raw_payload, keep_blank_values=True)
            }
            verified = verify_alipay_callback(payload)
            settle_payment_order(
                db_path,
                provider="alipay",
                out_trade_no=verified["out_trade_no"],
                gateway_transaction_id=verified["trade_no"],
                amount_cents=int(verified["amount_cents"]),
                provider_status=verified["trade_status"],
                metadata={"buyer_id": verified["buyer_id"]},
            )
        except AlipayGatewayConfigurationError as exc:
            raise HTTPException(status_code=503, detail="Alipay payment is not configured") from exc
        except AlipaySignatureError as exc:
            raise HTTPException(status_code=400, detail="Alipay callback verification failed") from exc
        return PlainTextResponse(content="success")

    @app.get("/api/customer/billing/payment-return")
    def billing_payment_return() -> HTMLResponse:
        """Bring the payer back to the installed workbench after payment."""

        target = _alipay_local_return_url()
        escaped_target = html.escape(target, quote=True)
        script_target = json.dumps(target)
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            f"<meta http-equiv='refresh' content='0;url={escaped_target}'><title>支付完成</title></head>"
            "<body><p>支付结果已返回工作台，正在跳转...</p>"
            f"<script>window.location.replace({script_target});</script>"
            f"<p><a href='{escaped_target}'>无法跳转时，点击返回工作台</a></p></body></html>"
        )

    @app.post("/api/customer/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        token = _bearer_token(authorization)
        with transaction(db_path) as conn:
            session_row = conn.execute(
                """
                SELECT account_id
                FROM auth_platform_sessions
                WHERE token_hash = ? AND revoked_at = ''
                """,
                (_hash_token(token),),
            ).fetchone()
            conn.execute(
                """
                UPDATE auth_platform_sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at = ''
                """,
                (_utc_now(), _hash_token(token)),
            )
            if session_row is not None:
                # 该账号若无其他未过期且未撤销的会话，则账号整体回到离线状态。
                remaining = conn.execute(
                    """
                    SELECT 1 FROM auth_platform_sessions
                    WHERE account_id = ?
                      AND revoked_at = ''
                      AND expires_at > ?
                    """,
                    (session_row["account_id"], _utc_now()),
                ).fetchone()
                if remaining is None:
                    conn.execute(
                        """
                        UPDATE auth_accounts
                        SET login_status = 'offline', updated_at = ?
                        WHERE account_id = ?
                        """,
                        (_utc_now(), session_row["account_id"]),
                    )
        return {"ok": True}

    @app.post("/api/customer/activate")
    def activate(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.activate, payload))

    @app.post("/api/customer/email-code")
    def email_code(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        enriched_payload = dict(payload)
        enriched_payload.setdefault("request_ip", request.client.host if request.client else "")
        enriched_payload.setdefault("user_agent", request.headers.get("user-agent", ""))
        return _action_response(_call_action(service.email_code, enriched_payload))

    @app.post("/api/customer/password-reset")
    def password_reset(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.password_reset, payload))

    @app.post("/api/customer/change-password")
    def change_password(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.change_password, payload))

    @app.post("/api/customer/forgot-password")
    def forgot_password(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        enriched_payload = dict(payload)
        enriched_payload.setdefault("request_ip", request.client.host if request.client else "")
        enriched_payload.setdefault("user_agent", request.headers.get("user-agent", ""))
        return _action_response(_call_action(service.forgot_password, enriched_payload))

    @app.post("/api/customer/reset-password")
    def reset_password(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.reset_password, payload))

    # ---- 邀请码管理（管理员在服务器上生成/查看邀请码） ----
    @app.post("/api/customer/invitations/generate")
    def generate_invitations(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            token = _bearer_token(authorization)
            actor = _account_by_token(db_path, token)
            if actor is None:
                raise HTTPException(status_code=401, detail="invalid bearer token")
            if str(actor.get("role", "")).lower() != "admin":
                raise HTTPException(status_code=403, detail="admin role required")
        except HTTPException:
            raise
        except Exception as exc:
            _raise_http_error(exc)

        try:
            count = int(payload.get("count", 1))
            max_uses = int(payload.get("max_uses", 100))
            expires_at = str(payload.get("expires_at", "") or "")
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="count/max_uses must be integers")

        count = max(1, min(count, 500))
        max_uses = max(1, max_uses)
        codes = [_invitation_code() for _ in range(count)]
        now = _utc_now()
        with transaction(db_path) as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO invitation_codes (
                    code, max_uses, used_count, expires_at, created_by, created_at
                )
                VALUES (?, ?, 0, ?, ?, ?)
                """,
                [(code, max_uses, expires_at, actor.get("username", ""), now) for code in codes],
            )
        return {"ok": True, "count": len(codes), "codes": codes}

    @app.get("/api/customer/invitations")
    def list_invitations(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            token = _bearer_token(authorization)
            actor = _account_by_token(db_path, token)
            if actor is None:
                raise HTTPException(status_code=401, detail="invalid bearer token")
            if str(actor.get("role", "")).lower() != "admin":
                raise HTTPException(status_code=403, detail="admin role required")
        except HTTPException:
            raise
        except Exception as exc:
            _raise_http_error(exc)

        with transaction(db_path) as conn:
            rows = conn.execute(
                """
                SELECT code, max_uses, used_count, expires_at, created_by, created_at
                FROM invitation_codes
                ORDER BY created_at DESC
                """,
            ).fetchall()
        return {
            "ok": True,
            "invitations": [
                {
                    "code": row["code"],
                    "max_uses": row["max_uses"],
                    "used_count": row["used_count"],
                    "expires_at": row["expires_at"],
                    "created_by": row["created_by"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ],
        }

    # ---- 采集凭据下发（OneBound API key 只在服务器持有，按用户身份加密下发） ----
    @app.post("/api/customer/collect-key")
    def collect_key(payload: dict[str, Any]) -> dict[str, Any]:
        account_id = str(payload.get("account_id") or "").strip()
        username = str(payload.get("username") or "").strip()
        workspace_code = str(payload.get("workspace_code") or "").strip()
        encrypted_session_key = str(payload.get("encrypted_session_key") or "")
        if (not account_id and not username) or not encrypted_session_key:
            raise HTTPException(status_code=400, detail="account_id/username and encrypted_session_key are required")

        # 校验用户存在且有效（账号必须在服务器注册过）
        with transaction(db_path) as conn:
            if account_id:
                row = conn.execute(
                    """
                    SELECT a.account_id, a.account_status
                    FROM auth_accounts a
                    LEFT JOIN workspaces w ON w.workspace_id = a.workspace_id
                    WHERE a.account_id = ?
                      AND (? = '' OR w.workspace_code = ?)
                    """,
                    (account_id, workspace_code, workspace_code),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT a.account_id, a.account_status
                    FROM auth_accounts a
                    LEFT JOIN workspaces w ON w.workspace_id = a.workspace_id
                    WHERE lower(a.username) = lower(?)
                      AND (? = '' OR w.workspace_code = ?)
                    """,
                    (username, workspace_code, workspace_code),
                ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="user is not registered on the server")
        if str(row["account_status"]).strip().lower() in {"disabled", "inactive", "locked", "suspended", "deleted"}:
            raise HTTPException(status_code=403, detail="user account is not active")

        # 用服务器私钥解出临时 AES 会话密钥，再加密 OneBound 凭据下发
        try:
            session_key = _rsa_decrypt_session_key(encrypted_session_key)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="cannot decrypt session key") from exc

        credentials = _server_onebound_config()
        if not credentials.get("api_key") or not credentials.get("api_secret"):
            raise HTTPException(status_code=503, detail="collect credentials are not configured on the server")

        plaintext = json.dumps(credentials).encode("utf-8")
        nonce = os.urandom(12)
        encryptor = Cipher(algorithms.AES(session_key), modes.GCM(nonce)).encryptor()
        ciphertext = encryptor.update(plaintext) + encryptor.finalize()
        return {
            "ok": True,
            "payload": base64.b64encode(ciphertext).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "tag": base64.b64encode(encryptor.tag).decode("ascii"),
        }

    return app


def create_default_auth_app() -> FastAPI:
    return create_auth_app()


def _call_action(func: Any, payload: dict[str, Any]) -> CustomerAuthActionResult:
    try:
        return func(payload)
    except Exception as exc:
        _raise_http_error(exc)


def _action_response(result: CustomerAuthActionResult) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": result.ok, "message": result.message}
    if result.raw:
        response["raw"] = result.raw
    return response


def _account_payload(customer: CustomerAuthResult) -> dict[str, Any]:
    return {
        "account_id": customer.customer_id,
        "customer_id": customer.customer_id,
        "username": customer.username,
        "email": customer.email,
        "display_name": customer.username,
        "account_status": customer.account_status or "active",
        "login_status": customer.login_status or "offline",
        "role": customer.role or "admin",
        "workspace_code": customer.workspace_code,
        "workspace_name": customer.workspace_name,
        "workspace": {"code": customer.workspace_code, "name": customer.workspace_name},
        "raw": customer.raw,
    }


def _issue_platform_session(
    database_path: Path,
    account_id: str,
    *,
    user_agent: str = "",
    client_ip: str = "",
) -> dict[str, str]:
    token = f"wh_auth_{secrets.token_urlsafe(32)}"
    session_id = f"auth_sess_{secrets.token_urlsafe(24)}"
    now = _utc_now()
    expires_at = (datetime.now(timezone.utc) + REMOTE_SESSION_TTL).isoformat(timespec="seconds")
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO auth_platform_sessions (
                session_id, account_id, token_hash, expires_at, revoked_at,
                last_used_at, created_at, user_agent, client_ip
            )
            VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
            """,
            (session_id, account_id, _hash_token(token), expires_at, now, now, user_agent, client_ip),
        )
    return {"session_id": session_id, "token": token, "expires_at": expires_at}


def _account_by_token(database_path: Path, token: str) -> dict[str, Any] | None:
    now = _utc_now()
    token_hash = _hash_token(token)
    with transaction(database_path) as conn:
        row = conn.execute(
            """
            SELECT
                a.account_id,
                a.username,
                a.email,
                a.display_name,
                a.role,
                a.workspace_id,
                a.account_status,
                a.login_status,
                w.workspace_code,
                w.workspace_name
            FROM auth_platform_sessions s
            JOIN auth_accounts a ON a.account_id = s.account_id
            LEFT JOIN workspaces w ON w.workspace_id = a.workspace_id
            WHERE s.token_hash = ?
              AND s.revoked_at = ''
              AND s.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE auth_platform_sessions SET last_used_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
    return {
        "account_id": row["account_id"],
        "customer_id": row["account_id"],
        "username": row["username"],
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "workspace_id": row["workspace_id"] or "default",
        "account_status": row["account_status"],
        "login_status": row["login_status"] if row["login_status"] is not None else "offline",
        "workspace_code": row["workspace_code"] or "",
        "workspace_name": row["workspace_name"] or "",
        "workspace": {"code": row["workspace_code"] or "", "name": row["workspace_name"] or ""},
    }


def _required_account(database_path: Path, authorization: str | None) -> dict[str, Any]:
    token = _bearer_token(authorization)
    account = _account_by_token(database_path, token)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    if str(account.get("account_status") or "").lower() not in {"active", ""}:
        raise HTTPException(status_code=403, detail="customer account is not active")
    return account


def _require_billing_admin(database_path: Path, authorization: str | None) -> dict[str, Any]:
    account = _required_account(database_path, authorization)
    if str(account.get("role") or "").lower() not in {"admin", "owner"}:
        raise HTTPException(status_code=403, detail="billing admin role required")
    return account


def _billing_actor(account: dict[str, Any]) -> Actor:
    return Actor(
        id=str(account["account_id"]),
        username=str(account.get("username") or account["account_id"]),
        role=str(account.get("role") or "operator"),
        workspace_id=str(account.get("workspace_id") or "default"),
        workspace_code=str(account.get("workspace_code") or "default"),
    )


def _ensure_usage_owner(database_path: Path, usage_id: str, account_id: str) -> None:
    with transaction(database_path) as conn:
        row = conn.execute(
            "SELECT account_id FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    if row is None or str(row["account_id"]) != account_id:
        raise HTTPException(status_code=404, detail="usage event not found")


def _usage_feature(database_path: Path, usage_id: str) -> str:
    with transaction(database_path) as conn:
        row = conn.execute(
            "SELECT feature_key FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="usage event not found")
    return str(row["feature_key"])


def _fixed_usage_provider(feature_key: str) -> tuple[str, str]:
    return (
        ("wuyin", "image_gpt")
        if feature_key == "product_processing.image_grid_2k"
        else ("aicoming", TEXT_MODEL)
    )


def _gateway_request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_reserved_usage(
    database_path: Path,
    *,
    usage_id: str,
    account_id: str,
    feature_key: str,
) -> None:
    """Perform the cheap authorization check before parsing attacker payloads.

    The atomic claim repeats every check below while holding BEGIN IMMEDIATE;
    this first pass is only an ordering/security gate and is not trusted for
    concurrency correctness.
    """
    if not usage_id:
        raise HTTPException(status_code=400, detail="usage_id is required")
    with transaction(database_path) as conn:
        usage = conn.execute(
            "SELECT account_id, feature_key, status FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    if usage is None or str(usage["account_id"]) != account_id:
        raise HTTPException(status_code=404, detail="usage event not found")
    if str(usage["feature_key"]) != feature_key:
        raise HTTPException(status_code=400, detail="usage feature does not match operation")
    if str(usage["status"]) != "reserved":
        raise HTTPException(status_code=409, detail="usage event is not reserved")


def _claim_gateway_request(
    database_path: Path,
    *,
    usage_id: str,
    account_id: str,
    feature_key: str,
    request_hash: str,
) -> _GatewayRequestClaim:
    if not usage_id:
        raise HTTPException(status_code=400, detail="usage_id is required")
    with transaction(database_path) as conn:
        usage = conn.execute(
            "SELECT account_id, feature_key, status FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if usage is None or str(usage["account_id"]) != account_id:
            raise HTTPException(status_code=404, detail="usage event not found")
        if str(usage["feature_key"]) != feature_key:
            raise HTTPException(status_code=400, detail="usage feature does not match operation")
        if str(usage["status"]) != "reserved":
            raise HTTPException(status_code=409, detail="usage event is not reserved")

        if feature_key == "product_processing.image_grid_2k":
            # A submit without a durable provider task is uncertain for the
            # whole reserved usage, not merely for one request hash.  Check it
            # before exact-hash lookup and distinct-slot accounting so changing
            # prompt/size can never cause duplicate provider egress.
            uncertain = conn.execute(
                """
                SELECT request_hash
                FROM billing_ai_gateway_requests
                WHERE usage_id = ? AND feature_key = ?
                  AND phase = 'submit_uncertain' AND provider_task_id = ''
                LIMIT 1
                """,
                (usage_id, feature_key),
            ).fetchone()
            if uncertain is not None:
                return _GatewayRequestClaim(submit_uncertain=True)
            submitting = conn.execute(
                """
                SELECT request_hash, status, response_json, attempt_count,
                       lease_expires_at, phase, provider_task_id, updated_at
                FROM billing_ai_gateway_requests
                WHERE usage_id = ? AND feature_key = ? AND status = 'in_progress'
                  AND phase = 'submitting' AND provider_task_id = ''
                LIMIT 1
                """,
                (usage_id, feature_key),
            ).fetchone()
            if submitting is not None:
                if _gateway_claim_is_fresh(submitting, feature_key):
                    detail = (
                        "identical gateway request is already in progress"
                        if str(submitting["request_hash"]) == request_hash
                        else "image submit is already in progress"
                    )
                    raise HTTPException(status_code=409, detail=detail)
                conn.execute(
                    """
                    UPDATE billing_ai_gateway_requests
                    SET status = 'failed', phase = 'submit_uncertain',
                        lease_expires_at = '', response_json = '', updated_at = ?
                    WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
                      AND phase = 'submitting' AND provider_task_id = ''
                    """,
                    (_utc_now(), usage_id, str(submitting["request_hash"])),
                )
                return _GatewayRequestClaim(submit_uncertain=True)

        existing = conn.execute(
            """
            SELECT status, response_json, attempt_count, lease_expires_at,
                   phase, provider_task_id, updated_at
            FROM billing_ai_gateway_requests
            WHERE usage_id = ? AND request_hash = ?
            """,
            (usage_id, request_hash),
        ).fetchone()
        if existing is not None:
            status = str(existing["status"])
            if status == "succeeded":
                try:
                    cached = json.loads(str(existing["response_json"] or ""))
                except (TypeError, json.JSONDecodeError) as exc:
                    raise HTTPException(status_code=503, detail="cached gateway response is unavailable") from exc
                if not isinstance(cached, dict):
                    raise HTTPException(status_code=503, detail="cached gateway response is unavailable")
                return _GatewayRequestClaim(cached_response=cached)
            if status == "in_progress":
                if _gateway_claim_is_fresh(existing, feature_key):
                    raise HTTPException(status_code=409, detail="identical gateway request is already in progress")
                provider_task_id = str(existing["provider_task_id"] or "").strip()
                lease_expires_at = _new_gateway_lease(feature_key)
                if provider_task_id:
                    conn.execute(
                        """
                        UPDATE billing_ai_gateway_requests
                        SET lease_expires_at = ?, phase = 'polling', updated_at = ?
                        WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
                        """,
                        (lease_expires_at, _utc_now(), usage_id, request_hash),
                    )
                    return _GatewayRequestClaim(provider_task_id=provider_task_id)
                attempt_limit = GATEWAY_SAME_REQUEST_ATTEMPT_LIMITS[feature_key]
                if int(existing["attempt_count"] or 0) >= attempt_limit:
                    raise HTTPException(status_code=409, detail="gateway retry limit reached for reserved usage")
                conn.execute(
                    """
                    UPDATE billing_ai_gateway_requests
                    SET lease_expires_at = ?, phase = 'claimed', response_json = '',
                        attempt_count = attempt_count + 1, updated_at = ?
                    WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
                    """,
                    (lease_expires_at, _utc_now(), usage_id, request_hash),
                )
                return _GatewayRequestClaim()
            attempt_limit = GATEWAY_SAME_REQUEST_ATTEMPT_LIMITS[feature_key]
            if int(existing["attempt_count"] or 0) >= attempt_limit:
                raise HTTPException(status_code=409, detail="gateway retry limit reached for reserved usage")
            conn.execute(
                """
                UPDATE billing_ai_gateway_requests
                SET status = 'in_progress', response_json = '',
                    attempt_count = attempt_count + 1, lease_expires_at = ?,
                    phase = 'claimed', provider_task_id = '', updated_at = ?
                WHERE usage_id = ? AND request_hash = ? AND status = 'failed'
                """,
                (_new_gateway_lease(feature_key), _utc_now(), usage_id, request_hash),
            )
            return _GatewayRequestClaim()

        row = conn.execute(
            "SELECT COUNT(*) AS count FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        limit = GATEWAY_DISTINCT_REQUEST_LIMITS[feature_key]
        if int(row["count"] if row else 0) >= limit:
            raise HTTPException(status_code=409, detail="gateway request limit reached for reserved usage")
        conn.execute(
            """
            INSERT INTO billing_ai_gateway_requests (
                usage_id, request_hash, account_id, feature_key, status,
                lease_expires_at, phase
            ) VALUES (?, ?, ?, ?, 'in_progress', ?, 'claimed')
            """,
            (
                usage_id,
                request_hash,
                account_id,
                feature_key,
                _new_gateway_lease(feature_key),
            ),
        )
    return _GatewayRequestClaim()


def _new_gateway_lease(feature_key: str) -> str:
    seconds = GATEWAY_LEASE_SECONDS[feature_key]
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _gateway_claim_is_fresh(row: Any, feature_key: str) -> bool:
    raw_expiry = str(row["lease_expires_at"] or "").strip()
    if raw_expiry:
        try:
            expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            return expiry > datetime.now(timezone.utc)
        except ValueError:
            return False
    try:
        updated = datetime.fromisoformat(str(row["updated_at"] or "").replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return updated + timedelta(seconds=GATEWAY_LEASE_SECONDS[feature_key]) > datetime.now(timezone.utc)


def _complete_gateway_request(
    database_path: Path,
    usage_id: str,
    request_hash: str,
    response_payload: dict[str, Any],
) -> None:
    encoded = json.dumps(
        response_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAX_GATEWAY_RESPONSE_BYTES:
        raise HTTPException(status_code=502, detail="server provider response is too large")
    with transaction(database_path) as conn:
        cursor = conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET status = 'succeeded', response_json = ?, phase = 'completed',
                lease_expires_at = '', updated_at = ?
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
            """,
            (encoded, _utc_now(), usage_id, request_hash),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="gateway request claim is no longer active")


def _record_gateway_provider_task(
    database_path: Path,
    usage_id: str,
    request_hash: str,
    provider_task_id: str,
) -> None:
    task_id = str(provider_task_id or "").strip()
    if not task_id:
        raise HTTPException(status_code=502, detail="server image provider returned an invalid task")
    with transaction(database_path) as conn:
        cursor = conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET provider_task_id = ?, phase = 'polling', lease_expires_at = ?, updated_at = ?
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
            """,
            (
                task_id[:240],
                _new_gateway_lease("product_processing.image_grid_2k"),
                _utc_now(),
                usage_id,
                request_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="gateway request claim is no longer active")


def _mark_gateway_submitting(
    database_path: Path,
    usage_id: str,
    request_hash: str,
) -> None:
    with transaction(database_path) as conn:
        cursor = conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET phase = 'submitting', lease_expires_at = ?, updated_at = ?
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
              AND provider_task_id = ''
            """,
            (
                _new_gateway_lease("product_processing.image_grid_2k"),
                _utc_now(),
                usage_id,
                request_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="gateway request claim is no longer active")


def _mark_gateway_submit_uncertain(
    database_path: Path,
    usage_id: str,
    request_hash: str,
) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET status = 'failed', phase = 'submit_uncertain', response_json = '',
                lease_expires_at = '', updated_at = ?
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
              AND provider_task_id = ''
            """,
            (_utc_now(), usage_id, request_hash),
        )


def _gateway_provider_task_id(database_path: Path, usage_id: str, request_hash: str) -> str:
    with transaction(database_path) as conn:
        row = conn.execute(
            """
            SELECT provider_task_id FROM billing_ai_gateway_requests
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
            """,
            (usage_id, request_hash),
        ).fetchone()
    return str(row["provider_task_id"] or "").strip() if row is not None else ""


def _pause_gateway_request(database_path: Path, usage_id: str, request_hash: str) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET lease_expires_at = ?, phase = 'polling', updated_at = ?
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
            """,
            (_utc_now(), _utc_now(), usage_id, request_hash),
        )


def _fail_gateway_request(
    database_path: Path,
    usage_id: str,
    request_hash: str,
    *,
    phase: str = "failed",
    clear_provider_task: bool = False,
) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET status = 'failed', response_json = '', phase = ?,
                lease_expires_at = '',
                provider_task_id = CASE WHEN ? THEN '' ELSE provider_task_id END,
                updated_at = ?
            WHERE usage_id = ? AND request_hash = ? AND status = 'in_progress'
            """,
            (phase[:64], int(clear_provider_task), _utc_now(), usage_id, request_hash),
        )


def _sanitized_gateway_response(
    value: Any,
    *,
    secrets: tuple[str, ...] = (),
    depth: int = 0,
) -> Any:
    if depth > 12:
        raise HTTPException(status_code=502, detail="server provider response is too deeply nested")
    blocked_keys = {"authorization", "api_key", "access_token", "refresh_token", "password", "secret", "key"}
    if isinstance(value, dict):
        return {
            str(key): _sanitized_gateway_response(item, secrets=secrets, depth=depth + 1)
            for key, item in value.items()
            if str(key).strip().lower() not in blocked_keys
        }
    if isinstance(value, list):
        return [
            _sanitized_gateway_response(item, secrets=secrets, depth=depth + 1)
            for item in value[:10_000]
        ]
    if isinstance(value, str):
        sanitized = value
        for secret in secrets:
            if secret:
                sanitized = sanitized.replace(secret, "[redacted]")
        return sanitized
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise HTTPException(status_code=502, detail="server provider response contains unsupported data")


def _validated_chat_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise HTTPException(status_code=400, detail="chat messages are invalid")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_CHAT_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="chat request is too large")
    messages: list[dict[str, Any]] = []
    for message in value:
        if (
            not isinstance(message, dict)
            or str(message.get("role") or "") not in {"system", "user", "assistant"}
        ):
            raise HTTPException(status_code=400, detail="chat message is invalid")
        content = message.get("content")
        if isinstance(content, str):
            validated_content: str | list[dict[str, Any]] = content
        elif isinstance(content, list):
            validated_content = _validated_chat_content_parts(content)
        else:
            raise HTTPException(status_code=400, detail="chat message content is invalid")
        messages.append({"role": str(message["role"]), "content": validated_content})
    return messages


def _validated_chat_content_parts(value: list[Any]) -> list[dict[str, Any]]:
    if not 1 <= len(value) <= MAX_CHAT_CONTENT_PARTS:
        raise HTTPException(status_code=400, detail="chat content parts are invalid")
    parts: list[dict[str, Any]] = []
    for part in value:
        if not isinstance(part, dict):
            raise HTTPException(status_code=400, detail="chat content part is invalid")
        part_type = str(part.get("type") or "")
        if part_type == "text":
            if set(part) != {"type", "text"} or not isinstance(part.get("text"), str):
                raise HTTPException(status_code=400, detail="chat text part is invalid")
            parts.append({"type": "text", "text": str(part["text"])})
            continue
        if part_type != "image_url" or set(part) != {"type", "image_url"}:
            raise HTTPException(status_code=400, detail="chat content part type is unsupported")
        image = part.get("image_url")
        if not isinstance(image, dict) or not set(image).issubset({"url", "detail"}) or "url" not in image:
            raise HTTPException(status_code=400, detail="chat image part is invalid")
        url = str(image.get("url") or "").strip()
        detail = str(image.get("detail") or "").strip().lower()
        if detail and detail not in {"auto", "low", "high"}:
            raise HTTPException(status_code=400, detail="chat image detail is invalid")
        if url.startswith("data:"):
            _validate_chat_data_image(url)
        elif not _safe_provider_image_url(url):
            raise HTTPException(status_code=400, detail="chat image URL is invalid")
        normalized_image = {"url": url}
        if detail:
            normalized_image["detail"] = detail
        parts.append({"type": "image_url", "image_url": normalized_image})
    return parts


def _validate_chat_data_image(value: str) -> None:
    try:
        header, encoded = value.split(",", 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="chat data image is invalid") from exc
    if header.lower() not in {
        "data:image/jpeg;base64",
        "data:image/jpg;base64",
        "data:image/png;base64",
        "data:image/webp;base64",
    }:
        raise HTTPException(status_code=400, detail="chat data image type is unsupported")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="chat data image is invalid") from exc
    if not decoded or len(decoded) > MAX_CHAT_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="chat data image is too large")


def _safe_provider_image_url(value: str) -> bool:
    normalized = str(value or "").strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return False
    return (
        len(normalized) <= 2048
        and parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and is_safe_external_url(normalized)
    )


def _trusted_provider_reference_url(value: str) -> bool:
    """Forward references only for explicitly trusted platform/COS hostnames."""

    normalized = str(value or "").strip()
    try:
        hostname = str(urlsplit(normalized).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return False
    entries = [
        item.strip().lower().rstrip(".")
        for item in str(os.environ.get("WH_AI_REFERENCE_HOST_ALLOWLIST") or "").split(",")
        if item.strip()
    ]
    if not hostname or not entries:
        return False
    allowed = any(
        (
            hostname.endswith(f".{entry[2:]}")
            and hostname != entry[2:]
        )
        if entry.startswith("*.") and len(entry) > 2
        else hostname == entry
        for entry in entries
    )
    return allowed and _safe_provider_image_url(normalized)


def _bounded_provider_json(response: requests.Response, *, provider: str) -> Any:
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            raw = bytes(chunk)
            total += len(raw)
            if total > MAX_GATEWAY_RESPONSE_BYTES:
                raise HTTPException(status_code=502, detail="server provider response is too large")
            chunks.append(raw)
        return json.loads(b"".join(chunks).decode("utf-8"))
    except HTTPException:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"server {provider} provider returned invalid JSON",
        ) from exc


def _server_text_chat(api_key: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not _TEXT_GATEWAY_SEMAPHORE.acquire(timeout=300):
        raise HTTPException(status_code=503, detail="server text request queue timed out")
    try:
        response: requests.Response | None = None
        try:
            response = requests.post(
                TEXT_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": TEXT_MODEL, "messages": messages, "temperature": 0.7},
                timeout=240,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(response.status_code)
            decoded = (
                _bounded_provider_json(response, provider="text")
                if 200 <= status_code < 300
                else None
            )
        except requests.RequestException as exc:
            raise HTTPException(status_code=503, detail="server text request failed") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="server text provider returned invalid JSON") from exc
        finally:
            if response is not None:
                response.close()
        if not 200 <= status_code < 300:
            if 300 <= status_code < 400:
                raise HTTPException(status_code=502, detail="server text provider redirected the request")
            if status_code == 429 or status_code >= 500:
                raise HTTPException(status_code=503, detail="server text provider is temporarily unavailable")
            raise HTTPException(status_code=status_code, detail="server text provider rejected the request")
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=502, detail="server text provider returned an invalid response")
        return decoded
    finally:
        _TEXT_GATEWAY_SEMAPHORE.release()


def _server_image_request(
    api_key: str,
    prompt: str,
    urls: list[str],
    size: str,
) -> dict[str, Any]:
    task_id = _submit_server_wuyin(api_key, prompt, urls, size)
    return {
        "ok": True,
        "task_id": task_id,
        "result_url": _poll_server_wuyin(api_key, task_id),
    }


def _submit_server_wuyin(
    api_key: str,
    prompt: str,
    urls: list[str],
    size: str,
) -> str:
    response: requests.Response | None = None
    try:
        response = requests.post(
            WUYIN_IMAGE_SUBMIT_URL,
            params={"key": api_key},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json={"prompt": prompt, "size": size, **({"urls": urls} if urls else {})},
            timeout=35,
            allow_redirects=False,
            stream=True,
        )
        status_code = int(response.status_code)
        if status_code == 429 or status_code >= 500:
            raise HTTPException(status_code=503, detail="server image provider is temporarily unavailable")
        if not 200 <= status_code < 300:
            raise HTTPException(status_code=502, detail="server image provider rejected the request")
        try:
            submitted = _bounded_provider_json(response, provider="image")
        except HTTPException as exc:
            raise _ImageSubmitUncertain() from exc
    except requests.RequestException as exc:
        raise _ImageSubmitUncertain() from exc
    finally:
        if response is not None:
            response.close()
    if not isinstance(submitted, dict):
        raise _ImageSubmitUncertain()
    try:
        provider_code = _provider_code(submitted.get("code"))
    except HTTPException as exc:
        raise _ImageSubmitUncertain() from exc
    if provider_code != 200:
        raise HTTPException(status_code=502, detail="server image provider rejected the request")
    data = submitted.get("data")
    task_id = str(data.get("id") or "").strip() if isinstance(data, dict) else ""
    if not task_id:
        raise _ImageSubmitUncertain()
    return task_id


def _first_provider_image_url(value: Any, *, depth: int = 0) -> str:
    if depth > 12:
        raise HTTPException(status_code=502, detail="server image provider response is too deeply nested")
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.lower().startswith(("http://", "https://")):
            if not _safe_provider_image_url(candidate):
                raise HTTPException(status_code=502, detail="server image provider returned an unsafe URL")
            return candidate
        return ""
    if isinstance(value, dict):
        for key in (
            "url", "image_url", "image", "src", "href", "result", "results",
            "images", "urls", "output", "outputs",
        ):
            found = _first_provider_image_url(value.get(key), depth=depth + 1)
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value[:10_000]:
            found = _first_provider_image_url(item, depth=depth + 1)
            if found:
                return found
    return ""


def _poll_server_wuyin(api_key: str, task_id: str) -> str:
    deadline = time.monotonic() + 620
    while time.monotonic() < deadline:
        response: requests.Response | None = None
        try:
            response = requests.get(
                WUYIN_IMAGE_DETAIL_URL,
                params={"key": api_key, "id": task_id},
                headers={"Authorization": api_key},
                timeout=35,
                allow_redirects=False,
                stream=True,
            )
            status_code = int(response.status_code)
            if status_code == 429 or status_code >= 500:
                raise HTTPException(status_code=503, detail="server image provider is temporarily unavailable")
            if not 200 <= status_code < 300:
                raise HTTPException(status_code=502, detail="server image provider rejected the poll request")
            payload = _bounded_provider_json(response, provider="image")
        except requests.RequestException as exc:
            raise HTTPException(status_code=503, detail="server image poll request failed") from exc
        finally:
            if response is not None:
                response.close()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="server image provider returned an invalid response")
        if _provider_code(payload.get("code")) != 200:
            raise _ImageProviderTerminalFailure()
        data = payload.get("data") or {}
        result_url = _first_provider_image_url(data) or _first_provider_image_url(payload)
        if result_url:
            return result_url
        status = (
            str(data.get("status") or payload.get("status") or "").lower()
            if isinstance(data, dict)
            else ""
        )
        if status in {"3", "4", "5", "fail", "failed", "error", "cancelled", "canceled"}:
            raise _ImageProviderTerminalFailure()
        time.sleep(3)
    raise HTTPException(status_code=504, detail="server image provider timed out")


def _provider_code(value: Any) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=502, detail="server image provider returned an invalid code")
    try:
        normalized = str(value).strip()
        if not normalized or any(character not in "0123456789" for character in normalized):
            raise ValueError
        return int(normalized)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="server image provider returned an invalid code") from exc


def _safe_int(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 100_000_000))
    except (TypeError, ValueError):
        return 0


def _safe_billing_metadata(value: dict[str, Any]) -> dict[str, Any]:
    remaining = [256]
    dropped = object()

    def sensitive_key(key: Any) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
        return (
            not normalized
            or "authorization" in normalized
            or "credential" in normalized
            or "cookie" in normalized
            or "session" in normalized
            or normalized == "apikey"
            or normalized.endswith(("token", "secret", "password", "apikey"))
        )

    def sanitize(item: Any, *, depth: int) -> Any:
        if remaining[0] <= 0 or depth > 6:
            return dropped
        remaining[0] -= 1
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, str):
            return item[:500]
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, nested in list(item.items())[:64]:
                if sensitive_key(key):
                    continue
                safe = sanitize(nested, depth=depth + 1)
                if safe is not dropped:
                    result[str(key)[:64]] = safe
            return result
        if isinstance(item, (list, tuple)):
            result_list: list[Any] = []
            for nested in list(item)[:64]:
                safe = sanitize(nested, depth=depth + 1)
                if safe is not dropped:
                    result_list.append(safe)
            return result_list
        return dropped

    sanitized = sanitize(value, depth=0)
    return sanitized if isinstance(sanitized, dict) else {}


def _billing_summary(database_path: Path, account: dict[str, Any]) -> dict[str, Any]:
    account_id = str(account["account_id"])
    workspace_id = str(account.get("workspace_id") or "default")
    pricing = active_pricing(database_path)
    with transaction(database_path) as conn:
        _ensure_wallet(conn, account_id, workspace_id)
        wallet = conn.execute(
            """
            SELECT points_balance, locked_points, manual_frozen_points, version, ledger_head_hash, updated_at
            FROM billing_wallets
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        ledgers = conn.execute(
            """
            SELECT entry_id, direction, points_delta, balance_after, source_type, source_id, created_at
            FROM billing_point_ledger
            WHERE account_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (account_id,),
        ).fetchall()
        orders = conn.execute(
            """
            SELECT order_id, out_trade_no, provider, package_id, amount_cents, currency,
                   points, status, created_at, paid_at, expires_at
            FROM billing_payment_orders
            WHERE account_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (account_id,),
        ).fetchall()
    return {
        "ok": True,
        "account": {
            "account_id": account_id,
            "username": account.get("username", ""),
            "workspace_id": workspace_id,
            "workspace_code": account.get("workspace_code", ""),
        },
        "wallet": {
            "points_balance": _display_billing_points(int(wallet["points_balance"] if wallet else 0), pricing),
            "locked_points": _display_billing_points(int(wallet["locked_points"] if wallet else 0), pricing),
            "manual_frozen_points": _display_billing_points(int(wallet["manual_frozen_points"] if wallet else 0), pricing),
            "frozen_points": _display_billing_points(int((wallet["locked_points"] if wallet else 0) + (wallet["manual_frozen_points"] if wallet else 0)), pricing),
            "available_points": _display_billing_points(int((wallet["points_balance"] if wallet else 0) - (wallet["locked_points"] if wallet else 0) - (wallet["manual_frozen_points"] if wallet else 0)), pricing),
            "version": int(wallet["version"] if wallet else 0),
            "ledger_head_hash": wallet["ledger_head_hash"] if wallet else "",
            "updated_at": wallet["updated_at"] if wallet else "",
        },
        "pricing": pricing,
        "topup_products": _topup_products(pricing),
        "recent_ledger": [_display_ledger_row(dict(row), pricing) for row in ledgers],
        "recent_orders": [
            {**dict(row), "points": _display_billing_points(int(row["points"]), pricing)}
            for row in orders
        ],
        "security": {
            "server_authoritative": True,
            "local_balance_trusted": False,
            "ledger_hash_chain": True,
            "settlement_requires_signed_provider_callback": True,
        },
    }


def _display_billing_points(units: int, pricing: dict[str, Any]) -> int | float:
    scale = int(pricing.get("point_unit_scale") or 10)
    value = int(units) / scale
    return int(value) if value.is_integer() else value


def _topup_products(pricing: dict[str, Any]) -> list[dict[str, Any]]:
    points_per_cny = int(pricing["points_per_cny"])
    scale = int(pricing["point_unit_scale"])
    return [
        {
            "package_id": package_id,
            "label": item["label"],
            "amount_cents": item["amount_cents"],
            "points": _display_billing_points(
                (int(item["amount_cents"]) // 100) * points_per_cny * scale,
                pricing,
            ),
        }
        for package_id, item in TOPUP_PACKAGE_CENTS.items()
    ]


def _display_ledger_row(row: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
    row["points_delta"] = _display_billing_points(int(row.get("points_delta") or 0), pricing)
    row["balance_after"] = _display_billing_points(int(row.get("balance_after") or 0), pricing)
    return row


def _create_topup_order(database_path: Path, account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    account_id = str(account["account_id"])
    workspace_id = str(account.get("workspace_id") or "default")
    provider = str(payload.get("provider") or "").strip().lower()
    package_id = str(payload.get("package_id") or "").strip()
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if provider not in PAYMENT_PROVIDERS:
        raise HTTPException(status_code=400, detail="provider must be wechat or alipay")
    if package_id != "custom" and package_id not in TOPUP_PACKAGE_CENTS:
        raise HTTPException(status_code=400, detail="unknown topup package")
    if not 16 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=400, detail="idempotency_key is required")

    pricing = active_pricing(database_path)
    if package_id == "custom":
        raw_amount_cents = payload.get("amount_cents")
        if isinstance(raw_amount_cents, bool):
            raise HTTPException(status_code=400, detail="custom amount is invalid")
        try:
            amount_cents = int(raw_amount_cents)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="custom amount is required") from exc
        if (
            amount_cents < CUSTOM_TOPUP_MIN_CENTS
            or amount_cents > CUSTOM_TOPUP_MAX_CENTS
            or amount_cents % 100 != 0
        ):
            raise HTTPException(
                status_code=400,
                detail="custom amount must be a whole amount from 1 to 3000 CNY",
            )
        product = {"amount_cents": amount_cents, "label": "自定义积分充值"}
    else:
        product = TOPUP_PACKAGE_CENTS[package_id]
    # Payment orders store raw 0.1-point units; user-facing responses always
    # convert through the active pricing rule.
    product_points = (
        (int(product["amount_cents"]) // 100)
        * int(pricing["points_per_cny"])
        * int(pricing["point_unit_scale"])
    )
    now = _utc_now()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    request_hash = _stable_json_hash(
        {
            "account_id": account_id,
            "workspace_id": workspace_id,
            "provider": provider,
            "package_id": package_id,
            "amount_cents": product["amount_cents"],
            "points": product_points,
            "idempotency_key": idempotency_key,
        }
    )
    with transaction(database_path) as conn:
        _ensure_wallet(conn, account_id, workspace_id)
        existing = conn.execute(
            """
            SELECT order_id, out_trade_no, provider, package_id, amount_cents, currency,
                   points, status, created_at, paid_at, expires_at
            FROM billing_payment_orders
            WHERE account_id = ? AND idempotency_key = ?
            """,
            (account_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return _topup_order_response(dict(existing), reused=True, pricing=pricing)
        order_id = f"billord_{secrets.token_urlsafe(18)}"
        out_trade_no = f"MP{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{secrets.token_hex(8)}"
        conn.execute(
            """
            INSERT INTO billing_payment_orders (
                order_id, out_trade_no, account_id, workspace_id, provider, package_id,
                amount_cents, currency, points, status, idempotency_key, request_hash,
                expires_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'CNY', ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                out_trade_no,
                account_id,
                workspace_id,
                provider,
                package_id,
                product["amount_cents"],
                product_points,
                idempotency_key,
                request_hash,
                expires_at,
                now,
                now,
            ),
        )
        order = conn.execute(
            """
            SELECT order_id, out_trade_no, provider, package_id, amount_cents, currency,
                   points, status, created_at, paid_at, expires_at
            FROM billing_payment_orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
    return _topup_order_response(dict(order), reused=False, pricing=pricing)


def _topup_order_response(
    order: dict[str, Any],
    *,
    reused: bool,
    pricing: dict[str, Any],
) -> dict[str, Any]:
    order = dict(order)
    order["points"] = _display_billing_points(int(order.get("points") or 0), pricing)
    payment = {
        "provider": order["provider"],
        "mode": "gateway_not_configured",
        "qr_code_url": "",
        "pay_url": "",
        "message": "支付网关尚未配置。订单已在服务器生成 pending 记录，待商户参数和回调验签接入后才可收款入账。",
    }
    if order["provider"] == "alipay" and alipay_is_configured():
        payment = {
            "provider": "alipay",
            "mode": "page_pay",
            "qr_code_url": "",
            "pay_url": build_page_payment_url(
                out_trade_no=str(order["out_trade_no"]),
                amount_cents=int(order["amount_cents"]),
                subject=f"界野电商平台 {order['package_id']} 积分充值",
                expires_at=str(order["expires_at"]),
            ),
            "message": "请在浏览器中完成支付宝付款。付款成功后积分会自动到账。",
        }
    return {
        "ok": True,
        "reused": reused,
        "order": order,
        "payment": payment,
    }


def _ensure_wallet(conn: Any, account_id: str, workspace_id: str) -> None:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO billing_wallets (account_id, workspace_id, points_balance, locked_points, version, created_at, updated_at)
        VALUES (?, ?, 0, 0, 0, ?, ?)
        ON CONFLICT(account_id) DO NOTHING
        """,
        (account_id, workspace_id, now, now),
    )


def _stable_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ledger_row_hash(secret: bytes, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(secret, encoded, hashlib.sha256).hexdigest()


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _invitation_code() -> str:
    """Generate a human-friendly invitation code, e.g. MAINPG-8F3K-2Q7M."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉易混淆的 I/O/0/1
    def _chunk(size: int) -> str:
        return "".join(secrets.choice(alphabet) for _ in range(size))
    return f"MAINPG-{_chunk(4)}-{_chunk(4)}"


# ---- 采集凭据（OneBound API key/secret）只在服务器持有 ----

def _server_rsa_private_key_pem() -> str:
    """Load the server's RSA private key used to unwrap collect-key sessions."""
    env = os.environ.get("WH_AUTH_RSA_PRIVATE_KEY", "").strip()
    if env:
        return env
    path = os.environ.get("WH_AUTH_RSA_PRIVATE_KEY_PATH", "/opt/wh-workbench/data/rsa_private.pem")
    return Path(path).read_text(encoding="utf-8")


def _rsa_decrypt_session_key(encrypted_session_key_b64: str) -> bytes:
    """Decrypt the client's one-time AES session key using the server RSA key."""
    pem = _server_rsa_private_key_pem().encode("utf-8")
    private_key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("configured collect-key private key is not RSA")
    encrypted = base64.b64decode(encrypted_session_key_b64)
    return private_key.decrypt(
        encrypted,
        asymmetric_padding.OAEP(
            mgf=asymmetric_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def _server_onebound_config() -> dict[str, str]:
    """Resolve OneBound credentials from server environment/config file."""
    env_key = os.environ.get("SERVER_ONEBOUND_API_KEY", "").strip()
    env_secret = os.environ.get("SERVER_ONEBOUND_API_SECRET", "").strip()
    if env_key and env_secret:
        return {
            "api_key": env_key,
            "api_secret": env_secret,
            "base_url": os.environ.get(
                "SERVER_ONEBOUND_BASE_URL", "https://api-gw.onebound.cn/1688"
            ),
        }
    # 配置文件兜底：与本地工作台相同位置，但只在服务器部署时存在。
    candidates = [
        Path(__file__).with_name("onebound.local.json"),
        Path("/opt/wh-workbench/MainPG/local-runtime/wh_local/onebound.local.json"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        api_key = str(data.get("api_key") or "").strip()
        api_secret = str(data.get("api_secret") or "").strip()
        if api_key and api_secret:
            return {
                "api_key": api_key,
                "api_secret": api_secret,
                "base_url": str(data.get("base_url") or "https://api-gw.onebound.cn/1688"),
            }
    return {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, CustomerAuthUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import FastAPI, Header, HTTPException, Request

from ..config import default_config
from ..db import init_db, transaction
from ..billing import reserve_ai_usage, settle_ai_usage_failure, settle_ai_usage_success
from ..session import Actor
from .auth_service import SQLiteCustomerAuthService
from .credential_vault import CredentialVaultError, active_secret, enabled_secrets
from .contracts import CustomerAuthActionResult, CustomerAuthResult, CustomerAuthUnavailable
from .email_sender import TencentCloudSESEmailSender


REMOTE_SESSION_TTL = timedelta(hours=12)
BILLING_POINT_RATIO = 100
BILLING_TOPUP_PRODUCTS = {
    "points_10": {"amount_cents": 1000, "points": 1000, "label": "10 元积分包"},
    "points_30": {"amount_cents": 3000, "points": 3000, "label": "30 元积分包"},
    "points_100": {"amount_cents": 10000, "points": 10000, "label": "100 元积分包"},
}
PAYMENT_PROVIDERS = {"wechat", "alipay"}
TEXT_CHAT_URL = "https://api.aicoming.top/v1/chat/completions"
TEXT_MODEL = "gpt-5.6-terra"
WUYIN_IMAGE_SUBMIT_URL = "https://api.wuyinkeji.com/api/async/image_gpt"
WUYIN_IMAGE_DETAIL_URL = "https://api.wuyinkeji.com/api/async/detail"
TEXT_GATEWAY_CONCURRENCY = 200
_TEXT_GATEWAY_SEMAPHORE = threading.BoundedSemaphore(TEXT_GATEWAY_CONCURRENCY)


class _TextCredentialPool:
    """Server-owned, least-loaded pool for text provider credentials.

    The pool is intentionally in the auth service: desktop clients never receive
    an upstream key or decide which one to use.  A 429 cools only the affected
    credential; other enabled keys continue serving requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, dict[str, float | int]] = {}

    def acquire(self) -> tuple[dict[str, Any] | None, int]:
        try:
            credentials = enabled_secrets("text")
        except CredentialVaultError:
            raise
        if not credentials:
            legacy = str(os.environ.get("WH_TEXT_API_KEY") or "").strip()
            credentials = (
                [{"credential_id": "legacy_environment", "secret": legacy, "max_concurrency": 40, "active": True}]
                if legacy
                else []
            )
        now = time.monotonic()
        with self._lock:
            current_ids = {str(item["credential_id"]) for item in credentials}
            self._states = {
                key: value for key, value in self._states.items() if key in current_ids
            }
            candidates: list[tuple[float, float, dict[str, Any]]] = []
            retry_after: list[float] = []
            for credential in credentials:
                credential_id = str(credential["credential_id"])
                state = self._states.setdefault(
                    credential_id,
                    {"in_flight": 0, "cooldown_until": 0.0, "last_selected": 0.0, "rate_failures": 0},
                )
                cooldown_until = float(state["cooldown_until"])
                if cooldown_until > now:
                    retry_after.append(cooldown_until - now)
                    continue
                limit = max(1, int(credential.get("max_concurrency") or 40))
                in_flight = int(state["in_flight"])
                if in_flight >= limit:
                    retry_after.append(1.0)
                    continue
                candidates.append((in_flight / limit, float(state["last_selected"]), credential))
            if not candidates:
                return None, max(1, int(min(retry_after) if retry_after else 1))
            _, _, selected = min(candidates, key=lambda value: (value[0], value[1]))
            selected_state = self._states[str(selected["credential_id"])]
            selected_state["in_flight"] = int(selected_state["in_flight"]) + 1
            selected_state["last_selected"] = now
            return selected, 0

    def release(self, credential_id: str, *, outcome: str) -> None:
        now = time.monotonic()
        with self._lock:
            state = self._states.get(credential_id)
            if state is None:
                return
            state["in_flight"] = max(0, int(state["in_flight"]) - 1)
            if outcome == "rate_limited":
                failures = min(4, int(state["rate_failures"]) + 1)
                state["rate_failures"] = failures
                state["cooldown_until"] = now + min(300.0, 60.0 * (2 ** (failures - 1)))
            elif outcome == "transient":
                state["cooldown_until"] = max(float(state["cooldown_until"]), now + 15.0)
            elif outcome == "success":
                state["rate_failures"] = 0

    def status(self) -> dict[str, int]:
        try:
            credentials = enabled_secrets("text")
        except CredentialVaultError:
            return {"configured_keys": 0, "available_keys": 0, "available_slots": 0, "retry_after_seconds": 0}
        now = time.monotonic()
        with self._lock:
            available_keys = 0
            available_slots = 0
            retry_after: list[float] = []
            for credential in credentials:
                state = self._states.get(str(credential["credential_id"]), {})
                cooldown_until = float(state.get("cooldown_until", 0.0))
                if cooldown_until > now:
                    retry_after.append(cooldown_until - now)
                    continue
                slots = max(0, int(credential.get("max_concurrency") or 40) - int(state.get("in_flight", 0)))
                if slots:
                    available_keys += 1
                    available_slots += slots
            return {
                "configured_keys": len(credentials),
                "available_keys": available_keys,
                "available_slots": available_slots,
                "retry_after_seconds": max(0, int(min(retry_after) if retry_after else 0)),
            }


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
    text_credential_pool = _TextCredentialPool()

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
        return {"ok": True, "usage": settle_ai_usage_success(
            db_path, usage_id, provider=provider, model=model,
            provider_task_id=str(payload.get("provider_task_id") or "")[:240],
            input_tokens=_safe_int(payload.get("input_tokens")),
            output_tokens=_safe_int(payload.get("output_tokens")),
            total_tokens=_safe_int(payload.get("total_tokens")),
            metadata=_safe_billing_metadata(metadata),
        )}

    @app.post("/api/customer/billing/usage/{usage_id}/fail")
    def settle_billing_usage_failure(
        usage_id: str,
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        _ensure_usage_owner(db_path, usage_id, str(account["account_id"]))
        settle_ai_usage_failure(db_path, usage_id, error_message=str(payload.get("error_message") or "AI operation failed")[:500])
        return {"ok": True, "usage_id": usage_id, "status": "failed"}

    @app.post("/api/customer/ai/chat")
    def server_managed_ai_chat(
        payload: dict[str, Any], authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        _require_reserved_usage(db_path, str(payload.get("usage_id") or ""), str(account["account_id"]), "product_processing.text")
        messages = _validated_chat_messages(payload.get("messages"))
        if str(payload.get("model") or TEXT_MODEL).strip() != TEXT_MODEL:
            raise HTTPException(status_code=400, detail="unsupported server-managed text model")
        try:
            credential, retry_after = text_credential_pool.acquire()
        except CredentialVaultError as exc:
            raise HTTPException(status_code=503, detail="server text credential vault is unavailable") from exc
        if credential is None:
            raise HTTPException(
                status_code=429,
                detail="server text credential capacity is temporarily exhausted",
                headers={"Retry-After": str(max(1, retry_after))},
            )
        credential_id = str(credential["credential_id"])
        # Persist the opaque scheduler choice before the provider call.  This is
        # how the platform can later reconcile one user usage event with one
        # server credential without ever storing or returning its plaintext.
        _record_text_credential_usage(
            db_path,
            str(payload.get("usage_id") or ""),
            credential_id,
        )
        try:
            body = _server_text_chat(str(credential["secret"]), messages)
        except HTTPException as exc:
            outcome = "rate_limited" if exc.status_code == 429 else "transient" if exc.status_code >= 500 else "rejected"
            text_credential_pool.release(credential_id, outcome=outcome)
            if exc.status_code == 429:
                raise HTTPException(
                    status_code=429,
                    detail="server text provider rate limited this credential",
                    headers={"Retry-After": "60"},
                ) from exc
            raise
        else:
            text_credential_pool.release(credential_id, outcome="success")
            return body

    @app.get("/api/customer/ai/text-capacity")
    def server_text_capacity(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _required_account(db_path, authorization)
        return {"ok": True, "text": text_credential_pool.status()}

    @app.post("/api/customer/ai/image")
    def server_managed_ai_image(
        payload: dict[str, Any], authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        account = _required_account(db_path, authorization)
        _require_reserved_usage(db_path, str(payload.get("usage_id") or ""), str(account["account_id"]), "product_processing.image_grid_2k")
        prompt = str(payload.get("prompt") or "").strip()
        urls = payload.get("urls") or []
        size = str(payload.get("size") or "1:1").strip().lower()
        if not 1 <= len(prompt) <= 24_000 or not isinstance(urls, list) or len(urls) > 4:
            raise HTTPException(status_code=400, detail="image request is invalid")
        urls = [str(value).strip() for value in urls]
        if any(not _safe_provider_image_url(value) for value in urls):
            raise HTTPException(status_code=400, detail="image reference URL is invalid")
        if size not in {"auto", "1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4", "21:9", "9:21", "1:3", "3:1", "2:1", "1:2"}:
            raise HTTPException(status_code=400, detail="image size is invalid")
        try:
            api_key = str(active_secret("image") or os.environ.get("WH_WUYIN_IMAGE_API_KEY") or "").strip()
        except CredentialVaultError as exc:
            raise HTTPException(status_code=503, detail="server image credential vault is unavailable") from exc
        if not api_key:
            raise HTTPException(status_code=503, detail="server image credential is not configured")
        try:
            response = requests.post(WUYIN_IMAGE_SUBMIT_URL, params={"key": api_key}, headers={"Authorization": api_key, "Content-Type": "application/json"}, json={"prompt": prompt, "size": size, **({"urls": urls} if urls else {})}, timeout=35, allow_redirects=False)
            submitted = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HTTPException(status_code=503, detail="server image request failed") from exc
        finally:
            if "response" in locals():
                response.close()
        data = submitted.get("data") if isinstance(submitted, dict) else None
        task_id = str(data.get("id") or "").strip() if isinstance(data, dict) else ""
        if response.status_code >= 400 or int(submitted.get("code") or 0) != 200 or not task_id:
            raise HTTPException(status_code=502, detail="server image provider rejected the request")
        return {"ok": True, "task_id": task_id, "result_url": _poll_server_wuyin(api_key, task_id)}

    @app.post("/api/customer/billing/payment-callback/{provider}")
    def billing_payment_callback(provider: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        # 真正接入微信/支付宝时，这里必须按 provider 官方规则完成：
        # 1) 平台证书/公钥验签；2) 解密资源；3) 金额、商户订单号、商户号、币种逐项比对；
        # 4) 同一 out_trade_no 幂等入账；5) 追加 hash 链账本。
        # 当前先 fail closed，避免任何未验签回调导致入账。
        raise HTTPException(
            status_code=503,
            detail=f"{provider} payment callback verification is not configured",
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
        row = conn.execute("SELECT account_id FROM billing_ai_usage_events WHERE usage_id = ?", (usage_id,)).fetchone()
    if row is None or str(row["account_id"]) != account_id:
        raise HTTPException(status_code=404, detail="usage event not found")


def _usage_feature(database_path: Path, usage_id: str) -> str:
    with transaction(database_path) as conn:
        row = conn.execute("SELECT feature_key FROM billing_ai_usage_events WHERE usage_id = ?", (usage_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="usage event not found")
    return str(row["feature_key"])


def _fixed_usage_provider(feature_key: str) -> tuple[str, str]:
    return ("wuyin", "image_gpt") if feature_key == "product_processing.image_grid_2k" else ("aicoming", TEXT_MODEL)


def _require_reserved_usage(database_path: Path, usage_id: str, account_id: str, expected_feature: str) -> None:
    if not usage_id:
        raise HTTPException(status_code=400, detail="usage_id is required")
    with transaction(database_path) as conn:
        row = conn.execute("SELECT account_id, feature_key, status FROM billing_ai_usage_events WHERE usage_id = ?", (usage_id,)).fetchone()
    if row is None or str(row["account_id"]) != account_id:
        raise HTTPException(status_code=404, detail="usage event not found")
    if str(row["feature_key"]) != expected_feature:
        raise HTTPException(status_code=400, detail="usage feature does not match operation")
    if str(row["status"]) != "reserved":
        raise HTTPException(status_code=409, detail="usage event is not reserved")


def _record_text_credential_usage(
    database_path: Path,
    usage_id: str,
    credential_id: str,
) -> None:
    """Attach only an opaque server credential id to an existing usage event."""
    with transaction(database_path) as conn:
        row = conn.execute(
            "SELECT metadata_json FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None:
            return
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["server_text_credential_id"] = credential_id
        conn.execute(
            "UPDATE billing_ai_usage_events SET metadata_json = ? WHERE usage_id = ?",
            (json.dumps(metadata, ensure_ascii=False, sort_keys=True), usage_id),
        )


def _validated_chat_messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise HTTPException(status_code=400, detail="chat messages are invalid")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="chat request is too large")
    messages: list[dict[str, Any]] = []
    for message in value:
        if not isinstance(message, dict) or str(message.get("role") or "") not in {"system", "user", "assistant"}:
            raise HTTPException(status_code=400, detail="chat message is invalid")
        content = message.get("content")
        if not isinstance(content, (str, list)):
            raise HTTPException(status_code=400, detail="chat message content is invalid")
        messages.append({"role": str(message["role"]), "content": content})
    return messages


def _safe_provider_image_url(value: str) -> bool:
    parsed = urlsplit(str(value or "").strip())
    return parsed.scheme == "https" and bool(parsed.netloc) and len(str(value)) <= 2048


def _server_text_chat(api_key: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not _TEXT_GATEWAY_SEMAPHORE.acquire(timeout=300):
        raise HTTPException(status_code=503, detail="server text request queue timed out")
    try:
        try:
            response = requests.post(TEXT_CHAT_URL, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": TEXT_MODEL, "messages": messages, "temperature": 0.7}, timeout=240, allow_redirects=False)
            status_code = int(response.status_code)
            decoded = response.json() if status_code < 400 else None
        except requests.RequestException as exc:
            raise HTTPException(status_code=503, detail="server text request failed") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="server text provider returned invalid JSON") from exc
        finally:
            if "response" in locals():
                response.close()
        if status_code >= 400:
            if status_code == 429:
                raise HTTPException(status_code=429, detail="server text provider rate limited the credential")
            if status_code >= 500:
                raise HTTPException(status_code=503, detail="server text provider is temporarily unavailable")
            raise HTTPException(status_code=status_code, detail="server text provider rejected the request")
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=502, detail="server text provider returned an invalid response")
        return decoded
    finally:
        _TEXT_GATEWAY_SEMAPHORE.release()


def _first_provider_image_url(value: Any) -> str:
    if isinstance(value, str):
        return value.strip() if _safe_provider_image_url(value) else ""
    if isinstance(value, dict):
        for key in ("url", "image_url", "image", "src", "href", "result", "results", "images", "urls", "output", "outputs"):
            found = _first_provider_image_url(value.get(key))
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_provider_image_url(item)
            if found:
                return found
    return ""


def _poll_server_wuyin(api_key: str, task_id: str) -> str:
    deadline = time.monotonic() + 620
    while time.monotonic() < deadline:
        time.sleep(3)
        try:
            response = requests.get(WUYIN_IMAGE_DETAIL_URL, params={"key": api_key, "id": task_id}, headers={"Authorization": api_key}, timeout=35, allow_redirects=False)
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue
        finally:
            if "response" in locals():
                response.close()
        if not isinstance(payload, dict):
            continue
        code = int(payload.get("code") or 0)
        if code and code != 200:
            raise HTTPException(status_code=502, detail="server image provider task failed")
        data = payload.get("data") or {}
        result_url = _first_provider_image_url(data) or _first_provider_image_url(payload)
        if result_url:
            return result_url
        status = str(data.get("status") or payload.get("status") or "").lower() if isinstance(data, dict) else ""
        if status in {"3", "4", "5", "fail", "failed", "error", "cancelled", "canceled"}:
            raise HTTPException(status_code=502, detail="server image provider task failed")
    raise HTTPException(status_code=504, detail="server image provider timed out")


def _safe_int(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), 100_000_000))
    except (TypeError, ValueError):
        return 0


def _safe_billing_metadata(value: dict[str, Any]) -> dict[str, Any]:
    blocked = {"api_key", "authorization", "token", "password", "secret"}
    return {str(key)[:64]: (str(item)[:500] if isinstance(item, str) else item) for key, item in value.items() if str(key).lower() not in blocked and isinstance(item, (str, int, float, bool, type(None)))}


def _billing_summary(database_path: Path, account: dict[str, Any]) -> dict[str, Any]:
    account_id = str(account["account_id"])
    workspace_id = str(account.get("workspace_id") or "default")
    with transaction(database_path) as conn:
        _ensure_wallet(conn, account_id, workspace_id)
        wallet = conn.execute(
            """
            SELECT points_balance, locked_points, manual_frozen_points,
                   version, ledger_head_hash, updated_at
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
            "points_balance": int(wallet["points_balance"] if wallet else 0),
            "locked_points": int(wallet["locked_points"] if wallet else 0),
            "manual_frozen_points": int(wallet["manual_frozen_points"] if wallet else 0),
            "frozen_points": int(
                (wallet["locked_points"] if wallet else 0)
                + (wallet["manual_frozen_points"] if wallet else 0)
            ),
            "available_points": int(
                (wallet["points_balance"] if wallet else 0)
                - (wallet["locked_points"] if wallet else 0)
                - (wallet["manual_frozen_points"] if wallet else 0)
            ),
            "version": int(wallet["version"] if wallet else 0),
            "ledger_head_hash": wallet["ledger_head_hash"] if wallet else "",
            "updated_at": wallet["updated_at"] if wallet else "",
        },
        "pricing": {
            "currency": "CNY",
            "point_ratio": BILLING_POINT_RATIO,
            "ratio_label": f"1 元 = {BILLING_POINT_RATIO} 积分",
            "status": "draft",
        },
        "topup_products": [
            {
                "package_id": package_id,
                "label": item["label"],
                "amount_cents": item["amount_cents"],
                "points": item["points"],
            }
            for package_id, item in BILLING_TOPUP_PRODUCTS.items()
        ],
        "recent_ledger": [dict(row) for row in ledgers],
        "recent_orders": [dict(row) for row in orders],
        "security": {
            "server_authoritative": True,
            "local_balance_trusted": False,
            "ledger_hash_chain": True,
            "settlement_requires_signed_provider_callback": True,
        },
    }


def _create_topup_order(database_path: Path, account: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    account_id = str(account["account_id"])
    workspace_id = str(account.get("workspace_id") or "default")
    provider = str(payload.get("provider") or "").strip().lower()
    package_id = str(payload.get("package_id") or "").strip()
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if provider not in PAYMENT_PROVIDERS:
        raise HTTPException(status_code=400, detail="provider must be wechat or alipay")
    if package_id not in BILLING_TOPUP_PRODUCTS:
        raise HTTPException(status_code=400, detail="unknown topup package")
    if not 16 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=400, detail="idempotency_key is required")

    product = BILLING_TOPUP_PRODUCTS[package_id]
    now = _utc_now()
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
    request_hash = _stable_json_hash(
        {
            "account_id": account_id,
            "workspace_id": workspace_id,
            "provider": provider,
            "package_id": package_id,
            "amount_cents": product["amount_cents"],
            "points": product["points"],
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
            return _topup_order_response(dict(existing), reused=True)
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
                product["points"],
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
    return _topup_order_response(dict(order), reused=False)


def _topup_order_response(order: dict[str, Any], *, reused: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "reused": reused,
        "order": order,
        "payment": {
            "provider": order["provider"],
            "mode": "gateway_not_configured",
            "qr_code_url": "",
            "pay_url": "",
            "message": "支付网关尚未配置。订单已在服务器生成 pending 记录，待微信/支付宝商户参数和回调验签接入后才可收款入账。",
        },
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

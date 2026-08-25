"""Minimal Alipay computer-website payment gateway.

The gateway only creates signed payment URLs and verifies signed callbacks.
It deliberately does not know about wallets or points. Accounting remains in
``wh_local.billing`` so every credit shares the existing server ledger.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


ALIPAY_PAGE_PAY_METHOD = "alipay.trade.page.pay"
ALIPAY_PRODUCT_CODE = "FAST_INSTANT_TRADE_PAY"
DEFAULT_GATEWAY = "https://openapi.alipay.com/gateway.do"


class AlipayGatewayConfigurationError(RuntimeError):
    """Raised when the server has not received its Alipay merchant settings."""


class AlipaySignatureError(RuntimeError):
    """Raised when an Alipay callback cannot be verified."""


@dataclass(frozen=True)
class AlipaySettings:
    app_id: str
    gateway: str
    notify_url: str
    return_url: str
    seller_id: str
    private_key_pem: bytes
    public_key_pem: bytes


def is_configured() -> bool:
    """Return a conservative readiness status without exposing credentials."""

    try:
        load_settings()
    except AlipayGatewayConfigurationError:
        return False
    return True


def load_settings() -> AlipaySettings:
    """Load merchant material from server-only environment variables."""

    app_id = os.environ.get("ALIPAY_APP_ID", "").strip()
    notify_url = os.environ.get("ALIPAY_NOTIFY_URL", "").strip()
    private_path = os.environ.get("ALIPAY_MERCHANT_PRIVATE_KEY_PATH", "").strip()
    public_path = os.environ.get("ALIPAY_PUBLIC_KEY_PATH", "").strip()
    missing = [
        name
        for name, value in (
            ("ALIPAY_APP_ID", app_id),
            ("ALIPAY_NOTIFY_URL", notify_url),
            ("ALIPAY_MERCHANT_PRIVATE_KEY_PATH", private_path),
            ("ALIPAY_PUBLIC_KEY_PATH", public_path),
        )
        if not value
    ]
    if missing:
        raise AlipayGatewayConfigurationError(
            "Alipay server settings are missing: " + ", ".join(missing)
        )
    try:
        private_key_pem = _read_private_key_file(Path(private_path))
        public_key_pem = _read_public_key_file(Path(public_path))
    except (OSError, ValueError, TypeError, binascii.Error) as exc:
        raise AlipayGatewayConfigurationError("Alipay key file is unavailable") from exc
    if not notify_url.startswith("https://"):
        raise AlipayGatewayConfigurationError("ALIPAY_NOTIFY_URL must use HTTPS")
    return AlipaySettings(
        app_id=app_id,
        gateway=os.environ.get("ALIPAY_GATEWAY", DEFAULT_GATEWAY).strip() or DEFAULT_GATEWAY,
        notify_url=notify_url,
        return_url=os.environ.get("ALIPAY_RETURN_URL", "").strip(),
        seller_id=os.environ.get("ALIPAY_SELLER_ID", "").strip(),
        private_key_pem=private_key_pem,
        public_key_pem=public_key_pem,
    )


def build_page_payment_url(
    *,
    out_trade_no: str,
    amount_cents: int,
    subject: str,
    expires_at: str,
) -> str:
    """Build a signed Alipay page-pay URL for one server-created order."""

    settings = load_settings()
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        expires = datetime.now(timezone.utc)
    biz_content: dict[str, Any] = {
        "out_trade_no": out_trade_no,
        "product_code": ALIPAY_PRODUCT_CODE,
        "total_amount": _cents_to_amount(amount_cents),
        "subject": subject[:128] or "界野电商平台积分充值",
        "time_expire": expires.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
    }
    params = {
        "app_id": settings.app_id,
        "method": ALIPAY_PAGE_PAY_METHOD,
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "notify_url": settings.notify_url,
        "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(",", ":")),
    }
    if settings.return_url:
        params["return_url"] = settings.return_url
    params["sign"] = _sign(params, settings.private_key_pem)
    return f"{settings.gateway}?{urlencode(params, encoding='utf-8')}"


def verify_callback(payload: dict[str, str]) -> dict[str, str]:
    """Verify a form callback and return only normalized, trusted fields."""

    settings = load_settings()
    signature = str(payload.get("sign") or "").strip()
    if not signature or not _verify(payload, signature, settings.public_key_pem):
        raise AlipaySignatureError("Alipay callback signature verification failed")
    if str(payload.get("app_id") or "") != settings.app_id:
        raise AlipaySignatureError("Alipay callback app id mismatch")
    if settings.seller_id and str(payload.get("seller_id") or "") != settings.seller_id:
        raise AlipaySignatureError("Alipay callback seller mismatch")
    out_trade_no = str(payload.get("out_trade_no") or "").strip()
    trade_no = str(payload.get("trade_no") or "").strip()
    status = str(payload.get("trade_status") or "").strip()
    amount_cents = _amount_to_cents(str(payload.get("total_amount") or ""))
    if not out_trade_no or not trade_no or amount_cents <= 0:
        raise AlipaySignatureError("Alipay callback payment fields are incomplete")
    if status not in {"TRADE_SUCCESS", "TRADE_FINISHED"}:
        raise AlipaySignatureError("Alipay callback trade is not successful")
    return {
        "out_trade_no": out_trade_no,
        "trade_no": trade_no,
        "trade_status": status,
        "amount_cents": str(amount_cents),
        "buyer_id": str(payload.get("buyer_id") or "")[:128],
    }


def _canonical_payload(payload: dict[str, str]) -> bytes:
    values = {
        str(key): str(value)
        for key, value in payload.items()
        if key not in {"sign", "sign_type"} and value is not None
    }
    return "&".join(f"{key}={values[key]}" for key in sorted(values)).encode("utf-8")


def _sign(params: dict[str, str], private_key_pem: bytes) -> str:
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        _canonical_payload(params),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def _read_private_key_file(path: Path) -> bytes:
    """Normalize PEM or Base64-only PKCS#1/PKCS#8 private keys to PEM."""

    raw = path.read_bytes().strip()
    if b"-----BEGIN" in raw:
        return raw
    compact = b"".join(raw.split())
    if not compact:
        raise OSError("empty key file")
    private_key = serialization.load_der_private_key(base64.b64decode(compact), password=None)
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _read_public_key_file(path: Path) -> bytes:
    """Normalize PEM or Base64-only SubjectPublicKeyInfo keys to PEM."""

    raw = path.read_bytes().strip()
    if b"-----BEGIN" in raw:
        return raw
    compact = b"".join(raw.split())
    if not compact:
        raise OSError("empty key file")
    public_key = serialization.load_der_public_key(base64.b64decode(compact))
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _verify(payload: dict[str, str], signature: str, public_key_pem: bytes) -> bool:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem)
        public_key.verify(
            base64.b64decode(signature),
            _canonical_payload(payload),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def _cents_to_amount(amount_cents: int) -> str:
    return f"{Decimal(int(amount_cents)) / Decimal(100):.2f}"


def _amount_to_cents(value: str) -> int:
    try:
        amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise AlipaySignatureError("Alipay callback amount is invalid") from exc
    return int(amount * 100)

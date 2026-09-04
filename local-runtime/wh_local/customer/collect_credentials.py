"""本地采集凭据申请：向服务器申请 OneBound API key 并解密。

安全模型：
- 服务器 RSA 私钥只在云服务器上；本地只内置公钥（公钥不能解密，用户拿到无用）。
- 每次采集前生成本次随机 AES-256 会话密钥，用服务器公钥加密后发送；
  服务器用私钥解出会话密钥，再用会话密钥 AES-GCM 加密 OneBound 凭据返回。
- OneBound api_key/api_secret 明文只在本地进程内存中出现，不落盘。
"""

from __future__ import annotations

import base64
import json
import os
import ssl
from typing import Any, Mapping
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..config import is_ip_literal_host


# 云服务器 RSA 公钥（与服务器私钥配对）。服务器更换密钥对时需同步更新此处。
SERVER_RSA_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAoyqilByaF4Qc8t2RwXwN
+zBAOTA+vikfTZQ8DprPSEkLX+NLzPN14yTvVjruwtXqxvNf/RUXveb1XYWkgbro
u0fy64k5Bt7pTUnBDzUxA2TQuxI/QevW+XQeiO/iz72mND1t7NRuQEYTGafOi9An
s437MgMOcZs4NLrYhtpi01l8qP/7ypaIRo3Wn6Ja4W/aMPbqH9/dmFzbhjFbbIUO
gUVayj9yhzPiykDKx+w9xZ3Fg1otManOkRKdQ/kaKeNgFavTr3xt6URNuFBnSB2Z
058gs7/SDnsCyXMo8PjAbGcFVCcZ2Tu1b+Q3jJ5N1b2gYrByYPbhXWTstOm7C02P
qwIDAQAB
-----END PUBLIC KEY-----"""


class CollectCredentialsError(RuntimeError):
    """Raised when OneBound credentials cannot be obtained from the server."""


def request_collect_credentials(
    *,
    base_url: str,
    account_id: str = "",
    username: str = "",
    workspace_code: str = "",
    timeout: float = 15,
) -> Mapping[str, str]:
    """Request OneBound credentials from the collect-key endpoint and decrypt them.

    Returns ``{"api_key": ..., "api_secret": ..., "base_url": ...}``.
    """
    if not base_url:
        raise CollectCredentialsError("collect credential service is not configured")

    # 1. 生成一次性 AES-256 会话密钥，用服务器公钥加密。
    session_key = os.urandom(32)
    public_key = serialization.load_pem_public_key(
        SERVER_RSA_PUBLIC_KEY_PEM.encode("utf-8")
    )
    encrypted_session_key = public_key.encrypt(
        session_key,
        asymmetric_padding.OAEP(
            mgf=asymmetric_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    # 2. 发送请求。
    body = json.dumps(
        {
            "account_id": account_id,
            "username": username,
            "workspace_code": workspace_code,
            "encrypted_session_key": base64.b64encode(encrypted_session_key).decode("ascii"),
        }
    ).encode("utf-8")
    endpoint = urljoin(base_url.rstrip("/") + "/", "api/customer/collect-key")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # IP-direct connections to a test/staging host cannot match the public
        # certificate's hostname. Skip chain/hostname verification only when the
        # target is a bare IP literal; production (domain) stays fully verified.
        context = ssl._create_unverified_context() if is_ip_literal_host(base_url) else None
        with urlopen(request, timeout=timeout, context=context) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        detail = _extract_error_message(exc)
        raise CollectCredentialsError(detail or f"collect-key request failed (HTTP {exc.code})") from exc
    except (OSError, TimeoutError) as exc:
        raise CollectCredentialsError(f"cannot reach collect-key service: {exc}") from exc

    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise CollectCredentialsError("collect-key service returned an invalid response")
    encrypted_payload = str(payload.get("payload") or "")
    nonce_b64 = str(payload.get("nonce") or "")
    tag_b64 = str(payload.get("tag") or "")
    if not encrypted_payload or not nonce_b64 or not tag_b64:
        raise CollectCredentialsError("collect-key response is missing encrypted payload")

    # 3. 用会话密钥解密凭据。
    try:
        ciphertext = base64.b64decode(encrypted_payload)
        nonce = base64.b64decode(nonce_b64)
        tag = base64.b64decode(tag_b64)
        decryptor = Cipher(algorithms.AES(session_key), modes.GCM(nonce, tag)).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        credentials = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise CollectCredentialsError("cannot decrypt collect-key response") from exc

    if not isinstance(credentials, Mapping):
        raise CollectCredentialsError("collect-key payload is not an object")
    return credentials


def _extract_error_message(exc: HTTPError) -> str:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw or "{}")
    except Exception:
        return raw[:300]
    if not isinstance(body, dict):
        return raw[:300]
    detail = body.get("detail") or body.get("message") or body.get("error")
    if isinstance(detail, dict):
        detail = detail.get("detail") or detail.get("message") or str(detail)
    return str(detail or "").strip() or raw[:300]

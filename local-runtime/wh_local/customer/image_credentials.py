"""One-shot in-memory Wuyin credential lease for local image generation."""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .collect_credentials import SERVER_RSA_PUBLIC_KEY_PEM


class ImageCredentialsError(RuntimeError):
    """Raised when a one-shot image credential cannot be obtained safely."""


@contextmanager
def lease_image_credentials(
    *,
    base_url: str,
    remote_token: str,
    usage_id: str,
    timeout: float = 20,
) -> Iterator[MutableMapping[str, str]]:
    """Yield decrypted Wuyin credentials once, then clear the local container."""

    credentials = dict(
        request_image_credentials(
            base_url=base_url,
            remote_token=remote_token,
            usage_id=usage_id,
            timeout=timeout,
        )
    )
    try:
        yield credentials
    finally:
        for key in tuple(credentials):
            credentials[key] = ""
        credentials.clear()


def request_image_credentials(
    *,
    base_url: str,
    remote_token: str,
    usage_id: str,
    timeout: float = 20,
) -> MutableMapping[str, str]:
    if not base_url or not remote_token or not usage_id:
        raise ImageCredentialsError("image credential service is not available")

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
    request = Request(
        urljoin(base_url.rstrip("/") + "/", "api/customer/ai/image-credentials"),
        data=json.dumps(
            {
                "usage_id": usage_id,
                "encrypted_session_key": base64.b64encode(encrypted_session_key).decode("ascii"),
            }
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {remote_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(1.0, float(timeout))) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        raise ImageCredentialsError(
            f"image credential service returned HTTP {int(exc.code)}"
        ) from exc
    except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImageCredentialsError("image credential service is not available") from exc

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ImageCredentialsError("image credential service returned an invalid response")
    try:
        ciphertext = base64.b64decode(str(payload.get("payload") or ""), validate=True)
        nonce = base64.b64decode(str(payload.get("nonce") or ""), validate=True)
        tag = base64.b64decode(str(payload.get("tag") or ""), validate=True)
        decryptor = Cipher(algorithms.AES(session_key), modes.GCM(nonce, tag)).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        decrypted = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ImageCredentialsError("image credential response could not be decrypted") from exc
    if not isinstance(decrypted, dict):
        raise ImageCredentialsError("image credential service returned an invalid response")
    credentials = {
        "api_key": str(decrypted.get("api_key") or "").strip(),
        "base_url": str(decrypted.get("base_url") or "").strip(),
    }
    if not credentials["api_key"] or credentials["base_url"] != "https://api.wuyinkeji.com":
        raise ImageCredentialsError("image credential service returned an invalid response")
    return credentials
